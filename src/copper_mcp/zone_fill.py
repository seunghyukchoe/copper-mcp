"""Bounded extraction and canonical digesting of cached KiCad zone-fill geometry.

Board IR deliberately carries no fill: a `filled_polygon` node is a cache KiCad wrote at some
past moment, and nothing in the file says whether it still matches the board around it. This
module reads that cache out of band so a separate authority step can decide whether it is
fresh, and it never becomes part of a snapshot or its digest.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from copper_mcp.adapters.kicad_board_ir import net_id_for_name
from copper_mcp.adapters.sexpr import (
    SExpr,
    SExprError,
    atoms,
    children,
    is_quoted_atom,
    parse_sexpr,
)
from copper_mcp.board_ir import ParseLimits, PointNM

#: One nanometre expressed in the millimetre tokens KiCad writes.
_NM_PER_MM = 1_000_000
_MAX_DECIMALS = 6


class ZoneFillError(ValueError):
    """Raised when cached fill geometry cannot be read within the configured bounds."""


@dataclass(frozen=True, slots=True)
class FillIsland:
    """One `filled_polygon` node: a single connected region of poured copper.

    KiCad 10.0.5 emits one node per island rather than one node per zone stitched together by
    keyhole seams, verified against a board authored to force two disjoint regions. Two pads
    touching *different* islands are therefore not connected, which is why an island is the
    unit here rather than a zone.
    """

    net_id: str
    layer_id: str
    points: tuple[PointNM, ...]


def _millimetre_to_nanometre(token: str, locator: str) -> int:
    """Convert one KiCad decimal millimetre token to exact integer nanometres."""

    text = token.strip()
    if not text or len(text) > 32:
        raise ZoneFillError(f"{locator} is malformed")
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    whole, _, fraction = text.partition(".")
    if not whole.isdigit() or (fraction and not fraction.isdigit()):
        raise ZoneFillError(f"{locator} is not a plain decimal")
    if len(fraction) > _MAX_DECIMALS:
        raise ZoneFillError(f"{locator} is finer than one nanometre")
    scaled = int(whole) * _NM_PER_MM + int(fraction.ljust(_MAX_DECIMALS, "0") or "0")
    return -scaled if negative else scaled


def _points(expression: SExpr, limits: ParseLimits, budget: int) -> tuple[PointNM, ...]:
    points: list[PointNM] = []
    for group in children(expression, "pts"):
        for index, point in enumerate(children(group, "xy")):
            if len(points) >= budget:
                # Deliberately not "cached": this reader is called twice per freshness proof,
                # once on the board the operator handed us and once on the copy KiCad refilled,
                # and it cannot tell which. Naming the wrong one produced the self-contradicting
                # "refilled zone fill could not be read: cached zone fill exceeds ..." (#165).
                # Which document ran out is the caller's to say, and both call sites say it.
                raise ZoneFillError("zone fill exceeds the configured vertex budget")
            values = atoms(point)
            if len(values) != 2:
                raise ZoneFillError(f"fill vertex {index} is malformed")
            points.append(
                PointNM(
                    _millimetre_to_nanometre(values[0], f"fill vertex {index} x"),
                    _millimetre_to_nanometre(values[1], f"fill vertex {index} y"),
                )
            )
    if len(points) < 3:
        raise ZoneFillError("a filled polygon needs at least three vertices")
    return tuple(points)


def read_fill_islands(
    source: bytes,
    *,
    max_vertices: int,
    limits: ParseLimits | None = None,
) -> tuple[FillIsland, ...]:
    """Return every cached fill island in the board, in canonical order.

    The result is sorted so two boards that describe the same copper in a different textual
    order digest identically; KiCad rewrites and reorders a board wholesale on save, so a
    byte comparison of the file says nothing about whether the fill changed.
    """

    active_limits = limits or ParseLimits()
    try:
        root = parse_sexpr(source, active_limits)
    except SExprError as error:
        raise ZoneFillError("board source could not be parsed for zone fill") from error

    # KiCad writes a net either as a quoted name or as a numeric code declared once at the root.
    # Reading the code as if it were a name silently invents a net, which filters every island
    # of the real one, so the declarations are resolved the same way the main adapter does.
    declared: dict[str, str] = {}
    for declaration in children(root, "net"):
        values = atoms(declaration)
        if len(values) == 2 and values[0].lstrip("-").isdigit():
            declared[values[0]] = values[1]

    islands: list[FillIsland] = []
    remaining = max_vertices
    for zone in children(root, "zone"):
        net_values = atoms_of(zone, "net")
        if not net_values:
            continue
        token = net_values[-1]
        if token.lstrip("-").isdigit() and not is_quoted_atom(token):
            if token not in declared:
                raise ZoneFillError("a zone references an undeclared numeric net code")
            net_name = declared[token]
        else:
            net_name = token
        for polygon in children(zone, "filled_polygon"):
            layer_values = atoms_of(polygon, "layer")
            if len(layer_values) != 1:
                raise ZoneFillError("a filled polygon must name exactly one layer")
            points = _points(polygon, active_limits, remaining)
            remaining -= len(points)
            islands.append(
                FillIsland(
                    net_id=net_id_for_name(net_name),
                    layer_id=f"layer:{layer_values[0]}",
                    points=points,
                )
            )
    return tuple(sorted(islands, key=_island_sort_key))


def atoms_of(expression: SExpr, head: str) -> tuple[str, ...]:
    """Return the atoms of one named child, or an empty tuple when it is absent."""

    found = children(expression, head)
    if not found:
        return ()
    return atoms(found[0])


def _island_sort_key(island: FillIsland) -> tuple[str, str, tuple[tuple[int, int], ...]]:
    return (
        island.layer_id,
        island.net_id,
        tuple((point.x, point.y) for point in island.points),
    )


def fill_digest(islands: tuple[FillIsland, ...]) -> str:
    """Return a canonical, order-independent digest of poured copper geometry.

    The input is sorted here rather than trusted to arrive canonical, so the digest depends
    only on the geometry itself and never on where KiCad happened to write each node in the
    file. That matters because KiCad rewrites and reorders a board wholesale on save.
    """

    payload = json.dumps(
        [
            {
                "layer_id": island.layer_id,
                "net_id": island.net_id,
                "points": [[point.x, point.y] for point in island.points],
            }
            for island in sorted(islands, key=_island_sort_key)
        ],
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"sha256:{hashlib.sha256(payload.encode('utf-8', errors='strict')).hexdigest()}"
