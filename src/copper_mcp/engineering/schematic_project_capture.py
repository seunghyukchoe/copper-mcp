"""Private bounded capture of one declared KiCad schematic project."""

from __future__ import annotations

import hashlib
import math
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import NoReturn

from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_settings import ProjectSettingsError, extract_project_variables
from copper_mcp.engineering.schematic_hierarchy import (
    SchematicHierarchy,
    SchematicHierarchyError,
    SchematicSource,
    derive_schematic_hierarchy,
)
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import WorkspaceViolationError, read_workspace_file

_MAX_SCHEMATICS = 64
_SHA256_PREFIX = "sha256:"
_SHA256_LENGTH = len(_SHA256_PREFIX) + 64


class SchematicProjectCaptureError(ValueError):
    """A fixed, redacted refusal from the schematic project capture boundary."""


@dataclass(frozen=True, slots=True, repr=False)
class ProjectFileBinding:
    """One caller-declared project file and its required SHA-256 digest."""

    path: str
    digest: str

    def __repr__(self) -> str:
        return "<ProjectFileBinding redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedProjectFile:
    path: str
    digest: str
    content: bytes = field(repr=False)

    def __repr__(self) -> str:
        return "<CapturedSchematicProjectFile redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class SchematicProjectCapture:
    """Frozen private project bytes and hierarchy; no semantic project validation occurs here."""

    _files: tuple[_CapturedProjectFile, ...] = field(repr=False)
    root_path: str
    project_path: str
    hierarchy: SchematicHierarchy
    digest: str

    def __repr__(self) -> str:
        return "<SchematicProjectCapture redacted>"


def _fail(message: str) -> NoReturn:
    raise SchematicProjectCaptureError(message)


def _portable_alias(path: str) -> str:
    return unicodedata.normalize("NFC", path.casefold())


def _canonical_path(path: object) -> str:
    if (
        type(path) is not str
        or not path
        or len(path) > 4096
        or path.startswith("~")
        or "\\" in path
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in path)
    ):
        _fail("schematic project capture bindings are malformed")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != path
    ):
        _fail("schematic project capture bindings are malformed")
    return path


def _canonical_digest(digest: object) -> str:
    if (
        type(digest) is not str
        or len(digest) != _SHA256_LENGTH
        or not digest.startswith(_SHA256_PREFIX)
        or any(character not in "0123456789abcdef" for character in digest[len(_SHA256_PREFIX) :])
    ):
        _fail("schematic project capture bindings are malformed")
    return digest


def _copy_limits(limits: CaptureLimits | None) -> CaptureLimits:
    if limits is None:
        return CaptureLimits()
    if type(limits) is not CaptureLimits:
        _fail("schematic project capture limits are malformed")
    copied = None
    try:
        copied = CaptureLimits(
            limits.max_file_bytes,
            limits.max_total_bytes,
            limits.max_capture_seconds,
        )
    except (AttributeError, ValueError):
        pass
    if copied is None:
        _fail("schematic project capture limits are malformed")
    return copied


def _deadline(started_at: float, limits: CaptureLimits, caller_deadline: float | None) -> float:
    configured = started_at + limits.max_capture_seconds
    if caller_deadline is None:
        return configured
    if type(caller_deadline) not in (int, float):
        _fail("schematic project capture deadline is malformed")
    value = None
    try:
        value = float(caller_deadline)
    except OverflowError:
        pass
    if value is None or not math.isfinite(value):
        _fail("schematic project capture deadline is malformed")
    return min(configured, value)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("schematic project capture deadline expired")


def _validate_bindings(
    root_path: object, files: object
) -> tuple[str, tuple[ProjectFileBinding, ...]]:
    root = _canonical_path(root_path)
    if PurePosixPath(root).suffix != ".kicad_sch":
        _fail("schematic project capture bindings are malformed")
    if type(files) is not tuple or not files or len(files) > _MAX_SCHEMATICS + 1:
        _fail("schematic project capture bindings are malformed")

    bindings: list[ProjectFileBinding] = []
    paths: set[str] = set()
    aliases: set[str] = set()
    for binding in files:
        if type(binding) is not ProjectFileBinding:
            _fail("schematic project capture bindings are malformed")
        path = _canonical_path(binding.path)
        digest = _canonical_digest(binding.digest)
        if PurePosixPath(path).suffix not in {".kicad_sch", ".kicad_pro"}:
            _fail("schematic project capture bindings are malformed")
        alias = _portable_alias(path)
        if path in paths or alias in aliases:
            _fail("schematic project capture bindings are ambiguous")
        paths.add(path)
        aliases.add(alias)
        bindings.append(ProjectFileBinding(path, digest))

    for alias in aliases:
        if any(parent.as_posix() in aliases for parent in PurePosixPath(alias).parents):
            _fail("schematic project capture bindings are ambiguous")

    project = str(PurePosixPath(root).with_suffix(".kicad_pro"))
    schematic_paths = {path for path in paths if PurePosixPath(path).suffix == ".kicad_sch"}
    project_paths = {path for path in paths if PurePosixPath(path).suffix == ".kicad_pro"}
    if (
        root not in schematic_paths
        or len(schematic_paths) > _MAX_SCHEMATICS
        or project_paths != {project}
    ):
        _fail("schematic project capture bindings are malformed")
    return root, tuple(sorted(bindings, key=lambda item: item.path))


def _read(
    workspace: Path,
    path: str,
    max_bytes: int,
    deadline: float,
    *,
    second_sweep: bool = False,
) -> bytes:
    _check_deadline(deadline)
    content: bytes | None = None
    try:
        content = read_workspace_file(
            workspace,
            path,
            allowed_suffixes=(PurePosixPath(path).suffix,),
            max_bytes=max_bytes,
        ).content
    except (WorkspaceViolationError, OSError, ValueError):
        pass
    # Raise outside the handler: even a suppressed exception context can retain private data.
    if content is None:
        _fail(
            "schematic project capture changed during capture"
            if second_sweep
            else "schematic project capture refused"
        )
    _check_deadline(deadline)
    return content


def _sha256(content: bytes, deadline: float) -> str:
    _check_deadline(deadline)
    digest = "sha256:" + hashlib.sha256(content).hexdigest()
    _check_deadline(deadline)
    return digest


def capture_schematic_project(
    workspace: Path,
    root_path: str,
    files: tuple[ProjectFileBinding, ...],
    *,
    limits: CaptureLimits | None = None,
    deadline: float | None = None,
) -> SchematicProjectCapture:
    """Capture bytes, derive their hierarchy, then revalidate every file before return."""

    started_at = time.monotonic()
    captured_limits = _copy_limits(limits)
    capture_deadline = _deadline(started_at, captured_limits, deadline)
    _check_deadline(capture_deadline)
    root, bindings = _validate_bindings(root_path, files)
    project = str(PurePosixPath(root).with_suffix(".kicad_pro"))

    captured: list[_CapturedProjectFile] = []
    total_bytes = 0
    for binding in bindings:
        remaining = captured_limits.max_total_bytes - total_bytes
        if remaining < 1:
            _fail("schematic project capture refused")
        first = _read(
            workspace,
            binding.path,
            min(captured_limits.max_file_bytes, remaining),
            capture_deadline,
        )
        if not first or _sha256(first, capture_deadline) != binding.digest:
            _fail("schematic project capture refused")
        total_bytes += len(first)
        if total_bytes > captured_limits.max_total_bytes:
            _fail("schematic project capture refused")
        captured.append(_CapturedProjectFile(binding.path, binding.digest, first))

    project_content = next(item.content for item in captured if item.path == project)
    project_variables: dict[str, str] | None = None
    try:
        project_variables = extract_project_variables(project_content, deadline=capture_deadline)
    except ProjectSettingsError:
        pass
    _check_deadline(capture_deadline)
    if project_variables is None:
        _fail("schematic project capture refused")

    hierarchy: SchematicHierarchy | None = None
    try:
        hierarchy = derive_schematic_hierarchy(
            root,
            tuple(
                SchematicSource(item.path, item.content)
                for item in captured
                if PurePosixPath(item.path).suffix == ".kicad_sch"
            ),
            limits=captured_limits,
            deadline=capture_deadline,
            project_variables=project_variables,
        )
    except SchematicHierarchyError:
        pass
    _check_deadline(capture_deadline)
    if hierarchy is None:
        _fail("schematic project capture refused")

    for item in captured:
        second = _read(
            workspace,
            item.path,
            len(item.content),
            capture_deadline,
            second_sweep=True,
        )
        if second != item.content:
            _fail("schematic project capture changed during capture")
    _check_deadline(capture_deadline)

    capture_digest = digest_document(
        "copper-mcp/schematic-project-capture/v1",
        {
            "root_path": root,
            "files": [
                {"path": item.path, "digest": item.digest, "size": len(item.content)}
                for item in sorted(captured, key=lambda item: item.path)
            ],
        },
    )
    _check_deadline(capture_deadline)
    return SchematicProjectCapture(tuple(captured), root, project, hierarchy, capture_digest)
