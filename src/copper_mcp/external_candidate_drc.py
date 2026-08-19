"""File-backed authoritative DRC continuation for accepted external route candidates.

The external document remains untrusted.  This coordinator derives Board IR, routing identity,
constraints, and work budgets from typed local state, then hands only a reconstructed immutable
candidate to the existing private KiCad DRC adapter.  It has no MCP, CLI, persistence, apply, or
workspace-mutation authority.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from typing import Any, Literal, cast

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.board_ir import BoardIRSnapshot, PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    RouteCandidateDrcEvidence,
    run_disposed_route_candidate_drc,
)
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.route_preview import (
    RoutePreviewError,
    RoutePreviewRequest,
    parse_route_preview_request,
)
from copper_mcp.routing.contracts import RouteCandidate, RoutePath, RouteRequest
from copper_mcp.routing.external_candidate_verifier import (
    EXTERNAL_ROUTE_CANDIDATE_SCHEMA,
    EXTERNAL_ROUTE_PATCH_SCHEMA,
    ExternalCandidateFailure,
    ExternalCandidateVerificationResult,
    _candidate,
    _compress,
    _patch_candidate,
    _point,
    _refused,
)
from copper_mcp.routing.external_candidate_verifier import (
    verify_external_route_candidate as verify_external_route_candidate_core,
)
from copper_mcp.security import read_workspace_file


class ExternalCandidateDrcError(RuntimeError):
    """Raised when the trusted file-backed continuation cannot produce DRC evidence."""


class ExternalCandidatePublicError(RuntimeError):
    """Fixed public failure when no authoritative external-candidate result exists."""


_PUBLIC_SCHEMA_VERSION = "1.0"
_PUBLIC_ERROR = "external candidate verification could not be completed"
_PUBLIC_KEYS = frozenset({"schema_version", "request", "document", "start_pad_id", "end_pad_id"})
_PUBLIC_REQUEST_REQUIRED_KEYS = frozenset(
    {
        "board",
        "layer",
        "constraints",
        "net_ref_id",
        "expect_board_revision",
        "expect_snapshot_digest",
    }
)
_PUBLIC_REQUEST_OPTIONAL_KEYS = frozenset({"seed", "settings"})
_PUBLIC_REQUEST_KEYS = _PUBLIC_REQUEST_REQUIRED_KEYS | _PUBLIC_REQUEST_OPTIONAL_KEYS


@dataclass(frozen=True, slots=True)
class _ExternalCandidateDisposition:
    """Internal continuation carrying geometry only after the redacted boundary accepts it."""

    verification: ExternalCandidateVerificationResult
    candidate: RouteCandidate | None

    def __post_init__(self) -> None:
        if self.verification.accepted != (self.candidate is not None):
            raise ValueError("external candidate disposition is inconsistent")
        if (
            self.candidate is not None
            and self.candidate.candidate_id != self.verification.candidate_id
        ):
            raise ValueError("external candidate disposition is bound to another candidate")


def _dispose_external_route_candidate(
    snapshot: BoardIRSnapshot,
    request: RouteRequest,
    document: object,
    *,
    start_pad_id: object,
    end_pad_id: object,
    max_obstacle_checks: object,
    max_path_edges: object,
    deadline_check: object = None,
) -> _ExternalCandidateDisposition:
    """Verify once, then reconstruct the already-accepted immutable candidate for DRC."""

    verification = verify_external_route_candidate_core(
        snapshot,
        request,
        document,
        start_pad_id=start_pad_id,
        end_pad_id=end_pad_id,
        max_obstacle_checks=max_obstacle_checks,
        max_path_edges=max_path_edges,
        deadline_check=deadline_check,
    )
    if not verification.accepted:
        return _ExternalCandidateDisposition(verification=verification, candidate=None)

    assert isinstance(document, dict)
    assert isinstance(start_pad_id, str)
    assert isinstance(end_pad_id, str)
    schema = document["schema"]
    raw_paths: tuple[list[dict[str, object]], ...]
    if schema == EXTERNAL_ROUTE_CANDIDATE_SCHEMA:
        raw_paths = (cast(list[dict[str, object]], document["segments"]),)
        pad_count = 2
    else:
        assert schema == EXTERNAL_ROUTE_PATCH_SCHEMA
        path_documents = cast(list[dict[str, object]], document["paths"])
        raw_paths = tuple(
            cast(list[dict[str, object]], path["segments"]) for path in path_documents
        )
        pad_count = sum(
            1
            for pad in snapshot.content.pads
            if pad.net_id == request.net_id and request.layer_id in pad.layer_ids
        )

    paths: list[RoutePath] = []
    width_nm: int | None = None
    for raw_segments in raw_paths:
        parsed: list[tuple[PointNM, PointNM]] = []
        for segment in raw_segments:
            start = _point(segment["start"])
            end = _point(segment["end"])
            assert start is not None and end is not None
            parsed.append((start, end))
        paths.append(RoutePath(vertices=_compress([parsed[0][0], *(end for _, end in parsed)])))
        path_width = raw_segments[0]["width_nm"]
        assert isinstance(path_width, int) and not isinstance(path_width, bool)
        width_nm = path_width if width_nm is None else width_nm

    assert width_nm is not None
    if schema == EXTERNAL_ROUTE_CANDIDATE_SCHEMA:
        candidate = _candidate(
            request,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            width_nm=width_nm,
            vertices=paths[0].vertices,
        )
    else:
        candidate = _patch_candidate(
            request,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            width_nm=width_nm,
            paths=tuple(paths),
            pad_count=pad_count,
        )
    if candidate.candidate_id != verification.candidate_id:
        raise ExternalCandidateDrcError(
            "accepted external candidate identity could not be reconstructed"
        )
    return _ExternalCandidateDisposition(verification=verification, candidate=candidate)


@dataclass(frozen=True, slots=True)
class ExternalCandidateDrcResult:
    """Redacted structural result with optional candidate-bound authoritative DRC evidence."""

    verification: ExternalCandidateVerificationResult
    drc_evidence: RouteCandidateDrcEvidence | None

    def __post_init__(self) -> None:
        if not isinstance(self.verification, ExternalCandidateVerificationResult):
            raise ValueError("external candidate DRC verification is malformed")
        if self.verification.accepted != (self.drc_evidence is not None):
            raise ValueError("external candidate DRC evidence is inconsistent")
        if (
            self.drc_evidence is not None
            and self.drc_evidence.candidate_id != self.verification.candidate_id
        ):
            raise ValueError("external candidate DRC evidence is bound to another candidate")

    @property
    def physical_validation(self) -> Literal["not_run", "completed"]:
        return "completed" if self.drc_evidence is not None else "not_run"

    def to_dict(self) -> dict[str, Any]:
        payload = self.verification.to_dict()
        payload["physical_validation"] = self.physical_validation
        payload["drc_evidence"] = None if self.drc_evidence is None else self.drc_evidence.to_dict()
        if self.drc_evidence is not None:
            payload["drc_comparability"] = "single_invocation"
        return payload


def verify_external_route_candidate_drc(
    request: RoutePreviewRequest,
    document: object,
    settings: Settings,
    *,
    start_pad_id: object,
    end_pad_id: object,
    max_obstacle_checks: object,
    max_path_edges: object,
) -> ExternalCandidateDrcResult:
    """Bind an accepted external route document to private authoritative KiCad DRC evidence."""

    if type(request) is not RoutePreviewRequest or type(settings) is not Settings:
        raise ExternalCandidateDrcError("external candidate DRC coordinator input is malformed")
    if not request.include_drc:
        raise ExternalCandidateDrcError(
            "the external candidate request does not authorize authoritative DRC"
        )
    deadline = time.monotonic() + settings.max_route_preview_seconds
    board = read_workspace_file(
        settings.workspace,
        request.board,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    relative_path = board.path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
    source_revision = f"sha256:{hashlib.sha256(board.content).hexdigest()}"
    if (
        request.expect_board_revision is not None
        and request.expect_board_revision != source_revision
    ):
        return ExternalCandidateDrcResult(
            verification=_refused(ExternalCandidateFailure.STALE_REVISION),
            drc_evidence=None,
        )

    profile = request.profile()
    conversion = parse_kicad_bytes(board.content, profile, parse_limits_for(settings))
    if conversion.snapshot is None or conversion.diagnostics:
        raise ExternalCandidateDrcError(
            "the board cannot be represented by the supported external candidate authority"
        )
    snapshot = conversion.snapshot
    if snapshot.content.source.revision != source_revision:
        raise ExternalCandidateDrcError("the converted board revision is inconsistent")
    if (
        request.expect_snapshot_digest is not None
        and request.expect_snapshot_digest != snapshot.snapshot_digest
    ):
        return ExternalCandidateDrcResult(
            verification=_refused(ExternalCandidateFailure.STALE_REVISION),
            drc_evidence=None,
        )

    route_request = RouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=request.net_id,
        layer_id=request.layer_id,
        seed=request.seed,
        settings=request.settings,
    )
    disposition = _dispose_external_route_candidate(
        snapshot,
        route_request,
        document,
        start_pad_id=start_pad_id,
        end_pad_id=end_pad_id,
        max_obstacle_checks=max_obstacle_checks,
        max_path_edges=max_path_edges,
        deadline_check=lambda: time.monotonic() >= deadline,
    )
    if disposition.candidate is None:
        return ExternalCandidateDrcResult(
            verification=disposition.verification,
            drc_evidence=None,
        )

    try:
        evidence = run_disposed_route_candidate_drc(
            relative_path,
            disposition,
            profile,
            settings,
            expected_source_revision=source_revision,
            deadline=deadline,
        )
    except KiCadCliError as error:
        raise ExternalCandidateDrcError(
            "authoritative KiCad DRC could not verify the external candidate"
        ) from error
    if time.monotonic() >= deadline:
        raise ExternalCandidateDrcError(
            "the external candidate deadline expired during authoritative DRC"
        )
    return ExternalCandidateDrcResult(
        verification=disposition.verification,
        drc_evidence=evidence,
    )


def _invalid_public_result() -> dict[str, object]:
    result = ExternalCandidateDrcResult(
        verification=_refused(ExternalCandidateFailure.INVALID_REQUEST),
        drc_evidence=None,
    ).to_dict()
    return {"schema_version": _PUBLIC_SCHEMA_VERSION, **result}


def _parse_public_request(
    payload: object,
) -> tuple[RoutePreviewRequest, object, object, object] | None:
    """Parse the closed public envelope before any file or geometry work."""

    if (
        type(payload) is not dict
        or len(payload) != len(_PUBLIC_KEYS)
        or not all(key in payload for key in _PUBLIC_KEYS)
        or type(payload["schema_version"]) is not str
        or payload["schema_version"] != _PUBLIC_SCHEMA_VERSION
    ):
        return None
    request_payload = payload["request"]
    if (
        type(request_payload) is not dict
        or not all(key in request_payload for key in _PUBLIC_REQUEST_REQUIRED_KEYS)
        or len(request_payload)
        != len(_PUBLIC_REQUEST_REQUIRED_KEYS)
        + sum(key in request_payload for key in _PUBLIC_REQUEST_OPTIONAL_KEYS)
    ):
        return None
    trusted_request = {
        key: request_payload[key] for key in _PUBLIC_REQUEST_KEYS if key in request_payload
    }
    trusted_request.update(
        {
            "include_drc": True,
            "include_fill_authority": False,
            "include_apply_token": False,
        }
    )
    try:
        request = parse_route_preview_request(trusted_request)
    except RoutePreviewError:
        return None
    return request, payload["document"], payload["start_pad_id"], payload["end_pad_id"]


def verify_external_route_candidate_request(
    payload: object,
    settings: Settings,
) -> dict[str, object]:
    """Verify one closed public external-candidate request without mutation or geometry output."""

    try:
        parsed = _parse_public_request(payload)
    except Exception as error:
        raise ExternalCandidatePublicError(_PUBLIC_ERROR) from error
    if parsed is None:
        return _invalid_public_result()
    request, document, start_pad_id, end_pad_id = parsed
    try:
        result = verify_external_route_candidate_drc(
            request,
            document,
            settings,
            start_pad_id=start_pad_id,
            end_pad_id=end_pad_id,
            max_obstacle_checks=request.settings.max_obstacle_checks,
            max_path_edges=min(4_096, request.settings.max_grid_nodes),
        )
    except Exception as error:
        raise ExternalCandidatePublicError(_PUBLIC_ERROR) from error
    return {"schema_version": _PUBLIC_SCHEMA_VERSION, **result.to_dict()}


__all__ = [
    "ExternalCandidateDrcError",
    "ExternalCandidateDrcResult",
    "ExternalCandidatePublicError",
    "verify_external_route_candidate_drc",
    "verify_external_route_candidate_request",
]
