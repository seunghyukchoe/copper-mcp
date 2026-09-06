"""Private, bounded KiCad schematic hierarchy derivation.

KiCad schematic format reference:
https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/

This module derives file relationships and UUID instance paths from caller-captured bytes.  It
does not read files, resolve libraries, interpret circuit semantics, publish a public projection,
or grant engineering, completeness, execution, or apply authority.

Field tails follow KiCad 10.0.5, commit 18fb9289ff0efdca53c0352ed81a0973f0a6b58c,
eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr_parser.cpp: parseSchField and parseEDA_TEXT.
Supported effects are font (quoted face, size, thickness, bold, italic, color, line_spacing),
justify and hide.  Hyperlink (href) effects and unknown styling are explicitly unsupported.
Numeric styling is limited to finite decimals/scientific notation and signed 32-bit integers.
These are syntax checks, not text rendering or full schematic semantic validation.
"""

from __future__ import annotations

import hashlib
import math
import re
import time
import unicodedata
import uuid
from dataclasses import dataclass, field, replace
from datetime import date
from pathlib import PurePosixPath
from typing import NoReturn

from copper_mcp.adapters.sexpr import SExpr, SExprError, is_quoted_atom, parse_sexpr
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.engineering.capture import CaptureLimits

_MAX_FILES = 64
_MAX_EXPANDED_INSTANCES = 512
_MAX_FILE_EDGES = _MAX_EXPANDED_INSTANCES - 1
_MAX_HIERARCHY_DEPTH = 16
_MIN_FORMAT_VERSION = 20211123
_MAX_FORMAT_VERSION = 20260306
_HASH_CHUNK_BYTES = 64 * 1024
_VARIABLE_NAME = re.compile(r"[A-Za-z0-9_:]+")
_MAX_PROJECT_VARIABLES = 128
_MAX_PROJECT_VARIABLE_KEY_CHARS = 128
_MAX_PROJECT_VARIABLE_VALUE_CHARS = 4096
_MAX_VARIABLE_EXPANSION_STEPS = 8
_MAX_EXPANDED_REFERENCE_CHARS = 4096
_MAX_VARIABLE_EXPANSION_WORK = 65_536
_REFUSED_VARIABLES = {
    "CURRENT_DATE",
    "CURRENT_TIME_HH_MM_SS",
    "CURRENT_TIME_LOCALE",
    "VCSHASH",
    "VCSSHORTHASH",
}
_INTEGER = re.compile(r"[+-]?[0-9]+")
_NUMBER = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")


class SchematicHierarchyError(ValueError):
    """A redacted refusal from private schematic hierarchy derivation."""


def _fail(message: str) -> NoReturn:
    raise SchematicHierarchyError(message)


@dataclass(frozen=True, slots=True, repr=False)
class SchematicSource:
    """One immutable in-memory schematic source supplied by a trusted capture layer."""

    path: str
    content: bytes = field(repr=False)

    def __repr__(self) -> str:
        return "<SchematicSource redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class SchematicSourceDigest:
    path: str
    digest: str

    def __repr__(self) -> str:
        return "<SchematicSourceDigest redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class SchematicFileEdge:
    parent_path: str
    child_path: str
    sheet_name: str
    sheet_uuid: str

    def __repr__(self) -> str:
        return "<SchematicFileEdge redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class SchematicInstancePath:
    source_path: str
    uuid_path: str

    def __repr__(self) -> str:
        return "<SchematicInstancePath redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class SchematicHierarchy:
    """Frozen private metadata; it is deliberately not a public completeness projection."""

    source_digests: tuple[SchematicSourceDigest, ...]
    file_edges: tuple[SchematicFileEdge, ...]
    instance_paths: tuple[SchematicInstancePath, ...]

    def __repr__(self) -> str:
        return "<SchematicHierarchy redacted>"


@dataclass(frozen=True, slots=True)
class _SheetReference:
    name: str
    target_path: str
    sheet_uuid: str


@dataclass(frozen=True, slots=True)
class _ParsedSource:
    path: str
    root_uuid: str
    sheets: tuple[_SheetReference, ...]


def _portable_alias(path: str) -> str:
    return unicodedata.normalize("NFC", path.casefold())


def _canonical_source_path(path: object) -> str:
    if (
        type(path) is not str
        or not path
        or len(path) > 4096
        or path.startswith("~")
        or "\\" in path
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in path)
    ):
        _fail("schematic hierarchy source path is malformed")
    parsed = PurePosixPath(path)
    if (
        parsed.is_absolute()
        or parsed.suffix != ".kicad_sch"
        or not parsed.parts
        or any(part in {"", ".", ".."} for part in parsed.parts)
        or parsed.as_posix() != path
    ):
        _fail("schematic hierarchy source path is malformed")
    return path


def _operation_deadline(
    started_at: float, limits: CaptureLimits, caller_deadline: float | None
) -> float:
    configured = started_at + limits.max_capture_seconds
    if caller_deadline is None:
        return configured
    if type(caller_deadline) not in (int, float):
        _fail("schematic hierarchy deadline is malformed")
    try:
        value = float(caller_deadline)
    except OverflowError:
        _fail("schematic hierarchy deadline is malformed")
    if not math.isfinite(value):
        _fail("schematic hierarchy deadline is malformed")
    return min(configured, value)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("schematic hierarchy deadline expired")


def _sha256(payload: bytes, deadline: float) -> str:
    digest = hashlib.sha256()
    view = memoryview(payload)
    for offset in range(0, len(view), _HASH_CHUNK_BYTES):
        _check_deadline(deadline)
        digest.update(view[offset : offset + _HASH_CHUNK_BYTES])
    _check_deadline(deadline)
    return "sha256:" + digest.hexdigest()


def _validate_source_structure(root: SExpr, deadline: float) -> None:
    # Native parseKIID increments duplicates in its per-source m_uuids set.  Refuse
    # these sources so derived paths cannot disagree with KiCad's rewritten IDs.
    seen_uuids: set[str] = set()
    pending = [root]
    while pending:
        _check_deadline(deadline)
        expression = pending.pop()
        if (
            not expression.items
            or not isinstance(expression.items[0], str)
            or is_quoted_atom(expression.items[0])
        ):
            _fail("schematic hierarchy source is malformed")
        if expression.head == "uuid":
            value = _uuid_value(expression)
            if value in seen_uuids:
                _fail("schematic hierarchy source UUIDs are ambiguous")
            seen_uuids.add(value)
        for index, item in enumerate(expression.items[1:]):
            if index % 4096 == 0:
                _check_deadline(deadline)
            if isinstance(item, SExpr):
                pending.append(item)


def _direct_children(expression: SExpr, head: str, deadline: float) -> tuple[SExpr, ...]:
    matches: list[SExpr] = []
    for index, item in enumerate(expression.items[1:]):
        if index % 4096 == 0:
            _check_deadline(deadline)
        if isinstance(item, SExpr) and item.head == head:
            matches.append(item)
    return tuple(matches)


def _single_atom(expression: SExpr, *, quoted: bool | None = None) -> str:
    if len(expression.items) != 2 or not isinstance(expression.items[1], str):
        _fail("schematic hierarchy source is malformed")
    value = expression.items[1]
    if quoted is not None and is_quoted_atom(value) is not quoted:
        _fail("schematic hierarchy source is malformed")
    return value


def _uuid_value(expression: SExpr) -> str:
    value = _single_atom(expression)
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, ValueError):
        _fail("schematic hierarchy source is malformed")
    canonical = str(parsed)
    if value.casefold() != canonical:
        _fail("schematic hierarchy source is malformed")
    return canonical


def _format_version(root: SExpr, deadline: float) -> int:
    versions = _direct_children(root, "version", deadline)
    if len(versions) != 1:
        _fail("schematic hierarchy source header is malformed")
    value = _single_atom(versions[0], quoted=False)
    if len(value) != 8 or not value.isascii() or not value.isdecimal():
        _fail("schematic hierarchy source header is malformed")
    version = int(value)
    try:
        date(version // 10_000, version // 100 % 100, version % 100)
    except ValueError:
        _fail("schematic hierarchy source header is malformed")
    if not _MIN_FORMAT_VERSION <= version <= _MAX_FORMAT_VERSION:
        _fail("schematic hierarchy source header is malformed")
    return version


def _resolve_reference(
    parent_path: str,
    raw_reference: str,
    source_paths: frozenset[str],
    project_variables: dict[str, str],
    project_name: str,
    deadline: float,
) -> str:
    if (
        not raw_reference
        or len(raw_reference) > 4096
        or "\\" in raw_reference
        or raw_reference.startswith("~")
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in raw_reference)
    ):
        _fail("schematic hierarchy reference is malformed")

    relative = _expand_reference(raw_reference, project_variables, project_name, deadline)
    base_parts = list(PurePosixPath(parent_path).parent.parts)

    if (
        not relative
        or PurePosixPath(relative).is_absolute()
        or relative.startswith("/")
        or ":" in relative
        or "$" in relative
    ):
        _fail("schematic hierarchy reference is malformed")

    for part in relative.split("/"):
        if part == ".":
            continue
        if part == "..":
            if not base_parts:
                _fail("schematic hierarchy reference escapes the workspace")
            base_parts.pop()
            continue
        if not part:
            _fail("schematic hierarchy reference is malformed")
        base_parts.append(part)

    target = PurePosixPath(*base_parts).as_posix()
    if PurePosixPath(target).suffix != ".kicad_sch":
        _fail("schematic hierarchy reference is malformed")
    if target not in source_paths:
        _fail("schematic hierarchy reference target is missing")
    return target


def _copy_project_variables(project_variables: object) -> dict[str, str]:
    if project_variables is None:
        return {}
    if type(project_variables) is not dict or len(project_variables) > _MAX_PROJECT_VARIABLES:
        _fail("schematic hierarchy project variables are malformed")
    copied: dict[str, str] = {}
    for key, value in project_variables.items():
        if (
            type(key) is not str
            or not key
            or len(key) > _MAX_PROJECT_VARIABLE_KEY_CHARS
            or not _VARIABLE_NAME.fullmatch(key)
            or type(value) is not str
            or len(value) > _MAX_PROJECT_VARIABLE_VALUE_CHARS
        ):
            _fail("schematic hierarchy project variables are malformed")
        copied[key] = value
    return copied


def _expand_reference(
    raw_reference: str, project_variables: dict[str, str], project_name: str, deadline: float
) -> str:
    remaining_work = _MAX_VARIABLE_EXPANSION_WORK

    def resolve(value: str, chain: tuple[str, ...]) -> str:
        nonlocal remaining_work
        _check_deadline(deadline)
        remaining_work -= len(value) + 1
        if remaining_work < 0:
            _fail("schematic hierarchy variable expansion work budget exceeded")
        result: list[str] = []
        index = 0
        while index < len(value):
            character = value[index]
            if character != "$":
                result.append(character)
                index += 1
                continue
            if index + 2 >= len(value) or value[index + 1] != "{":
                _fail("schematic hierarchy reference is malformed")
            close = value.find("}", index + 2)
            if close < 0:
                _fail("schematic hierarchy reference is malformed")
            name = value[index + 2 : close]
            if not _VARIABLE_NAME.fullmatch(name) or name in _REFUSED_VARIABLES:
                _fail("schematic hierarchy reference is malformed")
            if name == "PROJECTNAME":
                replacement = project_name
            else:
                if name not in project_variables or name in chain:
                    _fail("schematic hierarchy reference is malformed")
                if len(chain) >= _MAX_VARIABLE_EXPANSION_STEPS:
                    _fail("schematic hierarchy reference is malformed")
                replacement = resolve(project_variables[name], (*chain, name))
            result.append(replacement)
            if sum(map(len, result)) > _MAX_EXPANDED_REFERENCE_CHARS:
                _fail("schematic hierarchy reference is malformed")
            index = close + 1
        expanded = "".join(result)
        if len(expanded) > _MAX_EXPANDED_REFERENCE_CHARS:
            _fail("schematic hierarchy reference is malformed")
        _check_deadline(deadline)
        return expanded

    return resolve(raw_reference, ())


def _field_numbers(values: tuple[str | SExpr, ...], count: int, *, integer: bool = False) -> None:
    if len(values) != count:
        _fail("schematic hierarchy sheet fields are malformed")
    pattern = _INTEGER if integer else _NUMBER
    for value in values:
        if not isinstance(value, str) or is_quoted_atom(value) or not pattern.fullmatch(value):
            _fail("schematic hierarchy sheet fields are malformed")
        if integer:
            if not -(2**31) <= int(value) < 2**31:
                _fail("schematic hierarchy sheet fields are malformed")
        elif not math.isfinite(float(value)):
            _fail("schematic hierarchy sheet fields are malformed")


def _field_bool(values: tuple[str | SExpr, ...], *, optional: bool = False) -> None:
    if optional and not values:
        return
    if (
        len(values) != 1
        or not isinstance(values[0], str)
        or is_quoted_atom(values[0])
        or values[0] not in {"yes", "no"}
    ):
        _fail("schematic hierarchy sheet fields are malformed")


def _font_effects(expression: SExpr, deadline: float) -> None:
    for item in expression.items[1:]:
        _check_deadline(deadline)
        if isinstance(item, str):
            if is_quoted_atom(item) or item not in {"bold", "italic"}:
                _fail("schematic hierarchy sheet font effects are unsupported")
            continue
        values = item.items[1:]
        if item.head == "face":
            if len(values) != 1 or not isinstance(values[0], str) or not is_quoted_atom(values[0]):
                _fail("schematic hierarchy sheet font face must be quoted text")
        elif item.head == "size":
            _field_numbers(values, 2)
        elif item.head in {"thickness", "line_spacing"}:
            _field_numbers(values, 1)
        elif item.head in {"bold", "italic"}:
            _field_bool(values, optional=True)
        elif item.head == "color":
            _field_numbers(values[:3], 3, integer=True)
            _field_numbers(values[3:], 1)
        else:
            _fail("schematic hierarchy sheet font effects are unsupported")


def _text_effects(expression: SExpr, deadline: float) -> None:
    for item in expression.items[1:]:
        _check_deadline(deadline)
        if isinstance(item, str):
            if is_quoted_atom(item) or item != "hide":
                _fail("schematic hierarchy sheet text effects are unsupported")
            continue
        if item.head == "font":
            _font_effects(item, deadline)
        elif item.head == "justify":
            for value in item.items[1:]:
                _check_deadline(deadline)
                if (
                    not isinstance(value, str)
                    or is_quoted_atom(value)
                    or value not in {"left", "right", "top", "bottom", "mirror"}
                ):
                    _fail("schematic hierarchy sheet text justification is malformed")
        elif item.head == "hide":
            _field_bool(item.items[1:], optional=True)
        else:
            _fail("schematic hierarchy sheet text effects are unsupported")


def _sheet_property(expression: SExpr, deadline: float) -> tuple[str, str]:
    payload = expression.items[1:]
    if (
        expression.head == "property"
        and payload
        and isinstance(payload[0], str)
        and not is_quoted_atom(payload[0])
        and payload[0] == "private"
    ):
        payload = payload[1:]
    if (
        len(payload) < 2
        or not isinstance(payload[0], str)
        or not isinstance(payload[1], str)
        or not is_quoted_atom(payload[0])
        or not is_quoted_atom(payload[1])
        or not payload[0]
    ):
        _fail("schematic hierarchy sheet fields are malformed")
    for tail in payload[2:]:
        _check_deadline(deadline)
        if not isinstance(tail, SExpr):
            _fail("schematic hierarchy sheet fields are malformed")
        if tail.head == "id":
            _field_numbers(tail.items[1:], 1, integer=True)
        elif tail.head == "at":
            _field_numbers(tail.items[1:], 3)
        elif tail.head == "hide":
            _field_bool(tail.items[1:])
        elif tail.head in {"show_name", "do_not_autoplace"}:
            _field_bool(tail.items[1:], optional=True)
        elif tail.head == "effects":
            _text_effects(tail, deadline)
        else:
            _fail("schematic hierarchy sheet property tail is unsupported")
    return payload[0], payload[1]


def _parse_sheet(
    sheet: SExpr,
    *,
    parent_path: str,
    source_paths: frozenset[str],
    project_variables: dict[str, str],
    project_name: str,
    deadline: float,
) -> _SheetReference:
    uuids = _direct_children(sheet, "uuid", deadline)
    if len(uuids) != 1:
        _fail("schematic hierarchy sheet fields are malformed")
    sheet_uuid = _uuid_value(uuids[0])

    names: list[str] = []
    files: list[str] = []
    for item in sheet.items[1:]:
        _check_deadline(deadline)
        if not isinstance(item, SExpr) or item.head == "private":
            _fail("schematic hierarchy sheet fields are malformed")
        if item.head != "property":
            continue
        key, value = _sheet_property(item, deadline)
        normalized = key.casefold()
        if normalized in {"sheetname", "sheet name"}:
            names.append(value)
        elif normalized in {"sheetfile", "sheet file"}:
            files.append(value)
    if len(names) != 1 or len(files) != 1 or not names[0] or not files[0]:
        _fail("schematic hierarchy sheet fields are malformed")
    return _SheetReference(
        name=names[0],
        target_path=_resolve_reference(
            parent_path, files[0], source_paths, project_variables, project_name, deadline
        ),
        sheet_uuid=sheet_uuid,
    )


def _parse_source(
    path: str,
    content: bytes,
    *,
    source_paths: frozenset[str],
    project_variables: dict[str, str],
    project_name: str,
    parse_limits: ParseLimits,
    remaining_file_edges: int,
    deadline: float,
) -> _ParsedSource:
    try:
        root = parse_sexpr(content, parse_limits, check_deadline=lambda: _check_deadline(deadline))
    except SExprError:
        _fail("schematic hierarchy source is malformed")
    _validate_source_structure(root, deadline)
    if root.head != "kicad_sch":
        _fail("schematic hierarchy source header is malformed")
    _format_version(root, deadline)
    root_uuids = _direct_children(root, "uuid", deadline)
    if len(root_uuids) != 1:
        _fail("schematic hierarchy root UUID is malformed")
    root_uuid = _uuid_value(root_uuids[0])

    sheets: list[_SheetReference] = []
    for item in root.items[1:]:
        _check_deadline(deadline)
        if not isinstance(item, SExpr) or item.head != "sheet":
            continue
        if len(sheets) >= remaining_file_edges:
            _fail("schematic hierarchy file edge budget exceeded")
        sheets.append(
            _parse_sheet(
                item,
                parent_path=path,
                source_paths=source_paths,
                project_variables=project_variables,
                project_name=project_name,
                deadline=deadline,
            )
        )
    _check_deadline(deadline)
    return _ParsedSource(path=path, root_uuid=root_uuid, sheets=tuple(sheets))


def _require_acyclic_and_complete(
    root_path: str, parsed: dict[str, _ParsedSource], deadline: float
) -> None:
    state: dict[str, int] = {}
    reachable: set[str] = set()
    stack: list[tuple[str, int]] = [(root_path, 0)]
    while stack:
        _check_deadline(deadline)
        path, next_child = stack[-1]
        if state.get(path, 0) == 0:
            state[path] = 1
            reachable.add(path)
        sheets = parsed[path].sheets
        if next_child >= len(sheets):
            state[path] = 2
            stack.pop()
            continue
        stack[-1] = (path, next_child + 1)
        child_path = sheets[next_child].target_path
        child_state = state.get(child_path, 0)
        if child_state == 1:
            _fail("schematic hierarchy contains a file cycle")
        if child_state == 0:
            stack.append((child_path, 0))
    if len(reachable) != len(parsed):
        _fail("schematic hierarchy contains unreachable source files")


def _expand_instances(
    root_path: str, parsed: dict[str, _ParsedSource], deadline: float
) -> tuple[SchematicInstancePath, ...]:
    root = parsed[root_path]
    pending: list[tuple[str, tuple[str, ...]]] = [(root_path, (root.root_uuid,))]
    instances: list[SchematicInstancePath] = []
    while pending:
        _check_deadline(deadline)
        source_path, uuid_path = pending.pop()
        if len(uuid_path) > _MAX_HIERARCHY_DEPTH:
            _fail("schematic hierarchy depth budget exceeded")
        instances.append(
            SchematicInstancePath(source_path=source_path, uuid_path="/" + "/".join(uuid_path))
        )
        children = parsed[source_path].sheets
        if len(instances) + len(pending) + len(children) > _MAX_EXPANDED_INSTANCES:
            _fail("schematic hierarchy instance budget exceeded")
        for sheet in reversed(children):
            pending.append((sheet.target_path, (*uuid_path, sheet.sheet_uuid)))
    return tuple(sorted(instances, key=lambda item: (item.uuid_path, item.source_path)))


def derive_schematic_hierarchy(
    root_path: str,
    files: tuple[SchematicSource, ...],
    *,
    limits: CaptureLimits | None = None,
    deadline: float | None = None,
    project_variables: dict[str, str] | None = None,
) -> SchematicHierarchy:
    """Derive bounded private file and instance metadata from already-captured source bytes."""

    started_at = time.monotonic()
    if limits is None:
        limits = CaptureLimits()
    if type(limits) is not CaptureLimits:
        _fail("schematic hierarchy limits are malformed")
    try:
        limits = CaptureLimits(
            limits.max_file_bytes, limits.max_total_bytes, limits.max_capture_seconds
        )
    except (AttributeError, ValueError):
        _fail("schematic hierarchy limits are malformed")
    operation_deadline = _operation_deadline(started_at, limits, deadline)
    _check_deadline(operation_deadline)

    root_path = _canonical_source_path(root_path)
    copied_project_variables = _copy_project_variables(project_variables)
    project_name = PurePosixPath(root_path).stem
    if type(files) is not tuple or not files or len(files) > _MAX_FILES:
        _fail("schematic hierarchy sources are malformed or exceed the file budget")

    sources: dict[str, bytes] = {}
    aliases: dict[str, str] = {}
    total_bytes = 0
    for source in files:
        _check_deadline(operation_deadline)
        if type(source) is not SchematicSource:
            _fail("schematic hierarchy sources are malformed")
        path = _canonical_source_path(source.path)
        content = source.content
        if type(content) is not bytes or not content:
            _fail("schematic hierarchy sources are malformed")
        alias = _portable_alias(path)
        if path in sources or alias in aliases:
            _fail("schematic hierarchy source paths are ambiguous")
        if len(content) > limits.max_file_bytes:
            _fail("schematic hierarchy source byte budget exceeded")
        total_bytes += len(content)
        if total_bytes > limits.max_total_bytes:
            _fail("schematic hierarchy total byte budget exceeded")
        sources[path] = content
        aliases[alias] = path

    if root_path not in sources:
        _fail("schematic hierarchy root source is missing")

    parse_limits = replace(ParseLimits(), max_input_bytes=limits.max_file_bytes)
    source_paths = frozenset(sources)

    source_digests = tuple(
        SchematicSourceDigest(path=path, digest=_sha256(sources[path], operation_deadline))
        for path in sorted(sources)
    )
    parsed: dict[str, _ParsedSource] = {}
    remaining_file_edges = _MAX_FILE_EDGES
    for path in sorted(sources):
        parent = _parse_source(
            path,
            sources[path],
            source_paths=source_paths,
            project_variables=copied_project_variables,
            project_name=project_name,
            parse_limits=parse_limits,
            remaining_file_edges=remaining_file_edges,
            deadline=operation_deadline,
        )
        remaining_file_edges -= len(parent.sheets)
        parsed[path] = parent
    _require_acyclic_and_complete(root_path, parsed, operation_deadline)

    instance_paths = _expand_instances(root_path, parsed, operation_deadline)
    file_edges = tuple(
        sorted(
            (
                SchematicFileEdge(
                    parent_path=parent.path,
                    child_path=sheet.target_path,
                    sheet_name=sheet.name,
                    sheet_uuid=sheet.sheet_uuid,
                )
                for parent in parsed.values()
                for sheet in parent.sheets
            ),
            key=lambda edge: (
                edge.parent_path,
                edge.sheet_uuid,
                edge.child_path,
                edge.sheet_name,
            ),
        )
    )
    _check_deadline(operation_deadline)
    return SchematicHierarchy(
        source_digests=source_digests,
        file_edges=file_edges,
        instance_paths=instance_paths,
    )
