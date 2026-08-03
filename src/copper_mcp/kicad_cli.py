"""Bounded, read-only integration with the authoritative KiCad CLI DRC."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    render_kicad_candidate_board,
)
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.routing import RouteCandidate
from copper_mcp.security import (
    WorkspaceViolationError,
    read_bounded_file,
    resolve_workspace_file,
)

KICAD_DRC_SCHEMA = "https://schemas.kicad.org/drc.v1.json"
_MACOS_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
_ACCEPTED_DRC_RETURN_CODES = frozenset({0, 5})
_SEVERITIES = frozenset({"error", "warning"})
_INCLUDED_SEVERITIES = frozenset({"error", "warning", "exclusion"})
_DRC_CONTEXT_SUFFIXES = (".kicad_pro", ".kicad_dru")
_LOCAL_LIBRARY_SUFFIXES = frozenset({".kicad_dru", ".kicad_mod", ".kicad_sym"})
_LOCAL_LIBRARY_TABLES = frozenset({"fp-lib-table", "sym-lib-table", "design-block-lib-table"})
_KICAD_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?$")
_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_KICAD_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_BOUNDED_EXEC = Path(__file__).with_name("_bounded_exec.py")


class KiCadCliError(RuntimeError):
    """Raised when the trusted KiCad DRC adapter cannot produce valid evidence."""


@dataclass(frozen=True, slots=True)
class RouteCandidateDrcEvidence:
    """Immutable bindings between one replayed route candidate and KiCad DRC evidence."""

    candidate_id: str
    candidate_base_revision: str
    source_revision: str
    patched_board_revision: str
    patched_drc_context_revision: str
    summary: DrcSummary

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "candidate_base_revision",
            "source_revision",
            "patched_board_revision",
            "patched_drc_context_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_ID.fullmatch(value):
                raise ValueError(f"{name} must be content-addressed with sha256")
        if not isinstance(self.summary, DrcSummary):
            raise ValueError("summary must be strict KiCad DRC evidence")
        if self.summary.base_revision != self.patched_board_revision:
            raise ValueError("DRC summary is not bound to the patched board revision")
        if self.summary.drc_context_revision != self.patched_drc_context_revision:
            raise ValueError("DRC summary is not bound to the patched context revision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "candidate_base_revision": self.candidate_base_revision,
            "source_revision": self.source_revision,
            "patched_board_revision": self.patched_board_revision,
            "patched_drc_context_revision": self.patched_drc_context_revision,
            "summary": self.summary.to_dict(),
        }


def _validated_executable(candidate: Path) -> Path | None:
    try:
        resolved = candidate.expanduser().resolve(strict=True)
    except (FileNotFoundError, OSError, RuntimeError):
        return None
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        return None
    return resolved


def discover_kicad_cli(settings: Settings) -> Path:
    """Find an executable KiCad CLI without accepting caller-controlled arguments."""

    if settings.kicad_cli is not None:
        configured = _validated_executable(settings.kicad_cli)
        if configured is None:
            raise KiCadCliError("configured KiCad CLI is missing or not executable")
        return configured

    path_candidate = shutil.which("kicad-cli")
    candidates = (Path(path_candidate) if path_candidate else None, _MACOS_KICAD_CLI)
    for candidate in filter(None, candidates):
        executable = _validated_executable(candidate)
        if executable is not None:
            return executable
    raise KiCadCliError("KiCad CLI was not found; set COPPER_MCP_KICAD_CLI")


def _revision(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def _resolve_context_file(path: Path, settings: Settings) -> tuple[str, Path]:
    root = settings.workspace.resolve(strict=True)
    try:
        resolved = path.resolve(strict=True)
        relative = resolved.relative_to(root).as_posix()
    except (FileNotFoundError, OSError, RuntimeError, ValueError) as error:
        raise KiCadCliError("KiCad DRC context must stay inside the workspace") from error
    if path.is_symlink() or not resolved.is_file():
        raise KiCadCliError("KiCad DRC context must contain regular files, not symlinks")
    return relative, resolved


def _drc_context(board_path: Path, settings: Settings) -> dict[str, bytes]:
    context: dict[str, bytes] = {}
    total_bytes = 0
    deadline = time.monotonic() + settings.max_drc_context_scan_seconds

    def check_discovery_budget() -> None:
        if time.monotonic() > deadline:
            raise KiCadCliError("KiCad DRC context discovery timed out")

    def add(candidate: Path) -> None:
        nonlocal total_bytes
        check_discovery_budget()
        relative, resolved = _resolve_context_file(candidate, settings)
        if relative in context:
            return
        if len(context) >= settings.max_drc_context_files:
            raise KiCadCliError("KiCad DRC context exceeds the configured file-count limit")
        remaining_bytes = settings.max_drc_context_bytes - total_bytes
        if remaining_bytes <= 0:
            raise KiCadCliError("KiCad DRC context exceeds the configured cumulative limit")
        try:
            payload = read_bounded_file(
                resolved,
                max_bytes=min(settings.max_board_bytes, remaining_bytes),
            )
        except WorkspaceViolationError as error:
            raise KiCadCliError(
                "KiCad DRC context exceeds the configured cumulative limit"
            ) from error
        check_discovery_budget()
        total_bytes += len(payload)
        if total_bytes > settings.max_drc_context_bytes:
            raise KiCadCliError("KiCad DRC context exceeds the configured cumulative limit")
        context[relative] = payload

    add(board_path)
    for suffix in _DRC_CONTEXT_SUFFIXES:
        candidate = board_path.with_suffix(suffix)
        if not candidate.exists():
            continue
        add(candidate)

    for candidate in settings.workspace.rglob("*"):
        check_discovery_budget()
        if not candidate.is_file():
            continue
        if candidate.name not in _LOCAL_LIBRARY_TABLES and candidate.suffix not in (
            _LOCAL_LIBRARY_SUFFIXES
        ):
            continue
        add(candidate)
    return context


def _write_drc_snapshot(context: dict[str, bytes], destination_root: Path) -> None:
    for name, payload in context.items():
        destination = destination_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _context_revision(context: dict[str, bytes]) -> str:
    digest = hashlib.sha256()
    for name, payload in sorted(context.items()):
        digest.update(name.encode("utf-8"))
        digest.update(b"\0")
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return f"sha256:{digest.hexdigest()}"


def _candidate_drc_context(
    context: dict[str, bytes],
    *,
    board_relative: str,
    patched_board: bytes,
    settings: Settings,
) -> dict[str, bytes]:
    """Replace only the captured board payload and recheck every context budget."""

    if board_relative not in context:
        raise KiCadCliError("captured KiCad DRC context is missing its source board")
    if not isinstance(patched_board, bytes):
        raise KiCadCliError("patched candidate board must be immutable bytes")
    if len(patched_board) > settings.max_board_bytes:
        raise KiCadCliError("patched candidate board exceeds the configured board limit")
    if len(context) > settings.max_drc_context_files:
        raise KiCadCliError("patched KiCad DRC context exceeds the file-count limit")

    patched_context = dict(context)
    patched_context[board_relative] = patched_board
    total_bytes = 0
    for payload in patched_context.values():
        if not isinstance(payload, bytes) or len(payload) > settings.max_board_bytes:
            raise KiCadCliError("patched KiCad DRC context exceeds the per-file limit")
        total_bytes += len(payload)
        if total_bytes > settings.max_drc_context_bytes:
            raise KiCadCliError("patched KiCad DRC context exceeds the cumulative limit")
    return patched_context


def _parse_drc_report(
    payload: bytes,
    *,
    return_code: int,
    base_revision: str,
    drc_context_revision: str,
    expected_source: str,
) -> DrcSummary:
    try:
        report: Any = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KiCadCliError("KiCad DRC report is not valid UTF-8 JSON") from error
    if not isinstance(report, dict):
        raise KiCadCliError("KiCad DRC report must be a JSON object")
    if report.get("$schema") != KICAD_DRC_SCHEMA:
        raise KiCadCliError("KiCad DRC report schema is unsupported")
    if report.get("coordinate_units") != "mm":
        raise KiCadCliError("KiCad DRC report must use millimetres")
    if report.get("source") != expected_source:
        raise KiCadCliError("KiCad DRC report source does not match the board snapshot")
    report_date = report.get("date")
    if not isinstance(report_date, str):
        raise KiCadCliError("KiCad DRC report has no valid generation date")
    try:
        datetime.fromisoformat(report_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise KiCadCliError("KiCad DRC report has no valid generation date") from error
    if not _KICAD_DATE_TIME.fullmatch(report_date):
        raise KiCadCliError("KiCad DRC report has no valid generation date")
    kicad_version = report.get("kicad_version")
    violations = report.get("violations")
    unconnected_items = report.get("unconnected_items")
    schematic_parity = report.get("schematic_parity")
    included_severities = report.get("included_severities")
    ignored_checks = report.get("ignored_checks")
    if not isinstance(kicad_version, str) or not _KICAD_VERSION.fullmatch(kicad_version):
        raise KiCadCliError("KiCad DRC report has no valid version")
    if (
        not isinstance(violations, list)
        or not isinstance(unconnected_items, list)
        or not isinstance(schematic_parity, list)
    ):
        raise KiCadCliError("KiCad DRC report collections are malformed")
    if schematic_parity:
        raise KiCadCliError("unexpected schematic-parity findings in board-only DRC report")
    if (
        not isinstance(included_severities, list)
        or len(included_severities) != len(_INCLUDED_SEVERITIES)
        or not all(isinstance(item, str) for item in included_severities)
        or frozenset(included_severities) != _INCLUDED_SEVERITIES
    ):
        raise KiCadCliError("KiCad DRC report did not include all requested severities")
    if not isinstance(ignored_checks, list) or len(ignored_checks) > 10_000:
        raise KiCadCliError("KiCad DRC ignored-check collection is malformed")
    for ignored_check in ignored_checks:
        if not isinstance(ignored_check, dict):
            raise KiCadCliError("KiCad DRC ignored check is malformed")
        check_key = ignored_check.get("key")
        description = ignored_check.get("description")
        if (
            not isinstance(check_key, str)
            or not 1 <= len(check_key) <= 128
            or not isinstance(description, str)
        ):
            raise KiCadCliError("KiCad DRC ignored check fields are malformed")
    if len(violations) + len(unconnected_items) > 100_000:
        raise KiCadCliError("KiCad DRC report contains too many findings")
    expected_return_code = 5 if violations or unconnected_items else 0
    if return_code != expected_return_code:
        raise KiCadCliError("KiCad DRC exit code does not match the report findings")

    severity_counts: Counter[str] = Counter()
    violation_type_counts: Counter[str] = Counter()
    exclusion_count = 0
    for collection_name, findings in (
        ("violation", violations),
        ("unconnected item", unconnected_items),
    ):
        for finding in findings:
            if not isinstance(finding, dict):
                raise KiCadCliError(f"KiCad DRC {collection_name} is malformed")
            violation_type = finding.get("type")
            description = finding.get("description")
            severity = finding.get("severity")
            items = finding.get("items")
            excluded = finding.get("excluded", False)
            if not isinstance(violation_type, str) or not 1 <= len(violation_type) <= 128:
                raise KiCadCliError(f"KiCad DRC {collection_name} type is malformed")
            if (
                not isinstance(description, str)
                or not isinstance(items, list)
                or not all(isinstance(item, dict) for item in items)
            ):
                raise KiCadCliError(f"KiCad DRC {collection_name} fields are malformed")
            if severity not in _SEVERITIES:
                raise KiCadCliError(f"KiCad DRC {collection_name} severity is unsupported")
            if not isinstance(excluded, bool):
                raise KiCadCliError(f"KiCad DRC {collection_name} exclusion is malformed")
            if excluded:
                exclusion_count += 1
            elif collection_name == "violation":
                severity_counts[severity] += 1
            violation_type_counts[violation_type] += 1

    active_unconnected = sum(
        1 for finding in unconnected_items if not finding.get("excluded", False)
    )

    error_count = severity_counts["error"]
    return DrcSummary(
        base_revision=base_revision,
        drc_context_revision=drc_context_revision,
        kicad_version=kicad_version,
        drc_schema=KICAD_DRC_SCHEMA,
        coordinate_units="mm",
        error_count=error_count,
        warning_count=severity_counts["warning"],
        exclusion_count=exclusion_count,
        ignored_check_count=len(ignored_checks),
        unconnected_count=active_unconnected,
        violation_type_counts=dict(sorted(violation_type_counts.items())),
        passed=error_count == 0 and active_unconnected == 0,
    )


def _run_captured_drc(
    context: dict[str, bytes],
    *,
    board_relative: str,
    settings: Settings,
) -> DrcSummary:
    """Consume one bounded context through the sole fixed KiCad DRC command path."""

    if board_relative not in context:
        raise KiCadCliError("captured KiCad DRC context is missing its source board")
    base_revision = _revision(context[board_relative])
    drc_context_revision = _context_revision(context)
    executable = discover_kicad_cli(settings)
    if os.name != "posix":
        raise KiCadCliError("bounded KiCad DRC execution is unsupported on this platform")
    python_executable = _validated_executable(Path(sys.executable))
    bounded_exec = _BOUNDED_EXEC.resolve(strict=True)
    if python_executable is None or not bounded_exec.is_file():
        raise KiCadCliError("bounded KiCad DRC execution helper is unavailable")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-drc-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        try:
            temporary_root.chmod(0o700)
        except OSError as error:
            raise KiCadCliError("private KiCad DRC directory could not be secured") from error
        workspace_snapshot = temporary_root / "workspace"
        try:
            _write_drc_snapshot(context, workspace_snapshot)
        except OSError as error:
            raise KiCadCliError("private KiCad DRC context could not be written") from error
        context.clear()
        snapshot_board = workspace_snapshot / board_relative
        report_path = temporary_root / "drc.json"
        kicad_command = [
            str(executable),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report_path),
            str(snapshot_board),
        ]
        command = [
            str(python_executable),
            "-I",
            str(bounded_exec),
            str(settings.max_drc_report_bytes),
            *kicad_command,
        ]
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=settings.kicad_timeout_seconds,
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError("KiCad DRC timed out") from error
        if completed.returncode == -signal.SIGXFSZ:
            raise KiCadCliError("KiCad DRC report exceeds the configured limit")
        if completed.returncode not in _ACCEPTED_DRC_RETURN_CODES:
            raise KiCadCliError(f"KiCad DRC failed with exit code {completed.returncode}")
        try:
            private_context_after = _drc_context(
                snapshot_board,
                replace(settings, workspace=workspace_snapshot),
            )
        except (KiCadCliError, OSError) as error:
            raise KiCadCliError("private KiCad DRC context changed during DRC") from error
        if _context_revision(private_context_after) != drc_context_revision:
            raise KiCadCliError("private KiCad DRC context changed during DRC")
        del private_context_after
        try:
            report = read_bounded_file(
                report_path,
                max_bytes=settings.max_drc_report_bytes,
            )
        except FileNotFoundError as error:
            raise KiCadCliError("KiCad DRC did not create a report") from error
        except WorkspaceViolationError as error:
            raise KiCadCliError("KiCad DRC report exceeds the configured limit") from error

    return _parse_drc_report(
        report,
        return_code=completed.returncode,
        base_revision=base_revision,
        drc_context_revision=drc_context_revision,
        expected_source=Path(board_relative).name,
    )


def run_board_drc(requested_path: str, settings: Settings) -> DrcSummary:
    """Run fixed-argument KiCad DRC and reject stale or malformed evidence."""

    board_path = resolve_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    before_context = _drc_context(board_path, settings)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    drc_context_revision = _context_revision(before_context)
    summary = _run_captured_drc(
        before_context,
        board_relative=board_relative,
        settings=settings,
    )
    if _context_revision(_drc_context(board_path, settings)) != drc_context_revision:
        raise KiCadCliError(
            "board or DRC rules changed while KiCad DRC was running; result discarded"
        )
    return summary


def run_route_candidate_drc(
    requested_path: str,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
) -> RouteCandidateDrcEvidence:
    """Bind an exact replayed candidate to authoritative DRC without exposing board bytes."""

    if not isinstance(candidate, RouteCandidate):
        raise KiCadCliError("route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadCliError("KiCad constraint profile is malformed")

    board_path = resolve_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    captured_context = _drc_context(board_path, settings)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    original_context_revision = _context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = _revision(source)

    default_limits = ParseLimits()
    parse_limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    conversion = parse_kicad_bytes(source, profile, parse_limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadCliError("captured KiCad board cannot be represented by the supported Board IR")
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise KiCadCliError("captured KiCad source revision is inconsistent")
    if candidate.base_revision != snapshot.snapshot_digest:
        raise KiCadCliError("route candidate is stale for the captured Board IR snapshot")
    try:
        patched_board = render_kicad_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            limits=parse_limits,
        )
    except KiCadRoutePatchError as error:
        raise KiCadCliError("route candidate failed replay-verified KiCad serialization") from error

    patched_context = _candidate_drc_context(
        captured_context,
        board_relative=board_relative,
        patched_board=patched_board,
        settings=settings,
    )
    patched_board_revision = _revision(patched_board)
    patched_drc_context_revision = _context_revision(patched_context)
    del captured_context, conversion, patched_board, snapshot, source

    summary = _run_captured_drc(
        patched_context,
        board_relative=board_relative,
        settings=settings,
    )
    if (
        summary.base_revision != patched_board_revision
        or summary.drc_context_revision != patched_drc_context_revision
    ):
        raise KiCadCliError("KiCad DRC summary revision binding is inconsistent")
    if _context_revision(_drc_context(board_path, settings)) != original_context_revision:
        raise KiCadCliError(
            "board or DRC rules changed while candidate DRC was running; result discarded"
        )
    return RouteCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=source_revision,
        patched_board_revision=patched_board_revision,
        patched_drc_context_revision=patched_drc_context_revision,
        summary=summary,
    )
