#!/usr/bin/env python3
"""Measure incremental ledger retention against the rebuild it replaces, on the same fixtures.

Three things are measured, and they are deliberately of different kinds:

1. **Exact operation counts.** How many unit-resource insertions or removals each reconstruction
   performs. These are integers, reproducible on any host, and they are what the design claim is
   actually about. A reader who distrusts every timing below can still check them.
2. **Wall clock.** Host-specific, reported as a median over repetitions, and never used on its own
   to support a claim.
3. **Equivalence.** Every A/B pair asserts the two reconstructions leave the ledger in a
   byte-identical observable state. A faster reconstruction that answered differently would be a
   defect, so the speed number is only meaningful next to this flag.

Fixtures are the same before and after by construction: both strategies are replayed against one
recorded candidate set, in one process, in the same run.

This is candidate-only routing evidence. It does not invoke KiCad, apply a candidate, or turn any
count here into a DRC, electrical, fabrication, or whole-board claim.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import sys
import time
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any, TypedDict
from unittest.mock import patch

SCRIPT_FILE = Path(__file__).resolve()
ROOT = SCRIPT_FILE.parents[1]
SCRIPT_PATH = SCRIPT_FILE.relative_to(ROOT)
if str(SCRIPT_FILE.parent) not in sys.path:  # pragma: no cover - import bootstrap
    sys.path.insert(0, str(SCRIPT_FILE.parent))

import benchmark_simple_route_json_corpus as corpus_harness  # noqa: E402

from copper_mcp.benchmarks.simple_route_json import (  # noqa: E402
    SimpleRouteJsonImportError,
    import_simple_route_json,
)
from copper_mcp.board_ir import (  # noqa: E402
    ConstraintSet,
    Footprint,
    FootprintSide,
    Layer,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    PointNM,
    Ring,
    SourceInfo,
    make_content,
    make_snapshot,
)
from copper_mcp.routing import (  # noqa: E402
    AStarRouter,
    AStarSettings,
    CancellationCheck,
    CongestionPenalty,
    NegotiatedRoutingRequest,
    NegotiationPlan,
    RipUpRule,
    RipUpSlot,
    RouteCandidate,
    RouteRequest,
    RouteResult,
    VerifiedFill,
    negotiate_routes,
)

from copper_mcp.board_ir import BoardIRSnapshot  # noqa: E402  # isort: skip
from copper_mcp.routing.congestion import CongestionLedger  # noqa: E402
from copper_mcp.routing.spatial_index import IncrementalSpatialIndex  # noqa: E402

REPORT_SCHEMA = "copper-mcp/benchmark/incremental-spatial-index/v1"
DEFAULT_OUTPUT = ROOT / "benchmarks/results/routing/2026-08-06-incremental-spatial-index.json"
REPLAY_MINIMUM = 10
REPLAY_MAXIMUM = 64
LAYER_ID = "layer:F.Cu"
MM = 1_000_000
#: The retention fractions swept for every ledger fixture, as (kept, total) numerators.
RETENTION_NUMERATORS: tuple[int, ...] = (0, 1, 2, 3, 4)
RETENTION_DENOMINATOR = 4


class BenchmarkError(RuntimeError):
    """Raised when the harness itself is broken, never when a measurement is merely unflattering."""


# ------------------------------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------------------------------


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, allow_nan=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _median_ns(samples: Sequence[int]) -> int:
    return int(statistics.median(samples))


def _ledger(grid_step_nm: int) -> CongestionLedger:
    return CongestionLedger(
        grid_step_nm=grid_step_nm, present_penalty_nm=8_000_000, history_penalty_nm=5_000_000
    )


def _observable_state(ledger: CongestionLedger) -> bytes:
    """Everything a router or a published result can read off a ledger, in canonical order."""

    return _canonical_bytes(
        {
            "added_nets": sorted(ledger.added_nets),
            "conflict_scores": ledger.conflict_scores(),
            "overflow": [
                [item.kind, item.start.x, item.start.y, item.end.x, item.end.y, item.usage]
                for item in ledger.overflow_resources()
            ],
        }
    )


# ------------------------------------------------------------------------------------------
# Case 1 — the spatial index itself
# ------------------------------------------------------------------------------------------


class _IndexCase(TypedDict):
    entries: int
    cell_size_nm: int
    churn: int
    rebuild_median_ns: int
    incremental_median_ns: int
    rebuild_insert_operations: int
    incremental_mutate_operations: int
    answers_identical: bool
    query_superset_of_brute_force: bool


def _index_bounds(count: int) -> tuple[tuple[str, tuple[int, int, int, int]], ...]:
    """A deterministic spread of integer rectangles, generated without a random source."""

    entries: list[tuple[str, tuple[int, int, int, int]]] = []
    for index in range(count):
        min_x = (index * 7919) % 4_096
        min_y = (index * 6151) % 4_096
        width = 8 + (index % 23)
        height = 8 + ((index * 3) % 19)
        entries.append((f"key:{index:05d}", (min_x, min_y, min_x + width, min_y + height)))
    return tuple(entries)


def _index_case(count: int, *, cell_size_nm: int, replays: int) -> _IndexCase:
    entries = _index_bounds(count)
    churn = max(1, count // 8)
    queries = tuple((x, y, x + 64, y + 64) for x, y in ((0, 0), (1_000, 1_000), (3_000, 500)))

    live = dict(entries)
    for key, _bounds in entries[:churn]:
        del live[key]

    rebuild_samples: list[int] = []
    incremental_samples: list[int] = []
    rebuilt_answers: tuple[tuple[str, ...], ...] = ()
    incremental_answers: tuple[tuple[str, ...], ...] = ()

    for _ in range(replays):
        start = time.perf_counter_ns()
        fresh = IncrementalSpatialIndex(cell_size_nm=cell_size_nm, max_entries=count + 1)
        for key in sorted(live):
            fresh.insert(key, live[key])
        rebuilt_answers = tuple(fresh.query(query) for query in queries)
        rebuild_samples.append(time.perf_counter_ns() - start)

        warm = IncrementalSpatialIndex(cell_size_nm=cell_size_nm, max_entries=count + 1)
        for key, bounds in entries:
            warm.insert(key, bounds)
        start = time.perf_counter_ns()
        for key, _bounds in entries[:churn]:
            warm.remove(key)
        incremental_answers = tuple(warm.query(query) for query in queries)
        incremental_samples.append(time.perf_counter_ns() - start)

    brute_force = tuple(
        tuple(
            sorted(
                key
                for key, bounds in live.items()
                if bounds[0] <= query[2]
                and query[0] <= bounds[2]
                and bounds[1] <= query[3]
                and query[1] <= bounds[3]
            )
        )
        for query in queries
    )
    return {
        "entries": count,
        "cell_size_nm": cell_size_nm,
        "churn": churn,
        "rebuild_median_ns": _median_ns(rebuild_samples),
        "incremental_median_ns": _median_ns(incremental_samples),
        # The rebuild pays one insert per surviving entry; the incremental path pays one remove
        # per ripped-up entry.  That ratio, not the clock, is the structural claim.
        "rebuild_insert_operations": len(live),
        "incremental_mutate_operations": churn,
        "answers_identical": rebuilt_answers == incremental_answers,
        "query_superset_of_brute_force": all(
            set(expected) <= set(answered)
            for expected, answered in zip(brute_force, incremental_answers, strict=True)
        ),
    }


# ------------------------------------------------------------------------------------------
# Case 2 — ledger reconstruction, which is what ADR-0073 recorded as linear
# ------------------------------------------------------------------------------------------


class _RetentionPoint(TypedDict):
    retained_nets: int
    ripped_up_nets: int
    before_resource_operations: int
    after_resource_operations: int
    after_strategy: str
    operation_ratio_percent: float
    before_median_ns: int
    after_median_ns: int
    speedup_percent: float
    states_identical: bool


class _LedgerCase(TypedDict):
    fixture: str
    origin: str
    grid_step_nm: int
    candidate_nets: int
    total_unit_resources: int
    retention: list[_RetentionPoint]


def _retention_point(
    candidates: tuple[RouteCandidate, ...], grid_step_nm: int, *, kept: int, replays: int
) -> _RetentionPoint:
    retained = frozenset(item.patch.net_id for item in candidates[:kept])

    after_samples: list[int] = []
    before_samples: list[int] = []
    after_operations = 0
    after_subtracted = False
    before_operations = 0
    after_state = b""
    before_state = b""

    for _ in range(replays):
        # After: one `retain_only` call, which picks the cheaper of subtracting the departures
        # and re-counting the survivors from cached per-net resource sets.
        warm = _ledger(grid_step_nm)
        for candidate in candidates:
            warm.add_candidate(candidate)
        warm.update_history()
        start = time.perf_counter_ns()
        warm.retain_only(retained)
        after_samples.append(time.perf_counter_ns() - start)
        after_operations = warm.reconstruction_operations
        after_subtracted = warm.resource_removals > 0
        after_state = _observable_state(warm)

        # Before: exactly the ADR-0073 reconstruction — clear the ledger, then re-add every
        # retained candidate, re-deriving its unit resources from its path geometry each time.
        cold = _ledger(grid_step_nm)
        for candidate in candidates:
            cold.add_candidate(candidate)
        cold.update_history()
        baseline = cold.resource_insertions
        start = time.perf_counter_ns()
        cold.clear_present()
        for candidate in candidates:
            if candidate.patch.net_id in retained:
                cold.add_candidate(candidate)
        before_samples.append(time.perf_counter_ns() - start)
        before_operations = cold.resource_insertions - baseline
        before_state = _observable_state(cold)

    before_ns = _median_ns(before_samples)
    after_ns = _median_ns(after_samples)
    return {
        "retained_nets": len(retained),
        "ripped_up_nets": len(candidates) - len(retained),
        "before_resource_operations": before_operations,
        "after_resource_operations": after_operations,
        "after_strategy": "subtract-departures" if after_subtracted else "recount-survivors",
        "operation_ratio_percent": (
            round(after_operations * 100 / before_operations, 3) if before_operations else 0.0
        ),
        "before_median_ns": before_ns,
        "after_median_ns": after_ns,
        # Positive is faster after; negative is a regression.  Reported signed on purpose.
        "speedup_percent": round((before_ns - after_ns) * 100 / before_ns, 2) if before_ns else 0.0,
        "states_identical": after_state == before_state,
    }


def _ledger_case(
    fixture: str,
    origin: str,
    candidates: tuple[RouteCandidate, ...],
    grid_step_nm: int,
    *,
    replays: int,
) -> _LedgerCase:
    probe = _ledger(grid_step_nm)
    for candidate in candidates:
        probe.add_candidate(candidate)
    return {
        "fixture": fixture,
        "origin": origin,
        "grid_step_nm": grid_step_nm,
        "candidate_nets": len(candidates),
        "total_unit_resources": probe.resource_insertions,
        "retention": [
            _retention_point(
                candidates,
                grid_step_nm,
                kept=len(candidates) * numerator // RETENTION_DENOMINATOR,
                replays=replays,
            )
            for numerator in RETENTION_NUMERATORS
        ],
    }


# ------------------------------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------------------------------


_CONGESTED_NETS: tuple[tuple[str, tuple[int, int], tuple[int, int]], ...] = (
    ("net:cross-a", (2, 5), (12, 5)),
    ("net:cross-b", (4, 1), (4, 9)),
    ("net:cross-c", (6, 1), (6, 9)),
    ("net:cross-d", (8, 1), (8, 9)),
    ("net:far-e", (16, 2), (21, 2)),
    ("net:far-f", (16, 8), (21, 8)),
)


def _pad(identifier: str, net_id: str, point: tuple[int, int]) -> Pad:
    return Pad(
        id=identifier,
        net_id=net_id,
        center=PointNM(point[0] * MM, point[1] * MM),
        rotation_udeg=0,
        shape=PadShape.RECT,
        kind=PadKind.SMD,
        size_x_nm=400_000,
        size_y_nm=400_000,
        roundrect_radius_nm=None,
        drill_x_nm=None,
        drill_y_nm=None,
        layer_ids=(LAYER_ID,),
    )


def _congested_snapshot() -> BoardIRSnapshot:
    """The B-087 congested-channel fixture, reproduced so the two ledgers meet the same geometry."""

    pads = tuple(
        pad
        for net_id, start, end in _CONGESTED_NETS
        for pad in (
            _pad(f"pad:{net_id.removeprefix('net:')}:a", net_id, start),
            _pad(f"pad:{net_id.removeprefix('net:')}:b", net_id, end),
        )
    )
    net_class = NetClass(
        id="class:signal",
        name="Signal",
        clearance_nm=100_000,
        track_width_nm=200_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    return make_snapshot(
        make_content(
            source=SourceInfo(
                format="synthetic-incremental-index-benchmark",
                revision=f"sha256:{'e' * 64}",
                format_version="1",
                generator="incremental-spatial-index-v1",
            ),
            outline=(
                OutlineContour(
                    id="contour:board",
                    outer=Ring(
                        (
                            PointNM(0, 0),
                            PointNM(23 * MM, 0),
                            PointNM(23 * MM, 11 * MM),
                            PointNM(0, 11 * MM),
                        )
                    ),
                ),
            ),
            copper_layers=(Layer(id=LAYER_ID, name="F.Cu", index=0, kind="signal"),),
            nets=tuple(
                Net(id=net_id, name=net_id.removeprefix("net:").upper())
                for net_id, _start, _end in _CONGESTED_NETS
            ),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=tuple(
                    NetClassAssignment(net_id=net_id, net_class_id=net_class.id)
                    for net_id, _start, _end in _CONGESTED_NETS
                ),
            ),
            footprints=tuple(
                Footprint(
                    id=f"footprint:{net_id.removeprefix('net:')}",
                    origin=pads[index * 2].center,
                    rotation_udeg=0,
                    side=FootprintSide.FRONT,
                    pad_ids=(pads[index * 2].id, pads[index * 2 + 1].id),
                )
                for index, (net_id, _start, _end) in enumerate(_CONGESTED_NETS)
            ),
            pads=pads,
        )
    )


def _congested_settings() -> AStarSettings:
    return AStarSettings(
        grid_step_nm=MM,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=4_096,
        max_expansions=20_000,
        max_obstacles=256,
        max_obstacle_checks=400_000,
    )


def _congested_envelope(snapshot: BoardIRSnapshot) -> NegotiatedRoutingRequest:
    settings = _congested_settings()
    return NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=tuple(
            RouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=net_id,
                layer_id=LAYER_ID,
                seed=index + 1,
                settings=settings,
            )
            for index, (net_id, _start, _end) in enumerate(_CONGESTED_NETS)
        ),
        max_iterations=8,
        present_penalty_nm=8_000_000,
        history_penalty_nm=5_000_000,
        max_total_expansions=500_000,
        max_total_obstacle_checks=5_000_000,
        max_total_physical_checks=500_000,
    )


def _congested_candidates() -> tuple[RouteCandidate, ...]:
    snapshot = _congested_snapshot()
    router = AStarRouter()
    candidates: list[RouteCandidate] = []
    for index, (net_id, _start, _end) in enumerate(_CONGESTED_NETS):
        result = router.propose(
            snapshot,
            RouteRequest(
                board_revision=snapshot.snapshot_digest,
                net_id=net_id,
                layer_id=LAYER_ID,
                seed=index + 1,
                settings=_congested_settings(),
            ),
        )
        if result.candidate is not None:
            candidates.append(result.candidate)
    if not candidates:
        raise BenchmarkError("the congested fixture produced no candidates to measure")
    return tuple(candidates)


def _corpus_cases(*, replays: int) -> list[_LedgerCase]:
    """Route each committed corpus board net by net and A/B the ledger on the result.

    The corpus boards are real, externally authored, MIT-licensed SimpleRouteJson documents. They
    are *not* large: the largest yields nine candidates. They are included because they are real
    geometry at real coordinates, not because they establish anything about scale.
    """

    _manifest, samples = corpus_harness.load_corpus()
    router = AStarRouter()
    cases: list[_LedgerCase] = []
    for name, payload in samples:
        board = Path(name).stem
        try:
            problem = import_simple_route_json(board, payload)
        except SimpleRouteJsonImportError:
            continue
        by_step: dict[int, list[RouteCandidate]] = {}
        for net in problem.nets:
            if net.pad_count < 2:
                continue
            step = corpus_harness.DIVISOR_POLICY.step_for(problem, net.pad_ids)
            result = router.propose(
                problem.snapshot,
                RouteRequest(
                    board_revision=problem.snapshot.snapshot_digest,
                    net_id=net.net_id,
                    layer_id=net.layer_id,
                    seed=corpus_harness.SEED,
                    settings=AStarSettings(grid_step_nm=step, **corpus_harness.ROUTER_LIMITS),
                ),
            )
            if result.candidate is not None:
                by_step.setdefault(step, []).append(result.candidate)
        # One ledger has one lattice step by contract, so a board routed at several steps
        # contributes its largest single-step group rather than being silently merged.
        if not by_step:
            continue
        step = max(by_step, key=lambda item: (len(by_step[item]), item))
        group = tuple(by_step[step])
        if len(group) < 2:
            continue
        cases.append(
            _ledger_case(
                f"corpus/{board}",
                "external MIT-licensed SimpleRouteJson corpus board, routed net by net",
                group,
                step,
                replays=replays,
            )
        )
    if not cases:
        raise BenchmarkError("no corpus board produced two routable nets at one lattice step")
    return cases


def _relabel(
    candidate: RouteCandidate, net_id: str, vertices: tuple[PointNM, ...]
) -> RouteCandidate:
    """Return a structurally valid candidate at a new net id and geometry.

    Only the ledger reads this object, and the ledger reads the patch alone. The candidate id is
    deliberately left unchanged: nothing in this measurement publishes it, and recomputing it
    would imply an identity claim the harness is not making.
    """

    patch = replace(
        candidate.patch,
        net_id=net_id,
        paths=(replace(candidate.patch.paths[0], vertices=vertices),),
    )
    return replace(candidate, patch=patch)


def _scale_cases(base: RouteCandidate, *, replays: int) -> list[_LedgerCase]:
    """A synthetic sweep whose only purpose is to show how the two costs move with net count.

    Parallel horizontal tracks, one lattice row apart, so every net holds a comparable resource
    count and the sweep isolates net count from geometry. This is not a board and claims nothing
    about routing; it exists so the operation-count ratio can be read against a moving `n`.
    """

    cases: list[_LedgerCase] = []
    for count in (4, 8, 16, 32):
        candidates = tuple(
            _relabel(
                base,
                f"net:scale-{index:03d}",
                tuple(PointNM(point.x, index * MM) for point in base.patch.paths[0].vertices),
            )
            for index in range(count)
        )
        cases.append(
            _ledger_case(
                f"synthetic/parallel-{count}",
                "synthetic parallel tracks; isolates net count from geometry",
                candidates,
                MM,
                replays=replays,
            )
        )
    return cases


# ------------------------------------------------------------------------------------------
# Case 3 — the coordinator, end to end
# ------------------------------------------------------------------------------------------


class _PlanRun(TypedDict):
    plan: str
    rationale: str
    status: str
    completed: bool
    iterations: int
    ripups: int
    router_calls: int
    total_wire_length_nm: int
    unrouted_nets: int
    candidate_digest: str
    replay_deterministic: bool
    median_wall_ns: int


_PLANS: tuple[tuple[str, str, NegotiationPlan | None], ...] = (
    ("no-plan-baseline", "the ADR-0055 coordinator with no declared plan", None),
    ("legacy-equivalent", "the default plan; rips up every net on every pass", NegotiationPlan()),
    (
        "rip-up/conflicted-only",
        "retain every net holding no conflict; B-087 recorded that this does not converge here",
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
    ),
    (
        "rip-up/conflict-window-1",
        "conflicted nets plus every retained net within one lattice cell of one",
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=1)),
    ),
    (
        "rip-up/conflict-window-2",
        "the same rule at a two-cell window",
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=2)),
    ),
    (
        "rip-up/conflict-window-4",
        "a four-cell window, wide enough to reach across the congested channel",
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=4)),
    ),
    (
        "rip-up/conflict-window-16",
        "a window wide enough to be a proxy for full rip-up on this fixture",
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=16)),
    ),
)


@contextmanager
def _record_router_calls() -> Iterator[list[str]]:
    """Record router calls without changing the coordinator's exact-router method identity check."""

    original = AStarRouter.propose
    calls: list[str] = []

    def recorded(
        self: AStarRouter,
        snapshot: BoardIRSnapshot,
        request: RouteRequest,
        *,
        cancelled: CancellationCheck | None = None,
        verified_fill: tuple[VerifiedFill, ...] = (),
        congestion_penalty: CongestionPenalty | None = None,
    ) -> RouteResult:
        calls.append(request.net_id)
        return original(
            self,
            snapshot,
            request,
            cancelled=cancelled,
            verified_fill=verified_fill,
            congestion_penalty=congestion_penalty,
        )

    with patch.object(AStarRouter, "propose", recorded):
        yield calls


def _plan_runs(*, replays: int) -> list[_PlanRun]:
    snapshot = _congested_snapshot()
    envelope = _congested_envelope(snapshot)
    runs: list[_PlanRun] = []
    for name, rationale, plan in _PLANS:
        digests: set[str] = set()
        samples: list[int] = []
        calls: list[str] = []
        result = None
        for _ in range(replays):
            with _record_router_calls() as recorded:
                start = time.perf_counter_ns()
                result = negotiate_routes(snapshot, envelope, plan=plan)
                samples.append(time.perf_counter_ns() - start)
            calls = recorded
            digests.add(
                _digest([candidate.candidate_id for candidate in result.candidates])
                + f"|{result.status.value}|{result.iterations}|{result.ripups}|{len(calls)}"
            )
        assert result is not None
        runs.append(
            {
                "plan": name,
                "rationale": rationale,
                "status": result.status.value,
                "completed": result.ok,
                "iterations": result.iterations,
                "ripups": result.ripups,
                "router_calls": len(calls),
                "total_wire_length_nm": result.total_wire_length_nm,
                "unrouted_nets": len(result.unrouted_nets),
                "candidate_digest": _digest(
                    [candidate.candidate_id for candidate in result.candidates]
                ),
                "replay_deterministic": len(digests) == 1,
                "median_wall_ns": _median_ns(samples),
            }
        )
    return runs


# ------------------------------------------------------------------------------------------
# Report
# ------------------------------------------------------------------------------------------


def _validate_evidence_harness_commit(harness_commit: str) -> None:
    is_lowercase_sha = len(harness_commit) == 40 and all(
        character in "0123456789abcdef" for character in harness_commit
    )
    if not is_lowercase_sha:
        raise ValueError("evidence_harness_commit must be a lowercase 40-character Git commit")


def _evidence_harness_command(*, replays: int, harness_commit: str) -> str:
    return (
        "PYTHONPATH=src python3 scripts/benchmark_incremental_spatial_index.py "
        f"--replays {replays} --evidence-harness-commit {harness_commit} "
        f"--output {DEFAULT_OUTPUT.relative_to(ROOT).as_posix()}"
    )


def build_report(*, replays: int = REPLAY_MINIMUM, evidence_harness_commit: str) -> dict[str, Any]:
    """Build deterministic before/after evidence for incremental ledger retention."""

    if not REPLAY_MINIMUM <= replays <= REPLAY_MAXIMUM:
        raise ValueError(f"replays must be between {REPLAY_MINIMUM} and {REPLAY_MAXIMUM}")
    _validate_evidence_harness_commit(evidence_harness_commit)

    congested = _congested_candidates()
    index_cases = [
        _index_case(count, cell_size_nm=64, replays=replays) for count in (64, 256, 1_024)
    ]
    ledger_cases = [
        _ledger_case(
            "synthetic/congested-channel",
            "the B-087 congested-channel fixture, routed once per net",
            congested,
            MM,
            replays=replays,
        )
    ]
    ledger_cases.extend(_scale_cases(congested[0], replays=replays))
    ledger_cases.extend(_corpus_cases(replays=replays))
    coordinator_runs = _plan_runs(replays=replays)

    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "evidence_harness_commit": evidence_harness_commit,
        "evidence_harness_command": _evidence_harness_command(
            replays=replays, harness_commit=evidence_harness_commit
        ),
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_FILE.read_bytes()).hexdigest(),
        "replays": replays,
        "index_cases": index_cases,
        "ledger_cases": ledger_cases,
        "coordinator_runs": coordinator_runs,
        # Every field whose name ends in `_ns` is a host-specific wall-clock median.  A replay of
        # this harness on another machine reproduces every other field exactly and reproduces
        # none of these; the bound test compares the report with them removed.
        "host_specific_field_suffix": "_ns",
        "invariants": {
            "every_retention_state_identical": all(
                point["states_identical"] for case in ledger_cases for point in case["retention"]
            ),
            "every_index_answer_identical": all(case["answers_identical"] for case in index_cases),
            "every_index_query_conservative": all(
                case["query_superset_of_brute_force"] for case in index_cases
            ),
            "every_coordinator_replay_deterministic": all(
                run["replay_deterministic"] for run in coordinator_runs
            ),
        },
        "claim": {
            "classification": "same-fixture before/after with an exact operation count",
            "quality_claim": False,
            "rule": (
                "the operation counts are exact and host-independent and are the claim. The "
                "wall-clock medians are host-specific context and are not a claim on their own. "
                "No routing-quality, convergence, or general-board improvement is asserted: the "
                "coordinator table records what each rip-up rule did on one synthetic congested "
                "fixture, including where a rule is no better than the default."
            ),
        },
        "kicad_drc": "not_run",
        "apply": "not_invoked",
        "non_claims": [
            "candidate geometry is not applied to a board",
            "no model output or model-generated copper is used",
            "no manufacturing, fabrication, or board-mutation claim",
            "the corpus boards are real but small; no scaling result is claimed from them",
            "the synthetic parallel-track sweep is not a board and claims nothing about routing",
            "wall-clock medians are host-specific and are not portable numbers",
            "no FreeRouting, KiCad DRC, electrical, or multilayer comparison",
        ],
    }
    report["run_id"] = _digest(report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--replays", type=int, default=REPLAY_MINIMUM)
    parser.add_argument("--evidence-harness-commit", required=True)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = build_report(
            replays=args.replays, evidence_harness_commit=args.evidence_harness_commit
        )
    except ValueError as error:
        raise SystemExit(str(error)) from error
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
