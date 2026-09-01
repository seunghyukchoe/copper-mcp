"""Preconditions for a one-undo-commit apply into a running KiCad editor.

This module owns the *authorization and binding* half of ADR-0074. It performs, in order, every
check that must hold before CopperMCP would be entitled to mutate a document the operator has
open in front of them — three-flag operator consent, a live-scoped single-use capability token,
a session compare-and-swap, a board-serialization compare-and-swap, a Board IR snapshot
compare-and-swap, and a full re-derivation of the candidate's identity and geometry against the
board the editor is holding *right now*.

It then refuses with ``capability_not_implemented``.

That refusal is the point of the slice, not an omission in it. The mutation itself is one
``begin_commit`` / ``push_commit`` pair, and the honest statement of what CopperMCP can prove
about that pair is recorded in [ADR-0074](../../docs/adr/0074-live-ipc-one-undo-commit-apply.md)
and its research note. Shipping the preconditions without the mutation gives operators and
reviewers a surface whose refusals are real — every code below is reachable and tested — while
the destructive step stays behind an adversarial review that has not happened yet. A half-working
mutation would give the opposite: a tool that sometimes changes a board and cannot say whether it
did.

Nothing here writes, and nothing here consumes a token: no mutation occurs, so a legitimate
retry after a transient refusal must still work. The token is spent only by a mutation that
actually happened, exactly as on the file-backed surface.
"""

from __future__ import annotations

import hmac
import time
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply.tokens import ApplyTokenAuthority, ApplyTokenError, LiveApplyBinding
from copper_mcp.board_ir import NetClass, PointNM
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    VERIFIED_API_COMPATIBILITY,
    KicadIpcConfigurationError,
    KicadIpcConnectionError,
    KicadIpcDeadlineError,
    KicadIpcDisabledError,
    KicadIpcError,
    KicadIpcPayloadError,
    KicadIpcPayloadTypeError,
    KicadIpcUnavailableError,
    KicadIpcVersionError,
    _is_session_revision,
    capture_live_board,
)
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.request_boundary import (
    RequestError,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)
from copper_mcp.routing.layered_astar import LayeredAStarSettings
from copper_mcp.routing.layered_candidate_verifier import (
    LayeredCandidateVerificationLimits,
    verify_layered_candidate,
)
from copper_mcp.routing.layered_contracts import (
    LayeredRouteCandidate,
    LayeredRouteCost,
    LayeredRouteMetrics,
    LayeredRoutePatch,
    LayeredRoutePath,
    LayeredRouteVia,
)

LIVE_APPLY_VERSION = "0.1.0"
LIVE_APPLY_SCHEMA_VERSION = "0.1.0"

_MAX_TOKEN_CHARACTERS = 512
_MAX_DIGEST_CHARACTERS = 71
_MAX_ID_CHARACTERS = 256
#: Structural ceilings applied while the untrusted manifest is still a document, so an oversized
#: candidate is refused before it is materialised into dataclasses and before the pairwise
#: geometry verifier is asked to look at it. They deliberately equal the verifier's own defaults:
#: two different ceilings would let a candidate pass one and fail the other for no stated reason.
_LIMITS = LayeredCandidateVerificationLimits()

_REQUIRED_FIELDS = (
    "board",
    "candidate",
    "constraints",
    "apply_token",
    "expect_board_revision",
    "expect_snapshot_digest",
    "expect_session_revision",
)

_SETTINGS_FIELDS = (
    "move_cost",
    "via_cost",
    "max_expansions",
    "max_nodes",
    "max_obstacles",
    "max_obstacle_checks",
)


class LiveApplyError(RuntimeError):
    """Raised for a caller-side programming fault, never for an untrusted request.

    An untrusted request that is malformed becomes an ``invalid_request`` diagnostic in the
    response. This exception is reserved for the embedder handing the service the wrong kind of
    object entirely, which no MCP client can cause.
    """


class LiveApplyFailureCode(StrEnum):
    """Every reason this surface can refuse. There is currently no success code."""

    INVALID_REQUEST = "invalid_request"
    LIVE_APPLY_DISABLED = "live_apply_disabled"
    LIVE_IPC_DISABLED = "live_ipc_disabled"
    # These name failure codes, not secrets; the scanner matches on "TOKEN" alone.
    INVALID_TOKEN = "invalid_token"  # noqa: S105
    TOKEN_EXPIRED = "token_expired"  # noqa: S105
    TOKEN_ALREADY_USED = "token_already_used"  # noqa: S105
    BINDING_UNAVAILABLE = "binding_unavailable"
    INVALID_ENDPOINT = "invalid_endpoint"
    UNSUPPORTED_KICAD_VERSION = "unsupported_kicad_version"
    LIVE_EDITOR_UNAVAILABLE = "live_editor_unavailable"
    DEADLINE_EXPIRED = "deadline_expired"
    LIVE_BOARD_OVER_BUDGET = "live_board_over_budget"
    STALE_SESSION = "stale_session"
    STALE_BOARD_REVISION = "stale_board_revision"
    STALE_SNAPSHOT_DIGEST = "stale_snapshot_digest"
    UNSUPPORTED_BOARD = "unsupported_board"
    CANDIDATE_VERIFICATION_FAILED = "candidate_verification_failed"
    CAPABILITY_NOT_IMPLEMENTED = "capability_not_implemented"


class LiveApplyPrecondition(StrEnum):
    """The named checks, in the fixed order this surface performs them.

    A response lists the preconditions it actually *verified*, so an absent name is never
    readable as a passing check. The list is a prefix of this enum by construction: the service
    appends only as each check completes, and returns immediately on the first refusal.
    """

    OPERATOR_OPT_IN = "operator_opt_in"
    CAPABILITY_TOKEN = "capability_token"  # noqa: S105 - a check name, not a secret
    LIVE_SESSION_BOUND = "live_session_bound"
    LIVE_BOARD_REVISION_BOUND = "live_board_revision_bound"
    BOARD_IR_SNAPSHOT_BOUND = "board_ir_snapshot_bound"
    CANDIDATE_IDENTITY_REPLAYED = "candidate_identity_replayed"


@dataclass(frozen=True, slots=True)
class LiveApplyRequest:
    """One validated live-apply request. The capability token is never echoed back."""

    candidate_id: str
    expect_board_revision: str
    expect_snapshot_digest: str
    expect_session_revision: str
    constraints: NetClass
    candidate: Mapping[str, Any]
    apply_token: str

    def to_dict(self) -> dict[str, object]:
        """Return only the non-sensitive, already-validated request fields."""

        return {
            "board": "live",
            "candidate_id": self.candidate_id,
            "expect_board_revision": self.expect_board_revision,
            "expect_snapshot_digest": self.expect_snapshot_digest,
            "expect_session_revision": self.expect_session_revision,
            "constraints": {
                "clearance_nm": self.constraints.clearance_nm,
                "track_width_nm": self.constraints.track_width_nm,
                "via_diameter_nm": self.constraints.via_diameter_nm,
                "via_drill_nm": self.constraints.via_drill_nm,
            },
        }

    def binding(self) -> LiveApplyBinding:
        """Return the exact capability this request claims to hold.

        ``candidate_id`` is taken from the manifest as an unproven *claim*. Binding the token to
        it is not trust: the claim is re-derived from the candidate's own geometry later, so a
        manifest whose identity was rewritten to match a stolen token still fails verification.
        """

        return LiveApplyBinding(
            candidate_id=self.candidate_id,
            base_revision=self.expect_snapshot_digest,
            board_revision=self.expect_board_revision,
            session_revision=self.expect_session_revision,
        )


def _digest(name: str, value: object) -> str:
    candidate = text(name, value, maximum=_MAX_DIGEST_CHARACTERS)
    if (
        len(candidate) != _MAX_DIGEST_CHARACTERS
        or not candidate.startswith("sha256:")
        or any(character not in "0123456789abcdef" for character in candidate[7:])
    ):
        raise RequestError(f"{name} must be content-addressed with sha256")
    return candidate


def parse_live_apply_request(payload: Any) -> LiveApplyRequest:
    """Validate one untrusted live-apply request without opening anything.

    Every compare-and-swap value is mandatory and none has a default. A live mutation has three
    independent ways to be stale — the editor process, the document bytes, and the converted
    snapshot — and a caller that has not stated all three has not stated what it previewed.
    """

    fields = mapping("request", payload)
    known_fields("request", fields, frozenset(_REQUIRED_FIELDS))
    required_fields("request", fields, _REQUIRED_FIELDS)
    if fields["board"] != "live":
        raise RequestError("live apply requests must set board to 'live'")
    candidate = mapping("candidate", fields["candidate"])
    session_revision = text(
        "expect_session_revision", fields["expect_session_revision"], maximum=_MAX_ID_CHARACTERS
    )
    if not _is_session_revision(session_revision):
        raise RequestError(
            "expect_session_revision must be a pbkdf2-hmac-sha256 live session revision"
        )
    return LiveApplyRequest(
        candidate_id=_digest("candidate.candidate_id", candidate.get("candidate_id")),
        expect_board_revision=_digest("expect_board_revision", fields["expect_board_revision"]),
        expect_snapshot_digest=_digest("expect_snapshot_digest", fields["expect_snapshot_digest"]),
        expect_session_revision=session_revision,
        constraints=net_class_constraints(fields["constraints"]),
        candidate=candidate,
        apply_token=text("apply_token", fields["apply_token"], maximum=_MAX_TOKEN_CHARACTERS),
    )


def _point(value: object) -> PointNM:
    if isinstance(value, Sequence) and not isinstance(value, str | bytes) and len(value) == 2:
        x_value, y_value = value[0], value[1]
    elif isinstance(value, Mapping):
        x_value, y_value = value.get("x_nm"), value.get("y_nm")
    else:
        raise RequestError("candidate geometry point is malformed")
    if (
        isinstance(x_value, bool)
        or isinstance(y_value, bool)
        or not isinstance(x_value, int)
        or not isinstance(y_value, int)
    ):
        raise RequestError("candidate geometry point is malformed")
    return PointNM(x=x_value, y=y_value)


def _bounded_list(name: str, value: object, maximum: int) -> list[Any]:
    if not isinstance(value, list):
        raise RequestError(f"{name} must be a list")
    if len(value) > maximum:
        raise RequestError(f"{name} exceeds the verification budget")
    return value


def layered_candidate_from_document(document: Mapping[str, Any]) -> LayeredRouteCandidate:
    """Rebuild a typed layered candidate from an untrusted manifest.

    This is a *structural* reconstruction only. Every semantic rule — orthogonality, bend
    compression, distinct via layers, drill-inside-diameter, integer ranges — is enforced by the
    dataclasses' own validators, and the candidate's content-addressed identity is recomputed
    separately by :func:`verify_layered_candidate`. Nothing in the manifest is believed; the
    manifest only says which object to rebuild and check.

    Unrecognised keys are ignored rather than refused, and that is safe here for a reason worth
    stating: the reconstructed object is re-hashed and compared, so a key this function does not
    read cannot influence the identity, the geometry, or the outcome. It would have to change
    the rebuilt candidate to matter, and it cannot.

    ``fill_binding`` is where that argument stops being merely true and starts being
    load-bearing (ADR-0106). Live apply holds no fill evidence and can replay nothing under it,
    so a candidate the exact pour shaped must never verify here — and it cannot, precisely
    because the binding is part of the canonical address: a manifest claiming one rebuilds
    without it and then fails its own identity recomputation. Reading the key would be the
    unsafe change, not ignoring it.
    """

    patch_fields = mapping("candidate.patch", document.get("patch"))
    raw_paths = _bounded_list("candidate.patch.paths", patch_fields.get("paths"), _LIMITS.max_paths)
    # The vertex ceiling is a *total* across paths, not a per-path one, so it is spent down as
    # each path is read. Bounding each path against the whole budget independently would admit
    # `max_paths * max_vertices` points before the verifier ever saw them.
    vertex_budget = _LIMITS.max_vertices
    paths: list[LayeredRoutePath] = []
    for raw_path in raw_paths:
        path_fields = mapping("candidate.patch.paths[]", raw_path)
        raw_vertices = _bounded_list(
            "candidate.patch.paths[].vertices_nm", path_fields.get("vertices_nm"), vertex_budget
        )
        vertex_budget -= len(raw_vertices)
        paths.append(
            LayeredRoutePath(
                layer_id=text(
                    "candidate.patch.paths[].layer_id",
                    path_fields.get("layer_id"),
                    maximum=_MAX_ID_CHARACTERS,
                ),
                vertices=tuple(_point(vertex) for vertex in raw_vertices),
            )
        )
    raw_vias = _bounded_list("candidate.patch.vias", patch_fields.get("vias", []), _LIMITS.max_vias)
    vias: list[LayeredRouteVia] = []
    for raw_via in raw_vias:
        via_fields = mapping("candidate.patch.vias[]", raw_via)
        vias.append(
            LayeredRouteVia(
                id=text(
                    "candidate.patch.vias[].id", via_fields.get("id"), maximum=_MAX_ID_CHARACTERS
                ),
                center=_point(via_fields.get("center_nm")),
                diameter_nm=_integer(
                    "candidate.patch.vias[].diameter_nm", via_fields.get("diameter_nm")
                ),
                drill_nm=_integer("candidate.patch.vias[].drill_nm", via_fields.get("drill_nm")),
                start_layer_id=text(
                    "candidate.patch.vias[].start_layer_id",
                    via_fields.get("start_layer_id"),
                    maximum=_MAX_ID_CHARACTERS,
                ),
                end_layer_id=text(
                    "candidate.patch.vias[].end_layer_id",
                    via_fields.get("end_layer_id"),
                    maximum=_MAX_ID_CHARACTERS,
                ),
            )
        )
    patch = LayeredRoutePatch(
        net_id=text(
            "candidate.patch.net_id", patch_fields.get("net_id"), maximum=_MAX_ID_CHARACTERS
        ),
        width_nm=_integer("candidate.patch.width_nm", patch_fields.get("width_nm")),
        via_diameter_nm=_integer(
            "candidate.patch.via_diameter_nm", patch_fields.get("via_diameter_nm")
        ),
        via_drill_nm=_integer("candidate.patch.via_drill_nm", patch_fields.get("via_drill_nm")),
        paths=tuple(paths),
        vias=tuple(vias),
    )
    cost_fields = mapping("candidate.cost", document.get("cost"))
    metrics_fields = mapping("candidate.metrics", document.get("metrics"))
    settings_fields = mapping("candidate.settings", document.get("settings"))
    known_fields("candidate.settings", settings_fields, frozenset(_SETTINGS_FIELDS))
    return LayeredRouteCandidate(
        candidate_id=_digest("candidate.candidate_id", document.get("candidate_id")),
        base_revision=_digest("candidate.base_revision", document.get("base_revision")),
        start_pad_id=text(
            "candidate.start_pad_id", document.get("start_pad_id"), maximum=_MAX_ID_CHARACTERS
        ),
        end_pad_id=text(
            "candidate.end_pad_id", document.get("end_pad_id"), maximum=_MAX_ID_CHARACTERS
        ),
        patch=patch,
        cost=LayeredRouteCost(
            wire_length_nm=_integer(
                "candidate.cost.wire_length_nm", cost_fields.get("wire_length_nm"), minimum=0
            ),
            via_count=_integer("candidate.cost.via_count", cost_fields.get("via_count"), minimum=0),
            via_cost_units=_integer(
                "candidate.cost.via_cost_units", cost_fields.get("via_cost_units"), minimum=0
            ),
            total_search_cost_units=_integer(
                "candidate.cost.total_search_cost_units",
                cost_fields.get("total_search_cost_units"),
                minimum=0,
            ),
        ),
        metrics=LayeredRouteMetrics(
            **{
                name: _integer(f"candidate.metrics.{name}", metrics_fields.get(name), minimum=0)
                for name in (
                    "expanded_states",
                    "discovered_states",
                    "peak_frontier_states",
                    "obstacle_checks",
                    "move_steps",
                    "vias",
                    "wire_length_nm",
                    "bend_count",
                )
            }
        ),
        settings=LayeredAStarSettings(
            **{
                name: _integer(f"candidate.settings.{name}", settings_fields.get(name))
                for name in _SETTINGS_FIELDS
            }
        ),
        router_version=text(
            "candidate.router_version", document.get("router_version"), maximum=_MAX_ID_CHARACTERS
        ),
        policy=text("candidate.policy", document.get("policy"), maximum=_MAX_ID_CHARACTERS),
        seed=_integer("candidate.seed", document.get("seed"), minimum=0),
    )


def _integer(name: str, value: object, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise RequestError(f"{name} must be an integer")
    if not minimum <= value <= 2**53 - 1:
        raise RequestError(f"{name} is outside the supported integer range")
    return value


def _refuse(
    code: LiveApplyFailureCode,
    message: str,
    *,
    request: LiveApplyRequest | None = None,
    verified: Sequence[LiveApplyPrecondition] = (),
    board_revision_before: str | None = None,
    snapshot_digest_before: str | None = None,
    conversion_diagnostic_counts: Mapping[str, int] | None = None,
) -> dict[str, object]:
    """Build one refusal that states exactly what was checked and what was not.

    ``mutation_attempted`` and ``undo_steps_pushed`` are constants in this slice rather than
    computed values, because there is no code path here that could make them anything else. That
    is the same device the file-backed surface uses for ``kicad_opened_board``: a field with one
    possible value cannot be misread as a check that happened to pass.
    """

    return {
        "schema_version": LIVE_APPLY_SCHEMA_VERSION,
        "status": "refused",
        "live_apply_version": LIVE_APPLY_VERSION,
        "board": "live",
        "board_revision_before": board_revision_before,
        "board_revision_after": None,
        "snapshot_digest_before": snapshot_digest_before,
        "candidate_id": None if request is None else request.candidate_id,
        "request": None if request is None else request.to_dict(),
        "preconditions_verified": [precondition.value for precondition in verified],
        "mutation_attempted": False,
        "undo_steps_pushed": 0,
        "post_apply_observation": "not_run",
        "diagnostic": {"code": code.value, "message": message},
        "conversion_diagnostic_counts": dict(conversion_diagnostic_counts or {}),
    }


def apply_live_candidate(
    payload: Any,
    settings: Settings,
    token_authority: ApplyTokenAuthority,
    *,
    client_factory: Any = None,
) -> dict[str, object]:
    """Verify every precondition for a live one-undo-commit apply, then refuse to mutate.

    The order below is the design. Consent is checked before anything is parsed, the capability
    is checked before a socket is opened, and each compare-and-swap is checked against the board
    the editor is holding at that moment rather than against a value the caller supplied. The
    final refusal is issued from the exact point at which the mutation would otherwise begin.
    """

    if not isinstance(settings, Settings):
        raise LiveApplyError("live apply settings are malformed")
    if not isinstance(token_authority, ApplyTokenAuthority):
        raise LiveApplyError("live apply token authority is malformed")

    # Consent first, and before the request is even parsed. A deployment that has not opted in
    # must not be distinguishable by how it fails on a well-formed versus a malformed request,
    # and must never reach the endpoint discovery in `capture_live_board`.
    if not settings.allow_live_apply:
        return _refuse(
            LiveApplyFailureCode.LIVE_APPLY_DISABLED,
            "live KiCad apply is disabled; set COPPER_MCP_ALLOW_LIVE_APPLY=1 to enable it",
        )
    if not settings.allow_live_ipc:
        # Live apply is strictly a superset of live observation: it opens the same socket and
        # reads the same document. The observation consent is therefore required as well, and
        # is named separately so the operator learns which of the two is missing.
        return _refuse(
            LiveApplyFailureCode.LIVE_IPC_DISABLED,
            "live KiCad IPC is disabled; set COPPER_MCP_ALLOW_LIVE_IPC=1 to enable it",
        )

    # Consent has passed by this point, so it is reported as verified even on a refusal that
    # follows: `preconditions_verified` names what actually ran, and understating it would be
    # the same kind of untruth as overstating it.
    verified: list[LiveApplyPrecondition] = [LiveApplyPrecondition.OPERATOR_OPT_IN]

    try:
        request = parse_live_apply_request(payload)
    except RequestError as error:
        return _refuse(LiveApplyFailureCode.INVALID_REQUEST, str(error), verified=verified)

    # The capability is checked before a socket is opened, so an unauthorized caller cannot make
    # this tool touch the operator's editor at all -- the same ordering the file-backed surface
    # uses to keep an unauthorized caller from making it read and parse a board.
    try:
        token_authority.verify(request.apply_token, request.binding())
    except ApplyTokenError as error:
        code = {
            "invalid_token": LiveApplyFailureCode.INVALID_TOKEN,
            "token_expired": LiveApplyFailureCode.TOKEN_EXPIRED,
            "token_already_used": LiveApplyFailureCode.TOKEN_ALREADY_USED,
        }.get(error.code, LiveApplyFailureCode.INVALID_TOKEN)
        return _refuse(code, str(error), request=request, verified=verified)
    verified.append(LiveApplyPrecondition.CAPABILITY_TOKEN)

    deadline = time.monotonic() + settings.max_route_preview_seconds
    timeout_ms = max(1, min(10_000, int(settings.max_route_preview_seconds * 1_000)))
    try:
        captured = capture_live_board(
            settings,
            client_factory=client_factory,
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
    except KicadIpcDisabledError as error:
        return _refuse(
            LiveApplyFailureCode.LIVE_IPC_DISABLED, str(error), request=request, verified=verified
        )
    except KicadIpcUnavailableError as error:
        return _refuse(
            LiveApplyFailureCode.BINDING_UNAVAILABLE,
            str(error),
            request=request,
            verified=verified,
        )
    except KicadIpcConfigurationError as error:
        return _refuse(
            LiveApplyFailureCode.INVALID_ENDPOINT, str(error), request=request, verified=verified
        )
    except KicadIpcVersionError as error:
        return _refuse(
            LiveApplyFailureCode.UNSUPPORTED_KICAD_VERSION,
            str(error),
            request=request,
            verified=verified,
        )
    except KicadIpcPayloadTypeError as error:
        # Checked before its `KicadIpcPayloadError` base. A payload of the wrong *kind* -- a
        # non-text snapshot, undecodable board text, a broken snapshot invariant -- is an editor
        # or binding fault, and reporting it as a budget overrun would send the operator to raise
        # a limit that was never the problem.
        return _refuse(
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
            str(error),
            request=request,
            verified=verified,
        )
    except KicadIpcPayloadError as error:
        return _refuse(
            LiveApplyFailureCode.LIVE_BOARD_OVER_BUDGET,
            str(error),
            request=request,
            verified=verified,
        )
    except KicadIpcDeadlineError as error:
        # Checked before its `KicadIpcConnectionError` base: a budget that ran out is a different
        # operator-facing fact from an editor that would not answer.
        return _refuse(
            LiveApplyFailureCode.DEADLINE_EXPIRED, str(error), request=request, verified=verified
        )
    except KicadIpcConnectionError as error:
        return _refuse(
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
            str(error),
            request=request,
            verified=verified,
        )
    except KicadIpcError as error:
        # Catch-all for the base class, which `LiveBoardObservation.__post_init__` raises from
        # outside `_capture_live_board_from_client`'s own try block. None of those is reachable
        # through the current fake, so this is latent -- but without it an untyped observer fault
        # would escape as an unhandled `RuntimeError` from an MCP tool, which is neither the
        # embedder fault `LiveApplyError` is reserved for nor a typed refusal.
        return _refuse(
            LiveApplyFailureCode.LIVE_EDITOR_UNAVAILABLE,
            str(error),
            request=request,
            verified=verified,
        )

    board_revision = captured.observation.board_digest
    # ADR-0128 tiers the window by what a surface can do, not by what it reads. A *read* across
    # an unverified binding is safe to publish because it carries the verdict that says so, and
    # its worst case is an incomplete answer. A mutation has no such disclosure to hide behind:
    # the board changes either way, and "the write was issued against an API this build never
    # verified" is not a caveat a caller can act on afterwards. So apply takes the strict half of
    # the window -- an exact version match -- and refuses every acceptance the read paths allow.
    if captured.observation.compatibility not in VERIFIED_API_COMPATIBILITY:
        return _refuse(
            LiveApplyFailureCode.UNSUPPORTED_KICAD_VERSION,
            "live apply requires a verified KiCad API binding; this session is "
            f"{captured.observation.compatibility} "
            f"(KiCad {captured.observation.kicad_version} against API "
            f"{captured.observation.api_version})",
            request=request,
            verified=verified,
            board_revision_before=board_revision,
        )
    captured_session = captured.session_revision
    if captured_session is None or not hmac.compare_digest(
        captured_session, request.expect_session_revision
    ):
        # A different editor process, or none at all. The session revision is unreproducible
        # across processes by construction, so this also catches "KiCad was restarted since the
        # preview" -- which must never be treated as the same document.
        return _refuse(
            LiveApplyFailureCode.STALE_SESSION,
            "live KiCad session is stale or unavailable",
            request=request,
            verified=verified,
            board_revision_before=board_revision,
        )
    verified.append(LiveApplyPrecondition.LIVE_SESSION_BOUND)

    if board_revision != request.expect_board_revision:
        # Never auto-refreshed. The caller previewed one document; a different one is a new
        # decision for them to make, not a value for this surface to substitute.
        return _refuse(
            LiveApplyFailureCode.STALE_BOARD_REVISION,
            "live board revision is stale",
            request=request,
            verified=verified,
            board_revision_before=board_revision,
        )
    verified.append(LiveApplyPrecondition.LIVE_BOARD_REVISION_BOUND)

    # The same net class the preview applied. Board IR carries net classes, so converting with
    # a different one would move `snapshot_digest` and make the snapshot compare-and-swap below
    # unsatisfiable rather than meaningful.
    profile = KiCadConstraintProfile(
        net_classes=(request.constraints,),
        default_net_class_id=request.constraints.id,
    )
    limits = parse_limits_for(settings)
    conversion = parse_kicad_bytes(captured.source, profile, limits)
    if conversion.snapshot is None:
        return _refuse(
            LiveApplyFailureCode.UNSUPPORTED_BOARD,
            "live board is outside the supported Board IR subset",
            request=request,
            verified=verified,
            board_revision_before=board_revision,
            conversion_diagnostic_counts=dict(
                Counter(diagnostic.code for diagnostic in conversion.diagnostics)
            ),
        )
    snapshot = conversion.snapshot
    if snapshot.snapshot_digest != request.expect_snapshot_digest:
        return _refuse(
            LiveApplyFailureCode.STALE_SNAPSHOT_DIGEST,
            "live Board IR snapshot revision is stale",
            request=request,
            verified=verified,
            board_revision_before=board_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
        )
    verified.append(LiveApplyPrecondition.BOARD_IR_SNAPSHOT_BOUND)

    try:
        candidate = layered_candidate_from_document(request.candidate)
    except (RequestError, TypeError, ValueError) as error:
        return _refuse(
            LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED,
            f"candidate manifest is malformed: {error}"[:1024],
            request=request,
            verified=verified,
            board_revision_before=board_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
        )
    verification = verify_layered_candidate(
        candidate,
        snapshot,
        expected_board_revision=snapshot.snapshot_digest,
        limits=_LIMITS,
    )
    if not verification.ok:
        # `verify_layered_candidate` recomputes the identity from the geometry
        # (`routing/layered_candidate_verifier.py`, `verify_layered_candidate_id`), so a manifest
        # whose `candidate_id` was rewritten to match a token fails inside `verification.ok`.
        # That recomputation is what makes binding the token to a claimed identity safe.
        #
        # There is deliberately no `candidate.candidate_id != request.candidate_id` clause here.
        # Both sides are `_digest(..., <the manifest>.get("candidate_id"))` over the same mapping
        # -- `request.candidate` *is* the document `candidate` was built from -- so the
        # comparison was two reads of one key and no input could fail it. It read as a second,
        # independent check while contributing nothing, which is worse than its absence.
        return _refuse(
            LiveApplyFailureCode.CANDIDATE_VERIFICATION_FAILED,
            verification.diagnostic.message,
            request=request,
            verified=verified,
            board_revision_before=board_revision,
            snapshot_digest_before=snapshot.snapshot_digest,
        )
    verified.append(LiveApplyPrecondition.CANDIDATE_IDENTITY_REPLAYED)

    # Everything the token authorizes is true of the running editor at this instant. This is
    # exactly where `begin_commit` would be called. It is not called: the mutation is deferred
    # to a slice that has been through adversarial review, and the token is deliberately not
    # consumed, because nothing was spent.
    return _refuse(
        LiveApplyFailureCode.CAPABILITY_NOT_IMPLEMENTED,
        "all live apply preconditions hold; the one-undo-commit mutation is not implemented",
        request=request,
        verified=verified,
        board_revision_before=board_revision,
        snapshot_digest_before=snapshot.snapshot_digest,
    )


__all__ = [
    "LIVE_APPLY_SCHEMA_VERSION",
    "LIVE_APPLY_VERSION",
    "LiveApplyError",
    "LiveApplyFailureCode",
    "LiveApplyPrecondition",
    "LiveApplyRequest",
    "apply_live_candidate",
    "layered_candidate_from_document",
    "parse_live_apply_request",
]
