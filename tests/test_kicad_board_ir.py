from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import tracemalloc
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExprError, parse_sexpr
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    FootprintSide,
    NetClass,
    Pad,
    PadShape,
    ParseLimits,
    PointNM,
    Severity,
    ZoneIslandRemoval,
    ZonePadConnection,
    encode_snapshot,
)

TEST_ROOT = Path(__file__).parent
REPOSITORY_ROOT = TEST_ROOT.parent
SUBSET_BOARD = TEST_ROOT / "fixtures" / "board-ir-v0.1" / "subset.kicad_pcb"
ROTATION_BOARD = TEST_ROOT / "fixtures" / "board-ir-v0.1" / "footprint-rotation.kicad_pcb"
FOOTPRINT_V02_BOARD = (
    TEST_ROOT / "fixtures" / "board-ir-v0.2" / "footprint-pose-courtyard.kicad_pcb"
)
FRONT_BACK_FOOTPRINT_V02_BOARD = (
    TEST_ROOT / "fixtures" / "board-ir-v0.2" / "footprint-front-back-pose.kicad_pcb"
)
ORTHOGONAL_COURTYARD_BOARD = (
    TEST_ROOT / "fixtures" / "board-ir-v0.2" / "courtyard-orthogonal-chains.kicad_pcb"
)
MALFORMED_BOARD = TEST_ROOT / "fixtures" / "board-ir-v0.1" / "malformed-unbalanced.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)
COPPERTONE_BOARD = (
    REPOSITORY_ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
)


def constraint_profile(
    *, clearance_nm: int = 250_000, assign_signal: bool = False
) -> KiCadConstraintProfile:
    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=clearance_nm,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    audio = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=300_000,
        track_width_nm=300_000,
        via_diameter_nm=900_000,
        via_drill_nm=450_000,
    )
    return KiCadConstraintProfile(
        net_classes=(default, audio),
        default_net_class_id=default.id,
        net_class_by_name=(("SIG_µ", audio.id),) if assign_signal else (),
    )


def parse_success(source: bytes, profile: KiCadConstraintProfile) -> BoardIRSnapshot:
    result = parse_kicad_bytes(source, profile)
    assert result.diagnostics == ()
    assert result.snapshot is not None
    return result.snapshot


def test_synthetic_kicad_subset_maps_exact_geometry_and_constraints() -> None:
    source = SUBSET_BOARD.read_bytes()
    snapshot = parse_success(source, constraint_profile(assign_signal=True))
    content = snapshot.content

    assert content.source.revision == f"sha256:{hashlib.sha256(source).hexdigest()}"
    assert content.source.format_version == "20260206"
    assert content.source.generator == "pcbnew"
    assert [layer.name for layer in content.copper_layers] == ["F.Cu", "B.Cu"]
    assert {net.name for net in content.nets} == {"GND", "SIG_µ"}
    assert (
        len(content.pads),
        len(content.vias),
        len(content.segments),
        len(content.arcs),
        len(content.zones),
        len(content.keepouts),
    ) == (2, 1, 1, 1, 1, 1)

    pads = {pad.id: pad for pad in content.pads}
    signal_pad = pads["pad:kicad:10000000-0000-0000-0000-000000000002"]
    through_pad = pads["pad:kicad:10000000-0000-0000-0000-000000000003"]
    # The footprint is at (10, 10) turned 90 degrees, so its local (-1, 0) pad maps to
    # (10, 11) and its local (1, 0) pad to (10, 9) under KiCad's own convention. See
    # test_footprint_rotation_matches_kicad_placement for how that is derived.
    assert signal_pad.center == PointNM(10_000_000, 11_000_000)
    # The pad angle is absolute, not relative to its footprint: this pad is written
    # `(at -1 0 0)` inside a footprint placed at 90 degrees, and KiCad draws it unrotated.
    # Verified against `kicad-cli pcb export svg` on this very fixture, which plots a
    # 2.00mm x 1.00mm rectangle centred on (10, 11) - see
    # test_pad_extents_match_the_geometry_kicad_actually_draws.
    assert signal_pad.rotation_udeg == 0
    assert signal_pad.shape is PadShape.ROUNDRECT
    assert signal_pad.roundrect_radius_nm == 250_000
    assert signal_pad.layer_ids == ("layer:F.Cu",)
    assert through_pad.center == PointNM(10_000_000, 9_000_000)
    # Written `(at 1 0 90)`, so its absolute angle is 90 degrees; the footprint's own 90
    # degrees is already resolved into that number by KiCad and must not be added again.
    assert through_pad.rotation_udeg == 90_000_000
    assert through_pad.drill_x_nm == through_pad.drill_y_nm == 1_000_000
    assert through_pad.layer_ids == ("layer:F.Cu", "layer:B.Cu")

    assert content.segments[0].locked is True
    assert content.arcs[0].mid == PointNM(16_000_000, 10_000_000)
    assert content.vias[0].diameter_nm == 800_000
    assert content.vias[0].drill_nm == 400_000
    assert content.zones[0].clearance_nm == 200_000
    assert content.zones[0].thermal_gap_nm == 300_000
    assert content.zones[0].priority == 0
    assert content.zones[0].pad_connection is ZonePadConnection.THERMAL
    assert content.zones[0].island_removal is ZoneIslandRemoval.ALWAYS
    assert content.keepouts[0].prohibit_tracks is True
    assert content.keepouts[0].prohibit_vias is True
    assert content.keepouts[0].prohibit_pads is False
    assert content.keepouts[0].prohibit_zones is True
    assert content.keepouts[0].prohibit_footprints is False

    assignments = {
        assignment.net_id: assignment.net_class_id for assignment in content.constraints.assignments
    }
    assert assignments[net_id_for_name("SIG_µ")] == "class:audio"
    assert assignments[net_id_for_name("GND")] == "class:default"


def test_kicad_conversion_is_read_only_and_byte_deterministic() -> None:
    before = SUBSET_BOARD.read_bytes()
    before_mtime = SUBSET_BOARD.stat().st_mtime_ns

    first = parse_success(before, constraint_profile(assign_signal=True))
    second = parse_success(before, constraint_profile(assign_signal=True))

    assert SUBSET_BOARD.read_bytes() == before
    assert SUBSET_BOARD.stat().st_mtime_ns == before_mtime
    assert first == second
    assert encode_snapshot(first) == encode_snapshot(second)


def test_adapter_constraint_profile_changes_constraint_and_snapshot_digests() -> None:
    source = SUBSET_BOARD.read_bytes()
    first = parse_success(source, constraint_profile(clearance_nm=200_000))
    second = parse_success(source, constraint_profile(clearance_nm=350_000))

    assert first.content.source.revision == second.content.source.revision
    assert first.content.constraint_digest != second.content.constraint_digest
    assert first.snapshot_digest != second.snapshot_digest


def test_real_coppertone_board_maps_the_committed_audio_subset_exactly() -> None:
    source = COPPERTONE_BOARD.read_bytes()
    before_mtime = COPPERTONE_BOARD.stat().st_mtime_ns
    snapshot = parse_success(source, constraint_profile())
    content = snapshot.content

    assert COPPERTONE_BOARD.read_bytes() == source
    assert COPPERTONE_BOARD.stat().st_mtime_ns == before_mtime
    assert content.source.revision == f"sha256:{hashlib.sha256(source).hexdigest()}"
    assert content.source.format_version == "20260206"
    assert content.source.generator == "pcbnew"
    assert {net.name for net in content.nets} == {
        "GND",
        "9V_RAW",
        "VCC",
        "VREF",
        "R_IN_RAW",
        "R_IN_BIASED",
        "R_BUF",
        "R_ISO",
        "R_OUT",
        "L_IN_RAW",
        "L_IN_BIASED",
        "L_BUF",
        "L_ISO",
        "L_OUT",
    }
    assert (
        len(content.nets),
        len(content.pads),
        len(content.vias),
        len(content.segments),
        len(content.arcs),
        len(content.zones),
        len(content.keepouts),
    ) == (14, 55, 9, 53, 0, 2, 2)
    assert len(content.outline) == 1
    assert set(content.outline[0].outer.points) == {
        PointNM(0, 0),
        PointNM(52_000_000, 0),
        PointNM(52_000_000, 30_000_000),
        PointNM(0, 30_000_000),
    }
    assert all(zone.priority == 0 for zone in content.zones)
    assert all(zone.pad_connection is ZonePadConnection.THERMAL for zone in content.zones)
    assert all(zone.island_removal is ZoneIslandRemoval.ALWAYS for zone in content.zones)


Mutation = Callable[[bytes], bytes]


def _replace(source: bytes, old: bytes, new: bytes) -> bytes:
    mutated = source.replace(old, new, 1)
    assert mutated != source
    return mutated


def _replace_after(source: bytes, marker: bytes, old: bytes, new: bytes) -> bytes:
    """Replace one exact field after a stable fixture-local marker."""

    prefix, suffix = source.split(marker, 1)
    return prefix + marker + _replace(suffix, old, new)


def _insert_before(source: bytes, marker: bytes, expression: bytes) -> bytes:
    """Insert one exact S-expression before a stable fixture-local marker."""

    prefix, suffix = source.split(marker, 1)
    return prefix + expression + marker + suffix


def _insert_root(source: bytes, expression: bytes) -> bytes:
    closing = source.rfind(b"\n)")
    assert closing > 0
    return source[:closing] + b"\n  " + expression + source[closing:]


def _back_side_orthogonal_polygon_source(turn: int) -> bytes:
    """Return an asymmetric B.CrtYd polygon at one quarter-turn for mirror regressions."""

    source = FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes()
    if turn != 0:
        source = _replace(source, b"(at 60 20 0)", f"(at 60 20 {turn})".encode())
    rectangle = b"""(fp_rect
      (start -3 -2)
      (end 4 3)
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "B.CrtYd")
      (uuid "93000000-0000-0000-0000-000000000014")
    )"""
    polygon = b"""(fp_poly
      (pts
        (xy -3 -2) (xy 4 -2) (xy 4 3) (xy 1 3) (xy 1 0) (xy -3 0)
      )
      (stroke (width 0.05) (type default))
      (fill none)
      (layer "B.CrtYd")
      (uuid "93000000-0000-0000-0000-000000000014")
    )"""
    return _replace_after(
        source,
        b'(uuid "93000000-0000-0000-0000-000000000011")',
        rectangle,
        polygon,
    )


def test_v02_footprints_preserve_exact_pose_pad_ownership_and_lock() -> None:
    snapshot = parse_success(FOOTPRINT_V02_BOARD.read_bytes(), constraint_profile())

    assert {
        footprint.id: (
            footprint.origin,
            footprint.rotation_udeg,
            footprint.side.value,
            footprint.locked,
            footprint.pad_ids,
        )
        for footprint in snapshot.content.footprints
    } == {
        "footprint:kicad:92000000-0000-0000-0000-000000000001": (
            PointNM(15_000_000, 15_000_000),
            0,
            "front",
            True,
            (
                "pad:kicad:92000000-0000-0000-0000-000000000002",
                "pad:kicad:92000000-0000-0000-0000-000000000003",
            ),
        ),
        "footprint:kicad:92000000-0000-0000-0000-000000000011": (
            PointNM(45_000_000, 15_000_000),
            90_000_000,
            "front",
            False,
            (
                "pad:kicad:92000000-0000-0000-0000-000000000012",
                "pad:kicad:92000000-0000-0000-0000-000000000013",
            ),
        ),
        "footprint:kicad:92000000-0000-0000-0000-000000000021": (
            PointNM(15_000_000, 35_000_000),
            180_000_000,
            "front",
            False,
            (
                "pad:kicad:92000000-0000-0000-0000-000000000022",
                "pad:kicad:92000000-0000-0000-0000-000000000023",
            ),
        ),
        "footprint:kicad:92000000-0000-0000-0000-000000000031": (
            PointNM(45_000_000, 35_000_000),
            270_000_000,
            "front",
            False,
            (
                "pad:kicad:92000000-0000-0000-0000-000000000032",
                "pad:kicad:92000000-0000-0000-0000-000000000033",
            ),
        ),
    }

    assert {pad.id: (pad.center, pad.locked) for pad in snapshot.content.pads} == {
        "pad:kicad:92000000-0000-0000-0000-000000000002": (
            PointNM(14_000_000, 14_500_000),
            True,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000003": (
            PointNM(17_000_000, 16_000_000),
            True,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000012": (
            PointNM(44_500_000, 16_000_000),
            False,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000013": (
            PointNM(46_000_000, 13_000_000),
            False,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000022": (
            PointNM(16_000_000, 35_500_000),
            False,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000023": (
            PointNM(13_000_000, 34_000_000),
            False,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000032": (
            PointNM(45_500_000, 34_000_000),
            False,
        ),
        "pad:kicad:92000000-0000-0000-0000-000000000033": (
            PointNM(44_000_000, 37_000_000),
            False,
        ),
    }


def test_v02_rectangular_courtyards_transform_into_exact_board_coordinates() -> None:
    snapshot = parse_success(FOOTPRINT_V02_BOARD.read_bytes(), constraint_profile())

    assert {
        footprint.rotation_udeg: footprint.courtyards[0].points
        for footprint in snapshot.content.footprints
    } == {
        0: (
            PointNM(12_000_000, 13_000_000),
            PointNM(19_000_000, 13_000_000),
            PointNM(19_000_000, 17_500_000),
            PointNM(12_000_000, 17_500_000),
        ),
        90_000_000: (
            PointNM(43_000_000, 11_000_000),
            PointNM(47_500_000, 11_000_000),
            PointNM(47_500_000, 18_000_000),
            PointNM(43_000_000, 18_000_000),
        ),
        180_000_000: (
            PointNM(11_000_000, 32_500_000),
            PointNM(18_000_000, 32_500_000),
            PointNM(18_000_000, 37_000_000),
            PointNM(11_000_000, 37_000_000),
        ),
        270_000_000: (
            PointNM(42_500_000, 32_000_000),
            PointNM(47_000_000, 32_000_000),
            PointNM(47_000_000, 39_000_000),
            PointNM(42_500_000, 39_000_000),
        ),
    }


def test_v02_closed_orthogonal_line_chains_and_polygons_are_observed_exactly() -> None:
    """A KiCad-resaved fixture pins both accepted non-rectangular courtyard encodings."""

    snapshot = parse_success(ORTHOGONAL_COURTYARD_BOARD.read_bytes(), constraint_profile())
    courtyards = {
        footprint.id: footprint.courtyards[0].points for footprint in snapshot.content.footprints
    }
    assert courtyards == {
        "footprint:kicad:a3000000-0000-0000-0000-000000000001": (
            PointNM(11_000_000, 12_000_000),
            PointNM(19_000_000, 12_000_000),
            PointNM(19_000_000, 18_000_000),
            PointNM(16_000_000, 18_000_000),
            PointNM(16_000_000, 15_000_000),
            PointNM(14_000_000, 15_000_000),
            PointNM(14_000_000, 18_000_000),
            PointNM(11_000_000, 18_000_000),
        ),
        "footprint:kicad:a3000000-0000-0000-0000-000000000011": (
            PointNM(33_000_000, 13_000_000),
            PointNM(37_000_000, 13_000_000),
            PointNM(37_000_000, 17_000_000),
            PointNM(33_000_000, 17_000_000),
        ),
    }


def test_v02_open_or_diagonal_courtyard_line_chains_fail_closed() -> None:
    source = ORTHOGONAL_COURTYARD_BOARD.read_bytes()
    open_chain = _replace(source, b"(end -2 -2)", b"(end -2 -1)")
    diagonal = _replace(source, b"(end 2 -2)", b"(end 2 -1)")

    for mutated, expected in ((open_chain, "geometry.invalid"), (diagonal, "unsupported.topology")):
        result = parse_kicad_bytes(mutated, constraint_profile())
        assert result.snapshot is None
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].code == expected


def test_v02_branching_or_duplicate_courtyard_line_chain_fails_closed() -> None:
    source = ORTHOGONAL_COURTYARD_BOARD.read_bytes()
    marker = b'\t\t(pad "1" smd rect\n\t\t\t(at -1 0)'
    branch = b"""\t\t(fp_line
\t\t\t(start -2 -2)
\t\t\t(end -2 0)
\t\t\t(stroke
\t\t\t\t(width 0.05)
\t\t\t\t(type default)
\t\t\t)
\t\t\t(layer "F.CrtYd")
\t\t\t(uuid "a3000000-0000-0000-0000-000000000018")
\t\t)
"""
    duplicate = b"""\t\t(fp_line
\t\t\t(start -2 -2)
\t\t\t(end 2 -2)
\t\t\t(stroke
\t\t\t\t(width 0.05)
\t\t\t\t(type default)
\t\t\t)
\t\t\t(layer "F.CrtYd")
\t\t\t(uuid "a3000000-0000-0000-0000-000000000019")
\t\t)
"""

    for inserted in (branch, duplicate):
        result = parse_kicad_bytes(_insert_before(source, marker, inserted), constraint_profile())
        assert result.snapshot is None
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].code == "geometry.invalid"


def test_v02_self_intersecting_orthogonal_courtyard_polygon_fails_closed() -> None:
    source = ORTHOGONAL_COURTYARD_BOARD.read_bytes()
    self_intersecting = _replace(
        source,
        b"(xy -4 -3) (xy 4 -3) (xy 4 3) (xy 1 3) (xy 1 0) (xy -1 0) (xy -1 3) (xy -4 3)",
        b"(xy -3 -3) (xy 3 -3) (xy 3 3) (xy -1 3) (xy -1 -5) (xy 1 -5) (xy 1 1) (xy -3 1)",
    )

    result = parse_kicad_bytes(self_intersecting, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "geometry.self_intersection"


@pytest.mark.parametrize("primitive", (b"fp_arc", b"fp_curve"))
def test_v02_curved_courtyard_primitive_fails_closed(primitive: bytes) -> None:
    source = _replace(ORTHOGONAL_COURTYARD_BOARD.read_bytes(), b"fp_line", primitive)

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "unsupported.construct"


def test_v02_filled_courtyard_polygon_fails_closed() -> None:
    source = _replace(
        ORTHOGONAL_COURTYARD_BOARD.read_bytes(),
        b'(fill no)\n\t\t\t(layer "F.CrtYd")',
        b'(fill solid)\n\t\t\t(layer "F.CrtYd")',
    )

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "unsupported.construct"


def test_v02_ambiguous_or_malformed_courtyard_fields_fail_closed() -> None:
    source = ORTHOGONAL_COURTYARD_BOARD.read_bytes()
    ambiguous = _replace(
        source,
        b"(start -2 -2)\n\t\t\t(end 2 -2)",
        b"(start -2 -2)\n\t\t\t(start -2 -2)\n\t\t\t(end 2 -2)",
    )
    malformed = _replace(source, b"(end 2 -2)", b"(end 2)")

    for mutated, expected in ((ambiguous, "syntax.duplicate_field"), (malformed, "syntax.invalid")):
        result = parse_kicad_bytes(mutated, constraint_profile())
        assert result.snapshot is None
        assert len(result.diagnostics) == 1
        assert result.diagnostics[0].code == expected


@pytest.mark.parametrize(
    ("angle_degrees", "expected"),
    (
        (
            0,
            (
                PointNM(57_000_000, 18_000_000),
                PointNM(64_000_000, 18_000_000),
                PointNM(64_000_000, 23_000_000),
                PointNM(61_000_000, 23_000_000),
                PointNM(61_000_000, 20_000_000),
                PointNM(57_000_000, 20_000_000),
            ),
        ),
        (
            90,
            (
                PointNM(58_000_000, 16_000_000),
                PointNM(63_000_000, 16_000_000),
                PointNM(63_000_000, 19_000_000),
                PointNM(60_000_000, 19_000_000),
                PointNM(60_000_000, 23_000_000),
                PointNM(58_000_000, 23_000_000),
            ),
        ),
    ),
)
def test_v02_back_side_orthogonal_polygon_preserves_authored_coordinates(
    angle_degrees: int, expected: tuple[PointNM, ...]
) -> None:
    """Board-frame coordinates are transformed once, never mirrored because the side is back."""

    snapshot = parse_success(
        _back_side_orthogonal_polygon_source(angle_degrees), constraint_profile()
    )
    back = snapshot.content.footprints[1]

    assert back.side is FootprintSide.BACK
    assert back.rotation_udeg == angle_degrees * 1_000_000
    assert back.courtyards[0].points == expected


def test_v02_back_side_footprints_preserve_authored_pose_and_matching_courtyard() -> None:
    snapshot = parse_success(FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes(), constraint_profile())

    assert [footprint.side for footprint in snapshot.content.footprints] == [
        FootprintSide.FRONT,
        FootprintSide.BACK,
    ]
    assert [footprint.origin for footprint in snapshot.content.footprints] == [
        PointNM(20_000_000, 20_000_000),
        PointNM(60_000_000, 20_000_000),
    ]
    assert [pad.center for pad in snapshot.content.pads] == [
        PointNM(18_000_000, 19_000_000),
        PointNM(21_000_000, 22_000_000),
        PointNM(58_000_000, 19_000_000),
        PointNM(61_000_000, 22_000_000),
    ]
    assert [pad.layer_ids for pad in snapshot.content.pads] == [
        ("layer:F.Cu",),
        ("layer:F.Cu",),
        ("layer:B.Cu",),
        ("layer:B.Cu",),
    ]
    assert [footprint.courtyards[0].points for footprint in snapshot.content.footprints] == [
        (
            PointNM(17_000_000, 18_000_000),
            PointNM(24_000_000, 18_000_000),
            PointNM(24_000_000, 23_000_000),
            PointNM(17_000_000, 23_000_000),
        ),
        (
            PointNM(57_000_000, 18_000_000),
            PointNM(64_000_000, 18_000_000),
            PointNM(64_000_000, 23_000_000),
            PointNM(57_000_000, 23_000_000),
        ),
    ]


def test_v02_back_side_requires_a_matching_back_courtyard_layer() -> None:
    source = _replace(
        FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes(),
        b'(layer "B.CrtYd")',
        b'(layer "F.CrtYd")',
    )

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "unsupported.transform"
    assert "does not match its footprint side" in result.diagnostics[0].message


def test_v02_footprint_without_a_courtyard_has_an_explicit_empty_state() -> None:
    source = _replace(
        FOOTPRINT_V02_BOARD.read_bytes(),
        b'(layer "F.CrtYd")',
        b'(layer "F.SilkS")',
    )
    snapshot = parse_success(source, constraint_profile())

    assert snapshot.content.footprints[0].courtyards == ()
    assert all(len(footprint.courtyards) == 1 for footprint in snapshot.content.footprints[1:])


def test_v02_mismatched_courtyard_layer_fails_closed() -> None:
    source = _replace(
        FOOTPRINT_V02_BOARD.read_bytes(),
        b'(layer "F.CrtYd")',
        b'(layer "B.CrtYd")',
    )

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "unsupported.transform"
    assert "does not match its footprint side" in result.diagnostics[0].message


def test_v02_malformed_courtyard_line_fails_closed() -> None:
    source = _replace(FOOTPRINT_V02_BOARD.read_bytes(), b"    (fp_rect\n", b"    (fp_line\n")

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "unsupported.construct"
    assert "unsupported semantic field" in result.diagnostics[0].message


TWO_LAYER_STACK = b'    (0 "F.Cu" signal)\n    (2 "B.Cu" signal)'


def _with_copper_stack(stack: bytes) -> bytes:
    """Replace the subset board's two-layer copper stack with ``stack``.

    KiCad writes copper layers in physical stack order - ``F.Cu``, ``In1.Cu`` ... ``InN.Cu``,
    ``B.Cu`` - while numbering them ``F.Cu=0``, ``B.Cu=2``, ``InN.Cu=2+2N``, so the declared
    ordinals do not ascend.  See ``docs/research/kicad-copper-layer-numbering-v1.md``.
    """

    return _replace(SUBSET_BOARD.read_bytes(), TWO_LAYER_STACK, stack)


def _four_layer_source() -> bytes:
    return _with_copper_stack(
        b'    (0 "F.Cu" signal)\n'
        b'    (4 "In1.Cu" signal)\n'
        b'    (6 "In2.Cu" signal)\n'
        b'    (2 "B.Cu" signal)'
    )


def _six_layer_source() -> bytes:
    return _with_copper_stack(
        b'    (0 "F.Cu" signal)\n'
        b'    (4 "In1.Cu" signal)\n'
        b'    (6 "In2.Cu" power)\n'
        b'    (8 "In3.Cu" power)\n'
        b'    (10 "In4.Cu" signal)\n'
        b'    (2 "B.Cu" signal)'
    )


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda _source: MALFORMED_BOARD.read_bytes(), "syntax.invalid"),
        (lambda _source: b"\xff", "syntax.invalid"),
        (
            lambda source: _replace(source, b"(version 20260206)", b"(version 20270206)"),
            "unsupported.version",
        ),
        (
            lambda source: _replace(source, b"(version 20260206)", b"(version malformed)"),
            "unsupported.version",
        ),
        (
            lambda source: _replace(
                source,
                b'    (0 "F.Cu" signal)\n    (2 "B.Cu" signal)',
                b'    (2 "B.Cu" signal)\n    (0 "F.Cu" signal)',
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(source, b'(pad "1" smd roundrect', b'(pad "1" smd custom'),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(
                source,
                b"  (via\n    (at 17 9)",
                b"  (via blind\n    (at 17 9)",
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(source, b"(at 10 10 90)", b"(at 10 10 45)"),
            "unsupported.transform",
        ),
        (
            lambda source: _replace(
                source,
                '(layer "F.Cu")\n    (net "SIG_µ")\n    (locked yes)'.encode(),
                '(layer "In1.Cu")\n    (net "SIG_µ")\n    (locked yes)'.encode(),
            ),
            "unknown.layer",
        ),
        (
            lambda source: _replace(source, b"(width 0.25)", b"(width 0.0000001)"),
            "integer.precision",
        ),
        (
            lambda source: _replace(
                source,
                b"    (locked yes)\n    (uuid",
                b"    locked\n    (locked yes)\n    (uuid",
            ),
            "syntax.invalid",
        ),
        (
            lambda source: _replace(
                source,
                b"(roundrect_rratio 0.25)",
                b"(roundrect_rratio "
                b"0.250000000000000000000000000000000000000000000000000000000000001)",
            ),
            "integer.precision",
        ),
        (
            lambda source: _insert_root(
                source,
                b'(gr_text "hidden copper" (at 1 1) (layer "F.Cu"))',
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _insert_root(
                source,
                b"(general (legacy_teardrops yes))",
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(
                source,
                b'    (pad "1" smd roundrect',
                b'    (fp_circle (center 0 0) (end 1 0) (layer "Edge.Cuts"))\n'
                b'    (pad "1" smd roundrect',
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(
                source,
                b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))",
                b"(fill yes)",
            ),
            "syntax.missing_field",
        ),
        (
            lambda source: _replace(
                source,
                b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))",
                b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3) (island_removal_mode 2))",
            ),
            "unsupported.construct",
        ),
        (
            lambda source: _replace(
                source,
                b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))",
                b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3) (smoothing fillet))",
            ),
            "unsupported.construct",
        ),
    ],
)
def test_adapter_fails_closed_with_structured_diagnostics(
    mutation: Mutation, expected_code: str
) -> None:
    source = mutation(SUBSET_BOARD.read_bytes())
    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    diagnostic = result.diagnostics[0]
    assert diagnostic.code == expected_code
    assert diagnostic.severity is Severity.ERROR
    assert 1 <= len(diagnostic.message) <= 512
    assert diagnostic.source_locator


def test_zone_priority_connection_and_island_policy_are_preserved() -> None:
    source = SUBSET_BOARD.read_bytes()
    source = _replace(
        source,
        b"    (connect_pads (clearance 0.2))",
        b"    (priority 7)\n    (connect_pads yes (clearance 0.2))",
    )
    source = _replace(
        source,
        b"(fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))",
        b"(fill yes (island_removal_mode 1))",
    )

    zone = parse_success(source, constraint_profile(assign_signal=True)).content.zones[0]

    assert zone.priority == 7
    assert zone.pad_connection is ZonePadConnection.SOLID
    assert zone.island_removal is ZoneIslandRemoval.NEVER
    assert zone.thermal_gap_nm == 0
    assert zone.thermal_bridge_width_nm == 0


def test_quoted_numeric_net_name_is_not_a_legacy_net_ordinal() -> None:
    source = SUBSET_BOARD.read_bytes()
    source = _replace(source, b'  (footprint "Test:R"', b'  (net 1 "GND")\n  (footprint "Test:R"')
    source = source.replace('"SIG_µ"'.encode(), b'"1"')

    content = parse_success(source, constraint_profile()).content
    numeric_name_id = net_id_for_name("1")

    assert {net.name for net in content.nets} == {"1", "GND"}
    assert content.segments[0].net_id == numeric_name_id
    assert content.arcs[0].net_id == numeric_name_id
    assert content.vias[0].net_id == numeric_name_id
    assert any(pad.net_id == numeric_name_id for pad in content.pads)


@pytest.mark.parametrize(
    "item_head",
    ["segment", "arc", "via"],
)
def test_bare_negative_routing_net_code_is_not_treated_as_a_name(item_head: str) -> None:
    source = SUBSET_BOARD.read_bytes()
    marker = f"  ({item_head}\n".encode()
    item_start = source.index(marker)
    original_net = '    (net "SIG_µ")'.encode()
    net_start = source.index(original_net, item_start)
    source = source[:net_start] + b"    (net -1)" + source[net_start + len(original_net) :]

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "net.unknown"


def test_two_field_pad_net_code_uses_canonical_numeric_identity() -> None:
    source = _replace(
        SUBSET_BOARD.read_bytes(),
        b'  (footprint "Test:R"',
        b'  (net 1 "GND")\n  (footprint "Test:R"',
    )
    source = _replace(source, '      (net "SIG_µ")'.encode(), b'      (net 01 "SIG_\xc2\xb5")')

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "net.ambiguous"


def test_non_neutral_board_level_via_treatment_is_rejected() -> None:
    source = _replace(
        SUBSET_BOARD.read_bytes(),
        b'  (generator "pcbnew")',
        b'  (generator "pcbnew")\n  (setup (capping yes))',
    )

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"


def test_default_board_tenting_is_accepted_but_non_default_tenting_is_rejected() -> None:
    default_source = _replace(
        SUBSET_BOARD.read_bytes(),
        b'  (generator "pcbnew")',
        b'  (generator "pcbnew")\n  (setup (tenting (front yes) (back yes)))',
    )
    parse_success(default_source, constraint_profile(assign_signal=True))

    non_default_source = _replace(default_source, b"(front yes)", b"(front no)")
    result = parse_kicad_bytes(non_default_source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"


def test_unmodeled_setup_routing_constraints_are_rejected() -> None:
    source = _replace(
        SUBSET_BOARD.read_bytes(),
        b'  (generator "pcbnew")',
        b'  (generator "pcbnew")\n  (setup (defaults (edge_clearance 5)))',
    )

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"


def test_multiple_native_identity_fields_are_rejected() -> None:
    source = _replace(
        SUBSET_BOARD.read_bytes(),
        b'    (uuid "10000000-0000-0000-0000-000000000005")',
        b'    (uuid "10000000-0000-0000-0000-000000000005")\n'
        b'    (tstamp "deadbeef-0000-0000-0000-000000000005")',
    )

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "identity.ambiguous"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda source: _replace(
            source,
            b'  (footprint "Test:R"',
            b'  (SECRET_BEARER_TOKEN yes)\n  (footprint "Test:R"',
        ),
        lambda source: _replace(
            source,
            b'    (pad "1" smd roundrect',
            b'    (SECRET_BOARD_FIELD yes)\n    (pad "1" smd roundrect',
        ),
        lambda source: _replace(
            source,
            b'  (footprint "Test:R"',
            b'  (gr_SECRET_AUDIO_DESIGN (layer "F.Cu"))\n  (footprint "Test:R"',
        ),
        lambda source: _replace(
            source,
            b'    (0 "F.Cu" signal)',
            b"    (SECRET_LAYER_TOKEN (nested))",
        ),
    ],
)
def test_diagnostics_never_echo_attacker_controlled_construct_names(mutation: Mutation) -> None:
    result = parse_kicad_bytes(
        mutation(SUBSET_BOARD.read_bytes()), constraint_profile(assign_signal=True)
    )

    assert result.snapshot is None
    diagnostic = result.diagnostics[0]
    rendered = " ".join(
        value
        for value in (
            diagnostic.message,
            diagnostic.source_locator,
            diagnostic.object_kind,
            diagnostic.object_id,
        )
        if value is not None
    )
    assert "SECRET" not in rendered


def test_adapter_semantic_diagnostics_never_echo_attacker_controlled_ids() -> None:
    source = SUBSET_BOARD.read_bytes()
    source = _replace(
        source,
        b'    (uuid "10000000-0000-0000-0000-000000000008")',
        b'    (uuid "SECRET_AUDIO_DESIGN")',
    )
    source = _replace(
        source,
        b"      (pts (xy 1 1) (xy 39 1) (xy 39 29) (xy 1 29))",
        b"      (pts (xy 0 0) (xy 4 0) (xy 0 4) (xy 3 3))",
    )

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    diagnostic = result.diagnostics[0]
    rendered = " ".join(
        value
        for value in (
            diagnostic.message,
            diagnostic.source_locator,
            diagnostic.object_kind,
            diagnostic.object_id,
        )
        if value is not None
    ).casefold()
    assert diagnostic.code == "geometry.self_intersection"
    assert "secret_audio_design" not in rendered


def test_four_layer_board_uses_kicads_real_copper_numbering() -> None:
    """A four-layer stack as KiCad actually writes it converts to the physical stack order.

    KiCad numbers copper ``F.Cu=0``, ``B.Cu=2``, ``InN.Cu=2+2N`` and declares it front-to-back,
    so the ordinals in the file are ``0, 4, 6, 2`` - deliberately not ascending.  Board IR keeps
    ``index`` as the *declaration position*, so the IR stack stays dense and front-to-back.
    """

    content = parse_success(_four_layer_source(), constraint_profile(assign_signal=True)).content

    assert [(layer.id, layer.name, layer.index, layer.kind) for layer in content.copper_layers] == [
        ("layer:F.Cu", "F.Cu", 0, "signal"),
        ("layer:In1.Cu", "In1.Cu", 1, "signal"),
        ("layer:In2.Cu", "In2.Cu", 2, "signal"),
        ("layer:B.Cu", "B.Cu", 3, "signal"),
    ]


def test_six_layer_board_converts_with_inner_plane_layers() -> None:
    content = parse_success(_six_layer_source(), constraint_profile(assign_signal=True)).content

    assert [(layer.name, layer.index, layer.kind) for layer in content.copper_layers] == [
        ("F.Cu", 0, "signal"),
        ("In1.Cu", 1, "signal"),
        ("In2.Cu", 2, "plane"),
        ("In3.Cu", 3, "plane"),
        ("In4.Cu", 4, "signal"),
        ("B.Cu", 5, "signal"),
    ]


def test_single_inner_layer_board_converts() -> None:
    source = _with_copper_stack(
        b'    (0 "F.Cu" signal)\n    (4 "In1.Cu" signal)\n    (2 "B.Cu" signal)'
    )

    content = parse_success(source, constraint_profile(assign_signal=True)).content

    assert [layer.name for layer in content.copper_layers] == ["F.Cu", "In1.Cu", "B.Cu"]


@pytest.mark.parametrize(
    ("label", "stack"),
    [
        # The rule CopperMCP used to enforce (issue #104): position * 2.  ``In1.Cu = 2`` is
        # B.Cu's ordinal, so accepting it would silently misidentify the stack.
        (
            "coppermcp's old position-times-two numbering",
            b'    (0 "F.Cu" signal)\n'
            b'    (2 "In1.Cu" signal)\n'
            b'    (4 "In2.Cu" signal)\n'
            b'    (6 "B.Cu" signal)',
        ),
        # KiCad's own pre-V9 numbering: real, but not this format version's.  Ordinal 1 is
        # F.Mask and 31 is a technical layer under the numbering 20260206 boards use.
        (
            "kicad's pre-v9 numbering",
            b'    (0 "F.Cu" signal)\n'
            b'    (1 "In1.Cu" signal)\n'
            b'    (2 "In2.Cu" signal)\n'
            b'    (31 "B.Cu" signal)',
        ),
        (
            "numeric rather than physical declaration order",
            b'    (0 "F.Cu" signal)\n'
            b'    (2 "B.Cu" signal)\n'
            b'    (4 "In1.Cu" signal)\n'
            b'    (6 "In2.Cu" signal)',
        ),
        (
            "duplicate ordinal",
            b'    (0 "F.Cu" signal)\n'
            b'    (4 "In1.Cu" signal)\n'
            b'    (4 "In2.Cu" signal)\n'
            b'    (2 "B.Cu" signal)',
        ),
        (
            "duplicate front copper layer",
            b'    (0 "F.Cu" signal)\n'
            b'    (0 "F.Cu" signal)\n'
            b'    (6 "In2.Cu" signal)\n'
            b'    (2 "B.Cu" signal)',
        ),
        (
            "gap in the inner layer sequence",
            b'    (0 "F.Cu" signal)\n'
            b'    (4 "In1.Cu" signal)\n'
            b'    (8 "In3.Cu" signal)\n'
            b'    (2 "B.Cu" signal)',
        ),
        (
            "inner layer carrying an ordinal from another position",
            b'    (0 "F.Cu" signal)\n'
            b'    (6 "In1.Cu" signal)\n'
            b'    (4 "In2.Cu" signal)\n'
            b'    (2 "B.Cu" signal)',
        ),
        (
            "back copper that is not B.Cu",
            b'    (0 "F.Cu" signal)\n'
            b'    (4 "In1.Cu" signal)\n'
            b'    (6 "In2.Cu" signal)\n'
            b'    (8 "In3.Cu" signal)',
        ),
        (
            "front copper that is not F.Cu",
            b'    (4 "In1.Cu" signal)\n    (6 "In2.Cu" signal)\n    (2 "B.Cu" signal)',
        ),
        (
            "inner layer beyond KiCad's In30.Cu",
            b'    (0 "F.Cu" signal)\n    (64 "In31.Cu" signal)\n    (2 "B.Cu" signal)',
        ),
        (
            "front copper numbered as an inner layer",
            b'    (4 "F.Cu" signal)\n    (2 "B.Cu" signal)',
        ),
        (
            "back copper numbered under the old rule",
            b'    (0 "F.Cu" signal)\n'
            b'    (4 "In1.Cu" signal)\n'
            b'    (6 "In2.Cu" signal)\n'
            b'    (8 "B.Cu" signal)',
        ),
    ],
)
def test_malformed_copper_stacks_are_still_refused(label: str, stack: bytes) -> None:
    """Widening the rule to KiCad's real numbering must not widen it into accepting garbage."""

    result = parse_kicad_bytes(_with_copper_stack(stack), constraint_profile(assign_signal=True))

    assert result.snapshot is None, label
    assert [diagnostic.code for diagnostic in result.diagnostics] == ["unsupported.construct"], (
        label
    )
    assert result.diagnostics[0].object_kind == "layer", label


def test_stack_deeper_than_kicads_thirty_inner_layers_is_refused() -> None:
    """KiCad stops at ``In30.Cu``; a 33rd copper layer has no ID to check against.

    The positional name for that slot is ``In31.Cu``, which is absent from KiCad's table, so the
    entry is refused rather than extrapolated - even though `2+2N` would happily keep counting.
    """

    inner = b"".join(
        b'    (%d "In%d.Cu" signal)\n' % (2 + 2 * index, index) for index in range(1, 32)
    )
    source = _with_copper_stack(b'    (0 "F.Cu" signal)\n' + inner + b'    (2 "B.Cu" signal)')

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"
    assert result.diagnostics[0].object_kind == "layer"


def test_thirty_two_layer_stack_is_the_deepest_kicad_stack_and_converts() -> None:
    inner = b"".join(
        b'    (%d "In%d.Cu" signal)\n' % (2 + 2 * index, index) for index in range(1, 31)
    )
    source = _with_copper_stack(b'    (0 "F.Cu" signal)\n' + inner + b'    (2 "B.Cu" signal)')

    content = parse_success(source, constraint_profile(assign_signal=True)).content

    assert len(content.copper_layers) == 32
    assert [layer.name for layer in content.copper_layers[-2:]] == ["In30.Cu", "B.Cu"]
    assert [layer.index for layer in content.copper_layers] == list(range(32))


def test_board_without_back_copper_is_refused() -> None:
    result = parse_kicad_bytes(
        _with_copper_stack(b'    (0 "F.Cu" signal)'), constraint_profile(assign_signal=True)
    )

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unknown.layer"


def test_f_and_b_wildcard_excludes_inner_layers() -> None:
    source = _replace(
        _four_layer_source(),
        b'(layers "F.Cu" "B.Cu")',
        b'(layers "F&B.Cu")',
    )

    content = parse_success(source, constraint_profile(assign_signal=True)).content

    assert [layer.name for layer in content.copper_layers] == [
        "F.Cu",
        "In1.Cu",
        "In2.Cu",
        "B.Cu",
    ]
    assert content.vias[0].start_layer_id == "layer:F.Cu"
    assert content.vias[0].end_layer_id == "layer:B.Cu"
    assert content.keepouts[0].layer_ids == ("layer:F.Cu", "layer:B.Cu")


def test_partial_stack_via_is_rejected() -> None:
    source = _replace(
        _four_layer_source(),
        b'(layers "F.Cu" "B.Cu")',
        b'(layers "F.Cu" "In1.Cu")',
    )

    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True))

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"


def test_locked_positional_names_do_not_change_lock_state() -> None:
    source = SUBSET_BOARD.read_bytes()
    source = _replace(source, b'(footprint "Test:R"', b'(footprint "locked"')
    source = _replace(source, b'(pad "1" smd roundrect', b'(pad "locked" smd roundrect')

    content = parse_success(source, constraint_profile(assign_signal=True)).content
    signal_pad = next(pad for pad in content.pads if pad.net_id == net_id_for_name("SIG_µ"))

    assert signal_pad.locked is False


def test_streaming_sexpr_reader_stops_before_tokenizing_large_rejected_tail() -> None:
    source = b"(root " + b"a " * 500_000 + b")"
    limits = ParseLimits(
        max_input_bytes=2_000_000,
        max_children_per_list=8,
    )

    tracemalloc.start()
    try:
        with pytest.raises(SExprError) as caught:
            parse_sexpr(source, limits)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert caught.value.code == "budget.exceeded"
    assert peak < 12_000_000


def test_parser_deadline_checkpoints_while_scanning_a_large_quoted_atom() -> None:
    class DeadlineExpiredError(Exception):
        pass

    calls = 0

    def check_deadline() -> None:
        nonlocal calls
        calls += 1
        if calls >= 3:
            raise DeadlineExpiredError()

    with pytest.raises(DeadlineExpiredError):
        parse_sexpr(
            b'(root "' + b"x" * 16_384 + b'")',
            ParseLimits(max_atom_chars=32_768),
            check_deadline=check_deadline,
        )
    assert calls == 3


@pytest.mark.parametrize(
    "limits",
    [
        ParseLimits(max_input_bytes=64),
        ParseLimits(max_depth=4),
        ParseLimits(max_tokens=8),
        ParseLimits(max_nodes=8),
    ],
)
def test_parser_limits_fail_closed(limits: ParseLimits) -> None:
    source = SUBSET_BOARD.read_bytes()
    result = parse_kicad_bytes(source, constraint_profile(assign_signal=True), limits)

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == "budget.exceeded"


# Random untrusted bytes exercise the public fail-closed boundary without timing assertions.
@given(st.binary(min_size=0, max_size=256))
@settings(max_examples=80, deadline=None)
def test_bounded_random_bytes_never_escape_the_conversion_contract(source: bytes) -> None:
    result = parse_kicad_bytes(
        source,
        constraint_profile(),
        ParseLimits(
            max_input_bytes=256,
            max_depth=16,
            max_tokens=128,
            max_nodes=128,
            max_atom_chars=128,
            max_children_per_list=64,
            max_objects=64,
            max_vertices_per_ring=32,
            max_total_vertices=64,
            max_intersection_tests=128,
            max_diagnostics=4,
        ),
    )

    if result.snapshot is None:
        assert result.diagnostics
        assert all(diagnostic.severity is Severity.ERROR for diagnostic in result.diagnostics)
    else:
        assert not any(diagnostic.severity is Severity.ERROR for diagnostic in result.diagnostics)


# Ground truth for these positions is KiCad itself, not arithmetic in this repository. The
# committed `footprint-rotation.kicad_pcb` runs a track from each rotated footprint's pad "1"
# to a separate anchor pad on the same net. If a quarter turn were mirrored, that track would
# land on pad "2" — a different net — and KiCad would report a short plus an unconnected net.
# `test_real_kicad_confirms_the_footprint_rotation_ground_truth` asserts that it does not.
# The 0 and 180 degree cases are controls: they are identical under either convention, so only
# the 90 and 270 rows can actually discriminate.
_ROTATION_PAD_CENTERS = {
    "PROBE_0_A": PointNM(7_000_000, 12_000_000),
    "PROBE_0_B": PointNM(17_000_000, 12_000_000),
    "PROBE_90_A": PointNM(32_000_000, 17_000_000),
    "PROBE_90_B": PointNM(32_000_000, 7_000_000),
    "PROBE_180_A": PointNM(17_000_000, 28_000_000),
    "PROBE_180_B": PointNM(7_000_000, 28_000_000),
    "PROBE_270_A": PointNM(32_000_000, 23_000_000),
    "PROBE_270_B": PointNM(32_000_000, 33_000_000),
}


def _net_names(snapshot: BoardIRSnapshot) -> Callable[[Pad], str]:
    names = {net.id: net.name for net in snapshot.content.nets}

    def name_of(pad: Pad) -> str:
        return "" if pad.net_id is None else names.get(pad.net_id, "")

    return name_of


@pytest.mark.parametrize(("net_name", "expected"), sorted(_ROTATION_PAD_CENTERS.items()))
def test_footprint_rotation_matches_kicad_placement(net_name: str, expected: PointNM) -> None:
    snapshot = parse_success(ROTATION_BOARD.read_bytes(), constraint_profile())
    name_of = _net_names(snapshot)

    # Anchor pads sit outside the rotated footprints and are the only ones ending in "5".
    probe = [
        pad
        for pad in snapshot.content.pads
        if name_of(pad) == net_name and not pad.id.endswith("5")
    ]

    assert len(probe) == 1
    assert probe[0].center == expected


def test_footprint_rotation_keeps_pad_orientation_and_rejects_off_axis_turns() -> None:
    snapshot = parse_success(ROTATION_BOARD.read_bytes(), constraint_profile())
    name_of = _net_names(snapshot)
    rotations = {
        name_of(pad): pad.rotation_udeg
        for pad in snapshot.content.pads
        if name_of(pad).endswith("_B")
    }

    # Pad orientation composes the footprint and pad angles, which is unaffected by the
    # coordinate-mapping sign and is only ever consumed as a quarter-turn parity.
    assert rotations == {
        "PROBE_0_B": 0,
        "PROBE_90_B": 90_000_000,
        "PROBE_180_B": 180_000_000,
        "PROBE_270_B": 270_000_000,
    }

    off_axis = ROTATION_BOARD.read_bytes().replace(b"(at 32 12 90)", b"(at 32 12 45)")
    result = parse_kicad_bytes(off_axis, constraint_profile())
    assert result.snapshot is None
    assert any(
        "orthogonal footprint transforms" in diagnostic.message for diagnostic in result.diagnostics
    )


def test_coppertone_rotated_pads_are_consistent_with_its_clean_kicad_drc() -> None:
    """No foreign track may end inside a foreign pad, because KiCad reports no shorts.

    This is the argument that exposed the mirrored quarter turn: with the pads of every
    rotated two-pad footprint swapped, two tracks appeared to start inside a pad belonging to
    another net, which `kicad-cli pcb drc` contradicts by reporting zero violations.
    """

    snapshot = parse_success(COPPERTONE_BOARD.read_bytes(), constraint_profile())
    content = snapshot.content
    front_pads = [pad for pad in content.pads if "layer:F.Cu" in pad.layer_ids]

    def covered(pad: Pad) -> tuple[int, int, int, int]:
        size_x, size_y = pad.size_x_nm, pad.size_y_nm
        if pad.rotation_udeg // 90_000_000 % 2 == 1:
            size_x, size_y = size_y, size_x
        half_x, half_y = (size_x + 1) // 2, (size_y + 1) // 2
        return (
            pad.center.x - half_x,
            pad.center.y - half_y,
            pad.center.x + half_x,
            pad.center.y + half_y,
        )

    shorts = [
        (segment.id, pad.id)
        for segment in content.segments
        if segment.layer_id == "layer:F.Cu"
        for pad in front_pads
        if pad.net_id != segment.net_id
        for endpoint in (segment.start, segment.end)
        if covered(pad)[0] <= endpoint.x <= covered(pad)[2]
        and covered(pad)[1] <= endpoint.y <= covered(pad)[3]
    ]

    assert shorts == []


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_confirms_the_footprint_rotation_ground_truth(tmp_path: Path) -> None:
    """KiCad's own connectivity engine adjudicates the rotation convention."""

    board = tmp_path / ROTATION_BOARD.name
    board.write_bytes(ROTATION_BOARD.read_bytes())
    report = tmp_path / "drc.json"

    completed = subprocess.run(  # noqa: S603 - fixed local argv, trusted discovered CLI
        [
            str(REAL_KICAD_CLI),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    # Every probe track was drawn to the pad position this repository now predicts. A mirrored
    # quarter turn would put that track on the neighbouring pad, which is a different net.
    assert payload["violations"] == []
    assert payload["unconnected_items"] == []


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_accepts_front_and_back_observation_fixture(tmp_path: Path) -> None:
    """KiCad's DRC accepts both observed sides and the asymmetric geometry unchanged."""

    board = tmp_path / FRONT_BACK_FOOTPRINT_V02_BOARD.name
    board.write_bytes(FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes())
    report = tmp_path / "drc.json"

    completed = subprocess.run(  # noqa: S603 - fixed local argv, trusted discovered CLI
        [
            str(REAL_KICAD_CLI),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["violations"] == []
    assert payload["unconnected_items"] == []

    snapshot = parse_success(FRONT_BACK_FOOTPRINT_V02_BOARD.read_bytes(), constraint_profile())
    assert [footprint.side for footprint in snapshot.content.footprints] == [
        FootprintSide.FRONT,
        FootprintSide.BACK,
    ]


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_accepts_orthogonal_courtyard_chain_fixture(tmp_path: Path) -> None:
    """KiCad accepts the committed polygon and unordered-line source fixture unchanged."""

    board = tmp_path / ORTHOGONAL_COURTYARD_BOARD.name
    board.write_bytes(ORTHOGONAL_COURTYARD_BOARD.read_bytes())
    report = tmp_path / "drc.json"

    completed = subprocess.run(  # noqa: S603 - fixed local argv, trusted discovered CLI
        [
            str(REAL_KICAD_CLI),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        ],
        check=False,
        capture_output=True,
        timeout=120,
    )

    assert completed.returncode == 0
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert payload["violations"] == []
    assert payload["unconnected_items"] == []


def _drawn_rectangles(svg: bytes) -> dict[tuple[float, float], tuple[float, float]]:
    """Bounding box of every plotted path, keyed by its centre in millimetres.

    KiCad plots each pad as one closed path. A rounded or oval pad's bounding box is still
    its full rectangle, and a circle's is its diameter square, so comparing bounding boxes is
    valid for every shape Board IR models.
    """

    import re as _re

    boxes: dict[tuple[float, float], tuple[float, float]] = {}
    for data in _re.findall(rb'<path[^>]*d="([^"]+)"', svg):
        numbers = [float(value) for value in _re.findall(rb"-?\d+\.?\d*", data)]
        xs, ys = numbers[0::2], numbers[1::2]
        if not xs or not ys:
            continue
        centre = (round((min(xs) + max(xs)) / 2, 3), round((min(ys) + max(ys)) / 2, 3))
        boxes[centre] = (round(max(xs) - min(xs), 3), round(max(ys) - min(ys), 3))
    return boxes


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_pad_extents_match_the_geometry_kicad_actually_draws(tmp_path: Path) -> None:
    """Compare Board IR's pad extents against KiCad's own plotted geometry.

    This is the oracle the adapter lacked. Every previous rotation test compared the adapter
    with itself - ``parse(rotate(board))`` against ``rotate(parse(board))`` - which a
    consistently wrong convention satisfies perfectly. It also could not have discriminated
    anything, because the rotation fixture's pads were all square.

    The defect this pins: a pad's angle in a KiCad file is already resolved into the board
    frame, so adding its footprint's rotation counted the turn twice and transposed the
    extents of every non-square pad on a rotated footprint.
    """

    from copper_mcp.config import Settings
    from copper_mcp.kicad_cli import run_scene_render

    board = tmp_path / "footprint-rotation.kicad_pcb"
    shutil.copy2(ROTATION_BOARD, board)
    _, svg = run_scene_render(board.name, Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI))
    drawn = _drawn_rectangles(svg)

    snapshot = parse_success(ROTATION_BOARD.read_bytes(), constraint_profile())
    compared = 0
    oblong = 0
    for pad in snapshot.content.pads:
        width_nm, height_nm = pad.size_x_nm, pad.size_y_nm
        if pad.rotation_udeg // 90_000_000 % 2 == 1:
            width_nm, height_nm = height_nm, width_nm
        centre = (round(pad.center.x / 1e6, 3), round(pad.center.y / 1e6, 3))
        plotted = drawn.get(centre)
        assert plotted is not None, f"KiCad plotted no pad centred on {centre}"
        assert plotted == pytest.approx((width_nm / 1e6, height_nm / 1e6), abs=1e-3), (
            f"pad {pad.id} at {centre}: Board IR says "
            f"{width_nm / 1e6}x{height_nm / 1e6}, KiCad drew {plotted}"
        )
        compared += 1
        if pad.size_x_nm != pad.size_y_nm:
            oblong += 1

    assert compared >= 8, "the oracle must actually have compared some pads"
    assert oblong >= 2, (
        "the fixture must contain non-square pads, or this oracle cannot tell a correct "
        "rotation convention from a transposed one"
    )


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_the_pad_extent_oracle_would_reject_the_double_counted_rotation(tmp_path: Path) -> None:
    """Guard the guard: re-introducing the defect must make the oracle fail."""

    from copper_mcp.config import Settings
    from copper_mcp.kicad_cli import run_scene_render

    board = tmp_path / "footprint-rotation.kicad_pcb"
    shutil.copy2(ROTATION_BOARD, board)
    _, svg = run_scene_render(board.name, Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI))
    drawn = _drawn_rectangles(svg)

    snapshot = parse_success(ROTATION_BOARD.read_bytes(), constraint_profile())
    footprint_turns = {"OBLONG_ROT": 90_000_000}
    names = _net_names(snapshot)
    disagreements = 0
    for pad in snapshot.content.pads:
        added = footprint_turns.get(names(pad))
        if added is None or pad.size_x_nm == pad.size_y_nm:
            continue
        # The old behaviour: footprint rotation added on top of the pad's own angle.
        doubled = (pad.rotation_udeg + added) % 360_000_000
        width_nm, height_nm = pad.size_x_nm, pad.size_y_nm
        if doubled // 90_000_000 % 2 == 1:
            width_nm, height_nm = height_nm, width_nm
        centre = (round(pad.center.x / 1e6, 3), round(pad.center.y / 1e6, 3))
        if drawn.get(centre) != pytest.approx((width_nm / 1e6, height_nm / 1e6), abs=1e-3):
            disagreements += 1

    assert disagreements > 0, (
        "the double-counted rotation would still match KiCad, so this fixture cannot "
        "detect the defect"
    )


def test_coppertone_has_one_residual_pad_bounding_box_overlap_and_it_is_a_rounding_artifact() -> (
    None
):
    """Pin the real board's overlap count, which the rotation fix moved from six to one.

    ``kicad-cli pcb drc`` reports zero violations on this board, so every different-net pad
    overlap found by axis-aligned bounding boxes is an artifact rather than real copper.
    Before the fix there were six, all caused by transposed extents on rotated footprints.

    The single survivor is an oval against a roundrect whose *bounding boxes* clip at a
    corner that both shapes round away. That is the expected direction of error: a bounding
    box over-approximates a rounded pad, so "boxes do not overlap" proves the pads do not,
    while "boxes overlap" proves nothing. Placement legality has to respect that asymmetry.
    """

    snapshot = parse_success(COPPERTONE_BOARD.read_bytes(), constraint_profile())
    pads = list(snapshot.content.pads)

    def bounds(pad: Pad) -> tuple[int, int, int, int]:
        width_nm, height_nm = pad.size_x_nm, pad.size_y_nm
        if pad.rotation_udeg // 90_000_000 % 2 == 1:
            width_nm, height_nm = height_nm, width_nm
        return (
            pad.center.x - width_nm // 2,
            pad.center.y - height_nm // 2,
            pad.center.x + width_nm // 2,
            pad.center.y + height_nm // 2,
        )

    overlapping: list[tuple[PadShape, PadShape]] = []
    for index, first in enumerate(pads):
        for second in pads[index + 1 :]:
            if not set(first.layer_ids) & set(second.layer_ids):
                continue
            if first.net_id is not None and first.net_id == second.net_id:
                continue
            left, right = bounds(first), bounds(second)
            if not (
                left[2] <= right[0]
                or right[2] <= left[0]
                or left[3] <= right[1]
                or right[3] <= left[1]
            ):
                overlapping.append((first.shape, second.shape))

    assert len(overlapping) == 1, overlapping
    assert set(overlapping[0]) == {PadShape.OVAL, PadShape.ROUNDRECT}


# ---------------------------------------------------------------------------
# Edge.Cuts outlines assembled from gr_line segments (issue #111)
# ---------------------------------------------------------------------------

EDGE_CUTS_RECTANGLE = (
    b"  (gr_rect\n"
    b"    (start 0 0)\n"
    b"    (end 40 30)\n"
    b"    (stroke (width 0.1) (type default))\n"
    b"    (fill no)\n"
    b'    (layer "Edge.Cuts")\n'
    b'    (uuid "10000000-0000-0000-0000-000000000004")\n'
    b"  )\n"
)

RECTANGLE_EDGES = (
    ("0 0", "40 0"),
    ("40 0", "40 30"),
    ("40 30", "0 30"),
    ("0 30", "0 0"),
)

L_SHAPED_EDGES = (
    ("0 0", "40 0"),
    ("40 0", "40 10"),
    ("40 10", "15 10"),
    ("15 10", "15 30"),
    ("15 30", "0 30"),
    ("0 30", "0 0"),
)

TRUE_RECTANGLE = (
    PointNM(0, 0),
    PointNM(40_000_000, 0),
    PointNM(40_000_000, 30_000_000),
    PointNM(0, 30_000_000),
)

TRUE_L_SHAPE = (
    PointNM(0, 0),
    PointNM(40_000_000, 0),
    PointNM(40_000_000, 10_000_000),
    PointNM(15_000_000, 10_000_000),
    PointNM(15_000_000, 30_000_000),
    PointNM(0, 30_000_000),
)


def _edge_cuts_lines(edges: tuple[tuple[str, str], ...], *, head: str = "gr_line") -> bytes:
    """Render unordered ``Edge.Cuts`` segment graphics exactly as KiCad writes them."""

    rendered = b""
    for index, (start, end) in enumerate(edges):
        rendered += (
            f"  ({head}\n    (start {start})\n    (end {end})\n".encode()
            + b"    (stroke (width 0.1) (type default))\n"
            + b'    (layer "Edge.Cuts")\n'
            + f'    (uuid "20000000-0000-0000-0000-{index:012d}")\n'.encode()
            + b"  )\n"
        )
    return rendered


def _segment_outline_source(edges: tuple[tuple[str, str], ...], *, extra: bytes = b"") -> bytes:
    """Replace the subset board's single ``gr_rect`` outline with segment graphics."""

    return _replace(SUBSET_BOARD.read_bytes(), EDGE_CUTS_RECTANGLE, _edge_cuts_lines(edges) + extra)


def _double_area(points: tuple[PointNM, ...]) -> int:
    total = 0
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        total += start.x * end.y - end.x * start.y
    return abs(total)


def _inside_or_on(polygon: tuple[tuple[int, int], ...], point: tuple[int, int]) -> bool:
    """Exact integer point-in-polygon test; a boundary point counts as contained."""

    x, y = point
    inside = False
    for index, (ax, ay) in enumerate(polygon):
        bx, by = polygon[(index + 1) % len(polygon)]
        if (bx - ax) * (y - ay) - (by - ay) * (x - ax) == 0 and (
            min(ax, bx) <= x <= max(ax, bx) and min(ay, by) <= y <= max(ay, by)
        ):
            return True
        if (ay > y) != (by > y):
            side = (bx - ax) * (y - ay) - (x - ax) * (by - ay)
            if (side > 0) == (by > ay):
                inside = not inside
    return inside


def _properly_crosses(
    a: tuple[int, int], b: tuple[int, int], c: tuple[int, int], d: tuple[int, int]
) -> bool:
    def turn(p: tuple[int, int], q: tuple[int, int], r: tuple[int, int]) -> int:
        cross = (q[0] - p[0]) * (r[1] - p[1]) - (q[1] - p[1]) * (r[0] - p[0])
        return (cross > 0) - (cross < 0)

    return turn(a, b, c) * turn(a, b, d) < 0 and turn(c, d, a) * turn(c, d, b) < 0


def _is_contained(inner: tuple[PointNM, ...], outer: tuple[PointNM, ...]) -> bool:
    """Exact integer containment test for two simple polygons, boundary included.

    Coordinates are doubled so that edge midpoints stay exact integers: no float appears in
    any predicate.  ``inner`` is contained in ``outer`` when no edge of one properly crosses
    an edge of the other and every ``inner`` vertex *and* edge midpoint lies inside or on
    ``outer``.  Sampling midpoints as well as vertices is what rules out an ``inner`` edge
    that leaves ``outer`` between two shared vertices.
    """

    scaled_outer = tuple((point.x * 2, point.y * 2) for point in outer)
    scaled_inner = tuple((point.x * 2, point.y * 2) for point in inner)
    for index, start in enumerate(scaled_inner):
        end = scaled_inner[(index + 1) % len(scaled_inner)]
        for other, third in enumerate(scaled_outer):
            fourth = scaled_outer[(other + 1) % len(scaled_outer)]
            if _properly_crosses(start, end, third, fourth):
                return False
    probes: list[tuple[int, int]] = []
    for index, first in enumerate(inner):
        second = inner[(index + 1) % len(inner)]
        probes.append((first.x * 2, first.y * 2))
        probes.append((first.x + second.x, first.y + second.y))
    return all(_inside_or_on(scaled_outer, probe) for probe in probes)


def _canonical_ring(points: tuple[PointNM, ...]) -> tuple[PointNM, ...]:
    """Normalize winding and start vertex so two orderings of one ring compare equal."""

    total = 0
    for index, start in enumerate(points):
        end = points[(index + 1) % len(points)]
        total += start.x * end.y - end.x * start.y
    ordered = points if total > 0 else tuple(reversed(points))
    least = min(range(len(ordered)), key=lambda index: ordered[index])
    return ordered[least:] + ordered[:least]


def test_edge_cuts_rectangle_drawn_as_four_lines_converts_and_is_inscribed() -> None:
    """Four ``gr_line`` segments are how KiCad users actually draw a rectangular board.

    Direction of error: the outline is routing *room*, not an obstacle, so the modelled
    contour must be contained within the outline the board actually draws.  A modelled
    outline one nanometre too large hands the router copper the fabricated board does not
    have.  For straight segments that containment is exact - the assembled ring's vertices
    are the drawn endpoints and nothing is synthesized - which is the strongest form of
    "never larger", and the area equality below pins it.
    """

    snapshot = parse_success(_segment_outline_source(RECTANGLE_EDGES), constraint_profile())

    assert len(snapshot.content.outline) == 1
    modelled = snapshot.content.outline[0].outer.points
    assert set(modelled) == set(TRUE_RECTANGLE)
    assert _is_contained(modelled, TRUE_RECTANGLE)
    assert _double_area(modelled) <= _double_area(TRUE_RECTANGLE)
    assert _double_area(modelled) == _double_area(TRUE_RECTANGLE)
    assert snapshot.content.outline[0].id.startswith("contour:")


def test_edge_cuts_l_shaped_segment_outline_converts_inscribed() -> None:
    """Most real boards are not rectangles.  An L-shape has to survive, still inscribed."""

    snapshot = parse_success(_segment_outline_source(L_SHAPED_EDGES), constraint_profile())

    modelled = snapshot.content.outline[0].outer.points
    assert len(modelled) == 6
    assert set(modelled) == set(TRUE_L_SHAPE)
    assert _is_contained(modelled, TRUE_L_SHAPE)
    assert _double_area(modelled) <= _double_area(TRUE_L_SHAPE)
    assert not _is_contained(TRUE_RECTANGLE, modelled), (
        "the L-shape must not be modelled as its bounding rectangle"
    )


def test_edge_cuts_segment_order_and_direction_do_not_move_the_outline() -> None:
    """KiCad writes the segments in drawing order, in whichever direction they were drawn.

    The real four-layer board that motivated this fix writes its four edges as
    ``(0,0)->(159,0)``, ``(0,150)->(0,0)``, ``(159,0)->(159,150)``, ``(159,150)->(0,150)``:
    neither contiguous nor consistently wound.  The assembled ring must therefore depend on
    the segment *set*, not on file order and not on the direction each segment was drawn.
    """

    scrambled = (
        ("40 30", "0 30"),
        ("0 0", "40 0"),
        ("0 0", "0 30"),
        ("40 0", "40 30"),
    )

    ordered = parse_success(_segment_outline_source(L_SHAPED_EDGES), constraint_profile())
    flipped = parse_success(
        _segment_outline_source(tuple(reversed([(end, start) for start, end in L_SHAPED_EDGES]))),
        constraint_profile(),
    )
    assert _canonical_ring(ordered.content.outline[0].outer.points) == _canonical_ring(
        flipped.content.outline[0].outer.points
    )

    straightforward = parse_success(_segment_outline_source(RECTANGLE_EDGES), constraint_profile())
    shuffled = parse_success(_segment_outline_source(scrambled), constraint_profile())
    assert _canonical_ring(straightforward.content.outline[0].outer.points) == _canonical_ring(
        shuffled.content.outline[0].outer.points
    )


OPEN_CONTOUR_EDGES = RECTANGLE_EDGES[:3]

NEAR_MISS_EDGES = (
    ("0 0", "40 0"),
    ("40 0", "40 30"),
    ("40 30", "0 30"),
    ("0 30", "0 0.01"),
)

# A closed, non-branching, single-component chain whose fourth edge crosses its second.  The
# crossing is not a vertex, so degree alone cannot see it: the ring's own simplicity contract is
# what refuses it.  The quadrilateral is deliberately asymmetric, because a symmetric bowtie has
# zero signed area and would be refused for that instead.
SELF_INTERSECTING_EDGES = (
    ("0 0", "40 0"),
    ("40 0", "0 30"),
    ("0 30", "30 20"),
    ("30 20", "0 0"),
)

DISJOINT_LOOP_EDGES = (
    *RECTANGLE_EDGES,
    ("50 0", "60 0"),
    ("60 0", "60 10"),
    ("60 10", "50 10"),
    ("50 10", "50 0"),
)

ZERO_LENGTH_EDGES = (*RECTANGLE_EDGES, ("20 0", "20 0"))

DUPLICATE_EDGES = (*RECTANGLE_EDGES, ("40 0", "0 0"))

BRANCHING_EDGES = (
    ("0 0", "20 0"),
    ("20 0", "40 0"),
    ("40 0", "40 30"),
    ("40 30", "0 30"),
    ("0 30", "0 0"),
    ("20 0", "20 15"),
)


@pytest.mark.parametrize(
    ("edges", "expected_code", "expected_message"),
    [
        (OPEN_CONTOUR_EDGES, "geometry.invalid", "closed"),
        (NEAR_MISS_EDGES, "geometry.invalid", "closed"),
        (SELF_INTERSECTING_EDGES, "geometry.self_intersection", ""),
        (DISJOINT_LOOP_EDGES, "unsupported.topology", "disjoint"),
        (ZERO_LENGTH_EDGES, "geometry.invalid", "zero-length"),
        (DUPLICATE_EDGES, "geometry.invalid", "duplicate"),
        (BRANCHING_EDGES, "geometry.invalid", "closed"),
    ],
    ids=[
        "open-contour",
        "near-miss-gap",
        "self-intersecting",
        "two-disjoint-loops",
        "zero-length-segment",
        "duplicate-segment",
        "branching-spur",
    ],
)
def test_malformed_segment_outlines_are_refused_with_a_typed_code(
    edges: tuple[tuple[str, str], ...], expected_code: str, expected_message: str
) -> None:
    """Refuse honestly instead of repairing.

    Every one of these is a shape a human can draw by accident, and for each there is a
    plausible "helpful" repair - snap the gap, drop the spur, keep the biggest loop.  Each
    repair invents board area the drawn geometry does not enclose, which is the one direction
    of error this contour may not take, so each is a typed refusal instead.  The near-miss gap
    is 10 um: inside KiCad's own outline chaining epsilon, and refused here anyway, because
    closing it would enlarge the modelled board.
    """

    result = parse_kicad_bytes(_segment_outline_source(edges), constraint_profile())

    assert result.snapshot is None
    assert len(result.diagnostics) == 1
    assert result.diagnostics[0].code == expected_code
    assert expected_message in result.diagnostics[0].message


def test_edge_cuts_arc_outline_stays_refused_with_an_honest_diagnostic() -> None:
    """Arcs are deliberately out of scope for this slice, and the refusal has to say so.

    ADR-0072's sagitta bound over-approximates an arc, which is right for an obstacle and
    exactly backwards for an outline: an outline arc needs an *inscribed* approximation, and
    whether a chord is inscribed depends on which side of the ring the arc bulges toward.
    Rather than guess, the adapter refuses - and names arcs, so a caller can tell this apart
    from an unsupported layer or a malformed loop.
    """

    arc = (
        b"  (gr_arc\n"
        b"    (start 40 0)\n"
        b"    (mid 41 15)\n"
        b"    (end 40 30)\n"
        b"    (stroke (width 0.1) (type default))\n"
        b'    (layer "Edge.Cuts")\n'
        b'    (uuid "30000000-0000-0000-0000-000000000001")\n'
        b"  )\n"
    )
    source = _segment_outline_source(
        (RECTANGLE_EDGES[0], RECTANGLE_EDGES[2], RECTANGLE_EDGES[3]), extra=arc
    )

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"
    assert "arc" in result.diagnostics[0].message
    assert result.diagnostics[0].object_kind == "outline"


def test_mixed_rectangle_and_segment_outlines_are_refused() -> None:
    """One board, one outline.  A rectangle plus a segment loop is two, so it refuses."""

    source = _replace(
        SUBSET_BOARD.read_bytes(),
        EDGE_CUTS_RECTANGLE,
        EDGE_CUTS_RECTANGLE
        + _edge_cuts_lines(
            (
                ("50 0", "60 0"),
                ("60 0", "60 10"),
                ("60 10", "50 10"),
                ("50 10", "50 0"),
            )
        ),
    )

    result = parse_kicad_bytes(source, constraint_profile())

    assert result.snapshot is None
    assert result.diagnostics[0].code == "unsupported.construct"
    assert result.diagnostics[0].object_kind == "outline"


def test_segment_outline_charges_a_segment_budget() -> None:
    """Work is bounded by a declared budget rather than by the board being reasonable."""

    split_bottom_edge = (
        ("0 0", "20 0"),
        ("20 0", "40 0"),
        ("40 0", "40 30"),
        ("40 30", "0 30"),
        ("0 30", "0 0"),
    )

    result = parse_kicad_bytes(
        _segment_outline_source(split_bottom_edge),
        constraint_profile(),
        ParseLimits(max_vertices_per_ring=4),
    )

    assert result.snapshot is None
    assert result.diagnostics[0].code == "budget.exceeded"
    assert "Edge.Cuts outline segment budget" in result.diagnostics[0].message


def test_pathological_segment_outline_hits_a_budget_instead_of_spinning() -> None:
    """Two thousand collinear sub-segments still form a closed loop, and still bounded work.

    The ring simplicity test is quadratic in its vertices, so a board that splits one edge
    into thousands of pieces has to charge - and exhaust - the intersection-test budget
    rather than run to completion.
    """

    steps = 2_000
    edges = [("0 0", "40 0"), ("40 0", "40 30")]
    edges += [
        (
            f"{40 - index * 40 / steps:.6f} 30",
            f"{40 - (index + 1) * 40 / steps:.6f} 30",
        )
        for index in range(steps)
    ]
    edges.append(("0 30", "0 0"))

    result = parse_kicad_bytes(
        _segment_outline_source(tuple(edges)),
        constraint_profile(),
        ParseLimits(max_intersection_tests=10_000),
    )

    assert result.snapshot is None
    assert result.diagnostics[0].code == "budget.exceeded"
