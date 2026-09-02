"""Closed, deterministic advisory ranking for immutable route candidates.

This module is deliberately only a surrogate seam.  It validates and orders already-produced
``RouteCandidate`` values using one fixed integer cost signal; it does not inspect a board, run a
router, perform DRC, or make an apply/sign-off decision.  The successful result is a redacted
envelope containing candidate bindings and bounded advisories, never candidate geometry.

The aggregate vertex check is intentionally performed before ``verify_candidate_id``.  Candidate
identity verification hashes the complete route payload, so the cheap aggregate preflight is the
first resource gate at this boundary.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, TypeAlias

from ..board_ir import PointNM
from .astar import verify_candidate_id
from .authoritative_signoff import (
    CancellationCheck,
    CandidateBinding,
    DeadlineCheck,
    SignoffDomain,
    SurrogateAdvisory,
)
from .contracts import (
    AStarSettings,
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
)

_SHA256_PREFIX: Final = "sha256:"
_SHA256_LENGTH: Final = len(_SHA256_PREFIX) + 64
_SCHEMA: Final = "copper-mcp/surrogate-ranking/v1"
_MODEL_ID: Final = "copper-mcp-deterministic-cost-surrogate"
_MODEL_VERSION: Final = "1"
_FEATURE_ORDER: Final = ("total_cost_nm",)
_PROJECTION_ORDER: Final = ("score_milli",)
_COST_SCALE_NM: Final = 1_000
_SCORE_CAP: Final = 1_000_000
_MAX_CANDIDATES: Final = 32
_MAX_TOTAL_VERTICES: Final = 16_384
_COMPARISON_SETTINGS_FIELDS: Final = (
    "grid_step_nm",
    "bend_penalty_nm",
    "proximity_penalty_nm",
    "max_grid_nodes",
    "max_expansions",
    "max_obstacles",
    "max_net_objects",
    "region_margin_nm",
    "max_obstacle_checks",
)


class SurrogateRankingCode(StrEnum):
    """Stable, non-echoing refusals for the advisory ranking boundary."""

    INVALID_INPUT = "invalid_input"
    INVALID_DOMAIN = "invalid_domain"
    INVALID_CANDIDATE = "invalid_candidate"
    DUPLICATE_CANDIDATE = "duplicate_candidate"
    MIXED_BASE_REVISION = "mixed_base_revision"
    INCOMPARABLE_CANDIDATES = "incomparable_candidates"
    VERTEX_BUDGET_EXCEEDED = "vertex_budget_exceeded"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_DIAGNOSTICS: Final[dict[SurrogateRankingCode, str]] = {
    SurrogateRankingCode.INVALID_INPUT: "surrogate ranking input is invalid",
    SurrogateRankingCode.INVALID_DOMAIN: "surrogate ranking domain is unsupported",
    SurrogateRankingCode.INVALID_CANDIDATE: "surrogate ranking candidate is invalid",
    SurrogateRankingCode.DUPLICATE_CANDIDATE: "surrogate ranking candidates are not unique",
    SurrogateRankingCode.MIXED_BASE_REVISION: "surrogate ranking candidates use multiple revisions",
    SurrogateRankingCode.INCOMPARABLE_CANDIDATES: "surrogate ranking candidates are not comparable",
    SurrogateRankingCode.VERTEX_BUDGET_EXCEEDED: "surrogate ranking exceeded its vertex budget",
    SurrogateRankingCode.CANCELLED: "surrogate ranking was cancelled",
    SurrogateRankingCode.DEADLINE_EXCEEDED: "surrogate ranking exceeded its deadline",
}


def _is_digest(value: object) -> bool:
    return (
        type(value) is str
        and len(value) == _SHA256_LENGTH
        and value.startswith(_SHA256_PREFIX)
        and all(character in "0123456789abcdef" for character in value[len(_SHA256_PREFIX) :])
    )


def _canonical_bytes(value: object) -> bytes:
    rendered = json.dumps(
        value,
        ensure_ascii=True,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return rendered.encode("utf-8", errors="strict") + b"\n"


def _digest(value: object) -> str:
    return _SHA256_PREFIX + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _settings_payload() -> dict[str, object]:
    return {
        "schema": _SCHEMA,
        "model_id": _MODEL_ID,
        "model_version": _MODEL_VERSION,
        "feature_order": list(_FEATURE_ORDER),
        "coefficients": {
            "cost_scale_nm": _COST_SCALE_NM,
            "score_sign": -1,
            "score_cap": _SCORE_CAP,
        },
        "limits": {
            "max_candidates": _MAX_CANDIDATES,
            "max_total_vertices": _MAX_TOTAL_VERTICES,
        },
        "comparison_context_fields": [
            "base_revision",
            "start_pad_id",
            "end_pad_id",
            "net_id",
            "layer_id",
            "width_nm",
            "pad_count",
            "ordering_policy",
            "fill_binding",
            "settings",
            "router_version",
            "policy",
        ],
        "comparison_settings_fields": list(_COMPARISON_SETTINGS_FIELDS),
    }


_SETTINGS_DIGEST: Final = _digest(_settings_payload())


def _binding_payload(binding: CandidateBinding) -> dict[str, str]:
    return {
        "candidate_id": binding.candidate_id,
        "base_revision": binding.base_revision,
    }


def _comparison_context(candidate: RouteCandidate) -> dict[str, object]:
    """Return only source-owned semantics that make candidates comparable.

    Seed, geometry, cost, and metrics are deliberately absent: those are the values this
    advisory ranks.  Every remaining routing-problem and policy field is bound instead.
    """

    settings = candidate.settings
    return {
        "base_revision": candidate.base_revision,
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "net_id": candidate.patch.net_id,
        "layer_id": candidate.patch.layer_id,
        "width_nm": candidate.patch.width_nm,
        "pad_count": candidate.pad_count,
        "ordering_policy": candidate.ordering_policy,
        "fill_binding": candidate.fill_binding,
        "settings": {name: getattr(settings, name) for name in _COMPARISON_SETTINGS_FIELDS},
        "router_version": candidate.router_version,
        "policy": candidate.policy,
    }


def _comparison_digest(candidate: RouteCandidate) -> str:
    return _digest({"schema": _SCHEMA, "context": _comparison_context(candidate)})


def _input_payload(
    *,
    domain: SignoffDomain,
    base_revision: str,
    comparison_digest: str,
    bindings: tuple[CandidateBinding, ...],
) -> dict[str, object]:
    return {
        "schema": _SCHEMA,
        "model_id": _MODEL_ID,
        "model_version": _MODEL_VERSION,
        "domain": domain.value,
        "base_revision": base_revision,
        "comparison_digest": comparison_digest,
        "candidate_bindings": [
            _binding_payload(binding)
            for binding in sorted(bindings, key=lambda item: item.candidate_id)
        ],
    }


def _entry_payload(entry: SurrogateRankingEntry) -> dict[str, object]:
    return {
        "binding": _binding_payload(entry.binding),
        "advisory": {
            "rank": entry.advisory.rank,
            "score_milli": entry.advisory.score_milli,
        },
        "feature_digest": entry.feature_digest,
    }


def _ranking_payload_values(
    *,
    status: str,
    advisory_only: bool,
    domain: SignoffDomain,
    model_id: str,
    model_version: str,
    base_revision: str,
    comparison_digest: str,
    settings_digest: str,
    input_digest: str,
    entries: tuple[SurrogateRankingEntry, ...],
) -> dict[str, object]:
    return {
        "schema": _SCHEMA,
        "status": status,
        "advisory_only": advisory_only,
        "domain": domain.value,
        "model_id": model_id,
        "model_version": model_version,
        "base_revision": base_revision,
        "comparison_digest": comparison_digest,
        "settings_digest": settings_digest,
        "input_digest": input_digest,
        "entries": [_entry_payload(entry) for entry in entries],
    }


def _score(total_cost_nm: int) -> int:
    # ``total_cost_nm`` is validated as an exact non-negative integer before this function.
    cost_units = (total_cost_nm + (_COST_SCALE_NM - 1)) // _COST_SCALE_NM
    return -min(_SCORE_CAP, cost_units)


def _feature_digest(binding: CandidateBinding, *, score_milli: int) -> str:
    return _digest(
        {
            "schema": _SCHEMA,
            "model_id": _MODEL_ID,
            "model_version": _MODEL_VERSION,
            "projection_order": list(_PROJECTION_ORDER),
            "binding": _binding_payload(binding),
            "features": {"score_milli": score_milli},
        }
    )


@dataclass(frozen=True, slots=True)
class SurrogateRankingEntry:
    """One redacted candidate binding and its bounded advisory ranking."""

    binding: CandidateBinding
    advisory: SurrogateAdvisory
    feature_digest: str

    def __post_init__(self) -> None:
        if type(self.binding) is not CandidateBinding:
            raise ValueError("surrogate ranking binding is malformed")
        if type(self.advisory) is not SurrogateAdvisory:
            raise ValueError("surrogate ranking advisory is malformed")
        if self.advisory.rank < 1:
            raise ValueError("surrogate ranking rank must be one-based")
        if not _is_digest(self.feature_digest):
            raise ValueError("surrogate feature digest is malformed")
        if self.feature_digest != _feature_digest(
            self.binding,
            score_milli=self.advisory.score_milli,
        ):
            raise ValueError("surrogate feature digest does not match the advisory")

    @property
    def candidate_id(self) -> str:
        """Return the redacted candidate identity without exposing the candidate itself."""

        return self.binding.candidate_id

    @property
    def base_revision(self) -> str:
        return self.binding.base_revision

    def to_dict(self) -> dict[str, object]:
        return _entry_payload(self)


@dataclass(frozen=True, slots=True)
class SurrogateRankingRefused:
    """Closed refusal containing only a typed code and fixed non-echoing diagnostic."""

    status: str
    code: SurrogateRankingCode
    diagnostic: str

    def __post_init__(self) -> None:
        if self.status != "refused" or type(self.status) is not str:
            raise ValueError("surrogate refusal status is malformed")
        if type(self.code) is not SurrogateRankingCode:
            raise ValueError("surrogate refusal code is malformed")
        expected = _DIAGNOSTICS[self.code]
        if self.diagnostic != expected or type(self.diagnostic) is not str:
            raise ValueError("surrogate refusal diagnostic is malformed")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "status": self.status,
            "code": self.code.value,
            "diagnostic": self.diagnostic,
        }


@dataclass(frozen=True, slots=True)
class SurrogateRankingAccepted:
    """Closed, redacted advisory result; acceptance never implies a sign-off."""

    status: str
    advisory_only: bool
    domain: SignoffDomain
    model_id: str
    model_version: str
    base_revision: str
    comparison_digest: str
    settings_digest: str
    input_digest: str
    ranking_digest: str
    entries: tuple[SurrogateRankingEntry, ...]

    def __post_init__(self) -> None:
        if self.status != "accepted" or type(self.status) is not str:
            raise ValueError("surrogate accepted status is malformed")
        if self.advisory_only is not True or type(self.advisory_only) is not bool:
            raise ValueError("surrogate results are always advisory-only")
        if type(self.domain) is not SignoffDomain:
            raise ValueError("surrogate ranking domain is unsupported")
        if self.model_id != _MODEL_ID or type(self.model_id) is not str:
            raise ValueError("surrogate model identity is source-owned")
        if self.model_version != _MODEL_VERSION or type(self.model_version) is not str:
            raise ValueError("surrogate model version is source-owned")
        if not _is_digest(self.base_revision):
            raise ValueError("surrogate base revision is malformed")
        for name, value in (
            ("comparison digest", self.comparison_digest),
            ("settings digest", self.settings_digest),
            ("input digest", self.input_digest),
            ("ranking digest", self.ranking_digest),
        ):
            if not _is_digest(value):
                raise ValueError(f"{name} is malformed")
        if type(self.entries) is not tuple or not 1 <= len(self.entries) <= _MAX_CANDIDATES:
            raise ValueError("surrogate entries are outside the supported bound")

        seen_ids: set[str] = set()
        previous_key: tuple[int, str] | None = None
        for expected_rank, entry in enumerate(self.entries, start=1):
            if type(entry) is not SurrogateRankingEntry:
                raise ValueError("surrogate ranking entry is malformed")
            if entry.base_revision != self.base_revision:
                raise ValueError("surrogate entries use multiple revisions")
            if entry.advisory.rank != expected_rank:
                raise ValueError("surrogate ranks are not one-based and contiguous")
            if entry.candidate_id in seen_ids:
                raise ValueError("surrogate entries are not unique")
            if entry.feature_digest != _feature_digest(
                entry.binding,
                score_milli=entry.advisory.score_milli,
            ):
                raise ValueError("surrogate feature digest does not match the advisory")
            current_key = (-entry.advisory.score_milli, entry.candidate_id)
            if previous_key is not None and current_key < previous_key:
                raise ValueError("surrogate entries are not in deterministic ranking order")
            seen_ids.add(entry.candidate_id)
            previous_key = current_key

        if self.settings_digest != _SETTINGS_DIGEST:
            raise ValueError("surrogate settings digest does not match the source baseline")
        expected_input = _digest(
            _input_payload(
                domain=self.domain,
                base_revision=self.base_revision,
                comparison_digest=self.comparison_digest,
                bindings=tuple(entry.binding for entry in self.entries),
            )
        )
        if self.input_digest != expected_input:
            raise ValueError("surrogate input digest does not match the bindings")
        expected_ranking = _digest(
            _ranking_payload_values(
                status=self.status,
                advisory_only=self.advisory_only,
                domain=self.domain,
                model_id=self.model_id,
                model_version=self.model_version,
                base_revision=self.base_revision,
                comparison_digest=self.comparison_digest,
                settings_digest=self.settings_digest,
                input_digest=self.input_digest,
                entries=self.entries,
            )
        )
        if self.ranking_digest != expected_ranking:
            raise ValueError("surrogate ranking digest does not match the result")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "status": self.status,
            "advisory_only": self.advisory_only,
            "domain": self.domain.value,
            "model_id": self.model_id,
            "model_version": self.model_version,
            "base_revision": self.base_revision,
            "comparison_digest": self.comparison_digest,
            "settings_digest": self.settings_digest,
            "input_digest": self.input_digest,
            "ranking_digest": self.ranking_digest,
            "entries": [_entry_payload(entry) for entry in self.entries],
        }


SurrogateRankingResult: TypeAlias = SurrogateRankingAccepted | SurrogateRankingRefused


def _refused(code: SurrogateRankingCode) -> SurrogateRankingRefused:
    return SurrogateRankingRefused("refused", code, _DIAGNOSTICS[code])


def _stop_code(
    cancelled: CancellationCheck | None, deadline: DeadlineCheck | Callable[[], object] | None
) -> SurrogateRankingCode | None:
    """Evaluate untrusted cooperative stops with a fail-closed, typed result."""

    if cancelled is not None:
        try:
            value = cancelled()
        except Exception:  # pragma: no cover - exercised by hostile callback tests
            return SurrogateRankingCode.CANCELLED
        if type(value) is not bool or value:
            return SurrogateRankingCode.CANCELLED

    if deadline is not None:
        try:
            value = deadline()
        except Exception:  # pragma: no cover - exercised by hostile callback tests
            return SurrogateRankingCode.DEADLINE_EXCEEDED
        if type(value) is not bool or value:
            return SurrogateRankingCode.DEADLINE_EXCEEDED
    return None


def _aggregate_vertex_count(candidates: tuple[RouteCandidate, ...]) -> int | None:
    """Count only vertex tuple lengths, before any candidate identity hash is attempted."""

    path_groups: list[tuple[RoutePath, ...]] = []
    path_count = 0
    try:
        for candidate in candidates:
            patch = candidate.patch
            if type(patch) is not RoutePatch:
                return None
            paths = patch.paths
            if type(paths) is not tuple or not paths:
                return None
            path_groups.append(paths)
            path_count += len(paths)
            if path_count > _MAX_TOTAL_VERTICES // 2:
                return _MAX_TOTAL_VERTICES + 1

        total = 0
        for paths in path_groups:
            for path in paths:
                if type(path) is not RoutePath:
                    return None
                vertices = path.vertices
                if type(vertices) is not tuple or len(vertices) < 2:
                    return None
                total += len(vertices)
                if total > _MAX_TOTAL_VERTICES:
                    return total
    except Exception:
        return None
    return total


def _reconstruct_candidate(candidate: RouteCandidate) -> RouteCandidate | None:
    """Rebuild every nested contract before allowing canonical identity hashing."""

    try:
        if type(candidate) is not RouteCandidate:
            return None

        patch = candidate.patch
        cost = candidate.cost
        metrics = candidate.metrics
        settings = candidate.settings
        if (
            type(patch) is not RoutePatch
            or type(cost) is not RouteCost
            or type(metrics) is not RouteMetrics
            or type(settings) is not AStarSettings
        ):
            return None

        paths = patch.paths
        if type(paths) is not tuple or not paths:
            return None
        fresh_paths: list[RoutePath] = []
        for path in paths:
            if type(path) is not RoutePath:
                return None
            vertices = path.vertices
            if type(vertices) is not tuple:
                return None
            fresh_vertices: list[PointNM] = []
            for point in vertices:
                if type(point) is not PointNM:
                    return None
                fresh_vertices.append(PointNM(x=point.x, y=point.y))
            fresh_paths.append(RoutePath(vertices=tuple(fresh_vertices)))

        fresh_patch = RoutePatch(
            net_id=patch.net_id,
            layer_id=patch.layer_id,
            width_nm=patch.width_nm,
            paths=tuple(fresh_paths),
        )
        fresh_cost = RouteCost(
            length_nm=cost.length_nm,
            bend_count=cost.bend_count,
            bend_cost_nm=cost.bend_cost_nm,
            proximity_steps=cost.proximity_steps,
            proximity_cost_nm=cost.proximity_cost_nm,
            via_cost_nm=cost.via_cost_nm,
            total_cost_nm=cost.total_cost_nm,
        )
        fresh_metrics = RouteMetrics(
            hard_internal_violations=metrics.hard_internal_violations,
            unrouted_connections=metrics.unrouted_connections,
            vias=metrics.vias,
            wire_length_nm=metrics.wire_length_nm,
            expanded_states=metrics.expanded_states,
            peak_frontier_states=metrics.peak_frontier_states,
            obstacle_checks=metrics.obstacle_checks,
        )
        fresh_settings = AStarSettings(
            grid_step_nm=settings.grid_step_nm,
            bend_penalty_nm=settings.bend_penalty_nm,
            proximity_penalty_nm=settings.proximity_penalty_nm,
            max_grid_nodes=settings.max_grid_nodes,
            max_expansions=settings.max_expansions,
            max_obstacles=settings.max_obstacles,
            max_net_objects=settings.max_net_objects,
            region_margin_nm=settings.region_margin_nm,
            max_obstacle_checks=settings.max_obstacle_checks,
        )
        return RouteCandidate(
            candidate_id=candidate.candidate_id,
            base_revision=candidate.base_revision,
            start_pad_id=candidate.start_pad_id,
            end_pad_id=candidate.end_pad_id,
            patch=fresh_patch,
            cost=fresh_cost,
            metrics=fresh_metrics,
            settings=fresh_settings,
            router_version=candidate.router_version,
            policy=candidate.policy,
            seed=candidate.seed,
            pad_count=candidate.pad_count,
            ordering_policy=candidate.ordering_policy,
            fill_binding=candidate.fill_binding,
        )
    except Exception:
        return None


def _validated_cost(candidate: RouteCandidate) -> int | None:
    """Read and revalidate the exact integer feature used by this baseline."""

    cost = candidate.cost
    metrics = candidate.metrics
    if type(cost) is not RouteCost or type(metrics) is not RouteMetrics:
        return None
    cost_values = (
        cost.length_nm,
        cost.bend_count,
        cost.bend_cost_nm,
        cost.proximity_steps,
        cost.proximity_cost_nm,
        cost.via_cost_nm,
        cost.total_cost_nm,
    )
    metric_values = (
        metrics.hard_internal_violations,
        metrics.unrouted_connections,
        metrics.vias,
        metrics.wire_length_nm,
        metrics.expanded_states,
        metrics.peak_frontier_states,
        metrics.obstacle_checks,
    )
    if any(
        type(value) is not int or isinstance(value, bool) or not 0 <= value <= (1 << 53) - 1
        for value in (*cost_values, *metric_values)
    ):
        return None
    if cost.total_cost_nm != (
        cost.length_nm + cost.bend_cost_nm + cost.proximity_cost_nm + cost.via_cost_nm
    ):
        return None
    total_cost_nm = cost.total_cost_nm
    return total_cost_nm


def rank_surrogate_candidates(
    candidates: tuple[RouteCandidate, ...],
    domain: SignoffDomain,
    *,
    cancelled: CancellationCheck | None = None,
    deadline: DeadlineCheck | Callable[[], object] | None = None,
) -> SurrogateRankingResult:
    """Return a deterministic advisory order for one bounded batch of route candidates.

    The domain is metadata only: every supported domain receives the same advisory-only result.
    No domain is treated as a physics claim, and no callback, candidate object, geometry, or
    exception text is copied into a result.
    """

    if type(domain) is not SignoffDomain:
        return _refused(SurrogateRankingCode.INVALID_DOMAIN)
    if type(candidates) is not tuple or not 1 <= len(candidates) <= _MAX_CANDIDATES:
        return _refused(SurrogateRankingCode.INVALID_INPUT)
    if any(type(candidate) is not RouteCandidate for candidate in candidates):
        return _refused(SurrogateRankingCode.INVALID_CANDIDATE)

    # The first cooperative check precedes all candidate work, including the len-only preflight.
    stop = _stop_code(cancelled, deadline)
    if stop is not None:
        return _refused(stop)

    vertex_count = _aggregate_vertex_count(candidates)
    if vertex_count is None:
        return _refused(SurrogateRankingCode.INVALID_CANDIDATE)
    if vertex_count > _MAX_TOTAL_VERTICES:
        return _refused(SurrogateRankingCode.VERTEX_BUDGET_EXCEEDED)

    reconstructed_candidates: list[RouteCandidate] = []
    for candidate in candidates:
        stop = _stop_code(cancelled, deadline)
        if stop is not None:
            return _refused(stop)
        reconstructed = _reconstruct_candidate(candidate)
        if reconstructed is None:
            return _refused(SurrogateRankingCode.INVALID_CANDIDATE)
        reconstructed_candidates.append(reconstructed)

    scored_by_id: dict[str, tuple[int, str]] = {}
    base_revision: str | None = None
    comparison_digest: str | None = None
    for candidate in reconstructed_candidates:
        # Identity verification is deliberately before feature extraction.  Its implementation
        # is the only operation here permitted to hash a candidate's full canonical content.
        stop = _stop_code(cancelled, deadline)
        if stop is not None:
            return _refused(stop)
        try:
            verified_ok = verify_candidate_id(candidate)
        except Exception:
            return _refused(SurrogateRankingCode.INVALID_CANDIDATE)
        if type(verified_ok) is not bool or not verified_ok:
            return _refused(SurrogateRankingCode.INVALID_CANDIDATE)

        candidate_id = candidate.candidate_id
        revision = candidate.base_revision
        if not _is_digest(candidate_id) or not _is_digest(revision):
            return _refused(SurrogateRankingCode.INVALID_CANDIDATE)
        if base_revision is None:
            base_revision = revision
        elif revision != base_revision:
            return _refused(SurrogateRankingCode.MIXED_BASE_REVISION)

        candidate_comparison_digest = _comparison_digest(candidate)
        if comparison_digest is None:
            comparison_digest = candidate_comparison_digest
        elif candidate_comparison_digest != comparison_digest:
            return _refused(SurrogateRankingCode.INCOMPARABLE_CANDIDATES)
        if candidate_id in scored_by_id:
            return _refused(SurrogateRankingCode.DUPLICATE_CANDIDATE)

        total_cost_nm = _validated_cost(candidate)
        if total_cost_nm is None:
            return _refused(SurrogateRankingCode.INVALID_CANDIDATE)
        score_milli = _score(total_cost_nm)
        binding = CandidateBinding(candidate_id=candidate_id, base_revision=revision)
        feature_digest = _feature_digest(
            binding,
            score_milli=score_milli,
        )
        # A check after feature extraction makes cancellation/deadline changes during scoring
        # fail closed before any partially-ranked entry can be retained.
        stop = _stop_code(cancelled, deadline)
        if stop is not None:
            return _refused(stop)
        # Keep only the bounded score and digest; no candidate or raw feature crosses the result
        # boundary or remains in the internal ranking accumulator.
        scored_by_id[candidate_id] = (score_milli, feature_digest)

    assert base_revision is not None
    assert comparison_digest is not None
    scored = []
    for candidate_id, record in scored_by_id.items():
        score_milli, feature_digest = record
        scored.append((score_milli, candidate_id, feature_digest))
    scored.sort(key=lambda item: (-item[0], item[1]))

    entries = tuple(
        SurrogateRankingEntry(
            binding=CandidateBinding(candidate_id=candidate_id, base_revision=base_revision),
            advisory=SurrogateAdvisory(rank=rank, score_milli=score_milli),
            feature_digest=feature_digest,
        )
        for rank, (score_milli, candidate_id, feature_digest) in enumerate(scored, start=1)
    )

    input_digest = _digest(
        _input_payload(
            domain=domain,
            base_revision=base_revision,
            comparison_digest=comparison_digest,
            bindings=tuple(entry.binding for entry in entries),
        )
    )
    ranking_digest = _digest(
        _ranking_payload_values(
            status="accepted",
            advisory_only=True,
            domain=domain,
            model_id=_MODEL_ID,
            model_version=_MODEL_VERSION,
            base_revision=base_revision,
            comparison_digest=comparison_digest,
            settings_digest=_SETTINGS_DIGEST,
            input_digest=input_digest,
            entries=entries,
        )
    )

    # Publication is the final stop boundary.  Constructing the accepted envelope below is
    # deterministic and contains only already-redacted values.
    stop = _stop_code(cancelled, deadline)
    if stop is not None:
        return _refused(stop)
    try:
        return SurrogateRankingAccepted(
            status="accepted",
            advisory_only=True,
            domain=domain,
            model_id=_MODEL_ID,
            model_version=_MODEL_VERSION,
            base_revision=base_revision,
            comparison_digest=comparison_digest,
            settings_digest=_SETTINGS_DIGEST,
            input_digest=input_digest,
            ranking_digest=ranking_digest,
            entries=entries,
        )
    except Exception:
        # Do not expose constructor or digest failures from a malformed/tampered input.
        return _refused(SurrogateRankingCode.INVALID_CANDIDATE)


__all__ = [
    "SurrogateRankingAccepted",
    "SurrogateRankingCode",
    "SurrogateRankingEntry",
    "SurrogateRankingRefused",
    "SurrogateRankingResult",
    "rank_surrogate_candidates",
]
