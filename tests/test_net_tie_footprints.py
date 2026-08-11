"""Net-tie footprints: netless obstacle copper, no connectivity claim (ADR-0092).

KiCad's ``net_tie_pad_groups`` declares that "nets attached to pads within a single
pad-group are allowed to short", and the footprint's filled copper polygon *is* that
short.  Board IR models nets as disjoint, so the copper cannot carry either tied net.
The contract pinned here resolves its two roles separately, each in the only safe
direction:

- **Obstacle — over-approximated.**  The polygon converts to a full-width netless
  ``Segment`` along its long midline whose modelled envelope — the endpoint bounding box
  grown by ``(width_nm + 1) // 2`` on all four sides — contains the drawn rectangle, so a
  third net can never route through the tie, and even the tied nets are kept out of it
  (over-refusal is the accepted direction for obstacles).
- **Connectivity — under-approximated.**  ``net_id`` is ``None``: the tied nets are
  *never* claimed connected through the tie, even though the fabricated board joins
  them.  A joined-nets claim could not be test-bound without proving the polygon
  actually bridges both pad groups, so it is a typed non-claim, mirrored on ADR-0078's
  net-0 copper.
- **Write-back — refused.**  The tie segments carry revision-derived identities on
  purpose (an ``fp_poly`` is not a KiCad track, so its UUID names no segment), and every
  source-preserving patch path refuses a snapshot containing a derived identity
  (ADR-0026).  No patch can break the short because no patch can touch the board at all.
"""

from __future__ import annotations

from dataclasses import replace
from itertools import pairwise
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    _require_native_geometry_identities,
)
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    NetClass,
    PointNM,
    make_snapshot,
)
from copper_mcp.routing import AStarRouter, AStarSettings, RouteCandidate, RouteRequest

NET_TIE_BOARD = Path(__file__).parent / "fixtures" / "board-ir-v0.2" / "net-tie-two-pad.kicad_pcb"

#: The tie polygon in board coordinates: local (0, -0.65)..(2.6, 0.65) placed at (20, 15).
TIE_RECT_NM = (20_000_000, 14_350_000, 22_600_000, 15_650_000)

#: One tie polygon exactly as the fixture carries it, for removal surgery.
_F_CU_TIE_POLY = (
    b"    (fp_poly\n"
    b"      (pts (xy 0 -0.65) (xy 2.6 -0.65) (xy 2.6 0.65) (xy 0 0.65))\n"
    b"      (stroke (width 0) (type solid))\n"
    b"      (fill yes)\n"
    b'      (layer "F.Cu")\n'
    b'      (uuid "20000000-0000-0000-0000-000000000002")\n'
    b"    )\n"
)
_B_CU_TIE_POLY = _F_CU_TIE_POLY.replace(b'"F.Cu"', b'"B.Cu"').replace(
    b"-000000000002", b"-000000000003"
)


def _profile() -> KiCadConstraintProfile:
    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(default,), default_net_class_id=default.id)


def _snapshot(source: bytes | None = None) -> BoardIRSnapshot:
    result = parse_kicad_bytes(source or NET_TIE_BOARD.read_bytes(), _profile())
    assert result.diagnostics == ()
    assert result.snapshot is not None
    return result.snapshot


def _refusal(source: bytes) -> tuple[str, str]:
    result = parse_kicad_bytes(source, _profile())
    assert result.snapshot is None
    diagnostic = result.diagnostics[0]
    return diagnostic.code, diagnostic.message


def _tie_segments(snapshot: BoardIRSnapshot) -> list:
    return [item for item in snapshot.content.segments if ":derived:" in item.id]


def _request(snapshot: BoardIRSnapshot, net_name: str, *, grid_step_nm: int) -> RouteRequest:
    return RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=net_id_for_name(net_name),
        layer_id="layer:F.Cu",
        seed=7,
        settings=AStarSettings(grid_step_nm=grid_step_nm),
    )


def _orthogonal_edge_meets_rect(
    start: PointNM, end: PointNM, rect: tuple[int, int, int, int]
) -> bool:
    min_x, min_y, max_x, max_y = rect
    lo_x, hi_x = sorted((start.x, end.x))
    lo_y, hi_y = sorted((start.y, end.y))
    return hi_x >= min_x and lo_x <= max_x and hi_y >= min_y and lo_y <= max_y


# --- Conversion: the representation itself -------------------------------------------------


def test_two_pad_net_tie_converts_with_netless_obstacle_copper() -> None:
    """The board converts; each tie polygon is one netless full-width segment.

    The drawn rectangle is (20, 14.35)..(22.6, 15.65) on both outer layers.  The modelled
    segment runs its long midline at the rectangle's full height, so the router's
    ceil-rounded half width covers the rectangle exactly and over-approximates only by the
    two end caps — a superset, never a subset, of the real copper.
    """

    content = _snapshot().content

    ties = _tie_segments(_snapshot())
    assert len(ties) == 2
    assert {tie.layer_id for tie in ties} == {"layer:F.Cu", "layer:B.Cu"}
    for tie in ties:
        assert tie.net_id is None
        assert tie.start == PointNM(20_000_000, 15_000_000)
        assert tie.end == PointNM(22_600_000, 15_000_000)
        assert tie.width_nm == 1_300_000
    # The tied pads keep their own nets, and the nets stay disjoint objects.
    assert {net.name for net in content.nets} == {"GND_A", "GND_B", "SIG"}
    pads_by_net = {pad.net_id for pad in content.pads}
    assert net_id_for_name("GND_A") in pads_by_net
    assert net_id_for_name("GND_B") in pads_by_net


def test_net_tie_boards_stay_write_back_refused() -> None:
    """ADR-0026's mechanism applies: derived identities keep every patch path closed.

    The tie segments are revision-derived by construction, and both source-preserving
    patch adapters refuse any snapshot carrying a derived identity — so no route or
    placement write-back can ever separate the tie copper from the pads it shorts.
    """

    with pytest.raises(KiCadPlacementPatchError):
        _require_native_geometry_identities(_snapshot())


# --- Obstacle role: a third net is kept out, and the guard is mutation-checked --------------


def test_a_third_net_route_through_the_tie_copper_is_refused_as_an_obstacle() -> None:
    """SIG's straight corridor crosses the tie rectangle, so the route must detour.

    The two SIG pads sit at (21.5, 8) and (21.5, 24): the straight track between them
    passes between the tie pads and through the tie polygon.  With the tie modelled the
    router detours around it; no vertex and no edge of the candidate may meet the tie
    rectangle.
    """

    snapshot = _snapshot()
    result = AStarRouter().propose(snapshot, _request(snapshot, "SIG", grid_step_nm=500_000))

    assert result.connected is None
    assert isinstance(result.candidate, RouteCandidate)
    path = result.candidate.patch.paths[0]
    assert result.candidate.cost.bend_count > 0
    for start, end in pairwise(path.vertices):
        assert not _orthogonal_edge_meets_rect(start, end, TIE_RECT_NM)


def test_the_third_net_guard_is_load_bearing() -> None:
    """Mutation check: with the tie segments deleted, the same route goes straight through.

    This is the control experiment for the test above.  If the adapter ever stopped
    converting tie copper — the guard whose removal is the dangerous mutation — the SIG
    route would cross the tie rectangle exactly as it does here, and the detour assertion
    above is what would catch it.
    """

    snapshot = _snapshot()
    mutated_content = replace(
        snapshot.content,
        segments=tuple(
            segment for segment in snapshot.content.segments if ":derived:" not in segment.id
        ),
    )
    mutated = make_snapshot(mutated_content)

    result = AStarRouter().propose(mutated, _request(mutated, "SIG", grid_step_nm=500_000))

    assert result.connected is None
    assert isinstance(result.candidate, RouteCandidate)
    assert result.candidate.cost.bend_count == 0
    path = result.candidate.patch.paths[0]
    assert any(
        _orthogonal_edge_meets_rect(start, end, TIE_RECT_NM)
        for start, end in pairwise(path.vertices)
    )


# --- Connectivity role: the non-claim is pinned explicitly ----------------------------------


def test_the_tied_nets_are_never_claimed_connected_through_the_tie() -> None:
    """GND_A's two pads are physically joined only through the tie; the model says nothing.

    The GND_A stub track ends on the tie polygon clear of both tie pads, so on the
    fabricated board GND_A's connector pad reaches tie pad 1 (and GND_B) through the tie
    copper.  Connectivity under-approximates: netless copper joins nothing, so no
    already-connected claim may appear.  If a mutation ever let a ``None`` net compare
    equal to the routed net — or let tie copper adopt a tied net — the stub, the tie and
    tie pad 1 would fuse into one component and ``connected`` would flip non-None here.
    The router does not even propose new GND_A copper: the tie obstacle envelope covers
    tie pad 1 entirely, so over-refusal, the accepted direction, is the outcome.
    """

    snapshot = _snapshot()
    result = AStarRouter().propose(snapshot, _request(snapshot, "GND_A", grid_step_nm=100_000))

    assert result.connected is None
    assert result.candidate is None
    assert result.diagnostic is not None


# --- Malformed ties still refuse -------------------------------------------------------------


@pytest.mark.parametrize(
    ("surgery", "expected_code", "expected_message"),
    [
        pytest.param(
            (b'"1, 2"', b'"1, 9"', 1),
            "syntax.invalid",
            "net-tie pad group references a pad the footprint does not carry",
            id="group-names-a-missing-pad",
        ),
        pytest.param(
            (b'"1, 2"', b'"1, 2, 3"', 1),
            "unsupported.construct",
            "net-tie pad groups of other than two pads are unsupported",
            id="three-pad-group",
        ),
        pytest.param(
            (b'"1, 2"', b'"1"', 1),
            "unsupported.construct",
            "net-tie pad groups of other than two pads are unsupported",
            id="one-pad-group",
        ),
        pytest.param(
            (b'"1, 2"', b'"1" "2"', 1),
            "unsupported.construct",
            "net-tie footprints with more than one pad group are unsupported",
            id="two-groups",
        ),
        pytest.param(
            (b'"1, 2"', b'"1, "', 1),
            "syntax.invalid",
            "net-tie pad group is malformed",
            id="empty-pad-name",
        ),
        pytest.param(
            (b'"1, 2"', b'"1, 1"', 1),
            "syntax.invalid",
            "net-tie pad group is malformed",
            id="repeated-pad-name",
        ),
        pytest.param(
            (b"(xy 0 -0.65) (xy 2.6 -0.65)", b"(xy 0 -0.65) (xy 2.7 -0.75) (xy 2.6 -0.65)", 1),
            "unsupported.construct",
            "net-tie copper polygon must be an axis-aligned rectangle",
            id="non-rectangular-polygon",
        ),
        pytest.param(
            (b"(xy 0 -0.65)", b"(xy 0.1 -0.6)", 1),
            "unsupported.construct",
            "net-tie copper polygon must be an axis-aligned rectangle",
            id="skewed-corner",
        ),
        pytest.param(
            (b"(stroke (width 0) (type solid))", b"(stroke (width 0.1) (type solid))", 1),
            "unsupported.construct",
            "net-tie copper polygon with a stroked outline is unsupported",
            id="stroked-outline",
        ),
        pytest.param(
            (b"(fill yes)", b"(fill no)", 1),
            "unsupported.construct",
            "net-tie copper polygon must be filled",
            id="unfilled-polygon",
        ),
    ],
)
def test_a_malformed_net_tie_still_refuses_typed(
    surgery: tuple[bytes, bytes, int], expected_code: str, expected_message: str
) -> None:
    old, new, count = surgery
    source = NET_TIE_BOARD.read_bytes()
    assert source.count(old) >= count
    mutated = source.replace(old, new, count)

    code, message = _refusal(mutated)

    assert code == expected_code
    assert message == expected_message


def test_tie_copper_on_a_layer_its_pads_do_not_occupy_refuses() -> None:
    """A B.Cu polygon whose tied pads exist only on F.Cu cannot be part of the short."""

    source = NET_TIE_BOARD.read_bytes().replace(b'(layers "*.Cu")', b'(layers "F.Cu")')

    code, message = _refusal(source)

    assert code == "unsupported.construct"
    assert message == "net-tie copper lies on a copper layer its tied pads do not occupy"


def test_a_net_tie_without_tie_copper_refuses() -> None:
    """A declaration with no supported shorting copper is an unobserved construct."""

    source = NET_TIE_BOARD.read_bytes().replace(_F_CU_TIE_POLY, b"").replace(_B_CU_TIE_POLY, b"")
    assert b"fp_poly" not in source

    code, message = _refusal(source)

    assert code == "unsupported.construct"
    assert message == "net-tie footprint carries no supported tie copper"


def test_a_non_polygon_net_tie_primitive_refuses_by_name() -> None:
    """The preflight names the unsupported primitive instead of calling it a stray drawing."""

    source = NET_TIE_BOARD.read_bytes().replace(
        _F_CU_TIE_POLY,
        (
            b"    (fp_line\n"
            b"      (start 0 0) (end 2.6 0)\n"
            b"      (stroke (width 1.3) (type solid))\n"
            b'      (layer "F.Cu")\n'
            b'      (uuid "20000000-0000-0000-0000-0000000000f2")\n'
            b"    )\n"
        ),
    )

    code, message = _refusal(source)

    assert code == "unsupported.construct"
    assert message == "net-tie copper must be a filled polygon; other primitives are unsupported"


def test_a_square_tie_takes_the_x_axis_midline_so_the_degenerate_case_is_pinned() -> None:
    """A square tie has no long side, and the tie-break must still be one fixed answer.

    `x_max - x_min >= y_max - y_min` picks the x-axis midline when the two are equal. Both
    branches are sound -- each modelled envelope contains the square -- but they are *different*
    segments, so the choice is observable in every published content address. Weakening the
    comparison to `>` silently transposes the emitted geometry on exactly this input and on no
    other, which is the shape of defect that reaches a digest before it reaches a person.
    """

    square = NET_TIE_BOARD.read_bytes().replace(
        b"(xy 0 -0.65) (xy 2.6 -0.65) (xy 2.6 0.65) (xy 0 0.65)",
        b"(xy 0 -0.65) (xy 1.3 -0.65) (xy 1.3 0.65) (xy 0 0.65)",
    )

    ties = _tie_segments(_snapshot(square))

    assert len(ties) == 2
    for tie in ties:
        # The x-axis branch: a horizontal midline spanning the square, full width.
        assert tie.start == PointNM(20_000_000, 15_000_000)
        assert tie.end == PointNM(21_300_000, 15_000_000)
        assert tie.start.y == tie.end.y
        assert tie.width_nm == 1_300_000


def test_duplicate_pad_numbers_intersect_their_layers_rather_than_union_them() -> None:
    """A tied pad number occupies only the layers *every* pad carrying it occupies.

    The fixture's pad "1" is through-hole on `*.Cu`; adding a second pad "1" that is SMD on
    `F.Cu` alone means copper called "1" is not present on `B.Cu` for both of them. Intersecting
    is the conservative reading and refuses the `B.Cu` tie polygon; unioning would accept a tie
    on a layer one of the tied pads never reaches, which is a connectivity claim the geometry
    does not support.
    """

    source = NET_TIE_BOARD.read_bytes().replace(
        b'    (pad "2" thru_hole circle',
        b'    (pad "1" smd rect\n'
        b"      (at 0 0)\n"
        b"      (size 1 1)\n"
        b'      (layers "F.Cu")\n'
        b'      (net "GND_A")\n'
        b'      (uuid "20000000-0000-0000-0000-0000000000f1")\n'
        b"    )\n"
        b'    (pad "2" thru_hole circle',
    )

    code, message = _refusal(source)

    assert code == "unsupported.construct"
    assert message == "net-tie copper lies on a copper layer its tied pads do not occupy"


def test_a_five_point_tie_polygon_refuses_even_when_its_corner_set_is_a_rectangle() -> None:
    """Four points, not merely four distinct corners: a repeated vertex is still refused.

    A five-point ring whose last point does not close it can still carry exactly four distinct
    corners with two distinct x and two distinct y values, so every *corner-set* check passes.
    Only the point count separates it from the surveyed construct. The accepted subset is
    deliberately exactly what was measured, and everything wider is a typed refusal rather than
    an accepted guess -- accepting this shape would be sound and still unmeasured.
    """

    source = NET_TIE_BOARD.read_bytes().replace(
        b"(xy 0 -0.65) (xy 2.6 -0.65) (xy 2.6 0.65) (xy 0 0.65)",
        b"(xy 0 -0.65) (xy 2.6 -0.65) (xy 2.6 0.65) (xy 0 0.65) (xy 2.6 -0.65)",
    )

    code, message = _refusal(source)

    assert code == "unsupported.construct"
    assert message == "net-tie copper polygon must be an axis-aligned rectangle"


def test_a_footprint_declaring_net_tie_pad_groups_twice_refuses() -> None:
    """Two declarations are a contradiction the adapter must not silently resolve.

    Reading the first and ignoring the rest would pick one of two possible tie topologies by
    document order, so the second declaration's pads would be tied in the file and untied in
    Board IR -- a connectivity claim made by omission.
    """

    source = NET_TIE_BOARD.read_bytes().replace(
        b'(net_tie_pad_groups "1, 2")',
        b'(net_tie_pad_groups "1, 2")\n    (net_tie_pad_groups "1, 2")',
    )

    code, message = _refusal(source)

    assert code == "syntax.duplicate_field"
    assert message == "footprint declares net_tie_pad_groups more than once"
