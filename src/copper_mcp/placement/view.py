"""Placement view derived from revision-bound Board IR v0.2 footprints."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType

from copper_mcp.board_ir import (
    BoardIRSnapshot,
    BoardIRValidationError,
    Pad,
    ParseLimits,
    PointNM,
    validate_content,
    verify_snapshot,
)
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
    locked: bool
    #: Union of the footprint's pad bounds in the board frame, as currently placed.
    hull: Rect

    def __post_init__(self) -> None:
        if not self.ref_id.startswith("footprint:"):
            raise PlacementViewError("footprint reference must be typed")
        if self.side not in {"front", "back"}:
            raise PlacementViewError("footprint side must be front or back")
        if not self.pad_ids:
            raise PlacementViewError("a placeable footprint must own at least one pad")
        if not isinstance(self.locked, bool):
            raise PlacementViewError("footprint locked state must be boolean")


@dataclass(frozen=True, slots=True)
class PlacementView:
    """Footprint grouping for one board, bound to the exact bytes it was read from."""

    board_revision: str
    snapshot_digest: str
    footprints: Mapping[str, FootprintView]
    #: Reverse index, so a rule naming a pad can be resolved to the footprint that owns it.
    owner_by_pad: Mapping[str, str] = field(default_factory=dict)
    #: Footprints that exist in Board IR but own no copper pad, so this version cannot place
    #: them. They are kept out of ``footprints`` because a ``FootprintView`` is by definition
    #: placeable - it must have a pad hull to evaluate rules against - but their identity is
    #: retained so a caller naming one is told it cannot be placed rather than that it does not
    #: exist. The second answer would be false: Board IR carries it and the scene reports it.
    padless_refs: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        object.__setattr__(self, "footprints", MappingProxyType(dict(self.footprints)))
        object.__setattr__(self, "owner_by_pad", MappingProxyType(dict(self.owner_by_pad)))
        object.__setattr__(self, "padless_refs", frozenset(self.padless_refs))

    def resolve(self, ref_id: str) -> FootprintView | None:
        """Resolve a footprint reference, or the footprint owning a pad reference."""

        direct = self.footprints.get(ref_id)
        if direct is not None:
            return direct
        owner = self.owner_by_pad.get(ref_id)
        return None if owner is None else self.footprints.get(owner)

    def is_padless(self, ref_id: str) -> bool:
        """Whether a reference names a real footprint this version cannot place."""

        return ref_id in self.padless_refs


def build_placement_view(
    source: bytes,
    snapshot: BoardIRSnapshot,
    *,
    limits: ParseLimits | None = None,
) -> PlacementView:
    """Build the placement view from footprint data already bound into Board IR v0.2."""

    if not isinstance(source, bytes):
        raise PlacementViewError("board source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise PlacementViewError("board snapshot is malformed")
    if limits is not None and not isinstance(limits, ParseLimits):
        raise PlacementViewError("placement view limits are malformed")
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    if snapshot.content.source.revision != board_revision:
        raise PlacementViewError("board source and Board IR snapshot revisions disagree")
    try:
        # Enforce caller-tightened ceilings before canonical digest work. ``verify_snapshot``
        # then applies the default contract limits, canonical-order check, and digest binding.
        if limits is not None:
            validate_content(snapshot.content, limits)
        verify_snapshot(snapshot)
    except BoardIRValidationError as error:
        raise PlacementViewError("Board IR snapshot failed placement-view validation") from error

    pads_by_id: dict[str, Pad] = {pad.id: pad for pad in snapshot.content.pads}
    owner_by_pad: dict[str, str] = {}
    footprints: dict[str, FootprintView] = {}
    padless_refs: set[str] = set()

    for footprint in snapshot.content.footprints:
        hull: Rect | None = None
        for pad_id in footprint.pad_ids:
            pad = pads_by_id.get(pad_id)
            if pad is None:
                raise PlacementViewError(
                    "board source and Board IR snapshot disagree about pad identity"
                )
            if pad_id in owner_by_pad:
                raise PlacementViewError("a Board IR pad belongs to more than one footprint")
            owner_by_pad[pad_id] = footprint.id
            bounds = pad_bounds(pad)
            hull = bounds if hull is None else union(hull, bounds)

        if not footprint.pad_ids or hull is None:
            # A graphics-only footprint is faithfully present in Board IR, but this placement
            # version has no copper hull with which to evaluate its pad-based rules. Its
            # identity is kept so naming it is refused as unplaceable rather than as unknown.
            padless_refs.add(footprint.id)
            continue
        footprints[footprint.id] = FootprintView(
            ref_id=footprint.id,
            origin=footprint.origin,
            orientation_udeg=footprint.rotation_udeg,
            side=footprint.side.value,
            pad_ids=footprint.pad_ids,
            locked=footprint.locked,
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
        padless_refs=frozenset(padless_refs),
    )


__all__ = [
    "FootprintView",
    "PlacementView",
    "PlacementViewError",
    "build_placement_view",
]
