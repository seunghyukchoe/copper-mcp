"""Stable, JSON-serializable domain models for the public protocol boundary.

This module owns the small set of shapes that cross the wire: board counts and manifests, DRC
summaries, and candidate metrics and summaries, together with their strict decoding and the
correctness-first ranking order in which hard DRC and unrouted connections outrank wire length,
via count, and runtime.

Stability is the point. These shapes are versioned alongside the JSON schemas under `schemas/`,
so a field may be added but not silently repurposed, and a value that could not be verified is
carried as an explicit literal rather than as an absent field implying success.

It refuses to be a domain layer. Nothing here reads a board, computes geometry, invokes KiCad,
or decides whether a candidate is legal — decoding rejects a malformed payload and stops. Board
IR, routing, placement, and scene types are deliberately not re-exported through this module;
importing it must never pull in the deterministic core.

Every rejection is a `ManifestContractError`, so a caller — and the MCP adapter in particular —
can tell a deliberate refusal of an untrusted manifest from an unhandled defect. It is a
`ValueError`, so an existing `except ValueError` around a decode is unaffected.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from types import MappingProxyType
from typing import Any

SCHEMA_VERSION = "1.0"
_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


class ManifestContractError(ValueError):
    """Raised when an untrusted protocol-boundary manifest violates its declared contract.

    This is the same *kind* of statement `request_boundary.RequestError` makes about a
    request payload, spelled separately because this module must not import the
    deterministic core: `request_boundary` pulls in `board_ir`, and the module docstring
    above forbids that edge. It subclasses `ValueError` so nothing that already catches a
    decoding failure changes behaviour.

    Every decode failure in this module is one of these. Decoding rejects a malformed
    payload and stops -- there is no computation here that could fail for any other reason
    -- so the type carries no risk of dressing an unhandled defect as a deliberate answer.
    That distinction is what `mcp_server` translates on: it is a refusal the caller is
    meant to read, not a crash whose text must be withheld.
    """


def _non_negative(name: str, value: int | float) -> None:
    if isinstance(value, bool) or value < 0:
        raise ManifestContractError(f"{name} must be a non-negative number")


@dataclass(frozen=True, slots=True)
class BoardCounts:
    """Conservative object counts extracted from a KiCad board."""

    copper_layers: int
    footprints: int
    nets: int
    segments: int
    vias: int
    zones: int

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _non_negative(name, value)


@dataclass(frozen=True, slots=True)
class BoardManifest:
    """A content-addressed, read-only board snapshot descriptor."""

    board_id: str
    revision: str
    relative_path: str
    format: str
    size_bytes: int
    counts: BoardCounts
    source_version: str | None = None
    source_generator: str | None = None
    schema_version: str = SCHEMA_VERSION

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DrcSummary:
    """Privacy-preserving evidence from an authoritative KiCad DRC run.

    ``passed`` is the compatibility hard-gate used by the routing/apply paths: it means that
    KiCad reported no active errors or unconnected items.  It intentionally permits warnings and
    exclusions because KiCad treats those as non-blocking findings.  ``clean`` is the stricter
    presentation signal for public evidence: it is true only when the report has no findings or
    ignored checks at all.  Keeping both signals prevents a warning-only report from being
    advertised as a clean board while preserving the existing hard-gate semantics.
    """

    base_revision: str
    drc_context_revision: str
    kicad_version: str
    drc_schema: str
    coordinate_units: str
    error_count: int
    warning_count: int
    exclusion_count: int
    ignored_check_count: int
    unconnected_count: int
    violation_type_counts: Mapping[str, int]
    passed: bool
    schema_version: str = SCHEMA_VERSION

    @property
    def clean(self) -> bool:
        """Whether the authoritative report contains no findings or ignored checks."""

        return (
            self.error_count == 0
            and self.warning_count == 0
            and self.exclusion_count == 0
            and self.ignored_check_count == 0
            and self.unconnected_count == 0
            and not self.violation_type_counts
        )

    def __post_init__(self) -> None:
        for name, revision in (
            ("base_revision", self.base_revision),
            ("drc_context_revision", self.drc_context_revision),
        ):
            if not _SHA256_ID.fullmatch(revision):
                raise ManifestContractError(f"{name} must be content-addressed with sha256")
        if not 1 <= len(self.kicad_version.strip()) <= 128:
            raise ManifestContractError("KiCad version is malformed")
        if self.drc_schema != "https://schemas.kicad.org/drc.v1.json":
            raise ManifestContractError("DRC schema is unsupported")
        if self.coordinate_units != "mm":
            raise ManifestContractError("DRC coordinates must use millimetres")
        for name in (
            "error_count",
            "warning_count",
            "exclusion_count",
            "ignored_check_count",
            "unconnected_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ManifestContractError(f"{name} must be an integer")
            _non_negative(name, value)
        if not isinstance(self.violation_type_counts, Mapping):
            raise ManifestContractError("DRC violation counts must be a mapping")
        if len(self.violation_type_counts) > 1000:
            raise ManifestContractError("too many DRC violation types")
        normalized_counts: dict[str, int] = {}
        for violation_type, count in self.violation_type_counts.items():
            if (
                not isinstance(violation_type, str)
                or not violation_type
                or len(violation_type) > 128
            ):
                raise ManifestContractError("DRC violation type is malformed")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ManifestContractError("DRC violation count must be an integer")
            _non_negative(violation_type, count)
            normalized_counts[violation_type] = count
        aggregate_finding_count = (
            self.error_count + self.warning_count + self.exclusion_count + self.unconnected_count
        )
        if sum(normalized_counts.values()) != aggregate_finding_count:
            raise ManifestContractError(
                "DRC violation-type counts must equal aggregate finding counts"
            )
        expected_pass = self.error_count == 0 and self.unconnected_count == 0
        if self.passed is not expected_pass:
            raise ManifestContractError("passed must reflect hard DRC and connectivity correctness")
        if self.schema_version != SCHEMA_VERSION:
            raise ManifestContractError("DRC summary schema version is unsupported")
        object.__setattr__(
            self,
            "violation_type_counts",
            MappingProxyType(dict(sorted(normalized_counts.items()))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "base_revision": self.base_revision,
            "drc_context_revision": self.drc_context_revision,
            "kicad_version": self.kicad_version,
            "drc_schema": self.drc_schema,
            "coordinate_units": self.coordinate_units,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "exclusion_count": self.exclusion_count,
            "ignored_check_count": self.ignored_check_count,
            "unconnected_count": self.unconnected_count,
            "violation_type_counts": dict(self.violation_type_counts),
            "passed": self.passed,
            "clean": self.clean,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class ErcSummary:
    """Privacy-preserving evidence from an authoritative KiCad schematic ERC run.

    This mirrors :class:`DrcSummary` deliberately: CopperMCP never decides what an electrical
    rule violation is, it only transports KiCad's verdict.  ``passed`` is the hard gate and means
    KiCad reported no active *error*-severity violation.  It permits warnings and exclusions
    because KiCad treats those as non-blocking.  ``clean`` is the stricter presentation signal and
    is true only when the report carries no findings and no ignored checks at all, so a
    warning-only schematic can never be advertised as ERC-clean.

    ``intent_digest`` and ``schematic_digest`` bind the verdict to the exact Circuit Intent
    snapshot and the exact schematic bytes that were checked.  A summary that is not bound to both
    is not evidence about anything.
    """

    intent_digest: str
    schematic_digest: str
    kicad_version: str
    erc_schema: str
    coordinate_units: str
    error_count: int
    warning_count: int
    exclusion_count: int
    ignored_check_count: int
    sheet_count: int
    violation_type_counts: Mapping[str, int]
    passed: bool
    schema_version: str = SCHEMA_VERSION

    @property
    def clean(self) -> bool:
        """Whether the authoritative report contains no findings or ignored checks."""

        return (
            self.error_count == 0
            and self.warning_count == 0
            and self.exclusion_count == 0
            and self.ignored_check_count == 0
            and not self.violation_type_counts
        )

    def __post_init__(self) -> None:
        for name, digest in (
            ("intent_digest", self.intent_digest),
            ("schematic_digest", self.schematic_digest),
        ):
            if not _SHA256_ID.fullmatch(digest):
                raise ManifestContractError(f"{name} must be content-addressed with sha256")
        if not 1 <= len(self.kicad_version.strip()) <= 128:
            raise ManifestContractError("KiCad version is malformed")
        if self.erc_schema != "https://schemas.kicad.org/erc.v1.json":
            raise ManifestContractError("ERC schema is unsupported")
        if self.coordinate_units != "mm":
            raise ManifestContractError("ERC coordinates must use millimetres")
        for name in (
            "error_count",
            "warning_count",
            "exclusion_count",
            "ignored_check_count",
            "sheet_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ManifestContractError(f"{name} must be an integer")
            _non_negative(name, value)
        if self.sheet_count < 1:
            raise ManifestContractError("an ERC report must cover at least one sheet")
        if not isinstance(self.violation_type_counts, Mapping):
            raise ManifestContractError("ERC violation counts must be a mapping")
        if len(self.violation_type_counts) > 1000:
            raise ManifestContractError("too many ERC violation types")
        normalized_counts: dict[str, int] = {}
        for violation_type, count in self.violation_type_counts.items():
            if (
                not isinstance(violation_type, str)
                or not violation_type
                or len(violation_type) > 128
            ):
                raise ManifestContractError("ERC violation type is malformed")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ManifestContractError("ERC violation count must be an integer")
            _non_negative(violation_type, count)
            normalized_counts[violation_type] = count
        aggregate_finding_count = self.error_count + self.warning_count + self.exclusion_count
        if sum(normalized_counts.values()) != aggregate_finding_count:
            raise ManifestContractError(
                "ERC violation-type counts must equal aggregate finding counts"
            )
        if self.passed is not (self.error_count == 0):
            raise ManifestContractError("passed must reflect the absence of active ERC errors")
        if self.schema_version != SCHEMA_VERSION:
            raise ManifestContractError("ERC summary schema version is unsupported")
        object.__setattr__(
            self,
            "violation_type_counts",
            MappingProxyType(dict(sorted(normalized_counts.items()))),
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "intent_digest": self.intent_digest,
            "schematic_digest": self.schematic_digest,
            "kicad_version": self.kicad_version,
            "erc_schema": self.erc_schema,
            "coordinate_units": self.coordinate_units,
            "error_count": self.error_count,
            "warning_count": self.warning_count,
            "exclusion_count": self.exclusion_count,
            "ignored_check_count": self.ignored_check_count,
            "sheet_count": self.sheet_count,
            "violation_type_counts": dict(self.violation_type_counts),
            "passed": self.passed,
            "clean": self.clean,
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class CandidateMetrics:
    """Comparable metrics, ordered by correctness before optimization quality."""

    hard_drc_errors: int
    unrouted_connections: int
    vias: int
    wire_length_mm: float
    runtime_seconds: float

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            _non_negative(name, value)


@dataclass(frozen=True, slots=True)
class CandidateSummary:
    """Immutable summary of a generated candidate."""

    candidate_id: str
    base_revision: str
    status: str
    metrics: CandidateMetrics
    router_version: str
    policy: str
    seed: int
    warnings: tuple[str, ...] = field(default_factory=tuple)
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        if not _SHA256_ID.fullmatch(self.candidate_id):
            raise ManifestContractError("candidate_id must be content-addressed with sha256")
        if not _SHA256_ID.fullmatch(self.base_revision):
            raise ManifestContractError("base_revision must be content-addressed with sha256")
        if self.status not in {"proposed", "validated", "rejected"}:
            raise ManifestContractError("candidate status is invalid")
        if isinstance(self.seed, bool) or self.seed < 0:
            raise ManifestContractError("seed must be a non-negative integer")
        if not self.router_version.strip() or not self.policy.strip():
            raise ManifestContractError("router_version and policy must be non-empty")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def candidate_from_dict(payload: dict[str, Any]) -> CandidateSummary:
    """Parse and validate a candidate at an untrusted JSON boundary."""

    required = {
        "candidate_id",
        "base_revision",
        "status",
        "metrics",
        "router_version",
        "policy",
        "seed",
    }
    missing = sorted(required - payload.keys())
    if missing:
        raise ManifestContractError(f"candidate is missing required fields: {', '.join(missing)}")
    metrics_payload = payload["metrics"]
    if not isinstance(metrics_payload, dict):
        raise ManifestContractError("metrics must be an object")
    try:
        integer_fields = ("hard_drc_errors", "unrouted_connections", "vias")
        if any(
            isinstance(metrics_payload[field], bool) or not isinstance(metrics_payload[field], int)
            for field in integer_fields
        ):
            raise ManifestContractError
        number_fields = ("wire_length_mm", "runtime_seconds")
        if any(
            isinstance(metrics_payload[field], bool)
            or not isinstance(metrics_payload[field], int | float)
            for field in number_fields
        ):
            raise ManifestContractError
        metrics = CandidateMetrics(
            hard_drc_errors=metrics_payload["hard_drc_errors"],
            unrouted_connections=metrics_payload["unrouted_connections"],
            vias=metrics_payload["vias"],
            wire_length_mm=float(metrics_payload["wire_length_mm"]),
            runtime_seconds=float(metrics_payload["runtime_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ManifestContractError("candidate metrics are malformed") from error

    warnings_payload = payload.get("warnings", [])
    if (
        not isinstance(warnings_payload, list)
        or len(warnings_payload) > 1000
        or not all(isinstance(warning, str) for warning in warnings_payload)
    ):
        raise ManifestContractError("warnings must be a list of strings")

    seed = payload["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ManifestContractError("seed must be an integer")
    return CandidateSummary(
        candidate_id=str(payload["candidate_id"]),
        base_revision=str(payload["base_revision"]),
        status=str(payload["status"]),
        metrics=metrics,
        router_version=str(payload["router_version"]),
        policy=str(payload["policy"]),
        seed=seed,
        warnings=tuple(warnings_payload),
    )


def rank_candidates(candidates: list[CandidateSummary]) -> list[CandidateSummary]:
    """Rank candidates lexicographically with hard correctness first."""

    return sorted(
        candidates,
        key=lambda candidate: (
            candidate.metrics.hard_drc_errors,
            candidate.metrics.unrouted_connections,
            candidate.metrics.vias,
            candidate.metrics.wire_length_mm,
            candidate.metrics.runtime_seconds,
            candidate.candidate_id,
        ),
    )
