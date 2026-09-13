"""Private v2 pose edits; legacy apply acceptance remains unchanged."""

from dataclasses import replace

import pytest
from test_kicad_placement_patch import FIXTURE, _candidate

from copper_mcp.adapters import kicad_placement_patch as patch
from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.cst import span
from copper_mcp.adapters.sexpr import children, parse_sexpr
from copper_mcp.placement.contracts import finalise_candidate


def _edit_footprint(source, index, update):
    text = source.decode()
    expression = children(parse_sexpr(source), "footprint")[index]
    start, end = span(expression, text)
    return (text[:start] + update(text[start:end]) + text[end:]).encode()


def _back(source, index):
    return _edit_footprint(source, index, lambda text: text.replace('"F.', '"B.'))


def _render(source, snapshot, profile, candidate, subject):
    return patch._render_optimization_placement_candidate_board(
        source, snapshot, candidate, profile, movable_footprint_refs=(subject,)
    )


def test_front_move_preserves_unchanged_back_footprint_byte_exactly():
    source = _back(FIXTURE.read_bytes(), 0)
    source, snapshot, profile, candidate, subject = _candidate(source)
    with pytest.raises(patch.KiCadPlacementPatchError, match="front-side"):
        patch.render_kicad_placement_candidate_board(source, snapshot, candidate, profile)
    rendered = _render(source, snapshot, profile, candidate, subject)
    prefix = source[: source.index(b"(footprint", source.index(b"(footprint") + 1)]
    assert rendered.startswith(prefix)
    assert parse_kicad_bytes(rendered, profile).snapshot is not None


@pytest.mark.parametrize("rotation", [0, 90_000_000, 180_000_000, 270_000_000])
def test_back_pose_uses_saved_local_coordinates_without_second_mirror(rotation):
    source = _back(FIXTURE.read_bytes(), 1)
    source, snapshot, profile, candidate, subject = _candidate(source, orientation_udeg=rotation)
    rendered = _render(source, snapshot, profile, candidate, subject)
    observed = parse_kicad_bytes(rendered, profile).snapshot
    assert observed is not None
    fp = next(item for item in observed.content.footprints if item.id == subject)
    assert fp.side.value == "back" and fp.rotation_udeg == rotation
    pad = next(item for item in observed.content.pads if item.id.endswith("000000000012"))
    offsets = (
        (-1_000_000, -500_000),
        (-500_000, 1_000_000),
        (1_000_000, 500_000),
        (500_000, -1_000_000),
    )
    dx, dy = offsets[rotation // 90_000_000]
    assert (pad.center.x, pad.center.y) == (fp.origin.x + dx, fp.origin.y + dy)
    assert pad.rotation_udeg == rotation
    with pytest.raises(patch.KiCadPlacementPatchError):
        patch.render_kicad_placement_candidate_board(source, snapshot, candidate, profile)


@pytest.mark.parametrize("property_name", ["Reference", "ki_keywords", "ki_description"])
def test_known_metadata_local_graphics_and_absolute_text_angles(property_name):
    extra = """
      (descr "µ local metadata") (tags "owned") (attr smd)
      (property "Reference" "Rµ" (at 3 4 45) (layer "F.SilkS")
        (effects (font (size 1 1) (thickness 0.15))))
      (fp_text user "local" (at -3 4 315 unlocked) (layer "F.Fab")
        (effects (font (size 1 1) (thickness 0.15))))
      (fp_line (start -2 0) (end 2 0) (stroke (width 0.1) (type solid))
        (layer "F.Fab") (uuid "91000000-0000-0000-0000-000000000111"))
      (fp_circle (center 0 0) (end 1 0) (stroke (width 0.05) (type solid))
        (fill none) (layer "B.CrtYd") (uuid "91000000-0000-0000-0000-000000000112"))
    """
    extra = extra.replace('(property "Reference"', f'(property "{property_name}"')
    source = _edit_footprint(FIXTURE.read_bytes(), 1, lambda text: text[:-1] + extra + ")")
    source, snapshot, profile, candidate, subject = _candidate(source)
    rendered = _render(source, snapshot, profile, candidate, subject)
    assert b"(at 3 4 135)" in rendered
    assert b"(at -3 4 45 unlocked)" in rendered
    assert "µ local metadata".encode() in rendered
    assert b"(fp_line (start -2 0) (end 2 0)" in rendered
    observed = parse_kicad_bytes(rendered, profile).snapshot
    assert observed is not None
    fp = next(item for item in observed.content.footprints if item.id == subject)
    assert fp.far_side_courtyard_circles[0].center == fp.origin
    with pytest.raises(patch.KiCadPlacementPatchError):
        patch.render_kicad_placement_candidate_board(source, snapshot, candidate, profile)


@pytest.mark.parametrize("property_name", ["ki_fp_filters", "Footprint"])
def test_non_field_property_exemptions_remain_byte_identical(property_name):
    extra = (
        f'(property "{property_name}" "owned" (at 3 4 45) (layer "F.Fab") '
        "(effects (font (size 1 1) (thickness 0.15))))"
    )
    source = _edit_footprint(FIXTURE.read_bytes(), 1, lambda text: text[:-1] + extra + ")")
    source, snapshot, profile, candidate, subject = _candidate(source)
    rendered = _render(source, snapshot, profile, candidate, subject)
    assert extra.encode() in rendered


@pytest.mark.parametrize(
    "extra",
    [
        '(fp_text user "opaque" (at 0 0) (layer "F.Fab") (render_cache "opaque" 0))',
        '(fp_future (at 1 2) (layer "F.Fab"))',
        '(fp_text_box "box" (start 0 0) (end 1 1) (angle 90) (layer "F.Fab"))',
    ],
)
def test_opaque_unmoved_expression_is_preserved_but_moved_expression_refuses(extra):
    source = _edit_footprint(FIXTURE.read_bytes(), 0, lambda text: text[:-1] + extra + ")")
    source, snapshot, profile, candidate, subject = _candidate(source)
    rendered = _render(source, snapshot, profile, candidate, subject)
    assert extra.encode() in rendered
    changed = _edit_footprint(FIXTURE.read_bytes(), 1, lambda text: text[:-1] + extra + ")")
    source, snapshot, profile, candidate, subject = _candidate(changed)
    with pytest.raises(patch.KiCadPlacementPatchError):
        _render(source, snapshot, profile, candidate, subject)


def test_private_pose_entry_preserves_scope_side_lock_and_full_revision_checks():
    source, snapshot, profile, candidate, subject = _candidate(_back(FIXTURE.read_bytes(), 1))
    with pytest.raises(patch.KiCadPlacementPatchError):
        patch._render_optimization_placement_candidate_board(
            source, snapshot, candidate, profile, movable_footprint_refs=()
        )
    changed = tuple(
        replace(row, side="front") if row.ref_id == subject else row for row in candidate.placements
    )
    flipped = finalise_candidate(replace(candidate, placements=changed))
    with pytest.raises(patch.KiCadPlacementPatchError):
        _render(source, snapshot, profile, flipped, subject)
    with pytest.raises(patch.KiCadPlacementPatchError):
        _render(source + b"\n", snapshot, profile, candidate, subject)
