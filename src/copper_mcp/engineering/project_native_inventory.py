"""Joint native component and pin/net acquisition from one authenticated context."""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import cast

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.component_netlist import ComponentNetlist, parse_component_netlist
from copper_mcp.engineering.native_pin_net_map import NativePinNetMap, parse_native_pin_net_map
from copper_mcp.engineering.project_components import (
    ProjectComponentInventory,
    _expected_sheet_paths,
)
from copper_mcp.engineering.project_erc_inputs import (
    PreparedProjectErc,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.project_pin_census import ProjectPinCensus, derive_project_pin_census
from copper_mcp.engineering.project_pin_net_map import ProjectPinNetMap
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_FLAGS = ("sch", "export", "netlist", "--format", "kicadxml")
_DIAGNOSTIC_BYTES = 64 * 1024


class ProjectNativeInventoryError(ValueError):
    """Fixed refusal without source, path, native output, or authentication context."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectNativeInventoryError("project native inventory deadline expired")


def _controls(
    settings: object,
    limits: object,
    deadline: object,
) -> tuple[Settings, CaptureLimits, float]:
    started = time.monotonic()
    copied: Settings | None = None
    copied_limits: CaptureLimits | None = None
    try:
        if type(settings) is Settings:
            copied = replace(settings)
        if limits is None:
            copied_limits = CaptureLimits(max_capture_seconds=30)
        elif type(limits) is CaptureLimits:
            copied_limits = CaptureLimits(
                limits.max_file_bytes,
                limits.max_total_bytes,
                limits.max_capture_seconds,
            )
    except (AttributeError, TypeError, ValueError):
        pass
    if copied is None or copied_limits is None:
        raise ProjectNativeInventoryError("project native inventory controls are malformed")
    if not isinstance(copied.workspace, Path) or (
        copied.kicad_cli is not None and not isinstance(copied.kicad_cli, Path)
    ):
        raise ProjectNativeInventoryError("project native inventory paths are malformed")
    for value, maximum in (
        (copied.kicad_timeout_seconds, 3600),
        (copied.max_drc_report_bytes, 64 * 1024 * 1024),
        (copied.max_board_bytes, 128 * 1024 * 1024),
        (copied.max_drc_context_bytes, 512 * 1024 * 1024),
        (copied.max_drc_context_files, 100_000),
        (copied.max_drc_context_scan_seconds, 120),
    ):
        if type(value) is not int or not 1 <= value <= maximum:
            raise ProjectNativeInventoryError(
                "project native inventory settings exceed their bounds"
            )
    active = started + copied.kicad_timeout_seconds
    if deadline is not None:
        if type(deadline) not in (int, float):
            raise ProjectNativeInventoryError("project native inventory deadline is malformed")
        try:
            caller = float(cast(int | float, deadline))
        except OverflowError:
            caller = float("nan")
        if not math.isfinite(caller):
            raise ProjectNativeInventoryError("project native inventory deadline is malformed")
        active = min(active, caller)
    _check(active)
    return copied, copied_limits, active


def _execute(
    prepared: PreparedProjectErc,
    capture: SchematicProjectCapture,
    census: ProjectPinCensus,
    settings: Settings,
    deadline: float,
) -> tuple[ProjectComponentInventory, ProjectPinNetMap]:
    expected_sheets = _expected_sheet_paths(capture, deadline)
    with execution.open_project_execution_context(prepared, settings, deadline) as context:
        component_observations: list[ComponentNetlist] = []
        pin_observations: list[NativePinNetMap] = []
        for index in range(2):
            _check(deadline)
            output = context.temporary / f"native-inventory-{index}.xml"
            diagnostics = context.temporary / f"native-inventory-{index}.log"
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
            diagnostic = read_workspace_file(
                context.temporary,
                diagnostics.name,
                allowed_suffixes={".log"},
                max_bytes=_DIAGNOSTIC_BYTES,
            ).content
            if code != 0 or diagnostic:
                raise ProjectNativeInventoryError(
                    "project native inventory execution was not clean"
                )
            payload = read_workspace_file(
                context.temporary,
                output.name,
                allowed_suffixes={".xml"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            expected_source = str(context.snapshot / prepared.root_path)
            component = parse_component_netlist(
                payload,
                expected_source=expected_source,
                expected_sheet_paths=expected_sheets,
                deadline=deadline,
                max_bytes=settings.max_drc_report_bytes,
            )
            pin_map = parse_native_pin_net_map(
                payload,
                census=census,
                expected_source=expected_source,
                expected_sheet_paths=expected_sheets,
                deadline=deadline,
                max_bytes=settings.max_drc_report_bytes,
            )
            if (
                component.backend_version != context.version
                or pin_map.backend_version != context.version
                or pin_map.component_inventory_digest != component._digest(deadline)
            ):
                raise ProjectNativeInventoryError(
                    "project native inventory backend binding is invalid"
                )
            component_observations.append(component)
            pin_observations.append(pin_map)
        if (
            len(component_observations) != 2
            or len(pin_observations) != 2
            or component_observations[0] != component_observations[1]
            or pin_observations[0] != pin_observations[1]
        ):
            raise ProjectNativeInventoryError("project native inventory observations disagree")
        command_fields = {
            "executable_digest": context.executable_digest,
            "flags": _FLAGS,
            "input": prepared.root_path,
            "output": "<private-netlist>",
            "repetitions": 2,
            "native_syntax_digest": context.native_syntax_digest,
        }
        component = component_observations[0]
        mapping = pin_observations[0]
        inventory = ProjectComponentInventory(
            prepared.capture_digest,
            prepared.execution_digest,
            context.native_syntax_digest,
            digest_document("copper-mcp/project-component-inventory-command/v1", command_fields),
            context.executable_digest,
            context.authentication_digest,
            component.backend_version,
            component.components,
            component.sheet_paths,
        )
        pin_report = ProjectPinNetMap(
            prepared.capture_digest,
            prepared.execution_digest,
            context.native_syntax_digest,
            digest_document("copper-mcp/project-pin-net-map-command/v1", command_fields),
            context.executable_digest,
            context.authentication_digest,
            mapping,
        )
        _ = inventory._digest(deadline)
        _ = pin_report._digest(deadline)
        _check(deadline)
        return inventory, pin_report


def run_project_native_inventory(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> tuple[ProjectComponentInventory, ProjectPinNetMap]:
    """Return both semantic views from one fresh authenticated paired export."""

    result: tuple[ProjectComponentInventory, ProjectPinNetMap] | None = None
    try:
        copied, active_limits, active = _controls(settings, limits, deadline)
        if type(capture) is not SchematicProjectCapture or type(libraries) is not tuple:
            raise ProjectNativeInventoryError("project native inventory inputs are malformed")
        census = derive_project_pin_census(
            capture, libraries, limits=active_limits, deadline=active
        )
        prepared = prepare_project_erc(capture, libraries, limits=active_limits, deadline=active)
        if (
            census.capture_digest != prepared.capture_digest
            or census.symbol_context_digest != prepared.execution_digest
        ):
            raise ProjectNativeInventoryError("project native inventory input identities disagree")
        source_files = tuple((item.path, item.content) for item in capture._files)
        execution._verify_workspace_source(copied.workspace, source_files, active)
        completed = _execute(prepared, capture, census, copied, active)
        execution._verify_workspace_source(copied.workspace, source_files, active)
        _check(active)
        result = completed
    except ProjectNativeInventoryError:
        raise
    except (
        AttributeError,
        OSError,
        OverflowError,
        RuntimeError,
        subprocess.SubprocessError,
        TypeError,
        ValueError,
    ):
        pass
    if result is None:
        raise ProjectNativeInventoryError(
            "project native inventory could not produce both semantic observations"
        )
    return result


__all__ = ["ProjectNativeInventoryError", "run_project_native_inventory"]
