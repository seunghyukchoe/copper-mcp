"""The pure half of route-candidate application: bytes in, bytes out.

Nothing here touches the filesystem. The engine takes a board's source bytes and a verified
candidate and returns the bytes that *would* be written, together with the evidence that they
are safe to write. Deciding whether to write them - authorization, locking, staleness, atomic
replacement - is the mutating path, which is designed but not yet built.

Three assertions have to hold before the result is offered to anyone, and all three run here:

1. **Every untouched byte is identical.** A route patch is purely additive and is spliced in at
   the root's closing delimiter, so the assertion is total: everything before the splice and
   everything after it must match the source exactly, bit for bit.
2. **The result reparses through the fail-closed adapter**, with no diagnostics.
3. **The resulting Board IR equals the source IR plus the candidate, exactly** - no other
   object added, removed, or altered.

Unlike the disposable board rendered for candidate DRC, an applied board is **not** stamped
with CopperMCP writer metadata. That board is our derivative and claiming authorship of it is
honest; this one is the user's, authored by KiCad, to which we are adding tracks. Rewriting its
``generator`` field would both misattribute it and break assertion 1.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from itertools import pairwise

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.cst import CstError, Splice, apply_splices, root_close_offset

# These are the shared internals of the KiCad patch surface: identity derivation, segment
# rendering, and the replay check. Reusing them rather than re-deriving keeps the applied board
# byte-identical to the disposable board that candidate DRC already validates.
from copper_mcp.adapters.kicad_route_patch import (
    _modeled_object_count,
    _render_segment,
    _replay_candidate,
    _require_native_geometry_identities,
    _segment_uuid,
    _source_structure,
)
from copper_mcp.board_ir import BoardIRSnapshot, ParseLimits, Segment
from copper_mcp.routing.astar import VerifiedFill, verify_candidate_id
from copper_mcp.routing.contracts import RouteCandidate


class ApplyEngineError(ValueError):
    """Raised when a candidate cannot be applied to the supplied board bytes."""


@dataclass(frozen=True, slots=True)
class ApplyVerification:
    """What was checked, in the vocabulary of what was actually performed.

    The three performed checks are literals rather than booleans so a caller cannot read a
    missing check as a passing one. The two unperformed ones are named explicitly: this engine
    never runs KiCad, so it can say nothing about whether the applied board opens or passes
    DRC, and there is no value here in which it could claim otherwise.
    """

    untouched_bytes_identical: str = "passed"
    reparse_fail_closed: str = "passed"
    ir_equals_source_plus_patch: str = "passed"
    kicad_opened_board: str = "not_run"
    drc_after_apply: str = "not_run"

    def __post_init__(self) -> None:
        performed = (
            "untouched_bytes_identical",
            "reparse_fail_closed",
            "ir_equals_source_plus_patch",
        )
        for name in performed:
            if getattr(self, name) != "passed":
                raise ApplyEngineError("a verification stage may only be recorded once it passed")
        for name in ("kicad_opened_board", "drc_after_apply"):
            if getattr(self, name) != "not_run":
                raise ApplyEngineError("this engine never runs KiCad and must not claim to")

    def to_dict(self) -> dict[str, str]:
        return {
            "untouched_bytes_identical": self.untouched_bytes_identical,
            "reparse_fail_closed": self.reparse_fail_closed,
            "ir_equals_source_plus_patch": self.ir_equals_source_plus_patch,
            "kicad_opened_board": self.kicad_opened_board,
            "drc_after_apply": self.drc_after_apply,
        }


@dataclass(frozen=True, slots=True)
class AppliedBoard:
    """Bytes that may be written, and the evidence that they are the right bytes."""

    content: bytes
    source_revision: str
    result_revision: str
    base_revision: str
    candidate_id: str
    splice_offset: int
    bytes_added: int
    segments_added: int
    verification: ApplyVerification

    def to_dict(self) -> dict[str, object]:
        return {
            "source_revision": self.source_revision,
            "result_revision": self.result_revision,
            "base_revision": self.base_revision,
            "candidate_id": self.candidate_id,
            "splice_offset": self.splice_offset,
            "bytes_added": self.bytes_added,
            "segments_added": self.segments_added,
            "verification": self.verification.to_dict(),
        }


def _revision(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def apply_route_candidate(
    source: bytes,
    snapshot: BoardIRSnapshot,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    *,
    limits: ParseLimits | None = None,
    verified_fill: tuple[VerifiedFill, ...] = (),
) -> AppliedBoard:
    """Return the board bytes that applying ``candidate`` to ``source`` would produce.

    ``verified_fill`` is the freshness-bound zone fill the candidate was routed under. Nothing
    in the shipped apply service holds it - apply runs in a later process than the preview that
    established it - so a candidate carrying a fill binding refuses here rather than being
    replayed under the conservative envelope and blamed for the disagreement.
    """

    limits = limits or ParseLimits()
    if not isinstance(source, bytes):
        raise ApplyEngineError("board source must be immutable bytes")
    if not isinstance(snapshot, BoardIRSnapshot):
        raise ApplyEngineError("board snapshot is malformed")
    if not isinstance(candidate, RouteCandidate):
        raise ApplyEngineError("route candidate is malformed")
    if not isinstance(profile, KiCadConstraintProfile):
        raise ApplyEngineError("KiCad constraint profile is malformed")
    if not isinstance(limits, ParseLimits):
        raise ApplyEngineError("parse limits are malformed")

    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        raise ApplyEngineError("board source cannot be represented by the supported Board IR")
    if conversion.snapshot != snapshot:
        raise ApplyEngineError("board source and constraint profile do not match the snapshot")
    _require_native_geometry_identities(snapshot)
    if candidate.base_revision != snapshot.snapshot_digest:
        raise ApplyEngineError("candidate is stale for the supplied board snapshot")
    # Never trusted from its manifest: the identity is recomputed from the candidate's own
    # content, and the geometry is replayed against the board before a single byte is written.
    try:
        verify_candidate_id(candidate)
    except ValueError as error:
        raise ApplyEngineError("candidate identity verification failed") from error
    _replay_candidate(snapshot, candidate, verified_fill)

    nets = {item.id: item for item in snapshot.content.nets}
    layers = {item.id: item for item in snapshot.content.copper_layers}
    net = nets.get(candidate.patch.net_id)
    layer = layers.get(candidate.patch.layer_id)
    if net is None or layer is None:
        raise ApplyEngineError("candidate references an unknown net or copper layer")

    edges = [edge for path in candidate.patch.paths for edge in pairwise(path.vertices)]
    if not edges:
        raise ApplyEngineError("candidate carries no routed geometry to apply")
    if _modeled_object_count(snapshot) + len(edges) > limits.max_objects:
        raise ApplyEngineError("applied board exceeds the configured object budget")

    _, native_identities = _source_structure(source, limits)
    text = source.decode("utf-8", errors="strict")
    try:
        close = root_close_offset(text)
    except CstError as error:
        raise ApplyEngineError("board source has no root closing delimiter") from error

    rendered: list[str] = []
    expected_segments: list[Segment] = []
    for index, (start, end) in enumerate(edges):
        native_uuid = _segment_uuid(candidate.candidate_id, index)
        if native_uuid in native_identities:
            raise ApplyEngineError("deterministic route identity collides with the source board")
        rendered.append(
            _render_segment(
                start_x_nm=start.x,
                start_y_nm=start.y,
                end_x_nm=end.x,
                end_y_nm=end.y,
                width_nm=candidate.patch.width_nm,
                layer_name=layer.name,
                net_name=net.name,
                segment_uuid=native_uuid,
            ).decode("utf-8", errors="strict")
        )
        expected_segments.append(
            Segment(
                id=f"segment:kicad:{native_uuid}",
                net_id=net.id,
                layer_id=layer.id,
                start=start,
                end=end,
                width_nm=candidate.patch.width_nm,
            )
        )

    separator = "" if text[:close].endswith("\n") else "\n"
    insertion = separator + "".join(rendered)
    try:
        result = apply_splices(text, [Splice(close, close, insertion)]).encode(
            "utf-8", errors="strict"
        )
    except CstError as error:
        raise ApplyEngineError("route patch could not be spliced into the board") from error
    if len(result) > limits.max_input_bytes:
        raise ApplyEngineError("applied board exceeds the input-byte budget")

    _assert_untouched_bytes_identical(source, result, text, close, insertion)
    patched = _assert_reparses_clean(result, profile, limits)
    _assert_ir_equals_source_plus_patch(snapshot, patched, expected_segments)

    return AppliedBoard(
        content=result,
        source_revision=_revision(source),
        result_revision=_revision(result),
        base_revision=snapshot.snapshot_digest,
        candidate_id=candidate.candidate_id,
        splice_offset=close,
        bytes_added=len(result) - len(source),
        segments_added=len(expected_segments),
        verification=ApplyVerification(),
    )


def _assert_untouched_bytes_identical(
    source: bytes, result: bytes, text: str, close: int, insertion: str
) -> None:
    """Prove that only the inserted region differs, in bytes rather than in characters.

    The splice is expressed in characters, so the byte boundaries are recovered by encoding
    the prefix and suffix. Comparing the two halves directly is what makes this an assertion
    about the file rather than about our model of it.
    """

    prefix = text[:close].encode("utf-8", errors="strict")
    suffix = text[close:].encode("utf-8", errors="strict")
    added = insertion.encode("utf-8", errors="strict")
    if source != prefix + suffix:
        raise ApplyEngineError("board source did not round-trip through its own decoding")
    if len(result) != len(prefix) + len(added) + len(suffix):
        raise ApplyEngineError("applied board is not the source with one insertion")
    if result[: len(prefix)] != prefix:
        raise ApplyEngineError("bytes before the route patch were modified")
    if result[len(prefix) + len(added) :] != suffix:
        raise ApplyEngineError("bytes after the route patch were modified")


def _assert_reparses_clean(
    result: bytes, profile: KiCadConstraintProfile, limits: ParseLimits
) -> BoardIRSnapshot:
    patched = parse_kicad_bytes(result, profile, limits)
    if patched.snapshot is None or patched.diagnostics:
        raise ApplyEngineError("applied board failed Board IR round-trip parsing")
    return patched.snapshot


def _assert_ir_equals_source_plus_patch(
    snapshot: BoardIRSnapshot,
    patched: BoardIRSnapshot,
    expected_segments: list[Segment],
) -> None:
    """The applied board must differ from its source by the patch and nothing else.

    Only the source revision may move, because the bytes changed. The ``generator`` field is
    deliberately *not* rewritten, so it must come back exactly as the board's author left it -
    an applied board is the user's file with tracks added, not a CopperMCP artifact.
    """

    expected_source = replace(snapshot.content.source, revision=patched.content.source.revision)
    expected_content = replace(
        snapshot.content,
        source=expected_source,
        segments=tuple(
            sorted(snapshot.content.segments + tuple(expected_segments), key=lambda item: item.id)
        ),
    )
    if patched.content != expected_content:
        raise ApplyEngineError("applied board changed content outside its route patch")


__all__ = [
    "AppliedBoard",
    "ApplyEngineError",
    "ApplyVerification",
    "apply_route_candidate",
]
