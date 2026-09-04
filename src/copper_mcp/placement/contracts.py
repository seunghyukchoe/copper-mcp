"""Typed placement intent, proposals, and immutable placement candidates.

The intent language is deliberately **unable to express an illegal result**. There is no rule
that places a footprint at an absolute coordinate, and none that says two objects may overlap.
Every rule names objects by the references a scene already handed out, and every parameter is
an exact integer. Legality - overlap, containment, keepout respect - is not vocabulary here at
all; it belongs to the legalizer, which is the only thing allowed to decide it.

Proposals are ref-anchored for the same reason. A model says "put this 2.5mm to the right of
that", never "put this at (48200000, 17000000)", so the absolute coordinates in a candidate are
always derived by this code from geometry it read itself.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile
from copper_mcp.apply_token_reasons import (
    APPLY_TOKEN_WITHHELD_REASONS,
    ApplyTokenWithheldReason,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.models import DrcSummary
from copper_mcp.request_boundary import (
    CONSTRAINT_FIELDS,
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    boolean,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)

PLACEMENT_VERSION = "0.1.0"
PLACEMENT_PREVIEW_VERSION = "0.2.0"
#: How proposals are resolved and ordered. Recorded on every candidate so a later solver
#: cannot be mistaken for this one.
ORDERING_POLICY = "validate-snap-v1"
#: Version of the solve response envelope. Candidates inside carry
#: ``PLACEMENT_PREVIEW_VERSION`` because they are the same preview-shaped identity.
PLACEMENT_SOLVE_VERSION = "0.1.0"

#: Caller-settable solver ceilings on the public surface. Every one sits below the core
#: maxima in ``placement/solver.py``: the public gate is the narrow one, and raising any
#: ceiling is a new decision with its own measurement rather than a tuning constant.
SOLVER_MAX_EVALUATIONS = 1_024
SOLVER_MAX_ROUNDS = 16
SOLVER_MAX_BEAM_WIDTH = 32
SOLVER_MAX_RANKED = 16
SOLVER_MAX_STEP_NM = 100_000_000

_SOLVE_REQUIRED_FIELDS = ("board", "constraints", "subjects")
_SOLVE_OPTIONAL_FIELDS = (
    "rules",
    "proposals",
    "placement_grid_nm",
    "expect_board_revision",
    "expect_snapshot_digest",
    "solver",
)
_SOLVER_FIELDS = (
    "max_evaluations",
    "max_rounds",
    "beam_width",
    "max_ranked",
    "step_nm",
    "scoring_policy",
)
EMPTY_DIGEST = f"sha256:{'0' * 64}"
_SHA256_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

MAX_REF_CHARACTERS = 200
MAX_DIMENSION_NM = 1_000_000_000
QUARTER_UDEG = 90_000_000
ORIENTATIONS = (0, 90_000_000, 180_000_000, 270_000_000)
SIDES = ("front", "back")
AXES = ("x", "y")
EDGES = ("north", "south", "east", "west")
ANCHOR_POINTS = ("center", "north", "south", "east", "west")


class PlacementError(ValueError):
    """Raised when an untrusted placement request violates its declared contract."""


class PlacementPreviewError(PlacementError):
    """Raised when an opt-in placement preview operation cannot be honoured safely."""


class PlacementFailureCode(StrEnum):
    """Why a placement preview produced no candidate.

    ``infeasible_constraints`` and ``budget_exhausted`` are kept strictly apart. The first is a
    proof that no placement satisfies the rules as written; the second is an admission that the
    work ran out before an answer was reached. Collapsing them would report ignorance as
    certainty, which is the one thing a caller cannot recover from.
    """

    INVALID_REQUEST = "invalid_request"
    UNRESOLVED_REF = "unresolved_ref"
    INFEASIBLE_CONSTRAINTS = "infeasible_constraints"
    BUDGET_EXHAUSTED = "budget_exhausted"
    UNSUPPORTED_GEOMETRY = "unsupported_geometry"
    ILLEGAL_PLACEMENT = "illegal_placement"
    UNSUPPORTED_BOARD = "unsupported_board"
    STALE_REVISION = "stale_revision"


# --- rules ------------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Rule:
    """Common tolerance handling.

    ``tolerance_nm`` is ``None`` unless the caller supplied one. A rule is never reported as
    satisfied-within-tolerance by default: an unstated tolerance means exact, so a residual of
    one nanometre is a violation and says so.
    """

    tolerance_nm: int | None = None

    def _check_tolerance(self) -> None:
        if self.tolerance_nm is not None and self.tolerance_nm < 0:
            raise PlacementError("a tolerance must not be negative")


@dataclass(frozen=True, slots=True)
class ProximityRule(_Rule):
    kind: str = "proximity"
    subject: str = ""
    target: str = ""
    max_distance_nm: int = 0

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.max_distance_nm < 0:
            raise PlacementError("a proximity distance must not be negative")


@dataclass(frozen=True, slots=True)
class AlignmentRule(_Rule):
    kind: str = "alignment"
    axis: str = "x"
    members: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.axis not in AXES:
            raise PlacementError("an alignment axis must be x or y")
        if len(self.members) < 2:
            raise PlacementError("an alignment needs at least two members")
        if len(set(self.members)) != len(self.members):
            raise PlacementError("alignment members must be distinct")


@dataclass(frozen=True, slots=True)
class SymmetryRule(_Rule):
    kind: str = "symmetry"
    axis: str = "x"
    about: str = ""
    pairs: tuple[tuple[str, str], ...] = ()

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.axis not in AXES:
            raise PlacementError("a symmetry axis must be x or y")
        if not self.pairs:
            raise PlacementError("a symmetry needs at least one mirrored pair")
        for left, right in self.pairs:
            if left == right:
                raise PlacementError("a symmetry pair must name two different objects")


@dataclass(frozen=True, slots=True)
class EdgeRule(_Rule):
    kind: str = "edge"
    subject: str = ""
    edge: str = "north"
    offset_nm: int = 0

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.edge not in EDGES:
            raise PlacementError("a board edge must be north, south, east or west")
        if self.offset_nm < 0:
            raise PlacementError("an edge offset must not be negative")


@dataclass(frozen=True, slots=True)
class RegionRule(_Rule):
    kind: str = "region"
    subject: str = ""
    mode: str = "keep_in"
    boundary_ref: str = ""

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.mode not in {"keep_in", "keep_out"}:
            raise PlacementError("a region mode must be keep_in or keep_out")


@dataclass(frozen=True, slots=True)
class OrientationRule(_Rule):
    kind: str = "orientation"
    subject: str = ""
    allowed: tuple[int, ...] = ()

    def __post_init__(self) -> None:
        self._check_tolerance()
        if not self.allowed:
            raise PlacementError("an orientation rule must allow at least one orientation")
        if any(value not in ORIENTATIONS for value in self.allowed):
            raise PlacementError("placement supports orthogonal orientations only")


@dataclass(frozen=True, slots=True)
class SideRule(_Rule):
    kind: str = "side"
    subject: str = ""
    side: str = "front"

    def __post_init__(self) -> None:
        self._check_tolerance()
        if self.side not in SIDES:
            raise PlacementError("a side must be front or back")


PlacementRule = (
    ProximityRule
    | AlignmentRule
    | SymmetryRule
    | EdgeRule
    | RegionRule
    | OrientationRule
    | SideRule
)


@dataclass(frozen=True, slots=True)
class PlacementProposal:
    """One ref-anchored move. Absolute coordinates are derived, never supplied."""

    subject: str
    anchor: str | None = None
    anchor_point: str = "center"
    offset_x_nm: int = 0
    offset_y_nm: int = 0
    orientation_udeg: int | None = None
    side: str | None = None

    def __post_init__(self) -> None:
        if self.anchor_point not in ANCHOR_POINTS:
            raise PlacementError("an anchor point is malformed")
        if self.orientation_udeg is not None and self.orientation_udeg not in ORIENTATIONS:
            raise PlacementError("placement supports orthogonal orientations only")
        if self.side is not None and self.side not in SIDES:
            raise PlacementError("a side must be front or back")
        for value in (self.offset_x_nm, self.offset_y_nm):
            if abs(value) > MAX_DIMENSION_NM:
                raise PlacementError("a placement offset is out of range")


@dataclass(frozen=True, slots=True)
class PlacementIntent:
    """One validated, immutable placement request."""

    board: str
    constraints: NetClass
    subject_refs: tuple[str, ...]
    rules: tuple[PlacementRule, ...]
    proposals: tuple[PlacementProposal, ...]
    placement_grid_nm: int = 1_000
    expect_board_revision: str | None = None
    expect_snapshot_digest: str | None = None
    #: Explicit capability request. A token is issued only by the file-backed preview when the
    #: operator has enabled apply and the pure placement replay accepts the candidate.
    include_apply_token: bool = False
    #: Request private, disposable KiCad DRC evidence for a file-backed candidate.
    include_drc: bool = False

    def profile(self) -> KiCadConstraintProfile:
        """The constraint profile this intent's board must be converted under."""

        return KiCadConstraintProfile(
            net_classes=(self.constraints,), default_net_class_id=self.constraints.id
        )

    def to_dict(self) -> dict[str, Any]:
        """Echo the validated request so a result is self-describing once detached."""

        return {
            "board": self.board,
            "subjects": list(self.subject_refs),
            "rule_count": len(self.rules),
            "proposal_count": len(self.proposals),
            "placement_grid_nm": self.placement_grid_nm,
            "constraints": {
                field_name: getattr(self.constraints, field_name)
                for field_name in CONSTRAINT_FIELDS
            },
            "expect_board_revision": self.expect_board_revision,
            "expect_snapshot_digest": self.expect_snapshot_digest,
            "include_apply_token": self.include_apply_token,
            "include_drc": self.include_drc,
        }

    def __post_init__(self) -> None:
        if not self.subject_refs:
            raise PlacementError("a placement intent must name at least one subject")
        if len(set(self.subject_refs)) != len(self.subject_refs):
            raise PlacementError("placement subjects must be distinct")
        if self.placement_grid_nm < 1:
            raise PlacementError("a placement grid must be positive")
        if type(self.include_apply_token) is not bool:
            raise PlacementError("include_apply_token must be boolean")
        if type(self.include_drc) is not bool:
            raise PlacementError("include_drc must be boolean")
        for name, revision in (
            ("expect_board_revision", self.expect_board_revision),
            ("expect_snapshot_digest", self.expect_snapshot_digest),
        ):
            if revision is not None and _SHA256_DIGEST.fullmatch(revision) is None:
                raise PlacementError(f"{name} must be content-addressed with sha256")
        moved = [proposal.subject for proposal in self.proposals]
        if len(set(moved)) != len(moved):
            raise PlacementError("a subject may be proposed at most once")
        outside = set(moved) - set(self.subject_refs)
        if outside:
            # A proposal that moves something the intent never declared as a subject would let
            # a caller edit the board outside the scope it asked for.
            raise PlacementError(f"{len(outside)} proposal(s) move objects that are not subjects")


# --- request parsing --------------------------------------------------------------------

_REQUIRED_FIELDS = ("board", "constraints", "subjects")
_OPTIONAL_FIELDS = (
    "rules",
    "proposals",
    "placement_grid_nm",
    "expect_board_revision",
    "expect_snapshot_digest",
    "include_apply_token",
    "include_drc",
)


def _ref(name: str, value: Any) -> str:
    return text(name, value, maximum=MAX_REF_CHARACTERS)


def _nanometres(name: str, value: Any, *, minimum: int = -MAX_DIMENSION_NM) -> int:
    return integer(name, value, minimum=minimum, maximum=MAX_DIMENSION_NM)


def _tolerance(fields: Mapping[str, Any]) -> int | None:
    if "tolerance_nm" not in fields:
        return None
    return integer("tolerance_nm", fields["tolerance_nm"], minimum=0, maximum=MAX_DIMENSION_NM)


def _sequence(name: str, value: Any, *, maximum: int) -> list[Any]:
    if not isinstance(value, list | tuple):
        raise PlacementError(f"{name} must be a list")
    if len(value) > maximum:
        raise PlacementError(f"{name} has too many entries")
    return list(value)


def _parse_rule(index: int, payload: Any) -> PlacementRule:
    fields = mapping(f"rules[{index}]", payload)
    kind = text("rule kind", fields.get("kind"), maximum=32)
    tolerance = _tolerance(fields)
    if kind == "proximity":
        known_fields(
            "proximity rule",
            fields,
            frozenset({"kind", "subject", "target", "max_distance_nm", "tolerance_nm"}),
        )
        required_fields("proximity rule", fields, ("subject", "target", "max_distance_nm"))
        return ProximityRule(
            subject=_ref("subject", fields["subject"]),
            target=_ref("target", fields["target"]),
            max_distance_nm=_nanometres("max_distance_nm", fields["max_distance_nm"], minimum=0),
            tolerance_nm=tolerance,
        )
    if kind == "alignment":
        known_fields(
            "alignment rule", fields, frozenset({"kind", "axis", "members", "tolerance_nm"})
        )
        required_fields("alignment rule", fields, ("axis", "members"))
        members = _sequence("members", fields["members"], maximum=64)
        return AlignmentRule(
            axis=text("axis", fields["axis"], maximum=1),
            members=tuple(_ref(f"members[{i}]", item) for i, item in enumerate(members)),
            tolerance_nm=tolerance,
        )
    if kind == "symmetry":
        known_fields(
            "symmetry rule",
            fields,
            frozenset({"kind", "axis", "about", "pairs", "tolerance_nm"}),
        )
        required_fields("symmetry rule", fields, ("axis", "about", "pairs"))
        raw_pairs = _sequence("pairs", fields["pairs"], maximum=64)
        pairs: list[tuple[str, str]] = []
        for pair_index, entry in enumerate(raw_pairs):
            values = _sequence(f"pairs[{pair_index}]", entry, maximum=2)
            if len(values) != 2:
                raise PlacementError("a symmetry pair must name exactly two objects")
            pairs.append(
                (
                    _ref(f"pairs[{pair_index}][0]", values[0]),
                    _ref(f"pairs[{pair_index}][1]", values[1]),
                )
            )
        return SymmetryRule(
            axis=text("axis", fields["axis"], maximum=1),
            about=_ref("about", fields["about"]),
            pairs=tuple(pairs),
            tolerance_nm=tolerance,
        )
    if kind == "edge":
        known_fields(
            "edge rule",
            fields,
            frozenset({"kind", "subject", "edge", "offset_nm", "tolerance_nm"}),
        )
        required_fields("edge rule", fields, ("subject", "edge", "offset_nm"))
        return EdgeRule(
            subject=_ref("subject", fields["subject"]),
            edge=text("edge", fields["edge"], maximum=8),
            offset_nm=_nanometres("offset_nm", fields["offset_nm"], minimum=0),
            tolerance_nm=tolerance,
        )
    if kind == "region":
        known_fields(
            "region rule",
            fields,
            frozenset({"kind", "subject", "mode", "boundary_ref", "tolerance_nm"}),
        )
        required_fields("region rule", fields, ("subject", "mode", "boundary_ref"))
        return RegionRule(
            subject=_ref("subject", fields["subject"]),
            mode=text("mode", fields["mode"], maximum=16),
            boundary_ref=_ref("boundary_ref", fields["boundary_ref"]),
            tolerance_nm=tolerance,
        )
    if kind == "orientation":
        known_fields(
            "orientation rule", fields, frozenset({"kind", "subject", "allowed", "tolerance_nm"})
        )
        required_fields("orientation rule", fields, ("subject", "allowed"))
        allowed = _sequence("allowed", fields["allowed"], maximum=4)
        return OrientationRule(
            subject=_ref("subject", fields["subject"]),
            allowed=tuple(
                integer(f"allowed[{i}]", item, minimum=0, maximum=270_000_000)
                for i, item in enumerate(allowed)
            ),
            tolerance_nm=tolerance,
        )
    if kind == "side":
        known_fields("side rule", fields, frozenset({"kind", "subject", "side", "tolerance_nm"}))
        required_fields("side rule", fields, ("subject", "side"))
        return SideRule(
            subject=_ref("subject", fields["subject"]),
            side=text("side", fields["side"], maximum=8),
            tolerance_nm=tolerance,
        )
    raise PlacementError("rule kind is not one of the seven supported kinds")


def _parse_proposal(index: int, payload: Any) -> PlacementProposal:
    fields = mapping(f"proposals[{index}]", payload)
    known_fields(
        "proposal",
        fields,
        frozenset(
            {
                "subject",
                "anchor",
                "anchor_point",
                "offset_x_nm",
                "offset_y_nm",
                "orientation_udeg",
                "side",
            }
        ),
    )
    required_fields("proposal", fields, ("subject",))
    return PlacementProposal(
        subject=_ref("subject", fields["subject"]),
        anchor=(_ref("anchor", fields["anchor"]) if fields.get("anchor") is not None else None),
        anchor_point=text("anchor_point", fields.get("anchor_point", "center"), maximum=8),
        offset_x_nm=_nanometres("offset_x_nm", fields.get("offset_x_nm", 0)),
        offset_y_nm=_nanometres("offset_y_nm", fields.get("offset_y_nm", 0)),
        orientation_udeg=(
            integer("orientation_udeg", fields["orientation_udeg"], minimum=0, maximum=270_000_000)
            if fields.get("orientation_udeg") is not None
            else None
        ),
        side=(text("side", fields["side"], maximum=8) if fields.get("side") is not None else None),
    )


def parse_placement_intent(
    payload: Any,
    *,
    max_subjects: int = 64,
    max_rules: int = 256,
    allow_live: bool = False,
    require_revisions: bool = False,
) -> PlacementIntent:
    """Validate one untrusted placement request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        subjects = _sequence("subjects", fields["subjects"], maximum=max_subjects)
        rules = _sequence("rules", fields.get("rules", []), maximum=max_rules)
        proposals = _sequence("proposals", fields.get("proposals", []), maximum=max_subjects)
        board_value = fields["board"]
        if allow_live and board_value == "live":
            board = "live"
        else:
            board = board_path(board_value)
        expected_board_revision = fields.get("expect_board_revision")
        expected_snapshot_digest = fields.get("expect_snapshot_digest")
        include_apply_token = boolean(
            "include_apply_token", fields.get("include_apply_token", False)
        )
        include_drc = boolean("include_drc", fields.get("include_drc", False))
        if allow_live and include_apply_token:
            raise PlacementError("live placement proposals cannot request apply authority")
        if allow_live and include_drc:
            raise PlacementError("live placement proposals cannot request authoritative DRC")
        if require_revisions and (
            expected_board_revision is None or expected_snapshot_digest is None
        ):
            raise PlacementError(
                "live placement requires board and snapshot revision preconditions"
            )
        if expected_board_revision is not None:
            expected_board_revision = text(
                "expect_board_revision", expected_board_revision, maximum=71
            )
        if expected_snapshot_digest is not None:
            expected_snapshot_digest = text(
                "expect_snapshot_digest", expected_snapshot_digest, maximum=71
            )
        return PlacementIntent(
            board=board,
            constraints=net_class_constraints(fields["constraints"]),
            subject_refs=tuple(_ref(f"subjects[{i}]", item) for i, item in enumerate(subjects)),
            rules=tuple(_parse_rule(i, item) for i, item in enumerate(rules)),
            proposals=tuple(_parse_proposal(i, item) for i, item in enumerate(proposals)),
            placement_grid_nm=integer(
                "placement_grid_nm",
                fields.get("placement_grid_nm", 1_000),
                minimum=1,
                maximum=MAX_DIMENSION_NM,
            ),
            expect_board_revision=expected_board_revision,
            expect_snapshot_digest=expected_snapshot_digest,
            include_apply_token=include_apply_token,
            include_drc=include_drc,
        )
    except PlacementError:
        raise
    except RequestError as error:
        raise PlacementError(str(error)) from error


# --- candidates -------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class FootprintPlacement:
    """One footprint's proposed pose. Immutable and fully derived."""

    ref_id: str
    origin_x_nm: int
    origin_y_nm: int
    orientation_udeg: int
    side: str
    moved: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "ref_id": self.ref_id,
            "origin_nm": [self.origin_x_nm, self.origin_y_nm],
            "orientation_udeg": self.orientation_udeg,
            "side": self.side,
            "moved": self.moved,
        }


@dataclass(frozen=True, slots=True)
class RuleResult:
    """What one rule concluded, and by how much."""

    rule_index: int
    kind: str
    status: str
    residual_nm: int

    def __post_init__(self) -> None:
        if self.status not in {
            "satisfied_exactly",
            "satisfied_within_tolerance",
            "violated",
        }:
            raise PlacementError("a rule result status is malformed")

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_index": self.rule_index,
            "kind": self.kind,
            "status": self.status,
            "residual_nm": self.residual_nm,
        }


@dataclass(frozen=True, slots=True)
class PlacementLegality:
    """Deterministic legality, with the limits of each check stated in its own vocabulary."""

    pad_overlap: str
    outline_containment: str
    keepout_respect: str
    #: Three-valued, and evaluated only between footprints on the same physical side. A footprint's
    #: rings are one even-odd region, so a ring nested inside another is a hole rather than a second
    #: solid, and each region is contracted by KiCad 10.0.5's cached-courtyard inset before the
    #: collision test. ``violated`` is exact parity with that model, ``proven_clear`` is a proof
    #: that no contracted region can touch, and ``inconclusive`` is the band where raw geometry and
    #: KiCad's contracted cache disagree — claimed as neither rather than as a confident answer.
    #: Unsupported topology is rejected by the Board IR contract before a placement view exists.
    courtyard_overlap: str = "proven_clear"

    def __post_init__(self) -> None:
        if self.pad_overlap not in {"proven_clear", "inconclusive", "violated"}:
            raise PlacementError("pad overlap must be three-valued")
        if self.outline_containment not in {"proven_inside", "inconclusive", "violated"}:
            raise PlacementError("outline containment must be three-valued")
        if self.keepout_respect not in {"proven_clear", "inconclusive", "violated"}:
            raise PlacementError("keepout respect must be three-valued")
        if self.courtyard_overlap not in {"proven_clear", "inconclusive", "violated"}:
            raise PlacementError("courtyard overlap must be three-valued")

    @property
    def legal(self) -> bool:
        """Whether nothing was *proven* illegal. Inconclusive is not a violation."""

        return (
            self.pad_overlap != "violated"
            and self.outline_containment != "violated"
            and self.keepout_respect != "violated"
            and self.courtyard_overlap != "violated"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "pad_overlap": self.pad_overlap,
            "outline_containment": self.outline_containment,
            "keepout_respect": self.keepout_respect,
            "courtyard_overlap": self.courtyard_overlap,
        }


@dataclass(frozen=True, slots=True)
class PlacementEvidence:
    """Everything a caller needs to check the verdict rather than trust it."""

    rule_results: tuple[RuleResult, ...]
    legality: PlacementLegality
    checks_used: int
    inconclusive_pairs: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_results": [item.to_dict() for item in self.rule_results],
            "legality": self.legality.to_dict(),
            "checks_used": self.checks_used,
            "inconclusive_pairs": self.inconclusive_pairs,
        }


@dataclass(frozen=True, slots=True)
class PlacementCandidate:
    """One immutable proposed placement, bound to the board it was derived from."""

    candidate_id: str
    base_revision: str
    view_revision: str
    placements: tuple[FootprintPlacement, ...]
    evidence: PlacementEvidence
    placement_grid_nm: int
    ordering_policy: str = ORDERING_POLICY
    placement_version: str = PLACEMENT_VERSION

    def __post_init__(self) -> None:
        if self.ordering_policy != ORDERING_POLICY:
            raise PlacementError("placement candidates record exactly one ordering policy")
        if not self.placements:
            raise PlacementError("a placement candidate must place at least one footprint")
        refs = [item.ref_id for item in self.placements]
        if refs != sorted(refs):
            raise PlacementError("placements must be recorded in reference order")

    def to_dict(self) -> dict[str, Any]:
        return {
            "candidate_id": self.candidate_id,
            "base_revision": self.base_revision,
            "view_revision": self.view_revision,
            "placement_version": self.placement_version,
            "ordering_policy": self.ordering_policy,
            "placement_grid_nm": self.placement_grid_nm,
            "placements": [item.to_dict() for item in self.placements],
            "evidence": self.evidence.to_dict(),
        }


def _candidate_payload(candidate: PlacementCandidate) -> dict[str, Any]:
    document = candidate.to_dict()
    # The identity bytes omit the circular id field and nothing else.
    document.pop("candidate_id")
    return document


def canonical_candidate_bytes(candidate: PlacementCandidate) -> bytes:
    rendered = json.dumps(
        _candidate_payload(candidate),
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def verify_placement_id(candidate: PlacementCandidate) -> bool:
    """Raise when a candidate ID does not match its canonical content."""

    expected = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    if candidate.candidate_id != expected:
        raise PlacementError("candidate ID does not match canonical placement content")
    return True


def finalise_candidate(candidate: PlacementCandidate) -> PlacementCandidate:
    """Stamp a candidate with the digest of its own canonical content."""

    digest = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    stamped = replace(candidate, candidate_id=digest)
    verify_placement_id(stamped)
    return stamped


@dataclass(frozen=True, slots=True)
class PlacementDiagnostic:
    """One typed, non-echoing refusal.

    An illegal placement carries the legality record that condemned it. A refusal that only
    said "illegal" would force a caller to guess which of three independent checks failed, and
    guessing is what this contract exists to remove.
    """

    code: PlacementFailureCode
    message: str
    checks_used: int = 0
    legality: PlacementLegality | None = None
    rule_results: tuple[RuleResult, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": str(self.code),
            "message": self.message,
            "checks_used": self.checks_used,
            "legality": None if self.legality is None else self.legality.to_dict(),
            "rule_results": [item.to_dict() for item in self.rule_results],
        }


@dataclass(frozen=True, slots=True)
class PlacementCandidateDrcEvidence:
    """Redacted, candidate-bound KiCad DRC evidence for a disposable placement board."""

    candidate_id: str
    candidate_base_revision: str
    source_revision: str
    patched_board_revision: str
    patched_drc_context_revision: str
    summary: DrcSummary

    def __post_init__(self) -> None:
        for name in (
            "candidate_id",
            "candidate_base_revision",
            "source_revision",
            "patched_board_revision",
            "patched_drc_context_revision",
        ):
            value = getattr(self, name)
            if not isinstance(value, str) or not _SHA256_DIGEST.fullmatch(value):
                raise ValueError(f"{name} must be content-addressed with sha256")
        if not isinstance(self.summary, DrcSummary):
            raise ValueError("summary must be strict KiCad DRC evidence")
        if self.summary.base_revision != self.patched_board_revision:
            raise ValueError("DRC summary is not bound to the patched board revision")
        if self.summary.drc_context_revision != self.patched_drc_context_revision:
            raise ValueError("DRC summary is not bound to the patched context revision")

    def to_dict(self) -> dict[str, Any]:
        """Return digest bindings and aggregate findings without board-private details."""

        return {
            "candidate_id": self.candidate_id,
            "candidate_base_revision": self.candidate_base_revision,
            "source_revision": self.source_revision,
            "patched_board_revision": self.patched_board_revision,
            "patched_drc_context_revision": self.patched_drc_context_revision,
            "summary": self.summary.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class PlacementResult:
    """Either a candidate or a typed refusal - never both, never neither."""

    status: str
    board_revision: str
    board_path: str = ""
    request: PlacementIntent | None = None
    snapshot_digest: str | None = None
    candidate: PlacementCandidate | None = None
    diagnostic: PlacementDiagnostic | None = None
    apply_token: str | None = None
    #: Why no capability accompanies this result, from the closed set in
    #: :mod:`copper_mcp.apply_token_reasons`. Exactly one of this and ``apply_token`` is set on
    #: every result a caller can see; ``to_dict`` is where that is enforced, because the legalizer
    #: builds intermediate results before the surface knows what the operator permits.
    apply_token_withheld_reason: ApplyTokenWithheldReason | None = None
    drc_evidence: PlacementCandidateDrcEvidence | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(self.conversion_diagnostic_counts)),
        )
        if self.status not in {"previewed", "refused", "unsupported_board"}:
            raise PlacementError("a placement status is malformed")
        if (self.candidate is None) == (self.diagnostic is None):
            raise PlacementError("a placement result carries exactly one of candidate or refusal")
        if self.apply_token is not None:
            if not isinstance(self.apply_token, str) or not 1 <= len(self.apply_token) <= 512:
                raise PlacementError("placement apply token is malformed")
            if self.status != "previewed" or self.candidate is None:
                raise PlacementError("placement apply authority requires a candidate")
            if self.request is None or not self.request.include_apply_token:
                raise PlacementError("placement apply authority was not requested")
            if self.apply_token_withheld_reason is not None:
                raise PlacementError("an issued placement apply token cannot also be withheld")
        elif (
            self.apply_token_withheld_reason is not None
            and self.apply_token_withheld_reason not in APPLY_TOKEN_WITHHELD_REASONS
        ):
            raise PlacementError("a withheld placement apply token names an unlisted reason")
        if self.drc_evidence is not None:
            if self.status != "previewed" or self.candidate is None:
                raise PlacementError("placement DRC evidence requires a candidate")
            if self.request is None or not self.request.include_drc:
                raise PlacementError("placement DRC evidence was not requested")
            if (
                self.drc_evidence.candidate_id != self.candidate.candidate_id
                or self.drc_evidence.candidate_base_revision != self.candidate.base_revision
                or self.drc_evidence.source_revision != self.board_revision
            ):
                raise PlacementError("placement DRC evidence is not bound to this candidate")

    def to_dict(self) -> dict[str, Any]:
        if self.apply_token is None and self.apply_token_withheld_reason is None:
            # No default, and no silence: a result reaching a caller without a token states
            # which closed reason withheld it, or it does not reach the caller at all (R-149).
            raise PlacementError("a withheld placement apply token must name a closed reason")
        return {
            "status": self.status,
            "placement_version": PLACEMENT_PREVIEW_VERSION,
            "board_path": self.board_path,
            "request": None if self.request is None else self.request.to_dict(),
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "candidate": None if self.candidate is None else self.candidate.to_dict(),
            "diagnostic": None if self.diagnostic is None else self.diagnostic.to_dict(),
            "apply_token": self.apply_token,
            "apply_token_withheld_reason": self.apply_token_withheld_reason,
            "drc_evidence": None if self.drc_evidence is None else self.drc_evidence.to_dict(),
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


# --- bounded solve ------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PlacementSolveRequest:
    """A validated solve request: one placement intent plus caller work budgets.

    ``include_apply_token`` and ``include_drc`` are not fields of this surface: the closed
    contract drops them, so smuggling either in refuses as an unknown field before anything
    is read. Wall-clock deadlines are not caller-settable; the service derives them from the
    operation budget, because a caller-set CPU deadline is not a reproducible bound.
    """

    intent: PlacementIntent
    max_evaluations: int = 64
    max_rounds: int = 4
    beam_width: int = 4
    max_ranked: int = 4
    step_nm: int = 1_000_000
    scoring_policy: str = "same-net-manhattan-v1"

    def solver_settings_dict(self) -> dict[str, Any]:
        """Caller-visible budgets for the response echo (deadlines stay server-side)."""

        return {
            "max_evaluations": self.max_evaluations,
            "max_rounds": self.max_rounds,
            "beam_width": self.beam_width,
            "max_ranked": self.max_ranked,
            "step_nm": self.step_nm,
            "scoring_policy": self.scoring_policy,
        }


def parse_placement_solve_request(
    payload: Any,
    *,
    max_subjects: int = 64,
    max_rules: int = 256,
) -> PlacementSolveRequest:
    """Validate one untrusted solve request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_SOLVE_REQUIRED_FIELDS + _SOLVE_OPTIONAL_FIELDS))
        required_fields("request", fields, _SOLVE_REQUIRED_FIELDS)
        subjects = _sequence("subjects", fields["subjects"], maximum=max_subjects)
        rules = _sequence("rules", fields.get("rules", []), maximum=max_rules)
        proposals = _sequence("proposals", fields.get("proposals", []), maximum=max_subjects)
        board = board_path(fields["board"])
        solver_fields = mapping("solver", fields.get("solver", {}))
        known_fields("solver", solver_fields, frozenset(_SOLVER_FIELDS))
        return PlacementSolveRequest(
            intent=PlacementIntent(
                board=board,
                constraints=net_class_constraints(fields["constraints"]),
                subject_refs=tuple(_ref(f"subjects[{i}]", item) for i, item in enumerate(subjects)),
                rules=tuple(_parse_rule(i, item) for i, item in enumerate(rules)),
                proposals=tuple(_parse_proposal(i, item) for i, item in enumerate(proposals)),
                placement_grid_nm=integer(
                    "placement_grid_nm",
                    fields.get("placement_grid_nm", 1_000),
                    minimum=1,
                    maximum=MAX_DIMENSION_NM,
                ),
                expect_board_revision=_optional_revision(fields, "expect_board_revision"),
                expect_snapshot_digest=_optional_revision(fields, "expect_snapshot_digest"),
            ),
            max_evaluations=integer(
                "max_evaluations",
                solver_fields.get("max_evaluations", 64),
                minimum=1,
                maximum=SOLVER_MAX_EVALUATIONS,
            ),
            max_rounds=integer(
                "max_rounds",
                solver_fields.get("max_rounds", 4),
                minimum=0,
                maximum=SOLVER_MAX_ROUNDS,
            ),
            beam_width=integer(
                "beam_width",
                solver_fields.get("beam_width", 4),
                minimum=1,
                maximum=SOLVER_MAX_BEAM_WIDTH,
            ),
            max_ranked=integer(
                "max_ranked",
                solver_fields.get("max_ranked", 4),
                minimum=1,
                maximum=SOLVER_MAX_RANKED,
            ),
            step_nm=integer(
                "step_nm",
                solver_fields.get("step_nm", 1_000_000),
                minimum=1,
                maximum=SOLVER_MAX_STEP_NM,
            ),
            scoring_policy=text(
                "scoring_policy",
                solver_fields.get("scoring_policy", "same-net-manhattan-v1"),
                maximum=64,
            ),
        )
    except PlacementError:
        raise
    except RequestError as error:
        raise PlacementError(str(error)) from error


def _optional_revision(fields: dict[str, Any], name: str) -> str | None:
    value = fields.get(name)
    if value is None:
        return None
    return text(name, value, maximum=71)


@dataclass(frozen=True, slots=True)
class PlacementSolveResponse:
    """Either ranked candidates or a typed refusal - never both, never neither.

    Candidates are the same preview-shaped identity a preview mints for the same pose, so a
    solved pose can be re-previewed and applied through the ordinary placement path. The
    surface mints no apply authority under any setting: every response reaching a caller
    carries ``apply_token`` null with the closed ``unsupported_surface`` reason, and every
    candidate inside it is preview-grade until re-previewed. Solver accounting (evaluations,
    route-probe use against its limit, ranked count, policy) is reported even on refusal,
    because spent work is a fact about the run rather than a claim about the board.
    """

    status: str
    board_revision: str
    board_path: str = ""
    request: PlacementSolveRequest | None = None
    solver: dict[str, Any] = field(default_factory=dict)
    snapshot_digest: str | None = None
    candidates: tuple[PlacementCandidate, ...] = ()
    diagnostic: PlacementDiagnostic | None = None
    evaluations: int = 0
    route_probes_used: int = 0
    route_probe_limit: int = 0
    scoring_policy: str = "same-net-manhattan-v1"
    apply_token: str | None = None
    apply_token_withheld_reason: ApplyTokenWithheldReason | None = None
    conversion_diagnostic_counts: Mapping[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        object.__setattr__(
            self,
            "conversion_diagnostic_counts",
            MappingProxyType(dict(self.conversion_diagnostic_counts)),
        )
        object.__setattr__(self, "solver", dict(self.solver))
        if self.status not in {"solved", "refused", "unsupported_board"}:
            raise PlacementError("a placement solve status is malformed")
        if (not self.candidates) == (self.diagnostic is None):
            raise PlacementError("a placement solve carries exactly one of candidates or refusal")
        if self.status == "solved" and (not self.candidates or self.diagnostic is not None):
            raise PlacementError("a solved placement solve carries candidates and no refusal")
        if self.status != "solved" and (self.candidates or self.diagnostic is None):
            raise PlacementError("an unsolved placement solve carries a refusal and no candidates")
        # Closed-vocabulary literal, not a credential: the S105 finding is the attribute
        # name containing "token", and the precedent is the same noqa beside the apply
        # failure-code literals.
        if (
            self.apply_token is not None
            or self.apply_token_withheld_reason != "unsupported_surface"  # noqa: S105
        ):
            raise PlacementError("a placement solve never mints apply authority")
        if self.evaluations < 0:
            raise PlacementError("solver evaluations must not be negative")
        if self.route_probes_used < 0 or self.route_probe_limit < 0:
            raise PlacementError("solver route probe accounting must not be negative")
        if self.route_probes_used > self.route_probe_limit:
            raise PlacementError("solver route probe use exceeds its limit")

    def to_dict(self) -> dict[str, Any]:
        request = None
        if self.request is not None:
            intent = self.request.intent.to_dict()
            request = {
                "board": intent["board"],
                "subjects": intent["subjects"],
                "rule_count": intent["rule_count"],
                "proposal_count": intent["proposal_count"],
                "placement_grid_nm": intent["placement_grid_nm"],
                "constraints": intent["constraints"],
                "expect_board_revision": intent["expect_board_revision"],
                "expect_snapshot_digest": intent["expect_snapshot_digest"],
                "solver": dict(self.solver),
            }
        return {
            "status": self.status,
            "placement_solve_version": PLACEMENT_SOLVE_VERSION,
            "board_path": self.board_path,
            "request": request,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "candidates": [candidate.to_dict() for candidate in self.candidates],
            "diagnostic": None if self.diagnostic is None else self.diagnostic.to_dict(),
            "evaluations": self.evaluations,
            "route_probes_used": self.route_probes_used,
            "route_probe_limit": self.route_probe_limit,
            "scoring_policy": self.scoring_policy,
            "apply_token": self.apply_token,
            "apply_token_withheld_reason": self.apply_token_withheld_reason,
            "conversion_diagnostic_counts": dict(self.conversion_diagnostic_counts),
        }


__all__ = [
    "ANCHOR_POINTS",
    "AXES",
    "EDGES",
    "EMPTY_DIGEST",
    "MAX_JSON_SAFE_INTEGER",
    "ORDERING_POLICY",
    "ORIENTATIONS",
    "PLACEMENT_PREVIEW_VERSION",
    "PLACEMENT_SOLVE_VERSION",
    "PLACEMENT_VERSION",
    "SIDES",
    "AlignmentRule",
    "EdgeRule",
    "FootprintPlacement",
    "OrientationRule",
    "PlacementCandidate",
    "PlacementCandidateDrcEvidence",
    "PlacementDiagnostic",
    "PlacementError",
    "PlacementEvidence",
    "PlacementFailureCode",
    "PlacementIntent",
    "PlacementLegality",
    "PlacementPreviewError",
    "PlacementProposal",
    "PlacementResult",
    "PlacementRule",
    "PlacementSolveRequest",
    "PlacementSolveResponse",
    "ProximityRule",
    "RegionRule",
    "RuleResult",
    "SideRule",
    "SymmetryRule",
    "canonical_candidate_bytes",
    "finalise_candidate",
    "parse_placement_intent",
    "parse_placement_solve_request",
    "verify_placement_id",
]
