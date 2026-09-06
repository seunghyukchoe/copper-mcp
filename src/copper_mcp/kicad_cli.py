"""Bounded, read-only integration with the authoritative KiCad CLI DRC."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path, PurePosixPath
from types import MappingProxyType
from typing import Any, Literal

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_layered_route_patch import (
    KiCadLayeredRoutePatchError,
    render_kicad_layered_candidate_board,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    _render_kicad_disposed_candidate_board,
    render_kicad_candidate_board,
)
from copper_mcp.adapters.kicad_schematic import MAX_RENDERED_SCHEMATIC_BYTES
from copper_mcp.adapters.sexpr import SExpr, SExprError, atoms, parse_sexpr
from copper_mcp.attestation import (
    build_bundle_drc_statement,
    build_candidate_drc_statement,
    canonical_statement_bytes,
)
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary, ErcSummary
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.routing import (
    LayeredRouteCandidate,
    LayeredRouteRequest,
    RouteCandidate,
    VerifiedFill,
)
from copper_mcp.scene_render import (
    RENDER_LAYERS,
    SVG_CANONICALIZATION,
    SceneRenderError,
    SceneRenderEvidence,
    canonicalize_svg,
    render_digest,
)
from copper_mcp.security import (
    WorkspaceFileSnapshot,
    WorkspaceViolationError,
    read_workspace_file,
)
from copper_mcp.zone_fill import (
    FillIsland,
    ZoneFillError,
    fill_digest,
    read_fill_islands,
)

KICAD_DRC_SCHEMA = "https://schemas.kicad.org/drc.v1.json"
KICAD_ERC_SCHEMA = "https://schemas.kicad.org/erc.v1.json"
_MACOS_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
_ACCEPTED_DRC_RETURN_CODES = frozenset({0, 5})
_ACCEPTED_ERC_RETURN_CODES = frozenset({0, 5})
_MAX_ERC_SHEETS = 1_000
_MAX_ERC_VIOLATIONS = 100_000
_SEVERITIES = frozenset({"error", "warning"})
_INCLUDED_SEVERITIES = frozenset({"error", "warning", "exclusion"})
_DRC_CONTEXT_SUFFIXES = (".kicad_pro", ".kicad_dru")
_LOCAL_LIBRARY_SUFFIXES = frozenset({".kicad_mod", ".kicad_sym"})
_LOCAL_LIBRARY_TABLE_ROOTS = {
    "fp-lib-table": "fp_lib_table",
    "sym-lib-table": "sym_lib_table",
    "design-block-lib-table": "design_block_lib_table",
}
_LOCAL_LIBRARY_TABLES = frozenset(_LOCAL_LIBRARY_TABLE_ROOTS)
_KICAD_VERSION = re.compile(r"^\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?$")
_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_KICAD_DATE_TIME = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$"
)
_MAX_DRC_JSON_DEPTH = 64
_MAX_DRC_JSON_VALUES = 250_000
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
            "statement": self.to_statement(),
        }

    def to_statement(self) -> dict[str, Any]:
        """Return the redacted unsigned in-toto Statement payload."""

        return build_candidate_drc_statement(
            candidate_id=self.candidate_id,
            candidate_base_revision=self.candidate_base_revision,
            source_revision=self.source_revision,
            patched_board_revision=self.patched_board_revision,
            patched_drc_context_revision=self.patched_drc_context_revision,
            summary=self.summary,
        )

    def canonical_statement_bytes(self) -> bytes:
        """Return deterministic Statement JSON bytes; no signature is included."""

        return canonical_statement_bytes(self.to_statement())


@dataclass(frozen=True, slots=True)
class LayeredRouteCandidateDrcEvidence:
    """Immutable KiCad DRC evidence bound to one layered route proposal."""

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
            "statement": self.to_statement(),
        }

    def to_statement(self) -> dict[str, Any]:
        """Return the redacted unsigned in-toto Statement payload."""

        return build_candidate_drc_statement(
            candidate_id=self.candidate_id,
            candidate_base_revision=self.candidate_base_revision,
            source_revision=self.source_revision,
            patched_board_revision=self.patched_board_revision,
            patched_drc_context_revision=self.patched_drc_context_revision,
            summary=self.summary,
        )

    def canonical_statement_bytes(self) -> bytes:
        """Return deterministic Statement JSON bytes; no signature is included."""

        return canonical_statement_bytes(self.to_statement())


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


def _context_relative_path(path: Path, settings: Settings) -> str:
    root = settings.workspace.resolve(strict=True)
    try:
        relative = path.relative_to(root).as_posix()
    except ValueError as error:
        raise KiCadCliError("KiCad DRC context must stay inside the workspace") from error
    return relative


def _library_table_dependencies(
    table_path: Path,
    payload: bytes,
    board_path: Path,
    settings: Settings,
    *,
    deadline: float,
) -> tuple[Path, ...]:
    """Resolve only bounded, project-local KiCad file-library table entries."""

    expected_root = _LOCAL_LIBRARY_TABLE_ROOTS.get(table_path.name)
    if expected_root is None:
        raise KiCadCliError("KiCad DRC library table type is unsupported")
    try:
        root = parse_sexpr(payload, ParseLimits())
        if root.head != expected_root:
            raise SExprError("syntax.invalid", "library table root is unsupported", root.offset)
        libraries: list[SExpr] = []
        version_seen = False
        for item in root.items[1:]:
            if not isinstance(item, SExpr) or item.head not in {"version", "lib"}:
                raise SExprError(
                    "syntax.invalid",
                    "library table field is unsupported",
                    root.offset,
                )
            if item.head == "version":
                if version_seen or len(atoms(item)) != 1:
                    raise SExprError(
                        "syntax.invalid",
                        "library table version is malformed",
                        item.offset,
                    )
                version_seen = True
                continue
            libraries.append(item)
    except SExprError as error:
        raise KiCadCliError("KiCad DRC library table is malformed or over budget") from error

    if table_path.name == "design-block-lib-table" and libraries:
        raise KiCadCliError("KiCad DRC design-block library entries are unsupported")

    workspace_root = settings.workspace.resolve(strict=True)
    project_root = board_path.parent
    dependencies: list[Path] = []
    library_names: set[str] = set()
    allowed_fields = frozenset({"name", "type", "uri", "options", "descr", "hidden", "disabled"})
    for library in libraries:
        if time.monotonic() > deadline:
            raise KiCadCliError("KiCad DRC context discovery timed out")
        fields: dict[str, tuple[str, ...]] = {}
        try:
            for item in library.items[1:]:
                if (
                    not isinstance(item, SExpr)
                    or item.head is None
                    or item.head not in allowed_fields
                    or item.head in fields
                ):
                    raise SExprError(
                        "syntax.invalid",
                        "library entry field is unsupported",
                        library.offset,
                    )
                fields[item.head] = atoms(item)
        except SExprError as error:
            raise KiCadCliError("KiCad DRC library table entry is malformed") from error
        if not {"name", "type", "uri"} <= fields.keys():
            raise KiCadCliError("KiCad DRC library table entry is incomplete")
        if (
            len(fields["name"]) != 1
            or not fields["name"][0]
            or fields["name"][0] in library_names
            or fields["type"] != ("KiCad",)
            or len(fields["uri"]) != 1
            or ("options" in fields and fields["options"] != ("",))
            or ("descr" in fields and len(fields["descr"]) != 1)
            # KiCad also writes these as bare flags, whose atom tuple is empty,
            # so presence alone marks the entry as unusable for this run.
            or "hidden" in fields
            or "disabled" in fields
        ):
            raise KiCadCliError("KiCad DRC library table entry is unsupported")
        library_names.add(fields["name"][0])

        prefix = "${KIPRJMOD}/"
        uri = fields["uri"][0]
        if not uri.startswith(prefix) or "\\" in uri:
            raise KiCadCliError("KiCad DRC library URI must be project-local")
        relative_uri = PurePosixPath(uri.removeprefix(prefix))
        if (
            relative_uri.is_absolute()
            or not relative_uri.parts
            or any(part in {"", ".", ".."} for part in relative_uri.parts)
            or any("$" in part or ":" in part for part in relative_uri.parts)
        ):
            raise KiCadCliError("KiCad DRC library URI must be project-local")
        target = project_root.joinpath(*relative_uri.parts)
        try:
            target.relative_to(workspace_root)
        except ValueError as error:
            raise KiCadCliError("KiCad DRC library URI escapes the workspace") from error

        current = workspace_root
        try:
            for part in target.relative_to(workspace_root).parts:
                current /= part
                if stat.S_ISLNK(current.lstat().st_mode):
                    raise KiCadCliError("KiCad DRC library URI contains a symlink")
        except FileNotFoundError as error:
            raise KiCadCliError("KiCad DRC library dependency is missing") from error

        if table_path.name == "fp-lib-table":
            if target.suffix != ".pretty" or not target.is_dir():
                raise KiCadCliError("KiCad DRC footprint library target is unsupported")
            for candidate in target.rglob("*"):
                if time.monotonic() > deadline:
                    raise KiCadCliError("KiCad DRC context discovery timed out")
                try:
                    candidate_stat = candidate.lstat()
                except OSError as error:
                    raise KiCadCliError("KiCad DRC library dependency changed") from error
                if stat.S_ISLNK(candidate_stat.st_mode):
                    raise KiCadCliError("KiCad DRC library dependency contains a symlink")
                if stat.S_ISREG(candidate_stat.st_mode) and candidate.suffix == ".kicad_mod":
                    dependencies.append(candidate)
        elif table_path.name == "sym-lib-table":
            if target.suffix != ".kicad_sym" or not target.is_file():
                raise KiCadCliError("KiCad DRC symbol library target is unsupported")
            dependencies.append(target)
    return tuple(dependencies)


def _drc_context(
    board_path: Path,
    settings: Settings,
    board_snapshot: WorkspaceFileSnapshot | None = None,
) -> dict[str, bytes]:
    context: dict[str, bytes] = {}
    total_bytes = 0
    deadline = time.monotonic() + settings.max_drc_context_scan_seconds

    def check_discovery_budget() -> None:
        if time.monotonic() > deadline:
            raise KiCadCliError("KiCad DRC context discovery timed out")

    def add(candidate: Path, captured: WorkspaceFileSnapshot | None = None) -> bytes:
        nonlocal total_bytes
        check_discovery_budget()
        relative = _context_relative_path(candidate, settings)
        if relative in context:
            return context[relative]
        if len(context) >= settings.max_drc_context_files:
            raise KiCadCliError("KiCad DRC context exceeds the configured file-count limit")
        remaining_bytes = settings.max_drc_context_bytes - total_bytes
        if remaining_bytes <= 0:
            raise KiCadCliError("KiCad DRC context exceeds the configured cumulative limit")
        try:
            snapshot = captured or read_workspace_file(
                settings.workspace,
                relative,
                allowed_suffixes={
                    ".kicad_pcb",
                    *_DRC_CONTEXT_SUFFIXES,
                    *_LOCAL_LIBRARY_SUFFIXES,
                },
                allowed_names=_LOCAL_LIBRARY_TABLES,
                max_bytes=min(settings.max_board_bytes, remaining_bytes),
            )
            if snapshot.path != settings.workspace.resolve(strict=True) / relative:
                raise WorkspaceViolationError("captured context path does not match its request")
            payload = snapshot.content
        except WorkspaceViolationError as error:
            raise KiCadCliError(
                "KiCad DRC context exceeds the configured cumulative limit"
            ) from error
        check_discovery_budget()
        total_bytes += len(payload)
        if total_bytes > settings.max_drc_context_bytes:
            raise KiCadCliError("KiCad DRC context exceeds the configured cumulative limit")
        context[relative] = payload
        return payload

    add(board_path, board_snapshot)
    for suffix in _DRC_CONTEXT_SUFFIXES:
        candidate = board_path.with_suffix(suffix)
        if not candidate.exists():
            continue
        add(candidate)

    for table_name in sorted(_LOCAL_LIBRARY_TABLES):
        table_path = board_path.parent / table_name
        if not table_path.exists():
            continue
        table_payload = add(table_path)
        for dependency in _library_table_dependencies(
            table_path,
            table_payload,
            board_path,
            settings,
            deadline=deadline,
        ):
            add(dependency)
    return context


def _write_drc_snapshot(context: dict[str, bytes], destination_root: Path) -> None:
    for name, payload in context.items():
        destination = destination_root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(payload)


def _make_snapshot_read_only(snapshot_root: Path) -> None:
    """Remove write permission from every captured input before KiCad starts."""

    paths = tuple(snapshot_root.rglob("*"))
    for path in paths:
        file_stat = path.lstat()
        if stat.S_ISLNK(file_stat.st_mode):
            raise KiCadCliError("private KiCad DRC context contains a symlink")
        if stat.S_ISREG(file_stat.st_mode):
            path.chmod(0o400)
        elif not stat.S_ISDIR(file_stat.st_mode):
            raise KiCadCliError("private KiCad DRC context contains a special file")
    for path in sorted(
        (path for path in paths if path.is_dir()),
        key=lambda item: len(item.parts),
        reverse=True,
    ):
        path.chmod(0o500)
    snapshot_root.chmod(0o500)


def _validate_snapshot_tree(
    snapshot_root: Path,
    expected_files: frozenset[str],
    settings: Settings,
) -> None:
    """Bound and reject every unknown or writable child-side-effect path."""

    expected_directories: set[str] = set()
    for name in expected_files:
        parent = PurePosixPath(name).parent
        while str(parent) != ".":
            expected_directories.add(parent.as_posix())
            parent = parent.parent

    deadline = time.monotonic() + settings.max_drc_context_scan_seconds
    observed_files: set[str] = set()
    observed_directories: set[str] = set()
    total_bytes = 0
    try:
        root_stat = snapshot_root.lstat()
    except OSError as error:
        raise KiCadCliError("private KiCad DRC context could not be inspected") from error
    if not stat.S_ISDIR(root_stat.st_mode) or root_stat.st_mode & 0o222:
        raise KiCadCliError("private KiCad DRC context root changed or became writable")
    for path in snapshot_root.rglob("*"):
        if time.monotonic() > deadline:
            raise KiCadCliError("private KiCad DRC context inspection timed out")
        try:
            file_stat = path.lstat()
            relative = path.relative_to(snapshot_root).as_posix()
        except (OSError, ValueError) as error:
            raise KiCadCliError("private KiCad DRC context could not be inspected") from error
        if stat.S_ISLNK(file_stat.st_mode):
            raise KiCadCliError("private KiCad DRC context contains a symlink")
        if stat.S_ISDIR(file_stat.st_mode):
            if file_stat.st_mode & 0o222:
                raise KiCadCliError("private KiCad DRC context became writable")
            if relative not in expected_directories:
                raise KiCadCliError("private KiCad DRC context changed: unknown side effect")
            observed_directories.add(relative)
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise KiCadCliError("private KiCad DRC context contains a special file")
        if file_stat.st_mode & 0o222:
            raise KiCadCliError("private KiCad DRC context became writable")
        if relative not in expected_files:
            raise KiCadCliError("private KiCad DRC context changed: unknown side effect")
        observed_files.add(relative)
        if len(observed_files) > settings.max_drc_context_files:
            raise KiCadCliError("private KiCad DRC context exceeds the file-count limit")
        if file_stat.st_size > settings.max_board_bytes:
            raise KiCadCliError("private KiCad DRC context exceeds the per-file limit")
        total_bytes += file_stat.st_size
        if total_bytes > settings.max_drc_context_bytes:
            raise KiCadCliError("private KiCad DRC context exceeds the cumulative limit")
    if observed_files != set(expected_files) or observed_directories != expected_directories:
        raise KiCadCliError("private KiCad DRC context changed: unknown side effect")


def _private_kicad_environment(state_root: Path) -> dict[str, str]:
    """Create a minimal child environment isolated from live KiCad user state."""

    locations = {
        "HOME": state_root / "home",
        "KICAD_CONFIG_HOME": state_root / "config",
        "KICAD_DOCUMENTS_HOME": state_root / "documents",
        "XDG_CONFIG_HOME": state_root / "xdg-config",
        "XDG_CACHE_HOME": state_root / "cache",
        "XDG_DATA_HOME": state_root / "data",
        "XDG_STATE_HOME": state_root / "state",
        "XDG_RUNTIME_DIR": state_root / "runtime",
        "TMPDIR": state_root / "tmp",
    }
    state_root.mkdir(mode=0o700)
    for location in locations.values():
        location.mkdir(mode=0o700)
    return {
        "PATH": os.defpath,
        "LANG": "C",
        "LC_ALL": "C",
        **{name: str(location) for name, location in locations.items()},
    }


def _validate_private_kicad_state(state_root: Path, settings: Settings) -> None:
    """Reject symlinks, special files, or unbounded KiCad child side effects."""

    deadline = time.monotonic() + settings.max_drc_context_scan_seconds
    file_count = 0
    total_bytes = 0
    for path in state_root.rglob("*"):
        if time.monotonic() > deadline:
            raise KiCadCliError("private KiCad state inspection timed out")
        try:
            file_stat = path.lstat()
        except OSError as error:
            raise KiCadCliError("private KiCad state could not be inspected") from error
        if stat.S_ISLNK(file_stat.st_mode):
            raise KiCadCliError("private KiCad state contains a symlink")
        if stat.S_ISDIR(file_stat.st_mode):
            continue
        if not stat.S_ISREG(file_stat.st_mode):
            raise KiCadCliError("private KiCad state contains a special file")
        file_count += 1
        if file_count > settings.max_drc_context_files:
            raise KiCadCliError("private KiCad state exceeds the configured file-count limit")
        if file_stat.st_size > settings.max_drc_report_bytes:
            raise KiCadCliError("private KiCad state exceeds the configured per-file limit")
        total_bytes += file_stat.st_size
        if total_bytes > settings.max_drc_context_bytes:
            raise KiCadCliError("private KiCad state exceeds the configured cumulative limit")


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


def _preflight_drc_json(text: str, *, check_deadline: Callable[[], None] | None = None) -> None:
    """Bound JSON depth and approximate value count before recursive decoding."""

    depth = 0
    values = 0
    in_string = False
    escaped = False
    for index, character in enumerate(text):
        if check_deadline is not None and index % 4096 == 0:
            check_deadline()
        if in_string:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                in_string = False
            continue
        if character == '"':
            in_string = True
        elif character in "[{":
            depth += 1
            values += 1
            if depth > _MAX_DRC_JSON_DEPTH:
                raise ValueError("DRC report JSON exceeds the nesting budget")
        elif character in "]}":
            depth -= 1
            if depth < 0:
                raise ValueError("DRC report JSON is unbalanced")
        elif character == ",":
            values += 1
        if values > _MAX_DRC_JSON_VALUES:
            raise ValueError("DRC report JSON exceeds the value budget")
    if in_string or depth != 0:
        raise ValueError("DRC report JSON is incomplete")
    if check_deadline is not None:
        check_deadline()


def _drc_object_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("DRC report JSON contains a duplicate key")
        result[key] = value
    return result


def _reject_json_constant(value: str) -> Any:
    del value
    raise ValueError("DRC report JSON contains a non-finite number")


def _finite_json_float(value: str) -> float:
    decoded = float(value)
    if not math.isfinite(decoded):
        raise ValueError("DRC report JSON contains a non-finite number")
    return decoded


def _validate_drc_json_tree(
    value: Any, *, check_deadline: Callable[[], None] | None = None
) -> None:
    pending: list[tuple[Any, int]] = [(value, 1)]
    visited = 0
    while pending:
        if check_deadline is not None and visited % 4096 == 0:
            check_deadline()
        item, depth = pending.pop()
        visited += 1
        if visited > _MAX_DRC_JSON_VALUES or depth > _MAX_DRC_JSON_DEPTH:
            raise ValueError("DRC report JSON exceeds the structure budget")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
        elif not isinstance(item, str | int | float | bool | None):
            raise ValueError("DRC report JSON contains an unsupported value")
    if check_deadline is not None:
        check_deadline()


def _parse_drc_report(
    payload: bytes,
    *,
    return_code: int,
    base_revision: str,
    drc_context_revision: str,
    expected_source: str,
) -> DrcSummary:
    try:
        text = payload.decode("utf-8", errors="strict")
        _preflight_drc_json(text)
        report: Any = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _validate_drc_json_tree(report)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
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


def _candidate_drc_deadline_settings(settings: Settings, deadline: float | None) -> Settings:
    """Return per-phase limits that cannot outlive an external candidate deadline."""

    if deadline is None:
        return settings
    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise KiCadCliError("route candidate DRC deadline exceeded")
    return replace(
        settings,
        kicad_timeout_seconds=min(settings.kicad_timeout_seconds, remaining),
        max_drc_context_scan_seconds=min(settings.max_drc_context_scan_seconds, remaining),
    )


def _run_captured_drc(
    context: dict[str, bytes],
    *,
    board_relative: str,
    settings: Settings,
    deadline: float | None = None,
) -> DrcSummary:
    """Consume one bounded context through the sole fixed KiCad DRC command path."""

    settings = _candidate_drc_deadline_settings(settings, deadline)
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
            expected_snapshot_files = frozenset(context)
            _make_snapshot_read_only(workspace_snapshot)
            workspace_snapshot = workspace_snapshot.resolve(strict=True)
        except OSError as error:
            raise KiCadCliError("private KiCad DRC context could not be written") from error
        context.clear()
        snapshot_board = (workspace_snapshot / board_relative).resolve(strict=True)
        report_path = temporary_root / "drc.json"
        private_state = temporary_root / "process-state"
        settings = _candidate_drc_deadline_settings(settings, deadline)
        try:
            child_environment = _private_kicad_environment(private_state)
        except OSError as error:
            raise KiCadCliError("private KiCad process state could not be created") from error
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
            # SEC-113: untrusted child diagnostics keep a zero-byte parent capture budget, so a
            # chatty-but-valid run cannot spend the RLIMIT_FSIZE report budget on unread bytes.
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=settings.kicad_timeout_seconds,
                env=child_environment,
                cwd=child_environment["TMPDIR"],
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError("KiCad DRC timed out") from error
        settings = _candidate_drc_deadline_settings(settings, deadline)
        _validate_private_kicad_state(private_state, settings)
        _validate_snapshot_tree(workspace_snapshot, expected_snapshot_files, settings)
        if completed.returncode == -signal.SIGXFSZ:
            raise KiCadCliError("KiCad DRC report exceeds the configured limit")
        if completed.returncode not in _ACCEPTED_DRC_RETURN_CODES:
            raise KiCadCliError(f"KiCad DRC failed with exit code {completed.returncode}")
        try:
            private_context_after = _drc_context(
                snapshot_board,
                replace(
                    _candidate_drc_deadline_settings(settings, deadline),
                    workspace=workspace_snapshot,
                ),
            )
        except (KiCadCliError, OSError) as error:
            raise KiCadCliError("private KiCad DRC context changed during DRC") from error
        if _context_revision(private_context_after) != drc_context_revision:
            raise KiCadCliError("private KiCad DRC context changed during DRC")
        del private_context_after
        try:
            report = read_workspace_file(
                temporary_root,
                report_path.name,
                allowed_suffixes={".json"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
        except FileNotFoundError as error:
            raise KiCadCliError("KiCad DRC did not create a report") from error
        except WorkspaceViolationError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise KiCadCliError("KiCad DRC did not create a report") from error
            raise KiCadCliError("KiCad DRC report exceeds the configured limit") from error

    summary = _parse_drc_report(
        report,
        return_code=completed.returncode,
        base_revision=base_revision,
        drc_context_revision=drc_context_revision,
        expected_source=Path(board_relative).name,
    )
    _candidate_drc_deadline_settings(settings, deadline)
    return summary


@dataclass(frozen=True, slots=True)
class ZoneFillAuthority:
    """Evidence that a board's cached zone fill is what KiCad recomputes from it today."""

    source_revision: str
    context_revision: str
    source_fill_digest: str
    refilled_fill_digest: str
    kicad_version: str
    fill_polygon_count: int
    fill_vertex_count: int

    def __post_init__(self) -> None:
        for name, value in (
            ("source revision", self.source_revision),
            ("context revision", self.context_revision),
            ("source fill digest", self.source_fill_digest),
            ("refilled fill digest", self.refilled_fill_digest),
        ):
            if not _SHA256_ID.fullmatch(value):
                raise ValueError(f"{name} must be content-addressed with sha256")
        # A stale record must be impossible to construct, so freshness is a type invariant
        # rather than a flag a caller could forget to read.
        if self.source_fill_digest != self.refilled_fill_digest:
            raise ValueError("zone fill authority requires the cache to match a fresh refill")
        if not isinstance(self.kicad_version, str) or not self.kicad_version:
            raise ValueError("zone fill authority must record its KiCad version")
        for name, count in (
            ("fill polygon count", self.fill_polygon_count),
            ("fill vertex count", self.fill_vertex_count),
        ):
            if isinstance(count, bool) or not isinstance(count, int) or count < 0:
                raise ValueError(f"{name} must be a non-negative integer")

    def to_dict(self) -> dict[str, object]:
        """Return a detached plain dictionary of this evidence."""

        return {
            "source_revision": self.source_revision,
            "context_revision": self.context_revision,
            "source_fill_digest": self.source_fill_digest,
            "refilled_fill_digest": self.refilled_fill_digest,
            "kicad_version": self.kicad_version,
            "fill_polygon_count": self.fill_polygon_count,
            "fill_vertex_count": self.fill_vertex_count,
        }


class ZoneFillStaleError(KiCadCliError):
    """Raised when a board's cached zone fill disagrees with a fresh KiCad refill."""


def run_zone_fill_authority(
    requested_path: str,
    settings: Settings,
) -> tuple[ZoneFillAuthority, tuple[FillIsland, ...]]:
    """Prove a board's cached fill is fresh, and return it for use as connectivity evidence.

    KiCad is asked to refill on a private disposable copy and save the result there. The
    workspace board is never passed ``--refill-zones``; every other DRC path in this module
    deliberately omits it, and that stays true. Comparison is over canonical fill geometry
    rather than file bytes, because KiCad rewrites and reorders a board wholesale on save.
    """

    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    board_path = board.path
    # Fill depends on net-class clearances and custom rules, which live beside the board rather
    # than in it. Refilling without them recomputes a different pour and the freshness argument
    # would compare two boards that were never the same board.
    captured_context = _drc_context(board_path, settings, board)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    context_revision = _context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = _revision(source)
    parse_limits = parse_limits_for(settings)
    try:
        cached = read_fill_islands(
            source, max_vertices=settings.max_fill_vertices, limits=parse_limits
        )
    except ZoneFillError as error:
        raise KiCadCliError(f"cached zone fill could not be read: {error}") from error

    executable = discover_kicad_cli(settings)
    if os.name != "posix":
        raise KiCadCliError("bounded KiCad execution is unsupported on this platform")
    python_executable = _validated_executable(Path(sys.executable))
    bounded_exec = _BOUNDED_EXEC.resolve(strict=True)
    if python_executable is None or not bounded_exec.is_file():
        raise KiCadCliError("bounded KiCad execution helper is unavailable")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-fill-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        try:
            temporary_root.chmod(0o700)
        except OSError as error:
            raise KiCadCliError("private KiCad fill directory could not be secured") from error
        # The board stays writable here precisely because `--save-board` must rewrite it; that
        # is only ever this disposable copy.
        workspace_snapshot = temporary_root / "workspace"
        report_path = temporary_root / "fill-drc.json"
        private_state = temporary_root / "process-state"
        try:
            # The whole project context travels with the board, path-preserving, so KiCad
            # resolves the same rules it would in the workspace. Unlike the DRC snapshot this
            # tree stays writable, because `--save-board` has to rewrite the copy.
            _write_drc_snapshot(captured_context, workspace_snapshot)
            child_environment = _private_kicad_environment(private_state)
        except OSError as error:
            raise KiCadCliError("private KiCad fill context could not be written") from error
        private_board = (workspace_snapshot / board_relative).resolve(strict=True)
        command = [
            str(python_executable),
            "-I",
            str(bounded_exec),
            # The child writes both a report and a rewritten board, so the file ceiling has to
            # admit the larger of the two rather than the report budget alone.
            str(max(settings.max_drc_report_bytes, settings.max_board_bytes)),
            str(executable),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--refill-zones",
            "--save-board",
            "--output",
            str(report_path),
            str(private_board),
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
                env=child_environment,
                cwd=child_environment["TMPDIR"],
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError("KiCad zone refill timed out") from error
        _validate_private_kicad_state(private_state, settings)
        if completed.returncode == -signal.SIGXFSZ:
            raise KiCadCliError("KiCad zone refill report exceeds the configured limit")
        if completed.returncode not in _ACCEPTED_DRC_RETURN_CODES:
            raise KiCadCliError(f"KiCad zone refill failed with exit code {completed.returncode}")
        try:
            refilled_source = read_workspace_file(
                workspace_snapshot,
                board_relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
            report = read_workspace_file(
                temporary_root,
                report_path.name,
                allowed_suffixes={".json"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
        except (FileNotFoundError, WorkspaceViolationError) as error:
            raise KiCadCliError("KiCad zone refill did not produce a readable result") from error
        try:
            refilled = read_fill_islands(
                refilled_source, max_vertices=settings.max_fill_vertices, limits=parse_limits
            )
        except ZoneFillError as error:
            raise KiCadCliError(f"refilled zone fill could not be read: {error}") from error

    kicad_version = _report_kicad_version(report)
    # The whole live context, not just the board, must be unchanged: a rule edited mid-run would
    # mean the pour was compared against clearances that no longer apply.
    try:
        recaptured_context = _drc_context(board_path, settings)
    except (KiCadCliError, OSError) as error:
        raise KiCadCliError(
            "the workspace context changed while zone fill authority was running"
        ) from error
    if _context_revision(recaptured_context) != context_revision:
        raise KiCadCliError("the workspace context changed while zone fill authority was running")

    cached_digest = fill_digest(cached)
    refilled_digest = fill_digest(refilled)
    if cached_digest != refilled_digest:
        raise ZoneFillStaleError("the board's cached zone fill does not match a fresh KiCad refill")
    return (
        ZoneFillAuthority(
            source_revision=source_revision,
            context_revision=context_revision,
            source_fill_digest=cached_digest,
            refilled_fill_digest=refilled_digest,
            kicad_version=kicad_version,
            fill_polygon_count=len(cached),
            fill_vertex_count=sum(len(island.points) for island in cached),
        ),
        cached,
    )


def _report_kicad_version(report: bytes) -> str:
    """Read the KiCad version out of a DRC report without trusting anything else in it."""

    try:
        document = json.loads(report.decode("utf-8", errors="strict"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise KiCadCliError("KiCad zone refill report is not valid UTF-8 JSON") from error
    version = document.get("kicad_version") if isinstance(document, dict) else None
    if not isinstance(version, str) or not 1 <= len(version) <= 64:
        raise KiCadCliError("KiCad zone refill report has no usable version")
    return version


def run_board_drc(requested_path: str, settings: Settings) -> DrcSummary:
    """Run fixed-argument KiCad DRC and reject stale or malformed evidence."""

    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    board_path = board.path
    before_context = _drc_context(board_path, settings, board)
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


_ERC_UUID_PATH = re.compile(
    r"^/(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})"
    r"(?:/[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})*/?$"
)


@dataclass(frozen=True, slots=True)
class _ErcObservation:
    """Validated, identity-neutral observations from one KiCad ERC report."""

    kicad_version: str
    erc_schema: str
    coordinate_units: str
    error_count: int
    warning_count: int
    exclusion_count: int
    ignored_check_count: int
    sheet_count: int
    violation_type_counts: Mapping[str, int]
    passed: bool
    normalized_report_digest: str
    ignored_check_keys: tuple[str, ...]

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "violation_type_counts", MappingProxyType(dict(self.violation_type_counts))
        )


def _check_erc_report_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise KiCadCliError("KiCad ERC report deadline expired")


def _normalized_erc_report_digest(report: dict[str, Any], *, deadline: float | None = None) -> str:
    """Hash the validated report while ignoring only KiCad's root generation timestamp."""

    def ordered_json(value: object) -> str:
        _check_erc_report_deadline(deadline)
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        _check_erc_report_deadline(deadline)
        return encoded

    _check_erc_report_deadline(deadline)
    normalized = {key: value for key, value in report.items() if key != "date"}
    sheets = normalized["sheets"]
    assert isinstance(sheets, list)
    normalized_sheets: list[dict[str, Any]] = []
    for sheet in sheets:
        _check_erc_report_deadline(deadline)
        assert isinstance(sheet, dict)
        normalized_sheet = dict(sheet)
        violations = normalized_sheet["violations"]
        assert isinstance(violations, list)
        normalized_sheet["violations"] = sorted(violations, key=ordered_json)
        _check_erc_report_deadline(deadline)
        normalized_sheets.append(normalized_sheet)
    normalized["sheets"] = sorted(normalized_sheets, key=ordered_json)
    canonical = ordered_json(normalized).encode("utf-8")
    _check_erc_report_deadline(deadline)
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    _check_erc_report_deadline(deadline)
    return digest


def _parse_erc_observation(
    payload: bytes,
    *,
    return_code: int,
    expected_source: str,
    expected_uuid_paths: frozenset[str] | None = None,
    minimum_severities: Mapping[str, str] | None = None,
    deadline: float | None = None,
) -> _ErcObservation:
    """Accept only the reviewed KiCad ERC report shape and reduce it to redacted counts.

    The bounded-JSON helpers are shared with the DRC path on purpose: they are generic
    depth/value/duplicate-key guards, not DRC semantics.  Nothing here decides what an electrical
    rule violation *is* — KiCad already did that, and this function only transports the verdict.
    """

    if deadline is not None:
        if type(deadline) not in (int, float):
            raise KiCadCliError("KiCad ERC report deadline is malformed")
        try:
            deadline = float(deadline)
        except OverflowError:
            deadline = float("nan")
        if not math.isfinite(deadline):
            raise KiCadCliError("KiCad ERC report deadline is malformed")
    _check_erc_report_deadline(deadline)
    checkpoint = None if deadline is None else lambda: _check_erc_report_deadline(deadline)
    severity_floors: dict[str, str] | None = None
    if minimum_severities is not None:
        if (
            not isinstance(minimum_severities, Mapping)
            or not 1 <= len(minimum_severities) <= 10_000
        ):
            raise KiCadCliError("KiCad ERC severity floor constraints are malformed")
        severity_floors = {}
        for key, value in minimum_severities.items():
            _check_erc_report_deadline(deadline)
            if (
                type(key) is not str
                or not 1 <= len(key) <= 128
                or type(value) is not str
                or value not in {"error", "warning", "ignore"}
                or key in severity_floors
                or len(severity_floors) >= 10_000
            ):
                raise KiCadCliError("KiCad ERC severity floor constraints are malformed")
            severity_floors[key] = value

    try:
        text = payload.decode("utf-8", errors="strict")
        _check_erc_report_deadline(deadline)
        _preflight_drc_json(text, check_deadline=checkpoint)
        report: Any = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _check_erc_report_deadline(deadline)
        _validate_drc_json_tree(report, check_deadline=checkpoint)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise KiCadCliError("KiCad ERC report is not valid UTF-8 JSON") from error
    if not isinstance(report, dict):
        raise KiCadCliError("KiCad ERC report must be a JSON object")
    if report.get("$schema") != KICAD_ERC_SCHEMA:
        raise KiCadCliError("KiCad ERC report schema is unsupported")
    if report.get("coordinate_units") != "mm":
        raise KiCadCliError("KiCad ERC report must use millimetres")
    if report.get("source") != expected_source:
        raise KiCadCliError("KiCad ERC report source does not match the schematic snapshot")
    report_date = report.get("date")
    if not isinstance(report_date, str):
        raise KiCadCliError("KiCad ERC report has no valid generation date")
    try:
        datetime.fromisoformat(report_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise KiCadCliError("KiCad ERC report has no valid generation date") from error
    if not _KICAD_DATE_TIME.fullmatch(report_date):
        raise KiCadCliError("KiCad ERC report has no valid generation date")
    kicad_version = report.get("kicad_version")
    if not isinstance(kicad_version, str) or not _KICAD_VERSION.fullmatch(kicad_version):
        raise KiCadCliError("KiCad ERC report has no valid version")
    included_severities = report.get("included_severities")
    if (
        not isinstance(included_severities, list)
        or len(included_severities) != len(_INCLUDED_SEVERITIES)
        or not all(isinstance(item, str) for item in included_severities)
        or frozenset(included_severities) != _INCLUDED_SEVERITIES
    ):
        raise KiCadCliError("KiCad ERC report did not include all requested severities")
    ignored_checks = report.get("ignored_checks")
    if not isinstance(ignored_checks, list) or len(ignored_checks) > 10_000:
        raise KiCadCliError("KiCad ERC ignored-check collection is malformed")
    for ignored_check in ignored_checks:
        _check_erc_report_deadline(deadline)
        if not isinstance(ignored_check, dict):
            raise KiCadCliError("KiCad ERC ignored check is malformed")
        check_key = ignored_check.get("key")
        description = ignored_check.get("description")
        if (
            not isinstance(check_key, str)
            or not 1 <= len(check_key) <= 128
            or not isinstance(description, str)
        ):
            raise KiCadCliError("KiCad ERC ignored check fields are malformed")
        if severity_floors is not None and severity_floors.get(check_key) in _SEVERITIES:
            raise KiCadCliError("KiCad ERC ignored check violates its bound severity floor")

    # ERC reports are nested per sheet, unlike the flat DRC report. Legacy generated
    # schematics reject duplicate display paths; project reports identify sheets by UUID path.
    sheets = report.get("sheets")
    if not isinstance(sheets, list) or not sheets or len(sheets) > _MAX_ERC_SHEETS:
        raise KiCadCliError("KiCad ERC report sheet collection is malformed")

    severity_counts: Counter[str] = Counter()
    violation_type_counts: Counter[str] = Counter()
    exclusion_count = 0
    total_violations = 0
    seen_sheet_paths: set[str] = set()
    seen_uuid_paths: set[str] = set()
    for sheet in sheets:
        _check_erc_report_deadline(deadline)
        if not isinstance(sheet, dict):
            raise KiCadCliError("KiCad ERC sheet is malformed")
        sheet_path = sheet.get("path")
        uuid_path = sheet.get("uuid_path")
        violations = sheet.get("violations")
        if (
            not isinstance(sheet_path, str)
            or not 1 <= len(sheet_path) <= 1024
            or not isinstance(uuid_path, str)
            or not 1 <= len(uuid_path) <= 1024
        ):
            raise KiCadCliError("KiCad ERC sheet identity is malformed")
        if expected_uuid_paths is None and sheet_path in seen_sheet_paths:
            raise KiCadCliError("KiCad ERC report contains a duplicate sheet")
        seen_sheet_paths.add(sheet_path)
        if expected_uuid_paths is not None:
            if not _ERC_UUID_PATH.fullmatch(uuid_path):
                raise KiCadCliError("KiCad ERC sheet UUID path is malformed")
            canonical_uuid_path = uuid_path.rstrip("/")
            if canonical_uuid_path in seen_uuid_paths:
                raise KiCadCliError("KiCad ERC report contains a duplicate sheet UUID path")
            seen_uuid_paths.add(canonical_uuid_path)
        if not isinstance(violations, list):
            raise KiCadCliError("KiCad ERC sheet violations are malformed")
        total_violations += len(violations)
        if total_violations > _MAX_ERC_VIOLATIONS:
            raise KiCadCliError("KiCad ERC report contains too many findings")
        for violation in violations:
            _check_erc_report_deadline(deadline)
            if not isinstance(violation, dict):
                raise KiCadCliError("KiCad ERC violation is malformed")
            violation_type = violation.get("type")
            description = violation.get("description")
            severity = violation.get("severity")
            items = violation.get("items")
            excluded = violation.get("excluded", False)
            if not isinstance(violation_type, str) or not 1 <= len(violation_type) <= 128:
                raise KiCadCliError("KiCad ERC violation type is malformed")
            if not isinstance(description, str) or not isinstance(items, list):
                raise KiCadCliError("KiCad ERC violation fields are malformed")
            for item in items:
                _check_erc_report_deadline(deadline)
                if not isinstance(item, dict):
                    raise KiCadCliError("KiCad ERC violation fields are malformed")
            if severity not in _SEVERITIES:
                raise KiCadCliError("KiCad ERC violation severity is unsupported")
            if not isinstance(excluded, bool):
                raise KiCadCliError("KiCad ERC violation exclusion is malformed")
            if severity_floors is not None:
                expected = severity_floors.get(violation_type)
                if (
                    expected not in _SEVERITIES
                    or (expected == "error" and severity != "error")
                    or excluded
                ):
                    raise KiCadCliError("KiCad ERC finding does not meet its bound severity floor")
            if excluded:
                exclusion_count += 1
            else:
                severity_counts[severity] += 1
            violation_type_counts[violation_type] += 1

    expected_return_code = 5 if total_violations else 0
    if return_code != expected_return_code:
        raise KiCadCliError("KiCad ERC exit code does not match the report findings")

    if expected_uuid_paths is not None:
        if type(expected_uuid_paths) is not frozenset or not expected_uuid_paths:
            raise KiCadCliError("expected ERC sheet UUID paths are malformed")
        canonical_expected_paths: set[str] = set()
        for path in expected_uuid_paths:
            _check_erc_report_deadline(deadline)
            if type(path) is not str or not _ERC_UUID_PATH.fullmatch(path):
                raise KiCadCliError("expected ERC sheet UUID paths are malformed")
            canonical_expected_paths.add(path.rstrip("/"))
        if len(canonical_expected_paths) != len(expected_uuid_paths):
            raise KiCadCliError("expected ERC sheet UUID paths are ambiguous")
        if seen_uuid_paths != canonical_expected_paths:
            raise KiCadCliError("KiCad ERC report sheet UUID paths do not match the project")

    error_count = severity_counts["error"]
    observation = _ErcObservation(
        kicad_version=kicad_version,
        erc_schema=KICAD_ERC_SCHEMA,
        coordinate_units="mm",
        error_count=error_count,
        warning_count=severity_counts["warning"],
        exclusion_count=exclusion_count,
        ignored_check_count=len(ignored_checks),
        sheet_count=len(sheets),
        violation_type_counts=dict(sorted(violation_type_counts.items())),
        passed=error_count == 0,
        normalized_report_digest=_normalized_erc_report_digest(report, deadline=deadline),
        ignored_check_keys=tuple(sorted(check["key"] for check in ignored_checks)),
    )
    _check_erc_report_deadline(deadline)
    return observation


def _parse_erc_report(
    payload: bytes,
    *,
    return_code: int,
    intent_digest: str,
    schematic_digest: str,
    expected_source: str,
) -> ErcSummary:
    """Parse legacy generated-schematic ERC evidence with its real artifact bindings."""

    observation = _parse_erc_observation(
        payload,
        return_code=return_code,
        expected_source=expected_source,
    )
    return ErcSummary(
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        kicad_version=observation.kicad_version,
        erc_schema=observation.erc_schema,
        coordinate_units=observation.coordinate_units,
        error_count=observation.error_count,
        warning_count=observation.warning_count,
        exclusion_count=observation.exclusion_count,
        ignored_check_count=observation.ignored_check_count,
        sheet_count=observation.sheet_count,
        violation_type_counts=observation.violation_type_counts,
        passed=observation.passed,
    )


SCHEMATIC_SNAPSHOT_NAME = "circuit.kicad_sch"


def _run_bounded_schematic_command(
    schematic: bytes,
    *,
    subcommand: tuple[str, ...],
    flags: tuple[str, ...],
    output_name: str,
    output_suffix: str,
    accepted_return_codes: frozenset[int],
    label: str,
    settings: Settings,
) -> tuple[bytes, int]:
    """Run one fixed-argument ``kicad-cli sch`` command over CopperMCP's own rendered bytes.

    The input is never a workspace file: it is the deterministic render of a Circuit Intent
    snapshot the caller already submitted. That is what lets this path skip the library-table
    discovery and project snapshotting the board DRC adapter needs, and it means the subprocess
    is handed no user design data that did not already arrive through the tool argument.

    The schematic is written into a private read-only directory, the argument vector is fixed
    (no caller-supplied flags, no ``--define-var``), the child runs under the same ``RLIMIT_FSIZE``
    wrapper and private HOME/config environment as DRC, and the tree is re-validated afterwards so
    an unexpected side effect or a mutated input is refused rather than reported.
    """

    if not isinstance(schematic, bytes) or not schematic:
        raise KiCadCliError("schematic bytes are malformed")
    if len(schematic) > MAX_RENDERED_SCHEMATIC_BYTES:
        raise KiCadCliError("schematic exceeds the rendered byte ceiling")

    executable = discover_kicad_cli(settings)
    if os.name != "posix":
        raise KiCadCliError(f"bounded KiCad {label} execution is unsupported on this platform")
    python_executable = _validated_executable(Path(sys.executable))
    bounded_exec = _BOUNDED_EXEC.resolve(strict=True)
    if python_executable is None or not bounded_exec.is_file():
        raise KiCadCliError(f"bounded KiCad {label} execution helper is unavailable")

    with tempfile.TemporaryDirectory(prefix="copper-mcp-sch-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        try:
            temporary_root.chmod(0o700)
        except OSError as error:
            raise KiCadCliError(f"private KiCad {label} directory could not be secured") from error
        snapshot_root = temporary_root / "schematic"
        schematic_path = snapshot_root / SCHEMATIC_SNAPSHOT_NAME
        try:
            snapshot_root.mkdir(mode=0o700)
            schematic_path.write_bytes(schematic)
            _make_snapshot_read_only(snapshot_root)
            snapshot_root = snapshot_root.resolve(strict=True)
            schematic_path = schematic_path.resolve(strict=True)
        except OSError as error:
            raise KiCadCliError(f"private KiCad {label} schematic could not be written") from error
        output_path = temporary_root / output_name
        private_state = temporary_root / "process-state"
        try:
            child_environment = _private_kicad_environment(private_state)
        except OSError as error:
            raise KiCadCliError("private KiCad process state could not be created") from error
        kicad_command = [
            str(executable),
            "sch",
            *subcommand,
            *flags,
            "--output",
            str(output_path),
            str(schematic_path),
        ]
        command = [
            str(python_executable),
            "-I",
            str(bounded_exec),
            str(settings.max_drc_report_bytes),
            *kicad_command,
        ]
        try:
            # Untrusted child diagnostics keep a zero-byte parent capture budget, matching the
            # DRC path: a chatty-but-valid run cannot spend the RLIMIT_FSIZE output budget on
            # bytes nobody reads.
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=settings.kicad_timeout_seconds,
                env=child_environment,
                cwd=child_environment["TMPDIR"],
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError(f"KiCad {label} timed out") from error
        _validate_private_kicad_state(private_state, settings)
        try:
            _validate_snapshot_tree(snapshot_root, frozenset({SCHEMATIC_SNAPSHOT_NAME}), settings)
        except KiCadCliError as error:
            raise KiCadCliError(
                f"private KiCad {label} schematic changed during the run"
            ) from error
        if completed.returncode == -signal.SIGXFSZ:
            raise KiCadCliError(f"KiCad {label} output exceeds the configured limit")
        if completed.returncode not in accepted_return_codes:
            raise KiCadCliError(f"KiCad {label} failed with exit code {completed.returncode}")
        try:
            if schematic_path.read_bytes() != schematic:
                raise KiCadCliError(f"KiCad {label} modified its own schematic input")
        except OSError as error:
            raise KiCadCliError(f"private KiCad {label} schematic could not be re-read") from error
        try:
            output = read_workspace_file(
                temporary_root,
                output_path.name,
                allowed_suffixes={output_suffix},
                max_bytes=settings.max_drc_report_bytes,
            ).content
        except FileNotFoundError as error:
            raise KiCadCliError(f"KiCad {label} did not create an output file") from error
        except WorkspaceViolationError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise KiCadCliError(f"KiCad {label} did not create an output file") from error
            raise KiCadCliError(f"KiCad {label} output exceeds the configured limit") from error

    return output, completed.returncode


def _validated_schematic_digests(
    schematic: bytes,
    intent_digest: str,
    schematic_digest: str,
) -> None:
    """Refuse any binding whose digests are malformed or do not match the bytes."""

    for name, digest in (
        ("intent digest", intent_digest),
        ("schematic digest", schematic_digest),
    ):
        if not isinstance(digest, str) or not _SHA256_ID.fullmatch(digest):
            raise KiCadCliError(f"{name} is malformed")
    if not isinstance(schematic, bytes) or not schematic:
        raise KiCadCliError("schematic bytes are malformed")
    if _revision(schematic) != schematic_digest:
        raise KiCadCliError("schematic digest does not match the schematic bytes")


def run_circuit_schematic_erc(
    schematic: bytes,
    *,
    intent_digest: str,
    schematic_digest: str,
    settings: Settings,
) -> ErcSummary:
    """Run fixed-argument KiCad ERC over one rendered schematic and bind the verdict to it."""

    _validated_schematic_digests(schematic, intent_digest, schematic_digest)
    report, return_code = _run_bounded_schematic_command(
        schematic,
        subcommand=("erc",),
        flags=("--format", "json", "--units", "mm", "--severity-all", "--exit-code-violations"),
        output_name="erc.json",
        output_suffix=".json",
        accepted_return_codes=_ACCEPTED_ERC_RETURN_CODES,
        label="ERC",
        settings=settings,
    )
    return _parse_erc_report(
        report,
        return_code=return_code,
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        expected_source=SCHEMATIC_SNAPSHOT_NAME,
    )


def export_circuit_schematic_netlist(
    schematic: bytes,
    *,
    settings: Settings,
) -> bytes:
    """Export one rendered schematic to a KiCad format-E XML netlist for round-trip parity.

    This is the read-back half of the round trip: KiCad re-parses bytes CopperMCP wrote and
    reports the connectivity *it* found, which the pure parity verifier then compares against the
    immutable Circuit Intent snapshot. Returning the raw XML here is deliberate — it stays inside
    the process and only digests and counts ever reach a caller.
    """

    netlist, _ = _run_bounded_schematic_command(
        schematic,
        subcommand=("export", "netlist"),
        flags=("--format", "kicadxml"),
        output_name="netlist.xml",
        output_suffix=".xml",
        accepted_return_codes=frozenset({0}),
        label="netlist export",
        settings=settings,
    )
    return netlist


PARITY_SNAPSHOT_STEM = "parity"
PARITY_BOARD_SNAPSHOT_NAME = f"{PARITY_SNAPSHOT_STEM}.kicad_pcb"
PARITY_SCHEMATIC_SNAPSHOT_NAME = f"{PARITY_SNAPSHOT_STEM}.kicad_sch"

# Findings that speak to whether the board implements the intent's connectivity. These decide the
# verdict. Keys are KiCad's own settings keys; see docs/research/source-to-board-parity-v1.md.
PARITY_CONNECTIVITY_TYPES = frozenset(
    {"net_conflict", "missing_footprint", "extra_footprint", "duplicate_footprints"}
)
# Findings that are the unavoidable signature of a footprint-less Circuit Intent: the projection's
# empty Footprint and its Description cannot equal whatever the board's footprint carries. They are
# disclosed as counts and never treated as parity failures.
PARITY_PROJECTION_TYPES = frozenset(
    {
        "footprint_symbol_mismatch",
        "footprint_symbol_field_mismatch",
        "footprint_filters_mismatch",
    }
)
_PARITY_TYPES = PARITY_CONNECTIVITY_TYPES | PARITY_PROJECTION_TYPES
_MAX_PARITY_FINDINGS = 100_000


@dataclass(frozen=True, slots=True)
class SourceToBoardParityEvidence:
    """Redacted evidence that KiCad compared this exact board to this exact intent projection.

    Every field is a digest, a count, or a fixed literal. Parity descriptions embed net names
    verbatim and affected items carry UUIDs and coordinates, so nothing from a finding's body
    crosses this boundary — only how many of each KiCad type occurred.

    ``passed`` means KiCad reported no connectivity-class parity finding. It is only meaningful
    alongside ``oracle_live``, which records that the liveness invariant of ADR-0084 held: an empty
    ``schematic_parity`` array is otherwise indistinguishable from a parity check that never ran.
    """

    intent_digest: str
    schematic_digest: str
    parity_schematic_digest: str
    board_revision: str
    kicad_version: str
    drc_schema: str
    coordinate_units: str
    component_count: int
    connectivity_finding_count: int
    projection_finding_count: int
    parity_type_counts: Mapping[str, int]
    oracle_live: Literal["passed"]
    passed: bool

    def __post_init__(self) -> None:
        for name, digest in (
            ("intent digest", self.intent_digest),
            ("schematic digest", self.schematic_digest),
            ("parity schematic digest", self.parity_schematic_digest),
            ("board revision", self.board_revision),
        ):
            if not isinstance(digest, str) or not _SHA256_ID.fullmatch(digest):
                raise ValueError(f"source-to-board parity {name} is malformed")
        if self.schematic_digest == self.parity_schematic_digest:
            raise ValueError("parity projection must differ from the delivered schematic")
        if self.drc_schema != KICAD_DRC_SCHEMA or self.coordinate_units != "mm":
            raise ValueError("source-to-board parity report contract is unsupported")
        if not isinstance(self.kicad_version, str) or not _KICAD_VERSION.fullmatch(
            self.kicad_version
        ):
            raise ValueError("source-to-board parity KiCad version is malformed")
        if isinstance(self.component_count, bool) or self.component_count < 1:
            raise ValueError("source-to-board parity requires at least one component")
        for name, value in (
            ("connectivity", self.connectivity_finding_count),
            ("projection", self.projection_finding_count),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f"source-to-board parity {name} count is malformed")
        if self.oracle_live != "passed":
            raise ValueError("source-to-board parity evidence cannot represent a dead oracle")
        if not isinstance(self.passed, bool):
            raise ValueError("source-to-board parity verdict is malformed")
        # The verdict is derived, never asserted independently of the counts it summarises.
        if self.passed != (self.connectivity_finding_count == 0):
            raise ValueError("source-to-board parity verdict does not match its findings")


@dataclass(frozen=True, slots=True)
class _ParityObservation:
    kicad_version: str
    parity_type_counts: Mapping[str, int]
    drc_finding_count: int
    unconnected_finding_count: int
    normalized_report_digest: str | None

    def __post_init__(self) -> None:
        object.__setattr__(
            self, "parity_type_counts", MappingProxyType(dict(self.parity_type_counts))
        )


def _check_parity_report_deadline(deadline: float | None) -> None:
    if deadline is not None and time.monotonic() >= deadline:
        raise KiCadCliError("KiCad parity report deadline expired")


def _normalized_parity_report_digest(report: dict[str, Any], deadline: float | None) -> str:
    def ordered_json(value: object) -> str:
        _check_parity_report_deadline(deadline)
        encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
        _check_parity_report_deadline(deadline)
        return encoded

    normalized = {key: value for key, value in report.items() if key != "date"}
    for name in ("violations", "unconnected_items", "schematic_parity", "ignored_checks"):
        normalized[name] = sorted(normalized[name], key=ordered_json)
        _check_parity_report_deadline(deadline)
    canonical = ordered_json(normalized).encode()
    _check_parity_report_deadline(deadline)
    digest = "sha256:" + hashlib.sha256(canonical).hexdigest()
    _check_parity_report_deadline(deadline)
    return digest


def _parse_parity_observation(
    payload: bytes,
    *,
    return_code: int,
    expected_source: str,
    required_enabled_checks: frozenset[str] | None = None,
    deadline: float | None = None,
) -> _ParityObservation:
    """Validate native parity observations without inventing Circuit Intent identities."""
    if deadline is not None:
        if type(deadline) not in (int, float):
            raise KiCadCliError("KiCad parity report deadline is malformed")
        try:
            deadline = float(deadline)
        except OverflowError:
            deadline = float("nan")
        if not math.isfinite(deadline):
            raise KiCadCliError("KiCad parity report deadline is malformed")
    _check_parity_report_deadline(deadline)
    checkpoint = None if deadline is None else lambda: _check_parity_report_deadline(deadline)
    try:
        text = payload.decode("utf-8", errors="strict")
        _check_parity_report_deadline(deadline)
        _preflight_drc_json(text, check_deadline=checkpoint)
        report: Any = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _check_parity_report_deadline(deadline)
        _validate_drc_json_tree(report, check_deadline=checkpoint)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise KiCadCliError("KiCad parity report is not valid UTF-8 JSON") from error
    if not isinstance(report, dict):
        raise KiCadCliError("KiCad parity report must be a JSON object")
    if report.get("$schema") != KICAD_DRC_SCHEMA:
        raise KiCadCliError("KiCad parity report schema is unsupported")
    if report.get("coordinate_units") != "mm":
        raise KiCadCliError("KiCad parity report must use millimetres")
    if report.get("source") != expected_source:
        raise KiCadCliError("KiCad parity report source does not match the board snapshot")
    report_date = report.get("date")
    if not isinstance(report_date, str):
        raise KiCadCliError("KiCad parity report has no valid generation date")
    try:
        datetime.fromisoformat(report_date.replace("Z", "+00:00"))
    except ValueError as error:
        raise KiCadCliError("KiCad parity report has no valid generation date") from error
    if not _KICAD_DATE_TIME.fullmatch(report_date):
        raise KiCadCliError("KiCad parity report has no valid generation date")
    kicad_version = report.get("kicad_version")
    if not isinstance(kicad_version, str) or not _KICAD_VERSION.fullmatch(kicad_version):
        raise KiCadCliError("KiCad parity report has no valid version")
    # We never pass --exit-code-violations: exit 5 ORs three unrelated providers together and a
    # board with no schematic at all still returns it. A parity run must exit cleanly.
    if return_code != 0:
        raise KiCadCliError("KiCad parity run did not complete cleanly")

    included_severities = report.get("included_severities")
    if (
        not isinstance(included_severities, list)
        or len(included_severities) != len(_INCLUDED_SEVERITIES)
        or not all(isinstance(item, str) for item in included_severities)
        or frozenset(included_severities) != _INCLUDED_SEVERITIES
    ):
        # Parity findings are warning-severity, so a narrowed severity set silently empties the
        # array for a genuinely mismatched board.
        raise KiCadCliError("KiCad parity report did not include all requested severities")
    for collection in ("violations", "unconnected_items"):
        if not isinstance(report.get(collection), list):
            raise KiCadCliError("KiCad parity report collections are malformed")
    schematic_parity = report.get("schematic_parity")
    if not isinstance(schematic_parity, list) or len(schematic_parity) > _MAX_PARITY_FINDINGS:
        raise KiCadCliError("KiCad parity collection is malformed")

    parity_type_counts: Counter[str] = Counter()
    for finding in schematic_parity:
        _check_parity_report_deadline(deadline)
        if not isinstance(finding, dict):
            raise KiCadCliError("KiCad parity finding is malformed")
        finding_type = finding.get("type")
        description = finding.get("description")
        severity = finding.get("severity")
        items = finding.get("items")
        excluded = finding.get("excluded", False)
        if not isinstance(finding_type, str) or not 1 <= len(finding_type) <= 128:
            raise KiCadCliError("KiCad parity finding type is malformed")
        if not isinstance(description, str) or not isinstance(items, list):
            raise KiCadCliError("KiCad parity finding fields are malformed")
        for item in items:
            _check_parity_report_deadline(deadline)
            if not isinstance(item, dict):
                raise KiCadCliError("KiCad parity finding fields are malformed")
        if severity not in _SEVERITIES:
            raise KiCadCliError("KiCad parity finding severity is unsupported")
        if not isinstance(excluded, bool):
            raise KiCadCliError("KiCad parity finding exclusion is malformed")
        if finding_type not in _PARITY_TYPES:
            # An unreviewed parity type may carry semantics this contract has not considered.
            raise KiCadCliError("KiCad parity report contains an unreviewed finding type")
        if excluded:
            raise KiCadCliError("KiCad parity findings cannot be excluded in a private snapshot")
        parity_type_counts[finding_type] += 1

    # Extra project expectations are explicit; the legacy wrapper keeps its prior accepted set.
    normalized_digest = None
    if required_enabled_checks is not None:
        if (
            type(required_enabled_checks) is not frozenset
            or not required_enabled_checks
            or not required_enabled_checks <= _PARITY_TYPES
        ):
            raise KiCadCliError("KiCad parity check constraints are malformed")
        ignored = report.get("ignored_checks")
        if not isinstance(ignored, list) or len(ignored) > 10_000:
            raise KiCadCliError("KiCad parity ignored checks are malformed")
        seen: set[str] = set()
        for check in ignored:
            _check_parity_report_deadline(deadline)
            if not isinstance(check, dict):
                raise KiCadCliError("KiCad parity ignored check is malformed")
            key = check.get("key")
            if (
                not isinstance(key, str)
                or not 1 <= len(key) <= 128
                or key in seen
                or not isinstance(check.get("description"), str)
            ):
                raise KiCadCliError("KiCad parity ignored check is malformed")
            if key in required_enabled_checks:
                raise KiCadCliError("KiCad parity required check was ignored")
            seen.add(key)
        for name in ("violations", "unconnected_items"):
            for finding in report[name]:
                _check_parity_report_deadline(deadline)
                if (
                    not isinstance(finding, dict)
                    or not isinstance(finding.get("type"), str)
                    or not 1 <= len(finding["type"]) <= 128
                    or not isinstance(finding.get("description"), str)
                    or finding.get("severity") not in _SEVERITIES
                    or not isinstance(finding.get("items"), list)
                    or not isinstance(finding.get("excluded", False), bool)
                ):
                    raise KiCadCliError("KiCad parity companion finding is malformed")
                for item in finding["items"]:
                    _check_parity_report_deadline(deadline)
                    if not isinstance(item, dict):
                        raise KiCadCliError("KiCad parity companion item is malformed")
        normalized_digest = _normalized_parity_report_digest(report, deadline)
    observation = _ParityObservation(
        kicad_version=kicad_version,
        parity_type_counts=dict(sorted(parity_type_counts.items())),
        drc_finding_count=len(report["violations"]),
        unconnected_finding_count=len(report["unconnected_items"]),
        normalized_report_digest=normalized_digest,
    )
    _check_parity_report_deadline(deadline)
    return observation


def _parse_parity_report(
    payload: bytes,
    *,
    return_code: int,
    component_count: int,
    intent_digest: str,
    schematic_digest: str,
    parity_schematic_digest: str,
    board_revision: str,
) -> SourceToBoardParityEvidence:
    """Accept only the reviewed DRC report shape and reduce its parity array to counts.

    Nothing here decides what parity *is* — KiCad already did. This transports the verdict and
    enforces the one thing KiCad will not tell us: whether the check actually ran.
    """

    observation = _parse_parity_observation(
        payload, return_code=return_code, expected_source=PARITY_BOARD_SNAPSHOT_NAME
    )
    kicad_version = observation.kicad_version
    parity_type_counts = Counter(observation.parity_type_counts)

    # ADR-0084's liveness invariant. Under a board-eligible, footprint-less projection every
    # component must appear exactly once as either missing_footprint (absent from the board) or
    # footprint_symbol_mismatch (present, but the symbol carries no footprint identifier). A sum
    # of zero is what an unfetched netlist, a suppressed severity, or a board-excluded projection
    # all look like -- and all three otherwise present as a clean pass.
    accounted = (
        parity_type_counts["missing_footprint"] + parity_type_counts["footprint_symbol_mismatch"]
    )
    if accounted != component_count:
        raise KiCadCliError(
            "KiCad parity oracle did not account for every component; the parity check did not run"
        )

    connectivity_finding_count = sum(
        count for name, count in parity_type_counts.items() if name in PARITY_CONNECTIVITY_TYPES
    )
    projection_finding_count = sum(
        count for name, count in parity_type_counts.items() if name in PARITY_PROJECTION_TYPES
    )
    return SourceToBoardParityEvidence(
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        parity_schematic_digest=parity_schematic_digest,
        board_revision=board_revision,
        kicad_version=kicad_version,
        drc_schema=KICAD_DRC_SCHEMA,
        coordinate_units="mm",
        component_count=component_count,
        connectivity_finding_count=connectivity_finding_count,
        projection_finding_count=projection_finding_count,
        parity_type_counts=dict(sorted(parity_type_counts.items())),
        oracle_live="passed",
        passed=connectivity_finding_count == 0,
    )


def _validate_source_to_board_parity_projection(
    projection: bytes,
    *,
    component_count: int,
    intent_digest: str,
    schematic_digest: str,
    parity_schematic_digest: str,
) -> None:
    """Reject malformed parity bindings before a workspace read or subprocess."""

    if type(projection) is not bytes or not projection:
        raise KiCadCliError("parity projection bytes are malformed")
    if len(projection) > MAX_RENDERED_SCHEMATIC_BYTES:
        raise KiCadCliError("parity projection exceeds the rendered byte ceiling")
    if isinstance(component_count, bool) or not isinstance(component_count, int):
        raise KiCadCliError("parity component count is malformed")
    if component_count < 1:
        raise KiCadCliError("source-to-board parity requires at least one component")
    for name, digest in (
        ("intent digest", intent_digest),
        ("schematic digest", schematic_digest),
        ("parity schematic digest", parity_schematic_digest),
    ):
        if not isinstance(digest, str) or not _SHA256_ID.fullmatch(digest):
            raise KiCadCliError(f"parity {name} is malformed")
    if _revision(projection) != parity_schematic_digest:
        raise KiCadCliError("parity projection digest does not match the projection bytes")
    if schematic_digest == parity_schematic_digest:
        raise KiCadCliError("parity projection must differ from the delivered schematic")


def run_source_to_board_parity(
    requested_path: str,
    projection: bytes,
    *,
    component_count: int,
    intent_digest: str,
    schematic_digest: str,
    parity_schematic_digest: str,
    settings: Settings,
) -> SourceToBoardParityEvidence:
    """Ask KiCad whether one workspace board implements one Circuit Intent's connectivity.

    Unlike the ERC and netlist paths, this one *does* take a workspace board, because there is no
    other way to have a board to compare against. The board is read through the bounded workspace
    reader and copied, with the intent's board-eligible projection, into a private read-only
    snapshot under a fixed basename -- which is the only mechanism KiCad offers for pairing them,
    since ``JobExportDrc`` derives the schematic path by swapping the board's extension.

    No project file is written. KiCad therefore applies compiled-in default severities, so no user
    project can weaken this verdict -- and equally, the verdict is not necessarily what that user's
    project would report.
    """

    _validate_source_to_board_parity_projection(
        projection,
        component_count=component_count,
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        parity_schematic_digest=parity_schematic_digest,
    )
    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    return _run_captured_source_to_board_parity(
        board.content,
        projection,
        expected_board_revision=_revision(board.content),
        component_count=component_count,
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        parity_schematic_digest=parity_schematic_digest,
        settings=settings,
    )


def _run_captured_source_to_board_parity(
    board_bytes: bytes,
    projection: bytes,
    *,
    expected_board_revision: str,
    component_count: int,
    intent_digest: str,
    schematic_digest: str,
    parity_schematic_digest: str,
    settings: Settings,
    deadline: float | None = None,
) -> SourceToBoardParityEvidence:
    """Run parity on caller-captured board bytes through the fixed private snapshot."""

    if deadline is not None:
        if isinstance(deadline, bool) or not isinstance(deadline, (int, float)):
            raise KiCadCliError("captured parity deadline is malformed")
        if isinstance(deadline, float) and not math.isfinite(deadline):
            raise KiCadCliError("captured parity deadline is malformed")
        if isinstance(deadline, int) and not -sys.float_info.max <= deadline <= sys.float_info.max:
            raise KiCadCliError("captured parity deadline is malformed")
    _candidate_drc_deadline_settings(settings, deadline)
    if type(board_bytes) is not bytes or not board_bytes:
        raise KiCadCliError("captured parity board bytes are malformed")
    if len(board_bytes) > settings.max_board_bytes:
        raise KiCadCliError("captured parity board exceeds the configured byte ceiling")
    if not isinstance(expected_board_revision, str) or not _SHA256_ID.fullmatch(
        expected_board_revision
    ):
        raise KiCadCliError("captured parity board revision is malformed")
    if _revision(board_bytes) != expected_board_revision:
        raise KiCadCliError("captured parity board revision does not match the board bytes")
    _candidate_drc_deadline_settings(settings, deadline)
    _validate_source_to_board_parity_projection(
        projection,
        component_count=component_count,
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        parity_schematic_digest=parity_schematic_digest,
    )
    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    board_revision = expected_board_revision

    executable = discover_kicad_cli(phase_settings)
    if os.name != "posix":
        raise KiCadCliError("bounded KiCad parity execution is unsupported on this platform")
    python_executable = _validated_executable(Path(sys.executable))
    bounded_exec = _BOUNDED_EXEC.resolve(strict=True)
    if python_executable is None or not bounded_exec.is_file():
        raise KiCadCliError("bounded KiCad parity execution helper is unavailable")

    _candidate_drc_deadline_settings(settings, deadline)
    with tempfile.TemporaryDirectory(prefix="copper-mcp-parity-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        try:
            temporary_root.chmod(0o700)
        except OSError as error:
            raise KiCadCliError("private KiCad parity directory could not be secured") from error
        snapshot_root = temporary_root / "parity"
        board_path = snapshot_root / PARITY_BOARD_SNAPSHOT_NAME
        schematic_path = snapshot_root / PARITY_SCHEMATIC_SNAPSHOT_NAME
        try:
            snapshot_root.mkdir(mode=0o700)
            _candidate_drc_deadline_settings(settings, deadline)
            board_path.write_bytes(board_bytes)
            _candidate_drc_deadline_settings(settings, deadline)
            schematic_path.write_bytes(projection)
            _candidate_drc_deadline_settings(settings, deadline)
            _make_snapshot_read_only(snapshot_root)
            snapshot_root = snapshot_root.resolve(strict=True)
            board_path = board_path.resolve(strict=True)
        except OSError as error:
            raise KiCadCliError("private KiCad parity snapshot could not be written") from error
        output_path = temporary_root / "parity.json"
        private_state = temporary_root / "process-state"
        _candidate_drc_deadline_settings(settings, deadline)
        try:
            child_environment = _private_kicad_environment(private_state)
        except OSError as error:
            raise KiCadCliError("private KiCad process state could not be created") from error
        kicad_command = [
            str(executable),
            "pcb",
            "drc",
            "--schematic-parity",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--output",
            str(output_path),
            str(board_path),
        ]
        command = [
            str(python_executable),
            "-I",
            str(bounded_exec),
            str(phase_settings.max_drc_report_bytes),
            *kicad_command,
        ]
        try:
            phase_settings = _candidate_drc_deadline_settings(settings, deadline)
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=phase_settings.kicad_timeout_seconds,
                env=child_environment,
                cwd=child_environment["TMPDIR"],
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError("KiCad parity run timed out") from error
        phase_settings = _candidate_drc_deadline_settings(settings, deadline)
        _validate_private_kicad_state(private_state, phase_settings)
        phase_settings = _candidate_drc_deadline_settings(settings, deadline)
        try:
            _validate_snapshot_tree(
                snapshot_root,
                frozenset({PARITY_BOARD_SNAPSHOT_NAME, PARITY_SCHEMATIC_SNAPSHOT_NAME}),
                phase_settings,
            )
        except KiCadCliError as error:
            raise KiCadCliError("private KiCad parity snapshot changed during the run") from error
        if completed.returncode == -signal.SIGXFSZ:
            raise KiCadCliError("KiCad parity output exceeds the configured limit")
        try:
            _candidate_drc_deadline_settings(settings, deadline)
            if board_path.read_bytes() != board_bytes:
                raise KiCadCliError("KiCad parity run modified its own board input")
            _candidate_drc_deadline_settings(settings, deadline)
            if schematic_path.read_bytes() != projection:
                raise KiCadCliError("KiCad parity run modified its own projection input")
        except OSError as error:
            raise KiCadCliError("private KiCad parity inputs could not be re-read") from error
        phase_settings = _candidate_drc_deadline_settings(settings, deadline)
        try:
            report = read_workspace_file(
                temporary_root,
                output_path.name,
                allowed_suffixes={".json"},
                max_bytes=phase_settings.max_drc_report_bytes,
            ).content
        except FileNotFoundError as error:
            raise KiCadCliError("KiCad parity run did not create an output file") from error
        except WorkspaceViolationError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise KiCadCliError("KiCad parity run did not create an output file") from error
            raise KiCadCliError("KiCad parity output exceeds the configured limit") from error

    _candidate_drc_deadline_settings(settings, deadline)
    evidence = _parse_parity_report(
        report,
        return_code=completed.returncode,
        component_count=component_count,
        intent_digest=intent_digest,
        schematic_digest=schematic_digest,
        parity_schematic_digest=parity_schematic_digest,
        board_revision=board_revision,
    )
    _candidate_drc_deadline_settings(settings, deadline)
    return evidence


def _run_route_candidate_drc(
    requested_path: str,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    verified_fill: tuple[VerifiedFill, ...] = (),
    render_candidate: Callable[..., bytes],
    serialization_failure: str,
    deadline: float | None = None,
    expected_source_revision: str | None = None,
) -> RouteCandidateDrcEvidence:
    """Shared candidate-bound DRC flow after its caller selects the validation authority."""

    if not isinstance(candidate, RouteCandidate):
        raise KiCadCliError("route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadCliError("KiCad constraint profile is malformed")

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    board = read_workspace_file(
        phase_settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=phase_settings.max_board_bytes,
    )
    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    board_path = board.path
    captured_context = _drc_context(board_path, phase_settings, board)
    board_relative = board_path.relative_to(
        phase_settings.workspace.resolve(strict=True)
    ).as_posix()
    original_context_revision = _context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = _revision(source)
    if expected_source_revision is not None and source_revision != expected_source_revision:
        raise KiCadCliError("route candidate source changed before authoritative DRC")

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    parse_limits = parse_limits_for(phase_settings)
    conversion = parse_kicad_bytes(source, profile, parse_limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadCliError("captured KiCad board cannot be represented by the supported Board IR")
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise KiCadCliError("captured KiCad source revision is inconsistent")
    if candidate.base_revision != snapshot.snapshot_digest:
        raise KiCadCliError("route candidate is stale for the captured Board IR snapshot")
    try:
        patched_board = render_candidate(
            source,
            snapshot,
            candidate,
            profile,
            limits=parse_limits,
            verified_fill=verified_fill,
        )
    except KiCadRoutePatchError as error:
        raise KiCadCliError(serialization_failure) from error

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    patched_context = _candidate_drc_context(
        captured_context,
        board_relative=board_relative,
        patched_board=patched_board,
        settings=phase_settings,
    )
    patched_board_revision = _revision(patched_board)
    patched_drc_context_revision = _context_revision(patched_context)
    del captured_context, conversion, patched_board, snapshot, source

    summary = _run_captured_drc(
        patched_context,
        board_relative=board_relative,
        settings=_candidate_drc_deadline_settings(settings, deadline),
        deadline=deadline,
    )
    if (
        summary.base_revision != patched_board_revision
        or summary.drc_context_revision != patched_drc_context_revision
    ):
        raise KiCadCliError("KiCad DRC summary revision binding is inconsistent")
    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    if _context_revision(_drc_context(board_path, phase_settings)) != original_context_revision:
        raise KiCadCliError(
            "board or DRC rules changed while candidate DRC was running; result discarded"
        )
    _candidate_drc_deadline_settings(settings, deadline)
    return RouteCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=source_revision,
        patched_board_revision=patched_board_revision,
        patched_drc_context_revision=patched_drc_context_revision,
        summary=summary,
    )


def run_route_candidate_drc(
    requested_path: str,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    verified_fill: tuple[VerifiedFill, ...] = (),
) -> RouteCandidateDrcEvidence:
    """Bind an exact replayed reference-router candidate to authoritative KiCad DRC."""

    return _run_route_candidate_drc(
        requested_path,
        candidate,
        profile,
        settings,
        verified_fill=verified_fill,
        render_candidate=render_kicad_candidate_board,
        serialization_failure="route candidate failed replay-verified KiCad serialization",
    )


@dataclass(frozen=True, slots=True)
class RouteBundleDrcEvidence:
    """Immutable bindings between one composed route-bundle plan and KiCad DRC evidence.

    One DRC run covers the whole composition: the subject is the bundle, and the composed
    candidate set rides as a bound byproduct list rather than as N separate statements that
    could be cherry-picked into a differential.
    """

    bundle_id: str
    bundle_base_revision: str
    candidate_ids: tuple[str, ...]
    source_revision: str
    patched_board_revision: str
    patched_drc_context_revision: str
    summary: DrcSummary

    def __post_init__(self) -> None:
        for name in (
            "bundle_id",
            "bundle_base_revision",
            "source_revision",
            "patched_board_revision",
            "patched_drc_context_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_ID.fullmatch(value):
                raise ValueError(f"{name} must be content-addressed with sha256")
        if (
            not isinstance(self.candidate_ids, tuple)
            or not 2 <= len(self.candidate_ids) <= 8
            or any(
                not isinstance(item, str) or not _SHA256_ID.fullmatch(item)
                for item in self.candidate_ids
            )
            or len(set(self.candidate_ids)) != len(self.candidate_ids)
        ):
            raise ValueError("bundle candidate ids must be two to eight distinct digests")
        if not isinstance(self.summary, DrcSummary):
            raise ValueError("summary must be strict KiCad DRC evidence")
        if self.summary.base_revision != self.patched_board_revision:
            raise ValueError("DRC summary is not bound to the patched board revision")
        if self.summary.drc_context_revision != self.patched_drc_context_revision:
            raise ValueError("DRC summary is not bound to the patched context revision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "bundle_base_revision": self.bundle_base_revision,
            "candidate_ids": list(self.candidate_ids),
            "source_revision": self.source_revision,
            "patched_board_revision": self.patched_board_revision,
            "patched_drc_context_revision": self.patched_drc_context_revision,
            "summary": self.summary.to_dict(),
            "statement": self.to_statement(),
        }

    def to_statement(self) -> dict[str, Any]:
        """Return the redacted unsigned in-toto Statement payload."""

        return build_bundle_drc_statement(
            bundle_id=self.bundle_id,
            bundle_base_revision=self.bundle_base_revision,
            candidate_ids=self.candidate_ids,
            source_revision=self.source_revision,
            patched_board_revision=self.patched_board_revision,
            patched_drc_context_revision=self.patched_drc_context_revision,
            summary=self.summary,
        )

    def canonical_statement_bytes(self) -> bytes:
        """Return deterministic Statement JSON bytes; no signature is included."""

        return canonical_statement_bytes(self.to_statement())


def run_route_bundle_drc(
    requested_path: str,
    plan: object,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    deadline: float | None = None,
) -> RouteBundleDrcEvidence:
    """Bind one exact composed route-bundle plan to authoritative KiCad DRC.

    The plan is replayed against the original snapshot and all patches are spliced onto
    one private disposable board with a combined round-trip proof before KiCad starts, so
    structural problems refuse without executing a subprocess. Imports are deferred
    because the bundle adapter reaches this module through the preview path.
    """

    from copper_mcp.adapters.kicad_route_bundle_patch import render_kicad_route_bundle_board
    from copper_mcp.route_bundle import RouteBundlePlan

    if type(plan) is not RouteBundlePlan:
        raise KiCadCliError("route bundle plan is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadCliError("KiCad constraint profile is malformed")
    candidates = plan.candidates
    if (
        not isinstance(candidates, tuple)
        or not all(isinstance(item, RouteCandidate) for item in candidates)
        or any(item.base_revision != plan.base_revision for item in candidates)
    ):
        raise KiCadCliError("route bundle candidates are inconsistent with the plan")

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    board = read_workspace_file(
        phase_settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=phase_settings.max_board_bytes,
    )
    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    board_path = board.path
    captured_context = _drc_context(board_path, phase_settings, board)
    board_relative = board_path.relative_to(
        phase_settings.workspace.resolve(strict=True)
    ).as_posix()
    original_context_revision = _context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = _revision(source)

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    parse_limits = parse_limits_for(phase_settings)
    conversion = parse_kicad_bytes(source, profile, parse_limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadCliError("captured KiCad board cannot be represented by the supported Board IR")
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise KiCadCliError("captured KiCad source revision is inconsistent")
    if plan.base_revision != snapshot.snapshot_digest:
        raise KiCadCliError("route bundle plan is stale for the captured Board IR snapshot")
    try:
        patched_board = render_kicad_route_bundle_board(
            source,
            snapshot,
            plan,
            profile,
            limits=parse_limits,
        )
    except KiCadRoutePatchError as error:
        raise KiCadCliError("route bundle failed composed KiCad serialization") from error

    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    patched_context = _candidate_drc_context(
        captured_context,
        board_relative=board_relative,
        patched_board=patched_board,
        settings=phase_settings,
    )
    patched_board_revision = _revision(patched_board)
    patched_drc_context_revision = _context_revision(patched_context)
    del captured_context, conversion, patched_board, snapshot, source

    summary = _run_captured_drc(
        patched_context,
        board_relative=board_relative,
        settings=_candidate_drc_deadline_settings(settings, deadline),
        deadline=deadline,
    )
    if (
        summary.base_revision != patched_board_revision
        or summary.drc_context_revision != patched_drc_context_revision
    ):
        raise KiCadCliError("KiCad DRC summary revision binding is inconsistent")
    phase_settings = _candidate_drc_deadline_settings(settings, deadline)
    if _context_revision(_drc_context(board_path, phase_settings)) != original_context_revision:
        raise KiCadCliError(
            "board or DRC rules changed while bundle DRC was running; result discarded"
        )
    _candidate_drc_deadline_settings(settings, deadline)
    return RouteBundleDrcEvidence(
        bundle_id=plan.bundle_id,
        bundle_base_revision=plan.base_revision,
        candidate_ids=tuple(item.candidate_id for item in candidates),
        source_revision=source_revision,
        patched_board_revision=patched_board_revision,
        patched_drc_context_revision=patched_drc_context_revision,
        summary=summary,
    )


def run_disposed_route_candidate_drc(
    requested_path: str,
    disposition: object,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    expected_source_revision: str,
    deadline: float | None = None,
) -> RouteCandidateDrcEvidence:
    """Bind one exact external-disposer acceptance to authoritative KiCad DRC."""

    from copper_mcp.external_candidate_drc import _ExternalCandidateDisposition

    if type(disposition) is not _ExternalCandidateDisposition or disposition.candidate is None:
        raise KiCadCliError("external route candidate disposition is malformed or refused")
    if not disposition.verification.accepted:
        raise KiCadCliError("external route candidate disposition is inconsistent")
    if disposition.candidate.candidate_id != disposition.verification.candidate_id:
        raise KiCadCliError("external route candidate disposition is bound to another candidate")
    if not isinstance(expected_source_revision, str) or not _SHA256_ID.fullmatch(
        expected_source_revision
    ):
        raise KiCadCliError("external route candidate source revision is malformed")
    return _run_route_candidate_drc(
        requested_path,
        disposition.candidate,
        profile,
        settings,
        render_candidate=_render_kicad_disposed_candidate_board,
        serialization_failure="external route candidate failed disposer-verified serialization",
        deadline=deadline,
        expected_source_revision=expected_source_revision,
    )


def run_layered_route_candidate_drc(
    requested_path: str,
    candidate: LayeredRouteCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    request: LayeredRouteRequest,
) -> LayeredRouteCandidateDrcEvidence:
    """Bind an exact replayed layered candidate to private authoritative KiCad DRC."""

    if not isinstance(candidate, LayeredRouteCandidate):
        raise KiCadCliError("layered route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadCliError("KiCad constraint profile is malformed")
    if not isinstance(request, LayeredRouteRequest):
        raise KiCadCliError("layered route request is malformed")

    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    board_path = board.path
    captured_context = _drc_context(board_path, settings, board)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    original_context_revision = _context_revision(captured_context)
    source = captured_context[board_relative]
    source_revision = _revision(source)

    parse_limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(source, profile, parse_limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise KiCadCliError("captured KiCad board cannot be represented by the supported Board IR")
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise KiCadCliError("captured KiCad source revision is inconsistent")
    if candidate.base_revision != snapshot.snapshot_digest:
        raise KiCadCliError("layered route candidate is stale for the captured Board IR snapshot")
    try:
        patched_board = render_kicad_layered_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            request=request,
            limits=parse_limits,
        )
    except KiCadLayeredRoutePatchError as error:
        raise KiCadCliError(
            "layered route candidate failed replay-verified KiCad serialization"
        ) from error

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
            "board or DRC rules changed while layered candidate DRC was running; result discarded"
        )
    return LayeredRouteCandidateDrcEvidence(
        candidate_id=candidate.candidate_id,
        candidate_base_revision=candidate.base_revision,
        source_revision=source_revision,
        patched_board_revision=patched_board_revision,
        patched_drc_context_revision=patched_drc_context_revision,
        summary=summary,
    )


def _kicad_version(executable: Path, settings: Settings) -> str:
    """Ask the CLI its own version.

    An SVG export, unlike a DRC report, carries no version field, and a render is only
    comparable against another render from the same KiCad. Asking is the honest way to get
    it; inferring it from the executable's path would be a guess.
    """

    try:
        completed = subprocess.run(  # noqa: S603
            [str(executable), "--version"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=settings.kicad_timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        raise KiCadCliError("KiCad version query timed out") from error
    if completed.returncode != 0:
        raise KiCadCliError("KiCad version query failed")
    reported = completed.stdout.decode("utf-8", errors="replace").strip()
    if not _KICAD_VERSION.fullmatch(reported):
        raise KiCadCliError("KiCad reported an unrecognized version")
    return reported


def run_scene_render(
    requested_path: str,
    settings: Settings,
    *,
    side: str = "top",
) -> tuple[SceneRenderEvidence, bytes]:
    """Render one board to deterministic SVG bytes without touching the workspace.

    Unlike zone fill authority, the private snapshot here is made **read-only** before KiCad
    starts. Verified against KiCad 10.0.5: `pcb export svg` completes against a fully
    read-only input tree and writes nothing beside the board. Given a writable directory it
    does drop a `.kicad_prl` next to the input, which is exactly the side effect the
    read-only snapshot removes rather than merely relocates.
    """

    if side not in {"top", "bottom"}:
        raise SceneRenderError("a scene render side must be top or bottom")

    board = read_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    board_path = board.path
    # The project context travels with the board for the same reason it does for fill: layer
    # and theme settings live beside the board, and a render taken without them is a render of
    # a board that never existed. Recording context_revision is what makes the evidence
    # falsifiable rather than merely plausible.
    captured_context = _drc_context(board_path, settings, board)
    board_relative = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    context_revision = _context_revision(captured_context)
    source_revision = _revision(captured_context[board_relative])

    executable = discover_kicad_cli(settings)
    if os.name != "posix":
        raise KiCadCliError("bounded KiCad execution is unsupported on this platform")
    python_executable = _validated_executable(Path(sys.executable))
    bounded_exec = _BOUNDED_EXEC.resolve(strict=True)
    if python_executable is None or not bounded_exec.is_file():
        raise KiCadCliError("bounded KiCad execution helper is unavailable")
    kicad_version = _kicad_version(executable, settings)

    with tempfile.TemporaryDirectory(prefix="copper-mcp-render-") as temporary_directory:
        temporary_root = Path(temporary_directory)
        try:
            temporary_root.chmod(0o700)
        except OSError as error:
            raise KiCadCliError("private KiCad render directory could not be secured") from error
        workspace_snapshot = temporary_root / "workspace"
        try:
            _write_drc_snapshot(captured_context, workspace_snapshot)
            expected_snapshot_files = frozenset(captured_context)
            _make_snapshot_read_only(workspace_snapshot)
            workspace_snapshot = workspace_snapshot.resolve(strict=True)
        except OSError as error:
            raise KiCadCliError("private KiCad render context could not be written") from error
        snapshot_board = (workspace_snapshot / board_relative).resolve(strict=True)
        # The output name is fixed. It appears in KiCad's <title> line, so letting a caller
        # influence it would put caller-controlled text inside the artifact and, worse, make
        # the digest depend on the filename the caller happened to choose.
        render_path = temporary_root / "scene.svg"
        private_state = temporary_root / "process-state"
        try:
            child_environment = _private_kicad_environment(private_state)
        except OSError as error:
            raise KiCadCliError("private KiCad process state could not be created") from error
        command = [
            str(python_executable),
            "-I",
            str(bounded_exec),
            str(settings.max_render_bytes),
            str(executable),
            "pcb",
            "export",
            "svg",
            "--mode-single",
            # No drawing sheet: the frame and title block carry project text and add nothing
            # a router or a model can use.
            "--exclude-drawing-sheet",
            # Measured: colour output follows the active KiCad theme, so the same board
            # renders differently under "KiCad Classic" than under the default. Black and
            # white removes that dependence entirely and makes the bytes theme-invariant.
            "--black-and-white",
            # Board area only. The default is an A4 page with a small board in the corner.
            "--page-size-mode",
            "2",
            "--layers",
            ",".join(RENDER_LAYERS),
            "--output",
            str(render_path),
            str(snapshot_board),
        ]
        if side == "bottom":
            # Both sides draw the same copper layers; only the viewing side differs, so the
            # layer list is identical and `side` is recorded in evidence to keep the two
            # renders from being compared as though they were the same artifact.
            command.insert(command.index("--layers"), "--mirror")
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                check=False,
                shell=False,
                timeout=settings.kicad_timeout_seconds,
                env=child_environment,
                cwd=child_environment["TMPDIR"],
            )
        except subprocess.TimeoutExpired as error:
            raise KiCadCliError("KiCad board render timed out") from error
        _validate_private_kicad_state(private_state, settings)
        # Proves the read-only snapshot held: an export that wrote anything beside the board
        # would show up here as an unexpected path rather than being discovered later.
        _validate_snapshot_tree(workspace_snapshot, expected_snapshot_files, settings)
        if completed.returncode == -signal.SIGXFSZ:
            raise SceneRenderError("the board render exceeds the configured size limit")
        if completed.returncode != 0:
            raise KiCadCliError(f"KiCad board render failed with exit code {completed.returncode}")
        try:
            exported = read_workspace_file(
                temporary_root,
                render_path.name,
                allowed_suffixes={".svg"},
                max_bytes=settings.max_render_bytes,
            ).content
        except FileNotFoundError as error:
            raise KiCadCliError("KiCad did not produce a board render") from error
        except WorkspaceViolationError as error:
            if isinstance(error.__cause__, FileNotFoundError):
                raise KiCadCliError("KiCad did not produce a board render") from error
            raise SceneRenderError("the board render exceeds the configured size limit") from error

    # The whole live context, not just the board, must be unchanged; a layer or rule edited
    # mid-run would mean the evidence describes a board that no longer exists.
    try:
        recaptured_context = _drc_context(board_path, settings)
    except (KiCadCliError, OSError) as error:
        raise KiCadCliError(
            "the workspace context changed while the board render was running"
        ) from error
    if _context_revision(recaptured_context) != context_revision:
        raise KiCadCliError("the workspace context changed while the board render was running")

    canonical = canonicalize_svg(exported)
    return (
        SceneRenderEvidence(
            normalized_digest=render_digest(canonical),
            source_revision=source_revision,
            context_revision=context_revision,
            kicad_version=kicad_version,
            layers=RENDER_LAYERS,
            side=side,
            canonicalization=SVG_CANONICALIZATION,
            byte_count=len(canonical),
        ),
        canonical,
    )
