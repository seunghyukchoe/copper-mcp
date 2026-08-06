"""Verify foreign SimpleRouteJson route geometry without adopting it.

An ML autorouter — or any other external proposer — emits a SimpleRouteJson solution: the
problem document plus ``traces`` of wire points and vias in floating-point millimetres.  It has
no CopperMCP candidate identity, no ``base_revision``, and no reason to be trusted.  This module
is the seam that takes exactly that pair of documents and either *verifies* the geometry against
the deterministically imported board model or *refuses* with a typed, non-echoing reason.  It is
the disposer half of "AI proposes, deterministic code disposes" for proposers this project did
not write.

Trust boundary, stated once
---------------------------

* **Nothing is adopted.**  A verified foreign route does not become a CopperMCP candidate.  No
  ``candidate_id`` is minted, no candidate store is touched, no apply token can ever be issued
  from this result, and the response deliberately has no field through which any of those could
  travel.  The only identities in the result are content addresses this module computed itself
  over the submitted bytes, plus the imported snapshot digest.
* **Self-declared identity is refused, not ignored.**  A solution document carrying
  ``candidate_id``, ``base_revision``, ``apply_token``, or any other reserved CopperMCP identity
  or authority key is refused outright with ``forged_identity``.  Accepting and discarding such a
  key would let a caller construct a document that *looks* laundered even though it is not.
* **Revision binding is computed, never believed.**  The caller states which problem document the
  solution claims to solve as a SHA-256; this module hashes the supplied problem bytes itself and
  refuses on any mismatch before importing anything.

Direction of error
------------------

Acceptance is under-approximated everywhere; obstacles are over-approximated everywhere.

* The imported obstacle model already rounds copper outward and the outline inward
  (:mod:`copper_mcp.benchmarks.simple_route_json`).
* The foreign route's own copper is over-approximated for clearance and containment (widths round
  *up*, vias block on *every* layer) and under-approximated for connectivity (widths round
  *down*, oval pads attach on their inscribed core shrunk by the import's recorded outward
  rounding).
* A solution coordinate that is not exact at nanometre resolution is rounded to the nearest
  nanometre, and every subsequent comparison is then slackened in the refusing direction by a
  bound on the worst-case displacement (``_ROUNDING_SLACK2`` in doubled nanometres).  A document
  whose tokens are exact pays no slack, so an exactly-legal separation still verifies.
* Anything outside the documented subset — an unknown key, an unknown ``route_type``, an
  unattributed trace, a layer outside the declared stack — refuses the whole submission.  No
  element is silently dropped and nothing is ever repaired.

What a pass means
-----------------

The verdict literal is ``clearance_and_connectivity_verified``, and it means exactly this: within
the modelled subset, the over-approximated submitted geometry showed no exact-clearance violation
against the imported obstacle model, stayed inside the imported outline, and the under-approximated
geometry joins every pad of every multi-pad net — all assuming traces and vias are fabricated at
exactly the stated widths and the declared import policy dimensions.  It is **not** a KiCad DRC
result, **not** a manufacturability, signal-integrity, or netlist claim, and **not** an adoption
of the route.  The result carries those non-claims as data.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal, localcontext
from enum import StrEnum
from typing import Any, Final

from copper_mcp.benchmarks.simple_route_json import (
    DEFAULT_IMPORT_POLICY,
    ImportedProblem,
    ImportPolicy,
    SimpleRouteJsonImportError,
    SimpleRouteJsonImportLimits,
    import_simple_route_json,
    mm_token_to_nm,
)
from copper_mcp.board_ir import BoardIRSnapshot, PadShape

#: Recorded in every result so a replay can tell which contract produced a verdict.
FOREIGN_ROUTE_VERIFIER_VERSION: Final = "foreign-route-verification-v1"

#: The exact claim a passing verdict makes, and nothing more.
ACCEPTANCE_CLAIM: Final = (
    "Within the modelled subset, the over-approximated submitted geometry showed no "
    "exact-clearance violation against the imported obstacle model, stayed inside the imported "
    "board outline, and joins every pad of every multi-pad net, assuming traces and vias are "
    "fabricated at exactly the stated widths and the declared import policy dimensions."
)

#: What a passing verdict deliberately does not say.  Copied into every result as data.
NON_CLAIMS: Final = (
    "not a KiCad DRC result; kicad_drc is not_run",
    "no claim about manufacturability, signal integrity, thermal behaviour, or netlist correctness",
    "the route was not produced, repaired, or adopted by CopperMCP; no candidate identity exists",
    "no apply authority; this result can never authorize a board mutation",
    "acceptance is conditional on the declared policy dimensions; no physical artifact was "
    "measured",
)

#: Solution keys that only CopperMCP may ever assert.  Presence anywhere is a refusal.
_RESERVED_IDENTITY_KEYS: Final = frozenset(
    {
        "candidate_id",
        "base_revision",
        "board_revision",
        "snapshot_digest",
        "apply_token",
        "authorization_digest",
        "router_version",
        "origin",
        "copper_mcp",
    }
)

#: Root keys a solution document may carry.  ``traces`` is the payload; the rest are the
#: conventional non-authoritative echo of the problem document and are ignored as data.
_ALLOWED_ROOT_KEYS: Final = frozenset(
    {"traces", "bounds", "obstacles", "connections", "layerCount", "minTraceWidth"}
)
_ALLOWED_TRACE_KEYS: Final = frozenset({"type", "pcb_trace_id", "connection_name", "route"})
_WIRE_KEYS: Final = frozenset({"route_type", "x", "y", "width", "layer"})
_VIA_KEYS: Final = frozenset({"route_type", "x", "y", "from_layer", "to_layer"})

#: Worst-case doubled-nanometre displacement bound once any coordinate has been rounded:
#: two independently rounded points move relative to each other by at most 2·(√2/2) nm,
#: which is under 1.5 nm, i.e. under 3 doubled nanometres.
_ROUNDING_SLACK2: Final = 3
_DECIMAL_PRECISION: Final = 80
_MAX_SAFE_INT: Final = (1 << 53) - 1
_SHA256_HEX_LENGTH: Final = 64


class ForeignRouteRefusalCode(StrEnum):
    """Stable taxonomy for a submission this seam declines to verify."""

    INVALID_REQUEST = "invalid_request"
    MALFORMED_DOCUMENT = "malformed_document"
    BUDGET_EXCEEDED = "budget_exceeded"
    UNSUPPORTED_UNIT = "unsupported_unit"
    WRONG_REVISION = "wrong_revision"
    FORGED_IDENTITY = "forged_identity"
    PROBLEM_REFUSED = "problem_refused"
    UNATTRIBUTED_TRACE = "unattributed_trace"
    UNSUPPORTED_TRACE_ELEMENT = "unsupported_trace_element"
    TRACE_DISCONTINUITY = "trace_discontinuity"
    WIDTH_BELOW_MINIMUM = "width_below_minimum"
    OUTSIDE_BOARD_BOUNDS = "outside_board_bounds"
    CLEARANCE_VIOLATION = "clearance_violation"
    NOT_CONNECTED = "not_connected"


class ForeignRouteCheck(StrEnum):
    """The fixed, ordered checks a submission passes through."""

    DOCUMENT_CONTRACT = "document_contract"
    REVISION_BINDING = "revision_binding"
    IDENTITY_HYGIENE = "identity_hygiene"
    PROBLEM_IMPORT = "problem_import"
    STRUCTURAL_CONTINUITY = "structural_continuity"
    TRACE_WIDTH = "trace_width"
    BOARD_CONTAINMENT = "board_containment"
    CLEARANCE = "clearance"
    CONNECTIVITY = "connectivity"


class ForeignRouteCheckStatus(StrEnum):
    """Whether one check ran, and what it concluded."""

    PASSED = "passed"
    FAILED = "failed"
    NOT_RUN = "not_run"


class ForeignRouteOrigin(StrEnum):
    """One-value literal: this seam only ever examines foreign, untrusted geometry."""

    FOREIGN_UNTRUSTED = "foreign_untrusted"


class ForeignRouteApplyAuthority(StrEnum):
    """One-value literal: this result can never authorize a mutation."""

    NONE = "none"


class ForeignRouteKicadDrc(StrEnum):
    """One-value literal: the authoritative KiCad DRC is not run by this seam."""

    NOT_RUN = "not_run"


class ForeignRouteRepair(StrEnum):
    """One-value literal: this seam accepts or refuses; it never repairs."""

    NOT_ATTEMPTED = "not_attempted"


@dataclass(frozen=True, slots=True)
class ForeignRouteRefusal:
    """One bounded, non-echoing refusal.  Locators are indices and fixed names only."""

    code: ForeignRouteRefusalCode
    message: str
    locator: str = "document"

    def __post_init__(self) -> None:
        if not isinstance(self.code, ForeignRouteRefusalCode):
            raise ValueError("foreign route refusal code is unsupported")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 256:
            raise ValueError("foreign route refusal message is malformed")
        if not isinstance(self.locator, str) or not 1 <= len(self.locator) <= 128:
            raise ValueError("foreign route refusal locator is malformed")

    def payload(self) -> dict[str, str]:
        return {"code": self.code.value, "message": self.message, "locator": self.locator}


@dataclass(frozen=True, slots=True)
class ForeignRouteCheckEvidence:
    """The recorded outcome of one named check."""

    check: ForeignRouteCheck
    status: ForeignRouteCheckStatus

    def __post_init__(self) -> None:
        if not isinstance(self.check, ForeignRouteCheck) or not isinstance(
            self.status, ForeignRouteCheckStatus
        ):
            raise ValueError("foreign route check evidence is malformed")

    def payload(self) -> dict[str, str]:
        return {"check": self.check.value, "status": self.status.value}


@dataclass(frozen=True, slots=True)
class ForeignRouteVerificationLimits:
    """Closed budgets applied to an untrusted submission before and during verification."""

    max_document_bytes: int = 4_000_000
    max_traces: int = 512
    max_route_points_per_trace: int = 4_096
    max_total_route_points: int = 65_536
    max_vias: int = 1_024
    max_pair_checks: int = 5_000_000
    max_extent_nm: int = 1_000_000_000
    max_number_token_length: int = 40

    def __post_init__(self) -> None:
        for name in (
            "max_document_bytes",
            "max_traces",
            "max_route_points_per_trace",
            "max_total_route_points",
            "max_vias",
            "max_pair_checks",
            "max_extent_nm",
            "max_number_token_length",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 1 <= value <= _MAX_SAFE_INT
            ):
                raise ValueError(f"{name} must be a positive safe integer")


@dataclass(frozen=True, slots=True)
class ForeignRouteVerificationResult:
    """A typed verdict with per-check evidence and explicit non-claims.

    There is deliberately no ``candidate_id``, no ``base_revision``, and no ``apply_token``
    field — absent structurally, not conditionally — so a verified foreign route cannot be
    mistaken for, or laundered into, a CopperMCP-produced candidate.
    """

    checks: tuple[ForeignRouteCheckEvidence, ...]
    policy: ImportPolicy
    refusal: ForeignRouteRefusal | None = None
    problem_sha256: str | None = None
    solution_sha256: str | None = None
    snapshot_digest: str | None = None
    trace_count: int = 0
    wire_point_count: int = 0
    via_count: int = 0
    segment_count: int = 0
    pair_checks: int = 0
    rounding_slack_doubled_nm: int = 0
    origin: ForeignRouteOrigin = ForeignRouteOrigin.FOREIGN_UNTRUSTED
    apply_authority: ForeignRouteApplyAuthority = ForeignRouteApplyAuthority.NONE
    kicad_drc: ForeignRouteKicadDrc = ForeignRouteKicadDrc.NOT_RUN
    repair: ForeignRouteRepair = ForeignRouteRepair.NOT_ATTEMPTED

    def __post_init__(self) -> None:
        if not isinstance(self.checks, tuple) or tuple(
            evidence.check for evidence in self.checks
        ) != tuple(ForeignRouteCheck):
            raise ValueError("foreign route evidence must record every check exactly once")
        if not isinstance(self.policy, ImportPolicy):
            raise ValueError("foreign route policy is malformed")
        if self.refusal is not None and not isinstance(self.refusal, ForeignRouteRefusal):
            raise ValueError("foreign route refusal is malformed")
        for name in ("problem_sha256", "solution_sha256", "snapshot_digest"):
            value = getattr(self, name)
            if value is not None and not _is_digest(value):
                raise ValueError(f"{name} must be a sha256 content address")
        for name in (
            "trace_count",
            "wire_point_count",
            "via_count",
            "segment_count",
            "pair_checks",
            "rounding_slack_doubled_nm",
        ):
            value = getattr(self, name)
            if (
                isinstance(value, bool)
                or not isinstance(value, int)
                or not 0 <= value <= _MAX_SAFE_INT
            ):
                raise ValueError(f"{name} is outside the supported integer range")
        if self.origin is not ForeignRouteOrigin.FOREIGN_UNTRUSTED:
            raise ValueError("foreign route origin literal is unsupported")
        if self.apply_authority is not ForeignRouteApplyAuthority.NONE:
            raise ValueError("foreign route apply authority literal is unsupported")
        if self.kicad_drc is not ForeignRouteKicadDrc.NOT_RUN:
            raise ValueError("foreign route KiCad DRC literal is unsupported")
        if self.repair is not ForeignRouteRepair.NOT_ATTEMPTED:
            raise ValueError("foreign route repair literal is unsupported")
        if self.refusal is None and any(
            evidence.status is not ForeignRouteCheckStatus.PASSED for evidence in self.checks
        ):
            raise ValueError("a verified result requires every check to have passed")

    @property
    def verified(self) -> bool:
        """Return true only when every check ran and passed."""

        return self.refusal is None

    @property
    def verdict(self) -> str:
        """Return the bounded verdict literal; its name states what was and was not checked."""

        return "clearance_and_connectivity_verified" if self.verified else "refused"

    def to_dict(self) -> dict[str, Any]:
        """Return the detached response document."""

        return {
            "verifier_version": FOREIGN_ROUTE_VERIFIER_VERSION,
            "verdict": self.verdict,
            "claim": ACCEPTANCE_CLAIM if self.verified else None,
            "non_claims": list(NON_CLAIMS),
            "origin": self.origin.value,
            "apply_authority": self.apply_authority.value,
            "kicad_drc": self.kicad_drc.value,
            "repair": self.repair.value,
            "problem_sha256": self.problem_sha256,
            "solution_sha256": self.solution_sha256,
            "snapshot_digest": self.snapshot_digest,
            "policy": self.policy.payload(),
            "checks": [evidence.payload() for evidence in self.checks],
            "evidence": {
                "trace_count": self.trace_count,
                "wire_point_count": self.wire_point_count,
                "via_count": self.via_count,
                "segment_count": self.segment_count,
                "pair_checks": self.pair_checks,
                "rounding_slack_doubled_nm": self.rounding_slack_doubled_nm,
            },
            "refusal": None if self.refusal is None else self.refusal.payload(),
        }


def _is_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == _SHA256_HEX_LENGTH + 7
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


class _RefusalError(Exception):
    """Internal control flow: one check failed with one typed refusal."""

    def __init__(self, check: ForeignRouteCheck, refusal: ForeignRouteRefusal) -> None:
        super().__init__(refusal.message)
        self.check = check
        self.refusal = refusal


def _refuse(
    check: ForeignRouteCheck,
    code: ForeignRouteRefusalCode,
    message: str,
    locator: str = "document",
) -> _RefusalError:
    return _RefusalError(check, ForeignRouteRefusal(code=code, message=message, locator=locator))


def _reject_constant(token: str) -> Any:
    raise ValueError("solution coordinates must be finite decimal numbers")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("solution objects must not repeat a key")
        result[key] = value
    return result


@dataclass(slots=True)
class _Rect:
    """One axis-aligned rectangle in doubled nanometres."""

    min_x2: int
    min_y2: int
    max_x2: int
    max_y2: int


@dataclass(slots=True)
class _Segment:
    """One foreign centreline segment in doubled nanometres, with both width roundings."""

    net_id: str
    layer: str
    ax2: int
    ay2: int
    bx2: int
    by2: int
    envelope_radius2: int
    coverage_radius2: int
    trace_index: int


@dataclass(slots=True)
class _Via:
    """One foreign through-via in doubled nanometres.  It blocks and joins on every layer."""

    net_id: str
    x2: int
    y2: int
    trace_index: int


@dataclass(slots=True)
class _PadElement:
    """One imported own-net pad: an over-approximated block and an under-approximated core."""

    net_id: str
    layers: frozenset[str]
    block: _Rect
    core: _Rect


@dataclass(slots=True)
class _Geometry:
    """Everything the geometric checks need, in one deterministic bundle."""

    segments: list[_Segment]
    vias: list[_Via]
    wire_point_count: int
    trace_count: int
    slack2: int
    exact_widths: list[tuple[Decimal, str]]


class _PairBudget:
    """A single counted budget over every geometric pair comparison."""

    def __init__(self, maximum: int, check: ForeignRouteCheck) -> None:
        self.maximum = maximum
        self.count = 0
        self.check = check

    def spend(self) -> None:
        self.count += 1
        if self.count > self.maximum:
            raise _refuse(
                self.check,
                ForeignRouteRefusalCode.BUDGET_EXCEEDED,
                "geometric pair checks exceed the verification budget",
                "pair_checks",
            )


def _sign(value: int) -> int:
    return (value > 0) - (value < 0)


def _compare_point_segment(px: int, py: int, ax: int, ay: int, bx: int, by: int, limit: int) -> int:
    """Return the exact sign of ``distance(point, segment)**2 - limit**2``."""

    abx, aby = bx - ax, by - ay
    apx, apy = px - ax, py - ay
    length_squared = abx * abx + aby * aby
    limit_squared = limit * limit
    if length_squared == 0:
        return _sign(apx * apx + apy * apy - limit_squared)
    dot = apx * abx + apy * aby
    if dot <= 0:
        return _sign(apx * apx + apy * apy - limit_squared)
    if dot >= length_squared:
        bpx, bpy = px - bx, py - by
        return _sign(bpx * bpx + bpy * bpy - limit_squared)
    cross = apx * aby - apy * abx
    return _sign(cross * cross - limit_squared * length_squared)


def _orientation(ax: int, ay: int, bx: int, by: int, cx: int, cy: int) -> int:
    return _sign((bx - ax) * (cy - ay) - (by - ay) * (cx - ax))


def _collinear_point_on_segment(px: int, py: int, ax: int, ay: int, bx: int, by: int) -> bool:
    return min(ax, bx) <= px <= max(ax, bx) and min(ay, by) <= py <= max(ay, by)


def _segments_touch(ax: int, ay: int, bx: int, by: int, cx: int, cy: int, dx: int, dy: int) -> bool:
    """Return whether two closed segments share at least one point, at any angle."""

    o1 = _orientation(ax, ay, bx, by, cx, cy)
    o2 = _orientation(ax, ay, bx, by, dx, dy)
    o3 = _orientation(cx, cy, dx, dy, ax, ay)
    o4 = _orientation(cx, cy, dx, dy, bx, by)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _collinear_point_on_segment(cx, cy, ax, ay, bx, by):
        return True
    if o2 == 0 and _collinear_point_on_segment(dx, dy, ax, ay, bx, by):
        return True
    if o3 == 0 and _collinear_point_on_segment(ax, ay, cx, cy, dx, dy):
        return True
    return o4 == 0 and _collinear_point_on_segment(bx, by, cx, cy, dx, dy)


def _compare_segment_segment(
    ax: int,
    ay: int,
    bx: int,
    by: int,
    cx: int,
    cy: int,
    dx: int,
    dy: int,
    limit: int,
) -> int:
    """Return the exact sign of ``distance(segment, segment)**2 - limit**2``."""

    if _segments_touch(ax, ay, bx, by, cx, cy, dx, dy):
        return _sign(-limit * limit)
    return min(
        _compare_point_segment(ax, ay, cx, cy, dx, dy, limit),
        _compare_point_segment(bx, by, cx, cy, dx, dy, limit),
        _compare_point_segment(cx, cy, ax, ay, bx, by, limit),
        _compare_point_segment(dx, dy, ax, ay, bx, by, limit),
    )


def _compare_point_rect(px: int, py: int, rect: _Rect, limit: int) -> int:
    """Return the exact sign of ``distance(point, rectangle)**2 - limit**2``."""

    gap_x = max(0, rect.min_x2 - px, px - rect.max_x2)
    gap_y = max(0, rect.min_y2 - py, py - rect.max_y2)
    return _sign(gap_x * gap_x + gap_y * gap_y - limit * limit)


def _compare_segment_rect(ax: int, ay: int, bx: int, by: int, rect: _Rect, limit: int) -> int:
    """Return the exact sign of ``distance(segment, rectangle)**2 - limit**2``."""

    if (rect.min_x2 <= ax <= rect.max_x2 and rect.min_y2 <= ay <= rect.max_y2) or (
        rect.min_x2 <= bx <= rect.max_x2 and rect.min_y2 <= by <= rect.max_y2
    ):
        return _sign(-limit * limit)
    corners = (
        (rect.min_x2, rect.min_y2, rect.max_x2, rect.min_y2),
        (rect.max_x2, rect.min_y2, rect.max_x2, rect.max_y2),
        (rect.max_x2, rect.max_y2, rect.min_x2, rect.max_y2),
        (rect.min_x2, rect.max_y2, rect.min_x2, rect.min_y2),
    )
    return min(
        _compare_segment_segment(ax, ay, bx, by, ex, ey, fx, fy, limit)
        for ex, ey, fx, fy in corners
    )


def _compare_rect_rect(left: _Rect, right: _Rect, limit: int) -> int:
    """Return the exact sign of ``distance(rectangle, rectangle)**2 - limit**2``."""

    gap_x = max(0, left.min_x2 - right.max_x2, right.min_x2 - left.max_x2)
    gap_y = max(0, left.min_y2 - right.max_y2, right.min_y2 - left.max_y2)
    return _sign(gap_x * gap_x + gap_y * gap_y - limit * limit)


def _hash_bytes(document: bytes) -> str:
    return f"sha256:{hashlib.sha256(document).hexdigest()}"


def _normalized_expected_digest(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.lower()
    if candidate.startswith("sha256:"):
        candidate = candidate[7:]
    if len(candidate) != _SHA256_HEX_LENGTH or not all(
        character in "0123456789abcdef" for character in candidate
    ):
        return None
    return f"sha256:{candidate}"


def _parse_solution_root(document: bytes, limits: ForeignRouteVerificationLimits) -> dict[str, Any]:
    if len(document) > limits.max_document_bytes:
        raise _refuse(
            ForeignRouteCheck.DOCUMENT_CONTRACT,
            ForeignRouteRefusalCode.BUDGET_EXCEEDED,
            "solution document exceeds the byte budget",
        )
    try:
        text = document.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise _refuse(
            ForeignRouteCheck.DOCUMENT_CONTRACT,
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            "solution document is not strict UTF-8",
        ) from error
    try:
        value = json.loads(
            text,
            parse_float=str,
            parse_int=str,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except (json.JSONDecodeError, ValueError) as error:
        raise _refuse(
            ForeignRouteCheck.DOCUMENT_CONTRACT,
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            "solution document is not valid strict JSON",
        ) from error
    if not isinstance(value, dict):
        raise _refuse(
            ForeignRouteCheck.DOCUMENT_CONTRACT,
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            "solution root must be a JSON object",
            "root",
        )
    for key in value:
        if key in _RESERVED_IDENTITY_KEYS:
            continue  # reported by the identity-hygiene check, in its own evidence slot
        if key not in _ALLOWED_ROOT_KEYS:
            raise _refuse(
                ForeignRouteCheck.DOCUMENT_CONTRACT,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "solution root carries a key outside the accepted contract",
                "root",
            )
    return value


def _check_identity_hygiene(root: dict[str, Any]) -> None:
    for key in root:
        if key in _RESERVED_IDENTITY_KEYS:
            raise _refuse(
                ForeignRouteCheck.IDENTITY_HYGIENE,
                ForeignRouteRefusalCode.FORGED_IDENTITY,
                "solution root asserts a reserved CopperMCP identity or authority key",
                "root",
            )
    traces = root.get("traces")
    if isinstance(traces, list):
        for index, entry in enumerate(traces):
            if not isinstance(entry, dict):
                continue  # shape refusals belong to the structural check
            for key in entry:
                if key in _RESERVED_IDENTITY_KEYS:
                    raise _refuse(
                        ForeignRouteCheck.IDENTITY_HYGIENE,
                        ForeignRouteRefusalCode.FORGED_IDENTITY,
                        "a trace asserts a reserved CopperMCP identity or authority key",
                        f"traces[{index}]",
                    )


def _import_problem(
    problem_document: bytes,
    policy: ImportPolicy,
    limits: ForeignRouteVerificationLimits,
) -> ImportedProblem:
    try:
        return import_simple_route_json(
            "foreign-route-verification",
            problem_document,
            policy=policy,
            limits=SimpleRouteJsonImportLimits(
                max_document_bytes=limits.max_document_bytes,
                max_extent_nm=limits.max_extent_nm,
                max_number_token_length=limits.max_number_token_length,
            ),
        )
    except SimpleRouteJsonImportError as error:
        raise _refuse(
            ForeignRouteCheck.PROBLEM_IMPORT,
            ForeignRouteRefusalCode.PROBLEM_REFUSED,
            f"the problem document was refused by the import seam ({error.code.value})",
            error.locator[:128],
        ) from error


def _connection_net_map(problem: ImportedProblem) -> dict[str, str]:
    """Map each source connection name to the one imported net it belongs to.

    A name claimed by two different nets is dropped from the map, so a trace citing it is
    refused as unattributable rather than guessed at.
    """

    mapping: dict[str, str] = {}
    ambiguous: set[str] = set()
    for net in problem.nets:
        for name in net.source_connection_names:
            if name in mapping and mapping[name] != net.net_id:
                ambiguous.add(name)
            mapping[name] = net.net_id
    for name in ambiguous:
        del mapping[name]
    return mapping


def _round_token(
    value: Decimal,
) -> tuple[int, bool]:
    """Round one exact nanometre value to the nearest integer, reporting exactness."""

    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        rounded = value.to_integral_value(rounding="ROUND_HALF_EVEN")
        exact = rounded == value
        return int(rounded), exact


def _token_to_nm(
    token: object,
    locator: str,
    check: ForeignRouteCheck,
    limits: ForeignRouteVerificationLimits,
) -> Decimal:
    try:
        return mm_token_to_nm(
            token,
            locator,
            SimpleRouteJsonImportLimits(
                max_extent_nm=limits.max_extent_nm,
                max_number_token_length=limits.max_number_token_length,
            ),
        )
    except SimpleRouteJsonImportError as error:
        code = (
            ForeignRouteRefusalCode.BUDGET_EXCEEDED
            if error.code.value == "budget_exceeded"
            else ForeignRouteRefusalCode.UNSUPPORTED_UNIT
        )
        raise _refuse(check, code, error.message, locator[:128]) from error


def _require_trace_list(root: dict[str, Any]) -> list[Any]:
    traces = root.get("traces")
    if not isinstance(traces, list):
        raise _refuse(
            ForeignRouteCheck.STRUCTURAL_CONTINUITY,
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            "solution must carry a traces array",
            "traces",
        )
    return traces


def _stack_layer_names(snapshot: BoardIRSnapshot) -> dict[str, str]:
    """Map each declared SRJ layer name to its Board IR layer ID."""

    return {
        layer.name: layer.id
        for layer in sorted(snapshot.content.copper_layers, key=lambda item: item.index)
    }


@dataclass(slots=True)
class _WirePoint:
    x2: int
    y2: int
    layer: str
    envelope_width2: int
    coverage_width2: int


def _parse_wire_point(
    point: dict[str, Any],
    locator: str,
    layer_ids: dict[str, str],
    limits: ForeignRouteVerificationLimits,
    geometry: _Geometry,
) -> _WirePoint:
    if set(point) != _WIRE_KEYS:
        raise _refuse(
            ForeignRouteCheck.STRUCTURAL_CONTINUITY,
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            "a wire point must carry exactly route_type, x, y, width, and layer",
            locator,
        )
    layer_name = point.get("layer")
    if not isinstance(layer_name, str) or layer_name not in layer_ids:
        raise _refuse(
            ForeignRouteCheck.STRUCTURAL_CONTINUITY,
            ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT,
            "a wire point names a layer outside the declared stack",
            locator,
        )
    check = ForeignRouteCheck.STRUCTURAL_CONTINUITY
    x_nm = _token_to_nm(point.get("x"), f"{locator}.x", check, limits)
    y_nm = _token_to_nm(point.get("y"), f"{locator}.y", check, limits)
    width_nm = _token_to_nm(point.get("width"), f"{locator}.width", check, limits)
    if width_nm <= 0:
        raise _refuse(
            check,
            ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT,
            "a wire point width must be positive",
            locator,
        )
    x2, x_exact = _round_token(x_nm)
    y2, y_exact = _round_token(y_nm)
    if not (x_exact and y_exact):
        geometry.slack2 = _ROUNDING_SLACK2
    with localcontext() as context:
        context.prec = _DECIMAL_PRECISION
        envelope = int(width_nm.to_integral_value(rounding="ROUND_CEILING"))
        coverage = int(width_nm.to_integral_value(rounding="ROUND_FLOOR"))
    geometry.exact_widths.append((width_nm, locator))
    return _WirePoint(
        x2=2 * x2,
        y2=2 * y2,
        layer=layer_ids[layer_name],
        envelope_width2=envelope,
        coverage_width2=coverage,
    )


def _parse_traces(
    root: dict[str, Any],
    problem: ImportedProblem,
    connection_nets: dict[str, str],
    limits: ForeignRouteVerificationLimits,
) -> _Geometry:
    """Walk every trace once, enforcing attribution, continuity, and budgets."""

    traces = _require_trace_list(root)
    if len(traces) > limits.max_traces:
        raise _refuse(
            ForeignRouteCheck.STRUCTURAL_CONTINUITY,
            ForeignRouteRefusalCode.BUDGET_EXCEEDED,
            "trace budget exceeded",
            "traces",
        )
    layer_ids = _stack_layer_names(problem.snapshot)
    geometry = _Geometry(
        segments=[],
        vias=[],
        wire_point_count=0,
        trace_count=len(traces),
        slack2=0,
        exact_widths=[],
    )
    check = ForeignRouteCheck.STRUCTURAL_CONTINUITY
    for trace_index, entry in enumerate(traces):
        locator = f"traces[{trace_index}]"
        if not isinstance(entry, dict) or not set(entry) <= _ALLOWED_TRACE_KEYS:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "a trace must be an object with type, pcb_trace_id, connection_name, and route",
                locator,
            )
        declared_type = entry.get("type", "pcb_trace")
        if declared_type != "pcb_trace":
            raise _refuse(
                check,
                ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT,
                "a trace type outside pcb_trace is not accepted",
                locator,
            )
        connection_name = entry.get("connection_name")
        if not isinstance(connection_name, str) or not 1 <= len(connection_name) <= 512:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.UNATTRIBUTED_TRACE,
                "every trace must carry the connection_name it claims to route",
                locator,
            )
        net_id = connection_nets.get(connection_name)
        if net_id is None:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.UNATTRIBUTED_TRACE,
                "a trace names a connection the problem document does not state unambiguously",
                locator,
            )
        route = entry.get("route")
        if not isinstance(route, list) or not route:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "a trace route must be a non-empty array",
                f"{locator}.route",
            )
        if len(route) > limits.max_route_points_per_trace:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.BUDGET_EXCEEDED,
                "route point budget exceeded",
                f"{locator}.route",
            )
        geometry.wire_point_count += sum(
            1
            for element in route
            if isinstance(element, dict) and element.get("route_type") == "wire"
        )
        if geometry.wire_point_count > limits.max_total_route_points:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.BUDGET_EXCEEDED,
                "total route point budget exceeded",
                f"{locator}.route",
            )
        _walk_route(route, locator, trace_index, net_id, layer_ids, limits, geometry)
        if len(geometry.vias) > limits.max_vias:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.BUDGET_EXCEEDED,
                "via budget exceeded",
                f"{locator}.route",
            )
    return geometry


def _walk_route(
    route: list[Any],
    locator: str,
    trace_index: int,
    net_id: str,
    layer_ids: dict[str, str],
    limits: ForeignRouteVerificationLimits,
    geometry: _Geometry,
) -> None:
    check = ForeignRouteCheck.STRUCTURAL_CONTINUITY
    previous: _WirePoint | None = None
    pending_via: tuple[int, int, str] | None = None
    for position, element in enumerate(route):
        element_locator = f"{locator}.route[{position}]"
        if not isinstance(element, dict):
            raise _refuse(
                check,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "a route element must be a JSON object",
                element_locator,
            )
        route_type = element.get("route_type")
        if route_type == "wire":
            point = _parse_wire_point(element, element_locator, layer_ids, limits, geometry)
            if pending_via is not None:
                via_x2, via_y2, to_layer = pending_via
                if point.x2 != via_x2 or point.y2 != via_y2 or point.layer != to_layer:
                    raise _refuse(
                        check,
                        ForeignRouteRefusalCode.TRACE_DISCONTINUITY,
                        "the wire point after a via must sit on the via at its to_layer",
                        element_locator,
                    )
                pending_via = None
            elif previous is not None:
                if previous.layer != point.layer:
                    raise _refuse(
                        check,
                        ForeignRouteRefusalCode.TRACE_DISCONTINUITY,
                        "a layer change inside a trace requires an explicit via",
                        element_locator,
                    )
                geometry.segments.append(
                    _Segment(
                        net_id=net_id,
                        layer=point.layer,
                        ax2=previous.x2,
                        ay2=previous.y2,
                        bx2=point.x2,
                        by2=point.y2,
                        envelope_radius2=max(previous.envelope_width2, point.envelope_width2),
                        coverage_radius2=min(previous.coverage_width2, point.coverage_width2),
                        trace_index=trace_index,
                    )
                )
            previous = point
        elif route_type == "via":
            if set(element) != _VIA_KEYS:
                raise _refuse(
                    check,
                    ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                    "a via must carry exactly route_type, x, y, from_layer, and to_layer",
                    element_locator,
                )
            if previous is None or pending_via is not None:
                raise _refuse(
                    check,
                    ForeignRouteRefusalCode.TRACE_DISCONTINUITY,
                    "a via must sit between two wire points",
                    element_locator,
                )
            via_from_layer = element.get("from_layer")
            via_to_layer = element.get("to_layer")
            if (
                not isinstance(via_from_layer, str)
                or not isinstance(via_to_layer, str)
                or via_from_layer not in layer_ids
                or via_to_layer not in layer_ids
                or via_from_layer == via_to_layer
            ):
                raise _refuse(
                    check,
                    ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT,
                    "a via must span two distinct layers inside the declared stack",
                    element_locator,
                )
            x_nm = _token_to_nm(element.get("x"), f"{element_locator}.x", check, limits)
            y_nm = _token_to_nm(element.get("y"), f"{element_locator}.y", check, limits)
            x2, x_exact = _round_token(x_nm)
            y2, y_exact = _round_token(y_nm)
            if not (x_exact and y_exact):
                geometry.slack2 = _ROUNDING_SLACK2
            x2, y2 = 2 * x2, 2 * y2
            if (
                x2 != previous.x2
                or y2 != previous.y2
                or layer_ids[via_from_layer] != previous.layer
            ):
                raise _refuse(
                    check,
                    ForeignRouteRefusalCode.TRACE_DISCONTINUITY,
                    "a via must be coincident with the wire point before it on its from_layer",
                    element_locator,
                )
            geometry.vias.append(_Via(net_id=net_id, x2=x2, y2=y2, trace_index=trace_index))
            pending_via = (x2, y2, layer_ids[via_to_layer])
            previous = None
        else:
            raise _refuse(
                check,
                ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT,
                "route elements must declare route_type wire or via",
                element_locator,
            )
    if pending_via is not None:
        raise _refuse(
            check,
            ForeignRouteRefusalCode.TRACE_DISCONTINUITY,
            "a trace must not end on a via",
            f"{locator}.route",
        )


def _check_trace_widths(geometry: _Geometry, problem: ImportedProblem) -> None:
    minimum = Decimal(problem.track_width_nm)
    for width_nm, locator in geometry.exact_widths:
        if width_nm < minimum:
            raise _refuse(
                ForeignRouteCheck.TRACE_WIDTH,
                ForeignRouteRefusalCode.WIDTH_BELOW_MINIMUM,
                "a wire width is below the problem's minimum trace width",
                locator[:128],
            )


def _outline_rect(snapshot: BoardIRSnapshot) -> _Rect:
    points = snapshot.content.outline[0].outer.points
    return _Rect(
        min_x2=2 * min(point.x for point in points),
        min_y2=2 * min(point.y for point in points),
        max_x2=2 * max(point.x for point in points),
        max_y2=2 * max(point.y for point in points),
    )


def _check_containment(geometry: _Geometry, problem: ImportedProblem, policy: ImportPolicy) -> None:
    outline = _outline_rect(problem.snapshot)
    slack = geometry.slack2
    for segment in geometry.segments:
        inset = segment.envelope_radius2 + slack
        for x2, y2 in ((segment.ax2, segment.ay2), (segment.bx2, segment.by2)):
            if not (
                outline.min_x2 + inset <= x2 <= outline.max_x2 - inset
                and outline.min_y2 + inset <= y2 <= outline.max_y2 - inset
            ):
                raise _refuse(
                    ForeignRouteCheck.BOARD_CONTAINMENT,
                    ForeignRouteRefusalCode.OUTSIDE_BOARD_BOUNDS,
                    "route copper leaves the imported board outline",
                    f"traces[{segment.trace_index}]",
                )
    via_inset = policy.via_diameter_nm + slack
    for via in geometry.vias:
        if not (
            outline.min_x2 + via_inset <= via.x2 <= outline.max_x2 - via_inset
            and outline.min_y2 + via_inset <= via.y2 <= outline.max_y2 - via_inset
        ):
            raise _refuse(
                ForeignRouteCheck.BOARD_CONTAINMENT,
                ForeignRouteRefusalCode.OUTSIDE_BOARD_BOUNDS,
                "a via leaves the imported board outline",
                f"traces[{via.trace_index}]",
            )


def _pad_elements(problem: ImportedProblem) -> list[_PadElement]:
    """Return every imported pad with its blocking and attachment rectangles.

    The blocking rectangle is the pad's full outward-rounded extent.  The attachment core is the
    inscribed central rectangle for an oval pad, and it is additionally shrunk on every side by
    the import's recorded worst outward rounding, so contact with the core implies contact with
    copper the source document actually stated.
    """

    shrink = 2 * problem.statistics.max_outward_rounding_nm
    elements: list[_PadElement] = []
    for pad in sorted(problem.snapshot.content.pads, key=lambda item: item.id):
        if pad.net_id is None:
            continue
        cx2, cy2 = 2 * pad.center.x, 2 * pad.center.y
        half_x2, half_y2 = pad.size_x_nm, pad.size_y_nm
        block = _Rect(cx2 - half_x2, cy2 - half_y2, cx2 + half_x2, cy2 + half_y2)
        if pad.shape is PadShape.OVAL:
            short2 = min(half_x2, half_y2)
            core_half_x2 = half_x2 - short2 if half_x2 > half_y2 else 0
            core_half_y2 = half_y2 - short2 if half_y2 > half_x2 else 0
            if half_x2 > half_y2:
                core_half_y2 = half_y2
            elif half_y2 > half_x2:
                core_half_x2 = half_x2
        else:
            core_half_x2, core_half_y2 = half_x2, half_y2
        core = _Rect(
            cx2 - max(0, core_half_x2 - shrink),
            cy2 - max(0, core_half_y2 - shrink),
            cx2 + max(0, core_half_x2 - shrink),
            cy2 + max(0, core_half_y2 - shrink),
        )
        elements.append(
            _PadElement(
                net_id=pad.net_id,
                layers=frozenset(pad.layer_ids),
                block=block,
                core=core,
            )
        )
    return elements


def _keepout_rects(problem: ImportedProblem) -> list[tuple[frozenset[str], _Rect]]:
    rects: list[tuple[frozenset[str], _Rect]] = []
    for keepout in sorted(problem.snapshot.content.keepouts, key=lambda item: item.id):
        points = keepout.boundary.points
        rects.append(
            (
                frozenset(keepout.layer_ids),
                _Rect(
                    min_x2=2 * min(point.x for point in points),
                    min_y2=2 * min(point.y for point in points),
                    max_x2=2 * max(point.x for point in points),
                    max_y2=2 * max(point.y for point in points),
                ),
            )
        )
    return rects


def _check_clearance(
    geometry: _Geometry,
    problem: ImportedProblem,
    policy: ImportPolicy,
    pads: list[_PadElement],
    budget: _PairBudget,
) -> None:
    """Refuse on any exact-clearance violation between foreign copper and everything else."""

    clearance2 = 2 * policy.clearance_nm
    slack = geometry.slack2
    keepouts = _keepout_rects(problem)
    via_radius2 = policy.via_diameter_nm

    for segment in geometry.segments:
        limit_rect = clearance2 + segment.envelope_radius2 + slack
        for layers, rect in keepouts:
            if segment.layer not in layers:
                continue
            budget.spend()
            if (
                _compare_segment_rect(
                    segment.ax2, segment.ay2, segment.bx2, segment.by2, rect, limit_rect
                )
                < 0
            ):
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "route copper violates clearance to a keepout obstacle",
                    f"traces[{segment.trace_index}]",
                )
        for pad in pads:
            if pad.net_id == segment.net_id or segment.layer not in pad.layers:
                continue
            budget.spend()
            if (
                _compare_segment_rect(
                    segment.ax2, segment.ay2, segment.bx2, segment.by2, pad.block, limit_rect
                )
                < 0
            ):
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "route copper violates clearance to another net's copper",
                    f"traces[{segment.trace_index}]",
                )

    for via in geometry.vias:
        limit_rect = clearance2 + via_radius2 + slack
        for layers, rect in keepouts:
            del layers  # a through-via barrel crosses every declared layer
            budget.spend()
            if _compare_point_rect(via.x2, via.y2, rect, limit_rect) < 0:
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "a via violates clearance to a keepout obstacle",
                    f"traces[{via.trace_index}]",
                )
        for pad in pads:
            if pad.net_id == via.net_id:
                continue
            budget.spend()
            if _compare_point_rect(via.x2, via.y2, pad.block, limit_rect) < 0:
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "a via violates clearance to another net's copper",
                    f"traces[{via.trace_index}]",
                )

    segments = geometry.segments
    for left_index in range(len(segments)):
        left = segments[left_index]
        for right_index in range(left_index + 1, len(segments)):
            right = segments[right_index]
            if left.net_id == right.net_id or left.layer != right.layer:
                continue
            budget.spend()
            limit = clearance2 + left.envelope_radius2 + right.envelope_radius2 + slack
            if (
                _compare_segment_segment(
                    left.ax2,
                    left.ay2,
                    left.bx2,
                    left.by2,
                    right.ax2,
                    right.ay2,
                    right.bx2,
                    right.by2,
                    limit,
                )
                < 0
            ):
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "two traces on different nets violate pairwise clearance",
                    f"traces[{left.trace_index}]",
                )
    for via in geometry.vias:
        for segment in segments:
            if via.net_id == segment.net_id:
                continue
            budget.spend()
            limit = clearance2 + via_radius2 + segment.envelope_radius2 + slack
            if (
                _compare_point_segment(
                    via.x2, via.y2, segment.ax2, segment.ay2, segment.bx2, segment.by2, limit
                )
                < 0
            ):
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "a via and another net's trace violate pairwise clearance",
                    f"traces[{via.trace_index}]",
                )
    vias = geometry.vias
    for left_index in range(len(vias)):
        for right_index in range(left_index + 1, len(vias)):
            left_via, right_via = vias[left_index], vias[right_index]
            if left_via.net_id == right_via.net_id:
                continue
            budget.spend()
            gap_x = left_via.x2 - right_via.x2
            gap_y = left_via.y2 - right_via.y2
            limit = clearance2 + 2 * via_radius2 + slack
            if gap_x * gap_x + gap_y * gap_y < limit * limit:
                raise _refuse(
                    ForeignRouteCheck.CLEARANCE,
                    ForeignRouteRefusalCode.CLEARANCE_VIOLATION,
                    "two vias on different nets violate pairwise clearance",
                    f"traces[{left_via.trace_index}]",
                )


class _UnionFind:
    def __init__(self, size: int) -> None:
        self._parent = list(range(size))

    def find(self, item: int) -> int:
        root = item
        while self._parent[root] != root:
            root = self._parent[root]
        while self._parent[item] != root:
            self._parent[item], item = root, self._parent[item]
        return root

    def union(self, left: int, right: int) -> None:
        left_root, right_root = self.find(left), self.find(right)
        if left_root != right_root:
            self._parent[max(left_root, right_root)] = min(left_root, right_root)


def _check_connectivity(
    geometry: _Geometry,
    problem: ImportedProblem,
    policy: ImportPolicy,
    pads: list[_PadElement],
    budget: _PairBudget,
) -> None:
    """Refuse any multi-pad net whose pads the under-approximated copper does not all join.

    This is deliberately *pad-complete* rather than point-complete: every pad of the net must be
    in one connected component, which is stricter than joining only the stated connection points
    and therefore stays on the refusing side.
    """

    slack = geometry.slack2
    via_radius2 = policy.via_diameter_nm
    for net in problem.nets:
        if net.pad_count < 2:
            continue
        net_pads = [pad for pad in pads if pad.net_id == net.net_id]
        net_traces = sorted(
            {segment.trace_index for segment in geometry.segments if segment.net_id == net.net_id}
            | {via.trace_index for via in geometry.vias if via.net_id == net.net_id}
        )
        trace_slot = {
            trace_index: len(net_pads) + slot for slot, trace_index in enumerate(net_traces)
        }
        union = _UnionFind(len(net_pads) + len(net_traces))
        segments_by_trace: dict[int, list[_Segment]] = {index: [] for index in net_traces}
        vias_by_trace: dict[int, list[_Via]] = {index: [] for index in net_traces}
        for segment in geometry.segments:
            if segment.net_id == net.net_id:
                segments_by_trace[segment.trace_index].append(segment)
        for via in geometry.vias:
            if via.net_id == net.net_id:
                vias_by_trace[via.trace_index].append(via)

        for pad_index, pad in enumerate(net_pads):
            for trace_index in net_traces:
                if _trace_touches_pad(
                    segments_by_trace[trace_index],
                    vias_by_trace[trace_index],
                    pad,
                    via_radius2,
                    slack,
                    budget,
                ):
                    union.union(pad_index, trace_slot[trace_index])
        for left_position in range(len(net_traces)):
            for right_position in range(left_position + 1, len(net_traces)):
                left_trace = net_traces[left_position]
                right_trace = net_traces[right_position]
                if _traces_touch(
                    segments_by_trace[left_trace],
                    vias_by_trace[left_trace],
                    segments_by_trace[right_trace],
                    vias_by_trace[right_trace],
                    via_radius2,
                    slack,
                    budget,
                ):
                    union.union(trace_slot[left_trace], trace_slot[right_trace])
        for left_index in range(len(net_pads)):
            for right_index in range(left_index + 1, len(net_pads)):
                left_pad, right_pad = net_pads[left_index], net_pads[right_index]
                if not left_pad.layers & right_pad.layers:
                    continue
                budget.spend()
                # Pad rectangles come from the problem document, not the solution, so the
                # solution's coordinate-rounding slack does not apply to this contact test.
                if _compare_rect_rect(left_pad.core, right_pad.core, 0) <= 0:
                    union.union(left_index, right_index)

        roots = {union.find(index) for index in range(len(net_pads))}
        if len(roots) > 1:
            raise _refuse(
                ForeignRouteCheck.CONNECTIVITY,
                ForeignRouteRefusalCode.NOT_CONNECTED,
                "the submitted copper does not join every pad of a multi-pad net",
                net.net_id,
            )


def _trace_touches_pad(
    segments: list[_Segment],
    vias: list[_Via],
    pad: _PadElement,
    via_radius2: int,
    slack: int,
    budget: _PairBudget,
) -> bool:
    for segment in segments:
        if segment.layer not in pad.layers:
            continue
        budget.spend()
        limit = segment.coverage_radius2 - slack
        if limit < 0:
            continue
        if (
            _compare_segment_rect(
                segment.ax2, segment.ay2, segment.bx2, segment.by2, pad.core, limit
            )
            <= 0
        ):
            return True
    for via in vias:
        budget.spend()
        limit = via_radius2 - slack
        if limit < 0:
            continue
        if _compare_point_rect(via.x2, via.y2, pad.core, limit) <= 0:
            return True
    return False


def _traces_touch(
    left_segments: list[_Segment],
    left_vias: list[_Via],
    right_segments: list[_Segment],
    right_vias: list[_Via],
    via_radius2: int,
    slack: int,
    budget: _PairBudget,
) -> bool:
    for left in left_segments:
        for right in right_segments:
            if left.layer != right.layer:
                continue
            budget.spend()
            limit = left.coverage_radius2 + right.coverage_radius2 - slack
            if limit < 0:
                continue
            if (
                _compare_segment_segment(
                    left.ax2,
                    left.ay2,
                    left.bx2,
                    left.by2,
                    right.ax2,
                    right.ay2,
                    right.bx2,
                    right.by2,
                    limit,
                )
                <= 0
            ):
                return True
    for via in left_vias:
        for segment in right_segments:
            budget.spend()
            limit = via_radius2 + segment.coverage_radius2 - slack
            if limit < 0:
                continue
            if (
                _compare_point_segment(
                    via.x2, via.y2, segment.ax2, segment.ay2, segment.bx2, segment.by2, limit
                )
                <= 0
            ):
                return True
    for via in right_vias:
        for segment in left_segments:
            budget.spend()
            limit = via_radius2 + segment.coverage_radius2 - slack
            if limit < 0:
                continue
            if (
                _compare_point_segment(
                    via.x2, via.y2, segment.ax2, segment.ay2, segment.bx2, segment.by2, limit
                )
                <= 0
            ):
                return True
    for left_via in left_vias:
        for right_via in right_vias:
            budget.spend()
            limit = 2 * via_radius2 - slack
            if limit < 0:
                continue
            gap_x = left_via.x2 - right_via.x2
            gap_y = left_via.y2 - right_via.y2
            if gap_x * gap_x + gap_y * gap_y <= limit * limit:
                return True
    return False


class _CheckLedger:
    """Ordered evidence: every check is not_run until it passes or fails."""

    def __init__(self) -> None:
        self._status = dict.fromkeys(ForeignRouteCheck, ForeignRouteCheckStatus.NOT_RUN)

    def passed(self, check: ForeignRouteCheck) -> None:
        self._status[check] = ForeignRouteCheckStatus.PASSED

    def failed(self, check: ForeignRouteCheck) -> None:
        self._status[check] = ForeignRouteCheckStatus.FAILED

    def evidence(self) -> tuple[ForeignRouteCheckEvidence, ...]:
        return tuple(
            ForeignRouteCheckEvidence(check=check, status=self._status[check])
            for check in ForeignRouteCheck
        )


def verify_foreign_simple_route_json(
    problem_document: object,
    solution_document: object,
    *,
    expected_problem_sha256: object,
    policy: object = None,
    limits: object = None,
) -> ForeignRouteVerificationResult:
    """Verify one foreign SimpleRouteJson solution against one problem document.

    The function never raises for untrusted input: every refusal is a typed
    :class:`ForeignRouteRefusal` inside the result, with per-check evidence recording which
    checks ran.  ``expected_problem_sha256`` is the caller's revision binding and is compared
    against a digest this function computes itself over ``problem_document``.  Nothing here
    mints an identity, repairs geometry, or issues any authority.
    """

    ledger = _CheckLedger()
    active_policy = policy if policy is not None else DEFAULT_IMPORT_POLICY
    active_limits = limits if limits is not None else ForeignRouteVerificationLimits()
    if not isinstance(active_policy, ImportPolicy) or not isinstance(
        active_limits, ForeignRouteVerificationLimits
    ):
        return ForeignRouteVerificationResult(
            checks=ledger.evidence(),
            policy=DEFAULT_IMPORT_POLICY,
            refusal=ForeignRouteRefusal(
                code=ForeignRouteRefusalCode.INVALID_REQUEST,
                message="verification policy or limits are malformed",
                locator="request",
            ),
        )

    problem_sha256: str | None = None
    solution_sha256: str | None = None
    snapshot_digest: str | None = None
    geometry: _Geometry | None = None
    budget = _PairBudget(active_limits.max_pair_checks, ForeignRouteCheck.CLEARANCE)
    try:
        # 1. Document contract: both payloads are bounded bytes; the solution parses strictly.
        if not isinstance(problem_document, bytes | bytearray):
            raise _refuse(
                ForeignRouteCheck.DOCUMENT_CONTRACT,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "problem document must be raw bytes",
            )
        problem_bytes = bytes(problem_document)
        if len(problem_bytes) > active_limits.max_document_bytes:
            raise _refuse(
                ForeignRouteCheck.DOCUMENT_CONTRACT,
                ForeignRouteRefusalCode.BUDGET_EXCEEDED,
                "problem document exceeds the byte budget",
            )
        problem_sha256 = _hash_bytes(problem_bytes)
        if not isinstance(solution_document, bytes | bytearray):
            raise _refuse(
                ForeignRouteCheck.DOCUMENT_CONTRACT,
                ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                "solution document must be raw bytes",
            )
        solution_bytes = bytes(solution_document)
        root = _parse_solution_root(solution_bytes, active_limits)
        solution_sha256 = _hash_bytes(solution_bytes)
        ledger.passed(ForeignRouteCheck.DOCUMENT_CONTRACT)

        # 2. Revision binding: computed digest against the caller's stated one.
        expected = _normalized_expected_digest(expected_problem_sha256)
        if expected is None:
            raise _refuse(
                ForeignRouteCheck.REVISION_BINDING,
                ForeignRouteRefusalCode.WRONG_REVISION,
                "the expected problem digest is not a sha256 content address",
                "expected_problem_sha256",
            )
        if expected != problem_sha256:
            raise _refuse(
                ForeignRouteCheck.REVISION_BINDING,
                ForeignRouteRefusalCode.WRONG_REVISION,
                "the problem document is not the one this solution was declared against",
                "expected_problem_sha256",
            )
        ledger.passed(ForeignRouteCheck.REVISION_BINDING)

        # 3. Identity hygiene: reserved CopperMCP keys are refused, never ignored.
        _check_identity_hygiene(root)
        ledger.passed(ForeignRouteCheck.IDENTITY_HYGIENE)

        # 4. Problem import through the conservative seam.
        problem = _import_problem(problem_bytes, active_policy, active_limits)
        snapshot_digest = problem.snapshot.snapshot_digest
        ledger.passed(ForeignRouteCheck.PROBLEM_IMPORT)

        # 5. Structural continuity, attribution, and budgets — one bounded walk.
        connection_nets = _connection_net_map(problem)
        geometry = _parse_traces(root, problem, connection_nets, active_limits)
        ledger.passed(ForeignRouteCheck.STRUCTURAL_CONTINUITY)

        # 6. Widths, compared exactly against the imported minimum.
        _check_trace_widths(geometry, problem)
        ledger.passed(ForeignRouteCheck.TRACE_WIDTH)

        # 7. Containment inside the inward-rounded outline.
        _check_containment(geometry, problem, active_policy)
        ledger.passed(ForeignRouteCheck.BOARD_CONTAINMENT)

        # 8. Exact clearance with the route over-approximated outward.
        pads = _pad_elements(problem)
        _check_clearance(geometry, problem, active_policy, pads, budget)
        ledger.passed(ForeignRouteCheck.CLEARANCE)

        # 9. Connectivity with the route under-approximated inward.
        budget.check = ForeignRouteCheck.CONNECTIVITY
        _check_connectivity(geometry, problem, active_policy, pads, budget)
        ledger.passed(ForeignRouteCheck.CONNECTIVITY)
    except _RefusalError as error:
        ledger.failed(error.check)
        return _result(
            ledger,
            active_policy,
            error.refusal,
            problem_sha256,
            solution_sha256,
            snapshot_digest,
            geometry,
            budget,
        )
    except Exception:  # pragma: no cover - unexpected conditions must fail closed, not open
        ledger.failed(ForeignRouteCheck.DOCUMENT_CONTRACT)
        return _result(
            ledger,
            active_policy,
            ForeignRouteRefusal(
                code=ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
                message="verification failed closed on an unexpected condition",
                locator="document",
            ),
            problem_sha256,
            solution_sha256,
            snapshot_digest,
            geometry,
            budget,
        )
    return _result(
        ledger,
        active_policy,
        None,
        problem_sha256,
        solution_sha256,
        snapshot_digest,
        geometry,
        budget,
    )


def _result(
    ledger: _CheckLedger,
    policy: ImportPolicy,
    refusal: ForeignRouteRefusal | None,
    problem_sha256: str | None,
    solution_sha256: str | None,
    snapshot_digest: str | None,
    geometry: _Geometry | None,
    budget: _PairBudget,
) -> ForeignRouteVerificationResult:
    return ForeignRouteVerificationResult(
        checks=ledger.evidence(),
        policy=policy,
        refusal=refusal,
        problem_sha256=problem_sha256,
        solution_sha256=solution_sha256,
        snapshot_digest=snapshot_digest,
        trace_count=0 if geometry is None else geometry.trace_count,
        wire_point_count=0 if geometry is None else geometry.wire_point_count,
        via_count=0 if geometry is None else len(geometry.vias),
        segment_count=0 if geometry is None else len(geometry.segments),
        pair_checks=budget.count,
        rounding_slack_doubled_nm=0 if geometry is None else geometry.slack2,
    )


__all__ = [
    "ACCEPTANCE_CLAIM",
    "FOREIGN_ROUTE_VERIFIER_VERSION",
    "NON_CLAIMS",
    "ForeignRouteApplyAuthority",
    "ForeignRouteCheck",
    "ForeignRouteCheckEvidence",
    "ForeignRouteCheckStatus",
    "ForeignRouteKicadDrc",
    "ForeignRouteOrigin",
    "ForeignRouteRefusal",
    "ForeignRouteRefusalCode",
    "ForeignRouteRepair",
    "ForeignRouteVerificationLimits",
    "ForeignRouteVerificationResult",
    "verify_foreign_simple_route_json",
]
