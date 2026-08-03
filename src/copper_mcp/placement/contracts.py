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
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from enum import StrEnum
from types import MappingProxyType
from typing import Any

from copper_mcp.board_ir import NetClass
from copper_mcp.request_boundary import (
    MAX_JSON_SAFE_INTEGER,
    RequestError,
    board_path,
    integer,
    known_fields,
    mapping,
    net_class_constraints,
    required_fields,
    text,
)

PLACEMENT_VERSION = "0.1.0"
#: How proposals are resolved and ordered. Recorded on every candidate so a later solver
#: cannot be mistaken for this one.
ORDERING_POLICY = "validate-snap-v1"
EMPTY_DIGEST = f"sha256:{'0' * 64}"

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

    def __post_init__(self) -> None:
        if not self.subject_refs:
            raise PlacementError("a placement intent must name at least one subject")
        if len(set(self.subject_refs)) != len(self.subject_refs):
            raise PlacementError("placement subjects must be distinct")
        if self.placement_grid_nm < 1:
            raise PlacementError("a placement grid must be positive")
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
_OPTIONAL_FIELDS = ("rules", "proposals", "placement_grid_nm")


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
    payload: Any, *, max_subjects: int = 64, max_rules: int = 256
) -> PlacementIntent:
    """Validate one untrusted placement request without echoing unvalidated input."""

    try:
        fields = mapping("request", payload)
        known_fields("request", fields, frozenset(_REQUIRED_FIELDS + _OPTIONAL_FIELDS))
        required_fields("request", fields, _REQUIRED_FIELDS)
        subjects = _sequence("subjects", fields["subjects"], maximum=max_subjects)
        rules = _sequence("rules", fields.get("rules", []), maximum=max_rules)
        proposals = _sequence("proposals", fields.get("proposals", []), maximum=max_subjects)
        return PlacementIntent(
            board=board_path(fields["board"]),
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
    #: One permitted value. There is no vocabulary here for a courtyard that was checked, so a
    #: candidate can never imply a check this version does not perform. Board IR carries no
    #: courtyard geometry, and this repository's own board draws none at all.
    courtyard_overlap: str = "not_modelled"

    def __post_init__(self) -> None:
        if self.pad_overlap not in {"proven_clear", "inconclusive", "violated"}:
            raise PlacementError("pad overlap must be three-valued")
        if self.outline_containment not in {"proven_inside", "violated"}:
            raise PlacementError("outline containment is malformed")
        if self.keepout_respect not in {"proven_clear", "violated"}:
            raise PlacementError("keepout respect is malformed")
        if self.courtyard_overlap != "not_modelled":
            raise PlacementError("courtyard overlap has exactly one permitted value")

    @property
    def legal(self) -> bool:
        """Whether nothing was *proven* illegal. Inconclusive is not a violation."""

        return (
            self.pad_overlap != "violated"
            and self.outline_containment == "proven_inside"
            and self.keepout_respect == "proven_clear"
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
class PlacementResult:
    """Either a candidate or a typed refusal - never both, never neither."""

    status: str
    board_revision: str
    snapshot_digest: str | None = None
    candidate: PlacementCandidate | None = None
    diagnostic: PlacementDiagnostic | None = None
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "placement_version": PLACEMENT_VERSION,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "candidate": None if self.candidate is None else self.candidate.to_dict(),
            "diagnostic": None if self.diagnostic is None else self.diagnostic.to_dict(),
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
    "PLACEMENT_VERSION",
    "SIDES",
    "AlignmentRule",
    "EdgeRule",
    "FootprintPlacement",
    "OrientationRule",
    "PlacementCandidate",
    "PlacementDiagnostic",
    "PlacementError",
    "PlacementEvidence",
    "PlacementFailureCode",
    "PlacementIntent",
    "PlacementLegality",
    "PlacementProposal",
    "PlacementResult",
    "PlacementRule",
    "ProximityRule",
    "RegionRule",
    "RuleResult",
    "SideRule",
    "SymmetryRule",
    "canonical_candidate_bytes",
    "finalise_candidate",
    "parse_placement_intent",
    "verify_placement_id",
]
