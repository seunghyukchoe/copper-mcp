"""Prepare a confined, immutable SPICE source derivative from internal records.

The preparer performs no filesystem access, native execution, authentication, or apply.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import math
import time
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import cast

from copper_mcp.adapters.cst import CstError
from copper_mcp.adapters.sexpr import SExprError
from copper_mcp.engineering._pin_net_source import SourcePinAlias
from copper_mcp.engineering._project_spice_source_edits import (
    _MODEL_DIRECTORY,
    ProjectSpiceSourceError,
    _check,
    _DesiredFields,
    _fail,
    _project_relative,
    _source_edit_plan,
    _source_splices,
    _splice,
)
from copper_mcp.engineering._project_spice_source_edits import _field_text as _field_text
from copper_mcp.engineering._project_spice_source_edits import (
    _property_values as _property_values,
)
from copper_mcp.engineering._spice_terminal_binding import (
    BoundSpicePin,
    BoundSpiceReference,
)
from copper_mcp.engineering.capture import (
    CaptureLimits,
    ElectricalArtifactCapture,
    ElectricalCaptureError,
    PublicElectricalCaptureProjection,
    _canonical_path,
    _CapturedArtifact,
    _parse_paths,
)
from copper_mcp.engineering.project_erc_inputs import (
    PreparedProjectErc,
    ProjectErcInputError,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.project_spice_model_binding import (
    ProjectSpiceModelBinding,
    ProjectSpiceModelBindingError,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.engineering.spice_model_library import (
    SpiceDefinition,
    SpiceModelLibraryError,
    parse_spice_model_library,
)

_MAX_REFERENCES = 10_000
_MAX_PINS = 16_384
_MAX_ALIASES = 16_384
_MAX_MODEL_BYTES = 16 * 1024 * 1024
_HASH_CHUNK_BYTES = 64 * 1024


@dataclass(frozen=True, slots=True, repr=False)
class PreparedSpiceSource:
    prepared: PreparedProjectErc
    binding_digest: str
    model_paths: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "<PreparedSpiceSource redacted>"


def _active_controls(limits: object, deadline: object) -> tuple[CaptureLimits, float]:
    if type(limits) is not CaptureLimits or type(deadline) not in (int, float):
        _fail("project SPICE source preparation bounds are malformed")
    if type(deadline) is int and not -(1 << 1023) <= deadline <= 1 << 1023:
        _fail("project SPICE source preparation bounds are malformed")
    copied = CaptureLimits(
        limits.max_file_bytes,
        limits.max_total_bytes,
        limits.max_capture_seconds,
    )
    value = float(cast(int | float, deadline))
    if not math.isfinite(value):
        _fail("project SPICE source preparation bounds are malformed")
    value = min(value, time.monotonic() + copied.max_capture_seconds)
    _check(value)
    return copied, value


def _sha(payload: bytes, deadline: float) -> str:
    digest = hashlib.sha256()
    for offset in range(0, len(payload), _HASH_CHUNK_BYTES):
        _check(deadline)
        digest.update(payload[offset : offset + _HASH_CHUNK_BYTES])
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _document_digest(namespace: str, document: object, deadline: float) -> str:
    digest = hashlib.sha256(namespace.encode("ascii") + b"\x00")
    encoder = json.JSONEncoder(
        sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    )
    for token in encoder.iterencode(document):
        _check(deadline)
        digest.update(token.encode("ascii"))
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _portable(value: str) -> str:
    return unicodedata.normalize("NFC", value.casefold())


def _capture_artifacts(
    capture: ElectricalArtifactCapture,
    artifact_paths_json: bytes,
    limits: CaptureLimits,
    deadline: float,
) -> tuple[dict[str, _CapturedArtifact], dict[str, str]]:
    if (
        type(capture) is not ElectricalArtifactCapture
        or type(capture._artifacts) is not tuple
        or not 1 <= len(capture._artifacts) <= 16
        or type(artifact_paths_json) is not bytes
        or not artifact_paths_json
        or len(artifact_paths_json) > limits.max_file_bytes
    ):
        _fail()
    _check(deadline)
    document = _parse_paths(artifact_paths_json)
    _check(deadline)
    paths = {item.artifact_id: _canonical_path(item.path) for item in document.artifacts}
    if len({_portable(path) for path in paths.values()}) != len(paths):
        _fail()
    artifacts: dict[str, _CapturedArtifact] = {}
    total = 0
    rows = []
    for artifact in capture._artifacts:
        _check(deadline)
        if (
            type(artifact) is not _CapturedArtifact
            or artifact.artifact_id in artifacts
            or type(artifact.content) is not bytes
            or not artifact.content
            or artifact.role not in {"bom", "schematic", "netlist", "model-library"}
            or len(artifact.content) > limits.max_file_bytes
        ):
            _fail()
        size = len(artifact.content)
        if size > limits.max_total_bytes - total:
            _fail()
        _check(deadline)
        if artifact.digest != _sha(artifact.content, deadline):
            _fail()
        artifacts[artifact.artifact_id] = artifact
        total += size
        rows.append(
            {
                "artifact_id": artifact.artifact_id,
                "role": artifact.role,
                "digest": artifact.digest,
                "size": size,
            }
        )
    if set(paths) != set(artifacts):
        _fail()
    expected = _document_digest(
        "copper-mcp/electrical-artifact-capture/v1",
        {"declaration_digest": capture.declaration_digest, "artifacts": rows},
        deadline,
    )
    projection = capture.redacted_projection
    if (
        capture.digest != expected
        or type(projection) is not PublicElectricalCaptureProjection
        or projection.declaration_digest != capture.declaration_digest
        or projection.capture_digest != capture.digest
        or projection.artifact_count != len(artifacts)
        or projection.total_bytes != total
    ):
        _fail()
    return artifacts, paths


def _selected_models(
    binding: ProjectSpiceModelBinding,
    artifacts: dict[str, _CapturedArtifact],
    paths: dict[str, str],
    parent: PurePosixPath,
    deadline: float,
) -> tuple[
    dict[str, tuple[SpiceDefinition, ...]],
    dict[str, str],
    tuple[tuple[str, str], ...],
]:
    if (
        type(binding.references) is not tuple
        or not 1 <= len(binding.references) <= _MAX_REFERENCES
        or any(type(reference) is not BoundSpiceReference for reference in binding.references)
    ):
        _fail()
    selected = sorted({reference.artifact_id for reference in binding.references})
    definitions: dict[str, tuple[SpiceDefinition, ...]] = {}
    captured_paths: dict[str, str] = {}
    model_paths = []
    all_names: set[str] = set()
    total_model_bytes = 0
    generated_parent = parent / _MODEL_DIRECTORY
    reserved_alias = _portable(generated_parent.as_posix())
    if any(
        _portable(path) == reserved_alias or _portable(path).startswith(reserved_alias + "/")
        for path in paths.values()
    ):
        _fail()
    for index, artifact_id in enumerate(selected):
        _check(deadline)
        artifact = artifacts.get(artifact_id)
        if artifact is None or artifact.role != "model-library":
            _fail()
        total_model_bytes += len(artifact.content)
        if total_model_bytes > _MAX_MODEL_BYTES:
            _fail()
        captured_paths[artifact_id] = _project_relative(paths[artifact_id], parent)
        library = parse_spice_model_library(artifact.content, deadline=deadline)
        definitions[artifact_id] = library.definitions
        for definition in library.definitions:
            _check(deadline)
            alias = definition.name.casefold()
            if alias in all_names:
                _fail()
            all_names.add(alias)
        generated = (generated_parent / f"model-{index:03d}.lib").as_posix()
        model_paths.append((artifact_id, generated))
    return definitions, captured_paths, tuple(model_paths)


def _mapping(reference: BoundSpiceReference, deadline: float) -> tuple[tuple[str, str], ...]:
    if type(reference.pins) is not tuple or not reference.pins or len(reference.pins) > 64:
        _fail()
    pins: set[str] = set()
    ports: set[str] = set()
    result = []
    for pin in reference.pins:
        _check(deadline)
        if (
            type(pin) is not BoundSpicePin
            or pin.node.reference != reference.reference
            or type(pin.node.pin_number) is not str
            or not pin.node.pin_number
            or any(character.isspace() or character == "=" for character in pin.node.pin_number)
            or pin.node.pin_number in pins
            or type(pin.node.aliases) is not tuple
            or not pin.node.aliases
        ):
            _fail()
        pins.add(pin.node.pin_number)
        if pin.port is None:
            if not pin.node.no_connect:
                _fail()
        else:
            if (
                pin.node.no_connect
                or pin.port in ports
                or pin.port not in reference.definition.ports
            ):
                _fail()
            ports.add(pin.port)
            result.append((pin.node.pin_number, pin.port))
    if ports != set(reference.definition.ports):
        _fail()
    return tuple(sorted(result))


def _desired_edits(
    capture: SchematicProjectCapture,
    binding: ProjectSpiceModelBinding,
    definitions: dict[str, tuple[SpiceDefinition, ...]],
    captured_paths: dict[str, str],
    model_paths: tuple[tuple[str, str], ...],
    deadline: float,
) -> dict[tuple[str, str], _DesiredFields]:
    if type(binding.references) is not tuple or not 1 <= len(binding.references) <= _MAX_REFERENCES:
        _fail()
    instances: dict[str, str] = {}
    for instance in capture.hierarchy.instance_paths:
        _check(deadline)
        if instance.uuid_path in instances:
            _fail()
        instances[instance.uuid_path] = instance.source_path
    generated = dict(model_paths)
    edits: dict[tuple[str, str], _DesiredFields] = {}
    seen_references: set[str] = set()
    pin_count = 0
    alias_count = 0
    for reference in binding.references:
        _check(deadline)
        if (
            type(reference) is not BoundSpiceReference
            or reference.reference in seen_references
            or reference.artifact_id not in definitions
            or type(reference.definition) is not SpiceDefinition
        ):
            _fail()
        seen_references.add(reference.reference)
        matching = tuple(
            item
            for item in definitions[reference.artifact_id]
            if item.name.casefold() == reference.definition.name.casefold()
        )
        if len(matching) != 1 or matching[0] != reference.definition:
            _fail()
        mapping = _mapping(reference, deadline)
        pin_count += len(reference.pins)
        if pin_count > _MAX_PINS:
            _fail()
        desired = _DesiredFields(
            captured_paths[reference.artifact_id],
            PurePosixPath(generated[reference.artifact_id])
            .relative_to(PurePosixPath(capture.root_path).parent)
            .as_posix(),
            reference.definition.name,
            mapping,
        )
        source_aliases: set[tuple[str, str]] = set()
        for pin in reference.pins:
            for alias in pin.node.aliases:
                _check(deadline)
                alias_count += 1
                if alias_count > _MAX_ALIASES or type(alias) is not SourcePinAlias:
                    _fail()
                source_path = instances.get(alias.sheet_uuid_path)
                if source_path is None:
                    _fail()
                source_aliases.add((source_path, alias.source_symbol_uuid))
        if not source_aliases:
            _fail()
        for source_alias in source_aliases:
            existing = edits.get(source_alias)
            if existing is not None and existing != desired:
                _fail()
            edits[source_alias] = desired
    return edits


def _prepare_project_spice_source(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    binding: ProjectSpiceModelBinding,
    artifacts: ElectricalArtifactCapture,
    artifact_paths_json: bytes,
    *,
    limits: CaptureLimits,
    deadline: float,
) -> PreparedSpiceSource:
    active_limits, active_deadline = _active_controls(limits, deadline)
    if (
        type(capture) is not SchematicProjectCapture
        or type(libraries) is not tuple
        or type(binding) is not ProjectSpiceModelBinding
        or binding.project_capture_digest != capture.digest
        or binding.electrical_artifact_capture_digest != artifacts.digest
        or binding.declaration_digest != artifacts.declaration_digest
    ):
        _fail()
    prepared = prepare_project_erc(
        capture, libraries, limits=active_limits, deadline=active_deadline
    )
    captured_artifacts, artifact_paths = _capture_artifacts(
        artifacts, artifact_paths_json, active_limits, active_deadline
    )
    parent = PurePosixPath(capture.root_path).parent
    definitions, captured_paths, model_paths = _selected_models(
        binding,
        captured_artifacts,
        artifact_paths,
        parent,
        active_deadline,
    )
    binding_digest = binding._digest(active_deadline)
    edits = _desired_edits(
        capture,
        binding,
        definitions,
        captured_paths,
        model_paths,
        active_deadline,
    )
    context = dict(prepared.files)
    model_directory = (parent / _MODEL_DIRECTORY).as_posix()
    model_directory_alias = _portable(model_directory)
    if any(
        _portable(path) == model_directory_alias
        or _portable(path).startswith(model_directory_alias + "/")
        for path in context
    ):
        _fail()
    projected_total = sum(len(content) for content in context.values())
    projected_sizes: dict[str, int] = {}
    for artifact_id, generated_path in model_paths:
        _check(active_deadline)
        if generated_path in context:
            _fail()
        projected_total += len(captured_artifacts[artifact_id].content)
    for path, content in tuple(context.items()):
        _check(active_deadline)
        if not path.endswith(".kicad_sch"):
            continue
        _unused, projected_size = _source_edit_plan(
            path,
            content,
            edits,
            active_limits,
            active_deadline,
            materialize=False,
        )
        projected_sizes[path] = projected_size
        projected_total += projected_size - len(content)
    if projected_total > active_limits.max_total_bytes or any(
        size > active_limits.max_file_bytes for size in projected_sizes.values()
    ):
        _fail()
    for path, content in tuple(context.items()):
        _check(active_deadline)
        if not path.endswith(".kicad_sch"):
            continue
        splices = _source_splices(path, content, edits, active_limits, active_deadline)
        if splices:
            _check(active_deadline)
            context[path] = _splice(content, splices, active_deadline)
            _check(active_deadline)
        if len(context[path]) != projected_sizes[path]:
            _fail()
    for artifact_id, generated_path in model_paths:
        _check(active_deadline)
        context[generated_path] = captured_artifacts[artifact_id].content
    if (
        any(len(content) > active_limits.max_file_bytes for content in context.values())
        or sum(len(content) for content in context.values()) > active_limits.max_total_bytes
    ):
        _fail()
    file_rows = []
    for path, content in sorted(context.items()):
        _check(active_deadline)
        file_rows.append({"path": path, "digest": _sha(content, active_deadline)})
    execution_digest = _document_digest(
        "copper-mcp/project-spice-source/v1",
        {
            "prepared_execution_digest": prepared.execution_digest,
            "project_capture_digest": capture.digest,
            "artifact_capture_digest": artifacts.digest,
            "captured_model_paths": tuple(sorted(captured_paths.items())),
            "binding_digest": binding_digest,
            "model_paths": model_paths,
            "files": file_rows,
        },
        active_deadline,
    )
    confined = dataclasses.replace(
        prepared,
        files=tuple(sorted(context.items())),
        execution_digest=execution_digest,
    )
    _check(active_deadline)
    return PreparedSpiceSource(confined, binding_digest, model_paths)


def prepare_project_spice_source(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    binding: ProjectSpiceModelBinding,
    artifacts: ElectricalArtifactCapture,
    artifact_paths_json: bytes,
    *,
    limits: CaptureLimits,
    deadline: float,
) -> PreparedSpiceSource:
    """Build one private derivative with fixed, context-free refusals."""

    result = None
    try:
        result = _prepare_project_spice_source(
            capture,
            libraries,
            binding,
            artifacts,
            artifact_paths_json,
            limits=limits,
            deadline=deadline,
        )
    except ProjectSpiceSourceError:
        raise
    except (
        AttributeError,
        CstError,
        ElectricalCaptureError,
        ProjectErcInputError,
        ProjectSpiceModelBindingError,
        SExprError,
        SpiceModelLibraryError,
        TypeError,
        UnicodeError,
        ValueError,
        OverflowError,
        RecursionError,
    ):
        pass
    if result is None:
        _fail()
    return result


__all__ = [
    "PreparedSpiceSource",
    "ProjectSpiceSourceError",
    "prepare_project_spice_source",
]
