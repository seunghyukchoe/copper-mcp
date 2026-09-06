"""Bounded real KiCad connectivity ERC over a captured hierarchical project.

Source: https://docs.kicad.org/10.0/en/cli/cli.html#schematic-erc
No Circuit Intent identities, simulation/fabrication pass, save, or apply authority is created.
"""

from __future__ import annotations

import math
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.erc_profile import (
    NATIVE_RULE_SEVERITIES,
    OUTSIDE_CONNECTIVITY_SCOPE,
    PROFILE_ID,
)
from copper_mcp.engineering.kicad_project_execution import (
    _FONTCONFIG_DIGEST,
    ProjectErcError,
    open_project_execution_context,
)
from copper_mcp.engineering.project_erc_inputs import (
    PreparedProjectErc,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.kicad_cli import KiCadCliError, _parse_erc_observation
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_ERC_FLAGS = (
    "sch",
    "erc",
    "--format",
    "json",
    "--units",
    "mm",
    "--severity-all",
    "--exit-code-violations",
)
_KNOWN_FINDINGS = frozenset(NATIVE_RULE_SEVERITIES) | {
    "duplicate_pins",
    "generic-warning",
    "generic-error",
}


@dataclass(frozen=True, slots=True)
class ProjectErcSample:
    normalized_report_digest: str
    error_count: int
    warning_count: int
    exclusion_count: int
    ignored_check_keys: tuple[str, ...]
    sheet_count: int
    violation_type_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ProjectConnectivityErcReport:
    capture_digest: str
    execution_digest: str
    profile_digest: str
    executable_digest: str
    command_digest: str
    backend_authentication_digest: str
    native_syntax_digest: str
    native_syntax_file_count: int
    backend_version: str
    samples: tuple[ProjectErcSample, ...]
    rule_changes: tuple[str, ...]
    original_exclusion_count: int
    profile_id: str = PROFILE_ID

    @property
    def status(self) -> Literal["pass", "fail", "inconclusive"]:
        if any(sample.error_count for sample in self.samples):
            return "fail"
        if len(self.samples) != 2 or self.samples[0] != self.samples[1]:
            return "inconclusive"
        sample = self.samples[0]
        if sample.exclusion_count or sample.ignored_check_keys != tuple(
            sorted(OUTSIDE_CONNECTIVITY_SCOPE)
        ):
            return "inconclusive"
        if {"lib_symbol_issues", "lib_symbol_mismatch"} & dict(sample.violation_type_counts).keys():
            return "inconclusive"
        return "pass"

    @property
    def digest(self) -> str:
        return digest_document("copper-mcp/project-connectivity-erc/v1", asdict(self))

    def document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "status": self.status,
            "report_digest": self.digest,
            "outside_scope_checks": sorted(OUTSIDE_CONNECTIVITY_SCOPE),
            "simulation_validation": "not_run",
            "fabrication_validation": "not_run",
            "board_parity": "not_run",
            "symbol_library_parity": "exact-ordered-flat-body/v1",
            "backend_authentication": "apple-sealed-kicad/v1",
            "typography_validation": "not_run",
            "apply_authority": "none",
        }


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectErcError("project ERC deadline expired")


def _execute(
    prepared: PreparedProjectErc, settings: Settings, deadline: float
) -> ProjectConnectivityErcReport:
    with open_project_execution_context(prepared, settings, deadline) as execution:
        samples = []
        for index in range(2):
            _check(deadline)
            output = execution.temporary / f"erc-{index}.json"
            code = kicad_project_execution._invoke(
                [
                    *execution.base_command,
                    str(settings.max_drc_report_bytes),
                    str(execution.executable),
                    *_ERC_FLAGS,
                    "--output",
                    str(output),
                    str(execution.snapshot / prepared.root_path),
                ],
                settings=settings,
                environment=execution.environment,
                deadline=deadline,
            )
            execution.verify()
            payload = read_workspace_file(
                execution.temporary,
                output.name,
                allowed_suffixes={".json"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            # Discard only the unpredictable private directory spelling, never design content.
            payload = payload.replace(str(execution.temporary).encode(), b"<private-erc>")
            observation = _parse_erc_observation(
                payload,
                deadline=deadline,
                return_code=code,
                expected_source=PurePosixPath(prepared.root_path).name,
                expected_uuid_paths=prepared.expected_uuid_paths,
                minimum_severities={
                    **dict(prepared.effective_rule_severities),
                    "duplicate_pins": "error",
                    "generic-error": "error",
                    "generic-warning": "warning",
                },
            )
            if observation.kicad_version != execution.version:
                raise ProjectErcError("project ERC report backend binding is invalid")
            if set(observation.violation_type_counts) - _KNOWN_FINDINGS:
                raise ProjectErcError("project ERC report has unknown finding categories")
            samples.append(
                ProjectErcSample(
                    observation.normalized_report_digest,
                    observation.error_count,
                    observation.warning_count,
                    observation.exclusion_count,
                    observation.ignored_check_keys,
                    observation.sheet_count,
                    tuple(sorted(observation.violation_type_counts.items())),
                )
            )
        _check(deadline)
        command_digest = digest_document(
            "copper-mcp/project-erc-command/v1",
            {
                "executable_digest": execution.executable_digest,
                "flags": _ERC_FLAGS,
                "input": prepared.root_path,
                "output": "<private-report>",
                "repetitions": 2,
                "native_syntax_digest": execution.native_syntax_digest,
                "fontconfig_digest": _FONTCONFIG_DIGEST,
            },
        )
        return ProjectConnectivityErcReport(
            prepared.capture_digest,
            prepared.execution_digest,
            prepared.profile_digest,
            execution.executable_digest,
            command_digest,
            execution.authentication_digest,
            execution.native_syntax_digest,
            len(execution.source_names),
            execution.version,
            tuple(samples),
            prepared.rule_changes,
            prepared.original_exclusion_count,
        )


def run_project_erc(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectConnectivityErcReport:
    """Run two captured-input checks with workspace freshness observed at both boundaries.

    These observations are not an atomic workspace snapshot or live-editor mutation authority.
    """
    result = None
    try:
        started = time.monotonic()
        if type(settings) is not Settings:
            raise ProjectErcError("project ERC settings are malformed")
        settings = replace(settings)
        if not isinstance(settings.workspace, Path):
            raise ProjectErcError("project ERC workspace is malformed")
        if settings.kicad_cli is not None and not isinstance(settings.kicad_cli, Path):
            raise ProjectErcError("project ERC configured executable path is malformed")
        for value, maximum in (
            (settings.kicad_timeout_seconds, 3600),
            (settings.max_drc_report_bytes, 64 * 1024 * 1024),
            (settings.max_board_bytes, 128 * 1024 * 1024),
            (settings.max_drc_context_bytes, 512 * 1024 * 1024),
            (settings.max_drc_context_files, 100_000),
            (settings.max_drc_context_scan_seconds, 120),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ProjectErcError("project ERC settings exceed their profile bounds")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ProjectErcError("project ERC deadline is malformed")
        active_deadline = min(
            started + settings.kicad_timeout_seconds, deadline if deadline is not None else math.inf
        )
        active_limits = CaptureLimits(max_capture_seconds=30) if limits is None else limits
        prepared = prepare_project_erc(
            capture, libraries, limits=active_limits, deadline=active_deadline
        )
        source_files = tuple((item.path, item.content) for item in capture._files)
        kicad_project_execution._verify_workspace_source(
            settings.workspace, source_files, active_deadline
        )
        completed = _execute(prepared, settings, active_deadline)
        kicad_project_execution._verify_workspace_source(
            settings.workspace, source_files, active_deadline
        )
        result = completed
    except (
        ValueError,
        KiCadCliError,
        OSError,
        OverflowError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        pass
    if result is None:
        # Raised after the handler so private native/report exceptions are not retained.
        raise ProjectErcError("project ERC could not produce bound connectivity evidence")
    return result


__all__ = [
    "ProjectConnectivityErcReport",
    "ProjectErcError",
    "ProjectErcSample",
    "run_project_erc",
]
