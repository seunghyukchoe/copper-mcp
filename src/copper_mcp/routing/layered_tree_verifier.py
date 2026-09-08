"""Bounded layer-aware structural verification for private layered trees."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from itertools import combinations
from typing import cast

from copper_mcp.board_ir import BoardIRSnapshot, PointNM, verify_snapshot
from copper_mcp.routing._layered_tree_cancel import (
    LayeredTreeCancellationError,
    poll_cancelled,
)
from copper_mcp.routing.layered_candidate_verifier import (
    _point_in_pad_envelope,
    _point_on_segment,
    _segment_key,
    _segments_intersect,
)
from copper_mcp.routing.layered_tree_contracts import (
    LayeredTreeCandidate,
    _admit_layered_tree_candidate,
    layered_tree_physical_edges,
    layered_tree_physical_vias,
    verify_layered_tree_candidate_id,
)


@dataclass(frozen=True, slots=True)
class LayeredTreeVerification:
    ok: bool
    code: str
    pair_checks: int = 0

    def __post_init__(self) -> None:
        if type(self.ok) is not bool or type(self.code) is not str or not self.code:
            raise ValueError("layered tree verification result is malformed")
        if type(self.pair_checks) is not int or self.pair_checks < 0:
            raise ValueError("layered tree verification work is malformed")


class _UnionFind:
    def __init__(self, size: int) -> None:
        self.parent = list(range(size))

    def find(self, item: int) -> int:
        while self.parent[item] != item:
            self.parent[item] = self.parent[self.parent[item]]
            item = self.parent[item]
        return item

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self.parent[right_root] = left_root


def _failure(code: str, checks: int = 0) -> LayeredTreeVerification:
    return LayeredTreeVerification(False, code, checks)


def _valid_limits(max_segments: object, max_vias: object, max_pair_checks: object) -> bool:
    return (
        type(max_segments) is int
        and type(max_vias) is int
        and type(max_pair_checks) is int
        and 1 <= max_segments <= 16_384
        and 1 <= max_vias <= 256
        and 1 <= max_pair_checks <= 2_000_000
    )


def _typed_id(value: object, prefix: str) -> bool:
    return (
        type(value) is str
        and value.startswith(prefix)
        and 1 <= len(value.removeprefix(prefix)) <= 160
        and all(
            character.isascii() and (character.isalnum() or character in "_.:-")
            for character in value
        )
    )


def _cancellation_code(cancelled: Callable[[], bool] | None) -> str | None:
    try:
        return "cancelled" if poll_cancelled(cancelled) else None
    except LayeredTreeCancellationError:
        return "cancellation_failed"


class _VerificationStoppedError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code


def _admit_candidate_shape(candidate: object) -> tuple[int, int] | None:
    try:
        return _admit_layered_tree_candidate(candidate)
    except (TypeError, ValueError):
        return None


def _connected(
    segments: list[tuple[str, PointNM, PointNM]],
    vias: list[tuple[PointNM, str, str]],
    terminals: list[tuple[PointNM, tuple[str, ...]]],
    *,
    max_pair_checks: int,
    cancelled: Callable[[], bool] | None,
) -> LayeredTreeVerification:
    total = len(segments) + len(vias) + len(terminals)
    union = _UnionFind(total)
    checks = 0
    seen: set[tuple[str, PointNM, PointNM]] = set()
    for layer_id, start, end in segments:
        stopped = _cancellation_code(cancelled)
        if stopped is not None:
            return _failure(stopped, checks)
        key = _segment_key(layer_id, start, end)
        if key in seen:
            return _failure("duplicate_geometry", checks)
        seen.add(key)
    for (left_index, left), (right_index, right) in combinations(enumerate(segments), 2):
        stopped = _cancellation_code(cancelled)
        if stopped is not None:
            return _failure(stopped, checks)
        if left[0] != right[0]:
            continue
        checks += 1
        if checks > max_pair_checks:
            return _failure("budget_exhausted", checks)
        if _segments_intersect((left[1], left[2]), (right[1], right[2])):
            union.union(left_index, right_index)
    via_offset = len(segments)
    for via_index, (center, _start_layer, _end_layer) in enumerate(vias):
        touched_layers: set[str] = set()
        for segment_index, (layer_id, start, end) in enumerate(segments):
            stopped = _cancellation_code(cancelled)
            if stopped is not None:
                return _failure(stopped, checks)
            checks += 1
            if checks > max_pair_checks:
                return _failure("budget_exhausted", checks)
            if _point_on_segment(center, start, end):
                union.union(via_offset + via_index, segment_index)
                touched_layers.add(layer_id)
        if len(touched_layers) < 2:
            return _failure("via_discontinuity", checks)
    terminal_offset = via_offset + len(vias)
    source_contacts: set[int] = set()
    for (left_index, terminal_left), (right_index, terminal_right) in combinations(
        enumerate(terminals), 2
    ):
        stopped = _cancellation_code(cancelled)
        if stopped is not None:
            return _failure(stopped, checks)
        checks += 1
        if checks > max_pair_checks:
            return _failure("budget_exhausted", checks)
        if terminal_left[0] == terminal_right[0] and set(terminal_left[1]) & set(terminal_right[1]):
            union.union(terminal_offset + left_index, terminal_offset + right_index)
            source_contacts.update((left_index, right_index))
    for terminal_index, (center, layer_ids) in enumerate(terminals):
        attached = terminal_index in source_contacts
        for segment_index, (segment_layer, start, end) in enumerate(segments):
            stopped = _cancellation_code(cancelled)
            if stopped is not None:
                return _failure(stopped, checks)
            checks += 1
            if checks > max_pair_checks:
                return _failure("budget_exhausted", checks)
            if segment_layer in layer_ids and _point_on_segment(center, start, end):
                union.union(terminal_offset + terminal_index, segment_index)
                attached = True
        if not attached:
            return _failure("disconnected_terminal", checks)
    roots = {union.find(terminal_offset + index) for index in range(len(terminals))}
    return (
        LayeredTreeVerification(True, "verified", checks)
        if len(roots) == 1
        else _failure("disconnected_terminal", checks)
    )


def verify_layered_tree_candidate(
    candidate: object,
    snapshot: object,
    *,
    expected_request: object | None = None,
    max_segments: int = 16_384,
    max_vias: int = 256,
    max_pair_checks: int = 2_000_000,
    cancelled: Callable[[], bool] | None = None,
) -> LayeredTreeVerification:
    """Verify identity, branch chains, and complete layer-aware pad connectivity."""

    if not _valid_limits(max_segments, max_vias, max_pair_checks):
        return _failure("invalid_limits")
    cancelled_object: object = cancelled
    if cancelled_object is not None and not callable(cancelled_object):
        return _failure("cancellation_failed")
    active_cancelled = cast(Callable[[], bool] | None, cancelled_object)
    stopped = _cancellation_code(active_cancelled)
    if stopped is not None:
        return _failure(stopped)
    if type(candidate) is not LayeredTreeCandidate:
        return _failure("invalid_candidate")
    admitted = _admit_candidate_shape(candidate)
    if admitted is None:
        return _failure("budget_exhausted")
    try:
        candidate.__post_init__()
    except (TypeError, ValueError):
        return _failure("invalid_candidate")
    stopped = _cancellation_code(active_cancelled)
    if stopped is not None:
        return _failure(stopped)
    if type(snapshot) is not BoardIRSnapshot:
        return _failure("invalid_snapshot")
    try:
        verify_snapshot(snapshot)
    except (TypeError, ValueError):
        return _failure("invalid_snapshot")
    segment_count, _via_references = admitted
    try:
        via_count = len(layered_tree_physical_vias(candidate.branches))
    except ValueError:
        return _failure("invalid_candidate")
    if segment_count > max_segments or via_count > max_vias:
        return _failure("budget_exhausted")
    try:

        def hash_checkpoint() -> None:
            stopped_code = _cancellation_code(active_cancelled)
            if stopped_code is not None:
                raise _VerificationStoppedError(stopped_code)

        verify_layered_tree_candidate_id(candidate, checkpoint=hash_checkpoint)
    except _VerificationStoppedError as error:
        return _failure(error.code)
    except (TypeError, ValueError):
        return _failure("invalid_candidate")
    if candidate.base_revision != snapshot.snapshot_digest:
        return _failure("stale_revision")
    if expected_request is not None:
        from copper_mcp.routing.layered_tree_router import LayeredTreeRequest

        if (
            type(expected_request) is not LayeredTreeRequest
            or expected_request.board_revision != candidate.base_revision
            or expected_request.net_id != candidate.net_id
            or expected_request.terminals != candidate.terminals
            or expected_request.grid_step_nm != candidate.grid_step_nm
            or expected_request.settings != candidate.settings
            or expected_request.seed != candidate.seed
        ):
            return _failure("request_mismatch")
    pads = {pad.id: pad for pad in snapshot.content.pads}
    net_pad_ids = tuple(
        sorted(pad.id for pad in snapshot.content.pads if pad.net_id == candidate.net_id)
    )
    if net_pad_ids != tuple(item.pad_id for item in candidate.terminals):
        return _failure("terminal_mismatch")
    net_class_id = next(
        (
            item.net_class_id
            for item in snapshot.content.constraints.assignments
            if item.net_id == candidate.net_id
        ),
        None,
    )
    net_class = next(
        (item for item in snapshot.content.constraints.net_classes if item.id == net_class_id),
        None,
    )
    if (
        net_class is None
        or candidate.width_nm != net_class.track_width_nm
        or candidate.via_diameter_nm != net_class.via_diameter_nm
        or candidate.via_drill_nm != net_class.via_drill_nm
    ):
        return _failure("dimension_mismatch")
    layers = tuple(sorted(snapshot.content.copper_layers, key=lambda item: (item.index, item.id)))
    if not 2 <= len(layers) <= 8 or any(item.kind != "signal" for item in layers):
        return _failure("layer_mismatch")
    layer_ids = {item.id for item in layers}
    outer_span = {layers[0].id, layers[-1].id}
    terminal_by_id = {item.pad_id: item for item in candidate.terminals}
    if any(
        terminal.layer_id not in layer_ids
        or terminal.layer_id not in pads[terminal.pad_id].layer_ids
        for terminal in candidate.terminals
    ):
        return _failure("terminal_layer_mismatch")
    segments = list(layered_tree_physical_edges(candidate.branches))
    physical_vias = layered_tree_physical_vias(candidate.branches)
    vias = [(via.center, via.start_layer_id, via.end_layer_id) for via in physical_vias]
    prior_segments: list[tuple[str, PointNM, PointNM]] = []
    prior_via_centers: set[PointNM] = set()
    for branch in candidate.branches:
        stopped = _cancellation_code(active_cancelled)
        if stopped is not None:
            return _failure(stopped)
        start_terminal = terminal_by_id.get(branch.start_pad_id)
        end_terminal = terminal_by_id.get(branch.end_pad_id)
        if start_terminal is None or end_terminal is None:
            return _failure("terminal_mismatch")
        direct_attachment = (
            branch.attachment_point == pads[branch.end_pad_id].center
            and branch.attachment_layer_id == end_terminal.layer_id
        )
        copper_attachment = any(
            layer_id == branch.attachment_layer_id
            and _point_on_segment(branch.attachment_point, start, end)
            for layer_id, start, end in prior_segments
        )
        shared_via_attachment = branch.attachment_point in prior_via_centers
        if branch.attachment_only:
            if (
                branch.paths
                or branch.vias
                or branch.attachment_point != pads[branch.start_pad_id].center
                or branch.attachment_layer_id != start_terminal.layer_id
                or not (direct_attachment or copper_attachment or shared_via_attachment)
            ):
                return _failure("branch_attachment_mismatch")
            continue
        if (
            branch.paths[0].vertices[0] != pads[branch.start_pad_id].center
            or branch.paths[-1].vertices[-1] != branch.attachment_point
            or branch.paths[0].layer_id != start_terminal.layer_id
            or branch.paths[-1].layer_id != branch.attachment_layer_id
        ):
            return _failure("branch_endpoint_mismatch")
        if not (direct_attachment or copper_attachment or shared_via_attachment):
            return _failure("branch_attachment_mismatch")
        for path in branch.paths:
            if path.layer_id not in layer_ids:
                return _failure("layer_mismatch")
            prior_segments.extend(
                (path.layer_id, start, end)
                for start, end in zip(path.vertices, path.vertices[1:], strict=False)
            )
        for via_index, via in enumerate(branch.vias):
            if (
                {via.start_layer_id, via.end_layer_id} != outer_span
                or via.center != branch.paths[via_index].vertices[-1]
                or via.center != branch.paths[via_index + 1].vertices[0]
                or branch.paths[via_index].layer_id == branch.paths[via_index + 1].layer_id
                or via.diameter_nm != candidate.via_diameter_nm
                or via.drill_nm != candidate.via_drill_nm
            ):
                return _failure("via_discontinuity")
            if any(
                _point_in_pad_envelope(via.center, pads[terminal.pad_id])
                for terminal in candidate.terminals
            ):
                return _failure("unsupported_via_in_pad")
            prior_via_centers.add(via.center)
    terminals: list[tuple[PointNM, tuple[str, ...]]] = [
        (pads[item.pad_id].center, (item.layer_id,)) for item in candidate.terminals
    ]
    result = _connected(
        segments,
        vias,
        terminals,
        max_pair_checks=max_pair_checks,
        cancelled=active_cancelled,
    )
    stopped = _cancellation_code(active_cancelled)
    return _failure(stopped, result.pair_checks) if stopped is not None else result


def verify_reparsed_layered_tree_connectivity(
    snapshot: BoardIRSnapshot,
    net_id: str,
    terminal_pad_ids: tuple[str, ...],
    *,
    max_segments: int = 16_384,
    max_vias: int = 256,
    max_pair_checks: int = 2_000_000,
    cancelled: Callable[[], bool] | None = None,
) -> LayeredTreeVerification:
    """Prove all original terminals share one component in reparsed Board IR."""

    if not _valid_limits(max_segments, max_vias, max_pair_checks):
        return _failure("invalid_limits")
    cancelled_object: object = cancelled
    if cancelled_object is not None and not callable(cancelled_object):
        return _failure("cancellation_failed")
    active_cancelled = cast(Callable[[], bool] | None, cancelled_object)
    stopped = _cancellation_code(active_cancelled)
    if stopped is not None:
        return _failure(stopped)
    if type(snapshot) is not BoardIRSnapshot:
        return _failure("invalid_snapshot")
    try:
        verify_snapshot(snapshot)
    except (TypeError, ValueError):
        return _failure("invalid_snapshot")
    if (
        not _typed_id(net_id, "net:")
        or type(terminal_pad_ids) is not tuple
        or not 2 <= len(terminal_pad_ids) <= 32
        or any(not _typed_id(pad_id, "pad:") for pad_id in terminal_pad_ids)
        or tuple(sorted(set(terminal_pad_ids))) != terminal_pad_ids
    ):
        return _failure("terminal_mismatch")
    complete_pad_ids = tuple(
        sorted(pad.id for pad in snapshot.content.pads if pad.net_id == net_id)
    )
    if terminal_pad_ids != complete_pad_ids:
        return _failure("terminal_mismatch")
    segments: list[tuple[str, PointNM, PointNM]] = []
    for segment in snapshot.content.segments:
        stopped = _cancellation_code(active_cancelled)
        if stopped is not None:
            return _failure(stopped)
        if segment.net_id == net_id:
            segments.append((segment.layer_id, segment.start, segment.end))
            if len(segments) > max_segments:
                return _failure("budget_exhausted")
    vias: list[tuple[PointNM, str, str]] = []
    for via in snapshot.content.vias:
        stopped = _cancellation_code(active_cancelled)
        if stopped is not None:
            return _failure(stopped)
        if via.net_id == net_id:
            vias.append((via.center, via.start_layer_id, via.end_layer_id))
            if len(vias) > max_vias:
                return _failure("budget_exhausted")
    pads = {item.id: item for item in snapshot.content.pads}
    outer = {
        item.id
        for item in sorted(snapshot.content.copper_layers, key=lambda item: (item.index, item.id))[
            :1
        ]
    } | {
        item.id
        for item in sorted(snapshot.content.copper_layers, key=lambda item: (item.index, item.id))[
            -1:
        ]
    }
    if any({start, end} != outer for _center, start, end in vias):
        return _failure("via_discontinuity")
    terminals = [(pads[pad_id].center, pads[pad_id].layer_ids) for pad_id in terminal_pad_ids]
    result = _connected(
        segments,
        vias,
        terminals,
        max_pair_checks=max_pair_checks,
        cancelled=active_cancelled,
    )
    stopped = _cancellation_code(active_cancelled)
    return _failure(stopped, result.pair_checks) if stopped is not None else result


__all__: list[str] = []
