"""Confined source preparation only; native SPICE interpretation remains unproven."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import time

import pytest
from test_project_spice_model_binding import _case, _run

from copper_mcp.adapters.sexpr import children, parse_sexpr
from copper_mcp.engineering import _project_spice_source_edits as source_edits
from copper_mcp.engineering import project_spice_source as source_preparation
from copper_mcp.engineering._spice_terminal_binding import BoundSpicePin
from copper_mcp.engineering.capture import (
    CaptureLimits,
    ElectricalArtifactCapture,
    PublicElectricalCaptureProjection,
    _CapturedArtifact,
    capture_electrical_artifacts,
)
from copper_mcp.engineering.project_spice_source import (
    PreparedSpiceSource,
    ProjectSpiceSourceError,
    prepare_project_spice_source,
)
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)
from copper_mcp.engineering.spice_model_library import parse_spice_model_library
from copper_mcp.optimization.contracts import digest_document


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _inputs(tmp_path, monkeypatch):
    case = _case(tmp_path, monkeypatch)
    report = _run(case)
    root_instance = next(
        item.uuid_path
        for item in case[0].hierarchy.instance_paths
        if item.source_path == case[0].root_path
    )
    placed = {}
    parsed = parse_sexpr(case[-1][case[0].root_path])
    for symbol in children(parsed, "symbol"):
        properties = {
            item.items[1]: item.items[2]
            for item in children(symbol, "property")
            if len(item.items) >= 3
        }
        uuid = children(symbol, "uuid")[0].items[1]
        placed[properties["Reference"]] = uuid
    references = []
    for reference in report.references:
        pins = tuple(
            dataclasses.replace(
                pin,
                node=dataclasses.replace(
                    pin.node,
                    aliases=tuple(
                        dataclasses.replace(
                            alias,
                            sheet_uuid_path=root_instance,
                            source_symbol_uuid=placed[reference.reference],
                        )
                        for alias in pin.node.aliases
                    ),
                ),
            )
            for pin in reference.pins
        )
        references.append(dataclasses.replace(reference, pins=pins))
    report = dataclasses.replace(report, references=tuple(references))
    artifacts = capture_electrical_artifacts(case[2], case[3], tmp_path)
    return case, report, artifacts


def _recapture(tmp_path, case, report, root_source):
    context = dict(case[-1])
    context[case[0].root_path] = root_source
    for path, payload in context.items():
        (tmp_path / path).write_bytes(payload)
    capture = capture_schematic_project(
        tmp_path,
        case[0].root_path,
        tuple(ProjectFileBinding(path, _sha(payload)) for path, payload in sorted(context.items())),
    )
    return capture, dataclasses.replace(report, project_capture_digest=capture.digest), context


def _add_fields(source: bytes, reference: str, *, library: str, name: str, pins: str) -> bytes:
    marker = f'(property "Reference" "{reference}"'.encode()
    assert source.count(marker) == 1
    fields = (
        f'(property "Sim.Library" "{library}" (at 0 0 0) '
        "(effects (font (size 1.27 1.27)) hide))"
        f'(property "Sim.Name" "{name}" (at 0 0 0) '
        "(effects (font (size 1.27 1.27)) hide))"
        f'(property "Sim.Pins" "{pins}" (at 0 0 0) '
        "(effects (font (size 1.27 1.27)) hide))"
    ).encode()
    return source.replace(marker, fields + marker)


def _append_top_level(source: bytes, expression: bytes) -> bytes:
    return source.rstrip()[:-1] + expression + b")\n"


def _prepare(case, report, artifacts, paths=None, *, limits=None, deadline=None):
    return prepare_project_spice_source(
        case[0],
        case[1],
        report,
        artifacts,
        case[3] if paths is None else paths,
        limits=limits or CaptureLimits(),
        deadline=time.monotonic() + 30 if deadline is None else deadline,
    )


def _files(result: PreparedSpiceSource) -> dict[str, bytes]:
    return dict(result.prepared.files)


def test_private_desired_fields_repr_is_fixed_and_redacted():
    desired = source_preparation._DesiredFields(
        "private/original.lib",
        ".copper-spice-models/model-000.lib",
        "PRIVATE_MODEL",
        (("secret-pin", "secret-port"),),
    )
    assert repr(desired) == "<_DesiredFields redacted>"
    assert all(value not in repr(desired) for value in ("private", "PRIVATE", "secret"))


def test_facade_reexports_canonical_kernel_objects():
    assert source_preparation.ProjectSpiceSourceError is source_edits.ProjectSpiceSourceError
    assert source_preparation._DesiredFields is source_edits._DesiredFields
    assert source_preparation._fail is source_edits._fail
    assert source_preparation._check is source_edits._check
    assert source_preparation._project_relative is source_edits._project_relative
    assert source_preparation._property_values is source_edits._property_values
    assert source_preparation._field_text is source_edits._field_text
    assert source_preparation._source_edit_plan is source_edits._source_edit_plan
    assert source_preparation._source_splices is source_edits._source_splices
    assert source_preparation._splice is source_edits._splice


def test_absent_fields_are_added_and_full_existing_fields_are_confined(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    source = _add_fields(
        case[-1][case[0].root_path],
        "C1",
        library="inputs/models.lib",
        name="CAP",
        pins="\\t2=B\\n1=A ",
    )
    capture, report, context = _recapture(tmp_path, case, report, source)
    case = (capture, *case[1:-1], context)

    result = _prepare(case, report, artifacts)
    prepared_source = _files(result)[capture.root_path]
    assert result.model_paths == (("models-main", ".copper-spice-models/model-000.lib"),)
    model_artifact = next(
        item for item in artifacts._artifacts if item.artifact_id == "models-main"
    )
    assert _files(result)[".copper-spice-models/model-000.lib"] == model_artifact.content
    assert b'(property "Sim.Library" ".copper-spice-models/model-000.lib"' in prepared_source
    assert b'(property "Sim.Name" "CAP"' in prepared_source
    assert b'(property "Sim.Pins" "1=A 2=B"' in prepared_source
    assert prepared_source.count(b'(property "Sim.Library"') == 2
    assert parse_sexpr(prepared_source).head == "kicad_sch"


def test_reversed_mapping_and_explicit_nc_serialize_only_complete_ports(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    resistor = report.references[1]
    reversed_pins = tuple(
        dataclasses.replace(pin, port={"A": "B", "B": "A"}[pin.port]) for pin in resistor.pins
    )
    alias = resistor.pins[0].node.aliases[0]
    nc_node = dataclasses.replace(
        resistor.pins[0].node,
        pin_number="3",
        no_connect=True,
        aliases=(
            dataclasses.replace(alias, source_pin_uuid="30000000-0000-4000-8000-000000000099"),
        ),
    )
    resistor = dataclasses.replace(
        resistor,
        pins=(*reversed_pins, BoundSpicePin(nc_node, None)),
    )
    report = dataclasses.replace(report, references=(report.references[0], resistor))

    result = _prepare(case, report, artifacts)
    source = _files(result)[case[0].root_path]
    assert b'(property "Sim.Pins" "1=B 2=A"' in source
    assert b"3=" not in source and b"=NC" not in source


@pytest.mark.parametrize(
    "pins",
    (
        "1=A",
        "1=A 1=B",
        "1=A 2=A",
        "1=A nope",
        "1=A\u00a02=B",
        "1=A\u20282=B",
    ),
)
def test_partial_duplicate_or_malformed_existing_maps_refuse(tmp_path, monkeypatch, pins):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    source = _add_fields(
        case[-1][case[0].root_path],
        "C1",
        library="inputs/models.lib",
        name="CAP",
        pins=pins,
    )
    capture, report, context = _recapture(tmp_path, case, report, source)
    changed = (capture, *case[1:-1], context)
    with pytest.raises(ProjectSpiceSourceError) as error:
        _prepare(changed, report, artifacts)
    assert error.value.__context__ is None and "CAP" not in str(error.value)


@pytest.mark.parametrize(
    "shown_text",
    (
        "Header\\n.include concealed.lib",
        "Header\\rKCOUPLED L1 L2 0.99",
        "Header{return}.include encoded.lib",
        "Header{return}KENCODED L1 L2 0.98",
        "Header{unknown}literal",
        "Header ${SPICE_DIRECTIVE}",
        "Header ${=CONCAT('.include ', MODEL_PATH)}",
    ),
)
def test_multiline_coupling_and_dynamic_top_level_text_refuse(tmp_path, monkeypatch, shown_text):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    injected = f'(text "{shown_text}" (exclude_from_sim no) (at 0 0 0))'.encode()
    source = _append_top_level(case[-1][case[0].root_path], injected)
    capture, report, context = _recapture(tmp_path, case, report, source)
    with pytest.raises(ProjectSpiceSourceError) as error:
        _prepare((capture, *case[1:-1], context), report, artifacts)
    assert error.value.__context__ is None


def test_library_symbol_graphic_text_is_not_treated_as_top_level_directive(tmp_path, monkeypatch):
    case, _report, _artifacts = _inputs(tmp_path, monkeypatch)
    marker = b'(symbol "CopperMCP:R"'
    graphic = b'(text ".include graphic-only.lib" (at 0 0 0))'
    source = case[-1][case[0].root_path].replace(marker, marker + graphic, 1)
    assert (
        source_preparation._source_splices(
            case[0].root_path,
            source,
            {},
            CaptureLimits(),
            time.monotonic() + 30,
        )
        == ()
    )


def test_shared_source_aliases_require_identical_desired_edits(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    first, second = report.references
    shared_alias = first.pins[0].node.aliases[0]
    conflicting = tuple(
        dataclasses.replace(
            pin,
            node=dataclasses.replace(pin.node, aliases=(shared_alias,)),
        )
        for pin in second.pins
    )
    report = dataclasses.replace(
        report, references=(first, dataclasses.replace(second, pins=conflicting))
    )
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(case, report, artifacts)


@pytest.mark.parametrize(
    "injected",
    (
        b'(property "Sim.Params" "x=1" (at 0 0 0))',
        b'(property "Spice_Model" "legacy" (at 0 0 0))',
        b'(text ".include outside.lib" (exclude_from_sim no) (at 0 0 0))',
        b'(text ".include excluded.lib" (exclude_from_sim yes) (at 0 0 0))',
    ),
)
def test_unsupported_overrides_and_source_directives_refuse(tmp_path, monkeypatch, injected):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    if injected.startswith(b"(text "):
        source = _append_top_level(case[-1][case[0].root_path], injected)
    else:
        marker = b'(property "Reference" "C1"'
        source = case[-1][case[0].root_path].replace(marker, injected + marker)
    capture, report, context = _recapture(tmp_path, case, report, source)
    with pytest.raises(ProjectSpiceSourceError):
        _prepare((capture, *case[1:-1], context), report, artifacts)


def test_inherited_simulation_fields_refuse(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    marker = b'(symbol "CopperMCP:R"'
    inherited = b'(property "Sim.Name" "RES" (at 0 0 0))'
    source = case[-1][case[0].root_path].replace(marker, marker + inherited, 1)
    capture, report, context = _recapture(tmp_path, case, report, source)
    with pytest.raises(ProjectSpiceSourceError):
        _prepare((capture, *case[1:-1], context), report, artifacts)


@pytest.mark.parametrize("path", ("../models.lib", ".copper-spice-models/model-000.lib"))
def test_bad_or_reserved_artifact_paths_refuse(tmp_path, monkeypatch, path):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    document = json.loads(case[3])
    binding = next(item for item in document["artifacts"] if item["artifact_id"] == "models-main")
    binding["path"] = path
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(case, report, artifacts, json.dumps(document).encode())


def test_case_insensitive_artifact_path_collisions_refuse(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    document = json.loads(case[3])
    bom = next(item for item in document["artifacts"] if item["artifact_id"] == "bom-main")
    bom["path"] = "INPUTS/MODELS.LIB"
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(case, report, artifacts, json.dumps(document).encode())


def test_case_insensitive_definition_collision_across_selected_libraries_refuses(
    tmp_path, monkeypatch
):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    second_bytes = b".subckt cap A B\nC1 A B 1n\n.ends cap\n"
    second_artifact = _CapturedArtifact(
        "models-second", "model-library", _sha(second_bytes), second_bytes
    )
    captured = (*artifacts._artifacts, second_artifact)
    capture_digest = digest_document(
        "copper-mcp/electrical-artifact-capture/v1",
        {
            "declaration_digest": artifacts.declaration_digest,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "role": item.role,
                    "digest": item.digest,
                    "size": len(item.content),
                }
                for item in captured
            ],
        },
    )
    projection = PublicElectricalCaptureProjection(
        declaration_digest=artifacts.declaration_digest,
        capture_digest=capture_digest,
        artifact_count=len(captured),
        total_bytes=sum(len(item.content) for item in captured),
    )
    artifacts = ElectricalArtifactCapture(
        captured, artifacts.declaration_digest, capture_digest, projection
    )
    definition = parse_spice_model_library(
        second_bytes, deadline=time.monotonic() + 30
    ).definitions[0]
    second_reference = dataclasses.replace(
        report.references[1], artifact_id="models-second", definition=definition
    )
    report = dataclasses.replace(
        report,
        electrical_artifact_capture_digest=capture_digest,
        references=(report.references[0], second_reference),
    )
    paths = json.loads(case[3])
    paths["artifacts"].append({"artifact_id": "models-second", "path": "inputs/second.lib"})
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(case, report, artifacts, json.dumps(paths).encode())


def test_unicode_unrelated_regions_outputs_and_model_bytes_are_immutable(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    source = case[-1][case[0].root_path]
    marker = '(text "µ unchanged" (exclude_from_sim yes) (at 4 5 0))'.encode()
    source = source.rstrip()[:-1] + marker + b")\n"
    capture, report, context = _recapture(tmp_path, case, report, source)
    case = (capture, *case[1:-1], context)

    result = _prepare(case, report, artifacts)
    files = _files(result)
    model_path = result.model_paths[0][1]
    model_artifact = next(
        item for item in artifacts._artifacts if item.artifact_id == "models-main"
    )
    assert marker in files[capture.root_path]
    assert files[capture.root_path].endswith(marker + b")\n")
    assert files[model_path] == model_artifact.content
    assert repr(result) == "<PreparedSpiceSource redacted>"
    assert "CAP" not in repr(result) and "µ" not in repr(result)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.model_paths = ()
    with pytest.raises(TypeError):
        files[capture.root_path][0] = 0


@pytest.mark.parametrize("deadline", (0, True, float("nan"), float("inf"), "later", 10**1000))
def test_invalid_or_expired_deadline_refuses_context_free(tmp_path, monkeypatch, deadline):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    with pytest.raises(ProjectSpiceSourceError) as error:
        _prepare(case, report, artifacts, deadline=deadline)
    assert error.value.__context__ is None


def test_final_context_and_model_bytes_obey_capture_limits(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(
            case,
            report,
            artifacts,
            limits=CaptureLimits(max_file_bytes=1024, max_total_bytes=2048),
        )


def test_deadline_is_checked_before_each_placed_symbol(tmp_path, monkeypatch):
    case, _report, _artifacts = _inputs(tmp_path, monkeypatch)
    visited = []
    clock = [0.0]
    original = source_edits._property_values

    def watched(symbol, *args):
        visited.append(symbol)
        result = original(symbol, *args)
        clock[0] = 2.0
        return result

    monkeypatch.setattr(source_edits, "_property_values", watched)
    monkeypatch.setattr(source_edits.time, "monotonic", lambda: clock[0])
    with pytest.raises(ProjectSpiceSourceError):
        source_preparation._source_splices(
            case[0].root_path,
            case[-1][case[0].root_path],
            {},
            CaptureLimits(),
            1.0,
        )
    assert len(visited) == 1


def test_deadline_is_checked_during_property_traversal(tmp_path, monkeypatch):
    case, _report, _artifacts = _inputs(tmp_path, monkeypatch)
    checked = []
    clock = [0.0]
    original = source_edits.is_quoted_atom

    def watched(value):
        checked.append(value)
        result = original(value)
        if len(checked) == 2:
            clock[0] = 2.0
        return result

    monkeypatch.setattr(source_edits, "is_quoted_atom", watched)
    monkeypatch.setattr(source_edits.time, "monotonic", lambda: clock[0])
    with pytest.raises(ProjectSpiceSourceError):
        source_preparation._source_splices(
            case[0].root_path,
            case[-1][case[0].root_path],
            {},
            CaptureLimits(),
            1.0,
        )
    assert len(checked) == 2


def test_output_budget_refuses_before_splice_replacements_are_materialized(tmp_path, monkeypatch):
    case, report, artifacts = _inputs(tmp_path, monkeypatch)
    prepared = source_preparation.prepare_project_erc(
        case[0], case[1], limits=CaptureLimits(), deadline=time.monotonic() + 30
    )
    model_bytes = next(
        item.content for item in artifacts._artifacts if item.artifact_id == "models-main"
    )
    ceiling = sum(len(content) for _path, content in prepared.files) + len(model_bytes)
    limits = CaptureLimits(max_total_bytes=ceiling)
    monkeypatch.setattr(
        source_edits,
        "_field_text",
        lambda *_args: pytest.fail("must refuse before replacement materialization"),
    )
    with pytest.raises(ProjectSpiceSourceError):
        _prepare(case, report, artifacts, limits=limits)


def test_cumulative_artifact_budget_refuses_before_hashing_overflow(monkeypatch):
    declaration_digest = "sha256:" + "d" * 64
    first = b"a" * 20_000
    second = b"b" * 20_000
    captured = (
        _CapturedArtifact("model-a", "model-library", _sha(first), first),
        _CapturedArtifact("model-b", "model-library", _sha(second), second),
    )
    capture_digest = digest_document(
        "copper-mcp/electrical-artifact-capture/v1",
        {
            "declaration_digest": declaration_digest,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "role": item.role,
                    "digest": item.digest,
                    "size": len(item.content),
                }
                for item in captured
            ],
        },
    )
    artifacts = ElectricalArtifactCapture(
        captured,
        declaration_digest,
        capture_digest,
        PublicElectricalCaptureProjection(
            declaration_digest=declaration_digest,
            capture_digest=capture_digest,
            artifact_count=2,
            total_bytes=40_000,
        ),
    )
    paths = json.dumps(
        {
            "schema_version": "electrical-artifact-paths/v1",
            "artifacts": [
                {"artifact_id": "model-a", "path": "models/a.lib"},
                {"artifact_id": "model-b", "path": "models/b.lib"},
            ],
        }
    ).encode()
    original_sha = source_preparation._sha
    hashed = []

    def watched_sha(payload, deadline):
        hashed.append(payload)
        return original_sha(payload, deadline)

    monkeypatch.setattr(source_preparation, "_sha", watched_sha)
    with pytest.raises(ProjectSpiceSourceError):
        source_preparation._capture_artifacts(
            artifacts,
            paths,
            CaptureLimits(max_file_bytes=25_000, max_total_bytes=30_000),
            time.monotonic() + 30,
        )
    assert hashed == [first]
