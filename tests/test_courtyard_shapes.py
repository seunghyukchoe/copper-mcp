"""Chamfered and circular courtyards: exact import, three-valued legality, typed refusals.

These tests pin the per-shape decisions of the curved-courtyard ADR:

* an exact 45-degree chamfer is modelled **exactly in Board IR** and bracketed by outer/inner
  orthogonal bounds for legality;
* a circle with an exact integer radius is modelled **exactly in Board IR** and decided by an
  exact distance predicate (circle vs circle) or bracketed bounds (circle vs rings);
* an inexact circle radius, an arc primitive, an arbitrary-slope edge, and a non-quarter-turn
  footprint pose stay **typed refusals** - never a silent approximation;
* the bracketing direction is load-bearing: only an outer bound may prove ``proven_clear``,
  only an inner bound may prove ``violated``, and everything between is ``inconclusive``.
"""

from __future__ import annotations

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import (
    CourtyardCircle,
    NetClass,
    ParseLimits,
    PointNM,
    Ring,
    decode_snapshot_json,
    encode_snapshot,
)
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent
from copper_mcp.placement.geometry import courtyard_region_overlap


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=200_000,
        track_width_nm=250_000,
        via_diameter_nm=600_000,
        via_drill_nm=300_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


_CHAMFER_COURTYARD = """
    (fp_poly
      (pts (xy -3 -1.5) (xy -1.5 -3) (xy 3 -3) (xy 3 3) (xy -3 3))
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )"""

_CIRCLE_COURTYARD = """
    (fp_circle
      (center 0 0)
      (end 1.8 0)
      (stroke (width 0.05) (type default))
      (fill no)
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )"""


def _footprint(name: str, at: str, courtyard: str, serial: int) -> str:
    return f"""
  (footprint "{name}"
    (layer "F.Cu")
    (uuid "9b000000-0000-0000-0000-00000000{serial:04d}")
    (at {at})
    {courtyard.format(uuid=serial + 5000)}
    (pad "1" smd rect
      (at 0 0)
      (size 0.5 0.5)
      (layers "F.Cu" "F.Mask" "F.Paste")
      (uuid "9a000000-0000-0000-0000-00000000{serial:04d}")
    )
  )"""


def _board(*footprints: str) -> bytes:
    body = "".join(footprints)
    return f"""(kicad_pcb
  (version 20260206)
  (generator "copper-mcp-tests")
  (generator_version "0.2.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  ){body}
  (gr_rect
    (start 0 0)
    (end 40 30)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "99000000-0000-0000-0000-000000000099")
  )
)
""".encode()


def _courtyard_verdict(source: bytes) -> str:
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None, conversion.diagnostics
    view = build_placement_view(source, conversion.snapshot)
    intent = parse_placement_intent(
        {
            "board": "courtyard-shapes.kicad_pcb",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": sorted(view.footprints),
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    legality = (
        result.candidate.evidence.legality
        if result.candidate is not None
        else result.diagnostic.legality
        if result.diagnostic is not None
        else None
    )
    assert legality is not None
    return legality.courtyard_overlap


def test_chamfered_courtyards_convert_and_report_the_exact_three_valued_outcome() -> None:
    """One chamfered footprint against another at three separations, end to end.

    Far apart is a proof of clearance; a deep overlap is a proof of violation; an overlap
    confined to the chamfer's corner triangle is exactly the band where the orthogonal
    brackets disagree with the true shape, so the only honest published verdict there is
    ``inconclusive`` - the raw shapes are actually disjoint in that arrangement, which is
    why claiming ``violated`` from the outer bound would be a false accusation.
    """

    chamfer_a = _footprint("Test_ChamferA", "10 10", _CHAMFER_COURTYARD, 1)
    assert (
        _courtyard_verdict(
            _board(chamfer_a, _footprint("Test_ChamferB", "30 10", _CHAMFER_COURTYARD, 2))
        )
        == "proven_clear"
    )
    assert (
        _courtyard_verdict(
            _board(chamfer_a, _footprint("Test_ChamferB", "13 10", _CHAMFER_COURTYARD, 2))
        )
        == "violated"
    )
    # B's plain south-east corner reaches only into A's chamfered-away corner square,
    # (7, 7)..(8.5, 8.5) in the board frame: outside A's true region (the chamfer line is
    # x + y = 15.5 there and the shared box tops out at 15.2), but inside A's outer bound.
    # The raw shapes are disjoint and the outer bounds are not, which is precisely the band
    # where neither ``proven_clear`` nor ``violated`` is an honest claim.
    assert (
        _courtyard_verdict(
            _board(chamfer_a, _footprint("Test_ChamferB", "4.6 4.6", _CHAMFER_COURTYARD, 2))
        )
        == "inconclusive"
    )


def test_quarter_turned_chamfer_courtyard_is_transformed_exactly() -> None:
    """A 90-degree footprint pose keeps the chamfer exact: same class, integer vertices."""

    source = _board(
        _footprint("Test_ChamferA", "10 10 90", _CHAMFER_COURTYARD, 1),
        _footprint("Test_ChamferB", "30 10", _CHAMFER_COURTYARD, 2),
    )
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None, conversion.diagnostics
    rotated = next(
        footprint
        for footprint in conversion.snapshot.content.footprints
        if footprint.rotation_udeg == 90_000_000
    )
    assert len(rotated.courtyards) == 1
    # KiCad's quarter turn maps a local point (x, y) to (y, -x) before translation.
    assert set(rotated.courtyards[0].points) == {
        PointNM(8_500_000, 13_000_000),
        PointNM(7_000_000, 11_500_000),
        PointNM(7_000_000, 7_000_000),
        PointNM(13_000_000, 7_000_000),
        PointNM(13_000_000, 13_000_000),
    }
    assert _courtyard_verdict(source) == "proven_clear"


def test_arbitrary_angle_pose_and_arbitrary_slope_courtyard_stay_typed_refusals() -> None:
    """No measured board carries either shape, and neither has an exact integer model.

    A footprint at 30 degrees would need irrational courtyard vertices, so the pose is
    refused before any courtyard is read; an arbitrary-slope polygon edge is refused by the
    topology guard.  Both are refusals, not approximations - silently widening either and
    reporting ``proven_clear`` is the failure mode this surface exists to avoid.
    """

    rotated = parse_kicad_bytes(
        _board(_footprint("Test_ChamferA", "10 10 30", _CHAMFER_COURTYARD, 1)),
        _profile(),
        ParseLimits(),
    )
    assert rotated.snapshot is None
    assert rotated.diagnostics[0].code == "unsupported.transform"

    skewed_courtyard = """
    (fp_poly
      (pts (xy -3 -1.5) (xy -1 -3) (xy 3 -3) (xy 3 3) (xy -3 3))
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )"""
    skewed = parse_kicad_bytes(
        _board(_footprint("Test_Skewed", "10 10", skewed_courtyard, 1)),
        _profile(),
        ParseLimits(),
    )
    assert skewed.snapshot is None
    assert skewed.diagnostics[0].code == "unsupported.topology"
    assert (
        skewed.diagnostics[0].message
        == "courtyard edges must be non-zero and axis-aligned or 45-degree chamfers"
    )


def test_circular_courtyards_convert_exactly_and_report_all_three_outcomes() -> None:
    """Two lone circles are decided by the exact integer distance predicate.

    The radii sum to 3.6 mm.  10 mm apart is disjoint; 2 mm apart penetrates far beyond any
    cache loss; 3.59 mm apart penetrates by only 10,000 nm, inside the conceded band: a
    circle's cache is polygonised inward before contraction, so its worst-case loss is twice
    a polygon's, and the measured collision boundary (between 12,000 and 15,000 nm for this
    pair) is vertex-placement dependent.  The model concedes the whole band up to 20,000 nm
    rather than fitting KiCad's polygonisation internals.
    """

    circle_a = _footprint("Test_CircleA", "10 10", _CIRCLE_COURTYARD, 1)
    conversion = parse_kicad_bytes(
        _board(circle_a, _footprint("Test_CircleB", "20 10", _CIRCLE_COURTYARD, 2)),
        _profile(),
        ParseLimits(),
    )
    assert conversion.snapshot is not None, conversion.diagnostics
    circles = [
        circle
        for footprint in conversion.snapshot.content.footprints
        for circle in footprint.courtyard_circles
    ]
    assert circles == [
        CourtyardCircle(center=PointNM(10_000_000, 10_000_000), radius_nm=1_800_000),
        CourtyardCircle(center=PointNM(20_000_000, 10_000_000), radius_nm=1_800_000),
    ]
    # The canonical envelope round-trips the circles and never emits the key without them.
    envelope = encode_snapshot(conversion.snapshot)
    assert b"courtyard_circles" in envelope
    assert decode_snapshot_json(envelope) == conversion.snapshot

    assert (
        _courtyard_verdict(
            _board(circle_a, _footprint("Test_CircleB", "20 10", _CIRCLE_COURTYARD, 2))
        )
        == "proven_clear"
    )
    assert (
        _courtyard_verdict(
            _board(circle_a, _footprint("Test_CircleB", "12 10", _CIRCLE_COURTYARD, 2))
        )
        == "violated"
    )
    assert (
        _courtyard_verdict(
            _board(circle_a, _footprint("Test_CircleB", "13.59 10", _CIRCLE_COURTYARD, 2))
        )
        == "inconclusive"
    )
    # 20,000 nm of penetration is the worst-case double loss exactly; the closed end of the
    # band is conceded, not claimed.
    assert (
        _courtyard_verdict(
            _board(circle_a, _footprint("Test_CircleB", "13.58 10", _CIRCLE_COURTYARD, 2))
        )
        == "inconclusive"
    )


def test_circle_against_chamfered_ring_uses_the_bracketed_bounds() -> None:
    """Mixed shapes: a circle deep inside a polygonal courtyard is a proven violation,
    and one that only clips the polygon's bounding-box corner is a concession."""

    circle = _footprint("Test_CircleA", "10 10", _CIRCLE_COURTYARD, 1)
    assert (
        _courtyard_verdict(
            _board(circle, _footprint("Test_ChamferB", "10.5 10.5", _CHAMFER_COURTYARD, 2))
        )
        == "violated"
    )
    assert (
        _courtyard_verdict(
            _board(circle, _footprint("Test_ChamferB", "30 10", _CHAMFER_COURTYARD, 2))
        )
        == "proven_clear"
    )


def test_arc_courtyard_primitive_is_still_a_typed_refusal() -> None:
    """An arc is a fragment of a chain, not a closed region; no bound is honest yet."""

    arc_courtyard = """
    (fp_arc
      (start -1 0)
      (mid 0 -1)
      (end 1 0)
      (stroke (width 0.05) (type default))
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )"""
    result = parse_kicad_bytes(
        _board(_footprint("Test_Arc", "10 10", arc_courtyard, 1)),
        _profile(),
        ParseLimits(),
    )
    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"
    assert result.diagnostics[0].message == "courtyard primitive is unsupported by Board IR v0.2"


def test_inexact_circle_radius_is_refused_not_rounded() -> None:
    """A radius of sqrt(2) mm has no integer nanometre value; either rounding direction
    would misstate the keep-out on an evidence surface, so the import refuses."""

    diagonal_circle = """
    (fp_circle
      (center 0 0)
      (end 1 1)
      (stroke (width 0.05) (type default))
      (fill no)
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )"""
    result = parse_kicad_bytes(
        _board(_footprint("Test_Inexact", "10 10", diagonal_circle, 1)),
        _profile(),
        ParseLimits(),
    )
    assert result.snapshot is None
    assert result.diagnostics[0].code == "integer.precision"
    assert result.diagnostics[0].message == "courtyard circle radius is not an exact nanometre"


def test_circle_overlapping_a_sibling_courtyard_is_refused() -> None:
    """Even-odd pooling is only a union for disjoint contours, so overlap fails closed."""

    mixed = """
    (fp_circle
      (center 0 0)
      (end 1.8 0)
      (stroke (width 0.05) (type default))
      (fill no)
      (layer "F.CrtYd")
      (uuid "9c000000-0000-0000-0000-00000000{uuid:04d}")
    )
    (fp_rect
      (start -1 -1)
      (end 1 1)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "F.CrtYd")
      (uuid "9d000000-0000-0000-0000-000000000042")
    )"""
    result = parse_kicad_bytes(
        _board(_footprint("Test_Mixed", "10 10", mixed, 1)),
        _profile(),
        ParseLimits(),
    )
    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.topology"
    assert (
        result.diagnostics[0].message
        == "courtyard circles must be disjoint from other courtyard shapes"
    )


def test_circle_witness_must_survive_the_doubled_polygonisation_loss() -> None:
    """A circle-bearing overlap between one and two thresholds wide stays a concession.

    The inner-cross overlap here is 10,056 nm wide: enough to survive one cache contraction,
    not enough to survive a circle's worst-case double loss (inward polygonisation sagitta
    plus contraction).  Claiming ``violated`` from the single-loss witness is exactly the
    direction error the 10,001 nm oracle measurement caught, so this pins the doubled guard.
    """

    circle = CourtyardCircle(center=PointNM(10_000_000, 10_000_000), radius_nm=1_800_000)
    # The inscribed cross reaches x = 10,000,000 + isqrt(8 * r^2 / 9) = 11,697,056.
    ring = Ring(
        (
            PointNM(11_687_000, 7_000_000),
            PointNM(17_687_000, 7_000_000),
            PointNM(17_687_000, 13_000_000),
            PointNM(11_687_000, 13_000_000),
        )
    )
    assert courtyard_region_overlap((), (ring,), first_circles=(circle,)) == "inconclusive"
    # Moving the ring one full threshold closer makes the witness survive the double loss.
    deep_ring = Ring(
        (
            PointNM(11_667_000, 7_000_000),
            PointNM(17_667_000, 7_000_000),
            PointNM(17_667_000, 13_000_000),
            PointNM(11_667_000, 13_000_000),
        )
    )
    assert courtyard_region_overlap((), (deep_ring,), first_circles=(circle,)) == "violated"


def test_all_diagonal_ring_degrades_to_a_concession_never_a_verdict() -> None:
    """A diamond has no certified inner bound, so even a real deep overlap may only be
    conceded: fabricating ``violated`` from the outer bound is the exact direction error
    the bracketing exists to prevent, and ``proven_clear`` would be just as false."""

    million = 1_000_000
    diamond = Ring(
        (
            PointNM(0, -3 * million),
            PointNM(3 * million, 0),
            PointNM(0, 3 * million),
            PointNM(-3 * million, 0),
        )
    )
    square = Ring(
        (
            PointNM(-million, -million),
            PointNM(million, -million),
            PointNM(million, million),
            PointNM(-million, million),
        )
    )
    assert courtyard_region_overlap((diamond,), (square,)) == "inconclusive"
    far_square = Ring(
        (
            PointNM(9 * million, 9 * million),
            PointNM(11 * million, 9 * million),
            PointNM(11 * million, 11 * million),
            PointNM(9 * million, 11 * million),
        )
    )
    assert courtyard_region_overlap((diamond,), (far_square,)) == "proven_clear"
