"""Read-only layered route proposals against the active KiCad IPC snapshot.

The file-backed :mod:`copper_mcp.layered_route_preview` surface is deliberately kept
separate from this adapter.  A live proposal captures one revision through KiCad's local IPC
binding, converts those exact bytes through the same Board IR adapter, then invokes the pure
two-layer router.  No board bytes are written, no KiCad mutation/DRC operation is called, and
the candidate remains bound to both the IPC source digest and the converted snapshot digest.

The MCP registration layer exposes this adapter only through the closed, revision-bound request
and response contracts.  Keeping the implementation isolated from the registration layer makes
the fake-IPC safety tests and the candidate-only boundary explicit.
"""

from __future__ import annotations

import hmac
import time
from collections import Counter
from dataclasses import replace
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import _is_session_revision, capture_live_board
from copper_mcp.layered_route_preview import (
    LayeredRoutePreviewError,
    LayeredRoutePreviewRequest,
    _candidate_document,
    _diagnostic_document,
    _empty_result,
    _safe_router_message,
    parse_layered_route_preview_request,
)
from copper_mcp.request_boundary import mapping
from copper_mcp.routing import LayeredBoardRouter, LayeredRouteFailureCode, LayeredRouteRequest


def parse_live_layered_route_preview_request(payload: Any) -> LayeredRoutePreviewRequest:
    """Validate one live layered request without accepting a filesystem path.

    The shared layered parser owns all pad, constraint, layer, digest, and settings validation.
    It expects a file-shaped board value, so this adapter checks the exact live sentinel before
    parsing a private copy with a harmless parser sentinel and then replaces it with ``live``.
    The original object is never mutated or echoed.
    """

    fields = mapping("request", payload)
    if fields.get("board") != "live":
        raise LayeredRoutePreviewError("live layered preview requests must set board to 'live'")
    normalized = dict(fields)
    session_revision = normalized.pop("expect_session_revision", None)
    if normalized.get("include_drc", False) is True:
        raise LayeredRoutePreviewError(
            "live layered route proposals cannot request authoritative DRC"
        )
    normalized["board"] = "live.kicad_pcb"
    request = replace(parse_layered_route_preview_request(normalized), board="live")
    if not isinstance(session_revision, str):
        raise LayeredRoutePreviewError("expect_session_revision is required")
    if not _is_session_revision(session_revision):
        raise LayeredRoutePreviewError(
            "expect_session_revision must be a pbkdf2-hmac-sha256 session revision"
        )
    return replace(request, expect_session_revision=session_revision)


def preview_live_layered_route(
    payload: Any,
    settings: Settings,
    *,
    client_factory: Any = None,
) -> dict[str, object]:
    """Propose one bounded layered route from the exact active KiCad IPC snapshot.

    Both source and Board IR compare-and-swap preconditions are checked.  A stale source is
    rejected before conversion; a stale converted snapshot is rejected before routing.  The
    only successful output is a detached candidate document suitable for inspection or a later
    explicitly-authorized operation by another API surface.
    """

    if not isinstance(settings, Settings):
        raise LayeredRoutePreviewError("live layered preview settings are malformed")
    # Bound the IPC observation as well as conversion/search.  The KiCad binding accepts a
    # millisecond timeout capped at 10 seconds; using the remaining route budget prevents a
    # short request budget from silently spending the default two seconds in the transport.
    deadline = time.monotonic() + settings.max_route_preview_seconds
    request = parse_live_layered_route_preview_request(payload)
    remaining_ms = max(1, min(10_000, int((deadline - time.monotonic()) * 1_000)))
    captured = capture_live_board(
        settings,
        client_factory=client_factory,
        timeout_ms=remaining_ms,
        deadline=deadline,
    )
    board_revision = captured.observation.board_digest
    expected_session_revision = request.expect_session_revision
    assert isinstance(expected_session_revision, str)
    captured_session_revision = captured.session_revision
    if captured_session_revision is None:
        session_matches = False
    else:
        assert isinstance(captured_session_revision, str)
        session_matches = hmac.compare_digest(expected_session_revision, captured_session_revision)
    if not session_matches:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            diagnostic=_diagnostic_document(
                "stale_revision", "live IPC session revision is stale or unavailable"
            ),
        )
    if request.expect_board_revision != board_revision:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            diagnostic=_diagnostic_document("stale_revision", "live board revision is stale"),
        )

    profile = KiCadConstraintProfile(
        net_classes=(request.constraints,),
        default_net_class_id=request.constraints.id,
    )
    default_limits = ParseLimits()
    limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )
    conversion = parse_kicad_bytes(captured.source, profile, limits)
    if conversion.snapshot is None:
        counts = dict(Counter(diagnostic.code for diagnostic in conversion.diagnostics))
        return _empty_result(
            "unsupported_board",
            request,
            "live",
            board_revision,
            diagnostic=_diagnostic_document(
                "invalid_snapshot", "live board is outside the supported Board IR subset"
            ),
            conversion_diagnostic_counts=counts,
        )

    snapshot = conversion.snapshot
    if snapshot.content.source.revision != board_revision:
        raise LayeredRoutePreviewError(
            "converted live board revision is inconsistent with its IPC source bytes"
        )
    if request.expect_snapshot_digest != snapshot.snapshot_digest:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                "stale_revision", "live Board IR snapshot revision is stale"
            ),
        )
    if time.monotonic() >= deadline:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                LayeredRouteFailureCode.CANCELLED.value,
                "live layered route proposal deadline expired during board conversion",
            ),
        )

    pads = {pad.id: pad for pad in snapshot.content.pads}
    start_pad = pads.get(request.start_pad_id)
    end_pad = pads.get(request.end_pad_id)
    if start_pad is None or end_pad is None:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                LayeredRouteFailureCode.INVALID_REQUEST.value,
                "route endpoints are not pads on one common net",
            ),
        )
    net_id = start_pad.net_id
    if net_id is None or net_id != end_pad.net_id:
        return _empty_result(
            "not_routed",
            request,
            "live",
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            diagnostic=_diagnostic_document(
                LayeredRouteFailureCode.INVALID_REQUEST.value,
                "route endpoints are not pads on one common net",
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
        return _empty_result(
            "routed",
            request,
            "live",
            board_revision,
            snapshot_digest=snapshot.snapshot_digest,
            candidate=_candidate_document(result.candidate),
        )

    assert result.diagnostic is not None
    diagnostic = result.diagnostic
    return _empty_result(
        "not_routed",
        request,
        "live",
        board_revision,
        snapshot_digest=snapshot.snapshot_digest,
        diagnostic=_diagnostic_document(
            diagnostic.code.value,
            _safe_router_message(diagnostic.code),
            expanded_states=diagnostic.expanded_states,
            obstacle_checks=diagnostic.obstacle_checks,
        ),
    )


__all__ = [
    "LayeredRoutePreviewError",
    "parse_live_layered_route_preview_request",
    "preview_live_layered_route",
]
