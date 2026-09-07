"""Private complete joins between declared SPICE terminals and native pin/net evidence."""

from __future__ import annotations

import math
import time
import unicodedata
from dataclasses import dataclass
from typing import Annotated, Literal, NoReturn, cast

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from copper_mcp.engineering.bom_reconciliation import BomItemAssociation, BomReconciliationReport
from copper_mcp.engineering.capture import ElectricalArtifactCapture, _CapturedArtifact
from copper_mcp.engineering.component_netlist import ComponentNetlist
from copper_mcp.engineering.inputs import ElectricalInputs, Identifier
from copper_mcp.engineering.native_pin_net_map import NativePinNet, NativePinNetMap
from copper_mcp.engineering.project_components import ProjectComponentInventory
from copper_mcp.engineering.project_pin_net_map import ProjectPinNetMap
from copper_mcp.engineering.spice_model_library import (
    SpiceDefinition,
    SpiceModelLibraryError,
    parse_spice_model_library,
)
from copper_mcp.engineering.spice_model_library import (
    _digest as _library_digest,
)
from copper_mcp.optimization.contracts import ClosedModel, Digest, OptimizationError, bounded_json

_TEXT = Annotated[str, StringConstraints(min_length=1, max_length=4096)]
_SPICE_NAME = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z_][A-Za-z0-9_]{0,63}$", min_length=1, max_length=64)
]
_SPICE_PORT = Annotated[
    str,
    StringConstraints(
        pattern=r"^([A-Za-z_][A-Za-z0-9_]{0,63}|[0-9]{1,64})$", min_length=1, max_length=64
    ),
]
_MAX_JOIN_REFERENCES = 10_000
_MAX_JOIN_PINS = 16_384
_MAX_JOIN_ALIASES = 16_384
_MAX_JOIN_MODEL_BYTES = 16 * 1024 * 1024


class SpiceTerminalBindingError(ValueError):
    """A fixed refusal that never discloses captured sources, pin names, or bindings."""


def _fail(message: str = "SPICE terminal bindings are malformed") -> NoReturn:
    raise SpiceTerminalBindingError(message)


def _deadline(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        _fail()
    failed = False
    result = 0.0
    try:
        result = float(cast(int | float, value))
    except (OverflowError, ValueError):
        failed = True
    if failed or not math.isfinite(result):
        _fail()
    return result


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("SPICE terminal bindings deadline expired")


def _canonical_text(value: str) -> bool:
    return not any(unicodedata.category(character) in {"Cc", "Cs"} for character in value)


class _PortBinding(ClosedModel):
    kind: Literal["port"]
    port: _SPICE_PORT


class _NoConnectBinding(ClosedModel):
    kind: Literal["nc"]


class TerminalPinBinding(ClosedModel):
    pin_number: _TEXT
    binding: _PortBinding | _NoConnectBinding

    @model_validator(mode="after")
    def valid_pin_number(self) -> TerminalPinBinding:
        if not _canonical_text(self.pin_number):
            raise ValueError("pin number is malformed")
        return self


class TerminalReferenceBinding(ClosedModel):
    reference: _TEXT
    pins: Annotated[tuple[TerminalPinBinding, ...], Field(min_length=1, max_length=64)]

    @model_validator(mode="after")
    def canonical_pins(self) -> TerminalReferenceBinding:
        if (
            not _canonical_text(self.reference)
            or tuple(sorted(pin.pin_number for pin in self.pins))
            != tuple(pin.pin_number for pin in self.pins)
            or len({pin.pin_number for pin in self.pins}) != len(self.pins)
        ):
            raise ValueError("reference pins must be unique and canonical")
        return self


class TerminalModelBinding(ClosedModel):
    model_id: Identifier
    definition_name: _SPICE_NAME
    definition_digest: Digest
    references: Annotated[
        tuple[TerminalReferenceBinding, ...], Field(min_length=1, max_length=10_000)
    ]

    @model_validator(mode="after")
    def canonical_references(self) -> TerminalModelBinding:
        values = tuple(reference.reference for reference in self.references)
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("model references must be unique and canonical")
        return self


class TerminalBindings(ClosedModel):
    """Closed declaration with no execution or simulation authority."""

    identity_namespace = "copper-mcp/project-spice-terminal-bindings/v1"
    schema_version: Literal["project-spice-terminal-bindings/v1"]
    declaration_digest: Digest
    project_capture_digest: Digest
    models: Annotated[tuple[TerminalModelBinding, ...], Field(min_length=1, max_length=128)]

    @model_validator(mode="after")
    def canonical_models(self) -> TerminalBindings:
        values = tuple(model.model_id for model in self.models)
        if tuple(sorted(values)) != values or len(set(values)) != len(values):
            raise ValueError("models must be unique and canonical")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class BoundSpicePin:
    node: NativePinNet
    port: str | None

    def __repr__(self) -> str:
        return "<BoundSpicePin redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class BoundSpiceReference:
    model_id: str
    artifact_id: str
    reference: str
    definition: SpiceDefinition
    pins: tuple[BoundSpicePin, ...]

    def __repr__(self) -> str:
        return "<BoundSpiceReference redacted>"


def parse_terminal_bindings(payload: bytes, *, deadline: float) -> TerminalBindings:
    """Decode one bounded, canonical terminal-binding declaration without performing a join."""

    active_deadline = _deadline(deadline)
    _check(active_deadline)
    result: TerminalBindings | None = None
    try:
        bounded_json(payload)
        result = TypeAdapter(TerminalBindings).validate_json(payload)
    except (
        OptimizationError,
        ValidationError,
        UnicodeError,
        ValueError,
        TypeError,
        RecursionError,
    ):
        pass
    if result is None:
        _fail()
    _check(active_deadline)
    return result


def _capture_artifacts(
    declaration: ElectricalInputs, capture: ElectricalArtifactCapture, deadline: float
) -> dict[str, _CapturedArtifact]:
    if (
        type(capture) is not ElectricalArtifactCapture
        or capture.declaration_digest != declaration.digest
    ):
        _fail()
    if type(capture._artifacts) is not tuple:
        _fail()
    model_bytes = 0
    for artifact in capture._artifacts:
        _check(deadline)
        if type(artifact) is not _CapturedArtifact or type(artifact.content) is not bytes:
            _fail()
        if artifact.role == "model-library":
            model_bytes += len(artifact.content)
            if model_bytes > _MAX_JOIN_MODEL_BYTES:
                _fail()
    declared = {artifact.artifact_id: artifact for artifact in declaration.source_artifacts}
    actual: dict[str, _CapturedArtifact] = {}
    for artifact in capture._artifacts:
        _check(deadline)
        if type(artifact) is not _CapturedArtifact or artifact.artifact_id in actual:
            _fail()
        if (
            artifact.artifact_id not in declared
            or artifact.role != declared[artifact.artifact_id].role
            or type(artifact.content) is not bytes
            or artifact.digest != _library_digest(artifact.content, deadline)
            or artifact.digest != declared[artifact.artifact_id].artifact_digest
        ):
            _fail()
        actual[artifact.artifact_id] = artifact
    if set(actual) != set(declared):
        _fail()
    return actual


def _complete_bom_scope(
    declaration: ElectricalInputs, report: BomReconciliationReport, deadline: float
) -> dict[str, str]:
    if type(report) is not BomReconciliationReport or report.metadata_agreement != "pass":
        _fail()
    if (
        report.declaration_digest != declaration.digest
        or report.reasons
        or any(type(count) is not int or count != 0 for _, count in report.mismatch_counts)
    ):
        _fail()
    expected_items = {item.item_id for item in declaration.bom_bindings}
    associations: dict[str, BomItemAssociation] = {}
    references: dict[str, str] = {}
    for association in report.associations:
        _check(deadline)
        if association.item_id in associations or association.item_id not in expected_items:
            _fail()
        associations[association.item_id] = association
        for reference in association.references:
            if reference in references:
                _fail()
            references[reference] = association.item_id
    if set(associations) != expected_items or not references:
        _fail()
    all_models_by_item: dict[str, list[str]] = {}
    spice_by_item: dict[str, list[str]] = {}
    for model in declaration.model_bindings:
        _check(deadline)
        all_models_by_item.setdefault(model.bom_item_id, []).append(model.model_id)
        if model.model_kind == "spice":
            spice_by_item.setdefault(model.bom_item_id, []).append(model.model_id)
    if set(spice_by_item) != expected_items or any(
        len(models) != 1 for models in spice_by_item.values()
    ):
        _fail()
    for item_id, association in associations.items():
        _check(deadline)
        if tuple(association.declared_model_ids) != tuple(all_models_by_item[item_id]):
            _fail()
    return references


def _check_document_bounds(document: TerminalBindings, deadline: float) -> None:
    references = 0
    pins = 0
    for model in document.models:
        for reference in model.references:
            _check(deadline)
            references += 1
            pins += len(reference.pins)
            if references > _MAX_JOIN_REFERENCES or pins > _MAX_JOIN_PINS:
                _fail()


def _check_native_bounds(pin_map: ProjectPinNetMap, deadline: float) -> None:
    aliases = 0
    if type(pin_map.mapping.nodes) is not tuple or len(pin_map.mapping.nodes) > _MAX_JOIN_PINS:
        _fail()
    for node in pin_map.mapping.nodes:
        _check(deadline)
        if type(node) is not NativePinNet or type(node.aliases) is not tuple:
            _fail()
        aliases += len(node.aliases)
        if aliases > _MAX_JOIN_ALIASES:
            _fail()


def join_terminal_bindings(
    document: TerminalBindings,
    declaration: ElectricalInputs,
    artifact_capture: ElectricalArtifactCapture,
    bom_report: BomReconciliationReport,
    pin_map: ProjectPinNetMap,
    *,
    bom_inventory: ProjectComponentInventory,
    deadline: float,
) -> tuple[BoundSpiceReference, ...]:
    """Join complete declared SPICE terminals to one immutable native pin observation set."""

    active_deadline = _deadline(deadline)
    _check(active_deadline)
    result: tuple[BoundSpiceReference, ...] | None = None
    try:
        if type(document) is not TerminalBindings or type(declaration) is not ElectricalInputs:
            _fail()
        if document.declaration_digest != declaration.digest:
            _fail()
        _check_document_bounds(document, active_deadline)
        artifacts = _capture_artifacts(declaration, artifact_capture, active_deadline)
        if (
            bom_report.project_capture_digest != document.project_capture_digest
            or bom_report.electrical_artifact_capture_digest != artifact_capture.digest
            or type(pin_map) is not ProjectPinNetMap
            or pin_map.capture_digest != document.project_capture_digest
            or type(pin_map.mapping) is not NativePinNetMap
        ):
            _fail()
        _check_native_bounds(pin_map, active_deadline)
        if (
            type(bom_inventory) is not ProjectComponentInventory
            or bom_inventory.capture_digest != document.project_capture_digest
            or len(bom_inventory.components) != bom_report.native_component_count
            or any(
                getattr(bom_inventory, field) != getattr(pin_map, field)
                for field in (
                    "execution_digest",
                    "native_syntax_digest",
                    "executable_digest",
                    "backend_authentication_digest",
                )
            )
            or bom_inventory._digest(active_deadline) != bom_report.native_inventory_digest
            or ComponentNetlist(
                bom_inventory.components, bom_inventory.sheet_paths, bom_inventory.backend_version
            )._digest(active_deadline)
            != pin_map.mapping.component_inventory_digest
        ):
            _fail()
        _ = pin_map._digest(active_deadline)
        references_to_items = _complete_bom_scope(declaration, bom_report, active_deadline)

        spice_models = {
            model.model_id: model
            for model in declaration.model_bindings
            if model.model_kind == "spice"
        }
        parsed_models = {model.model_id: model for model in document.models}
        if set(parsed_models) != set(spice_models):
            _fail()
        model_artifacts = {model.artifact_id for model in spice_models.values()}
        libraries = {}
        for artifact_id in model_artifacts:
            _check(active_deadline)
            artifact = artifacts.get(artifact_id)
            if artifact is None or artifact.role != "model-library":
                _fail()
            libraries[artifact_id] = parse_spice_model_library(
                artifact.content, deadline=active_deadline
            )

        native: dict[tuple[str, str], NativePinNet] = {}
        for node in pin_map.mapping.nodes:
            _check(active_deadline)
            if type(node) is not NativePinNet or (node.reference, node.pin_number) in native:
                _fail()
            native[(node.reference, node.pin_number)] = node
        if not native or {reference for reference, _ in native} != set(references_to_items):
            _fail()

        bound: list[BoundSpiceReference] = []
        seen_nodes: set[tuple[str, str]] = set()
        seen_references: set[str] = set()
        for model_id, model in parsed_models.items():
            _check(active_deadline)
            declaration_model = spice_models[model_id]
            library = libraries[declaration_model.artifact_id]
            matching = tuple(
                definition
                for definition in library.definitions
                if definition.name.casefold() == model.definition_name.casefold()
            )
            if (
                len(matching) != 1
                or _library_digest(matching[0].source, active_deadline) != model.definition_digest
            ):
                _fail()
            definition = matching[0]
            expected_ports = set(definition.ports)
            for item in model.references:
                _check(active_deadline)
                if (
                    item.reference in seen_references
                    or references_to_items.get(item.reference) != declaration_model.bom_item_id
                ):
                    _fail()
                seen_references.add(item.reference)
                ports: set[str] = set()
                pins: list[BoundSpicePin] = []
                for pin in item.pins:
                    _check(active_deadline)
                    key = item.reference, pin.pin_number
                    mapped_node = native.get(key)
                    if mapped_node is None or key in seen_nodes:
                        _fail()
                    seen_nodes.add(key)
                    if pin.binding.kind == "nc":
                        if not mapped_node.no_connect:
                            _fail()
                        pins.append(BoundSpicePin(mapped_node, None))
                    else:
                        if (
                            mapped_node.no_connect
                            or pin.binding.port not in expected_ports
                            or pin.binding.port in ports
                        ):
                            _fail()
                        ports.add(pin.binding.port)
                        pins.append(BoundSpicePin(mapped_node, pin.binding.port))
                if ports != expected_ports:
                    _fail()
                bound.append(
                    BoundSpiceReference(
                        model_id,
                        declaration_model.artifact_id,
                        item.reference,
                        definition,
                        tuple(pins),
                    )
                )
        if seen_nodes != set(native) or seen_references != set(references_to_items):
            _fail()
        _check(active_deadline)
        result = tuple(bound)
    except (
        AttributeError,
        KeyError,
        SpiceModelLibraryError,
        SpiceTerminalBindingError,
        TypeError,
        ValueError,
        OverflowError,
    ):
        pass
    if result is None:
        _fail()
    return result
