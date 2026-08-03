"""Bounded, non-mutating route preview over one board inside the workspace.

This module is the first public routing surface. It parses an untrusted request,
reads one workspace board read-only, converts it through the fail-closed Board IR
adapter, proposes exactly one deterministic two-pin candidate, and optionally binds
that candidate to authoritative KiCad DRC evidence. It never writes, exports,
persists, previews into KiCad, or applies copper.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from copper_mcp.adapters import (
    KiCadConstraintProfile,
    net_id_for_name,
    parse_kicad_bytes,
)
from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    RouteCandidateDrcEvidence,
    ZoneFillAuthority,
    ZoneFillStaleError,
    run_route_candidate_drc,
    run_zone_fill_authority,
)
from copper_mcp.models import SCHEMA_VERSION
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    boolean,
    copper_layer,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    RouteCandidate,
    RouteConnection,
    RouteDiagnostic,
    RouteFailureCode,
    RouteRequest,
    VerifiedFill,
)
from copper_mcp.security import read_workspace_file

_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_MAX_NET_NAME_CHARACTERS = 255
_REQUIRED_FIELDS = ("board", "net", "layer", "constraints")
_OPTIONAL_FIELDS = (
    "seed",
    "settings",
    "include_drc",
    "include_fill_authority",
    "include_apply_token",
)
_SETTINGS_FIELDS = tuple(AStarSettings.__dataclass_fields__)


class RoutePreviewError(RequestError):
    """Raised when a route-preview request is malformed or cannot be honoured."""


class RoutePreviewStatus(StrEnum):
    """Stable outcome taxonomy for one preview attempt."""

    ROUTED = "routed"
    ALREADY_CONNECTED = "already_connected"
    NOT_ROUTED = "not_routed"
    UNSUPPORTED_BOARD = "unsupported_board"


@dataclass(frozen=True, slots=True)
class RoutePreviewRequest:
    """One validated, immutable preview request built from untrusted input."""

    board: str
    net: str
    layer: str
    constraints: NetClass
    settings: AStarSettings
    seed: int
    include_drc: bool
    include_fill_authority: bool = False
    include_apply_token: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.constraints, NetClass):
            raise RoutePreviewError("constraints must be a typed net class")
        if not isinstance(self.settings, AStarSettings):
            raise RoutePreviewError("settings must be typed router settings")
        boolean("include_drc", self.include_drc)
        boolean("include_fill_authority", self.include_fill_authority)
        boolean("include_apply_token", self.include_apply_token)
        integer("seed", self.seed, minimum=0, maximum=MAX_JSON_SAFE_INTEGER)
        board_path(self.board)
        text("net", self.net, maximum=_MAX_NET_NAME_CHARACTERS)
        copper_layer("layer", self.layer)

    @property
    def net_id(self) -> str:
        """Return the Board IR net identity for the requested KiCad net name."""

        return net_id_for_name(self.net)

    @property
    def layer_id(self) -> str:
        """Return the Board IR layer identity for the requested copper layer."""

        return f"layer:{self.layer}"

    def profile(self) -> KiCadConstraintProfile:
        """Build the typed constraint profile applied to the converted board."""

        return KiCadConstraintProfile(
            net_classes=(self.constraints,),
            default_net_class_id=self.constraints.id,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "board": self.board,
            "net": self.net,
            "layer": self.layer,
            "seed": self.seed,
            "include_drc": self.include_drc,
            "include_fill_authority": self.include_fill_authority,
            "include_apply_token": self.include_apply_token,
            "constraints": {field: getattr(self.constraints, field) for field in CONSTRAINT_FIELDS},
            "settings": {field: getattr(self.settings, field) for field in _SETTINGS_FIELDS},
        }


def _settings(payload: Any) -> AStarSettings:
    fields = mapping("settings", payload)
    known_fields("settings", fields, frozenset(_SETTINGS_FIELDS))
    overrides = {
        field: integer(f"settings.{field}", value, minimum=0, maximum=MAX_JSON_SAFE_INTEGER)
        for field, value in fields.items()
    }
    try:
        return AStarSettings(**overrides)
    except ValueError as error:
        raise RequestError(f"settings are invalid: {error}") from error


def parse_route_preview_request(payload: Any) -> RoutePreviewRequest:
    """Validate one untrusted preview request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        return RoutePreviewRequest(
            board=board_path(fields["board"]),
            net=text("net", fields["net"], maximum=_MAX_NET_NAME_CHARACTERS),
            layer=copper_layer("layer", fields["layer"]),
            constraints=net_class_constraints(fields["constraints"]),
            settings=_settings(fields.get("settings", {})),
            seed=integer("seed", fields.get("seed", 0), minimum=0, maximum=MAX_JSON_SAFE_INTEGER),
            include_drc=boolean("include_drc", fields.get("include_drc", False)),
            include_fill_authority=boolean(
                "include_fill_authority", fields.get("include_fill_authority", False)
            ),
            include_apply_token=boolean(
                "include_apply_token", fields.get("include_apply_token", False)
            ),
        )
    except RoutePreviewError:
        raise
    except RequestError as error:
        raise RoutePreviewError(str(error)) from error


def _candidate_to_dict(candidate: RouteCandidate) -> dict[str, Any]:
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
        "settings": {field: getattr(candidate.settings, field) for field in _SETTINGS_FIELDS},
    }


@dataclass(frozen=True, slots=True)
class RoutePreview:
    """One immutable, side-effect-free preview of a proposed route candidate."""

    status: RoutePreviewStatus
    board_path: str
    board_revision: str
    request: RoutePreviewRequest
    snapshot_digest: str | None = None
    candidate: RouteCandidate | None = None
    connection: RouteConnection | None = None
    fill_authority: ZoneFillAuthority | None = None
    diagnostic: RouteDiagnostic | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    drc_evidence: RouteCandidateDrcEvidence | None = None
    apply_token: str | None = None
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.status, RoutePreviewStatus):
            raise RoutePreviewError("preview status must use RoutePreviewStatus")
        if not isinstance(self.request, RoutePreviewRequest):
            raise RoutePreviewError("preview request is malformed")
        if not _SHA256_ID.fullmatch(self.board_revision):
            raise RoutePreviewError("board revision must be content-addressed with sha256")
        if not isinstance(self.board_path, str) or not self.board_path:
            raise RoutePreviewError("preview board path is malformed")
        if self.schema_version != SCHEMA_VERSION:
            raise RoutePreviewError("preview schema version is unsupported")
        if not isinstance(self.conversion_diagnostic_counts, Mapping):
            raise RoutePreviewError("conversion diagnostic counts must be a mapping")

        counts = {
            str(code): integer(
                f"diagnostic count for {code}", count, minimum=0, maximum=MAX_JSON_SAFE_INTEGER
            )
            for code, count in self.conversion_diagnostic_counts.items()
        }
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(sorted(counts.items()))),
        )

        if self.status is RoutePreviewStatus.UNSUPPORTED_BOARD:
            if (
                self.candidate is not None
                or self.connection is not None
                or self.diagnostic is not None
            ):
                raise RoutePreviewError("an unsupported board cannot carry a routing outcome")
            if self.snapshot_digest is not None or not counts:
                raise RoutePreviewError("an unsupported board must report conversion diagnostics")
        else:
            if counts:
                raise RoutePreviewError("a converted board must not report conversion errors")
            if self.snapshot_digest is None or not _SHA256_ID.fullmatch(self.snapshot_digest):
                raise RoutePreviewError("a converted board must record its Board IR digest")

        if self.status is RoutePreviewStatus.ROUTED:
            if (
                not isinstance(self.candidate, RouteCandidate)
                or self.connection is not None
                or self.diagnostic is not None
            ):
                raise RoutePreviewError("a routed preview must carry exactly one candidate")
            if self.candidate.base_revision != self.snapshot_digest:
                raise RoutePreviewError("candidate is not bound to the previewed Board IR snapshot")
        elif self.status is RoutePreviewStatus.ALREADY_CONNECTED:
            if (
                not isinstance(self.connection, RouteConnection)
                or self.candidate is not None
                or self.diagnostic is not None
            ):
                raise RoutePreviewError(
                    "an already-connected preview must carry exactly one connection"
                )
            if self.connection.base_revision != self.snapshot_digest:
                raise RoutePreviewError(
                    "connection is not bound to the previewed Board IR snapshot"
                )
        elif self.status is RoutePreviewStatus.NOT_ROUTED and (
            not isinstance(self.diagnostic, RouteDiagnostic)
            or self.candidate is not None
            or self.connection is not None
        ):
            raise RoutePreviewError("an unrouted preview must carry exactly one diagnostic")

        if self.apply_token is not None:
            # Cross-bound exactly like DRC evidence below. A token is an authorization to
            # change a file, so it must never appear on a preview that proposed no change,
            # and it must never be attachable to a different candidate than the one shown.
            if not isinstance(self.apply_token, str) or not 1 <= len(self.apply_token) <= 512:
                raise RoutePreviewError("apply token is malformed")
            if self.status is not RoutePreviewStatus.ROUTED or self.candidate is None:
                raise RoutePreviewError("an apply token requires a routed candidate")
            if not self.request.include_apply_token:
                raise RoutePreviewError("an apply token was not requested")

        if self.drc_evidence is None:
            return
        if not isinstance(self.drc_evidence, RouteCandidateDrcEvidence):
            raise RoutePreviewError("DRC evidence is malformed")
        if self.status is not RoutePreviewStatus.ROUTED or self.candidate is None:
            raise RoutePreviewError("DRC evidence requires a routed candidate")
        if (
            self.drc_evidence.candidate_id != self.candidate.candidate_id
            or self.drc_evidence.candidate_base_revision != self.candidate.base_revision
            or self.drc_evidence.source_revision != self.board_revision
        ):
            raise RoutePreviewError("DRC evidence is not bound to the previewed candidate")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached plain dictionary; mutating it cannot alter this preview."""

        return {
            "schema_version": self.schema_version,
            "status": str(self.status),
            "board_path": self.board_path,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "request": self.request.to_dict(),
            "candidate": None if self.candidate is None else _candidate_to_dict(self.candidate),
            "connection": (
                None
                if self.connection is None
                else {
                    "base_revision": self.connection.base_revision,
                    "start_pad_id": self.connection.start_pad_id,
                    "end_pad_id": self.connection.end_pad_id,
                    "attachment_segments": self.connection.attachment_segments,
                    "component_objects": self.connection.component_objects,
                    "pad_count": self.connection.pad_count,
                    "vias": self.connection.vias,
                    "fill_polygons": self.connection.fill_polygons,
                    "obstacle_checks": self.connection.obstacle_checks,
                }
            ),
            "diagnostic": (
                None
                if self.diagnostic is None
                else {
                    "code": str(self.diagnostic.code),
                    "message": self.diagnostic.message,
                    "expanded_states": self.diagnostic.expanded_states,
                    "obstacle_checks": self.diagnostic.obstacle_checks,
                }
            ),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
            "drc_evidence": (None if self.drc_evidence is None else self.drc_evidence.to_dict()),
            "apply_token": self.apply_token,
            "fill_authority": (
                None if self.fill_authority is None else self.fill_authority.to_dict()
            ),
        }


def _drc_settings(settings: Settings, deadline: float) -> Settings:
    """Clamp the KiCad timeout so authoritative DRC cannot outlive the preview deadline."""

    remaining = int(deadline - time.monotonic())
    if remaining < 1:
        raise RoutePreviewError("the preview deadline expired before authoritative DRC could run")
    return replace(
        settings,
        kicad_timeout_seconds=min(settings.kicad_timeout_seconds, remaining),
    )


def _board_is_appliable(snapshot: Any) -> bool:
    """Whether the append-only apply engine could ever apply a candidate to this board.

    Applying inserts segments carrying deterministic native identities, and refuses a board
    whose modeled geometry already uses derived (content-hash) identities. Checking that here
    means a preview never hands out an apply token the apply path would immediately reject.
    """

    from copper_mcp.adapters.kicad_route_patch import (
        KiCadRoutePatchError,
        _require_native_geometry_identities,
    )

    try:
        _require_native_geometry_identities(snapshot)
    except KiCadRoutePatchError:
        return False
    return True


def preview_route(
    payload: Any,
    settings: Settings,
    token_authority: ApplyTokenAuthority | None = None,
) -> RoutePreview:
    """Propose one deterministic candidate for a workspace board without mutating it."""

    if not isinstance(settings, Settings):
        raise RoutePreviewError("preview settings are malformed")
    deadline = time.monotonic() + settings.max_route_preview_seconds
    request = parse_route_preview_request(payload)

    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    default_limits = ParseLimits()
    limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    profile = request.profile()
    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None or conversion.diagnostics:
        counts = Counter(diagnostic.code for diagnostic in conversion.diagnostics)
        return RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            conversion_diagnostic_counts=counts,
        )

    snapshot = conversion.snapshot
    if snapshot.content.source.revision != board_revision:
        raise RoutePreviewError("converted board revision is inconsistent with its source bytes")

    if time.monotonic() >= deadline:
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.CANCELLED,
                message="the preview deadline expired during board conversion",
            ),
        )

    verified_fill: tuple[VerifiedFill, ...] = ()
    fill_authority: ZoneFillAuthority | None = None
    if request.include_fill_authority and any(
        zone.net_id == request.net_id for zone in snapshot.content.zones
    ):
        # Poured copper may only be believed when KiCad has just confirmed the board's cache
        # still describes it. Refill happens on a private disposable copy, never here.
        try:
            fill_authority, islands = run_zone_fill_authority(
                relative_path, _drc_settings(settings, deadline)
            )
        except ZoneFillStaleError:
            return RoutePreview(
                status=RoutePreviewStatus.NOT_ROUTED,
                board_path=relative_path,
                board_revision=board_revision,
                request=request,
                snapshot_digest=snapshot.snapshot_digest,
                diagnostic=RouteDiagnostic(
                    code=RouteFailureCode.STALE_FILL,
                    message="the board's cached zone fill does not match a fresh KiCad refill",
                ),
            )
        verified_fill = tuple(
            VerifiedFill(
                net_id=island.net_id,
                layer_id=island.layer_id,
                points=island.points,
                source_revision=fill_authority.source_revision,
            )
            for island in islands
        )

    result = AStarRouter().propose(
        snapshot,
        RouteRequest(
            board_revision=snapshot.snapshot_digest,
            net_id=request.net_id,
            layer_id=request.layer_id,
            seed=request.seed,
            settings=request.settings,
        ),
        cancelled=lambda: time.monotonic() >= deadline,
        verified_fill=verified_fill,
    )
    if result.connected is not None:
        # There is no candidate to bind evidence to, so `include_drc` is skipped rather than
        # failed: the fail-closed rule protects a proposal, and no copper is being proposed.
        return RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            connection=result.connected,
            fill_authority=fill_authority if result.connected.fill_polygons else None,
        )
    if result.candidate is None:
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=result.diagnostic,
        )

    evidence = None
    if request.include_drc:
        evidence = run_route_candidate_drc(
            relative_path,
            result.candidate,
            profile,
            _drc_settings(settings, deadline),
        )
    apply_token = None
    if (
        request.include_apply_token
        and token_authority is not None
        and settings.allow_apply
        and _board_is_appliable(snapshot)
    ):
        # Issued only for a routed candidate on an *appliable* board, when apply is enabled.
        # Gating on the flag stops a library embedder minting tokens with apply off, and gating
        # on appliability stops a token being minted for a board whose derived geometry
        # identities the append-only apply engine would reject - which used to surface as an
        # uncaught crash from the destructive tool. The token is bound to the four things that
        # make an apply unambiguous: which candidate, which snapshot, which bytes, which path.
        apply_token = token_authority.issue(
            ApplyBinding(
                candidate_id=result.candidate.candidate_id,
                base_revision=result.candidate.base_revision,
                board_revision=board_revision,
                relative_path=relative_path,
            )
        )
    return RoutePreview(
        status=RoutePreviewStatus.ROUTED,
        board_path=relative_path,
        board_revision=board_revision,
        request=request,
        snapshot_digest=snapshot.snapshot_digest,
        candidate=result.candidate,
        drc_evidence=evidence,
        apply_token=apply_token,
    )
