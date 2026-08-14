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


def test_render_preserves_non_orthogonal_board_frame_pad_angle() -> None:
    source = FIXTURE.read_bytes().replace(b"(at -1 -0.5 90)", b"(at -1 -0.5 45)", 1)
    source, snapshot, profile, candidate, _ = _candidate(source)

    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    # The parent footprint moves by 90 degrees, but the pad's original 45-degree board-frame
    # angle is preserved as 135 degrees instead of being rejected as a non-orthogonal footprint.
    assert b"(at -1 -0.5 135)" in rendered
    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    target_pad = next(
        pad
        for pad in patched.snapshot.content.pads
        if pad.id.endswith(":kicad:92000000-0000-0000-0000-000000000012")
    )
    assert target_pad.rotation_udeg == 135_000_000


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


SEGMENT_OUTLINE_FIXTURE = (
    Path(__file__).parent
    / "fixtures"
    / "board-ir-v0.2"
    / "footprint-pose-courtyard-segment-outline.kicad_pcb"
)


def test_render_applies_on_a_board_whose_outline_is_assembled_from_gr_lines() -> None:
    """The issue #126 unblocking case, proven at the byte level.

    Every one of the real boards that converted drew its ``Edge.Cuts`` outline with
    ``gr_line`` segments, and the assembled contour's revision-derived identity refused the
    whole board even when every footprint, pad, segment, via and zone was native.  With the
    composite identity of ADR-0087 the outline is native too, so the placement patch renders
    -- and every byte outside the spliced footprint pose stays identical, including all four
    ``gr_line`` expressions and their uuids.
    """

    source, snapshot, profile, candidate, subject = _candidate(SEGMENT_OUTLINE_FIXTURE.read_bytes())
    assert snapshot.content.outline[0].id.startswith("contour:assembled:")

    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert rendered != source
    target_start = source.index(
        b'  (footprint "CopperMCP_PoseProbe"',
        source.index(b'  (footprint "CopperMCP_PoseProbe"') + 1,
    )
    next_footprint = source.index(b'  (footprint "CopperMCP_PoseProbe"', target_start + 1)
    assert rendered.startswith(source[:target_start])
    assert rendered.endswith(source[next_footprint:])
    assert rendered.count(b"gr_line") == source.count(b"gr_line")

    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert patched.snapshot.content.outline[0].id == snapshot.content.outline[0].id
    moved = next(item for item in patched.snapshot.content.footprints if item.id == subject)
    assert (moved.origin.x, moved.origin.y) != (45_000_000, 15_000_000)


def test_render_still_refuses_the_assembled_outline_board_with_a_reused_footprint_uuid() -> None:
    """Regression evidence from issue #126: one duplicated footprint uuid still refuses.

    The assembled-outline identity must not weaken D-158: a footprint uuid claimed twice is an
    identity of neither claimant, both degrade to the revision-derived name, and the apply gate
    keeps refusing the board even though its outline is now native.
    """

    source = SEGMENT_OUTLINE_FIXTURE.read_bytes().replace(
        b'(uuid "92000000-0000-0000-0000-000000000011")',
        b'(uuid "92000000-0000-0000-0000-000000000001")',
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert ":derived:" not in conversion.snapshot.content.outline[0].id
    view = build_placement_view(source, conversion.snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {"board": "reused.kicad_pcb", "constraints": CONSTRAINTS, "subjects": refs}
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    assert result.candidate is not None
    with pytest.raises(KiCadPlacementPatchError, match="revision-derived"):
        render_kicad_placement_candidate_board(
            source, conversion.snapshot, result.candidate, profile
        )


def test_render_still_refuses_when_an_outline_member_has_no_native_identity() -> None:
    """Mutation check on the ADR-0087 fallback: an unnameable member set stays unappliable.

    Dropping one ``gr_line`` uuid makes the member set unresolvable, the contour degrades to
    the revision-derived name, and the gate must refuse.  If the degrade-to-derived fallback in
    ``_assembled_contour_identity`` were removed, this board would render and this test is the
    one that fails.
    """

    source = (
        b"\n".join(
            line
            for line in SEGMENT_OUTLINE_FIXTURE.read_bytes().splitlines()
            if b'uuid "92000000-0000-0000-0000-0000000000a3"' not in line
        )
        + b"\n"
    )
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert ":derived:" in conversion.snapshot.content.outline[0].id
    view = build_placement_view(source, conversion.snapshot)
    refs = sorted(view.footprints)
    intent = parse_placement_intent(
        {"board": "member-missing.kicad_pcb", "constraints": CONSTRAINTS, "subjects": refs}
    )
    result = evaluate_placement(intent, conversion.snapshot, view)
    assert result.candidate is not None
    with pytest.raises(KiCadPlacementPatchError, match="revision-derived"):
        render_kicad_placement_candidate_board(
            source, conversion.snapshot, result.candidate, profile
        )


# Shaped as KiCad 10 writes a board group: quoted name, the group's own uuid, member UUIDs.
# The members are the fixture's own footprint identities, so this is a real selection.
_ROOT_GROUP = (
    b'  (group "Input stage"\n'
    b'    (uuid "5d4757ce-239e-4b95-9019-069ab6718878")\n'
    b'    (members "92000000-0000-0000-0000-000000000010"\n'
    b'      "92000000-0000-0000-0000-000000000020")\n'
    b"  )\n"
)


def _grouped_source() -> bytes:
    """The placement fixture with one root ``(group ...)`` before its closing delimiter."""

    source = FIXTURE.read_bytes()
    closing = source.rfind(b"\n)")
    assert closing > 0
    return source[:closing] + b"\n" + _ROOT_GROUP.rstrip(b"\n") + source[closing:]


def test_a_placement_splice_leaves_a_root_group_byte_identical() -> None:
    """Write-back over a grouped board is verified, not assumed.

    Issue #129 left this open: a group's membership is by UUID, a member's UUID does not change
    when it moves, and the patch path is byte-preserving outside its target expressions -- so a
    group *should* survive a placement splice verbatim. "Should" is not evidence. This renders a
    real move on a board carrying a group and asserts the group's bytes are unchanged, so the
    conservative fallback issue #129 named -- refusing write-back on any grouped board -- is not
    needed. If a future splice ever rewrites the tail of the document, this fails.
    """

    source = _grouped_source()
    source, snapshot, profile, candidate, subject = _candidate(source)

    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert rendered != source
    assert rendered.count(_ROOT_GROUP.rstrip(b"\n")) == 1
    # Byte-exact, and in the same place: everything after the moved footprint is untouched.
    assert (
        rendered[rendered.index(_ROOT_GROUP.rstrip(b"\n")) :]
        == source[source.index(_ROOT_GROUP.rstrip(b"\n")) :]
    )

    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert patched.unmodelled_group_count == 1
    moved = next(item for item in patched.snapshot.content.footprints if item.id == subject)
    assert moved.origin.x == 47_250_000
    assert moved.origin.y == 15_500_000


def test_a_placement_splice_leaves_an_edge_connector_pad_token_intact() -> None:
    """The `connect` token a conversion discards must survive write-back byte-for-byte.

    ADR-0096 maps `connect` onto `PadKind.SMD`, so Board IR no longer records which pads were
    edge connectors. That is only tolerable because the *file* still does, and this is the
    evidence rather than the assertion: the splice rewrites the moved footprint's own `at` and
    every one of its pads' `at` expressions, so a pad in the moved footprint is the hardest case
    the write-back path offers. If a future splice ever re-emitted a pad header from Board IR, it
    would write `smd` over `connect`, silently adding solder paste to an edge-connector finger and
    changing KiCad's own DRC and fabrication output. This fails first.
    """

    source = FIXTURE.read_bytes().replace(b'(pad "2" smd rect', b'(pad "2" connect rect')
    assert source.count(b"connect") == 4

    source, snapshot, profile, candidate, subject = _candidate(source)
    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert rendered != source
    # Every token survives, including the one on the footprint the splice actually moved.
    assert rendered.count(b'(pad "2" connect rect') == 4

    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert patched.edge_connector_pad_count == 4
    moved = next(item for item in patched.snapshot.content.footprints if item.id == subject)
    assert (moved.origin.x, moved.origin.y) != (0, 0)


def test_a_placement_splice_preserves_a_thermal_bridge_angle_token_byte_exactly() -> None:
    """Moving a footprint cannot re-emit or normalize an unmodelled thermal-spoke angle."""

    expression = b"(thermal_bridge_angle -45.5)"
    source = FIXTURE.read_bytes().replace(
        b'      (uuid "92000000-0000-0000-0000-000000000012")',
        b"      " + expression + b'\n      (uuid "92000000-0000-0000-0000-000000000012")',
        1,
    )
    assert source.count(expression) == 1
    source, snapshot, profile, candidate, _ = _candidate(source)

    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert rendered != source
    assert rendered.count(expression) == 1
    patched = parse_kicad_bytes(rendered, profile)
    assert patched.snapshot is not None
    assert patched.unmodelled_thermal_bridge_angle_pad_count == 1


_ROOT_BOARD_PROPERTY = b'(property "Fabricator" "two-layer, 1.6 mm, lead-free")'


def test_a_placement_splice_leaves_a_root_board_property_byte_identical() -> None:
    """The board's text-variable map is not modelled, so a write-back must carry it verbatim.

    Board IR holds no text-variable map, so a splice that rebuilt the document from the snapshot
    would silently delete one. The patch path is byte-preserving outside the moved footprint's own
    expressions, which is what makes accepting the construct safe on the *write* side as well --
    but "should" is not evidence, so this renders a real move over a board carrying a property and
    asserts its bytes, and the whole document tail from it onward, are unchanged.
    """

    source = FIXTURE.read_bytes()
    closing = source.rfind(b"\n)")
    assert closing > 0
    source = source[:closing] + b"\n  " + _ROOT_BOARD_PROPERTY + source[closing:]
    source, snapshot, profile, candidate, subject = _candidate(source)

    rendered = render_kicad_placement_candidate_board(source, snapshot, candidate, profile)

    assert rendered != source
    assert rendered.count(_ROOT_BOARD_PROPERTY) == 1
    assert (
        rendered[rendered.index(_ROOT_BOARD_PROPERTY) :]
        == source[source.index(_ROOT_BOARD_PROPERTY) :]
    )

    patched = parse_kicad_bytes(rendered, profile)
    assert patched.diagnostics == ()
    assert patched.snapshot is not None
    assert patched.unmodelled_board_property_count == 1
    moved = next(item for item in patched.snapshot.content.footprints if item.id == subject)
    assert moved.origin.x == 47_250_000
    assert moved.origin.y == 15_500_000
