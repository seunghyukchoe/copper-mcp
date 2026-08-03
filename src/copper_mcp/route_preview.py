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
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import RouteCandidateDrcEvidence, run_route_candidate_drc
from copper_mcp.models import SCHEMA_VERSION
from copper_mcp.routing import (
    AStarRouter,
    AStarSettings,
    RouteCandidate,
    RouteDiagnostic,
    RouteFailureCode,
    RouteRequest,
)
from copper_mcp.security import read_bounded_file, resolve_workspace_file

PREVIEW_NET_CLASS_ID = "class:preview"
PREVIEW_NET_CLASS_NAME = "Preview"

_COPPER_LAYER = re.compile(r"^(?:F\.Cu|B\.Cu|In(?:[1-9]|[12][0-9]|3[0-2])\.Cu)$")
_CONTROL_CHARACTERS = re.compile(r"[\x00-\x1f\x7f]")
_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")
_MAX_SEED = (1 << 53) - 1
_MAX_DIMENSION_NM = 1_000_000_000
_MAX_NET_NAME_CHARACTERS = 255
_REQUIRED_FIELDS = ("board", "net", "layer", "constraints")
_OPTIONAL_FIELDS = ("seed", "settings", "include_drc")
_CONSTRAINT_FIELDS = (
    "clearance_nm",
    "track_width_nm",
    "via_diameter_nm",
    "via_drill_nm",
)
_SETTINGS_FIELDS = tuple(AStarSettings.__dataclass_fields__)


class RoutePreviewError(ValueError):
    """Raised when a route-preview request is malformed or cannot be honoured."""


class RoutePreviewStatus(StrEnum):
    """Stable outcome taxonomy for one preview attempt."""

    ROUTED = "routed"
    NOT_ROUTED = "not_routed"
    UNSUPPORTED_BOARD = "unsupported_board"


def _mapping(name: str, value: Any) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise RoutePreviewError(f"{name} must be an object")
    if len(value) > 64:
        raise RoutePreviewError(f"{name} has too many fields")
    for key in value:
        if not isinstance(key, str):
            raise RoutePreviewError(f"{name} field names must be strings")
    return dict(value)


def _known_fields(name: str, payload: Mapping[str, Any], allowed: frozenset[str]) -> None:
    """Reject unsupported fields by count, never by echoing caller-controlled names."""

    unknown = len(set(payload) - allowed)
    if unknown:
        raise RoutePreviewError(
            f"{name} has {unknown} unsupported field(s); supported fields are: "
            f"{', '.join(sorted(allowed))}"
        )


def _integer(name: str, value: Any, *, minimum: int, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RoutePreviewError(f"{name} must be an integer")
    if not minimum <= value <= maximum:
        raise RoutePreviewError(f"{name} must be between {minimum} and {maximum}")
    return int(value)


def _text(name: str, value: Any, *, maximum: int) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= maximum:
        raise RoutePreviewError(
            f"{name} must be a non-empty string of at most {maximum} characters"
        )
    if _CONTROL_CHARACTERS.search(value):
        raise RoutePreviewError(f"{name} must not contain control characters")
    return value


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

    def __post_init__(self) -> None:
        if not isinstance(self.constraints, NetClass):
            raise RoutePreviewError("constraints must be a typed net class")
        if not isinstance(self.settings, AStarSettings):
            raise RoutePreviewError("settings must be typed router settings")
        if not isinstance(self.include_drc, bool):
            raise RoutePreviewError("include_drc must be a boolean")
        _integer("seed", self.seed, minimum=0, maximum=_MAX_SEED)
        _text("board", self.board, maximum=4096)
        _text("net", self.net, maximum=_MAX_NET_NAME_CHARACTERS)
        if not _COPPER_LAYER.fullmatch(self.layer):
            raise RoutePreviewError("layer must be a documented KiCad copper layer name")

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
            "constraints": {
                field: getattr(self.constraints, field) for field in _CONSTRAINT_FIELDS
            },
            "settings": {field: getattr(self.settings, field) for field in _SETTINGS_FIELDS},
        }


def _constraints(payload: Any) -> NetClass:
    fields = _mapping("constraints", payload)
    _known_fields("constraints", fields, frozenset(_CONSTRAINT_FIELDS))
    missing = sorted(set(_CONSTRAINT_FIELDS) - set(fields))
    if missing:
        raise RoutePreviewError(f"constraints are missing required fields: {', '.join(missing)}")
    values = {
        field: _integer(
            f"constraints.{field}",
            fields[field],
            minimum=0 if field == "clearance_nm" else 1,
            maximum=_MAX_DIMENSION_NM,
        )
        for field in _CONSTRAINT_FIELDS
    }
    try:
        return NetClass(
            id=PREVIEW_NET_CLASS_ID,
            name=PREVIEW_NET_CLASS_NAME,
            **values,
        )
    except ValueError as error:
        raise RoutePreviewError(f"constraints are invalid: {error}") from error


def _settings(payload: Any) -> AStarSettings:
    fields = _mapping("settings", payload)
    _known_fields("settings", fields, frozenset(_SETTINGS_FIELDS))
    overrides = {
        field: _integer(f"settings.{field}", value, minimum=0, maximum=_MAX_SEED)
        for field, value in fields.items()
    }
    try:
        return AStarSettings(**overrides)
    except ValueError as error:
        raise RoutePreviewError(f"settings are invalid: {error}") from error


def parse_route_preview_request(payload: Any) -> RoutePreviewRequest:
    """Validate one untrusted preview request without echoing unvalidated input."""

    fields = _mapping("request", payload)
    _known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
    missing = sorted(set(_REQUIRED_FIELDS) - set(fields))
    if missing:
        raise RoutePreviewError(f"request is missing required fields: {', '.join(missing)}")
    include_drc = fields.get("include_drc", False)
    if not isinstance(include_drc, bool):
        raise RoutePreviewError("include_drc must be a boolean")
    return RoutePreviewRequest(
        board=_text("board", fields["board"], maximum=4096),
        net=_text("net", fields["net"], maximum=_MAX_NET_NAME_CHARACTERS),
        layer=_text("layer", fields["layer"], maximum=64),
        constraints=_constraints(fields["constraints"]),
        settings=_settings(fields.get("settings", {})),
        seed=_integer("seed", fields.get("seed", 0), minimum=0, maximum=_MAX_SEED),
        include_drc=include_drc,
    )


def _candidate_to_dict(candidate: RouteCandidate) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "base_revision": candidate.base_revision,
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "router_version": candidate.router_version,
        "policy": candidate.policy,
        "seed": candidate.seed,
        "patch": {
            "net_id": candidate.patch.net_id,
            "layer_id": candidate.patch.layer_id,
            "width_nm": candidate.patch.width_nm,
            "vertices_nm": [[point.x, point.y] for point in candidate.patch.vertices],
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
    diagnostic: RouteDiagnostic | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)
    drc_evidence: RouteCandidateDrcEvidence | None = None
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
            str(code): _integer(f"diagnostic count for {code}", count, minimum=0, maximum=_MAX_SEED)
            for code, count in self.conversion_diagnostic_counts.items()
        }
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(sorted(counts.items()))),
        )

        if self.status is RoutePreviewStatus.UNSUPPORTED_BOARD:
            if self.candidate is not None or self.diagnostic is not None:
                raise RoutePreviewError("an unsupported board cannot carry a routing outcome")
            if self.snapshot_digest is not None or not counts:
                raise RoutePreviewError("an unsupported board must report conversion diagnostics")
        else:
            if counts:
                raise RoutePreviewError("a converted board must not report conversion errors")
            if self.snapshot_digest is None or not _SHA256_ID.fullmatch(self.snapshot_digest):
                raise RoutePreviewError("a converted board must record its Board IR digest")

        if self.status is RoutePreviewStatus.ROUTED:
            if not isinstance(self.candidate, RouteCandidate) or self.diagnostic is not None:
                raise RoutePreviewError("a routed preview must carry exactly one candidate")
            if self.candidate.base_revision != self.snapshot_digest:
                raise RoutePreviewError("candidate is not bound to the previewed Board IR snapshot")
        elif self.status is RoutePreviewStatus.NOT_ROUTED and (
            not isinstance(self.diagnostic, RouteDiagnostic) or self.candidate is not None
        ):
            raise RoutePreviewError("an unrouted preview must carry exactly one diagnostic")

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


def preview_route(payload: Any, settings: Settings) -> RoutePreview:
    """Propose one deterministic candidate for a workspace board without mutating it."""

    if not isinstance(settings, Settings):
        raise RoutePreviewError("preview settings are malformed")
    deadline = time.monotonic() + settings.max_route_preview_seconds
    request = parse_route_preview_request(payload)

    board_path = resolve_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source = read_bounded_file(board_path, max_bytes=settings.max_board_bytes)
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
    return RoutePreview(
        status=RoutePreviewStatus.ROUTED,
        board_path=relative_path,
        board_revision=board_revision,
        request=request,
        snapshot_digest=snapshot.snapshot_digest,
        candidate=result.candidate,
        drc_evidence=evidence,
    )
