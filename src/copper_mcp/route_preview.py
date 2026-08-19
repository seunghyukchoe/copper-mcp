"""Bounded, non-mutating route preview over one board inside the workspace.

This module is the first public routing surface. It parses an untrusted request,
reads one workspace board read-only, converts it through the fail-closed Board IR
adapter, proposes at most one deterministic single-layer candidate, and optionally binds
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
from copper_mcp.apply_token_reasons import (
    APPLY_TOKEN_WITHHELD_REASONS,
    ApplyTokenWithheldReason,
    apply_token_withheld_reason,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    RouteCandidateDrcEvidence,
    ZoneFillAuthority,
    ZoneFillStaleError,
    run_route_candidate_drc,
    run_zone_fill_authority,
)
from copper_mcp.kicad_ipc import capture_live_board
from copper_mcp.parse_budgets import parse_limits_for
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
    OffGridEvidence,
    RouteCandidate,
    RouteConnection,
    RouteDiagnostic,
    RouteFailureCode,
    RouteRequest,
    VerifiedFill,
)
from copper_mcp.security import read_workspace_file

ROUTE_PREVIEW_SCHEMA_VERSION = "1.1"

_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_NET_REF_ID = re.compile(r"^net:name:[0-9a-f]{32}$")
_MAX_NET_NAME_CHARACTERS = 255
_REQUIRED_FIELDS = ("board", "layer", "constraints")
_OPTIONAL_FIELDS = (
    "net",
    "net_ref_id",
    "expect_board_revision",
    "expect_snapshot_digest",
    "seed",
    "settings",
    "include_drc",
    "include_fill_authority",
    "include_apply_token",
)
_SETTINGS_FIELDS = tuple(AStarSettings.__dataclass_fields__)
_FILL_ROUTING_EFFECTS = frozenset(
    {"foreign_zone_obstacles", "connectivity_evidence", "both", "verified_context"}
)


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
    layer: str
    constraints: NetClass
    settings: AStarSettings
    seed: int
    include_drc: bool
    net: str | None = None
    net_ref_id: str | None = None
    expect_board_revision: str | None = None
    expect_snapshot_digest: str | None = None
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
        copper_layer("layer", self.layer)
        if (self.net is None) == (self.net_ref_id is None):
            raise RoutePreviewError("exactly one net selector is required")
        if self.net is not None:
            text("net", self.net, maximum=_MAX_NET_NAME_CHARACTERS)
        if self.net_ref_id is not None:
            _net_ref_id(self.net_ref_id)
            if self.expect_board_revision is None or self.expect_snapshot_digest is None:
                raise RoutePreviewError(
                    "a net reference requires board and snapshot revision preconditions"
                )
        for name, revision in (
            ("expect_board_revision", self.expect_board_revision),
            ("expect_snapshot_digest", self.expect_snapshot_digest),
        ):
            if revision is not None:
                _digest(name, revision)

    @property
    def net_id(self) -> str:
        """Return the selected Board IR net identity without re-hashing a scene reference."""

        if self.net_ref_id is not None:
            return self.net_ref_id
        assert self.net is not None
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
        document: dict[str, Any] = {
            "board": self.board,
            "layer": self.layer,
            "seed": self.seed,
            "include_drc": self.include_drc,
            "include_fill_authority": self.include_fill_authority,
            "include_apply_token": self.include_apply_token,
            "constraints": {field: getattr(self.constraints, field) for field in CONSTRAINT_FIELDS},
            "settings": {field: getattr(self.settings, field) for field in _SETTINGS_FIELDS},
        }
        if self.net is not None:
            document["net"] = self.net
        else:
            document["net_ref_id"] = self.net_ref_id
        if self.expect_board_revision is not None:
            document["expect_board_revision"] = self.expect_board_revision
        if self.expect_snapshot_digest is not None:
            document["expect_snapshot_digest"] = self.expect_snapshot_digest
        return document


def _off_grid_document(evidence: OffGridEvidence | None) -> dict[str, Any] | None:
    """Serialize the geometry of one ``off_grid`` refusal, or ``None`` when there is none.

    Every other diagnostic reports ``None`` here rather than an empty object or a placeholder
    number, because a diagnostic that never measured a lattice has nothing to say about one.
    """

    if evidence is None:
        return None
    return {
        "pad_id": evidence.pad_id,
        "anchor_pad_id": evidence.anchor_pad_id,
        "grid_step_nm": evidence.grid_step_nm,
        "miss_x_nm": evidence.miss_x_nm,
        "miss_y_nm": evidence.miss_y_nm,
        "largest_representable_step_nm": evidence.largest_representable_step_nm,
    }


def _net_ref_id(value: Any) -> str:
    """Validate one Board IR net reference without accepting a raw KiCad name."""

    reference = text("net_ref_id", value, maximum=164)
    if not _NET_REF_ID.fullmatch(reference):
        raise RoutePreviewError("net_ref_id must be a stable Board IR net reference")
    return reference


def _digest(name: str, value: Any) -> str:
    """Validate one content-addressed precondition without echoing its value."""

    revision = text(name, value, maximum=71)
    if not _SHA256_ID.fullmatch(revision):
        raise RoutePreviewError(f"{name} must be content-addressed with sha256")
    return revision


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
        if ("net" in fields) == ("net_ref_id" in fields):
            raise RoutePreviewError("request must contain exactly one net selector")
        return RoutePreviewRequest(
            board=board_path(fields["board"]),
            layer=copper_layer("layer", fields["layer"]),
            constraints=net_class_constraints(fields["constraints"]),
            settings=_settings(fields.get("settings", {})),
            seed=integer("seed", fields.get("seed", 0), minimum=0, maximum=MAX_JSON_SAFE_INTEGER),
            include_drc=boolean("include_drc", fields.get("include_drc", False)),
            net=(
                text("net", fields["net"], maximum=_MAX_NET_NAME_CHARACTERS)
                if "net" in fields
                else None
            ),
            net_ref_id=_net_ref_id(fields["net_ref_id"]) if "net_ref_id" in fields else None,
            expect_board_revision=(
                _digest("expect_board_revision", fields["expect_board_revision"])
                if "expect_board_revision" in fields
                else None
            ),
            expect_snapshot_digest=(
                _digest("expect_snapshot_digest", fields["expect_snapshot_digest"])
                if "expect_snapshot_digest" in fields
                else None
            ),
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
        # `null` when the conservative zone envelope was the obstacle model, which is the
        # ordinary case. The *canonical identity* payload omits the key entirely in that case
        # (ADR-0103); this document is not content-addressed and follows the response
        # convention of naming every field.
        "fill_binding": candidate.fill_binding,
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


def _fill_routing_effect(
    fills: tuple[VerifiedFill, ...],
    routed_net_id: str,
    routed_layer_id: str,
) -> str:
    """Describe how freshness-bound islands affected one routed preview.

    The deterministic router remains the authority for geometry. This label only makes the
    already-verified evidence legible to an MCP caller: foreign islands become exact obstacles,
    same-net islands can prove connectivity, and an empty selected-layer cache is still a
    verified context rather than an inferred absence of copper.
    """

    selected = tuple(fill for fill in fills if fill.layer_id == routed_layer_id)
    foreign = any(fill.net_id != routed_net_id for fill in selected)
    same_net = any(fill.net_id == routed_net_id for fill in selected)
    if foreign and same_net:
        return "both"
    if foreign:
        return "foreign_zone_obstacles"
    if same_net:
        return "connectivity_evidence"
    return "verified_context"


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
    fill_routing_effect: str | None = None
    diagnostic: RouteDiagnostic | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    drc_evidence: RouteCandidateDrcEvidence | None = None
    apply_token: str | None = None
    #: Exactly one of ``apply_token`` and this field is set. A caller reading ``null`` used to
    #: have no way to tell "you did not ask" from "this board can never be applied to"; the
    #: closed set in :mod:`copper_mcp.apply_token_reasons` is now the answer (R-149).
    apply_token_withheld_reason: ApplyTokenWithheldReason | None = None
    schema_version: str = ROUTE_PREVIEW_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.status, RoutePreviewStatus):
            raise RoutePreviewError("preview status must use RoutePreviewStatus")
        if not isinstance(self.request, RoutePreviewRequest):
            raise RoutePreviewError("preview request is malformed")
        if not _SHA256_ID.fullmatch(self.board_revision):
            raise RoutePreviewError("board revision must be content-addressed with sha256")
        if not isinstance(self.board_path, str) or not self.board_path:
            raise RoutePreviewError("preview board path is malformed")
        if self.schema_version != ROUTE_PREVIEW_SCHEMA_VERSION:
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

        stale_before_conversion = (
            self.status is RoutePreviewStatus.NOT_ROUTED
            and self.snapshot_digest is None
            and isinstance(self.diagnostic, RouteDiagnostic)
            and self.diagnostic.code is RouteFailureCode.STALE_REVISION
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
        elif stale_before_conversion:
            if counts:
                raise RoutePreviewError("a stale board must not report conversion errors")
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
            if self.apply_token_withheld_reason is not None:
                raise RoutePreviewError("an issued apply token cannot also be withheld")
        elif self.apply_token_withheld_reason not in APPLY_TOKEN_WITHHELD_REASONS:
            # No default, and no silence. Every construction site that returns no token states
            # which closed reason it is returning, so an unlisted or forgotten one fails here
            # rather than reaching a caller as an unexplained `null`.
            raise RoutePreviewError("a withheld apply token must name a closed reason")

        if self.fill_authority is not None:
            if not isinstance(self.fill_authority, ZoneFillAuthority):
                raise RoutePreviewError("fill authority is malformed")
            if self.status not in (RoutePreviewStatus.ROUTED, RoutePreviewStatus.ALREADY_CONNECTED):
                raise RoutePreviewError("fill authority requires a routing outcome")
            if not self.request.include_fill_authority:
                raise RoutePreviewError("fill authority was not requested")
            if self.fill_authority.source_revision != self.board_revision:
                raise RoutePreviewError("fill authority is not bound to the previewed board")
            if self.fill_routing_effect not in _FILL_ROUTING_EFFECTS:
                raise RoutePreviewError("fill authority routing effect is malformed")
        elif self.fill_routing_effect is not None:
            raise RoutePreviewError("fill routing effect requires fill authority")
        if (
            self.status is RoutePreviewStatus.ALREADY_CONNECTED
            and self.connection is not None
            and self.connection.fill_polygons
            and self.fill_authority is None
        ):
            raise RoutePreviewError("fill-connected evidence requires fill authority")

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
                    "off_grid": _off_grid_document(self.diagnostic.off_grid),
                }
            ),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
            "drc_evidence": (None if self.drc_evidence is None else self.drc_evidence.to_dict()),
            "apply_token": self.apply_token,
            "apply_token_withheld_reason": self.apply_token_withheld_reason,
            "fill_authority": (
                None
                if self.fill_authority is None
                else {
                    **self.fill_authority.to_dict(),
                    "routing_effect": self.fill_routing_effect,
                }
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
    apply_enabled = token_authority is not None and settings.allow_apply
    # One reason serves every return taken before a candidate exists. What the request asked
    # for and what this server permits are already known here, and `has_candidate=False` is
    # what all of those returns have in common.
    withheld_without_candidate = apply_token_withheld_reason(
        requested=request.include_apply_token,
        apply_enabled=apply_enabled,
        has_candidate=False,
    )
    assert withheld_without_candidate is not None

    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"

    if (
        request.expect_board_revision is not None
        and request.expect_board_revision != board_revision
    ):
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.STALE_REVISION,
                message="the observed scene no longer matches the current board bytes",
            ),
            apply_token_withheld_reason=withheld_without_candidate,
        )

    limits = parse_limits_for(settings)
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
            apply_token_withheld_reason=withheld_without_candidate,
        )

    snapshot = conversion.snapshot
    if snapshot.content.source.revision != board_revision:
        raise RoutePreviewError("converted board revision is inconsistent with its source bytes")

    if (
        request.expect_snapshot_digest is not None
        and request.expect_snapshot_digest != snapshot.snapshot_digest
    ):
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.STALE_REVISION,
                message="the observed scene no longer matches the current routing snapshot",
            ),
            apply_token_withheld_reason=withheld_without_candidate,
        )

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
            apply_token_withheld_reason=withheld_without_candidate,
        )

    verified_fill: tuple[VerifiedFill, ...] = ()
    fill_authority: ZoneFillAuthority | None = None
    if request.include_fill_authority and any(
        zone.layer_id == request.layer_id for zone in snapshot.content.zones
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
                apply_token_withheld_reason=withheld_without_candidate,
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
            fill_routing_effect=(
                "connectivity_evidence" if result.connected.fill_polygons else None
            ),
            apply_token_withheld_reason=withheld_without_candidate,
        )
    if result.candidate is None:
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=relative_path,
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=result.diagnostic,
            apply_token_withheld_reason=withheld_without_candidate,
        )

    evidence = None
    if request.include_drc:
        # The fill goes with the candidate. DRC replays it at the serialization boundary, and a
        # candidate shaped by the exact pour cannot reproduce under the conservative envelope.
        evidence = run_route_candidate_drc(
            relative_path,
            result.candidate,
            profile,
            _drc_settings(settings, deadline),
            verified_fill=verified_fill,
        )
    # Issued only for a routed candidate on an *appliable* board, when apply is enabled.
    # Gating on the flag stops a library embedder minting tokens with apply off, and gating
    # on appliability stops a token being minted for a board whose derived geometry
    # identities the append-only apply engine would reject - which used to surface as an
    # uncaught crash from the destructive tool. The token is bound to the four things that
    # make an apply unambiguous: which candidate, which snapshot, which bytes, which path.
    #
    # A candidate the exact pour shaped is withheld for the same reason (#163, ADR-0103):
    # apply runs in a later process and holds no fill evidence, so it can only replay under
    # the conservative envelope, which is not the model that produced the route. A token is
    # a capability, and a capability whose exercise is guaranteed to refuse must not be
    # issued. The candidate and its DRC evidence are still returned.
    #
    # `_board_is_appliable` walks every modeled object, so it stays behind the cheap gates the
    # shared order already checks first: when one of those has closed, the value it would
    # return cannot change the reason, and the scan is skipped exactly as the old `and` chain
    # skipped it.
    gate_open = request.include_apply_token and apply_enabled
    withheld = apply_token_withheld_reason(
        requested=request.include_apply_token,
        apply_enabled=apply_enabled,
        has_candidate=True,
        board_appliable=not gate_open or _board_is_appliable(snapshot),
        fill_bound=gate_open and result.candidate.fill_binding is not None,
    )
    apply_token = None
    if withheld is None:
        assert token_authority is not None
        apply_token = token_authority.issue(
            ApplyBinding(
                candidate_id=result.candidate.candidate_id,
                base_revision=result.candidate.base_revision,
                board_revision=board_revision,
                relative_path=relative_path,
                operation="route",
            )
        )
    return RoutePreview(
        status=RoutePreviewStatus.ROUTED,
        board_path=relative_path,
        board_revision=board_revision,
        request=request,
        snapshot_digest=snapshot.snapshot_digest,
        candidate=result.candidate,
        fill_authority=fill_authority,
        fill_routing_effect=(
            _fill_routing_effect(
                verified_fill,
                request.net_id,
                request.layer_id,
            )
            if fill_authority is not None
            else None
        ),
        drc_evidence=evidence,
        apply_token=apply_token,
        apply_token_withheld_reason=withheld,
    )


def preview_live_route(
    payload: Any,
    settings: Settings,
    *,
    client_factory: Any = None,
) -> RoutePreview:
    """Propose one read-only route against the exact active KiCad IPC snapshot.

    Live proposals intentionally accept only a Circuit Scene ``net_ref_id`` and require both
    revision preconditions. They never invoke KiCad DRC, zone refill, or apply-token issuance:
    those operations need separate live session compare-and-swap contracts. The returned
    candidate is bound to the captured source and Board IR snapshot, so an AI client can inspect
    the active editor and receive a deterministic proposal without mutating it.
    """

    if not isinstance(settings, Settings):
        raise RoutePreviewError("preview settings are malformed")
    deadline = time.monotonic() + settings.max_route_preview_seconds
    request = parse_route_preview_request(payload)
    if request.board != "live":
        raise RoutePreviewError("live route requests must set board to 'live'")
    if request.net is not None or request.net_ref_id is None:
        raise RoutePreviewError("live route requests require a Circuit Scene net reference")
    if request.include_drc or request.include_fill_authority or request.include_apply_token:
        raise RoutePreviewError("live route proposals are read-only and cannot request actions")
    # This surface mints nothing at all, so the reason is fixed and does not depend on the
    # request or on the operator's apply flag: `include_apply_token` is refused above, and no
    # setting makes a live single-layer proposal issuable.
    live_withheld = apply_token_withheld_reason(
        surface_mints_tokens=False,
        requested=False,
        apply_enabled=False,
        has_candidate=False,
    )
    assert live_withheld is not None

    # Capture must share the preview's bounded wall-clock budget. The IPC binding accepts a
    # millisecond timeout capped at ten seconds; passing both it and the absolute deadline keeps
    # the individual call and its multi-step capture from silently consuming the default timeout.
    remaining_ms = max(1, min(10_000, int((deadline - time.monotonic()) * 1_000)))
    captured = capture_live_board(
        settings,
        client_factory=client_factory,
        timeout_ms=remaining_ms,
        deadline=deadline,
    )
    board_revision = captured.observation.board_digest
    if (
        request.expect_board_revision is not None
        and request.expect_board_revision != board_revision
    ):
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path="live",
            board_revision=board_revision,
            request=request,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.STALE_REVISION,
                message="the observed live board no longer matches the requested revision",
            ),
            apply_token_withheld_reason=live_withheld,
        )

    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(captured.source, request.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        counts = Counter(diagnostic.code for diagnostic in conversion.diagnostics)
        return RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path="live",
            board_revision=board_revision,
            request=request,
            conversion_diagnostic_counts=counts,
            apply_token_withheld_reason=live_withheld,
        )

    snapshot = conversion.snapshot
    if snapshot.content.source.revision != board_revision:
        raise RoutePreviewError(
            "converted live board revision is inconsistent with its source bytes"
        )
    if (
        request.expect_snapshot_digest is not None
        and request.expect_snapshot_digest != snapshot.snapshot_digest
    ):
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path="live",
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.STALE_REVISION,
                message="the observed live Board IR snapshot is stale",
            ),
            apply_token_withheld_reason=live_withheld,
        )
    if time.monotonic() >= deadline:
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path="live",
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=RouteDiagnostic(
                code=RouteFailureCode.CANCELLED,
                message="the live route proposal deadline expired during board conversion",
            ),
            apply_token_withheld_reason=live_withheld,
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
    )
    if result.connected is not None:
        return RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path="live",
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            connection=result.connected,
            apply_token_withheld_reason=live_withheld,
        )
    if result.candidate is None:
        return RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path="live",
            board_revision=board_revision,
            request=request,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=result.diagnostic,
            apply_token_withheld_reason=live_withheld,
        )
    return RoutePreview(
        status=RoutePreviewStatus.ROUTED,
        board_path="live",
        board_revision=board_revision,
        request=request,
        snapshot_digest=snapshot.snapshot_digest,
        candidate=result.candidate,
        apply_token_withheld_reason=live_withheld,
    )
