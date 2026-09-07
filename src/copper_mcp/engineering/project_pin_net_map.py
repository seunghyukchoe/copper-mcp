"""Fresh read-only native pin/net observations over a complete captured source census."""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import dataclass, replace
from pathlib import Path

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.native_pin_net_map import NativePinNetMap, parse_native_pin_net_map
from copper_mcp.engineering.project_components import _expected_sheet_paths
from copper_mcp.engineering.project_erc_inputs import (
    PreparedProjectErc,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.project_pin_census import ProjectPinCensus, derive_project_pin_census
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_FLAGS = ("sch", "export", "netlist", "--format", "kicadxml")
_DIAGNOSTIC_BYTES = 64 * 1024


class ProjectPinNetMapError(ValueError):
    """A fixed refusal with no retained native diagnostics or private source context."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectPinNetMapError("project pin-net map deadline expired")


@dataclass(frozen=True, slots=True, repr=False)
class ProjectPinNetMap:
    capture_digest: str
    execution_digest: str
    native_syntax_digest: str
    command_digest: str
    executable_digest: str
    backend_authentication_digest: str
    mapping: NativePinNetMap

    def __repr__(self) -> str:
        return "<ProjectPinNetMap redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        _check(deadline)
        mapping_digest = self.mapping._digest(deadline)
        result = digest_document(
            "copper-mcp/project-pin-net-map/v1",
            {
                "capture_digest": self.capture_digest,
                "execution_digest": self.execution_digest,
                "native_syntax_digest": self.native_syntax_digest,
                "command_digest": self.command_digest,
                "executable_digest": self.executable_digest,
                "backend_authentication_digest": self.backend_authentication_digest,
                "mapping_digest": mapping_digest,
            },
        )
        _check(deadline)
        return result

    def document(self) -> dict[str, object]:
        return {
            "capture_digest": self.capture_digest,
            "execution_digest": self.execution_digest,
            "census_digest": self.mapping.census_digest,
            "mapping_digest": self.mapping.digest,
            "report_digest": self.digest,
            "backend_version": self.mapping.backend_version,
            "logical_pin_count": len(self.mapping.nodes),
            "source_pin_occurrence_count": sum(len(node.aliases) for node in self.mapping.nodes),
            "virtual_pin_count": self.mapping.virtual_pin_count,
            "scope": "nonvirtual_component_pin_net_observations",
            "erc_validation": "not_run",
            "model_validation": "not_run",
            "engineering_validation": "not_run",
            "apply_authority": "none",
        }


def _execute(
    prepared: PreparedProjectErc,
    capture: SchematicProjectCapture,
    census: ProjectPinCensus,
    settings: Settings,
    deadline: float,
) -> ProjectPinNetMap:
    expected_sheets = _expected_sheet_paths(capture, deadline)
    with execution.open_project_execution_context(prepared, settings, deadline) as context:
        observations = []
        # Exactly two exports; their total retained payload is bounded by twice the
        # per-report ceiling. There is no retry loop that resets either allowance.
        for index in range(2):
            _check(deadline)
            output = context.temporary / f"pin-nets-{index}.xml"
            diagnostics = context.temporary / f"pin-nets-{index}.log"
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
                raise ProjectPinNetMapError("project pin-net native execution was not clean")
            payload = read_workspace_file(
                context.temporary,
                output.name,
                allowed_suffixes={".xml"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            observation = parse_native_pin_net_map(
                payload,
                census=census,
                expected_source=str(context.snapshot / prepared.root_path),
                expected_sheet_paths=expected_sheets,
                deadline=deadline,
                max_bytes=settings.max_drc_report_bytes,
            )
            if observation.backend_version != context.version:
                raise ProjectPinNetMapError("project pin-net backend binding is invalid")
            observations.append(observation)
        if observations[0] != observations[1]:
            raise ProjectPinNetMapError("project pin-net observations disagree")
        command_digest = digest_document(
            "copper-mcp/project-pin-net-map-command/v1",
            {
                "executable_digest": context.executable_digest,
                "flags": _FLAGS,
                "input": prepared.root_path,
                "output": "<private-netlist>",
                "repetitions": 2,
                "native_syntax_digest": context.native_syntax_digest,
            },
        )
        result = ProjectPinNetMap(
            prepared.capture_digest,
            prepared.execution_digest,
            context.native_syntax_digest,
            command_digest,
            context.executable_digest,
            context.authentication_digest,
            observations[0],
        )
        return result


def run_project_pin_net_map(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectPinNetMap:
    """Derive the census internally and bind two native observations without apply authority."""
    result = None
    try:
        started = time.monotonic()
        if type(settings) is not Settings:
            raise ProjectPinNetMapError("project pin-net settings are malformed")
        copied = replace(settings)
        if not isinstance(copied.workspace, Path) or (
            copied.kicad_cli is not None and not isinstance(copied.kicad_cli, Path)
        ):
            raise ProjectPinNetMapError("project pin-net paths are malformed")
        for value, maximum in (
            (copied.kicad_timeout_seconds, 3600),
            (copied.max_drc_report_bytes, 64 * 1024 * 1024),
            (copied.max_board_bytes, 128 * 1024 * 1024),
            (copied.max_drc_context_bytes, 512 * 1024 * 1024),
            (copied.max_drc_context_files, 100_000),
            (copied.max_drc_context_scan_seconds, 120),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ProjectPinNetMapError("project pin-net settings exceed their bounds")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ProjectPinNetMapError("project pin-net deadline is malformed")
        active_deadline = min(
            started + copied.kicad_timeout_seconds, deadline if deadline is not None else math.inf
        )
        if limits is not None and type(limits) is not CaptureLimits:
            raise ProjectPinNetMapError("project pin-net limits are malformed")
        active_limits = (
            CaptureLimits(max_capture_seconds=30)
            if limits is None
            else CaptureLimits(
                limits.max_file_bytes, limits.max_total_bytes, limits.max_capture_seconds
            )
        )
        _check(active_deadline)
        census = derive_project_pin_census(
            capture, libraries, limits=active_limits, deadline=active_deadline
        )
        prepared = prepare_project_erc(
            capture, libraries, limits=active_limits, deadline=active_deadline
        )
        if (
            census.capture_digest != prepared.capture_digest
            or census.symbol_context_digest != prepared.execution_digest
        ):
            raise ProjectPinNetMapError("project pin-net input identities disagree")
        source_files = tuple((item.path, item.content) for item in capture._files)
        execution._verify_workspace_source(copied.workspace, source_files, active_deadline)
        completed = _execute(prepared, capture, census, copied, active_deadline)
        _ = completed._digest(active_deadline)
        execution._verify_workspace_source(copied.workspace, source_files, active_deadline)
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
        raise ProjectPinNetMapError("project pin-net map could not produce bound native evidence")
    return result
