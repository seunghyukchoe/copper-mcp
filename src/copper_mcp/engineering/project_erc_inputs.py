"""Prepare an explicit, private project/library closure for bounded connectivity ERC.

The source project is never changed. A separately hashed execution derivative enables the
connectivity checks and preserves stricter user rules. Simulation and footprint checks belong
to other authorities and are explicitly outside this profile, not successful results.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import NoReturn

from copper_mcp.adapters.sexpr import SExpr, SExprError, children, is_quoted_atom, parse_sexpr
from copper_mcp.board_ir import ParseLimits
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.erc_profile import (
    LEGACY_IGNORED_RULE_KEYS,
    NATIVE_PIN_MAP,
    NATIVE_RULE_SEVERITIES,
    OUTSIDE_CONNECTIVITY_SCOPE,
    PROFILE_ID,
)
from copper_mcp.engineering.project_settings import parse_project_document
from copper_mcp.engineering.schematic_hierarchy import SchematicSource, derive_schematic_hierarchy
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    SchematicProjectCapture,
    _CapturedProjectFile,
    _validate_bindings,
)
from copper_mcp.optimization.contracts import digest_document

LIBRARY_ENVIRONMENT_KEY = "COPPER_MCP_ERC_LIBDIR"
_LIBRARY_DIRECTORY = ".copper-erc-libraries"
_UNBOUND_VARIABLE = re.compile(
    r"\$\{(?:CURRENT_DATE|CURRENT_TIME_HH_MM_SS|CURRENT_TIME_LOCALE|VCSHASH|VCSSHORTHASH)\}"
)


class ProjectErcInputError(ValueError):
    """A fixed refusal, never a private path, model, or source payload."""


def _fail(message: str) -> NoReturn:
    raise ProjectErcInputError(message)


@dataclass(frozen=True, slots=True, repr=False)
class SymbolLibraryInput:
    name: str
    content: bytes
    digest: str

    def __repr__(self) -> str:
        return "<SymbolLibraryInput redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class PreparedProjectErc:
    files: tuple[tuple[str, bytes], ...]
    root_path: str
    library_directory: str
    capture_digest: str
    execution_digest: str
    profile_digest: str
    expected_uuid_paths: frozenset[str]
    rule_changes: tuple[str, ...]
    original_exclusion_count: int
    effective_rule_severities: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "<PreparedProjectErc redacted>"


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _checkpoint(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("project ERC preparation deadline expired")


def _parse_source(payload: bytes, limits: CaptureLimits, deadline: float) -> SExpr:
    parsed: SExpr | None = None
    try:
        parsed = parse_sexpr(
            payload,
            ParseLimits(max_input_bytes=limits.max_file_bytes),
            check_deadline=lambda: _checkpoint(deadline),
        )
    except SExprError:
        pass
    if parsed is None:
        _fail("project ERC source syntax is unsupported")
    pending = [parsed]
    while pending:
        _checkpoint(deadline)
        node = pending.pop()
        if any(isinstance(item, str) and _UNBOUND_VARIABLE.search(item) for item in node.items):
            _fail("project ERC requires captured values for time or version-control variables")
        if node.head == "embedded_files" and len(node.items) > 1:
            _fail("project ERC embedded-file dependencies are not captured")
        pending.extend(item for item in node.items if isinstance(item, SExpr))
    return parsed


def _strict_project(
    project: dict[str, object],
) -> tuple[bytes, tuple[str, ...], int, tuple[tuple[str, str], ...]]:
    pending: list[object] = [project]
    while pending:
        value = pending.pop()
        if isinstance(value, str) and _UNBOUND_VARIABLE.search(value):
            _fail("project ERC requires captured values for time or version-control variables")
        if isinstance(value, dict):
            pending.extend(value.values())
        elif isinstance(value, list):
            pending.extend(value)
    schematic = project.get("schematic", {})
    if not isinstance(schematic, dict):
        _fail("project ERC schematic settings are malformed")
    empty_dependencies: dict[str, object] = {
        "legacy_lib_dir": "",
        "legacy_lib_list": [],
        "page_layout_descr_file": "",
        "top_level_sheets": [],
        "variants": [],
    }
    for key, empty in empty_dependencies.items():
        if key in schematic and (
            type(schematic[key]) is not type(empty) or schematic[key] != empty
        ):
            _fail("project ERC has uncaptured or unsupported project dependencies")
    variables = project.get("text_variables", {})
    if not isinstance(variables, dict) or any(
        name.startswith("COPPER_MCP_ERC_") for name in variables
    ):
        _fail("project ERC reserved variables cannot be supplied by the project")
    erc = project.get("erc", {})
    if not isinstance(erc, dict) or set(erc) - {
        "meta",
        "rule_severities",
        "erc_exclusions",
        "pin_map",
    }:
        _fail("project ERC settings are unsupported")
    for section, maximum in ((project, 1), (schematic, 1), (erc, 0)):
        meta = section.get("meta", {})
        if not isinstance(meta, dict) or (
            "version" in meta
            and (type(meta["version"]) is not int or not 0 <= meta["version"] <= maximum)
        ):
            _fail("project ERC settings schema version is unsupported")
    rules = erc.get("rule_severities", {})
    if not isinstance(rules, dict) or set(rules) - (
        set(NATIVE_RULE_SEVERITIES) | LEGACY_IGNORED_RULE_KEYS
    ):
        _fail("project ERC rule names are unsupported")
    levels = {"ignore": 0, "warning": 1, "error": 2}
    if any(type(value) is not str or value not in levels for value in rules.values()):
        _fail("project ERC rule severities are malformed")
    exclusions = erc.get("erc_exclusions", [])
    if not isinstance(exclusions, list) or len(exclusions) > 4096:
        _fail("project ERC exclusions are malformed")
    if any(
        not (
            (isinstance(row, str) and len(row) <= 4096)
            or (
                isinstance(row, list)
                and len(row) == 2
                and all(isinstance(value, str) and len(value) <= 4096 for value in row)
            )
        )
        for row in exclusions
    ):
        _fail("project ERC exclusions are malformed")
    pin_map = erc.get("pin_map", [list(row) for row in NATIVE_PIN_MAP])
    if (
        not isinstance(pin_map, list)
        or len(pin_map) != 12
        or any(
            not isinstance(row, list)
            or len(row) != 12
            or any(type(value) is not int or not 0 <= value <= 3 for value in row)
            for row in pin_map
        )
    ):
        _fail("project ERC pin conflict matrix is unsupported")
    strict_map = [
        [max(value, floor) for value, floor in zip(row, native, strict=True)]
        for row, native in zip(pin_map, NATIVE_PIN_MAP, strict=True)
    ]
    changes = [
        f"{key}:{rules[key]}->legacy-key-ignored-by-native"
        for key in sorted(LEGACY_IGNORED_RULE_KEYS & rules.keys())
    ]
    strict_rules = {}
    for key, default in NATIVE_RULE_SEVERITIES.items():
        original = rules.get(key, default)
        target = (
            "ignore"
            if key in OUTSIDE_CONNECTIVITY_SCOPE
            else max((original, default, "warning"), key=levels.__getitem__)
        )
        strict_rules[key] = target
        if target != original:
            changes.append(f"{key}:{original}->{target}")
    if strict_map != pin_map:
        changes.append("pin_map:strengthened-to-native-floor")
    if exclusions:
        changes.append("erc_exclusions:checked-without-waivers")
    execution = dict(project)
    execution["erc"] = {
        "meta": {"version": 0},
        "rule_severities": strict_rules,
        "pin_map": strict_map,
        "erc_exclusions": [],
    }
    return (
        json.dumps(execution, sort_keys=True, ensure_ascii=True, allow_nan=False).encode(),
        tuple(sorted(changes)),
        len(exclusions),
        tuple(sorted(strict_rules.items())),
    )


def _symbol_body_digest(symbol: SExpr, deadline: float) -> str:
    """Exact flat body equality, ignoring whitespace/offsets and only the root library name.

    Native 10.0.5 library mismatch ERC calls SCH_ITEM::compare for pins, which does not
    compare their electrical types. Native absence of that finding is therefore insufficient.
    Inheritance and reordered/otherwise different bodies are deliberately not normalized.
    """
    if children(symbol, "extends"):
        _fail("project ERC inherited symbol equivalence is not supported")
    digest = hashlib.sha256()
    pending: list[str | SExpr] = list(reversed(symbol.items[2:]))
    digest.update(len(pending).to_bytes(8, "big"))
    while pending:
        _checkpoint(deadline)
        item = pending.pop()
        if isinstance(item, SExpr):
            digest.update(b"L" + len(item.items).to_bytes(8, "big"))
            pending.extend(reversed(item.items))
        else:
            payload = item.encode("utf-8")
            digest.update(b"Q" if is_quoted_atom(item) else b"A")
            digest.update(len(payload).to_bytes(8, "big"))
            digest.update(payload)
    return digest.hexdigest()


def prepare_project_erc(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    *,
    limits: CaptureLimits,
    deadline: float,
) -> PreparedProjectErc:
    """Revalidate captured bytes and explicitly supplied symbol libraries before execution."""
    if type(limits) is not CaptureLimits or type(deadline) not in (int, float):
        _fail("project ERC preparation bounds are malformed")
    try:
        deadline = float(deadline)
    except OverflowError:
        deadline = float("nan")
    if not math.isfinite(deadline):
        _fail("project ERC preparation bounds are malformed")
    limits = CaptureLimits(
        limits.max_file_bytes, limits.max_total_bytes, limits.max_capture_seconds
    )
    deadline = min(deadline, time.monotonic() + limits.max_capture_seconds)
    _checkpoint(deadline)
    if (
        type(capture) is not SchematicProjectCapture
        or type(libraries) is not tuple
        or len(libraries) > 64
    ):
        _fail("project ERC capture or libraries are malformed")
    if (
        type(capture._files) is not tuple
        or not 1 <= len(capture._files) <= 65
        or any(type(item) is not _CapturedProjectFile for item in capture._files)
    ):
        _fail("project ERC captured files are malformed")
    files = tuple((item.path, item.digest, item.content) for item in capture._files)
    _validate_bindings(
        capture.root_path, tuple(ProjectFileBinding(path, digest) for path, digest, _ in files)
    )
    total = 0
    for _path, digest, content in files:
        _checkpoint(deadline)
        if (
            type(content) is not bytes
            or not content
            or len(content) > limits.max_file_bytes
            or _sha(content) != digest
        ):
            _fail("project ERC capture bytes do not match their binding")
        total += len(content)
        if total > limits.max_total_bytes:
            _fail("project ERC context exceeds its byte budget")
    expected_capture = digest_document(
        "copper-mcp/schematic-project-capture/v1",
        {
            "root_path": capture.root_path,
            "files": [
                {"path": path, "digest": digest, "size": len(content)}
                for path, digest, content in sorted(files)
            ],
        },
    )
    if expected_capture != capture.digest:
        _fail("project ERC capture identity does not match")
    context = {path: content for path, _, content in files}
    project_path = str(PurePosixPath(capture.root_path).with_suffix(".kicad_pro"))
    if capture.project_path != project_path:
        _fail("project ERC project binding does not match the root")
    project = parse_project_document(context[project_path], deadline=deadline)
    schematic_sources = tuple(
        SchematicSource(path, content)
        for path, content in context.items()
        if path.endswith(".kicad_sch")
    )
    hierarchy = derive_schematic_hierarchy(
        capture.root_path,
        schematic_sources,
        limits=limits,
        deadline=deadline,
        project_variables=project.get("text_variables", {}),
    )
    if hierarchy != capture.hierarchy:
        _fail("project ERC hierarchy does not match captured source")
    required: dict[str, set[str]] = {}
    cached_bodies: dict[str, set[str]] = {}
    for source in schematic_sources:
        parsed = _parse_source(source.content, limits, deadline)
        caches = children(parsed, "lib_symbols")
        if len(caches) != 1:
            _fail("project ERC requires one explicit symbol cache per schematic")
        cache: dict[str, SExpr] = {}
        for symbol in children(caches[0], "symbol"):
            if (
                len(symbol.items) < 2
                or not isinstance(symbol.items[1], str)
                or symbol.items[1] in cache
            ):
                _fail("project ERC cached symbol identities are ambiguous")
            cache[symbol.items[1]] = symbol
        for symbol in children(parsed, "symbol"):
            if children(symbol, "lib_name"):
                _fail("project ERC cached-name override is not supported by this profile")
            ids = children(symbol, "lib_id")
            if len(ids) != 1 or len(ids[0].items) != 2 or not isinstance(ids[0].items[1], str):
                _fail("project ERC symbol library reference is malformed")
            parts = ids[0].items[1].split(":")
            if len(parts) != 2 or not all(parts):
                _fail("project ERC symbol library reference is unsupported")
            required.setdefault(parts[0], set()).add(parts[1])
            library_id = ids[0].items[1]
            if library_id not in cache:
                _fail("project ERC placed symbol has no captured library body")
            cached_bodies.setdefault(library_id, set()).add(
                _symbol_body_digest(cache[library_id], deadline)
            )
    supplied: dict[str, bytes] = {}
    aliases: set[str] = set()
    for library in libraries:
        _checkpoint(deadline)
        if type(library) is not SymbolLibraryInput:
            _fail("project ERC library is malformed")
        name, content, digest = library.name, library.content, library.digest
        if (
            type(name) is not str
            or not 1 <= len(name) <= 128
            or any(
                character in "\\/:$" or unicodedata.category(character) in {"Cc", "Cs"}
                for character in name
            )
            or type(content) is not bytes
            or not content
            or len(content) > limits.max_file_bytes
            or _sha(content) != digest
        ):
            _fail("project ERC library binding is malformed")
        alias = unicodedata.normalize("NFC", name.casefold())
        if alias in aliases:
            _fail("project ERC library names are ambiguous")
        aliases.add(alias)
        total += len(content)
        if total > limits.max_total_bytes:
            _fail("project ERC context exceeds its byte budget")
        parsed = _parse_source(content, limits, deadline)
        if parsed.head != "kicad_symbol_lib":
            _fail("project ERC library format is unsupported")
        symbols = children(parsed, "symbol")
        names = [
            item.items[1]
            for item in symbols
            if len(item.items) > 1 and isinstance(item.items[1], str)
        ]
        symbol_names = set(names)
        if (
            len(names) != len(symbols)
            or len(symbol_names) != len(names)
            or not required.get(name, set()) <= symbol_names
        ):
            _fail("project ERC library lacks its referenced symbols")
        parents: dict[str, str | None] = {}
        for symbol, symbol_name in zip(symbols, names, strict=True):
            _checkpoint(deadline)
            inherited = children(symbol, "extends")
            if len(inherited) > 1 or (
                inherited
                and (
                    len(inherited[0].items) != 2
                    or not isinstance(inherited[0].items[1], str)
                    or inherited[0].items[1] not in symbol_names
                )
            ):
                _fail("project ERC symbol inheritance is not contained in its library")
            parent_name = inherited[0].items[1] if inherited else None
            if parent_name is not None and not isinstance(parent_name, str):
                _fail("project ERC symbol inheritance is malformed")
            parents[symbol_name] = parent_name
            if symbol_name in required.get(name, set()) and cached_bodies[
                f"{name}:{symbol_name}"
            ] != {_symbol_body_digest(symbol, deadline)}:
                _fail("project ERC cached and supplied symbol bodies are not proven equivalent")
        checked: set[str] = set()
        for symbol_name in parents:
            chain: set[str] = set()
            current: str | None = symbol_name
            while current is not None and current not in checked:
                _checkpoint(deadline)
                if current in chain:
                    _fail("project ERC symbol inheritance contains a cycle")
                chain.add(current)
                current = parents[current]
            checked.update(chain)
        supplied[name] = content
    if set(supplied) != set(required):
        _fail("project ERC symbol library closure is incomplete or contains extras")
    parent = PurePosixPath(capture.root_path).parent
    library_directory = (parent / _LIBRARY_DIRECTORY).as_posix()
    if any(
        path == library_directory or path.startswith(library_directory + "/") for path in context
    ):
        _fail("project ERC reserved snapshot paths collide with captured source")
    table_rows = []
    for index, (name, content) in enumerate(sorted(supplied.items())):
        filename = f"lib-{index:03d}.kicad_sym"
        context[f"{library_directory}/{filename}"] = content
        table_rows.append(
            f'(lib (name {json.dumps(name, ensure_ascii=False)}) (type "KiCad") '
            f'(uri "${{{LIBRARY_ENVIRONMENT_KEY}}}/{filename}") (options "") (descr ""))'
        )
    context[(parent / "sym-lib-table").as_posix()] = (
        "(sym_lib_table (version 7)" + "".join(table_rows) + ")"
    ).encode()
    context[project_path], changes, exclusions, effective_rules = _strict_project(project)
    if (
        any(len(content) > limits.max_file_bytes for content in context.values())
        or sum(map(len, context.values())) > limits.max_total_bytes
    ):
        _fail("project ERC execution context exceeds its byte budget")
    execution_digest = digest_document(
        "copper-mcp/project-erc-execution-context/v1",
        {
            "root_path": capture.root_path,
            "files": [
                {"path": path, "digest": _sha(content)} for path, content in sorted(context.items())
            ],
        },
    )
    profile_digest = digest_document(
        PROFILE_ID,
        {
            "rules": NATIVE_RULE_SEVERITIES,
            "pin_map": NATIVE_PIN_MAP,
            "outside_scope": sorted(OUTSIDE_CONNECTIVITY_SCOPE),
            "symbol_binding": "exact-ordered-flat-body/v1",
            "legacy_ignored_rules": sorted(LEGACY_IGNORED_RULE_KEYS),
        },
    )
    _checkpoint(deadline)
    return PreparedProjectErc(
        tuple(sorted(context.items())),
        capture.root_path,
        library_directory,
        capture.digest,
        execution_digest,
        profile_digest,
        frozenset(item.uuid_path for item in hierarchy.instance_paths),
        changes,
        exclusions,
        effective_rules,
    )
