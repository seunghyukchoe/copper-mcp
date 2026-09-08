"""Read-only project-bound nominal-DC observations; never an engineering sign-off."""

from __future__ import annotations

import math
import time
from collections.abc import Callable
from dataclasses import dataclass

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering._ngspice_execution import BACKEND_COMMAND, IMAGE, run_ngspice
from copper_mcp.engineering._operating_point_cases import _parse, resolve_operating_point_cases
from copper_mcp.engineering._operating_point_deck import (
    TITLE,
    compile_operating_point_deck,
    validate_native_diagnostics,
)
from copper_mcp.engineering.bom_reconciliation import _copy_controls
from copper_mcp.engineering.capture import CaptureLimits, capture_electrical_artifacts
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.project_spice_export import _run_project_spice_export_retained
from copper_mcp.engineering.project_spice_source import _document_digest
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.engineering.spice_operating_point import (
    SpiceOperatingPoint,
    parse_spice_operating_point,
)
from copper_mcp.optimization.confined_container import OperatorContainerRuntime, _stop_status


class ProjectOperatingPointError(ValueError):
    """Fixed refusal without proprietary source, model, case or native diagnostic data."""


def _check(deadline: float, cancelled: Callable[[], bool] | None) -> None:
    if time.monotonic() >= deadline or _stop_status(cancelled) is not None:
        raise ProjectOperatingPointError("project operating-point work stopped")


@dataclass(frozen=True, slots=True, repr=False)
class CaseOperatingPoint:
    case_id: str
    input_digest: str
    command_digest: str
    node_map: tuple[tuple[str, str], ...]
    observation: SpiceOperatingPoint

    def __repr__(self) -> str:
        return "<CaseOperatingPoint redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class ProjectOperatingPoints:
    native_export_digest: str
    case_document_digest: str
    binding_digest: str
    image_digest: str
    cases: tuple[CaseOperatingPoint, ...]

    def __repr__(self) -> str:
        return "<ProjectOperatingPoints redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        rows = []
        for case in self.cases:
            _check(deadline, None)
            rows.append(
                (
                    case.case_id,
                    case.input_digest,
                    case.command_digest,
                    case.node_map,
                    case.observation._digest(deadline),
                )
            )
        return _document_digest(
            "copper-mcp/project-operating-points/v1",
            {
                "native_export_digest": self.native_export_digest,
                "case_document_digest": self.case_document_digest,
                "binding_digest": self.binding_digest,
                "image_digest": self.image_digest,
                "cases": rows,
            },
            deadline,
        )

    def document(self) -> dict[str, object]:
        return {
            "report_digest": self.digest,
            "native_export_digest": self.native_export_digest,
            "case_document_digest": self.case_document_digest,
            "binding_digest": self.binding_digest,
            "image_digest": self.image_digest,
            "case_count": len(self.cases),
            "profile": "spice-passive-diode-nominal-dc/v1",
            "numerical_status": "repeatable_native_observations",
            "topology_preflight": "checked_within_profile",
            "load_duration_analysis": "not_run",
            "calibration": "not_run",
            "model_accuracy": "not_run",
            "engineering_validation": "inconclusive",
            "apply_authority": "none",
        }


def run_project_operating_points(
    project_capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    declaration_json: bytes,
    artifact_paths_json: bytes,
    bom_bindings_json: bytes,
    terminal_bindings_json: bytes,
    case_json: bytes,
    settings: Settings,
    runtime: OperatorContainerRuntime,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
    cancelled: Callable[[], bool] | None = None,
) -> ProjectOperatingPoints:
    """Run declared cases over one verified export, then recheck original source/model bytes."""
    result = None
    try:
        copied, active_limits, active = _copy_controls(settings, limits, time.monotonic(), deadline)
        if cancelled is not None and not callable(cancelled):
            raise ValueError
        _check(active, cancelled)
        _parse(case_json, active)  # Reject malformed requests before any native execution.
        retained = _run_project_spice_export_retained(
            project_capture,
            libraries,
            declaration_json,
            artifact_paths_json,
            bom_bindings_json,
            terminal_bindings_json,
            copied,
            deadline=active,
            limits=active_limits,
        )
        cases = resolve_operating_point_cases(
            case_json, declaration_json, retained.binding, deadline=active
        )
        completed_cases = []
        for case in cases.cases:
            _check(active, cancelled)
            deck = compile_operating_point_deck(retained, case, deadline=active)
            previous: CaseOperatingPoint | None = None
            for _repeat in range(2):
                _check(active, cancelled)
                native = run_ngspice(deck.payload, runtime, deadline=active, cancelled=cancelled)
                validate_native_diagnostics(
                    native.diagnostics, case, len(deck.vectors), deadline=active
                )
                observed = parse_spice_operating_point(
                    native.raw,
                    expected_title=TITLE,
                    expected_command=BACKEND_COMMAND,
                    expected_vectors=deck.vectors,
                    deadline=active,
                    max_bytes=256 * 1024,
                )
                if native.image_digest != IMAGE:
                    raise ValueError
                current = CaseOperatingPoint(
                    case.case_id,
                    native.input_digest,
                    native.command_digest,
                    deck.node_map,
                    observed,
                )
                if previous is not None and previous != current:
                    raise ProjectOperatingPointError("project operating-point replays disagree")
                previous = current
            if previous is None:
                raise ValueError
            completed_cases.append(previous)
        completed = ProjectOperatingPoints(
            retained.report._digest(active),
            cases.case_document_digest,
            cases.binding_digest,
            IMAGE,
            tuple(completed_cases),
        )
        _ = completed._digest(active)
        fresh = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            copied.workspace,
            limits=active_limits,
            deadline=active,
        )
        if fresh.digest != retained.binding.electrical_artifact_capture_digest:
            raise ProjectOperatingPointError("project operating-point artifacts changed")
        execution._verify_workspace_source(
            copied.workspace,
            tuple((item.path, item.content) for item in project_capture._files),
            active,
        )
        _check(active, cancelled)
        result = completed
    except (ValueError, TypeError, OSError, OverflowError, RuntimeError):
        pass
    if result is None:
        raise ProjectOperatingPointError(
            "project could not produce bounded operating-point observations"
        )
    return result
