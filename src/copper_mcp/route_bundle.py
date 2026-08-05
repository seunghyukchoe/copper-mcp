"""Atomic, read-only composition preview for a bounded set of route references.

This adapter deliberately wraps the existing negotiated deterministic core rather than accepting
model supplied copper.  A successful result is all-or-nothing: every requested two-pin net has a
candidate from the same Board IR revision, the coordinator has replayed the allocation, and its
exact pairwise cross-net clearance gate has accepted it.  The bundle is a plan only; it carries no
apply token, board bytes, persistence handle, or mutation authority.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    copper_layer,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
)
from copper_mcp.route_preview import RoutePreviewError, _digest, _net_ref_id, _settings
from copper_mcp.routing import (
    NegotiatedRoutingRequest,
    NegotiatedRoutingStatus,
    RouteCandidate,
    RouteRequest,
    negotiate_routes,
    verify_candidate_id,
)
from copper_mcp.security import read_workspace_file

_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_REQUIRED_FIELDS = (
    "board",
    "layer",
    "constraints",
    "net_ref_ids",
    "expect_board_revision",
    "expect_snapshot_digest",
)
_OPTIONAL_FIELDS = ("seed", "settings")
_MAX_NETS = 8


class RouteBundleError(RequestError):
    """Raised when a route-bundle request is malformed or cannot be honoured."""


class RouteBundleStatus(StrEnum):
    """Terminal status for an atomic route-bundle preview."""

    ROUTED = "routed"
    NOT_ROUTED = "not_routed"
    UNSUPPORTED_BOARD = "unsupported_board"


@dataclass(frozen=True, slots=True)
class RouteBundleRequest:
    """Closed, reference-only request for one immutable composed route plan."""

    board: str
    layer: str
    constraints: NetClass
    net_ref_ids: tuple[str, ...]
    expect_board_revision: str
    expect_snapshot_digest: str
    seed: int
    settings: Any

    def __post_init__(self) -> None:
        board_path(self.board)
        copper_layer("layer", self.layer)
        if not isinstance(self.constraints, NetClass):
            raise RouteBundleError("constraints must be a typed net class")
        if not isinstance(self.net_ref_ids, tuple) or not 2 <= len(self.net_ref_ids) <= _MAX_NETS:
            raise RouteBundleError("route bundles require a bounded set of net references")
        if len(set(self.net_ref_ids)) != len(self.net_ref_ids):
            raise RouteBundleError("route bundle net references must be distinct")
        for reference in self.net_ref_ids:
            _net_ref_id(reference)
        _digest("expect_board_revision", self.expect_board_revision)
        _digest("expect_snapshot_digest", self.expect_snapshot_digest)
        integer("seed", self.seed, minimum=0, maximum=MAX_JSON_SAFE_INTEGER)

    @property
    def layer_id(self) -> str:
        return f"layer:{self.layer}"

    def profile(self) -> KiCadConstraintProfile:
        return KiCadConstraintProfile(
            net_classes=(self.constraints,), default_net_class_id=self.constraints.id
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "layer": self.layer,
            "constraints": {field: getattr(self.constraints, field) for field in CONSTRAINT_FIELDS},
            "net_ref_ids": list(self.net_ref_ids),
            "expect_board_revision": self.expect_board_revision,
            "expect_snapshot_digest": self.expect_snapshot_digest,
            "seed": self.seed,
            "settings": {
                field: getattr(self.settings, field) for field in self.settings.__dataclass_fields__
            },
        }


def parse_route_bundle_request(payload: Any) -> RouteBundleRequest:
    """Validate untrusted MCP/application input without echoing caller-controlled values."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        raw_refs = fields["net_ref_ids"]
        if not isinstance(raw_refs, list):
            raise RouteBundleError("net_ref_ids must be an ordered list")
        references = tuple(_net_ref_id(reference) for reference in raw_refs)
        return RouteBundleRequest(
            board=board_path(fields["board"]),
            layer=copper_layer("layer", fields["layer"]),
            constraints=net_class_constraints(fields["constraints"]),
            net_ref_ids=references,
            expect_board_revision=_digest("expect_board_revision", fields["expect_board_revision"]),
            expect_snapshot_digest=_digest(
                "expect_snapshot_digest", fields["expect_snapshot_digest"]
            ),
            seed=integer("seed", fields.get("seed", 0), minimum=0, maximum=MAX_JSON_SAFE_INTEGER),
            settings=_settings(fields.get("settings", {})),
        )
    except RouteBundleError:
        raise
    except (RequestError, RoutePreviewError) as error:
        raise RouteBundleError(str(error)) from error


def _candidate_document(candidate: RouteCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "base_revision": candidate.base_revision,
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "router_version": candidate.router_version,
        "policy": candidate.policy,
        "seed": candidate.seed,
        "pad_count": candidate.pad_count,
        "ordering_policy": candidate.ordering_policy,
        "patch": {
            "net_id": candidate.patch.net_id,
            "layer_id": candidate.patch.layer_id,
            "width_nm": candidate.patch.width_nm,
            "paths": [
                {"vertices_nm": [[point.x, point.y] for point in path.vertices]}
                for path in candidate.patch.paths
            ],
        },
        "cost": {
            "length_nm": candidate.cost.length_nm,
            "bend_count": candidate.cost.bend_count,
            "bend_cost_nm": candidate.cost.bend_cost_nm,
            "proximity_steps": candidate.cost.proximity_steps,
            "proximity_cost_nm": candidate.cost.proximity_cost_nm,
            "via_cost_nm": candidate.cost.via_cost_nm,
            "total_cost_nm": candidate.cost.total_cost_nm,
        },
        "metrics": {
            "hard_internal_violations": candidate.metrics.hard_internal_violations,
            "unrouted_connections": candidate.metrics.unrouted_connections,
            "vias": candidate.metrics.vias,
            "wire_length_nm": candidate.metrics.wire_length_nm,
            "expanded_states": candidate.metrics.expanded_states,
            "peak_frontier_states": candidate.metrics.peak_frontier_states,
            "obstacle_checks": candidate.metrics.obstacle_checks,
        },
        "settings": {
            field: getattr(candidate.settings, field)
            for field in candidate.settings.__dataclass_fields__
        },
    }


def _bundle_bytes(plan: RouteBundlePlan) -> bytes:
    payload = {
        "base_revision": plan.base_revision,
        "candidate_ids": [candidate.candidate_id for candidate in plan.candidates],
        "core_replays": plan.core_replays,
        "layer_id": plan.layer_id,
        "net_ref_ids": list(plan.net_ref_ids),
        "physical_pair_checks": plan.physical_pair_checks,
        "schema": "copper-mcp.route-bundle.v1",
        "settings": {
            field: getattr(plan.settings, field) for field in plan.settings.__dataclass_fields__
        },
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8") + b"\n"


@dataclass(frozen=True, slots=True)
class RouteBundlePlan:
    """One immutable, all-or-nothing composition of deterministic candidates."""

    bundle_id: str
    base_revision: str
    layer_id: str
    net_ref_ids: tuple[str, ...]
    candidates: tuple[RouteCandidate, ...]
    settings: Any
    core_replays: int
    physical_pair_checks: int

    def __post_init__(self) -> None:
        if not _SHA256_ID.fullmatch(self.bundle_id) or not _SHA256_ID.fullmatch(self.base_revision):
            raise RouteBundleError("route bundle identity is malformed")
        if not isinstance(self.net_ref_ids, tuple) or len(self.net_ref_ids) != len(self.candidates):
            raise RouteBundleError("route bundle candidates do not cover the requested nets")
        candidate_net_ids = tuple(candidate.patch.net_id for candidate in self.candidates)
        if tuple(sorted(self.net_ref_ids)) != candidate_net_ids:
            raise RouteBundleError("route bundle candidates are not canonical")
        if any(candidate.base_revision != self.base_revision for candidate in self.candidates):
            raise RouteBundleError("route bundle candidate revision is inconsistent")
        if any(candidate.patch.layer_id != self.layer_id for candidate in self.candidates):
            raise RouteBundleError("route bundle candidate layer is inconsistent")
        if self.core_replays != 1 or not 0 <= self.physical_pair_checks <= MAX_JSON_SAFE_INTEGER:
            raise RouteBundleError("route bundle verification evidence is malformed")
        for candidate in self.candidates:
            try:
                verify_candidate_id(candidate)
            except ValueError as error:
                raise RouteBundleError("route bundle candidate identity is invalid") from error
        expected = f"sha256:{hashlib.sha256(_bundle_bytes(self)).hexdigest()}"
        if self.bundle_id != expected:
            raise RouteBundleError("route bundle identity does not match its immutable content")

    @property
    def total_wire_length_nm(self) -> int:
        return sum(candidate.patch.length_nm for candidate in self.candidates)

    def to_dict(self) -> dict[str, Any]:
        return {
            "bundle_id": self.bundle_id,
            "base_revision": self.base_revision,
            "layer_id": self.layer_id,
            "net_ref_ids": list(self.net_ref_ids),
            "candidates": [_candidate_document(candidate) for candidate in self.candidates],
            "metrics": {
                "candidate_count": len(self.candidates),
                "core_replays": self.core_replays,
                "physical_pair_checks": self.physical_pair_checks,
                "total_wire_length_nm": self.total_wire_length_nm,
            },
        }


@dataclass(frozen=True, slots=True)
class RouteBundlePreview:
    """Read-only response that only exposes a plan after full deterministic composition."""

    status: RouteBundleStatus
    board_path: str
    board_revision: str
    request: RouteBundleRequest
    snapshot_digest: str | None = None
    plan: RouteBundlePlan | None = None
    diagnostic: str | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.status, RouteBundleStatus) or not _SHA256_ID.fullmatch(
            self.board_revision
        ):
            raise RouteBundleError("route bundle preview is malformed")
        counts = {
            str(key): integer(
                "conversion diagnostic count",
                value,
                minimum=0,
                maximum=MAX_JSON_SAFE_INTEGER,
            )
            for key, value in self.conversion_diagnostic_counts.items()
        }
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(sorted(counts.items()))),
        )
        if self.status is RouteBundleStatus.ROUTED:
            if (
                self.plan is None
                or self.diagnostic is not None
                or self.snapshot_digest != self.plan.base_revision
            ):
                raise RouteBundleError("a routed bundle preview must carry one bound plan")
        elif self.status is RouteBundleStatus.UNSUPPORTED_BOARD:
            if (
                self.snapshot_digest is not None
                or self.plan is not None
                or self.diagnostic is not None
                or not counts
            ):
                raise RouteBundleError(
                    "an unsupported bundle board must carry conversion diagnostics"
                )
        elif self.plan is not None or self.diagnostic is None:
            raise RouteBundleError("an unsuccessful bundle preview must carry no plan")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": "1.0",
            "status": str(self.status),
            "board_path": self.board_path,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "request": self.request.to_dict(),
            "plan": None if self.plan is None else self.plan.to_dict(),
            "diagnostic": self.diagnostic,
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


def _routes(snapshot_digest: str, request: RouteBundleRequest) -> tuple[RouteRequest, ...]:
    return tuple(
        RouteRequest(
            board_revision=snapshot_digest,
            net_id=net_ref_id,
            layer_id=request.layer_id,
            seed=request.seed + index,
            settings=request.settings,
        )
        for index, net_ref_id in enumerate(request.net_ref_ids)
    )


def _plan(snapshot: Any, request: RouteBundleRequest, deadline: float) -> RouteBundlePlan | str:
    envelope = NegotiatedRoutingRequest(
        board_revision=snapshot.snapshot_digest,
        requests=_routes(snapshot.snapshot_digest, request),
        max_iterations=8,
    )

    def cancelled() -> bool:
        return time.monotonic() >= deadline

    first = negotiate_routes(snapshot, envelope, cancelled=cancelled)
    # Replaying the entire allocation, rather than each independent route, proves that the
    # negotiated occupancy and physical-clearance decision is reproducible as one composition.
    replay = negotiate_routes(snapshot, envelope, cancelled=cancelled)
    if first != replay:
        return "the deterministic route-bundle replay did not reproduce the allocation"
    if (
        first.status is not NegotiatedRoutingStatus.COMPLETED
        or not first.ok
        or len(first.candidates) != len(request.net_ref_ids)
        or first.connections
        or first.unrouted_nets
    ):
        return "the requested route bundle could not be composed atomically"
    # Build the exact bytes before the final constructor verifies its identity.
    digest_payload = {
        "base_revision": snapshot.snapshot_digest,
        "candidate_ids": [candidate.candidate_id for candidate in first.candidates],
        "core_replays": 1,
        "layer_id": request.layer_id,
        "net_ref_ids": list(request.net_ref_ids),
        "physical_pair_checks": first.total_physical_checks,
        "schema": "copper-mcp.route-bundle.v1",
        "settings": {
            field: getattr(request.settings, field)
            for field in request.settings.__dataclass_fields__
        },
    }
    bundle_id = (
        "sha256:"
        + hashlib.sha256(
            json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            + b"\n"
        ).hexdigest()
    )
    return RouteBundlePlan(
        bundle_id=bundle_id,
        base_revision=snapshot.snapshot_digest,
        layer_id=request.layer_id,
        net_ref_ids=request.net_ref_ids,
        candidates=first.candidates,
        settings=request.settings,
        core_replays=1,
        physical_pair_checks=first.total_physical_checks,
    )


def preview_route_bundle(payload: Any, settings: Settings) -> RouteBundlePreview:
    """Return one bounded, immutable composed plan without applying or authorizing copper."""

    if not isinstance(settings, Settings):
        raise RouteBundleError("route bundle settings are malformed")
    request = parse_route_bundle_request(payload)
    deadline = time.monotonic() + settings.max_route_preview_seconds
    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    if board_revision != request.expect_board_revision:
        return RouteBundlePreview(
            RouteBundleStatus.NOT_ROUTED,
            relative_path,
            board_revision,
            request,
            diagnostic="the observed scene no longer matches the current board bytes",
        )
    limits = ParseLimits(
        max_input_bytes=min(ParseLimits().max_input_bytes, settings.max_board_bytes)
    )
    conversion = parse_kicad_bytes(source, request.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        return RouteBundlePreview(
            RouteBundleStatus.UNSUPPORTED_BOARD,
            relative_path,
            board_revision,
            request,
            conversion_diagnostic_counts=Counter(item.code for item in conversion.diagnostics),
        )
    snapshot = conversion.snapshot
    if snapshot.snapshot_digest != request.expect_snapshot_digest:
        return RouteBundlePreview(
            RouteBundleStatus.NOT_ROUTED,
            relative_path,
            board_revision,
            request,
            snapshot.snapshot_digest,
            diagnostic="the observed scene no longer matches the current routing snapshot",
        )
    plan = _plan(snapshot, request, deadline)
    if isinstance(plan, str):
        return RouteBundlePreview(
            RouteBundleStatus.NOT_ROUTED,
            relative_path,
            board_revision,
            request,
            snapshot.snapshot_digest,
            diagnostic=plan,
        )
    return RouteBundlePreview(
        RouteBundleStatus.ROUTED,
        relative_path,
        board_revision,
        request,
        snapshot.snapshot_digest,
        plan=plan,
    )


__all__ = [
    "RouteBundleError",
    "RouteBundlePlan",
    "RouteBundlePreview",
    "RouteBundleRequest",
    "RouteBundleStatus",
    "parse_route_bundle_request",
    "preview_route_bundle",
]
