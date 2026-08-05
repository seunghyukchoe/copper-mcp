"""Read-only, file-backed preview for the bounded layered Board IR router.

The layered router itself is intentionally an internal, typed seam.  This module is the small
workspace boundary that makes that seam useful to an MCP caller without exposing KiCad source,
raw net names, or mutation authority.  A request names the two endpoint pads; the net identity is
resolved only after the board has been converted and both pads have been checked to agree.

Every successful result is bound to both the source-byte digest and the converted Board IR
snapshot digest.  A candidate is returned as canonical JSON-shaped data so callers can inspect
geometry without being handed a mutable model object or source-board contents.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections import Counter
from dataclasses import dataclass, replace
from typing import Any, cast

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, ParseLimits
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    LayeredRouteCandidateDrcEvidence,
    run_layered_route_candidate_drc,
)
from copper_mcp.request_boundary import (
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
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteFailureCode,
    LayeredRouteRequest,
    canonical_layered_candidate_bytes,
    verify_layered_candidate_id,
)
from copper_mcp.routing.layered_board_adapter import has_exactly_two_signal_layers
from copper_mcp.security import read_workspace_file

_MAX_GRID_STEP_NM = 1_000_000_000
_MAX_PAD_ID_CHARACTERS = 164
_SETTINGS_FIELDS = tuple(
    field for field in sorted(LayeredAStarSettings.__dataclass_fields__) if field != "max_vias"
)
_MAX_SETTINGS_FIELDS = frozenset(_SETTINGS_FIELDS)
_REQUIRED_FIELDS = (
    "board",
    "start_pad_id",
    "end_pad_id",
    "constraints",
    "expect_board_revision",
    "expect_snapshot_digest",
)
_OPTIONAL_FIELDS = (
    "grid_step_nm",
    "seed",
    "settings",
    "start_layer_id",
    "end_layer_id",
    "include_drc",
)
_SETTING_LIMITS: dict[str, int] = {
    "move_cost": 1_000_000_000,
    "via_cost": 1_000_000_000,
    "max_expansions": 1_000_000,
    "max_nodes": 500_000,
    "max_obstacles": 4_096,
    "max_obstacle_checks": 10_000_000,
}


class LayeredRoutePreviewError(RequestError):
    """Raised when a layered preview request is malformed at its trust boundary."""


@dataclass(frozen=True, slots=True)
class LayeredRoutePreviewRequest:
    """Validated input for one file-backed layered preview."""

    board: str
    start_pad_id: str
    end_pad_id: str
    expect_board_revision: str
    expect_snapshot_digest: str
    constraints: NetClass
    grid_step_nm: int
    seed: int
    settings: LayeredAStarSettings
    include_drc: bool = False
    start_layer_id: str | None = None
    end_layer_id: str | None = None
    expect_session_revision: str | None = None

    def to_dict(self) -> dict[str, object]:
        """Return only validated, non-sensitive request fields for MCP clients."""

        document = {
            "board": self.board,
            "start_pad_id": self.start_pad_id,
            "end_pad_id": self.end_pad_id,
            "expect_board_revision": self.expect_board_revision,
            "expect_snapshot_digest": self.expect_snapshot_digest,
            "constraints": {
                "clearance_nm": self.constraints.clearance_nm,
                "track_width_nm": self.constraints.track_width_nm,
                "via_diameter_nm": self.constraints.via_diameter_nm,
                "via_drill_nm": self.constraints.via_drill_nm,
            },
            "grid_step_nm": self.grid_step_nm,
            "seed": self.seed,
            "settings": {field: getattr(self.settings, field) for field in _SETTINGS_FIELDS},
            "include_drc": self.include_drc,
            "start_layer_id": self.start_layer_id,
            "end_layer_id": self.end_layer_id,
        }
        if self.expect_session_revision is not None:
            document["expect_session_revision"] = self.expect_session_revision
        return document


def _digest(name: str, value: object) -> str:
    """Validate a content-addressed precondition without exposing malformed input."""

    candidate = text(name, value, maximum=71)
    if (
        len(candidate) != 71
        or not candidate.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in candidate[7:])
    ):
        raise LayeredRoutePreviewError(f"{name} must be content-addressed with sha256")
    return candidate


def _pad_id(name: str, value: object) -> str:
    candidate = text(name, value, maximum=_MAX_PAD_ID_CHARACTERS)
    if not candidate.startswith("pad:") or not candidate[4:]:
        raise LayeredRoutePreviewError(f"{name} must be a stable Board IR pad ID")
    if not all(
        character.isascii() and (character.isalnum() or character in "_.:-")
        for character in candidate
    ):
        raise LayeredRoutePreviewError(f"{name} must be a stable Board IR pad ID")
    return candidate


def _layer_selector(name: str, value: object) -> str:
    candidate = text(name, value, maximum=64)
    if candidate.startswith("layer:"):
        candidate = candidate[6:]
    try:
        return f"layer:{copper_layer(name, candidate)}"
    except RequestError as error:
        raise LayeredRoutePreviewError(f"{name} is malformed") from error


def _settings(payload: object) -> LayeredAStarSettings:
    fields = mapping("settings", payload)
    known_fields("settings", fields, _MAX_SETTINGS_FIELDS)
    values: dict[str, int] = {}
    for field, value in fields.items():
        values[field] = integer(
            f"settings.{field}", value, minimum=1, maximum=_SETTING_LIMITS[field]
        )
    try:
        return LayeredAStarSettings(**values)
    except (TypeError, ValueError) as error:
        raise LayeredRoutePreviewError("settings are invalid") from error


def parse_layered_route_preview_request(payload: Any) -> LayeredRoutePreviewRequest:
    """Validate one untrusted layered preview request.

    Both revision preconditions are mandatory.  Unlike the legacy single-layer preview, no raw
    ``net`` field is accepted: endpoint pads provide the net identity after conversion.
    """

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        constraints = net_class_constraints(fields["constraints"])
        return LayeredRoutePreviewRequest(
            board=board_path(fields["board"]),
            start_pad_id=_pad_id("start_pad_id", fields["start_pad_id"]),
            end_pad_id=_pad_id("end_pad_id", fields["end_pad_id"]),
            expect_board_revision=_digest("expect_board_revision", fields["expect_board_revision"]),
            expect_snapshot_digest=_digest(
                "expect_snapshot_digest", fields["expect_snapshot_digest"]
            ),
            constraints=constraints,
            grid_step_nm=integer(
                "grid_step_nm",
                fields.get("grid_step_nm", 250_000),
                minimum=1,
                maximum=_MAX_GRID_STEP_NM,
            ),
            seed=integer("seed", fields.get("seed", 0), minimum=0, maximum=MAX_JSON_SAFE_INTEGER),
            settings=_settings(fields.get("settings", {})),
            include_drc=boolean("include_drc", fields.get("include_drc", False)),
            start_layer_id=(
                _layer_selector("start_layer_id", fields["start_layer_id"])
                if "start_layer_id" in fields
                else None
            ),
            end_layer_id=(
                _layer_selector("end_layer_id", fields["end_layer_id"])
                if "end_layer_id" in fields
                else None
            ),
        )
    except LayeredRoutePreviewError:
        raise
    except RequestError as error:
        raise LayeredRoutePreviewError(str(error)) from error


def _safe_router_message(code: LayeredRouteFailureCode) -> str:
    if code is LayeredRouteFailureCode.NO_PATH:
        return "no bounded layered route was found"
    if code in {
        LayeredRouteFailureCode.GRID_BUDGET_EXCEEDED,
        LayeredRouteFailureCode.OBSTACLE_BUDGET_EXCEEDED,
        LayeredRouteFailureCode.SEARCH_BUDGET_EXCEEDED,
    }:
        return "layered route search reached its configured budget"
    if code is LayeredRouteFailureCode.OFF_GRID:
        return "route endpoints are incompatible with the selected grid"
    if code is LayeredRouteFailureCode.CANCELLED:
        return "layered route search was cancelled"
    if code is LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY:
        return "board geometry is outside the layered preview subset"
    if code is LayeredRouteFailureCode.UNSUPPORTED_CONSTRAINT:
        return "routing constraints are outside the layered preview subset"
    if code is LayeredRouteFailureCode.INVALID_SNAPSHOT:
        return "converted Board IR snapshot failed validation"
    if code is LayeredRouteFailureCode.INVALID_REQUEST:
        return "layered route request could not be honoured"
    return "layered route preview failed"


def _diagnostic_document(
    code: str,
    message: str,
    *,
    expanded_states: int = 0,
    obstacle_checks: int = 0,
) -> dict[str, object]:
    return {
        "code": code,
        "message": message,
        "expanded_states": expanded_states,
        "obstacle_checks": obstacle_checks,
    }


def _candidate_document(candidate: object) -> dict[str, object]:
    """Serialize a typed candidate through its canonical identity representation."""

    verify_layered_candidate_id(cast(Any, candidate))
    canonical = canonical_layered_candidate_bytes(cast(Any, candidate))
    document = json.loads(canonical.decode("utf-8"))
    if not isinstance(document, dict):  # pragma: no cover - canonical contract guarantees this
        raise LayeredRoutePreviewError("layered candidate serialization is malformed")
    typed_document = cast(dict[str, object], document)
    patch = cast(dict[str, object], typed_document["patch"])
    canonical_paths = cast(list[dict[str, object]], patch["paths"])
    patch["paths"] = [
        {
            "layer_id": path["layer_id"],
            "vertices_nm": [
                [
                    vertex["x_nm"],
                    vertex["y_nm"],
                ]
                for vertex in cast(list[dict[str, int]], path["vertices"])
            ],
        }
        for path in canonical_paths
    ]
    canonical_vias = cast(list[dict[str, object]], patch["vias"])
    patch["vias"] = [
        {
            "id": via["id"],
            "center_nm": cast(dict[str, int], via["center"]),
            "diameter_nm": via["diameter_nm"],
            "drill_nm": via["drill_nm"],
            "start_layer_id": via["start_layer_id"],
            "end_layer_id": via["end_layer_id"],
        }
        for via in canonical_vias
    ]
    typed_document["candidate_id"] = cast(Any, candidate).candidate_id
    return typed_document


def _empty_result(
    status: str,
    request: LayeredRoutePreviewRequest,
    board_path_value: str,
    board_revision: str,
    *,
    snapshot_digest: str | None = None,
    diagnostic: dict[str, object] | None = None,
    candidate: dict[str, object] | None = None,
    conversion_diagnostic_counts: dict[str, int] | None = None,
    drc_evidence: LayeredRouteCandidateDrcEvidence | None = None,
) -> dict[str, object]:
    return {
        "schema_version": "1.0",
        "status": status,
        "board_path": board_path_value,
        "board_revision": board_revision,
        "snapshot_digest": snapshot_digest,
        "request": request.to_dict(),
        "candidate": candidate,
        "drc_evidence": None if drc_evidence is None else drc_evidence.to_dict(),
        "diagnostic": diagnostic,
        "conversion_diagnostic_counts": conversion_diagnostic_counts or {},
    }


def preview_layered_route(payload: Any, settings: Settings) -> dict[str, object]:
    """Preview one bounded layered route from a workspace board without writing or applying."""

    if not isinstance(settings, Settings):
        raise LayeredRoutePreviewError("layered preview settings are malformed")
    deadline = time.monotonic() + settings.max_route_preview_seconds
    request = parse_layered_route_preview_request(payload)
    workspace_root = settings.workspace.resolve(strict=True)
    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(workspace_root).as_posix()
    source = board.content
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    if request.expect_board_revision != board_revision:
        return _empty_result(
            "not_routed",
            request,
            relative_path,
            board_revision,
            diagnostic=_diagnostic_document("stale_revision", "board revision is stale"),
        )

    profile = KiCadConstraintProfile(
        net_classes=(request.constraints,),
        default_net_class_id=request.constraints.id,
    )
    limits = replace(
        ParseLimits(), max_input_bytes=min(ParseLimits().max_input_bytes, settings.max_board_bytes)
    )
    conversion = parse_kicad_bytes(source, profile, limits)
    if conversion.snapshot is None:
        counts = dict(Counter(diagnostic.code for diagnostic in conversion.diagnostics))
        return _empty_result(
            "unsupported_board",
            request,
            relative_path,
            board_revision,
            diagnostic=_diagnostic_document(
                "invalid_snapshot", "board is outside the supported Board IR subset"
            ),
            conversion_diagnostic_counts=counts,
        )
    snapshot = conversion.snapshot
    if request.expect_snapshot_digest != snapshot.snapshot_digest:
        return _empty_result(
            "not_routed",
            request,
            relative_path,
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                "stale_revision", "Board IR snapshot revision is stale"
            ),
        )
    if not has_exactly_two_signal_layers(snapshot):
        return _empty_result(
            "unsupported_board",
            request,
            relative_path,
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                LayeredRouteFailureCode.UNSUPPORTED_GEOMETRY.value,
                "board copper stack is outside the public two-layer preview subset",
            ),
        )

    pads = {pad.id: pad for pad in snapshot.content.pads}
    start_pad = pads.get(request.start_pad_id)
    end_pad = pads.get(request.end_pad_id)
    if start_pad is None or end_pad is None:
        return _empty_result(
            "not_routed",
            request,
            relative_path,
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                "invalid_request", "route endpoints are not pads on one common net"
            ),
        )
    net_id = start_pad.net_id
    if net_id is None or net_id != end_pad.net_id:
        return _empty_result(
            "not_routed",
            request,
            relative_path,
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                "invalid_request", "route endpoints are not pads on one common net"
            ),
        )

    layered_request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        expected_revision=snapshot.snapshot_digest,
        net_id=net_id,
        start_pad_id=start_pad.id,
        end_pad_id=end_pad.id,
        seed=request.seed,
        start_layer_id=request.start_layer_id,
        end_layer_id=request.end_layer_id,
        grid_step_nm=request.grid_step_nm,
        settings=request.settings,
    )
    result = LayeredBoardRouter().propose(
        snapshot,
        layered_request,
        cancelled=lambda: time.monotonic() >= deadline,
    )
    if result.candidate is not None:
        evidence = None
        if request.include_drc:
            remaining = int(deadline - time.monotonic())
            if remaining < 1:
                raise LayeredRoutePreviewError(
                    "the layered preview deadline expired before authoritative DRC could run"
                )
            drc_settings = replace(
                settings,
                kicad_timeout_seconds=min(settings.kicad_timeout_seconds, remaining),
            )
            try:
                evidence = run_layered_route_candidate_drc(
                    relative_path,
                    result.candidate,
                    profile,
                    drc_settings,
                    request=layered_request,
                )
            except KiCadCliError as error:
                raise LayeredRoutePreviewError(
                    "authoritative layered DRC evidence is unavailable"
                ) from error
            if not isinstance(evidence, LayeredRouteCandidateDrcEvidence):
                raise LayeredRoutePreviewError("authoritative layered DRC evidence is malformed")
            if (
                evidence.candidate_id != result.candidate.candidate_id
                or evidence.candidate_base_revision != result.candidate.base_revision
                or evidence.source_revision != board_revision
            ):
                raise LayeredRoutePreviewError(
                    "authoritative layered DRC evidence is not bound to this candidate"
                )
        return _empty_result(
            "routed",
            request,
            relative_path,
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            candidate=_candidate_document(result.candidate),
            drc_evidence=evidence,
        )
    assert result.diagnostic is not None
    diagnostic = result.diagnostic
    code = diagnostic.code
    status = "not_routed"
    result_snapshot_digest = snapshot.snapshot_digest
    return _empty_result(
        status,
        request,
        relative_path,
        board_revision,
        snapshot_digest=result_snapshot_digest,
        diagnostic=_diagnostic_document(
            code.value,
            _safe_router_message(code),
            expanded_states=diagnostic.expanded_states,
            obstacle_checks=diagnostic.obstacle_checks,
        ),
    )


__all__ = [
    "LayeredRoutePreviewError",
    "LayeredRoutePreviewRequest",
    "parse_layered_route_preview_request",
    "preview_layered_route",
]
