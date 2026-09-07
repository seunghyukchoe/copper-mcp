"""Bounded native KiCad component inventory over captured project inputs only."""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.component_netlist import (
    ComponentNetlist,
    NativeComponent,
    parse_component_netlist,
)
from copper_mcp.engineering.project_erc_inputs import (
    PreparedProjectErc,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_FLAGS = ("sch", "export", "netlist", "--format", "kicadxml")
_DIAGNOSTIC_BYTES = 64 * 1024


class ProjectComponentInventoryError(ValueError):
    """Fixed redacted refusal; it never carries private project or tool context."""


@dataclass(frozen=True, slots=True, repr=False)
class ProjectComponentInventory:
    capture_digest: str
    execution_digest: str
    native_syntax_digest: str
    command_digest: str
    executable_digest: str
    backend_authentication_digest: str
    backend_version: str
    components: tuple[NativeComponent, ...]
    sheet_paths: tuple[str, ...]

    def __repr__(self) -> str:
        return "<ProjectComponentInventory redacted>"

    @property
    def inventory_digest(self) -> str:
        """Bind every projection to the immutable records, not a copied prior digest."""

        return ComponentNetlist(self.components, self.sheet_paths, self.backend_version).digest

    @property
    def native_inventory_digest(self) -> str:
        return self.inventory_digest

    @property
    def digest(self) -> str:
        return digest_document(
            "copper-mcp/project-component-inventory/v1",
            {
                "capture_digest": self.capture_digest,
                "execution_digest": self.execution_digest,
                "native_syntax_digest": self.native_syntax_digest,
                "command_digest": self.command_digest,
                "executable_digest": self.executable_digest,
                "backend_authentication_digest": self.backend_authentication_digest,
                "backend_version": self.backend_version,
                "native_inventory_digest": self.inventory_digest,
            },
        )

    def document(self) -> dict[str, object]:
        return {
            "capture_digest": self.capture_digest,
            "execution_digest": self.execution_digest,
            "native_syntax_digest": self.native_syntax_digest,
            "command_digest": self.command_digest,
            "executable_digest": self.executable_digest,
            "backend_authentication_digest": self.backend_authentication_digest,
            "backend_version": self.backend_version,
            "component_count": len(self.components),
            "sheet_count": len(self.sheet_paths),
            "native_inventory_digest": self.inventory_digest,
            "model_validation": "not_run",
            "bom_validation": "not_run",
            "engineering_verdict": "not_run",
            "apply_authority": "none",
        }


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectComponentInventoryError("project component inventory deadline expired")


def _expected_sheet_paths(capture: SchematicProjectCapture, deadline: float) -> tuple[str, ...]:
    paths = []
    for instance in capture.hierarchy.instance_paths:
        _check(deadline)
        identifiers = instance.uuid_path.split("/")[1:]
        if not identifiers:
            raise ProjectComponentInventoryError("project component hierarchy is malformed")
        paths.append("/" if len(identifiers) == 1 else "/" + "/".join(identifiers[1:]) + "/")
    result = tuple(sorted(paths))
    if len(set(result)) != len(result):
        raise ProjectComponentInventoryError("project component hierarchy is ambiguous")
    return result


def _execute(
    prepared: PreparedProjectErc,
    capture: SchematicProjectCapture,
    settings: Settings,
    deadline: float,
) -> ProjectComponentInventory:
    expected_sheet_paths = _expected_sheet_paths(capture, deadline)
    with execution.open_project_execution_context(prepared, settings, deadline) as context:
        observations: list[ComponentNetlist] = []
        for index in range(2):
            _check(deadline)
            output = context.temporary / f"components-{index}.xml"
            diagnostics = context.temporary / f"components-{index}.log"
            with diagnostics.open("wb") as stream:
                code = execution._invoke(
                    [
                        *context.base_command,
                        str(settings.max_drc_report_bytes),
                        str(context.executable),
                        *_FLAGS,
                        "--output",
                        str(output),
                        str(context.snapshot / prepared.root_path),
                    ],
                    settings=settings,
                    environment=context.environment,
                    deadline=deadline,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            context.verify()
            diagnostic_bytes = read_workspace_file(
                context.temporary,
                diagnostics.name,
                allowed_suffixes={".log"},
                max_bytes=_DIAGNOSTIC_BYTES,
            ).content
            if code != 0 or diagnostic_bytes:
                raise ProjectComponentInventoryError(
                    "project component native execution was not clean"
                )
            payload = read_workspace_file(
                context.temporary,
                output.name,
                allowed_suffixes={".xml"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            observation = parse_component_netlist(
                payload,
                expected_source=str(context.snapshot / prepared.root_path),
                expected_sheet_paths=expected_sheet_paths,
                deadline=deadline,
                max_bytes=settings.max_drc_report_bytes,
            )
            if observation.backend_version != context.version:
                raise ProjectComponentInventoryError("project component backend binding is invalid")
            observations.append(observation)
        _check(deadline)
        if len(observations) != 2 or observations[0] != observations[1]:
            raise ProjectComponentInventoryError("project component observations disagree")
        command_digest = digest_document(
            "copper-mcp/project-component-inventory-command/v1",
            {
                "executable_digest": context.executable_digest,
                "flags": _FLAGS,
                "input": prepared.root_path,
                "output": "<private-netlist>",
                "repetitions": 2,
                "native_syntax_digest": context.native_syntax_digest,
            },
        )
        observation = observations[0]
        result = ProjectComponentInventory(
            prepared.capture_digest,
            prepared.execution_digest,
            context.native_syntax_digest,
            command_digest,
            context.executable_digest,
            context.authentication_digest,
            observation.backend_version,
            observation.components,
            observation.sheet_paths,
        )
        _ = result.inventory_digest
        _check(deadline)
        return result


def run_project_component_inventory(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectComponentInventory:
    """Capture twice from a sealed native closure; this grants no BOM or engineering verdict."""

    result: ProjectComponentInventory | None = None
    try:
        started = time.monotonic()
        if type(settings) is not Settings:
            raise ProjectComponentInventoryError("project component settings are malformed")
        settings = replace(settings)
        if not isinstance(settings.workspace, Path) or (
            settings.kicad_cli is not None and not isinstance(settings.kicad_cli, Path)
        ):
            raise ProjectComponentInventoryError("project component paths are malformed")
        for value, maximum in (
            (settings.kicad_timeout_seconds, 3600),
            (settings.max_drc_report_bytes, 64 * 1024 * 1024),
            (settings.max_board_bytes, 128 * 1024 * 1024),
            (settings.max_drc_context_bytes, 512 * 1024 * 1024),
            (settings.max_drc_context_files, 100_000),
            (settings.max_drc_context_scan_seconds, 120),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ProjectComponentInventoryError(
                    "project component settings exceed their bounds"
                )
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ProjectComponentInventoryError("project component deadline is malformed")
        active_deadline = min(
            started + settings.kicad_timeout_seconds, deadline if deadline is not None else math.inf
        )
        _check(active_deadline)
        if limits is not None and type(limits) is not CaptureLimits:
            raise ProjectComponentInventoryError("project component limits are malformed")
        active_limits = (
            CaptureLimits(max_capture_seconds=30)
            if limits is None
            else CaptureLimits(
                limits.max_file_bytes, limits.max_total_bytes, limits.max_capture_seconds
            )
        )
        prepared = prepare_project_erc(
            capture, libraries, limits=active_limits, deadline=active_deadline
        )
        source_files = tuple((item.path, item.content) for item in capture._files)
        execution._verify_workspace_source(settings.workspace, source_files, active_deadline)
        completed = _execute(prepared, capture, settings, active_deadline)
        execution._verify_workspace_source(settings.workspace, source_files, active_deadline)
        result = completed
    except (
        ValueError,
        OSError,
        OverflowError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        pass
    if result is None:
        raise ProjectComponentInventoryError(
            "project component inventory could not produce bound native evidence"
        )
    return result


__all__ = [
    "ProjectComponentInventory",
    "ProjectComponentInventoryError",
    "run_project_component_inventory",
]
