#!/usr/bin/env python3
"""Measure the closed public footprint-graphic-on-copper surface without accepting it.

B-133 recorded that clearing the `footprint` container allowlist left the six former
`setup_semantics` boards on three refusals that already name their construct, and that **two** of
them stop at ``footprint graphic on a copper layer is unmodelled copper``.  This instrument
decomposes that terminal and nothing else: which layer-bearing footprint children those two boards
put on a copper layer, of which primitive kind, in what grammar, and -- for the one kind that turns
out to occur -- what geometry a conservative envelope would have to bound.

**Why the geometry buckets are here and not left to the decision.**  Every other census in this
chain answered "which heads" and stopped, because for a *field* the head is the decision's whole
input.  A graphic is different: the head says only that a shape exists, and whether the shape is
modellable at all depends on whether an envelope can be derived from it.  So this census carries
one extra layer -- vertex counts, convexity, simplicity, and two envelope-cost ratios in buckets --
because those are the numbers the accompanying decision turns on, and measuring them after the
decision would make the decision taste rather than evidence.

**Why a fourth instrument rather than a parameterized one.**  The same reason
:mod:`scripts.benchmark_public_footprint_field_census` gives for being the third: a frozen
selection commitment means "these entries, selected *this way*", and a runtime-parameterized rule
would leave the constant saying only "these entries".  Output plumbing, path resolution and the
shape helper are imported from the setup census rather than restated, so the create-exclusive
anchored no-follow publish is the same already-tested code.

**Continuity with B-133 is computed, not asserted.**  B-133 reported the six boards splitting
`2 / 3 / 1` across three named successor refusals.  This instrument re-walks every public board's
terminal and requires that exact partition before it aggregates anything, so a drifted adapter or a
re-derived corpus fails the run instead of silently re-aggregating over a different population.

Aggregate counts from predeclared vocabularies only.  No board identity, path, digest, coordinate,
vertex, dimension, ratio or file name is ever committed: every ratio reaches the artifact as a
membership count in a predeclared bucket.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import platform
import re
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any, Final

from copper_mcp.adapters.sexpr import SExpr, is_quoted_atom, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.security import read_workspace_file
from scripts import benchmark_fixed_point_masking_census as masking
from scripts import benchmark_public_setup_field_census as setup_census

SCHEMA: Final = "copper-mcp/public-copper-graphic-census/v1"
EXPECTED_CAPTURED: Final = 13
EXPECTED_PUBLIC: Final = 10
EXPECTED_COPPER_GRAPHIC_TERMINALS: Final = 2
OTHER: Final = "other"
PREDECLARED_COHORT_FINGERPRINT: Final = masking.PREDECLARED_COHORT_FINGERPRINT
SELECTION_COMMITMENT_DOMAIN: Final = (
    b"copper-mcp/public-copper-graphic-census/selected-manifest-entries/v1\x00"
)
# Assigned once, from the exact B-129 cohort, in the pull request that first ran this instrument.
# A *freeze*, not a prediction: it binds which two entries were aggregated, so a later rerun whose
# selection differs -- a drifted classifier, a re-derived corpus, a swapped manifest row -- fails
# instead of silently re-aggregating over a different population.  The expected count alone cannot
# catch a same-count membership swap.
PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT: Final[str | None] = (
    "sha256:5cded48f9b024054682bfdda759843757d1d3912877169caf25d71476698b32a"
)

# The exact terminal this census exists to decompose, and the two sibling terminals B-133 recorded
# beside it.  Frozen copies of the adapter's sentences rather than live imports, for the reason the
# footprint census froze its vocabularies: an adapter edit must invalidate a recorded artifact
# loudly instead of silently re-bucketing it.
COPPER_GRAPHIC_MESSAGE: Final = "footprint graphic on a copper layer is unmodelled copper"
FOOTPRINT_ZONE_MESSAGE: Final = "footprint-local zones are unsupported"
EDGE_CUTS_GRAPHIC_MESSAGE: Final = "footprint graphic on Edge.Cuts is unsupported"
WALL_CODE: Final = "unsupported.construct"
GRAPHIC_LOCATOR_PREFIX: Final = "kicad_pcb.footprint["
GRAPHIC_LOCATOR_SUFFIX: Final = "].graphic"
ZONE_LOCATOR_SUFFIX: Final = "].zone"
# B-133's measured split of the six former container-allowlist boards across the three successors.
# Checked before aggregation; this is the continuity link to that row.
PREDECLARED_B133_SUCCESSOR_PARTITION: Final[dict[str, int]] = {
    "copper_graphic": 2,
    "footprint_zone": 3,
    "edge_cuts_graphic": 1,
}
SUCCESSOR_KEYS: Final = ("copper_graphic", "footprint_zone", "edge_cuts_graphic", OTHER)

# The closed vocabulary of `footprint` children the adapter routes through its **layer-aware**
# branch -- `head.startswith("fp_") or head in {"property", "point"}` in `_validate_footprints`.
# It is the union of the layer-bearing top-level `case T_…` arms of
# `PCB_IO_KICAD_SEXPR_PARSER::parseFOOTPRINT_unchecked` on the KiCad `9.0` and `10.0` release
# branches.  Deriving it from the grammar rather than from the boards is what makes `other` mean
# something: a head this cohort happens not to write is still in the vocabulary at zero, and a head
# KiCad can write that is missing here shows up as `other` rather than as silence.
#
# The `fp_ellipse`/`fp_ellipse_arc` pair is reachable only on `master` (11.0 development) and is
# deliberately **excluded**, so a board written by that version lands in `other` rather than being
# silently covered -- the same exclusion the footprint-field census makes for its three
# `master`-only heads.
GRAPHIC_HEADS: Final = frozenset(
    {
        "fp_arc",
        "fp_circle",
        "fp_curve",
        "fp_line",
        "fp_poly",
        "fp_rect",
        "fp_text",
        "fp_text_box",
        "point",
        "property",
    }
)
# Where a layer-routed head's `(layer …)` puts it, as a **total partition** of every layer-routed
# occurrence.  The classes mirror the adapter's own branch order in `_validate_footprints`:
# courtyard first, then the routing-layer test, then everything else is read past.  `multi_copper`
# is separated from `single_copper` because `*.Cu` and `F&B.Cu` name more than one layer and a
# Board IR `Segment` names exactly one, so the two are different modelling questions and a census
# that merged them could not report the second as absent.
LAYER_CLASSES: Final = (
    "single_copper",
    "multi_copper",
    "edge_cuts",
    "courtyard",
    "non_routing",
    "absent_or_malformed",
)
# Closed child grammar of an `fp_poly`, from the same parser arms.  `other` catches anything else.
POLY_CHILD_HEADS: Final = frozenset({"fill", "layer", "locked", "pts", "stroke", "tstamp", "uuid"})
# Payload partitions over the copper-layer occurrences of each head.  Every partition is **total**:
# each occurrence lands in exactly one bucket, and `measure` reconciles each partition's sum against
# the copper occurrence count it partitions.  That reconciliation is the #226 review lesson applied
# from the start rather than after the fact -- an absence is evidence only if the observation could
# have reported a presence, and a partition with a silent gap cannot.
FILL_BUCKETS: Final = ("fill_yes", "fill_no", "fill_absent", "fill_invalid")
STROKE_BUCKETS: Final = (
    "stroke_zero",
    "stroke_positive",
    "stroke_absent",
    "stroke_invalid",
)
NET_TIE_BUCKETS: Final = ("in_net_tie_footprint", "outside_net_tie_footprint")
PTS_BUCKETS: Final = ("pts_xy_only", "pts_with_curved_child", "pts_absent", "pts_invalid")
# Geometry partitions over the copper-layer `fp_poly` occurrences only.  These are the decision's
# direct inputs: whether an exact model exists at all (`simplicity`, `vertex_distinctness`) and what
# a bounding envelope would cost (`area_over_bbox`, and the per-board union ratio below).
VERTEX_COUNT_BUCKETS: Final = ("v_lt_5", "v_5_to_16", "v_17_to_64", "v_65_to_256", "v_gt_256")
CONVEXITY_BUCKETS: Final = ("convex", "concave", "degenerate")
SIMPLICITY_BUCKETS: Final = ("simple", "self_intersecting", "not_checked")
DISTINCTNESS_BUCKETS: Final = ("all_distinct", "closing_vertex_only", "interior_repeat")
RATIO_BUCKETS: Final = ("lt_0_05", "lt_0_25", "lt_0_50", "lt_0_75", "lt_0_90", "ge_0_90")
# Above this vertex count the quadratic self-intersection check is not run and the occurrence is
# reported as `not_checked` rather than guessed at.  A bound stated in the file is what keeps the
# artifact replayable.
SIMPLICITY_VERTEX_CAP: Final = 400
NM_PER_MM: Final = 1_000_000
# The decimal language a coordinate or width token must be written in to be read at all.  It
# deliberately **mirrors rather than imports** the adapter's grammar, for the reason the footprint
# census froze its own copy: importing the live grammar would let an adapter edit silently
# re-bucket a recorded measurement.  It rejects `nan`, `inf` and `Infinity` by construction.
_DECIMAL: Final = re.compile(r"^[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]+)?$")
_MAX_TOKEN = 64

Converter = Callable[[bytes, Settings], Any]


@dataclass(frozen=True, slots=True)
class BoardObservation:
    footprint_count: int
    head_occurrences: Mapping[str, int]
    head_presence: frozenset[str]
    layer_classes: Mapping[str, int]
    copper_head_occurrences: Mapping[str, int]
    fill_buckets: Mapping[str, int]
    stroke_buckets: Mapping[str, int]
    net_tie_buckets: Mapping[str, int]
    pts_buckets: Mapping[str, int]
    poly_child_heads: Mapping[str, int]
    poly_child_shapes: Mapping[str, int]
    vertex_counts: Mapping[str, int]
    convexity: Mapping[str, int]
    simplicity: Mapping[str, int]
    distinctness: Mapping[str, int]
    area_ratio: Mapping[str, int]
    envelope_ratio: Mapping[str, int]
    union_ratio_bucket: str
    bounded_polygons: int
    unbounded_polygons: int
    carrier_footprints: int
    padless_carrier_footprints: int


def _fixed_error(message: str) -> ValueError:
    return ValueError(message)


def _require_symbolic_head(node: SExpr, context: str) -> str:
    head = node.head
    if head is None or is_quoted_atom(head):
        raise _fixed_error(f"{context} must have an unquoted symbolic head")
    return head


def _bucket(head: str, vocabulary: frozenset[str]) -> str:
    return head if head in vocabulary else OTHER


def _ratio_bucket(value: Fraction) -> str:
    for edge, name in (
        (Fraction(5, 100), "lt_0_05"),
        (Fraction(25, 100), "lt_0_25"),
        (Fraction(50, 100), "lt_0_50"),
        (Fraction(75, 100), "lt_0_75"),
        (Fraction(90, 100), "lt_0_90"),
    ):
        if value < edge:
            return name
    return "ge_0_90"


def _vertex_bucket(count: int) -> str:
    if count < 5:
        return "v_lt_5"
    if count <= 16:
        return "v_5_to_16"
    if count <= 64:
        return "v_17_to_64"
    if count <= 256:
        return "v_65_to_256"
    return "v_gt_256"


def _children(node: SExpr, head: str) -> tuple[SExpr, ...]:
    return tuple(item for item in node.items[1:] if isinstance(item, SExpr) and item.head == head)


def _sole_child(node: SExpr, head: str) -> SExpr | None:
    found = _children(node, head)
    return found[0] if len(found) == 1 else None


def _bare_atoms(node: SExpr) -> tuple[str, ...] | None:
    """Return a node's payload atoms when every payload item is one unquoted atom."""

    payload = node.items[1:]
    if not payload or any(not isinstance(item, str) or is_quoted_atom(item) for item in payload):
        return None
    return tuple(item for item in payload if isinstance(item, str))


def _nanometres(token: str) -> int | None:
    """Return an exact integer-nanometre reading of a millimetre token, or None if unreadable."""

    if len(token) > _MAX_TOKEN or not _DECIMAL.fullmatch(token):
        return None
    scaled = Fraction(token) * NM_PER_MM
    return int(scaled) if scaled.denominator == 1 else None


def _layer_class(node: SExpr, copper_names: frozenset[str]) -> str:
    layer = _sole_child(node, "layer")
    if layer is None:
        return "absent_or_malformed"
    values = layer.items[1:]
    if len(values) != 1 or not isinstance(values[0], str):
        return "absent_or_malformed"
    name = values[0]
    if name in {"*.Cu", "F&B.Cu"}:
        return "multi_copper"
    if name == "Edge.Cuts":
        return "edge_cuts"
    if name in {"F.CrtYd", "B.CrtYd"}:
        return "courtyard"
    if name.endswith(".Cu"):
        return "single_copper" if name in copper_names else "absent_or_malformed"
    return "non_routing"


def _fill_bucket(node: SExpr) -> str:
    fill = _children(node, "fill")
    if not fill:
        return "fill_absent"
    if len(fill) != 1:
        return "fill_invalid"
    atoms = _bare_atoms(fill[0])
    if atoms is None or len(atoms) != 1:
        return "fill_invalid"
    if atoms[0] == "yes":
        return "fill_yes"
    if atoms[0] == "no":
        return "fill_no"
    return "fill_invalid"


def _stroke_bucket(node: SExpr) -> str:
    stroke = _children(node, "stroke")
    if not stroke:
        return "stroke_absent"
    if len(stroke) != 1:
        return "stroke_invalid"
    width = _children(stroke[0], "width")
    if len(width) != 1:
        return "stroke_invalid"
    atoms = _bare_atoms(width[0])
    if atoms is None or len(atoms) != 1:
        return "stroke_invalid"
    value = _nanometres(atoms[0])
    if value is None or value < 0:
        return "stroke_invalid"
    return "stroke_zero" if value == 0 else "stroke_positive"


def _stroke_half_width_nm(node: SExpr) -> int:
    """Return the ceil-rounded stroke half width, or 0 when it is not a readable non-negative."""

    stroke = _sole_child(node, "stroke")
    if stroke is None:
        return 0
    width = _sole_child(stroke, "width")
    if width is None:
        return 0
    atoms = _bare_atoms(width)
    if atoms is None or len(atoms) != 1:
        return 0
    value = _nanometres(atoms[0])
    if value is None or value < 0:
        return 0
    return (value + 1) // 2


def _points(node: SExpr) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Return the `pts` partition bucket and the readable integer-nanometre vertices."""

    groups = _children(node, "pts")
    if not groups:
        return "pts_absent", ()
    if len(groups) != 1:
        return "pts_invalid", ()
    payload = groups[0].items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        return "pts_invalid", ()
    children = tuple(item for item in payload if isinstance(item, SExpr))
    if any(item.head is None or is_quoted_atom(item.head) for item in children):
        return "pts_invalid", ()
    heads = {item.head for item in children}
    if heads - {"xy"}:
        # KiCad 9 writes `(arc …)` inside a `pts` list for a curved polygon side.  A curved side is
        # a different modelling question from a straight one, so it is partitioned out rather than
        # read as if its control points were vertices.
        return "pts_with_curved_child", ()
    vertices: list[tuple[int, int]] = []
    for item in children:
        atoms = _bare_atoms(item)
        if atoms is None or len(atoms) != 2:
            return "pts_invalid", ()
        x = _nanometres(atoms[0])
        y = _nanometres(atoms[1])
        if x is None or y is None:
            return "pts_invalid", ()
        vertices.append((x, y))
    return "pts_xy_only", tuple(vertices)


def _signed_double_area(points: Sequence[tuple[int, int]]) -> int:
    total = 0
    for index in range(len(points)):
        x0, y0 = points[index]
        x1, y1 = points[(index + 1) % len(points)]
        total += x0 * y1 - x1 * y0
    return total


def _convexity(points: Sequence[tuple[int, int]]) -> str:
    if len(points) < 3:
        return "degenerate"
    sign = 0
    for index in range(len(points)):
        ax, ay = points[index]
        bx, by = points[(index + 1) % len(points)]
        cx, cy = points[(index + 2) % len(points)]
        cross = (bx - ax) * (cy - by) - (by - ay) * (cx - bx)
        if cross == 0:
            continue
        current = 1 if cross > 0 else -1
        if sign == 0:
            sign = current
        elif current != sign:
            return "concave"
    return "convex" if sign else "degenerate"


def _orientation(a: tuple[int, int], b: tuple[int, int], c: tuple[int, int]) -> int:
    value = (b[0] - a[0]) * (c[1] - a[1]) - (b[1] - a[1]) * (c[0] - a[0])
    return (value > 0) - (value < 0)


def _simplicity(points: Sequence[tuple[int, int]]) -> str:
    count = len(points)
    if count < 3:
        return "not_checked"
    if count > SIMPLICITY_VERTEX_CAP:
        return "not_checked"
    for i in range(count):
        a, b = points[i], points[(i + 1) % count]
        for j in range(i + 1, count):
            if j == i or (j + 1) % count == i or j == (i + 1) % count:
                continue
            c, d = points[j], points[(j + 1) % count]
            o1, o2 = _orientation(a, b, c), _orientation(a, b, d)
            o3, o4 = _orientation(c, d, a), _orientation(c, d, b)
            if o1 != o2 and o3 != o4:
                return "self_intersecting"
    return "simple"


def _distinctness(points: Sequence[tuple[int, int]]) -> tuple[str, tuple[tuple[int, int], ...]]:
    """Return the distinctness bucket and the ring body with a repeated closing vertex removed."""

    if len(points) >= 2 and points[0] == points[-1]:
        body = tuple(points[:-1])
        closing = True
    else:
        body = tuple(points)
        closing = False
    if len(set(body)) != len(body):
        return "interior_repeat", body
    return ("closing_vertex_only" if closing else "all_distinct"), body


def _board_bounding_area(root: SExpr) -> int:
    """Return the area of the board's `Edge.Cuts` straight-segment bounding box, or 0."""

    xs: list[int] = []
    ys: list[int] = []
    for item in root.items[1:]:
        if not isinstance(item, SExpr) or item.head not in {"gr_line", "gr_rect"}:
            continue
        layer = _sole_child(item, "layer")
        if layer is None:
            continue
        values = layer.items[1:]
        if len(values) != 1 or values[0] != "Edge.Cuts":
            continue
        for key in ("start", "end"):
            field = _sole_child(item, key)
            if field is None:
                continue
            atoms = _bare_atoms(field)
            if atoms is None or len(atoms) != 2:
                continue
            x = _nanometres(atoms[0])
            y = _nanometres(atoms[1])
            if x is None or y is None:
                continue
            xs.append(x)
            ys.append(y)
    if not xs:
        return 0
    return (max(xs) - min(xs)) * (max(ys) - min(ys))


def _union_area(boxes: Sequence[tuple[int, int, int, int]]) -> int:
    """Return the exact union area of axis-aligned boxes by coordinate compression."""

    if not boxes:
        return 0
    xs = sorted({box[0] for box in boxes} | {box[2] for box in boxes})
    ys = sorted({box[1] for box in boxes} | {box[3] for box in boxes})
    total = 0
    for xi in range(len(xs) - 1):
        for yi in range(len(ys) - 1):
            x0, x1 = xs[xi], xs[xi + 1]
            y0, y1 = ys[yi], ys[yi + 1]
            if any(
                box[0] <= x0 and x1 <= box[2] and box[1] <= y0 and y1 <= box[3] for box in boxes
            ):
                total += (x1 - x0) * (y1 - y0)
    return total


def _copper_layer_names(root: SExpr) -> frozenset[str]:
    """Return the `.Cu` layer names the document declares, read past without publishing them."""

    layers = _sole_child(root, "layers")
    if layers is None:
        return frozenset()
    names: set[str] = set()
    for item in layers.items[1:]:
        if not isinstance(item, SExpr):
            continue
        values = [value for value in item.items[1:] if isinstance(value, str)]
        if values and values[0].endswith(".Cu"):
            names.add(values[0])
    return frozenset(names)


def _observe(root: SExpr) -> BoardObservation:
    copper_names = _copper_layer_names(root)
    head_occurrences: Counter[str] = Counter()
    head_presence: set[str] = set()
    layer_classes: Counter[str] = Counter()
    copper_heads: Counter[str] = Counter()
    fills: Counter[str] = Counter()
    strokes: Counter[str] = Counter()
    net_ties: Counter[str] = Counter()
    pts_classes: Counter[str] = Counter()
    poly_children: Counter[str] = Counter()
    poly_shapes: Counter[str] = Counter()
    vertices: Counter[str] = Counter()
    convexity: Counter[str] = Counter()
    simplicity: Counter[str] = Counter()
    distinctness: Counter[str] = Counter()
    area_ratio: Counter[str] = Counter()
    envelope_ratio: Counter[str] = Counter()
    boxes: list[tuple[int, int, int, int]] = []
    bounded = 0
    unbounded = 0
    carriers = 0
    padless = 0
    footprint_count = 0

    board_area = _board_bounding_area(root)

    for footprint in _children(root, "footprint"):
        footprint_count += 1
        in_net_tie = bool(_children(footprint, "net_tie_pad_groups"))
        has_pads = bool(_children(footprint, "pad"))
        carries_copper_graphic = False
        for item in footprint.items[1:]:
            if not isinstance(item, SExpr) or item.head is None or is_quoted_atom(item.head):
                continue
            head = item.head
            if not (head.startswith("fp_") or head in {"point", "property"}):
                continue
            bucket = _bucket(head, GRAPHIC_HEADS)
            head_occurrences[bucket] += 1
            head_presence.add(bucket)
            layer_class = _layer_class(item, copper_names)
            layer_classes[layer_class] += 1
            if layer_class != "single_copper":
                continue
            carries_copper_graphic = True
            copper_heads[bucket] += 1
            fills[_fill_bucket(item)] += 1
            strokes[_stroke_bucket(item)] += 1
            net_ties["in_net_tie_footprint" if in_net_tie else "outside_net_tie_footprint"] += 1
            pts_bucket, points = _points(item)
            pts_classes[pts_bucket] += 1
            if bucket != "fp_poly":
                continue
            for child in item.items[1:]:
                if not isinstance(child, SExpr) or child.head is None:
                    continue
                child_bucket = _bucket(child.head, POLY_CHILD_HEADS)
                poly_children[child_bucket] += 1
                poly_shapes[f"{child_bucket}:{setup_census._shape(child)}"] += 1
            vertices[_vertex_bucket(len(points))] += 1
            distinct_bucket, body = _distinctness(points)
            distinctness[distinct_bucket] += 1
            convexity[_convexity(body)] += 1
            simplicity[_simplicity(body)] += 1
            if len(body) < 3:
                # A polygon with no readable three-vertex ring -- a curved side, a malformed
                # point, a degenerate outline -- has **no area ratio and no envelope**. It is
                # counted as unbounded rather than filed into a ratio bucket: fabricating a
                # ratio for a shape that has none would make those buckets say something the
                # measurement does not support, which is the exact failure the partitions exist
                # to prevent. The two ratio partitions are therefore partitions of `bounded`,
                # and `bounded + unbounded` is reconciled against the polygon count.
                unbounded += 1
                continue
            bounded += 1
            xs = [point[0] for point in body]
            ys = [point[1] for point in body]
            box_area = (max(xs) - min(xs)) * (max(ys) - min(ys))
            shape_area = abs(_signed_double_area(body)) // 2
            area_ratio[
                _ratio_bucket(Fraction(shape_area, box_area) if box_area else Fraction(0))
            ] += 1
            half = _stroke_half_width_nm(item)
            box = (min(xs) - half, min(ys) - half, max(xs) + half, max(ys) + half)
            boxes.append(box)
            envelope_area = (box[2] - box[0]) * (box[3] - box[1])
            envelope_ratio[
                _ratio_bucket(
                    Fraction(min(envelope_area, board_area), board_area)
                    if board_area
                    else Fraction(1)
                )
            ] += 1
        if carries_copper_graphic:
            carriers += 1
            padless += 0 if has_pads else 1

    union = _union_area(boxes)
    union_bucket = (
        _ratio_bucket(Fraction(min(union, board_area), board_area)) if board_area else "ge_0_90"
    )
    return BoardObservation(
        footprint_count=footprint_count,
        head_occurrences=dict(head_occurrences),
        head_presence=frozenset(head_presence),
        layer_classes=dict(layer_classes),
        copper_head_occurrences=dict(copper_heads),
        fill_buckets=dict(fills),
        stroke_buckets=dict(strokes),
        net_tie_buckets=dict(net_ties),
        pts_buckets=dict(pts_classes),
        poly_child_heads=dict(poly_children),
        poly_child_shapes=dict(poly_shapes),
        vertex_counts=dict(vertices),
        convexity=dict(convexity),
        simplicity=dict(simplicity),
        distinctness=dict(distinctness),
        area_ratio=dict(area_ratio),
        envelope_ratio=dict(envelope_ratio),
        union_ratio_bucket=union_bucket,
        bounded_polygons=bounded,
        unbounded_polygons=unbounded,
        carrier_footprints=carriers,
        padless_carrier_footprints=padless,
    )


def _selection_commitment(snapshots: Sequence[masking.Snapshot]) -> str:
    digest = hashlib.sha256()
    digest.update(SELECTION_COMMITMENT_DOMAIN)
    digest.update(len(snapshots).to_bytes(4, "big"))
    for snapshot in snapshots:
        entry = snapshot.entry
        for value in (entry.identity, entry.visibility, entry.relative, entry.digest):
            encoded = value.encode("utf-8")
            digest.update(len(encoded).to_bytes(8, "big"))
            digest.update(encoded)
    return "sha256:" + digest.hexdigest()


def _expected_selection_commitment() -> str:
    expected = PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT
    if expected is None:
        raise _fixed_error("predeclared copper-graphic selection commitment is unassigned")
    if (
        not isinstance(expected, str)
        or len(expected) != 71
        or not expected.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in expected[7:])
    ):
        raise _fixed_error("predeclared copper-graphic selection commitment is malformed")
    return expected


def _successor_class(result: Any) -> str:
    """Bucket a terminal diagnostic into B-133's three named successors, or `other`."""

    diagnostics = getattr(result, "diagnostics", ())
    if not diagnostics:
        return OTHER
    diagnostic = diagnostics[0]
    code = getattr(diagnostic, "code", None)
    message = getattr(diagnostic, "message", None)
    locator = getattr(diagnostic, "source_locator", None)
    if code != WALL_CODE or not isinstance(locator, str):
        return OTHER
    if not locator.startswith(GRAPHIC_LOCATOR_PREFIX):
        return OTHER
    if message == COPPER_GRAPHIC_MESSAGE and locator.endswith(GRAPHIC_LOCATOR_SUFFIX):
        return "copper_graphic"
    if message == EDGE_CUTS_GRAPHIC_MESSAGE and locator.endswith(GRAPHIC_LOCATOR_SUFFIX):
        return "edge_cuts_graphic"
    if message == FOOTPRINT_ZONE_MESSAGE and locator.endswith(ZONE_LOCATOR_SUFFIX):
        return "footprint_zone"
    return OTHER


def _terminal_depth_and_class(
    source: bytes,
    settings: Settings,
    *,
    converter: Converter | None,
) -> tuple[int, str]:
    """Re-walk B-129's mask loop from its own primitives and classify the terminal diagnostic.

    `_classify_source_detail` returns a closed blocker class and deliberately never returns
    diagnostic text, so it cannot distinguish these three terminals from each other -- its
    vocabulary has no member for any of them and correctly reports `other` for all three.  Rather
    than widen that vocabulary, which would change the instrument B-137's differential must replay
    byte-for-byte, this composes the same module's `_convert` and `_mask_first_blocker`, and the
    caller requires the depth to agree with the classifier's.
    """

    convert = converter or (lambda data, opts: masking._convert(data, "frozen-board", opts))
    current = source
    seen = {source}
    for depth in range(masking.MAX_MASK_PASSES + 1):
        try:
            result = convert(current, settings)
        except Exception:
            return depth, OTHER
        if getattr(result, "snapshot", None) is not None and not getattr(result, "diagnostics", ()):
            return depth, OTHER
        if depth == masking.MAX_MASK_PASSES:
            return depth, OTHER
        replacement = masking._mask_first_blocker(current, result, settings)
        if replacement is None:
            return depth, _successor_class(result)
        if len(replacement) >= len(current) or replacement in seen:
            return depth, _successor_class(result)
        seen.add(replacement)
        current = replacement
    return masking.MAX_MASK_PASSES, OTHER


def _select_copper_graphic_terminals(
    snapshots: Sequence[masking.Snapshot],
    *,
    settings: Settings,
    converter: Converter | None,
) -> tuple[tuple[masking.Snapshot, ...], dict[str, int]]:
    public = tuple(snapshot for snapshot in snapshots if snapshot.entry.visibility == "public")
    if len(public) != EXPECTED_PUBLIC:
        raise _fixed_error(f"expected {EXPECTED_PUBLIC} public entries, got {len(public)}")

    partition: Counter[str] = Counter()
    selected: list[masking.Snapshot] = []
    for snapshot in public:
        depth, terminal, blocker = masking._classify_source_detail(
            snapshot.source,
            settings,
            converter=converter,
        )
        walk_depth, successor = _terminal_depth_and_class(
            snapshot.source,
            settings,
            converter=converter,
        )
        if walk_depth != depth:
            raise _fixed_error("terminal walk disagrees with the fixed-point classifier")
        if terminal != "unmaskable" or blocker != OTHER:
            successor = OTHER
        partition[successor] += 1
        if successor == "copper_graphic":
            selected.append(snapshot)
    # Continuity with B-133, computed rather than asserted: that row measured the six former
    # container-allowlist boards splitting 2/3/1 across these three named successors.
    for name, expected in PREDECLARED_B133_SUCCESSOR_PARTITION.items():
        if partition[name] != expected:
            raise _fixed_error("B-133 successor partition drifted")
    if len(selected) != EXPECTED_COPPER_GRAPHIC_TERMINALS:
        raise _fixed_error(
            "fixed-point copper-graphic terminal population drifted: "
            f"expected {EXPECTED_COPPER_GRAPHIC_TERMINALS}, got {len(selected)}"
        )
    return tuple(selected), {key: int(partition.get(key, 0)) for key in SUCCESSOR_KEYS}


def _verify_sources_unchanged(
    corpus: Path,
    snapshots: Sequence[masking.Snapshot],
    settings: Settings,
) -> None:
    for snapshot in snapshots:
        try:
            current = read_workspace_file(
                corpus,
                snapshot.entry.relative,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=settings.max_board_bytes,
            ).content
        except Exception as error:
            raise _fixed_error("source changed or became unavailable") from error
        if current != snapshot.source:
            raise _fixed_error("source changed during measurement")


def _closed_counts(counter: Mapping[str, int], keys: Sequence[str]) -> dict[str, int]:
    return {key: int(counter.get(key, 0)) for key in keys}


def _merge_mapping(observations: Sequence[BoardObservation], attribute: str) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, Mapping):
            raise _fixed_error("internal aggregate mapping is invalid")
        merged.update(value)
    return merged


def _merge_presence(observations: Sequence[BoardObservation], attribute: str) -> Counter[str]:
    merged: Counter[str] = Counter()
    for observation in observations:
        value = getattr(observation, attribute)
        if not isinstance(value, frozenset):
            raise _fixed_error("internal aggregate presence set is invalid")
        merged.update(value)
    return merged


def _root(source: bytes, settings: Settings) -> SExpr:
    root = parse_sexpr(source, parse_limits_for(settings))
    if _require_symbolic_head(root, "source root") != "kicad_pcb":
        raise _fixed_error("source root must be kicad_pcb")
    payload = root.items[1:]
    if any(not isinstance(item, SExpr) for item in payload):
        raise _fixed_error("source root must contain only child expressions")
    for child in payload:
        if isinstance(child, SExpr):
            _require_symbolic_head(child, "source root child")
    if not _children(root, "footprint"):
        raise _fixed_error("each selected public source must contain at least one footprint")
    return root


def measure(
    corpus: Path,
    manifest: Path,
    settings: Settings,
    *,
    converter: Converter | None = None,
) -> dict[str, Any]:
    if settings.allow_apply or settings.allow_live_ipc or settings.allow_live_apply:
        raise _fixed_error("copper-graphic census is read-only")
    # **There is deliberately no adapter drift guard here, and the absence is the honest option.**
    # The sibling censuses check *containment* of a frozen accepted vocabulary because a field
    # census can keep reading a surface the adapter has quietly widened.  This census cannot be
    # fooled that way: its entire population is selected by one refusal sentence and one locator
    # shape, so an adapter that stops emitting them makes the selection empty and the run fails in
    # `_select_copper_graphic_terminals` -- loudly, before a single aggregate is computed.  The
    # only guard that could be added on top would re-derive `GRAPHIC_HEADS` from a hardcoded copy
    # of the adapter's branch predicate and compare it against itself, which would pass whatever
    # the adapter did.  A check that cannot fail is not evidence, and writing one here would be
    # exactly the vacuous-differential shape this project refuses elsewhere.
    expected_selection = _expected_selection_commitment()

    entries, fingerprint = masking.load_manifest(manifest)
    expected = PREDECLARED_COHORT_FINGERPRINT
    if not isinstance(expected, str) or expected != fingerprint or len(expected) != 39:
        raise _fixed_error("predeclared cohort fingerprint does not match")

    snapshots = masking.capture_snapshots(
        corpus,
        entries,
        max_bytes=settings.max_board_bytes,
    )
    if len(snapshots) != EXPECTED_CAPTURED:
        raise _fixed_error(f"expected {EXPECTED_CAPTURED} captured entries, got {len(snapshots)}")
    selected, successor_partition = _select_copper_graphic_terminals(
        snapshots,
        settings=settings,
        converter=converter,
    )
    observed_selection = _selection_commitment(selected)
    if not hmac.compare_digest(expected_selection, observed_selection):
        raise _fixed_error("fixed-point copper-graphic terminal membership drifted")

    observations = tuple(_observe(_root(snapshot.source, settings)) for snapshot in selected)

    head_keys = (*sorted(GRAPHIC_HEADS), OTHER)
    poly_child_keys = (*sorted(POLY_CHILD_HEADS), OTHER)

    head_occurrences = _merge_mapping(observations, "head_occurrences")
    head_presence = _merge_presence(observations, "head_presence")
    layer_classes = _merge_mapping(observations, "layer_classes")
    copper_heads = _merge_mapping(observations, "copper_head_occurrences")
    fills = _merge_mapping(observations, "fill_buckets")
    strokes = _merge_mapping(observations, "stroke_buckets")
    net_ties = _merge_mapping(observations, "net_tie_buckets")
    pts_classes = _merge_mapping(observations, "pts_buckets")
    poly_children = _merge_mapping(observations, "poly_child_heads")
    poly_shapes = _merge_mapping(observations, "poly_child_shapes")
    vertices = _merge_mapping(observations, "vertex_counts")
    convexity = _merge_mapping(observations, "convexity")
    simplicity = _merge_mapping(observations, "simplicity")
    distinctness = _merge_mapping(observations, "distinctness")
    area_ratio = _merge_mapping(observations, "area_ratio")
    envelope_ratio = _merge_mapping(observations, "envelope_ratio")
    union_buckets: Counter[str] = Counter(
        observation.union_ratio_bucket for observation in observations
    )

    # Every partition is reconciled against the population it partitions.  This is what makes a
    # zero in any bucket readable as evidence rather than as silence, and it is checked rather
    # than trusted: the #226 review found a sibling instrument where a malformed payload produced
    # no bucket at all, which no aggregate could have revealed.
    #
    # **The sum is taken over the closed projection, not over the raw counter**, and the
    # difference is the whole point. `_closed_counts` drops any key outside the predeclared
    # vocabulary, so a classifier that returned a bucket name nobody declared would publish a
    # partition of zeros while the raw counter still summed to the right total -- a silent gap of
    # exactly the shape this check exists to catch, passing the check. Reconciling what the
    # artifact will actually say closes that.
    head_counts = _closed_counts(head_occurrences, head_keys)
    layer_counts = _closed_counts(layer_classes, LAYER_CLASSES)
    copper_counts = _closed_counts(copper_heads, head_keys)
    fill_counts = _closed_counts(fills, FILL_BUCKETS)
    stroke_counts = _closed_counts(strokes, STROKE_BUCKETS)
    net_tie_counts = _closed_counts(net_ties, NET_TIE_BUCKETS)
    pts_counts = _closed_counts(pts_classes, PTS_BUCKETS)
    vertex_counts = _closed_counts(vertices, VERTEX_COUNT_BUCKETS)
    convexity_counts = _closed_counts(convexity, CONVEXITY_BUCKETS)
    simplicity_counts = _closed_counts(simplicity, SIMPLICITY_BUCKETS)
    distinctness_counts = _closed_counts(distinctness, DISTINCTNESS_BUCKETS)
    area_counts = _closed_counts(area_ratio, RATIO_BUCKETS)
    envelope_counts = _closed_counts(envelope_ratio, RATIO_BUCKETS)
    union_counts = _closed_counts(union_buckets, RATIO_BUCKETS)
    poly_child_counts = _closed_counts(poly_children, poly_child_keys)

    layer_routed_total = sum(head_counts.values())
    copper_total = sum(copper_counts.values())
    poly_total = int(copper_counts.get("fp_poly", 0))
    bounded_total = sum(observation.bounded_polygons for observation in observations)
    unbounded_total = sum(observation.unbounded_polygons for observation in observations)
    if bounded_total + unbounded_total != poly_total:
        raise _fixed_error("bounded and unbounded polygons do not partition their population")
    for name, partition, total in (
        ("layer_class", layer_counts, layer_routed_total),
        ("fill", fill_counts, copper_total),
        ("stroke", stroke_counts, copper_total),
        ("net_tie", net_tie_counts, copper_total),
        ("pts", pts_counts, copper_total),
        ("vertex_count", vertex_counts, poly_total),
        ("convexity", convexity_counts, poly_total),
        ("simplicity", simplicity_counts, poly_total),
        ("distinctness", distinctness_counts, poly_total),
        ("area_over_bbox", area_counts, bounded_total),
        ("envelope_over_board", envelope_counts, bounded_total),
        ("board_envelope_union", union_counts, len(observations)),
    ):
        if sum(partition.values()) != total:
            raise _fixed_error(f"{name} buckets do not partition their population")
    # The raw counters must agree with their projections too, or a key outside the vocabulary was
    # dropped on the way to the artifact and the partition above reconciled against a population
    # the artifact does not describe.
    for name, raw, projected in (
        ("graphic_head", head_occurrences, head_counts),
        ("layer_class", layer_classes, layer_counts),
        ("copper_head", copper_heads, copper_counts),
        ("fill", fills, fill_counts),
        ("stroke", strokes, stroke_counts),
        ("net_tie", net_ties, net_tie_counts),
        ("pts", pts_classes, pts_counts),
        ("vertex_count", vertices, vertex_counts),
        ("convexity", convexity, convexity_counts),
        ("simplicity", simplicity, simplicity_counts),
        ("distinctness", distinctness, distinctness_counts),
        ("area_over_bbox", area_ratio, area_counts),
        ("envelope_over_board", envelope_ratio, envelope_counts),
        ("board_envelope_union", union_buckets, union_counts),
        ("poly_child", poly_children, poly_child_counts),
    ):
        if sum(raw.values()) != sum(projected.values()):
            raise _fixed_error(f"{name} buckets do not partition their population")
    if int(layer_counts.get("single_copper", 0)) != copper_total:
        raise _fixed_error("copper head occurrences disagree with the layer partition")

    _verify_sources_unchanged(corpus, snapshots, settings)

    return {
        "schema": SCHEMA,
        "source_census": {
            "source_schema": masking.SCHEMA,
            "sibling_schema": setup_census.SCHEMA,
            "cohort_fingerprint": fingerprint,
            "captured_entries": len(snapshots),
            "public_entries": EXPECTED_PUBLIC,
            "copper_graphic_terminal_entries": len(selected),
            "selection_rule": "fixed_point_terminal_footprint_copper_graphic",
            "b133_successor_partition": successor_partition,
            "b133_successor_partition_matches": True,
        },
        "closed_vocabularies": {
            "graphic_head": list(head_keys),
            "layer_class": list(LAYER_CLASSES),
            "poly_child": list(poly_child_keys),
            "fill": list(FILL_BUCKETS),
            "stroke": list(STROKE_BUCKETS),
            "net_tie": list(NET_TIE_BUCKETS),
            "pts": list(PTS_BUCKETS),
            "vertex_count": list(VERTEX_COUNT_BUCKETS),
            "convexity": list(CONVEXITY_BUCKETS),
            "simplicity": list(SIMPLICITY_BUCKETS),
            "distinctness": list(DISTINCTNESS_BUCKETS),
            "ratio": list(RATIO_BUCKETS),
            "successor": list(SUCCESSOR_KEYS),
            "shape": list(setup_census.SHAPE_BUCKETS),
        },
        "aggregates": {
            "boards": len(observations),
            "footprint_count": sum(observation.footprint_count for observation in observations),
            "layer_routed_heads": {
                "occurrences": head_counts,
                "board_presence": _closed_counts(head_presence, head_keys),
            },
            "layer_classes": layer_counts,
            "copper_layer_heads": copper_counts,
            "copper_payload": {
                "fill": fill_counts,
                "stroke": stroke_counts,
                "net_tie": net_tie_counts,
                "pts": pts_counts,
            },
            "copper_polygon_children": {
                "occurrences": poly_child_counts,
                "shape_occurrences": _closed_counts(
                    poly_shapes,
                    tuple(
                        f"{head}:{shape}"
                        for head in poly_child_keys
                        for shape in setup_census.SHAPE_BUCKETS
                    ),
                ),
            },
            "copper_polygon_geometry": {
                "vertex_count": vertex_counts,
                "convexity": convexity_counts,
                "simplicity": simplicity_counts,
                "vertex_distinctness": distinctness_counts,
                "simplicity_vertex_cap": SIMPLICITY_VERTEX_CAP,
            },
            "envelope_cost": {
                "bounded_polygons": bounded_total,
                "unbounded_polygons": unbounded_total,
                "area_over_bounding_box": area_counts,
                "envelope_over_board_bounding_box": envelope_counts,
                "board_envelope_union_over_board_bounding_box": union_counts,
            },
            "carriers": {
                "footprints_with_copper_graphics": sum(
                    observation.carrier_footprints for observation in observations
                ),
                "padless_carriers": sum(
                    observation.padless_carrier_footprints for observation in observations
                ),
            },
        },
        "source_hashes_unchanged": True,
        "privacy": {
            "aggregate_only": True,
            "atom_values_committed": 0,
            "coordinates_committed": 0,
            "ratios_committed": 0,
            "board_identities_committed": 0,
            "board_paths_committed": 0,
            "board_digests_committed": 0,
            "board_bytes_committed": 0,
        },
        "claim_scope": {
            "measurement_only": True,
            "copper_graphic_acceptance": False,
            "conversion_success": False,
            "board_ir_schema_change": False,
            "production_behavior_change": False,
        },
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    corpus, manifest, output_target, runner = setup_census._resolve_cli_paths(
        args.corpus,
        args.manifest,
        args.output,
        Path(__file__),
    )
    try:
        root = runner.parents[1]
        commit, dirty = masking._git_state(root)
        if dirty:
            raise SystemExit("measurement worktree must start clean")
        runner_bytes = runner.read_bytes()

        settings = Settings(workspace=corpus)
        result = measure(corpus, manifest, settings)

        final_commit, final_dirty = masking._git_state(root)
        if final_commit != commit or final_dirty or runner.read_bytes() != runner_bytes:
            raise SystemExit("measurement inputs changed during run")
        result.update(
            {
                "commit": commit,
                "dirty": False,
                "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
                "runner_digest": "sha256:" + hashlib.sha256(runner_bytes).hexdigest(),
                "environment": {
                    "platform": platform.platform(),
                    "python": platform.python_version(),
                },
                "configuration": {
                    "max_manifest_bytes": masking.MAX_MANIFEST_BYTES,
                    "max_source_bytes": settings.max_board_bytes,
                    "operation": "read_only_closed_copper_graphic_census",
                },
                "committed_board_bytes": 0,
                "not_claimed": [
                    "no footprint copper-graphic product support",
                    "no claim about the three boards refusing at footprint-local zones",
                    "no converted board, route, DRC, fabrication, or hardware result",
                    "no board write, apply authority, editor mutation, or committed source input",
                ],
            }
        )
        canonical = json.dumps(
            result,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
        result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        setup_census._write_output(
            output_target,
            json.dumps(result, sort_keys=True, indent=2) + "\n",
        )
        return 0
    finally:
        output_target.close()


if __name__ == "__main__":
    raise SystemExit(main())
