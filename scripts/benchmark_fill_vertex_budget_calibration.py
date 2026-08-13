#!/usr/bin/env python3
"""Measure what a cached zone fill costs to read, and what the vertex budget actually meters.

`max_fill_vertices` shipped at 50,000, calibrated in ADR-0021 on CopperTone's 4,314-vertex pour.
Issue #165 measured real pours at 50,482-130,305 vertices, so the freshness proof was unreachable
on most of a working designer's zoned boards -- refused as a *resource* problem before the
question it exists to answer was asked.

Three things are measured here, because the number cannot be defended without all three.

1. **Density.** Fill vertices per mebibyte of source, over every board reachable from the
   repository plus an optional private corpus. This is what a re-derived default follows from,
   under ADR-0079's rule: a board that fits the parser's 16 MiB input ceiling should fit inside
   every scale budget.

2. **Cost, split into the part the budget can prevent and the part it cannot.**
   ``read_fill_islands`` parses the whole document before it counts a single vertex, so a budget
   refusal is paid for at full parse price. Reading at a budget of 3 measures what refusing
   costs; reading unbounded measures the whole; the difference is the only work this budget
   meters.

3. **Reachability.** How many fill vertices an adversary can present at all before the parse
   budgets refuse the document, which is where the setting's range has to end (ADR-0079).

Nothing here writes to any corpus, spawns KiCad, or sets an allow flag. Corpus rows carry a
source-digest prefix, byte and vertex counts and timings -- never a net name, a reference, or a
coordinate -- so the artifact is publishable while the boards are not.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import statistics
import subprocess
import time
import tracemalloc
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters.sexpr import SExprError, parse_sexpr
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.zone_fill import ZoneFillError, read_fill_islands

ROOT = Path(__file__).resolve().parents[1]
MIB = 1024 * 1024

#: The budget before this calibration, kept so the artifact states the change and not only the
#: new state.
PREVIOUS_DEFAULT = 50_000

#: Far past any reachable document, so a measurement observes the board and not the ceiling.
UNBOUNDED = 100_000_000

#: A budget no board can satisfy, so the read refuses at the first vertex and the elapsed time is
#: the cost of everything the budget cannot prevent.
REFUSING = 3

#: Derived boards of another board in the same directory; measuring them counts one design twice.
DERIVED_STEMS = ("routed-source", "best-board", "-placed")

#: The per-island ceiling the ordered-layer adapter enforces on fill it is handed
#: (``routing/layered_board_adapter._MAX_FILL_VERTICES``). It is a different population from the
#: board total this budget meters, and counting islands above it is how the difference shows.
LAYERED_ISLAND_CEILING = 4_096


def _git_metadata() -> tuple[str, bool | None]:
    git = shutil.which("git")
    if git is None:
        return "unknown", None
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git argv
            [git, "-C", str(ROOT), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        dirty = bool(
            subprocess.run(  # noqa: S603 - fixed local Git argv
                [git, "-C", str(ROOT), "status", "--porcelain"],
                check=True,
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout
        )
    except (OSError, subprocess.SubprocessError):
        return "unknown", None
    return commit, dirty


def _timed(work: Any) -> tuple[float, float, Any]:
    tracemalloc.start()
    started = time.perf_counter_ns()
    try:
        value: Any = work()
    except (ZoneFillError, SExprError) as error:
        value = error
    elapsed_ns = time.perf_counter_ns() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    return elapsed_ns / 1e9, peak / MIB, value


def measure_board(path: Path, limits: ParseLimits) -> dict[str, Any] | None:
    """Return one board's fill population and read cost, or None when it carries no fill."""

    source = path.read_bytes()
    full_s, full_mib, islands = _timed(
        lambda: read_fill_islands(source, max_vertices=UNBOUNDED, limits=limits)
    )
    if isinstance(islands, ZoneFillError | SExprError) or not islands:
        return None
    refuse_s, refuse_mib, _ = _timed(
        lambda: read_fill_islands(source, max_vertices=REFUSING, limits=limits)
    )

    per_island = sorted(len(island.points) for island in islands)
    total = sum(per_island)
    marginal_s = max(full_s - refuse_s, 0.0)
    marginal_mib = max(full_mib - refuse_mib, 0.0)
    return {
        "source_digest12": hashlib.sha256(source).hexdigest()[:12],
        "source_bytes": len(source),
        "islands": len(per_island),
        "total_vertices": total,
        "max_island_vertices": per_island[-1],
        "median_island_vertices": per_island[len(per_island) // 2],
        "islands_over_layered_ceiling": sum(1 for n in per_island if n > LAYERED_ISLAND_CEILING),
        "vertices_per_mib": round(total / (len(source) / MIB), 1),
        "admitted_at_previous_default": total <= PREVIOUS_DEFAULT,
        "full_read_s": round(full_s, 4),
        "refusing_read_s": round(refuse_s, 4),
        "marginal_s": round(marginal_s, 4),
        "marginal_share_of_full": round(marginal_s / full_s, 4) if full_s else 0.0,
        "marginal_us_per_vertex": round(marginal_s / total * 1e6, 3),
        "marginal_bytes_per_vertex": round(marginal_mib * MIB / total, 1),
        "full_read_peak_mib": round(full_mib, 2),
    }


def _adversarial_document(vertices: int, islands: int) -> bytes:
    """The densest legal cached-fill document: nothing but eight-byte `(xy 0 0)` vertices."""

    per_island = max(3, vertices // islands)
    parts = [b'(kicad_pcb(net 1 "N")']
    for _ in range(islands):
        parts.append(b'(zone(net 1)(filled_polygon(layer "F.Cu")(pts')
        parts.append(b"(xy 0 0)" * per_island)
        parts.append(b")))")
    parts.append(b")")
    return b"".join(parts)


def _reachable_maximum(islands: int, limits: ParseLimits) -> dict[str, Any]:
    """Bisect the largest fill-vertex population the *parser* will admit at all.

    Above this the document refuses with `budget.exceeded.*` before a fill vertex is counted, so
    a `max_fill_vertices` above it cannot bind. That is where the setting's range has to end.
    """

    low, high = 3, 4_000_000
    while low < high:
        middle = (low + high + 1) // 2
        document = _adversarial_document(middle, islands)
        if len(document) > limits.max_input_bytes:
            high = middle - 1
            continue
        try:
            parse_sexpr(document, limits)
        except SExprError:
            high = middle - 1
        else:
            low = middle
    refusal = ""
    over = _adversarial_document(low + 1, islands)
    if len(over) > limits.max_input_bytes:
        refusal = "budget.exceeded.input_bytes"
    else:
        try:
            parse_sexpr(over, limits)
        except SExprError as error:
            refusal = str(error).split(" at byte ")[0]
    return {
        "islands": islands,
        "max_reachable_vertices": low,
        "document_bytes": len(_adversarial_document(low, islands)),
        "refuses_above_with": refusal,
    }


def _budget_cost(budget: int, vertices: int, islands: int, limits: ParseLimits) -> dict[str, Any]:
    document = _adversarial_document(vertices, islands)
    elapsed_s, peak_mib, outcome = _timed(
        lambda: read_fill_islands(document, max_vertices=budget, limits=limits)
    )
    return {
        "budget": budget,
        "offered_vertices": vertices,
        "document_bytes": len(document),
        "outcome": "refused" if isinstance(outcome, ZoneFillError | SExprError) else "read",
        "refusal": str(outcome) if isinstance(outcome, ZoneFillError | SExprError) else "",
        "elapsed_s": round(elapsed_s, 3),
        "peak_traced_mib": round(peak_mib, 1),
    }


def _boards(corpus: Path | None) -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = [
        ("repository", path)
        for path in sorted(ROOT.rglob("*.kicad_pcb"))
        if ".git" not in path.parts
    ]
    if corpus is not None:
        found.extend(
            ("corpus", path)
            for path in sorted(corpus.rglob("*.kicad_pcb"))
            if ".history" not in path.parts and not any(stem in path.name for stem in DERIVED_STEMS)
        )
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--corpus",
        type=Path,
        default=None,
        help="optional private board tree; read-only, never written, never redistributed",
    )
    parser.add_argument("--output", type=Path, default=None)
    arguments = parser.parse_args()

    settings = Settings(workspace=ROOT)
    limits = ParseLimits()
    corpus = arguments.corpus.expanduser().resolve(strict=True) if arguments.corpus else None

    boards: list[dict[str, Any]] = []
    for origin, path in _boards(corpus):
        row = measure_board(path, limits)
        if row is None:
            continue
        row["origin"] = origin
        if origin == "repository":
            row["path"] = path.relative_to(ROOT).as_posix()
        boards.append(row)

    reachability = [_reachable_maximum(islands, limits) for islands in (1, 4_096)]
    worst_reachable = max(row["max_reachable_vertices"] for row in reachability)
    budget_costs = [
        _budget_cost(budget, worst_reachable, 4_096, limits)
        for budget in (PREVIOUS_DEFAULT, settings.max_fill_vertices, 1_000_000)
    ]

    densities = [row["vertices_per_mib"] for row in boards]
    totals = [row["total_vertices"] for row in boards]
    commit, dirty = _git_metadata()
    report: dict[str, Any] = {
        "benchmark": "fill-vertex-budget-calibration-v1",
        "boards": boards,
        "boards_measured": len(boards),
        "budget_cost_at_the_densest_reachable_document": budget_costs,
        "commit": commit,
        "configuration": {
            "previous_default": PREVIOUS_DEFAULT,
            "shipped_default": settings.max_fill_vertices,
            "environment_variable": "COPPER_MCP_MAX_FILL_VERTICES",
            "environment_range": [3, 1_000_000],
            "layered_per_island_ceiling": LAYERED_ISLAND_CEILING,
            "parse_limits_defaults": {
                "max_input_bytes": limits.max_input_bytes,
                "max_nodes": limits.max_nodes,
                "max_tokens": limits.max_tokens,
                "max_children_per_list": limits.max_children_per_list,
            },
        },
        "density_per_mib": {
            "max": max(densities),
            "median": round(statistics.median(densities), 1),
            "min": min(densities),
        },
        "dirty": dirty,
        "environment": {
            "accelerator": "none (CPU-only)",
            "machine": platform.machine(),
            "platform": platform.platform(),
            "processor": platform.processor() or "unknown",
            "python": platform.python_version(),
        },
        "instrumentation": "perf_counter_ns with tracemalloc enabled",
        "marginal_cost": {
            "us_per_vertex_max": max(row["marginal_us_per_vertex"] for row in boards),
            "us_per_vertex_min": min(row["marginal_us_per_vertex"] for row in boards),
            "bytes_per_vertex_max": max(row["marginal_bytes_per_vertex"] for row in boards),
            "share_of_full_read_max": max(row["marginal_share_of_full"] for row in boards),
        },
        "observed_maxima": {
            "total_vertices": max(totals),
            "max_island_vertices": max(row["max_island_vertices"] for row in boards),
            "islands": max(row["islands"] for row in boards),
            "boards_over_previous_default": sum(
                1 for row in boards if not row["admitted_at_previous_default"]
            ),
            "boards_with_an_island_over_the_layered_ceiling": sum(
                1 for row in boards if row["islands_over_layered_ceiling"]
            ),
        },
        "reachability": reachability,
        "recorded_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "scope": (
            "Cached zone-fill reading only. No KiCad process is spawned, so no board here is "
            "measured fresh or stale; that split is B-105's. Corpus rows carry counts, timings "
            "and a source-digest prefix only."
        ),
        "not_claimed": [
            "no claim that these densities generalise beyond the boards measured",
            "no claim about refilled fill, which is a second population this budget also meters",
            "no electrical, thermal, manufacturing or fabrication claim",
            "the corpus, when supplied, is one designer's project family and not a random sample",
        ],
    }
    report["run_id"] = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.parent.mkdir(parents=True, exist_ok=True)
        arguments.output.write_text(rendered, encoding="utf-8")
        print(f"{len(boards)} boards -> {arguments.output}")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
