"""Read-only native SPICE observation over internally derived, confined inputs."""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.bom_reconciliation import _copy_controls
from copper_mcp.engineering.capture import CaptureLimits, capture_electrical_artifacts
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.project_spice_model_binding import (
    ProjectSpiceModelBinding,
    run_project_spice_model_binding,
)
from copper_mcp.engineering.project_spice_source import (
    PreparedSpiceSource,
    _document_digest,
    prepare_project_spice_source,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.engineering.spice_export import (
    SpiceExportObservation,
    expected_spice_rows,
    parse_spice_export,
)
from copper_mcp.security import read_workspace_file

_FLAGS = ("sch", "export", "netlist", "--format", "spice")
_MAX_REPORT_BYTES = 16 * 1024 * 1024


class ProjectSpiceExportError(ValueError):
    """A fixed refusal without private source, model, path or native output context."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectSpiceExportError("project SPICE export deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class ProjectSpiceExport:
    binding_digest: str
    execution_digest: str
    command_digest: str
    executable_digest: str
    backend_authentication_digest: str
    native_syntax_digest: str
    backend_version: str
    observation: SpiceExportObservation

    def __repr__(self) -> str:
        return "<ProjectSpiceExport redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        return _document_digest(
            "copper-mcp/project-spice-export/v1",
            {
                "binding_digest": self.binding_digest,
                "execution_digest": self.execution_digest,
                "command_digest": self.command_digest,
                "executable_digest": self.executable_digest,
                "backend_authentication_digest": self.backend_authentication_digest,
                "native_syntax_digest": self.native_syntax_digest,
                "backend_version": self.backend_version,
                "rows": self.observation.rows,
                "model_paths": self.observation.model_paths,
            },
            deadline,
        )

    def document(self) -> dict[str, object]:
        return {
            "report_digest": self.digest,
            "binding_digest": self.binding_digest,
            "execution_digest": self.execution_digest,
            "command_digest": self.command_digest,
            "executable_digest": self.executable_digest,
            "backend_authentication_digest": self.backend_authentication_digest,
            "native_syntax_digest": self.native_syntax_digest,
            "backend_version": self.backend_version,
            "component_count": len(self.observation.rows),
            "model_library_count": len(self.observation.model_paths),
            "source_settings": "preserved_or_explicit_derivative_additions",
            "native_spice_export": "verified_within_profile",
            "simulation": "not_run",
            "model_accuracy": "not_run",
            "engineering_validation": "not_run",
            "apply_authority": "none",
        }


def _execute(
    prepared: PreparedSpiceSource,
    binding: ProjectSpiceModelBinding,
    settings: Settings,
    deadline: float,
) -> ProjectSpiceExport:
    expected = expected_spice_rows(binding, deadline=deadline)
    report_bytes = min(settings.max_drc_report_bytes, _MAX_REPORT_BYTES)
    with execution.open_project_execution_context(prepared.prepared, settings, deadline) as native:
        includes = tuple(
            (str(native.snapshot / path), path) for _artifact, path in prepared.model_paths
        )
        previous: bytes | None = None
        observation: SpiceExportObservation | None = None
        # Exactly two exports; retained output is bounded by twice the report ceiling.
        # This shares the enclosing deadline with BOM/pin acquisition; no retry resets it.
        for index in range(2):
            _check(deadline)
            output = native.temporary / f"spice-{index}.cir"
            diagnostics = native.temporary / f"spice-{index}.log"
            with diagnostics.open("wb") as stream:
                code = execution._invoke(
                    [
                        *native.base_command,
                        str(report_bytes),
                        str(native.executable),
                        *_FLAGS,
                        "--output",
                        str(output),
                        str(native.snapshot / prepared.prepared.root_path),
                    ],
                    settings=settings,
                    environment=native.environment,
                    deadline=deadline,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            native.verify()
            diagnostic = read_workspace_file(
                native.temporary, diagnostics.name, allowed_suffixes={".log"}, max_bytes=64 * 1024
            ).content
            if code != 0 or diagnostic:
                raise ProjectSpiceExportError("project SPICE export was not clean")
            payload = read_workspace_file(
                native.temporary, output.name, allowed_suffixes={".cir"}, max_bytes=report_bytes
            ).content
            current = parse_spice_export(
                payload,
                expected_rows=expected,
                expected_includes=includes,
                deadline=deadline,
                max_bytes=report_bytes,
            )
            if previous is not None and (previous != payload or observation != current):
                raise ProjectSpiceExportError("project SPICE exports disagree")
            previous, observation = payload, current
        if observation is None:
            raise ProjectSpiceExportError("project SPICE export is unavailable")
        command = _document_digest(
            "copper-mcp/project-spice-export-command/v1",
            {
                "executable_digest": native.executable_digest,
                "flags": _FLAGS,
                "input": prepared.prepared.root_path,
                "output": "<private-spice-export>",
                "repetitions": 2,
                "max_report_bytes": report_bytes,
                "native_syntax_digest": native.native_syntax_digest,
            },
            deadline,
        )
        return ProjectSpiceExport(
            prepared.binding_digest,
            prepared.prepared.execution_digest,
            command,
            native.executable_digest,
            native.authentication_digest,
            native.native_syntax_digest,
            native.version,
            observation,
        )


def run_project_spice_export(
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
) -> ProjectSpiceExport:
    """Derive all inputs internally and deliver only after final source/model freshness."""
    result = None
    try:
        copied, active_limits, active = _copy_controls(settings, limits, time.monotonic(), deadline)
        binding = run_project_spice_model_binding(
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
        artifacts = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            copied.workspace,
            deadline=active,
            limits=active_limits,
        )
        if artifacts.digest != binding.electrical_artifact_capture_digest:
            raise ProjectSpiceExportError("project SPICE export artifacts changed")
        prepared = prepare_project_spice_source(
            project_capture,
            libraries,
            binding,
            artifacts,
            artifact_paths_json,
            deadline=active,
            limits=active_limits,
        )
        source_files = tuple((item.path, item.content) for item in project_capture._files)
        execution._verify_workspace_source(copied.workspace, source_files, active)
        completed = _execute(prepared, binding, copied, active)
        _ = completed._digest(active)
        recaptured = capture_electrical_artifacts(
            declaration_json,
            artifact_paths_json,
            copied.workspace,
            deadline=active,
            limits=active_limits,
        )
        if recaptured.digest != artifacts.digest:
            raise ProjectSpiceExportError("project SPICE export artifacts changed")
        execution._verify_workspace_source(copied.workspace, source_files, active)
        _check(active)
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
        raise ProjectSpiceExportError(
            "project SPICE export could not produce complete bound observations"
        )
    return result
