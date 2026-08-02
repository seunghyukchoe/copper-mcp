from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    NetClass,
    PadShape,
    ParseLimits,
    PointNM,
    Severity,
    encode_snapshot,
)

TEST_ROOT = Path(__file__).parent
REPOSITORY_ROOT = TEST_ROOT.parent
SUBSET_BOARD = TEST_ROOT / "fixtures" / "board-ir-v0.1" / "subset.kicad_pcb"
MALFORMED_BOARD = TEST_ROOT / "fixtures" / "board-ir-v0.1" / "malformed-unbalanced.kicad_pcb"
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
    assert signal_pad.center == PointNM(10_000_000, 9_000_000)
    assert signal_pad.rotation_udeg == 90_000_000
    assert signal_pad.shape is PadShape.ROUNDRECT
    assert signal_pad.roundrect_radius_nm == 250_000
    assert signal_pad.layer_ids == ("layer:F.Cu",)
    assert through_pad.center == PointNM(10_000_000, 11_000_000)
    assert through_pad.rotation_udeg == 180_000_000
    assert through_pad.drill_x_nm == through_pad.drill_y_nm == 1_000_000
    assert through_pad.layer_ids == ("layer:F.Cu", "layer:B.Cu")

    assert content.segments[0].locked is True
    assert content.arcs[0].mid == PointNM(16_000_000, 10_000_000)
    assert content.vias[0].diameter_nm == 800_000
    assert content.vias[0].drill_nm == 400_000
    assert content.zones[0].clearance_nm == 200_000
    assert content.zones[0].thermal_gap_nm == 300_000
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


Mutation = Callable[[bytes], bytes]


def _replace(source: bytes, old: bytes, new: bytes) -> bytes:
    mutated = source.replace(old, new, 1)
    assert mutated != source
    return mutated


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        (lambda _source: MALFORMED_BOARD.read_bytes(), "syntax.invalid"),
        (lambda _source: b"\xff", "syntax.invalid"),
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
