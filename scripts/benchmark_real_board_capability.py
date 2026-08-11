#!/usr/bin/env python3
"""Measure what CopperMCP's downstream surfaces actually do on real KiCad boards.

Board IR conversion is step one and is already measured (B-094, issue #116). This runner asks the
next question: once a real board converts, what do the surfaces built on top of it *do*? It
exercises five read-only surfaces per board -- Board IR conversion, authoritative KiCad DRC,
Circuit Scene observation, placement preview, and route preview -- at their default settings, and
records the outcome, the typed refusal code, and the wall clock of every call.

The corpus is supplied with ``--corpus``. It is deliberately not committed and not redistributable:
the boards this was run against are a private working design tree. Nothing in the emitted artifact
carries board content -- no net names, no component references, no coordinates. What it carries is
counts, typed status and refusal codes, timings, and the path stems already published in issue
#116.

Nothing here writes to the corpus. No apply flag and no live-IPC flag is set; ``Settings`` is
constructed directly so an ambient ``COPPER_MCP_ALLOW_APPLY`` in the environment cannot reach it.
Appliability is measured by calling the apply path's own pure identity predicate on the converted
snapshot, never by attempting an apply.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import statistics
import subprocess
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp import tools
from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.adapters.kicad_route_patch import (
    KiCadRoutePatchError,
    _require_native_geometry_identities,
)
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement.contracts import parse_placement_intent
from copper_mcp.placement_preview import preview_placement as preview_placement_service
from copper_mcp.route_preview import parse_route_preview_request
from copper_mcp.routing.astar import OFF_GRID_MESSAGE_LEAD

ROOT = Path(__file__).resolve().parents[1]

#: One ordinary net class, identical for every board and every surface, so a difference between
#: two boards is a difference between the boards and not between two requests.
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}

#: A box large enough to contain any board in the corpus, so the "whole board" scene request is
#: the same request everywhere and the only thing that varies is what the board contains.
WHOLE_BOARD_REGION = {
    "min_x_nm": -1_000_000_000,
    "min_y_nm": -1_000_000_000,
    "max_x_nm": 1_000_000_000,
    "max_y_nm": 1_000_000_000,
}

#: The bounded region: 5 mm around the lexicographically first footprint reference. Chosen from
#: the reference alone, never from the observed result, and stated as a radius so no coordinate
#: from the board reaches the artifact.
BOUNDED_REGION_RADIUS_NM = 5_000_000

#: Derived stems of another board in the same directory. Measuring them would count one design
#: several times.
DERIVED_STEMS = ("routed-source", "best-board", "-placed")

#: Route preview is per-net and each call re-reads and re-converts the whole board, so the largest
#: board would otherwise dominate the run. Nets are taken in Board IR canonical order, decided
#: before any routing, never by retrying.
MAX_NETS_PER_BOARD = 40

#: Placement preview accepts up to ``max_placement_subjects`` footprints per request. Batching is
#: an efficiency measure only: any batch that does not preview cleanly is re-run one subject at a
#: time, so every footprint still receives its own verdict.
PLACEMENT_BATCH = 64

#: How many single-subject placement previews to time per board. Per-call latency is the number a
#: caller feels; the batch number is not a substitute for it.
PLACEMENT_LATENCY_SAMPLES = 3


def _boards(corpus: Path) -> list[Path]:
    """Every distinct board in the corpus, excluding editor history and derived stems."""

    return sorted(
        path
        for path in corpus.rglob("*.kicad_pcb")
        if ".history" not in path.parts and not any(stem in path.name for stem in DERIVED_STEMS)
    )


def _elapsed_ms(started: float) -> float:
    return round((time.monotonic() - started) * 1000.0, 1)


def _convert(source: bytes, board: str, settings: Settings) -> Any:
    """Convert one board through the same adapter every surface uses."""

    request = parse_route_preview_request(
        {"board": board, "net": "unused", "layer": "F.Cu", "constraints": dict(CONSTRAINTS)}
    )
    return parse_kicad_bytes(source, request.profile(), parse_limits_for(settings))


def _identity_mix(content: Any) -> dict[str, dict[str, int]]:
    """Native versus revision-derived identity, per object kind.

    A derived identity is what the converter falls back to when a KiCad UUID names more than one
    object (see D-158). It is a first-class Board IR object, but every source-preserving write-back
    path refuses a snapshot containing one, so the mix decides what a preview can ever become.
    """

    kinds = {
        "outline": content.outline,
        "footprints": content.footprints,
        "pads": content.pads,
        "vias": content.vias,
        "segments": content.segments,
        "arcs": content.arcs,
        "zones": content.zones,
        "keepouts": content.keepouts,
    }
    mix: dict[str, dict[str, int]] = {}
    for name, items in kinds.items():
        counts = Counter("derived" if ":derived:" in item.id else "native" for item in items)
        if counts:
            mix[name] = dict(sorted(counts.items()))
    return mix


def _measure_board_ir(board: str, settings: Settings, source: bytes) -> dict[str, Any]:
    started = time.monotonic()
    summary = tools.inspect_board_ir({"board": board, "constraints": dict(CONSTRAINTS)}, settings)
    elapsed_ms = _elapsed_ms(started)
    conversion = _convert(source, board, settings)
    record: dict[str, Any] = {
        "elapsed_ms": elapsed_ms,
        "supported": bool(summary["supported"]),
        "object_counts": dict(summary["object_counts"]),
        "conversion_diagnostic_counts": dict(summary["conversion_diagnostic_counts"]),
        "source_bytes": len(source),
    }
    if conversion.diagnostics:
        # Every Board IR diagnostic message is a fixed string chosen by ``copper_mcp.board_ir``;
        # anything derived from the board travels in the locator, which is dropped here.
        record["first_diagnostic"] = {
            "code": conversion.diagnostics[0].code,
            "message": conversion.diagnostics[0].message,
        }
    if conversion.snapshot is not None:
        record["identity_mix"] = _identity_mix(conversion.snapshot.content)
    return record


def _measure_drc(board: str, settings: Settings) -> dict[str, Any]:
    started = time.monotonic()
    try:
        summary = tools.run_board_drc(board, settings)
    except Exception as error:
        return {
            "elapsed_ms": _elapsed_ms(started),
            "outcome": "refused",
            "refusal": f"{type(error).__name__}: {error}",
        }
    return {
        "elapsed_ms": _elapsed_ms(started),
        "outcome": "reported",
        "kicad_version": summary["kicad_version"],
        "passed": summary["passed"],
        "clean": summary["clean"],
        "error_count": summary["error_count"],
        "warning_count": summary["warning_count"],
        "unconnected_count": summary["unconnected_count"],
        "violation_type_counts": dict(summary["violation_type_counts"]),
    }


def _scene_shape(document: dict[str, Any]) -> dict[str, Any]:
    return {
        "supported": bool(document["supported"]),
        "truncation": dict(document["truncation"]),
        "ref_stability": dict(document["ref_stability"]),
        "returned": {
            partition: {kind: len(items) for kind, items in document[partition].items()}
            for partition in ("static", "mutable")
        },
    }


def _measure_scene(
    board: str, settings: Settings, first_footprint_ref: str | None
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    started = time.monotonic()
    try:
        document = tools.observe_board_scene(
            {
                "board": board,
                "constraints": dict(CONSTRAINTS),
                "region": dict(WHOLE_BOARD_REGION),
            },
            settings,
        )
        record["whole_board"] = {"elapsed_ms": _elapsed_ms(started), **_scene_shape(document)}
    except Exception as error:
        record["whole_board"] = {
            "elapsed_ms": _elapsed_ms(started),
            "outcome": "refused",
            "refusal": f"{type(error).__name__}: {error}",
        }
    if first_footprint_ref is None:
        record["bounded"] = {"outcome": "not_attempted", "reason": "board has no footprint"}
        return record
    started = time.monotonic()
    try:
        document = tools.observe_board_scene(
            {
                "board": board,
                "constraints": dict(CONSTRAINTS),
                "region": {
                    "around_ref_id": first_footprint_ref,
                    "radius_nm": BOUNDED_REGION_RADIUS_NM,
                },
            },
            settings,
        )
        record["bounded"] = {
            "elapsed_ms": _elapsed_ms(started),
            "radius_nm": BOUNDED_REGION_RADIUS_NM,
            **_scene_shape(document),
        }
    except Exception as error:
        record["bounded"] = {
            "elapsed_ms": _elapsed_ms(started),
            "radius_nm": BOUNDED_REGION_RADIUS_NM,
            "outcome": "refused",
            "refusal": f"{type(error).__name__}: {error}",
        }
    return record


def _placement_verdict(document: dict[str, Any]) -> str:
    diagnostic = document.get("diagnostic") or {}
    code = diagnostic.get("code")
    return document["status"] if code is None else f"{document['status']}/{code}"


def _preview_placement(board: str, subjects: list[str], settings: Settings) -> dict[str, Any]:
    try:
        return tools.preview_placement(
            {"board": board, "constraints": dict(CONSTRAINTS), "subjects": subjects}, settings
        )
    except Exception as error:
        return {
            "status": "raised",
            "diagnostic": {"code": type(error).__name__, "message": str(error)},
        }


def _measure_placement(
    board: str, settings: Settings, footprint_refs: list[str], conversion: Any
) -> dict[str, Any]:
    verdicts: Counter[str] = Counter()
    messages: dict[str, str] = {}
    started = time.monotonic()
    for offset in range(0, len(footprint_refs), PLACEMENT_BATCH):
        batch = footprint_refs[offset : offset + PLACEMENT_BATCH]
        document = _preview_placement(board, batch, settings)
        if document["status"] == "previewed":
            verdicts["previewed"] += len(batch)
            continue
        # A batch verdict cannot be attributed to a footprint, so fall back to one call per
        # subject rather than blaming the whole batch for one refusal.
        for ref in batch:
            single = _preview_placement(board, [ref], settings)
            verdict = _placement_verdict(single)
            verdicts[verdict] += 1
            diagnostic = single.get("diagnostic") or {}
            if verdict not in messages and diagnostic.get("message"):
                messages[verdict] = str(diagnostic["message"])
    record: dict[str, Any] = {
        "subjects": len(footprint_refs),
        "verdicts": dict(sorted(verdicts.items())),
        "refusal_messages": dict(sorted(messages.items())),
        "elapsed_ms": _elapsed_ms(started),
    }

    latencies: list[float] = []
    for ref in footprint_refs[:PLACEMENT_LATENCY_SAMPLES]:
        call_started = time.monotonic()
        _preview_placement(board, [ref], settings)
        latencies.append(_elapsed_ms(call_started))
    if latencies:
        record["single_subject_latency_ms"] = {
            "samples": len(latencies),
            "median": round(statistics.median(latencies), 1),
            "max": round(max(latencies), 1),
        }

    # What a clean preview could ever become. ``render_kicad_placement_candidate_board`` is the
    # exact pure replay the apply-token mint runs; calling it here writes nothing.
    record["source_preserving_render"] = _render_gate(board, settings, footprint_refs, conversion)
    return record


def _render_gate(
    board: str, settings: Settings, footprint_refs: list[str], conversion: Any
) -> dict[str, Any]:
    if not footprint_refs or conversion.snapshot is None:
        return {"outcome": "not_attempted"}
    request = {
        "board": board,
        "constraints": dict(CONSTRAINTS),
        "subjects": footprint_refs[:1],
    }
    result = preview_placement_service(request, settings)
    if result.status != "previewed" or result.candidate is None:
        return {"outcome": "no_candidate"}
    intent = parse_placement_intent(request)
    source = (settings.workspace / board).read_bytes()
    try:
        render_kicad_placement_candidate_board(
            source,
            conversion.snapshot,
            result.candidate,
            intent.profile(),
            limits=parse_limits_for(settings),
        )
    except KiCadPlacementPatchError as error:
        return {"outcome": "refused", "refusal": str(error)}
    return {"outcome": "rendered"}


def _route_appliable(conversion: Any) -> dict[str, Any]:
    if conversion.snapshot is None:
        return {"outcome": "not_attempted"}
    try:
        _require_native_geometry_identities(conversion.snapshot)
    except KiCadRoutePatchError as error:
        return {"outcome": "refused", "refusal": str(error)}
    return {"outcome": "appliable"}


def _redacted_message(diagnostic: dict[str, Any]) -> str:
    """One refusal message with every board-derived value removed.

    Most routing refusals are fixed strings chosen by ``copper_mcp.routing``. ``off_grid`` is
    not: since ADR-0093 it interpolates the pad's miss distance and the largest lattice step
    that represents the pad pair, which are per-request geometry a *caller* is entitled to and
    this artifact is not -- it is committed to a public repository while the corpus is a private
    design tree. The lead sentence is imported rather than retyped, so a reworded message cannot
    silently start carrying geometry into the artifact.
    """

    if diagnostic.get("off_grid") is not None:
        return OFF_GRID_MESSAGE_LEAD
    return str(diagnostic["message"])


def _measure_route(
    board: str, settings: Settings, net_names: list[str], conversion: Any
) -> dict[str, Any]:
    verdicts: Counter[str] = Counter()
    messages: dict[str, str] = {}
    latencies: list[float] = []
    attempted = net_names[:MAX_NETS_PER_BOARD]
    started = time.monotonic()
    for name in attempted:
        call_started = time.monotonic()
        try:
            document = tools.preview_route(
                {
                    "board": board,
                    "net": name,
                    "layer": "F.Cu",
                    "constraints": dict(CONSTRAINTS),
                },
                settings,
            )
        except Exception as error:
            latencies.append(_elapsed_ms(call_started))
            verdicts[f"raised/{type(error).__name__}"] += 1
            messages.setdefault(f"raised/{type(error).__name__}", str(error))
            continue
        latencies.append(_elapsed_ms(call_started))
        diagnostic = document.get("diagnostic") or {}
        code = diagnostic.get("code")
        verdict = document["status"] if code is None else f"{document['status']}/{code}"
        verdicts[verdict] += 1
        if verdict not in messages and diagnostic.get("message"):
            messages[verdict] = _redacted_message(diagnostic)
    record: dict[str, Any] = {
        "nets_total": len(net_names),
        "nets_attempted": len(attempted),
        "layer": "F.Cu",
        "verdicts": dict(sorted(verdicts.items())),
        "refusal_messages": dict(sorted(messages.items())),
        "elapsed_ms": _elapsed_ms(started),
    }
    if latencies:
        record["per_net_latency_ms"] = {
            "median": round(statistics.median(latencies), 1),
            "max": round(max(latencies), 1),
        }
    record["appliable_geometry"] = _route_appliable(conversion)
    return record


def _measure(board_path: Path, corpus: Path, settings: Settings) -> dict[str, Any]:
    relative = board_path.relative_to(corpus).as_posix()
    source = board_path.read_bytes()
    record: dict[str, Any] = {
        "board": relative,
        "board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
    }
    record["board_ir"] = _measure_board_ir(relative, settings, source)
    record["drc"] = _measure_drc(relative, settings)

    conversion = _convert(source, relative, settings)
    if conversion.snapshot is None:
        record["scene"] = {"outcome": "not_attempted", "reason": "board does not convert"}
        record["placement"] = {"outcome": "not_attempted", "reason": "board does not convert"}
        record["route"] = {"outcome": "not_attempted", "reason": "board does not convert"}
        return record

    content = conversion.snapshot.content
    footprint_refs = sorted(item.id for item in content.footprints)
    net_names = [net.name for net in sorted(content.nets, key=lambda net: net.id) if net.name]
    record["scene"] = _measure_scene(
        relative, settings, footprint_refs[0] if footprint_refs else None
    )
    record["placement"] = _measure_placement(relative, settings, footprint_refs, conversion)
    record["route"] = _measure_route(relative, settings, net_names, conversion)
    return record


def _totals(boards: list[dict[str, Any]]) -> dict[str, Any]:
    converting = [board for board in boards if board["board_ir"]["supported"]]
    drc_reported = [board for board in boards if board["drc"]["outcome"] == "reported"]
    placement_verdicts: Counter[str] = Counter()
    route_verdicts: Counter[str] = Counter()
    render_outcomes: Counter[str] = Counter()
    route_appliable: Counter[str] = Counter()
    scene_ceilings: Counter[str] = Counter()
    for board in converting:
        placement_verdicts.update(board["placement"]["verdicts"])
        route_verdicts.update(board["route"]["verdicts"])
        render_outcomes[board["placement"]["source_preserving_render"]["outcome"]] += 1
        route_appliable[board["route"]["appliable_geometry"]["outcome"]] += 1
        whole = board["scene"]["whole_board"]
        ceiling = whole.get("truncation", {}).get("ceiling_hit") or "none"
        scene_ceilings[str(ceiling)] += 1
    return {
        "boards_measured": len(boards),
        "boards_converting": len(converting),
        "drc_reported": len(drc_reported),
        "drc_passed": sum(1 for board in drc_reported if board["drc"]["passed"]),
        "drc_clean": sum(1 for board in drc_reported if board["drc"]["clean"]),
        "scene_whole_board_ceiling": dict(sorted(scene_ceilings.items())),
        "placement_verdicts": dict(sorted(placement_verdicts.items())),
        "placement_source_preserving_render": dict(sorted(render_outcomes.items())),
        "route_verdicts": dict(sorted(route_verdicts.items())),
        "route_appliable_geometry": dict(sorted(route_appliable.items())),
    }


def _commit() -> tuple[str, bool]:
    try:
        commit = subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), "rev-parse", "HEAD"],  # noqa: S607
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603
            ["git", "-C", str(ROOT), "status", "--porcelain"],  # noqa: S607
            capture_output=True,
            check=True,
            text=True,
        ).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        return "unknown", True
    return commit, bool(status)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        required=True,
        type=Path,
        help="root of the board corpus; read-only, never written",
    )
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument(
        "--kicad-cli",
        type=Path,
        default=None,
        help="kicad-cli executable; DRC is recorded as refused when it cannot be discovered",
    )
    arguments = parser.parse_args()

    corpus = arguments.corpus.expanduser().resolve(strict=True)
    settings = Settings(workspace=corpus, kicad_cli=arguments.kicad_cli)
    assert settings.allow_apply is False
    assert settings.allow_live_ipc is False
    assert settings.allow_live_apply is False

    started = time.monotonic()
    boards = [_measure(path, corpus, settings) for path in _boards(corpus)]
    wall_clock_s = round(time.monotonic() - started, 1)

    commit, dirty = _commit()
    runner = hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()
    report: dict[str, Any] = {
        "benchmark": "real-board-tier2-capability-v1",
        "scope": (
            "What five read-only CopperMCP surfaces do, per board, on a private real-board corpus "
            "at default settings. Conversion counts are the baseline; the measurement is what the "
            "surfaces built on conversion produce."
        ),
        "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
        "commit": commit,
        "dirty": dirty,
        "runner_digest": f"sha256:{runner}",
        "environment": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": platform.python_version(),
            "accelerator": "none (CPU-only)",
        },
        "configuration": {
            "constraints": dict(CONSTRAINTS),
            "route_layer": "F.Cu",
            "max_nets_per_board": MAX_NETS_PER_BOARD,
            "placement_batch": PLACEMENT_BATCH,
            "placement_latency_samples": PLACEMENT_LATENCY_SAMPLES,
            "bounded_region_radius_nm": BOUNDED_REGION_RADIUS_NM,
            "allow_apply": settings.allow_apply,
            "allow_live_ipc": settings.allow_live_ipc,
            "allow_live_apply": settings.allow_live_apply,
            "settings_defaults_otherwise": True,
        },
        "corpus": {
            "kind": "private working design tree; not redistributable and not committed",
            "excluded": [".history/ directories", *DERIVED_STEMS],
            "boards": len(boards),
        },
        "wall_clock_s": wall_clock_s,
        "totals": _totals(boards),
        "boards": boards,
        "not_claimed": [
            "no electrical, thermal, signal-integrity, manufacturing or fabrication claim",
            "no board was written, applied to, or opened in a live editor",
            "route preview was run on F.Cu only and on one net at a time against the unrouted "
            "snapshot, so the candidates are not mutually compatible and this is not a "
            "whole-board completion result",
            "placement preview was run without rules, so a clean verdict means legal-as-found "
            "and not a placement-quality result",
            "the corpus is one designer's project family, not a random sample of KiCad boards",
        ],
    }
    payload = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False)
    report["run_id"] = "sha256:" + hashlib.sha256(payload.encode()).hexdigest()
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    print(json.dumps(report["totals"], indent=2, sort_keys=True))
    print(f"wall clock {wall_clock_s}s -> {arguments.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
