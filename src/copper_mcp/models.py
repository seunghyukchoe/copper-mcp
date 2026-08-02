"""Stable, JSON-serializable domain models for the public protocol boundary."""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any

SCHEMA_VERSION = "1.0"
_SHA256_ID = re.compile(r"^sha256:[a-f0-9]{64}$")


def _non_negative(name: str, value: int | float) -> None:
    if isinstance(value, bool) or value < 0:
        raise ValueError(f"{name} must be a non-negative number")


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
    """Privacy-preserving evidence from an authoritative KiCad DRC run."""

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
    violation_type_counts: dict[str, int]
    passed: bool
    schema_version: str = SCHEMA_VERSION

    def __post_init__(self) -> None:
        for name, revision in (
            ("base_revision", self.base_revision),
            ("drc_context_revision", self.drc_context_revision),
        ):
            if not _SHA256_ID.fullmatch(revision):
                raise ValueError(f"{name} must be content-addressed with sha256")
        if not 1 <= len(self.kicad_version.strip()) <= 128:
            raise ValueError("KiCad version is malformed")
        if self.drc_schema != "https://schemas.kicad.org/drc.v1.json":
            raise ValueError("DRC schema is unsupported")
        if self.coordinate_units != "mm":
            raise ValueError("DRC coordinates must use millimetres")
        for name in (
            "error_count",
            "warning_count",
            "exclusion_count",
            "ignored_check_count",
            "unconnected_count",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            _non_negative(name, value)
        if len(self.violation_type_counts) > 1000:
            raise ValueError("too many DRC violation types")
        for violation_type, count in self.violation_type_counts.items():
            if not violation_type or len(violation_type) > 128:
                raise ValueError("DRC violation type is malformed")
            if isinstance(count, bool) or not isinstance(count, int):
                raise ValueError("DRC violation count must be an integer")
            _non_negative(violation_type, count)
        expected_pass = self.error_count == 0 and self.unconnected_count == 0
        if self.passed is not expected_pass:
            raise ValueError("passed must reflect hard DRC and connectivity correctness")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


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
            raise ValueError("candidate_id must be content-addressed with sha256")
        if not _SHA256_ID.fullmatch(self.base_revision):
            raise ValueError("base_revision must be content-addressed with sha256")
        if self.status not in {"proposed", "validated", "rejected"}:
            raise ValueError("candidate status is invalid")
        if isinstance(self.seed, bool) or self.seed < 0:
            raise ValueError("seed must be a non-negative integer")
        if not self.router_version.strip() or not self.policy.strip():
            raise ValueError("router_version and policy must be non-empty")

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
        raise ValueError(f"candidate is missing required fields: {', '.join(missing)}")
    metrics_payload = payload["metrics"]
    if not isinstance(metrics_payload, dict):
        raise ValueError("metrics must be an object")
    try:
        integer_fields = ("hard_drc_errors", "unrouted_connections", "vias")
        if any(
            isinstance(metrics_payload[field], bool) or not isinstance(metrics_payload[field], int)
            for field in integer_fields
        ):
            raise ValueError
        number_fields = ("wire_length_mm", "runtime_seconds")
        if any(
            isinstance(metrics_payload[field], bool)
            or not isinstance(metrics_payload[field], int | float)
            for field in number_fields
        ):
            raise ValueError
        metrics = CandidateMetrics(
            hard_drc_errors=metrics_payload["hard_drc_errors"],
            unrouted_connections=metrics_payload["unrouted_connections"],
            vias=metrics_payload["vias"],
            wire_length_mm=float(metrics_payload["wire_length_mm"]),
            runtime_seconds=float(metrics_payload["runtime_seconds"]),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("candidate metrics are malformed") from error

    warnings_payload = payload.get("warnings", [])
    if (
        not isinstance(warnings_payload, list)
        or len(warnings_payload) > 1000
        or not all(isinstance(warning, str) for warning in warnings_payload)
    ):
        raise ValueError("warnings must be a list of strings")

    seed = payload["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
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
