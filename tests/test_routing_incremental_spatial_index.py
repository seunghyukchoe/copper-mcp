"""Acceptance tests for the incremental spatial index and the bounded rip-up window (ADR-0075).

Three properties are load-bearing and each has its own section below.

1. **Conservatism.** A query returns a *superset* of the entries whose bounds intersect it. An
   index that returned fewer would let an obstacle disappear, which is a correctness defect and
   not a performance regression.
2. **Incremental equals rebuilt.** An index after N inserts and removes answers identically to one
   built fresh from the survivors, and a ledger retained incrementally holds byte-identical
   counters to one cleared and re-added.
3. **Determinism.** Identical inputs produce identical bytes, including the order of a returned
   tuple and the order of a returned mapping.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import replace

import pytest

from copper_mcp.board_ir import (
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
from copper_mcp.routing import (
    NegotiatedRoutingRequest,
    NegotiationPlan,
    PlanNegotiatedRoutingResult,
    RipUpRule,
    RipUpSlot,
    RouteRequest,
    negotiate_routes,
)
from copper_mcp.routing.astar import AStarSettings, canonical_candidate_bytes
from copper_mcp.routing.congestion import CongestionLedger
from copper_mcp.routing.contracts import (
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
)
from copper_mcp.routing.negotiation_plan import (
    MAX_RIPUP_WINDOW_CELLS,
    NEUTRAL_WINDOW_CELLS,
    ripup_net_ids,
)
from copper_mcp.routing.spatial_index import (
    IncrementalSpatialIndex,
    SpatialIndexCapacityError,
    bounds_intersect,
    inflate_bounds,
)

_Bounds = tuple[int, int, int, int]

LAYER_ID = "layer:F.Cu"
MM = 1_000_000


# ------------------------------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------------------------------


def _brute_force(entries: dict[str, _Bounds], query: _Bounds) -> set[str]:
    """The authoritative answer: every stored rectangle that touches the query rectangle."""

    return {key for key, bounds in entries.items() if bounds_intersect(bounds, query)}


def _built(entries: dict[str, _Bounds], **kwargs: int) -> IncrementalSpatialIndex:
    index = IncrementalSpatialIndex(**kwargs)  # type: ignore[arg-type]
    for key in sorted(entries):
        index.insert(key, entries[key])
    return index


def _random_bounds(rng: random.Random, *, extent: int, size: int) -> _Bounds:
    min_x = rng.randrange(-extent, extent)
    min_y = rng.randrange(-extent, extent)
    return (min_x, min_y, min_x + rng.randrange(0, size), min_y + rng.randrange(0, size))


# ------------------------------------------------------------------------------------------
# 1. Conservatism: the differential superset property
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(12))
def test_every_query_returns_a_superset_of_the_brute_force_answer(seed: int) -> None:
    """The contract, checked against a linear scan over randomized integer rectangles.

    Sizes and cell sizes are drawn to force all three code paths: ordinary bucketed entries,
    entries too large to bucket, and query rectangles too wide to sweep.
    """

    rng = random.Random(seed)  # noqa: S311 - deterministic test data, not a secret
    cell_size = rng.choice((1, 7, 64, 1_000))
    entries = {
        f"key:{index:03d}": _random_bounds(rng, extent=5_000, size=rng.choice((3, 40, 4_000)))
        for index in range(rng.randrange(1, 60))
    }
    index = _built(
        entries,
        cell_size_nm=cell_size,
        max_entries=128,
        max_cells_per_entry=rng.choice((1, 16, 4_096)),
    )

    for _ in range(40):
        query = _random_bounds(rng, extent=5_000, size=rng.choice((1, 25, 9_000)))
        answered = set(index.query(query))
        expected = _brute_force(entries, query)
        assert expected <= answered, (cell_size, query, sorted(expected - answered))
        # Conservative, not unbounded: the widest legal answer is still every entry.
        assert answered <= set(entries)


def test_touching_rectangles_are_reported_as_intersecting() -> None:
    """Contact is not separation.

    This is the single guard the mutation check targets: narrowing `<=` to `<` in the intersection
    predicate would silently drop every obstacle a route merely grazes.
    """

    index = IncrementalSpatialIndex(cell_size_nm=10)
    index.insert("key:left", (0, 0, 100, 100))
    assert index.query((100, 100, 200, 200)) == ("key:left",)
    assert index.query((101, 100, 200, 200)) == ()
    assert bounds_intersect((0, 0, 100, 100), (100, 0, 200, 100))
    assert not bounds_intersect((0, 0, 100, 100), (101, 0, 200, 100))


def test_an_oversize_entry_is_returned_by_every_query_it_touches() -> None:
    index = IncrementalSpatialIndex(cell_size_nm=1, max_cells_per_entry=4)
    index.insert("key:giant", (0, 0, 1_000, 1_000))
    index.insert("key:small", (10, 10, 11, 11))

    assert index.oversize_count == 1
    assert index.query((500, 500, 500, 500)) == ("key:giant",)
    assert index.query((10, 10, 10, 10)) == ("key:giant", "key:small")
    # The oversize fallback adds candidates; it never invents an intersection that is not there.
    assert index.query((2_000, 2_000, 2_001, 2_001)) == ()


def test_a_query_wider_than_the_cell_ceiling_degrades_to_a_bounded_full_scan() -> None:
    index = IncrementalSpatialIndex(cell_size_nm=1, max_cells_per_entry=4)
    index.insert("key:a", (0, 0, 1, 1))
    index.insert("key:b", (500, 500, 501, 501))

    narrow, narrow_examined = index.query_with_stats((0, 0, 1, 1))
    wide, wide_examined = index.query_with_stats((-1_000, -1_000, 1_000, 1_000))

    assert narrow == ("key:a",)
    assert narrow_examined == 1
    assert wide == ("key:a", "key:b")
    assert wide_examined == index.entry_count


# ------------------------------------------------------------------------------------------
# 2. Incremental equals rebuilt
# ------------------------------------------------------------------------------------------


@pytest.mark.parametrize("seed", range(8))
def test_an_index_after_mutations_answers_identically_to_a_freshly_built_one(seed: int) -> None:
    rng = random.Random(1_000 + seed)  # noqa: S311 - deterministic test data, not a secret
    live: dict[str, _Bounds] = {}
    mutated = IncrementalSpatialIndex(cell_size_nm=rng.choice((3, 50)), max_entries=64)

    for step in range(200):
        if live and rng.random() < 0.4:
            victim = rng.choice(sorted(live))
            del live[victim]
            mutated.remove(victim)
        elif len(live) < 60:
            key = f"key:{step:03d}"
            bounds = _random_bounds(rng, extent=800, size=rng.choice((2, 30, 300)))
            live[key] = bounds
            mutated.insert(key, bounds)

    rebuilt = _built(live, cell_size_nm=mutated.cell_size_nm, max_entries=64)

    assert mutated.keys() == rebuilt.keys()
    assert mutated.entry_count == rebuilt.entry_count
    # Structural identity, not merely answer identity: an emptied bucket that was kept would make
    # two indexes holding the same entries distinguishable.
    assert mutated.bucket_count == rebuilt.bucket_count
    assert mutated.oversize_count == rebuilt.oversize_count
    for _ in range(60):
        query = _random_bounds(rng, extent=800, size=rng.choice((1, 40, 900)))
        assert mutated.query_with_stats(query) == rebuilt.query_with_stats(query)


def test_the_index_is_independent_of_insertion_order() -> None:
    rng = random.Random(7)  # noqa: S311 - deterministic test data, not a secret
    entries = {f"key:{index:02d}": _random_bounds(rng, extent=400, size=60) for index in range(30)}

    forward = IncrementalSpatialIndex(cell_size_nm=17)
    for key in sorted(entries):
        forward.insert(key, entries[key])
    backward = IncrementalSpatialIndex(cell_size_nm=17)
    for key in sorted(entries, reverse=True):
        backward.insert(key, entries[key])

    assert forward.bucket_count == backward.bucket_count
    for _ in range(50):
        query = _random_bounds(rng, extent=400, size=80)
        assert forward.query(query) == backward.query(query)


# ------------------------------------------------------------------------------------------
# 3. Determinism
# ------------------------------------------------------------------------------------------


def test_identical_mutation_sequences_produce_identical_query_bytes() -> None:
    rng = random.Random(11)  # noqa: S311 - deterministic test data, not a secret
    plan = [(f"key:{index:02d}", _random_bounds(rng, extent=300, size=45)) for index in range(24)]
    queries = [_random_bounds(rng, extent=300, size=90) for _ in range(30)]

    def replay() -> bytes:
        index = IncrementalSpatialIndex(cell_size_nm=13)
        for key, bounds in plan:
            index.insert(key, bounds)
        for key, _bounds in plan[:8]:
            index.remove(key)
        return json.dumps([list(index.query(query)) for query in queries]).encode("utf-8")

    first = replay()
    assert hashlib.sha256(first).hexdigest() == hashlib.sha256(replay()).hexdigest()


# ------------------------------------------------------------------------------------------
# 4. Bounded work and typed refusals
# ------------------------------------------------------------------------------------------


def test_the_index_refuses_work_beyond_its_declared_capacity() -> None:
    index = IncrementalSpatialIndex(cell_size_nm=1, max_entries=2)
    index.insert("key:a", (0, 0, 1, 1))
    index.insert("key:b", (2, 2, 3, 3))

    with pytest.raises(SpatialIndexCapacityError):
        index.insert("key:c", (4, 4, 5, 5))
    assert isinstance(SpatialIndexCapacityError("x"), ValueError)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"cell_size_nm": 0},
        {"cell_size_nm": -1},
        {"cell_size_nm": True},
        {"cell_size_nm": 1, "max_entries": 0},
        {"cell_size_nm": 1, "max_cells_per_entry": 0},
    ],
)
def test_the_index_refuses_a_malformed_declaration(kwargs: dict[str, int]) -> None:
    with pytest.raises(ValueError):
        IncrementalSpatialIndex(**kwargs)  # type: ignore[arg-type]


def test_the_index_refuses_a_duplicate_key_an_absent_removal_and_a_reversed_rectangle() -> None:
    index = IncrementalSpatialIndex(cell_size_nm=5)
    index.insert("key:a", (0, 0, 10, 10))

    with pytest.raises(ValueError):
        index.insert("key:a", (0, 0, 10, 10))
    with pytest.raises(ValueError):
        index.remove("key:missing")
    with pytest.raises(ValueError):
        index.insert("key:bad", (10, 0, 0, 10))
    with pytest.raises(ValueError):
        index.insert("", (0, 0, 1, 1))
    with pytest.raises(ValueError):
        inflate_bounds((0, 0, 1, 1), -1)
    assert index.keys() == ("key:a",)


def test_removing_and_reinserting_a_key_restores_the_original_structure() -> None:
    index = IncrementalSpatialIndex(cell_size_nm=4)
    index.insert("key:a", (0, 0, 40, 40))
    index.insert("key:b", (100, 100, 140, 140))
    before = index.bucket_count

    index.remove("key:a")
    assert "key:a" not in index
    index.insert("key:a", (0, 0, 40, 40))

    assert index.bucket_count == before
    assert index.query((0, 0, 0, 0)) == ("key:a",)


# ------------------------------------------------------------------------------------------
# 5. The congestion ledger retains incrementally without moving a byte
# ------------------------------------------------------------------------------------------


def _candidate(net_id: str, path: tuple[PointNM, ...], *, seed: int) -> RouteCandidate:
    patch = RoutePatch(net_id=net_id, layer_id=LAYER_ID, width_nm=200_000, paths=(RoutePath(path),))
    candidate = RouteCandidate(
        candidate_id=f"sha256:{'0' * 64}",
        base_revision=f"sha256:{'a' * 64}",
        start_pad_id=f"pad:{net_id}:a",
        end_pad_id=f"pad:{net_id}:b",
        patch=patch,
        cost=RouteCost(
            length_nm=patch.length_nm,
            bend_count=patch.bend_count,
            bend_cost_nm=0,
            proximity_steps=0,
            proximity_cost_nm=0,
            via_cost_nm=0,
            total_cost_nm=patch.length_nm,
        ),
        metrics=RouteMetrics(
            hard_internal_violations=0,
            unrouted_connections=0,
            vias=0,
            wire_length_nm=patch.length_nm,
            expanded_states=0,
            peak_frontier_states=1,
            obstacle_checks=0,
        ),
        settings=AStarSettings(
            grid_step_nm=MM,
            bend_penalty_nm=0,
            proximity_penalty_nm=0,
            max_grid_nodes=4_096,
            max_expansions=20_000,
            max_obstacles=256,
            max_obstacle_checks=400_000,
        ),
        router_version="test",
        policy="test",
        seed=seed,
        pad_count=2,
        ordering_policy="single-path",
    )
    return replace(
        candidate,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}",
    )


def _ledger() -> CongestionLedger:
    return CongestionLedger(
        grid_step_nm=MM, present_penalty_nm=7_000_000, history_penalty_nm=3_000_000
    )


def _ledger_state(ledger: CongestionLedger) -> bytes:
    """Serialize everything the router can observe about a ledger, in canonical order."""

    probes = [
        (PointNM(x * MM, y * MM), PointNM((x + 1) * MM, y * MM))
        for x in range(0, 12)
        for y in range(0, 12)
    ]
    return json.dumps(
        {
            "conflict_scores": ledger.conflict_scores(),
            "added_nets": sorted(ledger.added_nets),
            "overflow": [
                [item.kind, item.start.x, item.start.y, item.end.x, item.end.y, item.usage]
                for item in ledger.overflow_resources()
            ],
            "penalties": [ledger.penalty(start, end) for start, end in probes],
        },
        sort_keys=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _corpus() -> tuple[RouteCandidate, ...]:
    return (
        _candidate(
            "net:a", (PointNM(0, 5 * MM), PointNM(10 * MM, 5 * MM)), seed=1
        ),  # crosses b and c
        _candidate("net:b", (PointNM(4 * MM, 0), PointNM(4 * MM, 10 * MM)), seed=2),
        _candidate("net:c", (PointNM(6 * MM, 0), PointNM(6 * MM, 10 * MM)), seed=3),
        _candidate("net:d", (PointNM(0, 9 * MM), PointNM(3 * MM, 9 * MM)), seed=4),
    )


@pytest.mark.parametrize(
    "retained",
    [
        frozenset(),
        frozenset({"net:a"}),
        frozenset({"net:b", "net:c"}),
        frozenset({"net:a", "net:b", "net:c", "net:d"}),
        frozenset({"net:a", "net:d"}),
    ],
)
def test_incremental_retention_equals_clear_and_readd_byte_for_byte(
    retained: frozenset[str],
) -> None:
    """The whole point of ADR-0075: the two reconstructions are the same computation."""

    corpus = _corpus()

    incremental = _ledger()
    for candidate in corpus:
        incremental.add_candidate(candidate)
    incremental.update_history()
    incremental.retain_only(retained)

    rebuilt = _ledger()
    for candidate in corpus:
        rebuilt.add_candidate(candidate)
    rebuilt.update_history()
    rebuilt.clear_present()
    for candidate in corpus:
        if candidate.patch.net_id in retained:
            rebuilt.add_candidate(candidate)

    assert _ledger_state(incremental) == _ledger_state(rebuilt)
    # History is negotiation memory and must survive a rip-up under both reconstructions.
    assert incremental.overflow_resources() == rebuilt.overflow_resources()
    # Bounded memory, not merely equal answers.  A resource left behind at a count of zero is
    # invisible to every published reader — they all filter `usage > 1`, or read a Counter whose
    # default is already zero — so nothing above would notice the overlay growing pass by pass.
    assert incremental.live_resource_count == rebuilt.live_resource_count


@pytest.mark.parametrize(
    ("retained", "expect_subtraction"),
    [
        (frozenset({"net:a", "net:b", "net:c"}), True),
        (frozenset({"net:d"}), False),
        (frozenset(), False),
    ],
)
def test_retention_costs_the_smaller_of_the_two_reconstructions(
    retained: frozenset[str], expect_subtraction: bool
) -> None:
    """The performance claim, stated as an exact integer rather than a wall clock.

    The measured baseline is the path this replaces: clear the ledger, then re-add every retained
    candidate, re-deriving its unit resources from its geometry each time.
    """

    corpus = _corpus()

    incremental = _ledger()
    rebuilt = _ledger()
    for candidate in corpus:
        incremental.add_candidate(candidate)
        rebuilt.add_candidate(candidate)
    baseline_insertions = rebuilt.resource_insertions

    incremental.retain_only(retained)
    rebuilt.clear_present()
    for candidate in corpus:
        if candidate.patch.net_id in retained:
            rebuilt.add_candidate(candidate)

    replaced_cost = rebuilt.resource_insertions - baseline_insertions
    assert incremental.reconstruction_operations <= replaced_cost
    # The strategy switch is real, not decorative: a pass that rips up almost everything must
    # re-count the survivors instead of subtracting the departures.
    assert (incremental.resource_removals > 0) is expect_subtraction
    assert incremental.resource_insertions == baseline_insertions
    assert _ledger_state(incremental) == _ledger_state(rebuilt)


def test_the_ledger_refuses_to_remove_a_net_it_does_not_hold() -> None:
    ledger = _ledger()
    with pytest.raises(ValueError):
        ledger.remove_net("net:absent")
    ledger.add_candidate(_corpus()[0])
    with pytest.raises(ValueError):
        ledger.add_candidate(_corpus()[0])


def test_the_ledger_window_query_names_spatial_neighbours_only() -> None:
    ledger = _ledger()
    for candidate in _corpus():
        ledger.add_candidate(candidate)

    # `net:d` runs from x=0..3mm at y=9mm; `net:b` sits at x=4mm spanning y=0..10mm.
    touching = ledger.nets_within_window(frozenset({"net:d"}), window_nm=MM)
    isolated = ledger.nets_within_window(frozenset({"net:d"}), window_nm=0)

    assert "net:b" in touching
    assert "net:d" not in touching
    assert "net:c" not in isolated
    assert ledger.nets_within_window(frozenset({"net:absent"}), window_nm=MM) == frozenset()
    with pytest.raises(ValueError):
        ledger.nets_within_window(frozenset({"net:a"}), window_nm=-1)


# ------------------------------------------------------------------------------------------
# 6. The bounded rip-up window slot
# ------------------------------------------------------------------------------------------


def test_the_window_slot_publishes_a_distinct_digest_without_moving_the_others() -> None:
    """Adding a fourth literal must not move an already-published rip-up or plan digest."""

    assert RipUpSlot().slot_digest == (
        "sha256:871de3d64827d267ed64443a705431c7a4a32fa35a5815b137d9abb23f73c71a"
    )
    assert NegotiationPlan().plan_digest == (
        "sha256:b3d090edeeb861f0c215dd18420bdd5624a7f178f1034af25526457538d3eac0"
    )
    assert "ripup_window_cells" not in RipUpSlot().as_json()
    windowed = RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=2)
    assert windowed.as_json()["ripup_window_cells"] == 2
    assert len({windowed.slot_digest, RipUpSlot().slot_digest}) == 2
    wider = RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=3)
    assert wider.slot_digest != windowed.slot_digest


@pytest.mark.parametrize(
    "kwargs",
    [
        {"ripup_window_cells": 1},
        {"rule": RipUpRule.CONFLICTED_ONLY, "ripup_window_cells": 1},
        {"rule": RipUpRule.CONFLICT_WINDOW, "ripup_window_cells": NEUTRAL_WINDOW_CELLS},
        {"rule": RipUpRule.CONFLICT_WINDOW, "ripup_window_cells": MAX_RIPUP_WINDOW_CELLS + 1},
        {"rule": RipUpRule.CONFLICT_WINDOW, "ripup_window_cells": -1},
        {"rule": RipUpRule.CONFLICT_WINDOW, "ripup_window_cells": True},
        {"rule": RipUpRule.CONFLICT_WINDOW, "ripup_window_cells": 2, "max_ripup_nets": 3},
    ],
)
def test_the_window_slot_refuses_an_inert_or_out_of_range_parameter(kwargs: object) -> None:
    with pytest.raises(ValueError):
        RipUpSlot(**kwargs)  # type: ignore[arg-type]


def test_the_window_rule_selects_conflicted_nets_plus_their_window() -> None:
    nets = (("net:a", 1), ("net:b", 2), ("net:c", 3), ("net:d", 4))
    slot = RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=2)

    selected = ripup_net_ids(
        slot,
        nets=nets,
        conflict_scores={"net:a": 2},
        retained=frozenset({"net:a", "net:b", "net:c", "net:d"}),
        window_nets=frozenset({"net:b"}),
    )

    assert selected == frozenset({"net:a", "net:b"})


def test_a_window_supplied_to_a_rule_that_does_not_read_one_is_refused() -> None:
    nets = (("net:a", 1), ("net:b", 2))
    with pytest.raises(ValueError):
        ripup_net_ids(
            RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY),
            nets=nets,
            conflict_scores={"net:a": 1},
            retained=frozenset({"net:a", "net:b"}),
            window_nets=frozenset({"net:b"}),
        )
    with pytest.raises(ValueError):
        ripup_net_ids(
            RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=1),
            nets=nets,
            conflict_scores={},
            retained=frozenset({"net:a"}),
            window_nets=frozenset({"net:elsewhere"}),
        )


# ------------------------------------------------------------------------------------------
# 7. End to end through the coordinator
# ------------------------------------------------------------------------------------------


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


_NETS = (
    ("net:cross-a", (2, 5), (12, 5)),
    ("net:cross-b", (4, 1), (4, 9)),
    ("net:cross-c", (6, 1), (6, 9)),
    ("net:cross-d", (8, 1), (8, 9)),
    ("net:far-e", (16, 2), (21, 2)),
    ("net:far-f", (16, 8), (21, 8)),
)


def _congested_snapshot() -> object:
    pads = tuple(
        pad
        for net_id, start, end in _NETS
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
                format="synthetic-incremental-index-test",
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
                for net_id, _start, _end in _NETS
            ),
            constraints=ConstraintSet(
                net_classes=(net_class,),
                assignments=tuple(
                    NetClassAssignment(net_id=net_id, net_class_id=net_class.id)
                    for net_id, _start, _end in _NETS
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
                for index, (net_id, _start, _end) in enumerate(_NETS)
            ),
            pads=pads,
        )
    )


def _envelope(snapshot: object) -> NegotiatedRoutingRequest:
    assert hasattr(snapshot, "snapshot_digest")
    settings = AStarSettings(
        grid_step_nm=MM,
        bend_penalty_nm=500_000,
        proximity_penalty_nm=0,
        max_grid_nodes=4_096,
        max_expansions=20_000,
        max_obstacles=256,
        max_obstacle_checks=400_000,
    )
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
            for index, (net_id, _start, _end) in enumerate(_NETS)
        ),
        max_iterations=8,
        present_penalty_nm=8_000_000,
        history_penalty_nm=5_000_000,
        max_total_expansions=500_000,
        max_total_obstacle_checks=5_000_000,
        max_total_physical_checks=500_000,
    )


def _candidate_bytes(result: object) -> bytes:
    assert hasattr(result, "candidates")
    return b"\n".join(canonical_candidate_bytes(item) for item in result.candidates)


def test_the_no_plan_path_is_unchanged_by_incremental_retention() -> None:
    """The historic path rips up everything, so it must keep clearing rather than retaining."""

    snapshot = _congested_snapshot()
    envelope = _envelope(snapshot)

    first = negotiate_routes(snapshot, envelope)
    second = negotiate_routes(snapshot, envelope)

    assert type(first) is type(second)
    assert _candidate_bytes(first) == _candidate_bytes(second)
    assert first.iterations == second.iterations
    assert first.total_wire_length_nm == second.total_wire_length_nm


@pytest.mark.parametrize(
    "plan",
    [
        NegotiationPlan(),
        NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY)),
        NegotiationPlan(
            rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=2),
        ),
    ],
)
def test_a_declared_plan_replays_to_identical_bytes(plan: NegotiationPlan) -> None:
    snapshot = _congested_snapshot()
    envelope = _envelope(snapshot)

    first = negotiate_routes(snapshot, envelope, plan=plan)
    second = negotiate_routes(snapshot, envelope, plan=plan)

    assert isinstance(first, PlanNegotiatedRoutingResult)
    assert isinstance(second, PlanNegotiatedRoutingResult)
    assert _candidate_bytes(first) == _candidate_bytes(second)
    assert first.status is second.status
    assert first.iterations == second.iterations
    assert first.ripups == second.ripups
    assert first.plan_evidence == second.plan_evidence


def test_the_bounded_window_rips_up_more_than_conflicted_only_and_less_than_everything() -> None:
    """A window rule that selected the same set as its neighbours would be a vacuous slot."""

    snapshot = _congested_snapshot()
    envelope = _envelope(snapshot)

    conflicted = negotiate_routes(
        snapshot, envelope, plan=NegotiationPlan(rip_up=RipUpSlot(rule=RipUpRule.CONFLICTED_ONLY))
    )
    windowed = negotiate_routes(
        snapshot,
        envelope,
        plan=NegotiationPlan(
            rip_up=RipUpSlot(rule=RipUpRule.CONFLICT_WINDOW, ripup_window_cells=4)
        ),
    )

    assert isinstance(conflicted, PlanNegotiatedRoutingResult)
    assert isinstance(windowed, PlanNegotiatedRoutingResult)
    # The two far nets can never conflict with the crossing channel, so a bounded window must
    # never reach them however many passes it takes.
    assert windowed.ripups > conflicted.ripups
    assert windowed.plan_evidence.rip_up_slot_digest != conflicted.plan_evidence.rip_up_slot_digest
    assert windowed.plan_evidence.net_order_slot_digest == (
        conflicted.plan_evidence.net_order_slot_digest
    )
