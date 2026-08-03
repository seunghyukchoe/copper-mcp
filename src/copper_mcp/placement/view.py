"""Footprint identity recovered out of band and joined to Board IR pads.

Board IR has no footprint object: pads are flattened to board level and carry no parent
reference. Placement moves footprints, so that grouping has to come from somewhere.

Adding footprints to Board IR would cost a schema version bump under ADR-0005 and would change
the ``snapshot_digest`` of every board ever converted. The cheaper and equally honest route is
the one the scene already uses for board text: read the grouping out of band from the same
source bytes, and bind the result to both digests so a caller can tell whether it still
describes the board in front of it.

The join is **total**, not best-effort. A pad with a native UUID joins by that UUID; a pad
without one is given a ``derived`` id by the adapter, computed as a hash over the source
revision, the object kind and the source locator - all of which are reproducible here from the
same bytes. Both cases are covered, and a view that cannot account for every pad refuses.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from copper_mcp.adapters.sexpr import SExpr, SExprError, children, parse_sexpr
from copper_mcp.board_ir import BoardIRSnapshot, Pad, ParseLimits, PointNM
from copper_mcp.placement.geometry import Rect, pad_bounds, union


class PlacementViewError(RuntimeError):
    """Raised when footprint identity cannot be recovered from a board."""


@dataclass(frozen=True, slots=True)
class FootprintView:
    """One placeable footprint: its identity, where it sits, and which pads it owns."""

    ref_id: str
    origin: PointNM
    orientation_udeg: int
    side: str
    pad_ids: tuple[str, ...]
    #: Union of the footprint's pad bounds in the board frame, as currently placed.
    hull: Rect

    def __post_init__(self) -> None:
        if not self.ref_id.startswith("footprint:"):
            raise PlacementViewError("footprint reference must be typed")
        if self.side not in {"front", "back"}:
            raise PlacementViewError("footprint side must be front or back")
        if not self.pad_ids:
            raise PlacementViewError("a placeable footprint must own at least one pad")


@dataclass(frozen=True, slots=True)
class PlacementView:
    """Footprint grouping for one board, bound to the exact bytes it was read from."""

    board_revision: str
    snapshot_digest: str
    footprints: Mapping[str, FootprintView]
    #: Reverse index, so a rule naming a pad can be resolved to the footprint that owns it.
    owner_by_pad: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(self, "footprints", MappingProxyType(dict(self.footprints)))
        object.__setattr__(self, "owner_by_pad", MappingProxyType(dict(self.owner_by_pad)))

    def resolve(self, ref_id: str) -> FootprintView | None:
        """Resolve a footprint reference, or the footprint owning a pad reference."""

        direct = self.footprints.get(ref_id)
        if direct is not None:
            return direct
        owner = self.owner_by_pad.get(ref_id)
        return None if owner is None else self.footprints.get(owner)


def _atoms(node: SExpr) -> tuple[str, ...]:
    payload: list[str] = []
    for value in node.items[1:]:
        if not isinstance(value, str):
            break
        payload.append(value)
    return tuple(payload)


def _uuid(node: SExpr) -> str | None:
    found = children(node, "uuid")
    if not found:
        return None
    values = _atoms(found[0])
    return values[0] if values else None


def _derived_id(source_revision: str, kind: str, locator: str) -> str:
    """Reproduce the adapter's derived identity for a pad that carries no UUID.

    Mirrors ``kicad_board_ir`` exactly. Duplicated rather than imported because it is a
    *contract* between the adapter's output and this join: if the adapter's derivation ever
    changes, the join must fail loudly here rather than silently follow it.
    """

    material = f"{source_revision}\0{kind}\0{locator}".encode()
    return f"{kind}:derived:{hashlib.sha256(material).hexdigest()[:32]}"


def build_placement_view(
    source: bytes,
    snapshot: BoardIRSnapshot,
    *,
    limits: ParseLimits | None = None,
) -> PlacementView:
    """Recover footprint identity for one board and join it to that board's Board IR pads."""

    if not isinstance(source, bytes):
        raise PlacementViewError("board source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise PlacementViewError("board snapshot is malformed")
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    try:
        root = parse_sexpr(source, limits or ParseLimits())
    except SExprError as error:
        raise PlacementViewError("board source could not be parsed for footprints") from error

    pads_by_id: dict[str, Pad] = {pad.id: pad for pad in snapshot.content.pads}
    owner_by_pad: dict[str, str] = {}
    footprints: dict[str, FootprintView] = {}

    for footprint_index, footprint in enumerate(children(root, "footprint")):
        locator_prefix = f"kicad_pcb.footprint[{footprint_index}]"
        footprint_uuid = _uuid(footprint)
        ref_id = (
            f"footprint:kicad:{footprint_uuid.lower()}"
            if footprint_uuid
            else _derived_id(board_revision, "footprint", locator_prefix)
        )
        placement = children(footprint, "at")
        if not placement:
            raise PlacementViewError("a footprint without a placement cannot be placed")
        values = _atoms(placement[0])
        if len(values) < 2:
            raise PlacementViewError("footprint placement is malformed")
        try:
            origin = PointNM(_nanometres(values[0]), _nanometres(values[1]))
            orientation = _microdegrees(values[2]) if len(values) > 2 else 0
        except ValueError as error:
            raise PlacementViewError("footprint placement is not exactly representable") from error

        layers = children(footprint, "layer")
        layer_values = _atoms(layers[0]) if layers else ()
        side = "back" if layer_values and layer_values[0].startswith("B.") else "front"

        owned: list[str] = []
        hull: Rect | None = None
        for pad_index, pad_node in enumerate(children(footprint, "pad")):
            pad_uuid = _uuid(pad_node)
            pad_id = (
                f"pad:kicad:{pad_uuid.lower()}"
                if pad_uuid
                else _derived_id(board_revision, "pad", f"{locator_prefix}.pad[{pad_index}]")
            )
            pad = pads_by_id.get(pad_id)
            if pad is None:
                # The join is total by construction, so a miss means the source and the
                # snapshot are not the same board - or the adapter's identity rule moved.
                raise PlacementViewError(
                    "board source and Board IR snapshot disagree about pad identity"
                )
            owned.append(pad_id)
            owner_by_pad[pad_id] = ref_id
            bounds = pad_bounds(pad)
            hull = bounds if hull is None else union(hull, bounds)

        if not owned or hull is None:
            # A footprint with no copper pads cannot be placed against copper rules, and
            # silently dropping it would let a rule name a reference that never resolves.
            continue
        footprints[ref_id] = FootprintView(
            ref_id=ref_id,
            origin=origin,
            orientation_udeg=orientation % 360_000_000,
            side=side,
            pad_ids=tuple(owned),
            hull=hull,
        )

    unowned = set(pads_by_id) - set(owner_by_pad)
    if unowned:
        raise PlacementViewError(
            f"{len(unowned)} pad(s) in Board IR could not be attributed to a footprint"
        )
    return PlacementView(
        board_revision=board_revision,
        snapshot_digest=snapshot.snapshot_digest,
        footprints=footprints,
        owner_by_pad=owner_by_pad,
    )


def _nanometres(token: str) -> int:
    """Convert a millimetre token exactly, refusing anything below one nanometre."""

    text = token.strip()
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    whole, _, fraction = text.partition(".")
    if not whole.isdigit() and whole != "":
        raise ValueError("millimetre token is malformed")
    if fraction and not fraction.isdigit():
        raise ValueError("millimetre token is malformed")
    if len(fraction) > 6:
        raise ValueError("millimetre token is finer than one nanometre")
    scaled = int(whole or "0") * 1_000_000 + int((fraction or "0").ljust(6, "0"))
    return -scaled if negative else scaled


def _microdegrees(token: str) -> int:
    text = token.strip()
    negative = text.startswith("-")
    if negative or text.startswith("+"):
        text = text[1:]
    whole, _, fraction = text.partition(".")
    if (whole and not whole.isdigit()) or (fraction and not fraction.isdigit()):
        raise ValueError("degree token is malformed")
    if len(fraction) > 6:
        raise ValueError("degree token is finer than one microdegree")
    scaled = int(whole or "0") * 1_000_000 + int((fraction or "0").ljust(6, "0"))
    return -scaled if negative else scaled


__all__ = [
    "FootprintView",
    "PlacementView",
    "PlacementViewError",
    "build_placement_view",
]
