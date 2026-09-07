"""Private complete model-terminal joins over captured bytes and fresh native nets.

This operation does not export or execute SPICE, rewrite source simulation settings,
validate model accuracy, or grant an engineering/apply verdict.
"""

from __future__ import annotations

import hashlib
import json
import math
import subprocess
import time
from dataclasses import asdict, dataclass, fields

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering._spice_terminal_binding import (
    BoundSpiceReference,
    join_terminal_bindings,
    parse_terminal_bindings,
)
from copper_mcp.engineering.bom_reconciliation import _copy_controls, _run_bom_reconciliation
from copper_mcp.engineering.capture import CaptureLimits, capture_electrical_artifacts
from copper_mcp.engineering.inputs import parse_electrical_inputs
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.project_pin_net_map import run_project_pin_net_map
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.engineering.spice_model_library import _digest as _definition_digest


class ProjectSpiceModelBindingError(ValueError):
    """Fixed context-free refusal, never source/model/terminal metadata."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectSpiceModelBindingError("project SPICE binding deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class ProjectSpiceModelBinding:
    project_capture_digest: str
    declaration_digest: str
    electrical_artifact_capture_digest: str
    bom_report_digest: str
    pin_map_digest: str
    binding_document_digest: str
    references: tuple[BoundSpiceReference, ...]

    def __repr__(self) -> str:
        return "<ProjectSpiceModelBinding redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        digest = hashlib.sha256(b"copper-mcp/project-spice-model-binding/v1\x00")
        encoder = json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )

        def write(value: object) -> None:
            for token in encoder.iterencode(value):
                _check(deadline)
                digest.update(token.encode("ascii"))
            digest.update(b"\x00")

        write(
            (
                self.project_capture_digest,
                self.declaration_digest,
                self.electrical_artifact_capture_digest,
                self.bom_report_digest,
                self.pin_map_digest,
                self.binding_document_digest,
            )
        )
        for item in self.references:
            _check(deadline)
            write(
                (
                    item.model_id,
                    item.artifact_id,
                    item.reference,
                    item.definition.name,
                    item.definition.kind,
                    item.definition.ports,
                    _definition_digest(item.definition.source, deadline),
                )
            )
            for pin in item.pins:
                _check(deadline)
                node_document = {
                    field.name: getattr(pin.node, field.name)
                    for field in fields(pin.node)
                    if field.name != "aliases"
                }
                aliases = []
                for alias in pin.node.aliases:
                    _check(deadline)
                    aliases.append(asdict(alias))
                node_document["aliases"] = tuple(aliases)
                write((node_document, pin.port))
        _check(deadline)
        return "sha256:" + digest.hexdigest()

    def document(self) -> dict[str, object]:
        return {
            "report_digest": self.digest,
            "project_capture_digest": self.project_capture_digest,
            "declaration_digest": self.declaration_digest,
            "electrical_artifact_capture_digest": self.electrical_artifact_capture_digest,
            "bom_report_digest": self.bom_report_digest,
            "pin_map_digest": self.pin_map_digest,
            "binding_document_digest": self.binding_document_digest,
            "model_count": len({item.model_id for item in self.references}),
            "reference_count": len(self.references),
            "logical_pin_count": sum(len(item.pins) for item in self.references),
            "source_pin_occurrence_count": sum(
                len(pin.node.aliases) for item in self.references for pin in item.pins
            ),
            "no_connect_count": sum(
                pin.port is None for item in self.references for pin in item.pins
            ),
            "binding_scope": "declared_spice_models_complete_nonvirtual_pin_join",
            "source_settings_validation": "not_run",
            "native_spice_export": "not_run",
            "simulation": "not_run",
            "model_accuracy": "not_run",
            "engineering_validation": "not_run",
            "apply_authority": "none",
        }


def run_project_spice_model_binding(
    project_capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    declaration_json: bytes,
    artifact_paths_json: bytes,
    bom_bindings_json: bytes,
    terminal_bindings_json: bytes,
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectSpiceModelBinding:
    """Derive native evidence internally, join actual models, then recheck source freshness."""
    result = None
    try:
        copied, active_limits, active_deadline = _copy_controls(
            settings, limits, time.monotonic(), deadline
        )
        if type(project_capture) is not SchematicProjectCapture or type(libraries) is not tuple:
            raise ProjectSpiceModelBindingError("project SPICE binding inputs are malformed")
        declaration = parse_electrical_inputs(declaration_json)
        document = parse_terminal_bindings(terminal_bindings_json, deadline=active_deadline)
        _check(active_deadline)
        if (
            document.declaration_digest != declaration.digest
            or document.project_capture_digest != project_capture.digest
        ):
            raise ProjectSpiceModelBindingError("project SPICE binding identities disagree")
        artifacts = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            copied.workspace,
            limits=active_limits,
            deadline=active_deadline,
        )
        bom, bom_inventory = _run_bom_reconciliation(
            project_capture,
            libraries,
            declaration_json,
            artifact_paths_json,
            bom_bindings_json,
            copied,
            deadline=active_deadline,
            limits=active_limits,
        )
        _check(active_deadline)
        if (
            bom.metadata_agreement != "pass"
            or bom.electrical_artifact_capture_digest != artifacts.digest
        ):
            raise ProjectSpiceModelBindingError("project SPICE binding BOM evidence disagrees")
        pin_map = run_project_pin_net_map(
            project_capture, libraries, copied, deadline=active_deadline, limits=active_limits
        )
        references = join_terminal_bindings(
            document,
            declaration,
            artifacts,
            bom,
            pin_map,
            bom_inventory=bom_inventory,
            deadline=active_deadline,
        )
        completed = ProjectSpiceModelBinding(
            project_capture.digest,
            declaration.digest,
            artifacts.digest,
            bom._digest(active_deadline),
            pin_map._digest(active_deadline),
            document.digest,
            references,
        )
        _ = completed._digest(active_deadline)
        # Hash first, then re-read all captured artifacts and the original project; matching
        # identifiers or a prior native report never stand in for this final freshness check.
        fresh = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            copied.workspace,
            limits=active_limits,
            deadline=active_deadline,
        )
        if fresh.digest != artifacts.digest:
            raise ProjectSpiceModelBindingError("project SPICE binding artifacts changed")
        execution._verify_workspace_source(
            copied.workspace,
            tuple((item.path, item.content) for item in project_capture._files),
            active_deadline,
        )
        _check(active_deadline)
        result = completed
    except (
        ValueError,
        TypeError,
        OSError,
        OverflowError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        pass
    if result is None:
        raise ProjectSpiceModelBindingError(
            "project SPICE binding could not produce complete bound inputs"
        )
    return result
