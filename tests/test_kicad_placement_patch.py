from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import (
    KiCadPlacementPatchError,
    render_kicad_placement_candidate_board,
)
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.placement import (
    build_placement_view,
    evaluate_placement,
    parse_placement_intent,
)
from copper_mcp.placement.contracts import finalise_candidate

FIXTURE = (
    Path(__file__).parent / "fixtures" / "board-ir-v0.2" / "footprint-pose-courtyard.kicad_pcb"
)
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate(
    source: bytes | None = None,
    *,
    offset_x_nm: int = 2_000_000,
    offset_y_nm: int = 1_000_000,
    orientation_udeg: int = 180_000_000,
) -> tuple[bytes, object, KiCadConstraintProfile, object, str]:
    source = FIXTURE.read_bytes() if source is None else source
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    view = build_placement_view(source, snapshot)
    refs = sorted(view.footprints)
    subject = refs[1]
    intent = parse_placement_intent(
        {
            "board": "placement-fixture.kicad_pcb",
            "constraints": CONSTRAINTS,
            "subjects": refs,
            "proposals": [
                {
                    "subject": subject,
                    "offset_x_nm": offset_x_nm,
                    "offset_y_nm": offset_y_nm,
                    "orientation_udeg": orientation_udeg,
                }
            ],
        }
    )
    result = evaluate_placement(intent, snapshot, view)
    assert result.candidate is not None
    return source, snapshot, profile, result.candidate, subject


def test_render_is_deterministic_source_preserving_and_round_trips() -> None:
    source, snapshot, profile, candidate, subject = _candidate()
    first = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)
    second = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert first == second
    assert source == FIXTURE.read_bytes()
    assert first != source
    assert b"(at 47.25 15.5 180)" in first
    # The absolute pad angles are rotated with their parent; the other three footprints' source
    # blocks remain byte-for-byte unchanged.
    assert b"(at -1 -0.5 180)" in first
    assert b"(at 2 1 180)" in first
    target_start = source.index(
        b'  (footprint "CopperMCP_PoseProbe"',
        source.index(b'  (footprint "CopperMCP_PoseProbe"') + 1,
    )
    next_footprint = source.index(b'  (footprint "CopperMCP_PoseProbe"', target_start + 1)
    assert first.startswith(source[:target_start])
    assert first.endswith(source[next_footprint:])
    assert b'uuid "92000000-0000-0000-0000-000000000011"' in first

    patched = parse_kicad_bytes(first, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    moved = next(item for item in patched.snapshot.content.footprints if item.id == subject)
    assert moved.origin.x == 47_250_000
    assert moved.origin.y == 15_500_000
    assert moved.rotation_udeg == 180_000_000
    assert patched.snapshot.content.source.revision != snapshot.content.source.revision


def test_render_rejects_stale_tampered_unknown_and_budgeted_inputs() -> None:
    source, snapshot, profile, candidate, _ = _candidate()
    with pytest.raises(KiCadPlacementPatchError, match="stale"):
        render_kicad_placement_candidate_board(
            source,
            snapshot,
            replace(candidate, base_revision="sha256:" + "0" * 64),
            profile,
        )
    with pytest.raises(KiCadPlacementPatchError, match="identity verification"):
        render_kicad_placement_candidate_board(
            source,
            snapshot,
            replace(candidate, candidate_id="sha256:" + "0" * 64),
            profile,
        )
    with pytest.raises(KiCadPlacementPatchError, match="stale"):
        render_kicad_placement_candidate_board(
            source,
            snapshot,
            replace(candidate, view_revision="sha256:" + "1" * 64),
            profile,
        )

    incomplete = finalise_candidate(replace(candidate, placements=candidate.placements[1:]))
    with pytest.raises(KiCadPlacementPatchError, match="footprint set"):
        render_kicad_placement_candidate_board(source, snapshot, incomplete, profile)

    malformed_moved = finalise_candidate(
        replace(
            candidate,
            placements=(replace(candidate.placements[0], moved=1), *candidate.placements[1:]),
        )
    )
    with pytest.raises(KiCadPlacementPatchError, match="moved flag"):
        render_kicad_placement_candidate_board(source, snapshot, malformed_moved, profile)

    unknown = replace(
        candidate.placements[1],
        ref_id="footprint:kicad:ffffffff-ffff-ffff-ffff-ffffffffffff",
    )
    placements = tuple(
        sorted(
            (candidate.placements[0], unknown, *candidate.placements[2:]),
            key=lambda item: item.ref_id,
        )
    )
    forged = finalise_candidate(replace(candidate, placements=placements))
    with pytest.raises(KiCadPlacementPatchError, match="unknown Board IR footprint"):
        render_kicad_placement_candidate_board(source, snapshot, forged, profile)

    with pytest.raises(KiCadPlacementPatchError, match="input-byte budget"):
        render_kicad_placement_candidate_board(
            source,
            snapshot,
            candidate,
            profile,
            limits=ParseLimits(max_input_bytes=len(source) - 1),
        )


def test_render_rejects_locked_and_unsupported_footprint_geometry() -> None:
    source, snapshot, profile, candidate, _ = _candidate()
    locked = replace(
        candidate.placements[0],
        origin_x_nm=candidate.placements[0].origin_x_nm + 1_000_000,
        moved=True,
    )
    locked_candidate = finalise_candidate(
        replace(candidate, placements=(locked, *candidate.placements[1:]))
    )
    with pytest.raises(KiCadPlacementPatchError, match="locked"):
        render_kicad_placement_candidate_board(source, snapshot, locked_candidate, profile)

    unsupported_source = source.replace(b'(layer "F.CrtYd")', b'(layer "F.Fab")', 1)
    conversion = parse_kicad_bytes(unsupported_source, profile)
    assert conversion.snapshot is not None
    unsupported_snapshot = conversion.snapshot
    view = build_placement_view(unsupported_source, unsupported_snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {"board": "unsupported.kicad_pcb", "constraints": CONSTRAINTS, "subjects": refs}
    )
    result = evaluate_placement(intent, unsupported_snapshot, view)
    assert result.candidate is not None
    with pytest.raises(KiCadPlacementPatchError, match=r"F\.CrtYd"):
        render_kicad_placement_candidate_board(
            unsupported_source, unsupported_snapshot, result.candidate, profile
        )


def test_render_rejects_revision_derived_footprint_identity() -> None:
    source, _, profile, _, _ = _candidate()
    identityless = (
        b"\n".join(
            line
            for line in source.splitlines()
            if b'uuid "92000000-0000-0000-0000-000000000011"' not in line
        )
        + b"\n"
    )
    conversion = parse_kicad_bytes(identityless, profile)
    assert conversion.snapshot is not None
    view = build_placement_view(identityless, conversion.snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {"board": "identityless.kicad_pcb", "constraints": CONSTRAINTS, "subjects": refs}
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    assert result.candidate is not None
    with pytest.raises(KiCadPlacementPatchError, match="revision-derived"):
        render_kicad_placement_candidate_board(
            identityless, conversion.snapshot, result.candidate, profile
        )


def test_render_preserves_padless_footprint_when_candidate_covers_placeable_set() -> None:
    source = (FIXTURE.parent / "padless-footprint.kicad_pcb").read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    view = build_placement_view(source, conversion.snapshot)
    refs = sorted(view.footprints)
    assert len(refs) == 1
    intent = parse_placement_intent(
        {
            "board": "padless-footprint.kicad_pcb",
            "constraints": CONSTRAINTS,
            "subjects": refs,
            "proposals": [
                {
                    "subject": refs[0],
                    "offset_x_nm": 2_000_000,
                    "offset_y_nm": 1_000_000,
                    "orientation_udeg": 90_000_000,
                }
            ],
        }
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    assert result.candidate is not None
    assert tuple(item.ref_id for item in result.candidate.placements) == tuple(refs)

    first = render_kicad_placement_candidate_board(
        source, conversion.snapshot, result.candidate, profile
    )
    second = render_kicad_placement_candidate_board(
        source, conversion.snapshot, result.candidate, profile
    )
    assert first == second
    padless_start = source.index(b'  (footprint "CopperMCP_MechanicalMark"')
    padless_end = source.index(b"  (gr_rect", padless_start)
    padless_block = source[padless_start:padless_end]
    assert first.count(padless_block) == 1
