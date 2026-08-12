"""A courtyard belongs to the layer it is drawn on, not to its footprint's side (ADR-0097).

The Board IR adapter used to refuse any board on which a footprint's courtyard layer disagreed
with its copper side, as `unsupported.transform` / "courtyard layer does not match its footprint
side". That refusal described a mismatch KiCad does not recognise. `FOOTPRINT::BuildCourtyardCaches`
files every courtyard shape by the shape's own `F_CrtYd` / `B_CrtYd` layer and never reads the
footprint's side, and `DRC_TEST_PROVIDER_COURTYARD_CLEARANCE` compares front against front and back
against back with no side test anywhere (KiCad 10.0.5). The stock KiCad library ships footprints
that rely on it: `Connector_Wire:SolderWire-*_Relief` is declared `(layer "F.Cu")` and draws its
strain-relief slot on `B.CrtYd`, because the wire feeds through the board.

These tests pin the three properties that follow, in the order they matter:

1. a courtyard on the layer opposite the footprint's side **converts**, into a separate far-side
   set, with its geometry unchanged;
2. the far-side set **keeps out on the layer it is drawn on** - two coincident `B.CrtYd`
   rectangles collide whichever side their footprints sit on, and a `B.CrtYd` never collides with
   an `F.CrtYd`;
3. every previously representable board is **byte-identical**: the canonical payload omits the new
   keys when they are empty, so no snapshot digest moves.

The verdicts in (2) are the ones real `kicad-cli` 10.0.5 reports for the same three boards; the
oracle comparison itself lives in `scripts/benchmark_courtyard_oracle_parity.py`, which shells out
to the tool, and these tests pin the model side of it deterministically.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import KiCadPlacementPatchError
from copper_mcp.board_ir import (
    FootprintSide,
    NetClass,
    ParseLimits,
    PointNM,
    decode_snapshot_json,
    encode_snapshot,
)
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent

FIXTURE = Path(__file__).parent / "fixtures" / "board-ir-v0.2" / "courtyard-far-side.kicad_pcb"

#: The back part's courtyard layer, as committed. Substituting it produces the cross-layer case.
_UNDER_PART_COURTYARD_LAYER = (
    b'(layer "B.CrtYd")\n      (uuid "9a000000-0000-0000-0000-000000000014")'
)
#: The back part's copper side, as committed. Substituting it moves it onto the front.
_UNDER_PART_SIDE = b'(layer "B.Cu")\n    (uuid "9a000000-0000-0000-0000-000000000011")'


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


def _replace_once(source: bytes, old: bytes, new: bytes) -> bytes:
    assert source.count(old) == 1, "fixture substitution must be unambiguous"
    return source.replace(old, new)


def _cross_layer_board() -> bytes:
    """The back part draws `F.CrtYd`, so nothing shares a courtyard layer with anything."""

    return _replace_once(
        FIXTURE.read_bytes(),
        _UNDER_PART_COURTYARD_LAYER,
        _UNDER_PART_COURTYARD_LAYER.replace(b"B.CrtYd", b"F.CrtYd"),
    )


def _both_front_board() -> bytes:
    """Both footprints sit on the front; both still draw the colliding `B.CrtYd` rectangles."""

    moved = _replace_once(
        FIXTURE.read_bytes(), _UNDER_PART_SIDE, _UNDER_PART_SIDE.replace(b"B.Cu", b"F.Cu")
    )
    return _replace_once(
        moved, b'(layers "B.Cu" "B.Mask" "B.Paste")', b'(layers "F.Cu" "F.Mask" "F.Paste")'
    )


def _snapshot(source: bytes):
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None, conversion.diagnostics
    return conversion.snapshot


def _legality(source: bytes) -> dict[str, str]:
    snapshot = _snapshot(source)
    view = build_placement_view(source, snapshot)
    intent = parse_placement_intent(
        {
            "board": "courtyard-far-side.kicad_pcb",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": sorted(view.footprints),
        }
    )
    result = evaluate_placement(intent, snapshot, view)
    legality = (
        result.candidate.evidence.legality
        if result.candidate is not None
        else result.diagnostic.legality
        if result.diagnostic is not None
        else None
    )
    assert legality is not None
    return legality.to_dict()


def test_feed_through_footprint_converts_with_its_courtyard_on_both_layers() -> None:
    """The front footprint keeps its front envelope and gains its back slot, both exact."""

    content = _snapshot(FIXTURE.read_bytes()).content
    feed_through, under_part = content.footprints

    assert feed_through.side is FootprintSide.FRONT
    assert feed_through.courtyards[0].points == (
        PointNM(18_000_000, 18_000_000),
        PointNM(22_000_000, 18_000_000),
        PointNM(22_000_000, 24_000_000),
        PointNM(18_000_000, 24_000_000),
    )
    assert feed_through.far_side_courtyards[0].points == (
        PointNM(18_000_000, 28_000_000),
        PointNM(22_000_000, 28_000_000),
        PointNM(22_000_000, 32_000_000),
        PointNM(18_000_000, 32_000_000),
    )
    # Read by layer rather than by storage slot: the same rectangle is the front footprint's
    # *back* courtyard and the back footprint's *near* one, and both accessors agree.
    assert feed_through.back_courtyards[0] == feed_through.far_side_courtyards
    assert feed_through.front_courtyards[0] == feed_through.courtyards
    assert under_part.side is FootprintSide.BACK
    assert under_part.far_side_courtyards == ()
    assert under_part.back_courtyards[0] == under_part.courtyards
    assert under_part.front_courtyards == ((), ())


@pytest.mark.parametrize(
    ("name", "board", "expected"),
    [
        # Real kicad-cli 10.0.5 on these exact three boards: courtyards_overlap, nothing,
        # courtyards_overlap. The model must not be clear where the tool is not.
        ("shared back layer, opposite sides", FIXTURE.read_bytes(), "violated"),
        ("different layers, opposite sides", _cross_layer_board(), "proven_clear"),
        ("shared back layer, both on the front", _both_front_board(), "violated"),
    ],
)
def test_courtyard_overlap_is_decided_by_layer_not_by_footprint_side(
    name: str, board: bytes, expected: str
) -> None:
    """The pair that decides it: same geometry, and only the courtyard *layer* changes the verdict.

    The first and third boards place the identical rectangle on the identical layer and differ
    only in which copper side the second footprint sits on - so a model that gated on footprint
    side would answer differently for them, and the tool does not. The second board moves that
    rectangle to the other courtyard layer without moving it in x or y, which is the only change
    that may turn the verdict clear.
    """

    assert _legality(board)["courtyard_overlap"] == expected


def test_the_old_side_gate_would_have_published_a_false_clearance() -> None:
    """Named explicitly, because it is the reason this is a safety fix and not a widening.

    Under the previous same-side pairing the first board's two footprints were never compared:
    one is on the front and one is on the back. The published evidence would have been
    ``proven_clear`` for a placement real KiCad reports as a courtyard overlap - a keep-out
    silently dropped, in the one direction an obstacle may never err.
    """

    snapshot = _snapshot(FIXTURE.read_bytes())
    feed_through, under_part = snapshot.content.footprints

    assert feed_through.side is not under_part.side
    assert _legality(FIXTURE.read_bytes())["courtyard_overlap"] == "violated"


def test_a_moved_feed_through_carries_its_far_side_courtyard_with_it() -> None:
    """The far-side ring is part of the same rigid body, so a proposal moves both or neither.

    Moving the back part 10 mm clear in x must clear the collision; leaving the far-side ring
    behind would report the overlap anyway, and dropping it would report clear at any offset.
    """

    source = FIXTURE.read_bytes()
    snapshot = _snapshot(source)
    view = build_placement_view(source, snapshot)
    under_part = next(
        item for item in snapshot.content.footprints if item.side is FootprintSide.BACK
    )
    intent = parse_placement_intent(
        {
            "board": "courtyard-far-side.kicad_pcb",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "subjects": sorted(view.footprints),
            "proposals": [{"subject": under_part.id, "offset_x_nm": 10_000_000}],
        }
    )
    result = evaluate_placement(intent, snapshot, view)
    legality = (
        result.candidate.evidence.legality
        if result.candidate is not None
        else result.diagnostic.legality
    )
    assert legality is not None
    assert legality.courtyard_overlap == "proven_clear"


def test_far_side_keys_are_omitted_when_empty_so_no_snapshot_digest_moves() -> None:
    """Digest stability by construction, not by inspection.

    Every board representable before this change has both far-side tuples empty, and the
    canonical encoder emits neither key in that case, so its payload is byte-identical to the one
    it produced before the fields existed. The committed golden identities in
    `tests/test_golden_identities.py` are the other half of this claim.
    """

    unchanged = json.loads(
        encode_snapshot(
            _snapshot(
                (
                    Path(__file__).parent
                    / "fixtures"
                    / "board-ir-v0.2"
                    / "footprint-pose-courtyard.kicad_pcb"
                ).read_bytes()
            )
        )
    )
    for footprint in unchanged["content"]["items"]["footprints"]:
        assert "far_side_courtyards" not in footprint
        assert "far_side_courtyard_circles" not in footprint

    carrying = json.loads(encode_snapshot(_snapshot(FIXTURE.read_bytes())))
    emitted = [
        footprint
        for footprint in carrying["content"]["items"]["footprints"]
        if "far_side_courtyards" in footprint
    ]
    assert len(emitted) == 1
    assert len(emitted[0]["far_side_courtyards"]) == 1


def test_far_side_courtyards_survive_the_canonical_round_trip() -> None:
    """A decoded snapshot re-encodes to the same bytes, so the field is not write-only."""

    payload = encode_snapshot(_snapshot(FIXTURE.read_bytes()))
    decoded = decode_snapshot_json(payload)
    reencoded = encode_snapshot(decoded)

    assert reencoded == payload
    carrier = next(item for item in decoded.content.footprints if item.far_side_courtyards)
    assert carrier.far_side_courtyards[0].points[0] == PointNM(18_000_000, 28_000_000)


def test_scene_reports_the_far_side_courtyard_as_its_own_field() -> None:
    """The observation is complete or it is withheld; it is never quietly partial.

    The far-side rings are reported under their own key rather than folded into
    ``courtyards_nm``, because a consumer that unioned the two would read a keep-out on a side
    that has none.
    """

    from copper_mcp.circuit_scene import _footprint_object

    content = _snapshot(FIXTURE.read_bytes()).content
    feed_through, under_part = content.footprints

    carrier = _footprint_object(feed_through).geometry
    assert carrier["courtyards_nm"] == [
        [
            [18_000_000, 18_000_000],
            [22_000_000, 18_000_000],
            [22_000_000, 24_000_000],
            [18_000_000, 24_000_000],
        ]
    ]
    assert carrier["far_side_courtyards_nm"] == [
        [
            [18_000_000, 28_000_000],
            [22_000_000, 28_000_000],
            [22_000_000, 32_000_000],
            [18_000_000, 32_000_000],
        ]
    ]
    assert "far_side_courtyard_circles_nm" not in carrier
    # A footprint with nothing on the far layer keeps the exact payload it had before the field
    # existed, which is what keeps every previously observable scene revision valid.
    assert "far_side_courtyards_nm" not in _footprint_object(under_part).geometry


def test_write_back_still_refuses_a_board_carrying_a_far_side_courtyard_rectangle() -> None:
    """Convertible is not appliable, and this pins the gap rather than closing it.

    The source-preserving placement serializer rewrites only `F.CrtYd` rectangles on front-side
    footprints. A feed-through part is now previewable and is still not movable through the
    serializer - the same asymmetry ADR-0080 recorded for chamfered and circular courtyards. This
    test exists so that widening the serializer has to confront the far-side rectangle
    deliberately rather than by accident.
    """

    from copper_mcp.adapters.kicad_placement_patch import _validate_footprint_expression
    from copper_mcp.adapters.sexpr import children, parse_sexpr

    root = parse_sexpr(FIXTURE.read_bytes(), ParseLimits())
    feed_through = children(root, "footprint")[0]

    with pytest.raises(KiCadPlacementPatchError, match="only matching F.CrtYd rectangles"):
        _validate_footprint_expression(feed_through, "kicad_pcb.footprint[0]")
