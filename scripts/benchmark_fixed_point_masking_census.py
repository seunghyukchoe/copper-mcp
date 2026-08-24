#!/usr/bin/env python3
"""Measure the closed, read-only fixed-point conversion masking experiment.

The manifest is the authority for the exact 13-board cohort.  Board bytes are captured once,
then only an in-memory CST splice is used to remove a specifically located unsupported
expression.  This module emits aggregate evidence only; it is not a repair or conversion
fallback.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.cst import CstError, Splice, span, splice_source
from copper_mcp.adapters.sexpr import SExpr, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.route_preview import parse_route_preview_request
from copper_mcp.security import read_workspace_file

SCHEMA: Final = "copper-mcp/fixed-point-masking-census/v1"
EXPECTED_TOTAL: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_PRIVATE: Final = 3
MAX_MASK_PASSES: Final = 16
MAX_SOURCE_BYTES: Final = 64 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 64 * 1024
PREDECLARED_COHORT_FINGERPRINT: Final = "sha256:bfec8210d6d4eb746ffdbfb3b70309ce"
EDGE_CURVE_MESSAGE: Final = "Edge.Cuts outline arcs, circles and curves are unsupported"
COPPER_TEXT_MESSAGE: Final = (
    "copper text has no envelope derivable from the board and is unsupported"
)
ROOT_DIMENSION_MESSAGE: Final = "root dimension objects are unsupported"
SETUP_SEMANTIC_MESSAGE: Final = "expression contains an unsupported semantic field"
ROOT_SEMANTIC_MESSAGE: Final = "root expression contains an unsupported semantic construct"
LAYER_ARITY_MESSAGE: Final = "layer field has an invalid arity"
EXPLICIT_LAYER_MESSAGE: Final = "graphic without one explicit layer is unsupported"
COPPER_LAYER_KIND_MESSAGE: Final = "copper layer kind is unsupported"
DISJOINT_OUTLINE_MESSAGE: Final = "multiple disjoint Edge.Cuts loops are unsupported"
COURTYARD_TOPOLOGY_MESSAGE: Final = (
    "courtyard edges must be non-zero and axis-aligned or 45-degree chamfers"
)
EDGE_CURVE_HEADS: Final = frozenset({"gr_arc", "gr_bezier", "gr_circle", "gr_curve"})
COPPER_TEXT_HEADS: Final = frozenset({"gr_text", "gr_text_box"})


def _convert(source: bytes, board: str, settings: Settings) -> Any:
    request = parse_route_preview_request(
        {
            "board": board,
            "net": "unused",
            "layer": "F.Cu",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
        }
    )
    return parse_kicad_bytes(source, request.profile(), parse_limits_for(settings))


TERMINAL: Final = frozenset({"converted", "bounded", "unmaskable"})


@dataclass(frozen=True, slots=True)
class CorpusEntry:
    identity: str
    visibility: str
    relative: str
    digest: str


@dataclass(frozen=True, slots=True)
class Snapshot:
    entry: CorpusEntry
    source: bytes


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _validate_digest(value: object) -> str:
    if not isinstance(value, str) or len(value) != 71 or not value.startswith("sha256:"):
        raise _fixed_error("manifest digest is malformed")
    if any(char not in "0123456789abcdef" for char in value[7:]):
        raise _fixed_error("manifest digest is malformed")
    return value


def _manifest_fingerprint(entries: list[CorpusEntry]) -> str:
    material = "".join(
        f"{entry.identity}:{entry.visibility}:{entry.relative}:{entry.digest}\n"
        for entry in entries
    ).encode()
    return "sha256:" + hashlib.sha256(material).hexdigest()[:32]


def load_manifest(path: Path) -> tuple[list[CorpusEntry], str]:
    """Load and validate the closed external manifest without publishing its paths."""

    if path.is_symlink() or path.suffix != ".json":
        raise _fixed_error("manifest must be a regular JSON file")
    try:
        document = json.loads(
            read_workspace_file(
                path.parent,
                path.name,
                allowed_suffixes={".json"},
                max_bytes=MAX_MANIFEST_BYTES,
            ).content.decode("utf-8")
        )
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise _fixed_error("manifest is unreadable or malformed") from error
    if (
        type(document) is not dict
        or document.get("schema") != SCHEMA
        or set(document) != {"schema", "entries", "fingerprint"}
        or type(document["entries"]) is not list
        or len(document["entries"]) != EXPECTED_TOTAL
    ):
        raise _fixed_error("manifest schema or cohort count is invalid")
    entries: list[CorpusEntry] = []
    identities: set[str] = set()
    relatives: set[str] = set()
    for raw in document["entries"]:
        if type(raw) is not dict or set(raw) != {"id", "visibility", "path", "sha256"}:
            raise _fixed_error("manifest entry schema is invalid")
        identity, visibility, relative = raw["id"], raw["visibility"], raw["path"]
        if (
            type(identity) is not str
            or not identity
            or identity in identities
            or visibility not in {"public", "private"}
            or type(relative) is not str
            or not relative
        ):
            raise _fixed_error("manifest entry identity or visibility is invalid")
        candidate = Path(relative)
        if candidate.is_absolute() or ".." in candidate.parts or candidate.suffix != ".kicad_pcb":
            raise _fixed_error("manifest entry path is unsafe")
        if relative in relatives:
            raise _fixed_error("manifest contains duplicate board paths")
        identities.add(identity)
        relatives.add(relative)
        entries.append(CorpusEntry(identity, visibility, relative, _validate_digest(raw["sha256"])))
    if (
        sum(entry.visibility == "public" for entry in entries) != EXPECTED_PUBLIC
        or sum(entry.visibility == "private" for entry in entries) != EXPECTED_PRIVATE
        or document["fingerprint"] != _manifest_fingerprint(entries)
    ):
        raise _fixed_error("manifest cohort or fingerprint is invalid")
    return entries, document["fingerprint"]


def capture_snapshots(
    corpus: Path, entries: list[CorpusEntry], *, max_bytes: int
) -> list[Snapshot]:
    if type(max_bytes) is not int or max_bytes < 1 or max_bytes > MAX_SOURCE_BYTES:
        raise _fixed_error("source byte budget is invalid")
    snapshots: list[Snapshot] = []
    for entry in entries:
        try:
            source = read_workspace_file(
                corpus,
                entry.relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=max_bytes,
            ).content
        except Exception as error:
            raise _fixed_error("manifest source is unavailable or unsafe") from error
        if hashlib.sha256(source).hexdigest() != entry.digest[7:]:
            raise _fixed_error("manifest source digest does not match")
        snapshots.append(Snapshot(entry, source))
    return snapshots


def _node_at_offset(node: SExpr, offset: int, text: str) -> SExpr | None:
    try:
        start, end = span(node, text)
    except CstError:
        return None
    if not start <= offset < end:
        return None
    children = [item for item in node.items if isinstance(item, SExpr)]
    for child in children:
        found = _node_at_offset(child, offset, text)
        if found is not None:
            return found
    return node


def _atom(node: SExpr, head: str) -> str | None:
    matches = [item for item in node.items[1:] if isinstance(item, SExpr) and item.head == head]
    if len(matches) != 1 or len(matches[0].items) != 2:
        return None
    value = matches[0].items[1]
    return value if isinstance(value, str) else None


def _direct_root_masks(root: SExpr, locator: str, message: str) -> tuple[SExpr, ...] | None:
    children = [item for item in root.items[1:] if isinstance(item, SExpr)]
    if locator.startswith("kicad_pcb.child[") and locator.endswith("]"):
        try:
            index = int(locator[len("kicad_pcb.child[") : -1])
        except ValueError:
            return None
        if not 0 <= index < len(root.items[1:]):
            return None
        candidate = root.items[1:][index]
        if not isinstance(candidate, SExpr) or candidate.head != "dimension":
            return None
        if message != ROOT_DIMENSION_MESSAGE:
            return None
        dimensions = tuple(item for item in children if item.head == "dimension")
        return dimensions or None
    if locator != "kicad_pcb.graphic":
        return None
    if message == EDGE_CURVE_MESSAGE:
        candidates = [
            item
            for item in children
            if item.head in EDGE_CURVE_HEADS and _atom(item, "layer") == "Edge.Cuts"
        ]
        return tuple(candidates) or None
    if message == COPPER_TEXT_MESSAGE:
        candidates = [
            item
            for item in children
            if item.head in COPPER_TEXT_HEADS
            and (
                (layer := _atom(item, "layer")) is not None
                and (layer == "F&B.Cu" or layer.endswith(".Cu"))
            )
        ]
        return tuple(candidates) or None
    return None


def _mask_first_blocker(source: bytes, conversion: Any, settings: Settings) -> bytes | None:
    diagnostics = getattr(conversion, "diagnostics", ())
    if not diagnostics:
        return None
    diagnostic = diagnostics[0]
    code = getattr(diagnostic, "code", None)
    if code != "unsupported.construct":
        return None
    locator = getattr(diagnostic, "source_locator", "")
    message = getattr(diagnostic, "message", None)
    if not isinstance(locator, str) or not isinstance(message, str):
        return None
    try:
        source.decode("utf-8", errors="strict")
        root = parse_sexpr(source, parse_limits_for(settings))
        nodes = _direct_root_masks(root, locator, message)
        if nodes is None or any(node is root for node in nodes):
            return None
        text = source.decode("utf-8", errors="strict")
        splices = [Splice(*span(node, text), "") for node in nodes]
        return splice_source(source, splices)
    except (CstError, UnicodeError, ValueError, TypeError):
        return None


def _terminal_blocker(result: Any) -> str:
    diagnostics = getattr(result, "diagnostics", ())
    if not diagnostics:
        return "other"
    diagnostic = diagnostics[0]
    code = getattr(diagnostic, "code", None)
    message = getattr(diagnostic, "message", None)
    locator = getattr(diagnostic, "source_locator", None)
    if (
        code == "unsupported.construct"
        and message == SETUP_SEMANTIC_MESSAGE
        and locator == "kicad_pcb.setup"
    ):
        return "setup_semantics"
    if (
        code == "unsupported.construct"
        and message == ROOT_SEMANTIC_MESSAGE
        and isinstance(locator, str)
        and locator.startswith("kicad_pcb.child[")
    ):
        return "root_semantic_construct"
    if (
        code == "syntax.invalid"
        and message == LAYER_ARITY_MESSAGE
        and locator == "kicad_pcb.graphic"
    ):
        return "graphic_layer_arity"
    if (
        code == "unsupported.construct"
        and message == EXPLICIT_LAYER_MESSAGE
        and isinstance(locator, str)
        and locator.startswith("kicad_pcb.footprint[")
        and locator.endswith(".graphic")
    ):
        return "footprint_graphic_layer"
    if (
        code == "unsupported.construct"
        and message == COPPER_LAYER_KIND_MESSAGE
        and locator == "kicad_pcb.layers"
    ):
        return "copper_layer_kind"
    if (
        code == "unsupported.topology"
        and message == DISJOINT_OUTLINE_MESSAGE
        and locator == "kicad_pcb"
    ):
        return "disjoint_outline_topology"
    if (
        code == "unsupported.topology"
        and message == COURTYARD_TOPOLOGY_MESSAGE
        and isinstance(locator, str)
        and ".courtyard[" in locator
    ):
        return "courtyard_topology"
    return "other"


def _classify_source_detail(
    source: bytes,
    settings: Settings,
    *,
    converter: Callable[[bytes, Settings], Any] | None = None,
) -> tuple[int, str, str]:
    """Return depth, terminal, and a closed blocker class without diagnostic text."""

    convert = converter or (lambda data, opts: _convert(data, "frozen-board", opts))
    current = source
    seen = {source}
    for depth in range(MAX_MASK_PASSES + 1):
        try:
            result = convert(current, settings)
        except Exception:
            return depth, "unmaskable", "measurement_error"
        if getattr(result, "snapshot", None) is not None and not getattr(result, "diagnostics", ()):
            return depth, "converted", "none"
        if depth == MAX_MASK_PASSES:
            return depth, "bounded", "mask_pass_budget"
        replacement = _mask_first_blocker(current, result, settings)
        if replacement is None:
            return depth, "unmaskable", _terminal_blocker(result)
        if len(replacement) >= len(current) or replacement in seen:
            return depth, "unmaskable", "no_progress"
        seen.add(replacement)
        current = replacement
    return MAX_MASK_PASSES, "bounded", "mask_pass_budget"


def classify_source(
    source: bytes,
    settings: Settings,
    *,
    converter: Callable[[bytes, Settings], Any] | None = None,
) -> tuple[int, str]:
    """Return mask depth and a fixed terminal category; never returns diagnostic text."""

    depth, terminal, _blocker = _classify_source_detail(source, settings, converter=converter)
    return depth, terminal


def measure(
    corpus: Path,
    manifest: Path,
    settings: Settings,
    *,
    expected_fingerprint: str | None = None,
    converter: Callable[[bytes, Settings], Any] | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("fixed-point census is read-only")
    entries, fingerprint = load_manifest(manifest)
    expected = (
        PREDECLARED_COHORT_FINGERPRINT if expected_fingerprint is None else expected_fingerprint
    )
    if not isinstance(expected, str) or expected != fingerprint or len(expected) != 39:
        raise _fixed_error("predeclared cohort fingerprint does not match")
    snapshots = capture_snapshots(corpus, entries, max_bytes=settings.max_board_bytes)
    aggregates: dict[str, dict[str, Counter[str]]] = {
        "overall": {
            "gate_stack_depth": Counter(),
            "terminal": Counter(),
            "terminal_blocker": Counter(),
        },
        "public": {
            "gate_stack_depth": Counter(),
            "terminal": Counter(),
            "terminal_blocker": Counter(),
        },
        "private": {
            "gate_stack_depth": Counter(),
            "terminal": Counter(),
            "terminal_blocker": Counter(),
        },
    }
    for item in snapshots:
        depth, terminal, blocker = _classify_source_detail(
            item.source, settings, converter=converter
        )
        for scope in ("overall", item.entry.visibility):
            aggregates[scope]["gate_stack_depth"][str(depth)] += 1
            aggregates[scope]["terminal"][terminal] += 1
            aggregates[scope]["terminal_blocker"][blocker] += 1
    for item in snapshots:
        try:
            current = read_workspace_file(
                corpus,
                item.entry.relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
        except Exception as error:
            raise _fixed_error("source changed or became unavailable") from error
        if current != item.source:
            raise _fixed_error("source changed during measurement")

    def closed(scope: str) -> dict[str, dict[str, int]]:
        return {
            "gate_stack_depth": dict(
                sorted(
                    aggregates[scope]["gate_stack_depth"].items(),
                    key=lambda pair: int(pair[0]),
                )
            ),
            "terminal": dict(sorted(aggregates[scope]["terminal"].items())),
            "terminal_blocker": dict(sorted(aggregates[scope]["terminal_blocker"].items())),
        }

    result = {
        "schema": SCHEMA,
        "cohort_count": len(snapshots),
        "public_count": EXPECTED_PUBLIC,
        "private_count": EXPECTED_PRIVATE,
        "cohort_fingerprint": fingerprint,
        "aggregates": {scope: closed(scope) for scope in ("overall", "public", "private")},
        "source_hashes_unchanged": True,
    }
    if sum(result["aggregates"]["overall"]["terminal"].values()) != EXPECTED_TOTAL:
        raise _fixed_error("overall aggregate denominator is invalid")
    if sum(result["aggregates"]["public"]["terminal"].values()) != EXPECTED_PUBLIC:
        raise _fixed_error("public aggregate denominator is invalid")
    if sum(result["aggregates"]["private"]["terminal"].values()) != EXPECTED_PRIVATE:
        raise _fixed_error("private aggregate denominator is invalid")
    return result


def _git_state(root: Path) -> tuple[str, bool]:
    git = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(  # noqa: S603
            [git, "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    return commit, dirty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    corpus = args.corpus.expanduser().resolve(strict=True)
    manifest = args.manifest.expanduser()
    if not manifest.is_file() or manifest.is_symlink():
        raise SystemExit("manifest must be a regular file")
    output = args.output.expanduser().resolve()
    if output == corpus or corpus in output.parents:
        raise SystemExit("output must be outside corpus")
    root = Path(__file__).resolve().parents[1]
    runner = Path(__file__).resolve()
    commit, dirty = _git_state(root)
    if dirty:
        raise SystemExit("measurement worktree must start clean")
    runner_bytes = runner.read_bytes()
    result = measure(
        corpus,
        manifest,
        Settings(workspace=corpus),
    )
    final_commit, final_dirty = _git_state(root)
    if final_commit != commit or final_dirty or runner.read_bytes() != runner_bytes:
        raise SystemExit("measurement inputs changed during run")
    result.update(
        {
            "commit": commit,
            "dirty": False,
            "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "runner_digest": "sha256:" + hashlib.sha256(runner_bytes).hexdigest(),
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
            },
            "configuration": {
                "max_manifest_bytes": MAX_MANIFEST_BYTES,
                "max_mask_passes": MAX_MASK_PASSES,
                "max_source_bytes": MAX_SOURCE_BYTES,
                "mask_classes": ["edge_cuts_curves", "copper_text", "root_dimensions"],
                "operation": "read_only_in_memory_counterfactual",
            },
            "committed_board_bytes": 0,
            "not_claimed": [
                "no product support for any masked construct",
                "no converted board, route, DRC, fabrication, or hardware result",
                "no board write, apply authority, editor mutation, or committed private input",
            ],
        }
    )
    canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
    result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, sort_keys=True, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
