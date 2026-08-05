"""Deterministic, source-preserving KiCad serialization for placement candidates.

This adapter deliberately supports only the narrow Board IR 0.2 footprint subset: front-side,
orthogonal footprints whose graphics are unfilled ``fp_rect`` courtyard centerlines.  It edits
the footprint pose and the absolute pad angles in place, returning disposable bytes and never
writing the caller's board.  Every output is parsed back through the Board IR adapter and checked
against the source snapshot so an unsupported construct cannot be silently carried through.
"""

from __future__ import annotations

import hashlib
from dataclasses import replace

from copper_mcp.adapters.cst import CstError, Splice, apply_splices, line_indent, span
from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExpr, SExprError, atoms, child, children, parse_sexpr
from copper_mcp.board_ir import (
    BoardIRSnapshot,
    Footprint,
    FootprintSide,
    Pad,
    ParseLimits,
    PointNM,
    Ring,
    mm_to_nm,
    nm_to_mm,
    normalize_rotation_udeg,
)
from copper_mcp.board_ir.canonical import normalize_content
from copper_mcp.placement.contracts import (
    ORIENTATIONS,
    PlacementCandidate,
    verify_placement_id,
)

_FULL_ROTATION_UDEG = 360_000_000
_NATIVE_ID_HEADS = frozenset({"uuid", "tstamp"})
_ALLOWED_FOOTPRINT_HEADS = frozenset({"at", "fp_rect", "layer", "locked", "pad", "tstamp", "uuid"})
_ALLOWED_RECT_HEADS = frozenset(
    {"end", "fill", "layer", "locked", "start", "stroke", "tstamp", "uuid"}
)


class KiCadPlacementPatchError(ValueError):
    """Raised when a placement candidate cannot be rendered safely."""


def _fail(message: str) -> None:
    raise KiCadPlacementPatchError(message)


def _native_identity(expression: SExpr, *, kind: str, locator: str) -> str:
    identities: list[str] = []
    for head in _NATIVE_ID_HEADS:
        field = child(expression, head)
        if field is None:
            continue
        values = atoms(field)
        if len(values) != 1:
            _fail(f"{locator}: native identity is malformed")
        identities.append(values[0].lower())
    if len(identities) != 1:
        _fail(f"{locator}: modeled geometry requires exactly one native identity")
    return f"{kind}:kicad:{identities[0]}"


def _require_native_geometry_identities(snapshot: BoardIRSnapshot) -> None:
    content = snapshot.content
    groups = (
        content.outline,
        content.footprints,
        content.pads,
        content.vias,
        content.segments,
        content.arcs,
        content.zones,
        content.keepouts,
    )
    if any(":derived:" in item.id for group in groups for item in group):
        _fail("source geometry uses revision-derived identities")


def _pose(expression: SExpr, locator: str) -> tuple[int, int, int]:
    field = child(expression, "at")
    if field is None:
        raise KiCadPlacementPatchError(f"{locator}: footprint/pad has no pose")
    values = atoms(field)
    if len(values) not in {2, 3}:
        _fail(f"{locator}: pose must contain x, y, and optional rotation")
    try:
        x = mm_to_nm(values[0])
        y = mm_to_nm(values[1])
        rotation = normalize_rotation_udeg(values[2] if len(values) == 3 else "0")
    except ValueError as error:
        raise KiCadPlacementPatchError(f"{locator}: pose is not exact decimal geometry") from error
    return x, y, rotation


def _rotation_degrees(rotation_udeg: int) -> int:
    if rotation_udeg not in ORIENTATIONS:
        _fail("placement candidate contains a non-orthogonal rotation")
    return rotation_udeg // 1_000_000


def _rotation_token(rotation_udeg: int) -> str:
    """Render any exact Board IR angle for a pad, including non-orthogonal angles."""

    whole, fraction = divmod(rotation_udeg, 1_000_000)
    if fraction == 0:
        return str(whole)
    return f"{whole}.{fraction:06d}".rstrip("0")


def _render_pose(x_nm: int, y_nm: int, rotation_udeg: int) -> str:
    return f"(at {nm_to_mm(x_nm)} {nm_to_mm(y_nm)} {_rotation_degrees(rotation_udeg)})"


def _render_pad_pose(x_nm: int, y_nm: int, rotation_udeg: int) -> str:
    return f"(at {nm_to_mm(x_nm)} {nm_to_mm(y_nm)} {_rotation_token(rotation_udeg)})"


def _quarter_turn(point: PointNM, turn: int) -> PointNM:
    return (
        point,
        PointNM(point.y, -point.x),
        PointNM(-point.x, -point.y),
        PointNM(-point.y, point.x),
    )[turn]


def _move_local(
    point: PointNM, old: Footprint, new_x: int, new_y: int, new_rotation: int
) -> PointNM:
    old_turn = (old.rotation_udeg // 90_000_000) % 4
    new_turn = (new_rotation // 90_000_000) % 4
    delta = PointNM(point.x - old.origin.x, point.y - old.origin.y)
    local = _quarter_turn(delta, (-old_turn) % 4)
    rotated = _quarter_turn(local, new_turn)
    return PointNM(new_x + rotated.x, new_y + rotated.y)


def _move_ring(ring: Ring, old: Footprint, new_x: int, new_y: int, new_rotation: int) -> Ring:
    return Ring(tuple(_move_local(point, old, new_x, new_y, new_rotation) for point in ring.points))


def _expected_content(
    snapshot: BoardIRSnapshot,
    placements: dict[str, tuple[int, int, int]],
    patched_source_revision: str,
) -> object:
    footprints: list[Footprint] = []
    for footprint in snapshot.content.footprints:
        pose = placements.get(footprint.id)
        if pose is None:
            footprints.append(footprint)
            continue
        x, y, rotation = pose
        footprints.append(
            replace(
                footprint,
                origin=PointNM(x, y),
                rotation_udeg=rotation,
                courtyards=tuple(
                    _move_ring(ring, footprint, x, y, rotation) for ring in footprint.courtyards
                ),
            )
        )

    pads: list[Pad] = []
    footprint_by_pad = {
        pad_id: footprint
        for footprint in snapshot.content.footprints
        for pad_id in footprint.pad_ids
    }
    for pad in snapshot.content.pads:
        footprint = footprint_by_pad[pad.id]
        pose = placements.get(footprint.id)
        if pose is None:
            pads.append(pad)
            continue
        x, y, rotation = pose
        delta = (rotation - footprint.rotation_udeg) % _FULL_ROTATION_UDEG
        pads.append(
            replace(
                pad,
                center=_move_local(pad.center, footprint, x, y, rotation),
                rotation_udeg=(pad.rotation_udeg + delta) % _FULL_ROTATION_UDEG,
            )
        )

    source = replace(snapshot.content.source, revision=patched_source_revision)
    return normalize_content(
        replace(
            snapshot.content,
            source=source,
            footprints=tuple(sorted(footprints, key=lambda item: item.id)),
            pads=tuple(sorted(pads, key=lambda item: item.id)),
        )
    )


def _validate_footprint_expression(expression: SExpr, locator: str) -> None:
    if expression.head != "footprint":
        _fail(f"{locator}: expected a footprint expression")
    if any(
        isinstance(item, SExpr) and item.head not in _ALLOWED_FOOTPRINT_HEADS
        for item in expression.items[1:]
    ):
        _fail(f"{locator}: footprint contains unsupported pose-carrying syntax")
    layer = child(expression, "layer")
    if layer is None or atoms(layer) != ("F.Cu",):
        _fail(f"{locator}: only front-side footprints are supported")
    if child(expression, "at") is None:
        _fail(f"{locator}: footprint has no pose")
    for index, rectangle in enumerate(children(expression, "fp_rect")):
        rect_locator = f"{locator}.fp_rect[{index}]"
        if any(
            isinstance(item, SExpr) and item.head not in _ALLOWED_RECT_HEADS
            for item in rectangle.items[1:]
        ):
            _fail(f"{rect_locator}: courtyard rectangle contains unsupported syntax")
        layer_field = child(rectangle, "layer")
        if layer_field is None or atoms(layer_field) != ("F.CrtYd",):
            _fail(f"{rect_locator}: only matching F.CrtYd rectangles are supported")
        fill = child(rectangle, "fill")
        if fill is not None and atoms(fill) != ("none",):
            _fail(f"{rect_locator}: filled courtyard rectangles are unsupported")
    for index, pad in enumerate(children(expression, "pad")):
        _pose(pad, f"{locator}.pad[{index}]")


def render_kicad_placement_candidate_board(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: PlacementCandidate,
    profile: KiCadConstraintProfile,
    *,
    limits: ParseLimits | None = None,
) -> bytes:
    """Render one revision-bound placement candidate into disposable KiCad bytes.

    Only changed footprint ``at`` expressions and their owned pad angles are spliced.  All bytes
    outside those target expressions remain byte-identical.  The returned board is parsed again
    and must equal the source Board IR with only the candidate footprint poses and derived pad and
    courtyard board-frame geometry changed.
    """

    limits = limits or ParseLimits()
    if not isinstance(source, bytes):
        raise KiCadPlacementPatchError("KiCad source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise KiCadPlacementPatchError("board snapshot is malformed")
    if not isinstance(candidate, PlacementCandidate):
        raise KiCadPlacementPatchError("placement candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise KiCadPlacementPatchError("KiCad constraint profile is malformed")
    if not isinstance(limits, ParseLimits):
        raise KiCadPlacementPatchError("parse limits are malformed")
    if len(source) > limits.max_input_bytes:
        _fail("KiCad source exceeds the input-byte budget")

    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        _fail("KiCad source cannot be represented by the supported Board IR")
    if conversion.snapshot != snapshot:
        _fail("KiCad source and constraint profile do not match the snapshot")
    if candidate.base_revision != snapshot.snapshot_digest:
        _fail("placement candidate is stale for the supplied board snapshot")
    if candidate.view_revision != f"sha256:{hashlib.sha256(source).hexdigest()}":
        _fail("placement candidate is stale for the supplied board bytes")
    try:
        verify_placement_id(candidate)
    except ValueError as error:
        raise KiCadPlacementPatchError(
            "placement candidate identity verification failed"
        ) from error
    _require_native_geometry_identities(snapshot)
    if len(candidate.placements) > limits.max_objects:
        _fail("placement candidate exceeds the object budget")

    root_text = source.decode("utf-8", errors="strict")
    try:
        root = parse_sexpr(source, limits)
    except SExprError as error:
        raise KiCadPlacementPatchError("KiCad source structure cannot be inspected") from error
    source_footprints = children(root, "footprint")
    if len(source_footprints) != len(snapshot.content.footprints):
        _fail("KiCad source footprint count disagrees with Board IR")
    expressions: dict[str, SExpr] = {}
    for index, expression in enumerate(source_footprints):
        _validate_footprint_expression(expression, f"kicad_pcb.footprint[{index}]")
        identity = _native_identity(
            expression, kind="footprint", locator=f"kicad_pcb.footprint[{index}]"
        )
        if identity in expressions:
            _fail("source contains duplicate footprint identities")
        expressions[identity] = expression

    snapshot_footprints = {item.id: item for item in snapshot.content.footprints}
    if set(expressions) != set(snapshot_footprints):
        _fail("source and Board IR footprint identities disagree")
    candidate_by_ref = {item.ref_id: item for item in candidate.placements}
    if len(candidate_by_ref) != len(candidate.placements):
        _fail("placement candidate contains duplicate footprint identities")
    if not set(candidate_by_ref) <= set(snapshot_footprints):
        _fail("placement candidate references an unknown Board IR footprint")
    placeable_refs = {
        footprint.id for footprint in snapshot.content.footprints if footprint.pad_ids
    }
    if set(candidate_by_ref) != placeable_refs:
        _fail("placement candidate footprint set must cover every placeable Board IR footprint")
    placeable_footprints = {ref_id for ref_id, item in snapshot_footprints.items() if item.pad_ids}
    if set(candidate_by_ref) != placeable_footprints:
        _fail("placement candidate footprint set disagrees with placeable Board IR footprints")

    changed: dict[str, tuple[int, int, int]] = {}
    splices: list[Splice] = []
    for ref_id in sorted(candidate_by_ref):
        footprint = snapshot_footprints[ref_id]
        proposed = candidate_by_ref[ref_id]
        moved_value: object = proposed.moved
        if type(moved_value) is not bool:
            _fail("placement candidate moved flag is malformed")
        if proposed.side != FootprintSide.FRONT.value:
            _fail("back-side placement changes are unsupported")
        moved: object = proposed.moved
        if type(moved) is not bool:
            _fail("placement candidate moved flag is malformed")
        if isinstance(proposed.orientation_udeg, bool) or not isinstance(
            proposed.orientation_udeg, int
        ):
            _fail("placement candidate rotation is malformed")
        try:
            PointNM(proposed.origin_x_nm, proposed.origin_y_nm)
        except ValueError as error:
            raise KiCadPlacementPatchError(
                "placement candidate contains invalid coordinates"
            ) from error
        _rotation_degrees(proposed.orientation_udeg)
        differs = (
            proposed.origin_x_nm != footprint.origin.x
            or proposed.origin_y_nm != footprint.origin.y
            or proposed.orientation_udeg != footprint.rotation_udeg
        )
        if proposed.moved != differs:
            _fail("placement candidate moved flags do not match its poses")
        if footprint.locked and differs:
            _fail("locked footprint movement is unsupported")
        if not differs:
            continue

        expression = expressions[ref_id]
        old_x, old_y, old_rotation = _pose(expression, f"{ref_id}.at")
        if (old_x, old_y, old_rotation) != (
            footprint.origin.x,
            footprint.origin.y,
            footprint.rotation_udeg,
        ):
            _fail("source footprint pose disagrees with Board IR")
        changed[ref_id] = (
            proposed.origin_x_nm,
            proposed.origin_y_nm,
            proposed.orientation_udeg,
        )
        at = child(expression, "at")
        assert at is not None
        at_start, at_end = span(at, root_text)
        indentation = line_indent(root_text, at_start)
        splices.append(Splice(at_start, at_end, indentation + _render_pose(*changed[ref_id])))

        delta = (proposed.orientation_udeg - old_rotation) % _FULL_ROTATION_UDEG
        for pad in children(expression, "pad"):
            pad_at = child(pad, "at")
            assert pad_at is not None
            values = atoms(pad_at)
            pad_rotation = normalize_rotation_udeg(values[2] if len(values) == 3 else "0")
            next_rotation = (pad_rotation + delta) % _FULL_ROTATION_UDEG
            start, end = span(pad_at, root_text)
            indentation = line_indent(root_text, start)
            x = mm_to_nm(values[0])
            y = mm_to_nm(values[1])
            splices.append(Splice(start, end, indentation + _render_pad_pose(x, y, next_rotation)))

    if len(splices) > limits.max_nodes:
        _fail("placement patch exceeds the edit budget")
    if not splices:
        return source
    try:
        rendered = apply_splices(root_text, splices).encode("utf-8", errors="strict")
    except (CstError, UnicodeError) as error:
        raise KiCadPlacementPatchError("placement patch could not be applied") from error
    if len(rendered) > limits.max_input_bytes:
        _fail("rendered placement board exceeds the input-byte budget")

    patched = parse_kicad_bytes(rendered, profile, limits)
    if patched.snapshot is None or patched.diagnostics:
        _fail("rendered placement board failed Board IR round-trip parsing")
    assert patched.snapshot is not None
    expected = _expected_content(snapshot, changed, patched.snapshot.content.source.revision)
    if patched.snapshot.content != expected:
        _fail("rendered placement board changed content outside its pose patch")
    return rendered


__all__ = ["KiCadPlacementPatchError", "render_kicad_placement_candidate_board"]
