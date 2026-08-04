"""Request, result, and failure vocabulary for candidate application."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from copper_mcp.apply.engine import ApplyVerification
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    RequestError,
    board_path,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)

APPLY_VERSION = "0.1.0"
PLACEMENT_APPLY_VERSION = "0.1.0"
_SHA256 = r"^sha256:[0-9a-f]{64}$"
MAX_TOKEN_CHARACTERS = 512
#: Caps on the candidate geometry carried in a manifest, enforced before anything is
#: materialised so an unbounded manifest cannot force unbounded work ahead of authorisation.
MAX_MANIFEST_PATHS = 4096
MAX_MANIFEST_VERTICES = 100_000
MAX_MANIFEST_FIELD_CHARACTERS = 256
MAX_MANIFEST_PLACEMENTS = 4_096
MAX_MANIFEST_RULE_RESULTS = 16_384
MAX_MANIFEST_COORDINATE_NM = 1_000_000_000


class ApplyRequestError(ValueError):
    """Raised when an untrusted apply request violates its declared contract."""


class ApplyFailureCode(StrEnum):
    """Why an apply produced no change.

    ``stale_candidate`` and ``apply_verification_failed`` are deliberately distinct: the first
    means nothing was written because the board moved, the second means something was written
    and then found wrong. A caller must be able to tell those apart.
    """

    INVALID_REQUEST = "invalid_request"
    APPLY_DISABLED = "apply_disabled"
    # These name failure codes, not secrets; the scanner matches on "TOKEN" alone.
    INVALID_TOKEN = "invalid_token"  # noqa: S105
    TOKEN_EXPIRED = "token_expired"  # noqa: S105
    TOKEN_ALREADY_USED = "token_already_used"  # noqa: S105
    STALE_CANDIDATE = "stale_candidate"
    BACKUP_FAILED = "backup_failed"
    KICAD_OPEN = "kicad_open"
    UNSUPPORTED_BOARD = "unsupported_board"
    UNSAFE_FILESYSTEM = "unsafe_filesystem"
    SPLICE_ASSERTION_FAILED = "splice_assertion_failed"
    APPLY_VERIFICATION_FAILED = "apply_verification_failed"


@dataclass(frozen=True, slots=True)
class ApplyRequest:
    """One validated apply request."""

    board: str
    candidate: Mapping[str, Any]
    apply_token: str
    expect_board_revision: str
    constraints: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate", MappingProxyType(dict(self.candidate)))

    def constraints_payload(self) -> dict[str, int]:
        return {name: getattr(self.constraints, name) for name in CONSTRAINT_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        """Echo the validated request. The token is never echoed back."""

        return {
            "board": self.board,
            "expect_board_revision": self.expect_board_revision,
            "candidate_id": str(self.candidate.get("candidate_id", "")),
            "constraints": self.constraints_payload(),
        }


@dataclass(frozen=True, slots=True)
class PlacementApplyRequest:
    """One validated, separately authorized placement-apply request."""

    board: str
    candidate: Mapping[str, Any]
    apply_token: str
    expect_board_revision: str
    constraints: Any

    def __post_init__(self) -> None:
        object.__setattr__(self, "candidate", MappingProxyType(dict(self.candidate)))

    def constraints_payload(self) -> dict[str, int]:
        return {name: getattr(self.constraints, name) for name in CONSTRAINT_FIELDS}

    def to_dict(self) -> dict[str, Any]:
        """Echo the validated request without ever returning its capability token."""

        return {
            "board": self.board,
            "expect_board_revision": self.expect_board_revision,
            "candidate_id": str(self.candidate.get("candidate_id", "")),
            "constraints": self.constraints_payload(),
        }


_REQUIRED = ("board", "candidate", "apply_token", "expect_board_revision", "constraints")


def parse_apply_request(payload: Any) -> ApplyRequest:
    """Validate one untrusted apply request without echoing unvalidated input."""

    import re

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED))
        required_fields("request", fields, _REQUIRED)
        revision = text("expect_board_revision", fields["expect_board_revision"], maximum=128)
        if not re.fullmatch(_SHA256, revision):
            raise ApplyRequestError("expect_board_revision must be a sha256 digest")
        candidate = mapping("candidate", fields["candidate"])
        _bound_manifest(candidate)
        return ApplyRequest(
            board=board_path(fields["board"]),
            candidate=candidate,
            apply_token=text("apply_token", fields["apply_token"], maximum=MAX_TOKEN_CHARACTERS),
            expect_board_revision=revision,
            constraints=net_class_constraints(fields["constraints"]),
        )
    except ApplyRequestError:
        raise
    except RequestError as error:
        raise ApplyRequestError(str(error)) from error


def parse_placement_apply_request(payload: Any) -> PlacementApplyRequest:
    """Validate a placement apply request before any board bytes are read."""

    import re

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED))
        required_fields("request", fields, _REQUIRED)
        revision = text("expect_board_revision", fields["expect_board_revision"], maximum=128)
        if not re.fullmatch(_SHA256, revision):
            raise ApplyRequestError("expect_board_revision must be a sha256 digest")
        candidate = mapping("candidate", fields["candidate"])
        _bound_manifest(candidate, placement=True)
        return PlacementApplyRequest(
            board=board_path(fields["board"]),
            candidate=candidate,
            apply_token=text("apply_token", fields["apply_token"], maximum=MAX_TOKEN_CHARACTERS),
            expect_board_revision=revision,
            constraints=net_class_constraints(fields["constraints"]),
        )
    except ApplyRequestError:
        raise
    except RequestError as error:
        raise ApplyRequestError(str(error)) from error


def _bound_manifest(candidate: Mapping[str, Any], *, placement: bool = False) -> None:
    """Cap the geometry a manifest can carry, before any of it is materialised.

    All of the pre-authorisation work - the board read, the IR parse, the candidate decode -
    happens for whatever manifest a caller sends, so an unbounded manifest is an unbounded
    workload handed to a destructive tool. The identity fields the token binds are also capped
    here so they cannot be pathological strings.
    """

    for field_name in ("candidate_id", "base_revision"):
        value = candidate.get(field_name)
        if value is not None and (
            not isinstance(value, str) or len(value) > MAX_MANIFEST_FIELD_CHARACTERS
        ):
            raise ApplyRequestError("a candidate identity field is malformed")
    if placement:
        known_fields(
            "placement candidate",
            candidate,
            frozenset(
                {
                    "candidate_id",
                    "base_revision",
                    "view_revision",
                    "placement_version",
                    "ordering_policy",
                    "placement_grid_nm",
                    "placements",
                    "evidence",
                }
            ),
        )
        for field_name in ("view_revision", "placement_version", "ordering_policy"):
            value = candidate.get(field_name)
            if not isinstance(value, str) or len(value) > MAX_MANIFEST_FIELD_CHARACTERS:
                raise ApplyRequestError("a placement candidate field is malformed")
        grid = candidate.get("placement_grid_nm")
        if type(grid) is not int or not 1 <= grid <= MAX_MANIFEST_COORDINATE_NM:
            raise ApplyRequestError("a placement grid is malformed")
        placements = candidate.get("placements")
        if not isinstance(placements, list | tuple) or len(placements) > MAX_MANIFEST_PLACEMENTS:
            raise ApplyRequestError("the placement candidate carries too many footprints")
        total_rules = 0
        for item in placements:
            if not isinstance(item, Mapping):
                raise ApplyRequestError("a placement entry is malformed")
            known_fields(
                "placement entry",
                item,
                frozenset({"ref_id", "origin_nm", "orientation_udeg", "side", "moved"}),
            )
            required_fields(
                "placement entry",
                item,
                ("ref_id", "origin_nm", "orientation_udeg", "side", "moved"),
            )
            ref_id = item.get("ref_id")
            if not isinstance(ref_id, str) or len(ref_id) > MAX_MANIFEST_FIELD_CHARACTERS:
                raise ApplyRequestError("a placement reference is malformed")
            origin = item.get("origin_nm")
            if (
                not isinstance(origin, list | tuple)
                or len(origin) != 2
                or any(
                    type(value) is not int or abs(value) > MAX_MANIFEST_COORDINATE_NM
                    for value in origin
                )
            ):
                raise ApplyRequestError("a placement origin is malformed")
            orientation = item.get("orientation_udeg")
            if type(orientation) is not int or orientation not in {
                0,
                90_000_000,
                180_000_000,
                270_000_000,
            }:
                raise ApplyRequestError("a placement rotation is malformed")
            side = item.get("side")
            if not isinstance(side, str) or side not in {"front", "back"}:
                raise ApplyRequestError("a placement side is malformed")
            if type(item.get("moved")) is not bool:
                raise ApplyRequestError("a placement moved flag is malformed")
        evidence = candidate.get("evidence")
        if not isinstance(evidence, Mapping):
            raise ApplyRequestError("placement evidence is malformed")
        known_fields(
            "placement evidence",
            evidence,
            frozenset({"rule_results", "legality", "checks_used", "inconclusive_pairs"}),
        )
        required_fields(
            "placement evidence",
            evidence,
            ("rule_results", "legality", "checks_used", "inconclusive_pairs"),
        )
        rule_results = evidence.get("rule_results")
        if not isinstance(rule_results, list | tuple) or len(rule_results) > (
            MAX_MANIFEST_RULE_RESULTS
        ):
            raise ApplyRequestError("the placement candidate carries too many rule results")
        legality = evidence.get("legality")
        if not isinstance(legality, Mapping):
            raise ApplyRequestError("placement legality evidence is malformed")
        known_fields(
            "placement legality",
            legality,
            frozenset(
                {"pad_overlap", "outline_containment", "keepout_respect", "courtyard_overlap"}
            ),
        )
        for item in rule_results:
            if not isinstance(item, Mapping):
                raise ApplyRequestError("placement rule evidence is malformed")
            known_fields(
                "placement rule evidence",
                item,
                frozenset({"rule_index", "kind", "status", "residual_nm"}),
            )
            required_fields(
                "placement rule evidence",
                item,
                ("rule_index", "kind", "status", "residual_nm"),
            )
            rule_index = item.get("rule_index")
            residual = item.get("residual_nm")
            if (
                type(rule_index) is not int
                or not 0 <= rule_index <= MAX_MANIFEST_RULE_RESULTS
                or type(residual) is not int
                or not 0 <= residual <= MAX_MANIFEST_COORDINATE_NM
            ):
                raise ApplyRequestError("placement rule evidence is malformed")
            for field_name in ("kind", "status"):
                value = item.get(field_name)
                if not isinstance(value, str) or len(value) > MAX_MANIFEST_FIELD_CHARACTERS:
                    raise ApplyRequestError("placement rule evidence is malformed")
        checks_used = evidence.get("checks_used")
        inconclusive_pairs = evidence.get("inconclusive_pairs")
        if (
            type(checks_used) is not int
            or not 0 <= checks_used <= MAX_MANIFEST_RULE_RESULTS
            or type(inconclusive_pairs) is not int
            or not 0 <= inconclusive_pairs <= MAX_MANIFEST_RULE_RESULTS
        ):
            raise ApplyRequestError("placement evidence counters are malformed")
        for field_name in (
            "pad_overlap",
            "outline_containment",
            "keepout_respect",
            "courtyard_overlap",
        ):
            value = legality.get(field_name)
            if not isinstance(value, str) or len(value) > MAX_MANIFEST_FIELD_CHARACTERS:
                raise ApplyRequestError("placement legality evidence is malformed")
        total_rules += len(rule_results)
        if total_rules > MAX_MANIFEST_RULE_RESULTS:
            raise ApplyRequestError("the placement candidate carries too many rule results")
        return

    patch = candidate.get("patch")
    if not isinstance(patch, Mapping):
        return
    paths = patch.get("paths")
    if paths is None:
        return
    if not isinstance(paths, list | tuple) or len(paths) > MAX_MANIFEST_PATHS:
        raise ApplyRequestError("the candidate carries too many route paths")
    total = 0
    for item in paths:
        if not isinstance(item, Mapping):
            raise ApplyRequestError("a route path is malformed")
        vertices = item.get("vertices_nm")
        if not isinstance(vertices, list | tuple):
            raise ApplyRequestError("a route path is malformed")
        total += len(vertices)
        if total > MAX_MANIFEST_VERTICES:
            raise ApplyRequestError("the candidate carries too many route vertices")


@dataclass(frozen=True, slots=True)
class ApplyDiagnostic:
    """One typed, non-echoing refusal."""

    code: ApplyFailureCode
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"code": str(self.code), "message": self.message}


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """Either a board that changed, or a typed reason why nothing did.

    Three statuses, because a destructive tool must never round its own outcome:

    * ``applied`` - the rename happened and was verified. ``diagnostic`` is absent.
    * ``refused`` - the board was **not** touched. ``board_revision_after`` is absent, because
      there is no new revision, and ``board_revision_before`` may itself be absent when the
      refusal came before the board was ever read (a disabled flag, a malformed request).
    * ``applied_but_unverified`` - the rename happened but a later step could not be confirmed.
      The board *is* changed, so ``board_revision_after`` is set truthfully and a diagnostic
      explains what could not be verified. Reporting this as ``refused`` would be a lie.
    """

    status: str
    board_path: str
    board_revision_before: str | None = None
    board_revision_after: str | None = None
    snapshot_digest_before: str | None = None
    base_revision: str | None = None
    candidate_id: str | None = None
    request: ApplyRequest | None = None
    backup_path: str | None = None
    bytes_added: int = 0
    segments_added: int = 0
    verification: ApplyVerification | None = None
    diagnostic: ApplyDiagnostic | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(self.conversion_diagnostic_counts)),
        )
        if self.status not in {"applied", "refused", "applied_but_unverified"}:
            raise ApplyRequestError("an apply status is malformed")
        if self.status == "applied":
            if self.diagnostic is not None:
                raise ApplyRequestError("an applied board carries no diagnostic")
            if self.board_revision_after is None or self.verification is None:
                raise ApplyRequestError(
                    "an applied board must report its new revision and evidence"
                )
            if self.board_revision_after == self.board_revision_before:
                raise ApplyRequestError("an applied board must differ from the board it replaced")
        elif self.status == "applied_but_unverified":
            if self.diagnostic is None or self.board_revision_after is None:
                raise ApplyRequestError(
                    "an unverified apply must report both what changed and why it is unverified"
                )
        else:  # refused
            if self.diagnostic is None:
                raise ApplyRequestError("a refusal must carry a diagnostic")
            if self.board_revision_after is not None:
                raise ApplyRequestError("a refusal must not report a new revision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "apply_version": APPLY_VERSION,
            "board_path": self.board_path,
            "board_revision_before": self.board_revision_before,
            "board_revision_after": self.board_revision_after,
            "snapshot_digest_before": self.snapshot_digest_before,
            "base_revision": self.base_revision,
            "candidate_id": self.candidate_id,
            "request": None if self.request is None else self.request.to_dict(),
            "backup_path": self.backup_path,
            "bytes_added": self.bytes_added,
            "segments_added": self.segments_added,
            "verification": None if self.verification is None else self.verification.to_dict(),
            "diagnostic": None if self.diagnostic is None else self.diagnostic.to_dict(),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


@dataclass(frozen=True, slots=True)
class PlacementApplyResult:
    """Result vocabulary for the separately authorized placement mutation surface."""

    status: str
    board_path: str
    board_revision_before: str | None = None
    board_revision_after: str | None = None
    snapshot_digest_before: str | None = None
    base_revision: str | None = None
    candidate_id: str | None = None
    request: PlacementApplyRequest | None = None
    backup_path: str | None = None
    bytes_changed: int = 0
    footprints_moved: int = 0
    verification: ApplyVerification | None = None
    diagnostic: ApplyDiagnostic | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(self.conversion_diagnostic_counts)),
        )
        if self.status not in {"applied", "refused", "applied_but_unverified"}:
            raise ApplyRequestError("a placement apply status is malformed")
        if self.bytes_changed < 0 or self.footprints_moved < 0:
            raise ApplyRequestError("placement apply metrics must be non-negative")
        if self.status == "applied":
            if self.diagnostic is not None or self.verification is None:
                raise ApplyRequestError("an applied placement carries no diagnostic")
            if self.board_revision_after is None:
                raise ApplyRequestError("an applied placement must report its new revision")
            if self.board_revision_after == self.board_revision_before:
                raise ApplyRequestError("an applied placement must differ from its source")
            if self.footprints_moved < 1:
                raise ApplyRequestError("an applied placement must move a footprint")
        elif self.status == "applied_but_unverified":
            if self.diagnostic is None or self.board_revision_after is None:
                raise ApplyRequestError(
                    "an unverified placement apply must report what changed and why"
                )
        else:
            if self.diagnostic is None or self.board_revision_after is not None:
                raise ApplyRequestError("a placement refusal must report no new revision")

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "placement_apply_version": PLACEMENT_APPLY_VERSION,
            "board_path": self.board_path,
            "board_revision_before": self.board_revision_before,
            "board_revision_after": self.board_revision_after,
            "snapshot_digest_before": self.snapshot_digest_before,
            "base_revision": self.base_revision,
            "candidate_id": self.candidate_id,
            "request": None if self.request is None else self.request.to_dict(),
            "backup_path": self.backup_path,
            "bytes_changed": self.bytes_changed,
            "footprints_moved": self.footprints_moved,
            "verification": None if self.verification is None else self.verification.to_dict(),
            "diagnostic": None if self.diagnostic is None else self.diagnostic.to_dict(),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


__all__ = [
    "APPLY_VERSION",
    "PLACEMENT_APPLY_VERSION",
    "ApplyDiagnostic",
    "ApplyFailureCode",
    "ApplyRequest",
    "ApplyRequestError",
    "ApplyResult",
    "PlacementApplyRequest",
    "PlacementApplyResult",
    "parse_apply_request",
    "parse_placement_apply_request",
]
