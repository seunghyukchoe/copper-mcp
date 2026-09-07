"""Private read-only BOM agreement against freshly captured native components."""

from __future__ import annotations

import hashlib
import json
import math
import time
import unicodedata
from dataclasses import dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal, NoReturn

from pydantic import Field, TypeAdapter, ValidationError, model_validator

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.bom_csv import BomCsvError, BomCsvRow, parse_bom_csv
from copper_mcp.engineering.capture import (
    CaptureLimits,
    ElectricalArtifactCapture,
    ElectricalCaptureError,
    capture_electrical_artifacts,
)
from copper_mcp.engineering.component_netlist import NativeComponent
from copper_mcp.engineering.inputs import ElectricalInputs, Identifier, parse_electrical_inputs
from copper_mcp.engineering.project_components import (
    ProjectComponentInventory,
    ProjectComponentInventoryError,
    run_project_component_inventory,
)
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.optimization.contracts import (
    ClosedModel,
    Digest,
    OptimizationError,
    bounded_json,
)

_MISMATCH_NAMES = tuple(
    sorted(
        (
            "missing_component",
            "unexpected_component",
            "duplicate_component",
            "missing_row_binding",
            "duplicate_row_binding",
            "item_row_mismatch",
            "quantity_mismatch",
            "value_mismatch",
            "footprint_mismatch",
            "dnp_mismatch",
            "declared_schematic_mismatch",
        )
    )
)
_COVERAGE_FIELDS = ("reference", "value", "footprint", "quantity", "dnp")
_MAX_BOM_ROWS = 100_000
_MAX_BOM_REFERENCES = 100_000


class BomReconciliationError(ValueError):
    """A fixed, non-disclosing refusal from the reconciliation boundary."""


class _BindingItem(ClosedModel):
    item_id: Identifier
    references: Annotated[tuple[str, ...], Field(min_length=1, max_length=10_000)]

    @model_validator(mode="after")
    def canonical_references(self) -> _BindingItem:
        if tuple(sorted(set(self.references))) != self.references:
            raise ValueError("references must be unique and canonical")
        for reference in self.references:
            if (
                not reference
                or any(unicodedata.category(character) in {"Cc", "Cs"} for character in reference)
                or len(reference.encode("utf-8")) > 4096
            ):
                raise ValueError("reference is malformed")
        return self


class _BomComponentBindings(ClosedModel):
    identity_namespace = "copper-mcp/bom-component-bindings/v1"
    schema_version: Literal["bom-component-bindings/v1"]
    declaration_digest: Digest
    project_capture_digest: Digest
    items: Annotated[tuple[_BindingItem, ...], Field(min_length=1, max_length=512)]

    @model_validator(mode="after")
    def canonical_items(self) -> _BomComponentBindings:
        item_ids = tuple(item.item_id for item in self.items)
        if tuple(sorted(set(item_ids))) != item_ids:
            raise ValueError("items must be unique and canonical")
        return self


@dataclass(frozen=True, slots=True, repr=False)
class BomItemAssociation:
    """Private declaration-to-row association without model or engineering authority."""

    item_id: str
    artifact_id: str
    references: tuple[str, ...]
    declared_model_ids: tuple[str, ...]

    def __repr__(self) -> str:
        return "<BomItemAssociation redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class BomReconciliationReport:
    """Frozen private reconciliation result with a redacted disclosure document."""

    project_capture_digest: str
    native_inventory_digest: str
    declaration_digest: str
    electrical_artifact_capture_digest: str
    binding_document_digest: str
    metadata_agreement: Literal["pass", "fail", "inconclusive"]
    mismatch_counts: tuple[tuple[str, int], ...]
    reasons: tuple[Literal["no_component_scope"], ...]
    associations: tuple[BomItemAssociation, ...]
    native_component_count: int
    bom_row_count: int
    bom_reference_count: int
    unvalidated_extra_field_count: int

    def __repr__(self) -> str:
        return "<BomReconciliationReport redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        _check_deadline(deadline)
        associations = []
        for association in self.associations:
            _check_deadline(deadline)
            associations.append(
                {
                    "item_id": association.item_id,
                    "artifact_id": association.artifact_id,
                    "references": association.references,
                    "declared_model_ids": association.declared_model_ids,
                }
            )
        document = {
            "project_capture_digest": self.project_capture_digest,
            "native_inventory_digest": self.native_inventory_digest,
            "declaration_digest": self.declaration_digest,
            "electrical_artifact_capture_digest": self.electrical_artifact_capture_digest,
            "binding_document_digest": self.binding_document_digest,
            "metadata_agreement": self.metadata_agreement,
            "mismatch_counts": dict(self.mismatch_counts),
            "reasons": self.reasons,
            "associations": associations,
            "native_component_count": self.native_component_count,
            "bom_row_count": self.bom_row_count,
            "bom_reference_count": self.bom_reference_count,
            "unvalidated_extra_field_count": self.unvalidated_extra_field_count,
            "coverage_fields": _COVERAGE_FIELDS,
            "model_definition_validation": "not_run",
            "ratings_validation": "not_run",
            "engineering_verdict": "not_run",
            "apply_authority": "none",
        }
        _check_deadline(deadline)
        encoder = json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )
        digest = hashlib.sha256(b"copper-mcp/bom-reconciliation/v1\x00")
        for token in encoder.iterencode(document):
            _check_deadline(deadline)
            digest.update(token.encode("ascii"))
        _check_deadline(deadline)
        return "sha256:" + digest.hexdigest()

    def document(self) -> dict[str, object]:
        return {
            "project_capture_digest": self.project_capture_digest,
            "native_inventory_digest": self.native_inventory_digest,
            "declaration_digest": self.declaration_digest,
            "electrical_artifact_capture_digest": self.electrical_artifact_capture_digest,
            "binding_document_digest": self.binding_document_digest,
            "metadata_agreement": self.metadata_agreement,
            "mismatch_counts": dict(self.mismatch_counts),
            "reasons": list(self.reasons),
            "association_count": len(self.associations),
            "native_component_count": self.native_component_count,
            "bom_row_count": self.bom_row_count,
            "bom_reference_count": self.bom_reference_count,
            "unvalidated_extra_field_count": self.unvalidated_extra_field_count,
            "coverage_fields": list(_COVERAGE_FIELDS),
            "model_definition_validation": "not_run",
            "ratings_validation": "not_run",
            "engineering_verdict": "not_run",
            "apply_authority": "none",
        }


def _fail(message: str) -> NoReturn:
    raise BomReconciliationError(message)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("BOM reconciliation deadline expired")


def _parse_declaration(payload: bytes) -> ElectricalInputs:
    result: ElectricalInputs | None = None
    try:
        result = parse_electrical_inputs(payload)
    except (OptimizationError, ValueError, TypeError, RecursionError):
        pass
    if result is None or not result.bom_bindings:
        _fail("BOM reconciliation declaration is malformed")
    return result


def _parse_bindings(payload: bytes) -> _BomComponentBindings:
    result: _BomComponentBindings | None = None
    try:
        bounded_json(payload)
        result = TypeAdapter(_BomComponentBindings).validate_json(payload)
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
        _fail("BOM reconciliation bindings are malformed")
    return result


def _copy_controls(
    settings: Settings,
    limits: CaptureLimits | None,
    started: float,
    caller_deadline: float | None,
) -> tuple[Settings, CaptureLimits, float]:
    copied_settings: Settings | None = None
    copied_limits: CaptureLimits | None = None
    try:
        if type(settings) is Settings:
            copied_settings = replace(settings)
        if limits is None:
            copied_limits = CaptureLimits()
        elif type(limits) is CaptureLimits:
            copied_limits = CaptureLimits(
                limits.max_file_bytes,
                limits.max_total_bytes,
                limits.max_capture_seconds,
            )
    except (AttributeError, TypeError, ValueError):
        pass
    if copied_settings is None:
        _fail("BOM reconciliation settings are malformed")
    if copied_limits is None:
        _fail("BOM reconciliation limits are malformed")
    if (
        not isinstance(copied_settings.workspace, Path)
        or (
            copied_settings.kicad_cli is not None
            and not isinstance(copied_settings.kicad_cli, Path)
        )
        or type(copied_settings.kicad_timeout_seconds) is not int
        or not 1 <= copied_settings.kicad_timeout_seconds <= 3600
    ):
        _fail("BOM reconciliation settings are malformed")
    deadline = started + copied_settings.kicad_timeout_seconds
    if caller_deadline is not None:
        value: float | None = None
        if type(caller_deadline) in (int, float) and not isinstance(caller_deadline, bool):
            try:
                value = float(caller_deadline)
            except OverflowError:
                pass
        if value is None or not math.isfinite(value):
            _fail("BOM reconciliation deadline is malformed")
        deadline = min(deadline, value)
    _check_deadline(deadline)
    return copied_settings, copied_limits, deadline


def _capture(
    declaration_json: bytes,
    artifact_paths_json: bytes,
    workspace: Path,
    limits: CaptureLimits,
    deadline: float,
    *,
    freshness: bool,
) -> ElectricalArtifactCapture:
    result: ElectricalArtifactCapture | None = None
    try:
        result = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            workspace,
            limits=limits,
            deadline=deadline,
        )
    except (ElectricalCaptureError, OptimizationError, OSError, ValueError, TypeError):
        pass
    if result is None:
        if time.monotonic() >= deadline:
            _check_deadline(deadline)
        _fail(
            "BOM reconciliation freshness check failed"
            if freshness
            else "BOM reconciliation artifact capture failed"
        )
    return result


def _native_inventory(
    project_capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    limits: CaptureLimits,
    deadline: float,
) -> ProjectComponentInventory:
    result: ProjectComponentInventory | None = None
    try:
        result = run_project_component_inventory(
            project_capture,
            libraries,
            settings,
            limits=limits,
            deadline=deadline,
        )
    except (ProjectComponentInventoryError, OSError, ValueError, TypeError):
        pass
    if result is None:
        if time.monotonic() >= deadline:
            _check_deadline(deadline)
        _fail("BOM reconciliation native inventory failed")
    return result


def _parse_rows(
    capture: ElectricalArtifactCapture,
    declaration: ElectricalInputs,
    known_references: frozenset[str],
    deadline: float,
) -> dict[str, tuple[BomCsvRow, ...]]:
    roles = {artifact.artifact_id: artifact.role for artifact in declaration.source_artifacts}
    rows: dict[str, tuple[BomCsvRow, ...]] = {}
    remaining_rows = _MAX_BOM_ROWS
    remaining_references = _MAX_BOM_REFERENCES
    failed = False
    try:
        for artifact in capture._artifacts:
            _check_deadline(deadline)
            if roles.get(artifact.artifact_id) == "bom":
                parsed_rows = parse_bom_csv(
                    artifact.content,
                    known_references=known_references,
                    deadline=deadline,
                    max_rows=remaining_rows,
                    max_references=remaining_references,
                )
                rows[artifact.artifact_id] = parsed_rows
                remaining_rows -= len(parsed_rows)
                remaining_references -= sum(len(row.references) for row in parsed_rows)
                _check_deadline(deadline)
    except (BomCsvError, ValueError, TypeError):
        failed = True
    if failed or set(rows) != {
        artifact.artifact_id for artifact in declaration.source_artifacts if artifact.role == "bom"
    }:
        if time.monotonic() >= deadline:
            _check_deadline(deadline)
        _fail("BOM reconciliation CSV failed")
    return rows


def _verify_project_source(
    project_capture: SchematicProjectCapture, workspace: Path, deadline: float
) -> None:
    verified = False
    try:
        execution._verify_workspace_source(
            workspace,
            tuple((item.path, item.content) for item in project_capture._files),
            deadline,
        )
        verified = True
    except (OSError, ValueError, TypeError):
        pass
    if not verified:
        if time.monotonic() >= deadline:
            _check_deadline(deadline)
        _fail("BOM reconciliation freshness check failed")


def run_bom_reconciliation(
    project_capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    declaration_json: bytes,
    artifact_paths_json: bytes,
    bindings_json: bytes,
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> BomReconciliationReport:
    """Compare every declared BOM row with one freshly executed native inventory."""

    started = time.monotonic()
    copied_settings, copied_limits, active_deadline = _copy_controls(
        settings, limits, started, deadline
    )
    if type(project_capture) is not SchematicProjectCapture or type(libraries) is not tuple:
        _fail("BOM reconciliation inputs are malformed")
    declaration = _parse_declaration(declaration_json)
    _check_deadline(active_deadline)
    bindings = _parse_bindings(bindings_json)
    _check_deadline(active_deadline)
    declaration_digest = declaration.digest
    binding_document_digest = bindings.digest
    _check_deadline(active_deadline)
    declaration_items = {item.item_id for item in declaration.bom_bindings}
    binding_items = {item.item_id for item in bindings.items}
    if (
        bindings.declaration_digest != declaration_digest
        or bindings.project_capture_digest != project_capture.digest
        or declaration_items != binding_items
    ):
        _fail("BOM reconciliation bindings are malformed")
    _check_deadline(active_deadline)

    artifact_capture = _capture(
        declaration_json,
        artifact_paths_json,
        copied_settings.workspace,
        copied_limits,
        active_deadline,
        freshness=False,
    )
    _check_deadline(active_deadline)
    inventory = _native_inventory(
        project_capture, libraries, copied_settings, copied_limits, active_deadline
    )
    if type(inventory) is not ProjectComponentInventory or (
        inventory.capture_digest != project_capture.digest
        or inventory.capture_digest != bindings.project_capture_digest
    ):
        _fail("BOM reconciliation native inventory failed")
    if type(inventory.components) is not tuple or any(
        type(component) is not NativeComponent for component in inventory.components
    ):
        _fail("BOM reconciliation native inventory failed")
    native_by_reference = {component.reference: component for component in inventory.components}
    if len(native_by_reference) != len(inventory.components):
        _fail("BOM reconciliation native inventory failed")
    native_inventory_digest = None
    try:
        native_inventory_digest = inventory._digest(active_deadline)
    except ValueError:
        pass
    _check_deadline(active_deadline)
    if native_inventory_digest is None:
        _fail("BOM reconciliation native inventory failed")
    expected = {
        reference: component
        for reference, component in native_by_reference.items()
        if not component.excluded_from_bom
    }
    rows_by_artifact = _parse_rows(
        artifact_capture,
        declaration,
        frozenset(native_by_reference),
        active_deadline,
    )
    _check_deadline(active_deadline)

    counts = dict.fromkeys(_MISMATCH_NAMES, 0)
    schematic_digests = {
        item.digest
        for item in project_capture._files
        if PurePosixPath(item.path).suffix == ".kicad_sch"
    }
    counts["declared_schematic_mismatch"] = sum(
        artifact.role == "schematic" and artifact.artifact_digest not in schematic_digests
        for artifact in declaration.source_artifacts
    )

    locations: dict[tuple[str, tuple[str, ...]], tuple[str, int, BomCsvRow]] = {}
    occurrences: dict[str, list[BomCsvRow]] = {}
    selection_counts: dict[tuple[str, int], int] = {}
    bom_row_count = 0
    unvalidated_extra_field_count = 0
    for artifact_id, rows in sorted(rows_by_artifact.items()):
        for index, row in enumerate(rows):
            _check_deadline(active_deadline)
            locations[(artifact_id, row.references)] = (artifact_id, index, row)
            selection_counts[(artifact_id, index)] = 0
            bom_row_count += 1
            unvalidated_extra_field_count += len(row.extra_fields)
            for reference in row.references:
                occurrences.setdefault(reference, []).append(row)

    bom_references = set(occurrences)
    counts["missing_component"] = len(set(expected) - bom_references)
    counts["unexpected_component"] = len(bom_references - set(expected))
    counts["duplicate_component"] = sum(len(rows) > 1 for rows in occurrences.values())
    for reference in sorted(set(expected) & bom_references):
        _check_deadline(active_deadline)
        component = expected[reference]
        reference_rows = occurrences[reference]
        counts["value_mismatch"] += any(row.value != component.value for row in reference_rows)
        counts["footprint_mismatch"] += any(
            row.footprint != component.footprint for row in reference_rows
        )
        counts["dnp_mismatch"] += any(row.dnp != component.dnp for row in reference_rows)

    declared_bindings = {item.item_id: item for item in declaration.bom_bindings}
    models_by_item: dict[str, list[str]] = {}
    for model in declaration.model_bindings:
        models_by_item.setdefault(model.bom_item_id, []).append(model.model_id)
    associations: list[BomItemAssociation] = []
    for item in bindings.items:
        _check_deadline(active_deadline)
        declared = declared_bindings[item.item_id]
        location = locations.get((declared.artifact_id, item.references))
        if location is None:
            counts["item_row_mismatch"] += 1
            continue
        artifact_id, row_index, row = location
        selection_counts[(artifact_id, row_index)] += 1
        counts["quantity_mismatch"] += declared.quantity != row.quantity
        associations.append(
            BomItemAssociation(
                item.item_id,
                artifact_id,
                row.references,
                tuple(sorted(models_by_item.get(item.item_id, ()))),
            )
        )
    counts["missing_row_binding"] = sum(count == 0 for count in selection_counts.values())
    counts["duplicate_row_binding"] = sum(count > 1 for count in selection_counts.values())
    mismatch_counts = tuple(sorted(counts.items()))
    reasons: tuple[Literal["no_component_scope"], ...] = (
        ("no_component_scope",) if not expected else ()
    )
    agreement: Literal["pass", "fail", "inconclusive"]
    if any(counts.values()):
        agreement = "fail"
    elif reasons:
        agreement = "inconclusive"
    else:
        agreement = "pass"

    _check_deadline(active_deadline)
    report = BomReconciliationReport(
        project_capture.digest,
        native_inventory_digest,
        declaration_digest,
        artifact_capture.digest,
        binding_document_digest,
        agreement,
        mismatch_counts,
        reasons,
        tuple(sorted(associations, key=lambda association: association.item_id)),
        len(inventory.components),
        bom_row_count,
        len(bom_references),
        unvalidated_extra_field_count,
    )
    _ = report._digest(active_deadline)
    _check_deadline(active_deadline)

    recaptured = _capture(
        declaration_json,
        artifact_paths_json,
        copied_settings.workspace,
        copied_limits,
        active_deadline,
        freshness=True,
    )
    if recaptured.digest != artifact_capture.digest:
        _fail("BOM reconciliation freshness check failed")
    _verify_project_source(project_capture, copied_settings.workspace, active_deadline)
    _check_deadline(active_deadline)
    return report
