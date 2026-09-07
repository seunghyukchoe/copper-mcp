"""Private native parity over captured ordinary projects and immutable candidate bytes."""

from __future__ import annotations

import hashlib
import json
import math
import re
import subprocess
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import Literal

from copper_mcp.config import Settings
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_erc_inputs import (
    LIBRARY_ENVIRONMENT_KEY,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.project_settings import parse_project_document
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.kicad_cli import (
    PARITY_CONNECTIVITY_TYPES,
    PARITY_PROJECTION_TYPES,
    KiCadCliError,
    _make_snapshot_read_only,
    _parse_parity_observation,
    _write_drc_snapshot,
)
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_CHECKS = PARITY_CONNECTIVITY_TYPES | PARITY_PROJECTION_TYPES
_FLAGS = ("pcb", "drc", "--schematic-parity", "--format", "json", "--units", "mm", "--severity-all")
_DIAGNOSTIC_BYTES = 64 * 1024
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")


class ProjectBoardParityError(ValueError):
    """A fixed refusal, not private native diagnostics or an engineering verdict."""


@dataclass(frozen=True, slots=True)
class ProjectParitySample:
    normalized_report_digest: str
    parity_type_counts: tuple[tuple[str, int], ...]
    drc_finding_count: int
    unconnected_finding_count: int


@dataclass(frozen=True, slots=True)
class ProjectBoardParityReport:
    capture_digest: str
    source_execution_digest: str
    parity_execution_digest: str
    board_revision: str
    backend_version: str
    executable_digest: str
    backend_authentication_digest: str
    native_syntax_digest: str
    command_digest: str
    samples: tuple[ProjectParitySample, ...]
    profile_id: str = "kicad-project-board-parity/v1"

    @property
    def status(self) -> Literal["pass", "fail", "inconclusive"]:
        if any(sum(count for _, count in sample.parity_type_counts) for sample in self.samples):
            return "fail"
        if len(self.samples) != 2 or self.samples[0] != self.samples[1]:
            return "inconclusive"
        return "pass"

    @property
    def digest(self) -> str:
        return digest_document("copper-mcp/project-board-parity/v1", asdict(self))

    def document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "status": self.status,
            "report_digest": self.digest,
            "drc_validation": "inconclusive",
            "simulation_validation": "not_run",
            "fabrication_validation": "not_run",
            "apply_authority": "none",
        }


def _sha(content: bytes, deadline: float) -> str:
    _check(deadline)
    digest = hashlib.sha256()
    view = memoryview(content)
    for offset in range(0, len(view), 64 * 1024):
        _check(deadline)
        digest.update(view[offset : offset + 64 * 1024])
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectBoardParityError("project parity deadline expired")


def _parity_project(content: bytes, deadline: float) -> bytes:
    project = parse_project_document(content, deadline=deadline)
    board = project.get("board", {})
    if not isinstance(board, dict):
        raise ProjectBoardParityError("project parity board settings are malformed")
    design = board.get("design_settings", {})
    if not isinstance(design, dict):
        raise ProjectBoardParityError("project parity design settings are malformed")
    severities = design.get("rule_severities", {})
    if not isinstance(severities, dict):
        raise ProjectBoardParityError("project parity severities are malformed")
    rules = {}
    for name in sorted(_CHECKS):
        severity = severities.get(name, "warning")
        if type(severity) is not str or severity not in {"ignore", "warning", "error"}:
            raise ProjectBoardParityError("project parity severity is malformed")
        rules[name] = "error" if severity == "error" else "warning"
    # This is an explicitly scoped derivative, not a general DRC or fabrication rule set.
    project["board"] = {"design_settings": {"rule_severities": rules, "drc_exclusions": []}}
    _check(deadline)
    output = json.dumps(project, sort_keys=True, ensure_ascii=True, allow_nan=False).encode()
    _check(deadline)
    return output


def _validate_parity_diagnostics(
    payload: bytes, output: Path, counts: tuple[int, int, int]
) -> None:
    if len(payload) > _DIAGNOSTIC_BYTES:
        raise ProjectBoardParityError("project parity diagnostics exceed their bound")
    number = rb"(0|[1-9][0-9]{0,5})"
    pattern = (
        rb"Found "
        + number
        + rb" violations\r?\nFound "
        + number
        + rb" unconnected items\r?\nFound "
        + number
        + rb" schematic parity issues\r?\n"
        + rb"Saved DRC Report to "
        + re.escape(str(output).encode())
        + rb"\r?\n"
    )
    match = re.fullmatch(pattern, payload)
    if match is None or tuple(int(value) for value in match.groups()) != counts:
        raise ProjectBoardParityError("project parity execution was not proven live")


def run_project_board_parity(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    candidate_board: bytes,
    expected_board_revision: str,
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectBoardParityReport:
    """Observe source freshness and native parity; never read or mutate a workspace board."""
    result = None
    try:
        started = time.monotonic()
        if type(settings) is not Settings:
            raise ProjectBoardParityError("project parity settings are malformed")
        settings = replace(settings)
        if not isinstance(settings.workspace, Path) or (
            settings.kicad_cli is not None and not isinstance(settings.kicad_cli, Path)
        ):
            raise ProjectBoardParityError("project parity paths are malformed")
        for value, maximum in (
            (settings.kicad_timeout_seconds, 3600),
            (settings.max_drc_report_bytes, 64 * 1024 * 1024),
            (settings.max_board_bytes, 128 * 1024 * 1024),
            (settings.max_drc_context_bytes, 512 * 1024 * 1024),
            (settings.max_drc_context_files, 100_000),
            (settings.max_drc_context_scan_seconds, 120),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ProjectBoardParityError("project parity settings exceed their bounds")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ProjectBoardParityError("project parity deadline is malformed")
        active_deadline = min(
            started + settings.kicad_timeout_seconds, deadline if deadline is not None else math.inf
        )
        _check(active_deadline)
        if (
            type(candidate_board) is not bytes
            or not candidate_board
            or len(candidate_board) > settings.max_board_bytes
            or type(expected_board_revision) is not str
            or not _DIGEST.fullmatch(expected_board_revision)
            or _sha(candidate_board, active_deadline) != expected_board_revision
        ):
            raise ProjectBoardParityError("project parity candidate identity is malformed")
        if limits is not None and type(limits) is not CaptureLimits:
            raise ProjectBoardParityError("project parity limits are malformed")
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
        sources = tuple((item.path, item.content) for item in capture._files)
        execution._verify_workspace_source(settings.workspace, sources, active_deadline)
        files = dict(prepared.files)
        board_name = str(PurePosixPath(prepared.root_path).with_suffix(".kicad_pcb"))
        project_name = str(PurePosixPath(prepared.root_path).with_suffix(".kicad_pro"))
        files[project_name] = _parity_project(files[project_name], active_deadline)
        if (
            len(files[project_name]) > active_limits.max_file_bytes
            or sum(map(len, files.values())) > active_limits.max_total_bytes
        ):
            raise ProjectBoardParityError("project parity derivative exceeds source byte limits")
        files[board_name] = candidate_board
        if (
            len(files) + len(prepared.files) > settings.max_drc_context_files
            or sum(map(len, files.values())) + sum(len(data) for _, data in prepared.files)
            > settings.max_drc_context_bytes
            or any(len(data) > settings.max_board_bytes for data in files.values())
        ):
            raise ProjectBoardParityError("project parity cumulative context exceeds its budget")
        parity_digest = digest_document(
            "copper-mcp/parity-execution-context/v1",
            {
                "root_path": prepared.root_path,
                "files": [
                    {"path": name, "digest": _sha(data, active_deadline)}
                    for name, data in sorted(files.items())
                ],
                "policy": "fixed-parity-board-settings/v1",
            },
        )
        _check(active_deadline)
        with execution.open_project_execution_context(
            prepared, settings, active_deadline
        ) as context:
            snapshot = context.temporary / "parity-input"
            snapshot.mkdir(mode=0o700)
            _write_drc_snapshot(files, snapshot)
            _make_snapshot_read_only(snapshot)
            environment = dict(context.environment)
            environment[LIBRARY_ENVIRONMENT_KEY] = str(snapshot / prepared.library_directory)
            samples = []
            for index in range(2):
                execution._verify_files(snapshot, files, settings, active_deadline)
                output = context.temporary / f"parity-{index}.json"
                diagnostics = context.temporary / f"parity-{index}.log"
                with diagnostics.open("wb") as stream:
                    code = execution._invoke(
                        [
                            *context.base_command,
                            str(settings.max_drc_report_bytes),
                            str(context.executable),
                            *_FLAGS,
                            "--output",
                            str(output),
                            str(snapshot / board_name),
                        ],
                        settings=settings,
                        environment=environment,
                        deadline=active_deadline,
                        stdout=stream,
                        stderr=subprocess.STDOUT,
                    )
                execution._verify_files(snapshot, files, settings, active_deadline)
                context.verify()
                report_bytes = read_workspace_file(
                    context.temporary,
                    output.name,
                    allowed_suffixes={".json"},
                    max_bytes=settings.max_drc_report_bytes,
                ).content
                diagnostic_bytes = read_workspace_file(
                    context.temporary,
                    diagnostics.name,
                    allowed_suffixes={".log"},
                    max_bytes=_DIAGNOSTIC_BYTES,
                ).content
                report_bytes = report_bytes.replace(
                    str(context.temporary).encode(), b"<private-parity>"
                )
                observation = _parse_parity_observation(
                    report_bytes,
                    return_code=code,
                    expected_source=PurePosixPath(board_name).name,
                    required_enabled_checks=_CHECKS,
                    deadline=active_deadline,
                )
                if (
                    observation.kicad_version != context.version
                    or observation.normalized_report_digest is None
                ):
                    raise ProjectBoardParityError("project parity backend observation is invalid")
                _validate_parity_diagnostics(
                    diagnostic_bytes,
                    output,
                    (
                        observation.drc_finding_count,
                        observation.unconnected_finding_count,
                        sum(observation.parity_type_counts.values()),
                    ),
                )
                _check(active_deadline)
                samples.append(
                    ProjectParitySample(
                        observation.normalized_report_digest,
                        tuple(sorted(observation.parity_type_counts.items())),
                        observation.drc_finding_count,
                        observation.unconnected_finding_count,
                    )
                )
            command_digest = digest_document(
                "copper-mcp/project-parity-command/v1",
                {
                    "flags": _FLAGS,
                    "board": board_name,
                    "repetitions": 2,
                    "diagnostic_policy": "closed-c-locale/v1",
                },
            )
            completed = ProjectBoardParityReport(
                capture.digest,
                prepared.execution_digest,
                parity_digest,
                expected_board_revision,
                context.version,
                context.executable_digest,
                context.authentication_digest,
                context.native_syntax_digest,
                command_digest,
                tuple(samples),
            )
        execution._verify_workspace_source(settings.workspace, sources, active_deadline)
        _check(active_deadline)
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
        raise ProjectBoardParityError("project parity could not produce bound evidence")
    return result
