"""Focused tests for the private, advisory-only surrogate ranking seam."""

from __future__ import annotations

import ast
import hashlib
import inspect
import json
from dataclasses import FrozenInstanceError, fields, replace
from itertools import pairwise
from pathlib import Path
from typing import cast

import pytest

import copper_mcp.routing.surrogate_ranking as surrogate_ranking
from copper_mcp.board_ir import PointNM
from copper_mcp.routing.astar import canonical_candidate_bytes, verify_candidate_id
from copper_mcp.routing.authoritative_signoff import (
    CandidateBinding,
    SignoffCode,
    SignoffDomain,
    SignoffStatus,
    SurrogateAdvisory,
    evaluate_authoritative_signoff,
)
from copper_mcp.routing.contracts import (
    AStarSettings,
    RouteCandidate,
    RouteCost,
    RouteMetrics,
    RoutePatch,
    RoutePath,
)
from copper_mcp.routing.surrogate_ranking import (
    SurrogateRankingAccepted,
    SurrogateRankingCode,
    SurrogateRankingEntry,
    SurrogateRankingRefused,
    rank_surrogate_candidates,
)

_EMPTY_DIGEST = f"sha256:{'0' * 64}"
_REVISION = f"sha256:{'a' * 64}"
_OTHER_REVISION = f"sha256:{'b' * 64}"
_GOLDEN_CANDIDATE_ID = "sha256:e83b51d1d86117d31570c26445ec76bdff019084bdf3e42915490be0775e8538"
_GOLDEN_COMPARISON_DIGEST = (
    "sha256:dfd3d897543ffb2edde0de78b6bd3b1d21f0ae9037dfbaa4c1359732fce7033b"
)
_GOLDEN_SETTINGS_DIGEST = "sha256:cf3c0b030ef039c0a01396143115f516b3b384caeeb9f1b4cb985150715e9c56"
_GOLDEN_INPUT_DIGEST = "sha256:b1d5447fcb691a1108665374357b810d0b5b733b29f4056388224e3d1bb76417"
_GOLDEN_FEATURE_DIGEST = "sha256:e910df18de2926646d040edcb53ed2ecead6dbb8b7eb17ec9c3b6933a6f58ffe"
_GOLDEN_RANKING_DIGEST = "sha256:e4ad60ac2a81cba1325bbc9e132ee7e7b761df7ca1a31d42b0f7ed4a587e04c5"
_ACCEPTED_KEYS = frozenset(
    {
        "schema",
        "status",
        "advisory_only",
        "domain",
        "model_id",
        "model_version",
        "base_revision",
        "comparison_digest",
        "settings_digest",
        "input_digest",
        "ranking_digest",
        "entries",
    }
)
_ENTRY_KEYS = frozenset({"binding", "advisory", "feature_digest"})
_BINDING_KEYS = frozenset({"candidate_id", "base_revision"})
_ADVISORY_KEYS = frozenset({"rank", "score_milli"})
_REFUSED_KEYS = frozenset({"schema", "status", "code", "diagnostic"})


def _candidate(
    label: str,
    *,
    revision: str = _REVISION,
    length_nm: int = 10_000,
    vertices: tuple[PointNM, ...] | None = None,
    seed: int = 1,
    context_label: str | None = None,
) -> RouteCandidate:
    """Build a real RouteCandidate and stamp its canonical content address."""

    settings = AStarSettings(
        grid_step_nm=1_000,
        bend_penalty_nm=500,
        proximity_penalty_nm=50,
        max_grid_nodes=250_000,
        max_expansions=100_000,
        max_obstacles=128,
        max_net_objects=128,
        region_margin_nm=10_000_000,
        max_obstacle_checks=2_000_000,
    )
    path = RoutePath(
        vertices=vertices or (PointNM(0, 0), PointNM(length_nm, 0)),
    )
    patch = RoutePatch(
        net_id=f"net:{context_label or label}",
        layer_id="layer:F.Cu",
        width_nm=200,
        paths=(path,),
    )
    cost = RouteCost(
        length_nm=path.length_nm,
        bend_count=path.bend_count,
        bend_cost_nm=path.bend_count * settings.bend_penalty_nm,
        proximity_steps=0,
        proximity_cost_nm=0,
        via_cost_nm=0,
        total_cost_nm=path.length_nm + path.bend_count * settings.bend_penalty_nm,
    )
    metrics = RouteMetrics(
        hard_internal_violations=0,
        unrouted_connections=0,
        vias=0,
        wire_length_nm=path.length_nm,
        expanded_states=1,
        peak_frontier_states=1,
        obstacle_checks=1,
    )
    provisional = RouteCandidate(
        candidate_id=_EMPTY_DIGEST,
        base_revision=revision,
        start_pad_id=f"pad:{context_label or label}:start",
        end_pad_id=f"pad:{context_label or label}:end",
        patch=patch,
        cost=cost,
        metrics=metrics,
        settings=settings,
        router_version="astar-grid/0.7.0",
        policy="orthogonal-a-star-v1",
        seed=seed,
    )
    candidate = replace(
        provisional,
        candidate_id=f"sha256:{hashlib.sha256(canonical_candidate_bytes(provisional)).hexdigest()}",
    )
    assert verify_candidate_id(candidate)
    return candidate


def _zigzag_vertices(count: int) -> tuple[PointNM, ...]:
    points = [PointNM(0, 0)]
    x = 0
    y = 0
    for index in range(1, count):
        if index % 2:
            x += 1
        else:
            y += 1
        points.append(PointNM(x, y))
    return tuple(points)


def _forged_dataclass(value: object, **changes: object) -> object:
    """Clone a frozen-slotted dataclass without running its validator."""

    forged = object.__new__(type(value))
    for field in fields(value):
        object.__setattr__(forged, field.name, changes.get(field.name, getattr(value, field.name)))
    return forged


def _restamp(candidate: RouteCandidate, **changes: object) -> RouteCandidate:
    forged = cast(RouteCandidate, _forged_dataclass(candidate, **changes))
    object.__setattr__(forged, "candidate_id", _EMPTY_DIGEST)
    object.__setattr__(
        forged,
        "candidate_id",
        f"sha256:{hashlib.sha256(canonical_candidate_bytes(forged)).hexdigest()}",
    )
    assert verify_candidate_id(forged)
    return forged


def _subclass_candidate(candidate: RouteCandidate) -> RouteCandidate:
    subclass = type("RouteCandidateSubclass", (RouteCandidate,), {})
    forged = object.__new__(subclass)
    for field in fields(candidate):
        object.__setattr__(forged, field.name, getattr(candidate, field.name))
    object.__setattr__(forged, "candidate_id", _EMPTY_DIGEST)
    object.__setattr__(
        forged,
        "candidate_id",
        f"sha256:{hashlib.sha256(canonical_candidate_bytes(forged)).hexdigest()}",
    )
    return forged


def _accepted(result: object) -> SurrogateRankingAccepted:
    assert isinstance(result, SurrogateRankingAccepted)
    assert result.status == "accepted"
    assert result.advisory_only is True
    return result


def _refused(
    result: object, expected_code: SurrogateRankingCode | None = None
) -> SurrogateRankingRefused:
    assert isinstance(result, SurrogateRankingRefused)
    assert result.status == "refused"
    payload = result.to_dict()
    assert set(payload) == _REFUSED_KEYS
    if expected_code is not None:
        assert type(result.code) is SurrogateRankingCode
        assert result.code is expected_code
        assert payload["code"] == expected_code.value
    return result


def _digest(value: object) -> str:
    assert type(value) is str
    assert value.startswith("sha256:")
    assert len(value) == 71
    assert all(character in "0123456789abcdef" for character in value[7:])
    return value


def test_rank_is_deterministic_across_reordered_input_and_digest_fields_are_stable() -> None:
    candidates = tuple(
        _candidate(label, context_label="shared", seed=index)
        for index, label in enumerate(("alpha", "beta", "gamma"), start=1)
    )
    first = _accepted(rank_surrogate_candidates(candidates, SignoffDomain.SI))
    reordered = _accepted(rank_surrogate_candidates(tuple(reversed(candidates)), SignoffDomain.SI))

    assert first == reordered
    assert first.to_dict() == reordered.to_dict()
    assert first.entries == reordered.entries
    for name in ("settings_digest", "input_digest", "ranking_digest"):
        assert _digest(getattr(first, name)) == getattr(reordered, name)
    for index, first_entry in enumerate(first.entries):
        reordered_entry = reordered.entries[index]
        assert first_entry.feature_digest == reordered_entry.feature_digest
        assert _digest(first_entry.feature_digest)


def test_fixed_candidate_and_domain_pin_source_owned_digest_literals() -> None:
    candidate = _candidate("golden", length_nm=10_000)
    result = _accepted(rank_surrogate_candidates((candidate,), SignoffDomain.THERMAL))
    entry = result.entries[0]

    assert candidate.candidate_id == _GOLDEN_CANDIDATE_ID
    assert result.comparison_digest == _GOLDEN_COMPARISON_DIGEST
    assert result.settings_digest == _GOLDEN_SETTINGS_DIGEST
    assert result.input_digest == _GOLDEN_INPUT_DIGEST
    assert entry.feature_digest == _GOLDEN_FEATURE_DIGEST
    assert result.ranking_digest == _GOLDEN_RANKING_DIGEST


def test_score_ordering_and_identity_tie_break_are_explicit() -> None:
    candidates = (
        _candidate("long", length_nm=20_000, context_label="shared", seed=1),
        _candidate("tie-b", length_nm=10_000, context_label="shared", seed=2),
        _candidate("tie-a", length_nm=10_000, context_label="shared", seed=3),
    )
    result = _accepted(rank_surrogate_candidates(candidates, SignoffDomain.PI))
    entries = result.entries
    scores = [entry.advisory.score_milli for entry in entries]

    assert scores == sorted(scores, reverse=True)
    assert [entry.advisory.rank for entry in entries] == [1, 2, 3]
    for left, right in pairwise(entries):
        if left.advisory.score_milli == right.advisory.score_milli:
            assert left.binding.candidate_id < right.binding.candidate_id
    assert all(type(entry.advisory) is SurrogateAdvisory for entry in entries)
    assert all(type(entry.binding) is CandidateBinding for entry in entries)


def test_score_formula_pins_rounding_and_cap_boundaries() -> None:
    cases = (
        ("score-1000", 1_000, -1),
        ("score-1001", 1_001, -2),
        ("score-cap", 1_000_000_000, -1_000_000),
        ("score-above-cap", 1_000_000_001, -1_000_000),
    )
    candidates = tuple(
        _candidate(label, length_nm=length, context_label="shared") for label, length, _ in cases
    )
    result = _accepted(rank_surrogate_candidates(candidates, SignoffDomain.SI))
    scores = {entry.binding.candidate_id: entry.advisory.score_milli for entry in result.entries}

    for (label, _length, expected), candidate in zip(cases, candidates, strict=True):
        assert scores[candidate.candidate_id] == expected, label


@pytest.mark.parametrize("domain", tuple(SignoffDomain))
def test_every_domain_is_advisory_and_signoff_without_evidence_is_non_claim(
    domain: SignoffDomain,
) -> None:
    result = _accepted(rank_surrogate_candidates((_candidate("bridge"),), domain))
    entry = result.entries[0]
    assert entry.advisory.rank == 1

    signoff = evaluate_authoritative_signoff(
        entry.binding,
        domain,
        surrogate=entry.advisory,
    )
    assert signoff.status is SignoffStatus.NON_CLAIM
    assert signoff.code is SignoffCode.SURROGATE_ONLY
    assert signoff.claimed is False


def test_accepted_entry_and_result_payloads_have_exact_closed_key_sets() -> None:
    result = _accepted(rank_surrogate_candidates((_candidate("payload"),), SignoffDomain.THERMAL))
    payload = result.to_dict()
    assert set(payload) == _ACCEPTED_KEYS
    assert isinstance(payload["entries"], list)
    assert len(payload["entries"]) == 1
    assert set(payload["entries"][0]) == _ENTRY_KEYS
    assert set(payload["entries"][0]["binding"]) == _BINDING_KEYS
    assert set(payload["entries"][0]["advisory"]) == _ADVISORY_KEYS
    for name in (
        "settings_digest",
        "input_digest",
        "ranking_digest",
        "base_revision",
    ):
        _digest(getattr(result, name))
    _digest(payload["entries"][0]["feature_digest"])


def test_refused_payload_is_closed_and_does_not_echo_untrusted_input() -> None:
    result = _refused(
        rank_surrogate_candidates(
            {"prompt": "private-design-secret", "candidates": []},
            SignoffDomain.DFM,
        )
    )
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    assert "private-design-secret" not in serialized
    assert "prompt" not in serialized
    assert "candidates" not in serialized


def test_candidate_result_entry_and_advisory_contracts_remain_immutable() -> None:
    candidate = _candidate("immutable")
    before = candidate
    with pytest.raises(FrozenInstanceError):
        candidate.seed = 2  # type: ignore[misc]
    result = _accepted(rank_surrogate_candidates((candidate,), SignoffDomain.SI))
    assert candidate == before
    assert result.entries[0].binding == CandidateBinding(
        candidate_id=candidate.candidate_id,
        base_revision=candidate.base_revision,
    )
    with pytest.raises(FrozenInstanceError):
        result.entries = ()  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        result.entries[0].advisory.rank = 99  # type: ignore[misc]


def test_exact_batch_bound_accepts_32_and_refuses_33_without_partial_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    one = _accepted(rank_surrogate_candidates((_candidate("batch-one"),), SignoffDomain.DFM))
    assert len(one.entries) == 1

    empty = _refused(rank_surrogate_candidates((), SignoffDomain.DFM))
    assert empty.to_dict()["status"] == "refused"

    thirty_two = tuple(
        _candidate(f"batch{index:02d}", context_label="shared", seed=index + 1)
        for index in range(32)
    )
    accepted = _accepted(rank_surrogate_candidates(thirty_two, SignoffDomain.DFM))
    assert len(accepted.entries) == 32
    assert [entry.advisory.rank for entry in accepted.entries] == list(range(1, 33))

    thirty_three = (*thirty_two, _candidate("batch32", context_label="shared", seed=33))
    monkeypatch.setattr(
        surrogate_ranking,
        "_aggregate_vertex_count",
        lambda _: pytest.fail("33-candidate refusal must precede aggregate preflight"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_reconstruct_candidate",
        lambda _: pytest.fail("33-candidate refusal must precede reconstruction"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "verify_candidate_id",
        lambda _: pytest.fail("33-candidate refusal must precede candidate hashing"),
    )
    refused = _refused(rank_surrogate_candidates(thirty_three, SignoffDomain.DFM))
    assert refused.code is SurrogateRankingCode.INVALID_INPUT
    assert "entries" not in refused.to_dict()


def test_aggregate_vertex_bound_is_exact_and_overflow_is_rejected_before_id_verification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boundary = (
        _candidate(
            "vertex-boundary-a", vertices=_zigzag_vertices(8_192), context_label="shared", seed=1
        ),
        _candidate(
            "vertex-boundary-b", vertices=_zigzag_vertices(8_192), context_label="shared", seed=2
        ),
    )
    accepted = _accepted(rank_surrogate_candidates(boundary, SignoffDomain.SI))
    assert len(accepted.entries) == 2

    overflow = (
        _candidate("vertex-overflow-a", vertices=_zigzag_vertices(8_192)),
        _candidate("vertex-overflow-b", vertices=_zigzag_vertices(8_193)),
    )
    verify_calls = 0

    def should_not_verify(_: object) -> bool:
        nonlocal verify_calls
        verify_calls += 1
        pytest.fail("aggregate vertex refusal must precede candidate-ID verification")

    monkeypatch.setattr(surrogate_ranking, "verify_candidate_id", should_not_verify)
    _refused(
        rank_surrogate_candidates(overflow, SignoffDomain.SI),
        SurrogateRankingCode.VERTEX_BUDGET_EXCEEDED,
    )
    assert verify_calls == 0


def test_route_patch_subclass_is_rejected_without_touching_its_paths_accessor() -> None:
    candidate = _candidate("hostile-patch-subclass")
    accessor_calls = 0

    class RaisingPathsPatch(RoutePatch):
        @property
        def paths(self) -> tuple[RoutePath, ...]:
            nonlocal accessor_calls
            accessor_calls += 1
            raise AssertionError("hostile paths accessor must not run")

    hostile_patch = object.__new__(RaisingPathsPatch)
    object.__setattr__(hostile_patch, "net_id", candidate.patch.net_id)
    object.__setattr__(hostile_patch, "layer_id", candidate.patch.layer_id)
    object.__setattr__(hostile_patch, "width_nm", candidate.patch.width_nm)
    forged = cast(
        RouteCandidate,
        _forged_dataclass(
            candidate,
            patch=hostile_patch,
            candidate_id=_EMPTY_DIGEST,
        ),
    )

    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )
    assert accessor_calls == 0


def test_vertex_element_tuple_overflow_is_rejected_before_element_inspection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = _candidate("hostile-path-elements")
    hostile_paths = (object(),) * 8_193
    hostile_patch = cast(
        RoutePatch,
        _forged_dataclass(candidate.patch, paths=hostile_paths),
    )
    forged = cast(
        RouteCandidate,
        _forged_dataclass(
            candidate,
            patch=hostile_patch,
            candidate_id=_EMPTY_DIGEST,
        ),
    )

    monkeypatch.setattr(
        surrogate_ranking,
        "verify_candidate_id",
        lambda _: pytest.fail("vertex preflight must precede candidate hashing"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_reconstruct_candidate",
        lambda _: pytest.fail("vertex preflight must precede reconstruction"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_feature_digest",
        lambda *_args, **_kwargs: pytest.fail("vertex preflight must precede feature extraction"),
    )

    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.VERTEX_BUDGET_EXCEEDED,
    )


@pytest.mark.parametrize("container", (None, [], {}, "not-a-tuple", frozenset()))
def test_candidates_container_must_be_exactly_an_immutable_tuple(container: object) -> None:
    _refused(rank_surrogate_candidates(container, SignoffDomain.SI))


def test_tuple_subclass_is_not_an_admitted_candidate_container() -> None:
    candidate = _candidate("tuple-subclass")
    subclass = type("CandidateTupleSubclass", (tuple,), {})((candidate,))
    _refused(rank_surrogate_candidates(subclass, SignoffDomain.SI))


@pytest.mark.parametrize("domain", (None, "si", object()))
def test_domain_must_be_a_supported_exact_signoff_domain(domain: object) -> None:
    _refused(rank_surrogate_candidates((_candidate("domain"),), domain))


def test_exact_route_candidate_type_is_required() -> None:
    candidate = _candidate("exact-type")
    subclass = _subclass_candidate(candidate)
    assert type(subclass) is not RouteCandidate
    _refused(rank_surrogate_candidates((subclass,), SignoffDomain.SI))


def test_tampered_id_duplicate_and_mixed_revision_inputs_are_refused() -> None:
    candidate = _candidate("identity")
    tampered = replace(candidate, candidate_id=f"sha256:{'f' * 64}")
    _refused(rank_surrogate_candidates((tampered,), SignoffDomain.SI))
    _refused(rank_surrogate_candidates((candidate, candidate), SignoffDomain.SI))
    mixed = _candidate("other-revision", revision=_OTHER_REVISION)
    _refused(rank_surrogate_candidates((candidate, mixed), SignoffDomain.SI))


@pytest.mark.parametrize("field", ("net", "endpoints", "fill", "policy"))
def test_candidates_with_one_route_problem_context_drift_are_incomparable(field: str) -> None:
    first = _candidate("context-a", context_label="shared", seed=1)
    changes: dict[str, object] = {}
    if field == "net":
        changes["patch"] = replace(first.patch, net_id="net:other")
    elif field == "endpoints":
        changes.update(start_pad_id="pad:other:start", end_pad_id="pad:other:end")
    elif field == "fill":
        changes["fill_binding"] = f"sha256:{'c' * 64}"
    else:
        changes["policy"] = "other-policy-v1"
    second = _restamp(first, **changes)
    refused = _refused(
        rank_surrogate_candidates((first, second), SignoffDomain.SI),
        SurrogateRankingCode.INCOMPARABLE_CANDIDATES,
    )
    assert refused.diagnostic == "surrogate ranking candidates are not comparable"


@pytest.mark.parametrize("field", ("region_margin_nm", "max_net_objects"))
def test_candidates_with_different_settings_context_are_incomparable(field: str) -> None:
    first = _candidate("settings-a", context_label="shared")
    changed_settings = replace(first.settings, **{field: getattr(first.settings, field) + 1})
    second = _restamp(first, settings=changed_settings)
    _refused(
        rank_surrogate_candidates((first, second), SignoffDomain.SI),
        SurrogateRankingCode.INCOMPARABLE_CANDIDATES,
    )


def test_same_context_allows_seed_and_geometry_variations() -> None:
    first = _candidate("seed-a", context_label="shared", seed=1)
    second = _candidate(
        "seed-b",
        context_label="shared",
        seed=2,
        vertices=(PointNM(0, 0), PointNM(5_000, 0), PointNM(5_000, 5_000)),
    )
    result = _accepted(rank_surrogate_candidates((first, second), SignoffDomain.SI))
    assert len(result.entries) == 2
    single = _accepted(rank_surrogate_candidates((first,), SignoffDomain.SI))
    assert result.comparison_digest == single.comparison_digest


def test_every_refusal_family_has_an_exact_typed_code() -> None:
    candidate = _candidate("refusal-codes")
    tampered = replace(candidate, candidate_id=f"sha256:{'f' * 64}")
    mixed = _candidate("refusal-mixed", revision=_OTHER_REVISION)
    overflow = _candidate("refusal-overflow", vertices=_zigzag_vertices(16_385))

    cases = (
        (rank_surrogate_candidates((), SignoffDomain.SI), SurrogateRankingCode.INVALID_INPUT),
        (rank_surrogate_candidates((candidate,), None), SurrogateRankingCode.INVALID_DOMAIN),
        (
            rank_surrogate_candidates((tampered,), SignoffDomain.SI),
            SurrogateRankingCode.INVALID_CANDIDATE,
        ),
        (
            rank_surrogate_candidates((candidate, candidate), SignoffDomain.SI),
            SurrogateRankingCode.DUPLICATE_CANDIDATE,
        ),
        (
            rank_surrogate_candidates((candidate, mixed), SignoffDomain.SI),
            SurrogateRankingCode.MIXED_BASE_REVISION,
        ),
        (
            rank_surrogate_candidates((overflow,), SignoffDomain.SI),
            SurrogateRankingCode.VERTEX_BUDGET_EXCEEDED,
        ),
        (
            rank_surrogate_candidates((candidate,), SignoffDomain.SI, cancelled=lambda: True),
            SurrogateRankingCode.CANCELLED,
        ),
        (
            rank_surrogate_candidates((candidate,), SignoffDomain.SI, deadline=lambda: True),
            SurrogateRankingCode.DEADLINE_EXCEEDED,
        ),
    )
    for result, expected_code in cases:
        _refused(result, expected_code)


@pytest.mark.parametrize("malformed", (True, 1 << 60))
def test_forged_bool_or_huge_metric_fields_are_refused(malformed: object) -> None:
    candidate = _candidate("malformed")
    metrics = _forged_dataclass(candidate.metrics, wire_length_nm=malformed)
    forged = _restamp(candidate, metrics=metrics)
    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )


def test_forged_empty_path_is_rejected_after_the_tuple_boundary() -> None:
    candidate = _candidate("empty-path")
    patch = cast(RoutePatch, _forged_dataclass(candidate.patch, paths=()))
    forged = _restamp(candidate, patch=patch)
    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )


@pytest.mark.parametrize("vertices", ((), (PointNM(0, 0),)))
def test_short_exact_route_path_is_rejected_before_identity_or_feature_work(
    vertices: tuple[PointNM, ...], monkeypatch: pytest.MonkeyPatch
) -> None:
    candidate = _candidate("short-route-path")
    path = cast(
        RoutePath,
        _forged_dataclass(candidate.patch.paths[0], vertices=vertices),
    )
    patch = cast(RoutePatch, _forged_dataclass(candidate.patch, paths=(path,)))
    forged = cast(
        RouteCandidate,
        _forged_dataclass(
            candidate,
            patch=patch,
            candidate_id=_EMPTY_DIGEST,
        ),
    )
    assert type(forged.patch) is RoutePatch
    assert forged.patch.paths
    assert len(forged.patch.paths[0].vertices) < 2

    monkeypatch.setattr(
        surrogate_ranking,
        "verify_candidate_id",
        lambda _: pytest.fail("short-path preflight must precede candidate hashing"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_reconstruct_candidate",
        lambda _: pytest.fail("short-path preflight must precede reconstruction"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_feature_digest",
        lambda *_args, **_kwargs: pytest.fail(
            "short-path preflight must precede feature extraction"
        ),
    )

    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )


@pytest.mark.parametrize("coordinate", (True, 1 << 60))
def test_forged_bool_or_unsafe_point_coordinates_are_rejected(coordinate: object) -> None:
    candidate = _candidate("unsafe-point")
    point = cast(PointNM, _forged_dataclass(PointNM(0, 0), x=coordinate))
    path = cast(
        RoutePath,
        _forged_dataclass(candidate.patch.paths[0], vertices=(point, PointNM(1, 0))),
    )
    patch = cast(RoutePatch, _forged_dataclass(candidate.patch, paths=(path,)))
    forged = _restamp(candidate, patch=patch)
    _refused(
        rank_surrogate_candidates((forged,), SignoffDomain.SI),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )


def test_forged_cost_geometry_metrics_and_settings_drift_are_rejected() -> None:
    candidate = _candidate("cost-drift")
    drifted_cost = cast(
        RouteCost,
        _forged_dataclass(
            candidate.cost,
            length_nm=candidate.cost.length_nm + 1,
            total_cost_nm=candidate.cost.total_cost_nm + 1,
        ),
    )
    _refused(
        rank_surrogate_candidates(
            (_restamp(candidate, cost=drifted_cost),),
            SignoffDomain.SI,
        ),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )

    drifted_metrics = cast(
        RouteMetrics,
        _forged_dataclass(
            candidate.metrics,
            wire_length_nm=candidate.metrics.wire_length_nm + 1,
        ),
    )
    _refused(
        rank_surrogate_candidates(
            (_restamp(candidate, metrics=drifted_metrics),),
            SignoffDomain.SI,
        ),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )

    bent = _candidate(
        "settings-drift",
        vertices=(PointNM(0, 0), PointNM(1, 0), PointNM(1, 1)),
    )
    drifted_settings = cast(
        AStarSettings,
        _forged_dataclass(
            bent.settings,
            bend_penalty_nm=bent.settings.bend_penalty_nm + 1,
        ),
    )
    _refused(
        rank_surrogate_candidates(
            (_restamp(bent, settings=drifted_settings),),
            SignoffDomain.SI,
        ),
        SurrogateRankingCode.INVALID_CANDIDATE,
    )


def test_entry_requires_an_exact_feature_digest() -> None:
    result = _accepted(rank_surrogate_candidates((_candidate("entry-digest"),), SignoffDomain.SI))
    entry = result.entries[0]
    reconstructed = SurrogateRankingEntry(
        binding=entry.binding,
        advisory=entry.advisory,
        feature_digest=entry.feature_digest,
    )
    assert reconstructed == entry
    with pytest.raises(ValueError, match="feature digest"):
        SurrogateRankingEntry(
            binding=entry.binding,
            advisory=entry.advisory,
            feature_digest=f"sha256:{'0' * 64}",
        )


@pytest.mark.parametrize("keyword", ("model", "weights", "backend"))
def test_model_backend_and_weight_injection_are_not_arguments(keyword: str) -> None:
    with pytest.raises(TypeError):
        rank_surrogate_candidates(
            (_candidate("injection"),),
            SignoffDomain.SI,
            **{keyword: object()},
        )


def test_cancellation_before_work_refuses_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(
        surrogate_ranking,
        "_reconstruct_candidate",
        lambda _: pytest.fail("cancellation must precede candidate reconstruction"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "_aggregate_vertex_count",
        lambda _: pytest.fail("cancellation must precede aggregate preflight"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "verify_candidate_id",
        lambda _: pytest.fail("cancellation must precede candidate verification"),
    )
    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("cancel-before"),), SignoffDomain.SI, cancelled=cancelled
        ),
        SurrogateRankingCode.CANCELLED,
    )
    assert calls == 1
    assert "entries" not in refused.to_dict()


def test_deadline_before_work_refuses_atomically(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def deadline() -> bool:
        nonlocal calls
        calls += 1
        return True

    monkeypatch.setattr(
        surrogate_ranking,
        "_reconstruct_candidate",
        lambda _: pytest.fail("deadline must precede candidate reconstruction"),
    )
    monkeypatch.setattr(
        surrogate_ranking,
        "verify_candidate_id",
        lambda _: pytest.fail("deadline must precede candidate verification"),
    )
    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("deadline-before"),), SignoffDomain.SI, deadline=deadline
        ),
        SurrogateRankingCode.DEADLINE_EXCEEDED,
    )
    assert calls == 1
    assert "entries" not in refused.to_dict()


def test_mid_loop_cancellation_discards_partial_rankings() -> None:
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("mid-a"), _candidate("mid-b"), _candidate("mid-c")),
            SignoffDomain.SI,
            cancelled=cancelled,
        ),
        SurrogateRankingCode.CANCELLED,
    )
    assert calls == 3
    assert "entries" not in refused.to_dict()


def test_mid_loop_deadline_discards_partial_rankings() -> None:
    calls = 0

    def deadline() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 3

    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("mid-deadline-a"), _candidate("mid-deadline-b")),
            SignoffDomain.SI,
            deadline=deadline,
        ),
        SurrogateRankingCode.DEADLINE_EXCEEDED,
    )
    assert calls == 3
    assert "entries" not in refused.to_dict()


def test_post_feature_deadline_checkpoint_is_fourth_and_atomic() -> None:
    calls = 0

    def deadline() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 4

    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("deadline-post-feature"),), SignoffDomain.SI, deadline=deadline
        ),
        SurrogateRankingCode.DEADLINE_EXCEEDED,
    )
    assert calls == 4
    assert "entries" not in refused.to_dict()


def test_cancellation_at_final_publication_discards_the_complete_ranking() -> None:
    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 5

    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("cancel-after"),), SignoffDomain.SI, cancelled=cancelled
        ),
        SurrogateRankingCode.CANCELLED,
    )
    assert calls == 5
    assert "entries" not in refused.to_dict()


@pytest.mark.parametrize(
    ("callback_name", "callback"),
    [
        ("cancelled", lambda: (_ for _ in ()).throw(RuntimeError("callback-secret"))),
        ("deadline", lambda: (_ for _ in ()).throw(RuntimeError("callback-secret"))),
        ("cancelled", lambda: "not-a-bool"),
        ("deadline", lambda: 1),
    ],
)
def test_stop_callback_exceptions_and_non_bool_results_are_fixed_refusals(
    callback_name: str, callback: object
) -> None:
    expected_code = (
        SurrogateRankingCode.CANCELLED
        if callback_name == "cancelled"
        else SurrogateRankingCode.DEADLINE_EXCEEDED
    )
    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("callback-hostile"),),
            SignoffDomain.SI,
            **{callback_name: callback},
        ),
        expected_code,
    )
    serialized = json.dumps(refused.to_dict(), sort_keys=True)
    assert "callback-secret" not in serialized
    assert "not-a-bool" not in serialized


@pytest.mark.parametrize("outcome", ("true", "raises", "non-bool"))
def test_cancellation_precedes_deadline_for_every_cancelled_outcome(
    outcome: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    cancellation_calls = 0
    deadline_calls = 0

    def cancelled() -> object:
        nonlocal cancellation_calls
        cancellation_calls += 1
        if outcome == "raises":
            raise RuntimeError("cancel-secret")
        if outcome == "non-bool":
            return "not-a-bool"
        return True

    def deadline() -> bool:
        nonlocal deadline_calls
        deadline_calls += 1
        return True

    monkeypatch.setattr(
        surrogate_ranking,
        "_aggregate_vertex_count",
        lambda _: pytest.fail("cancellation must precede all ranking work"),
    )
    refused = _refused(
        rank_surrogate_candidates(
            (_candidate("cancel-before-deadline"),),
            SignoffDomain.SI,
            cancelled=cancelled,
            deadline=deadline,
        ),
        SurrogateRankingCode.CANCELLED,
    )
    assert cancellation_calls == 1
    assert deadline_calls == 0
    assert "entries" not in refused.to_dict()
    assert "cancel-secret" not in json.dumps(refused.to_dict(), sort_keys=True)


def test_public_signature_is_closed_and_keeps_stop_checks_keyword_only() -> None:
    signature = inspect.signature(rank_surrogate_candidates)
    parameters = tuple(signature.parameters.values())
    assert tuple(parameter.name for parameter in parameters) == (
        "candidates",
        "domain",
        "cancelled",
        "deadline",
    )
    assert parameters[0].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[1].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    assert parameters[2].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters[3].kind is inspect.Parameter.KEYWORD_ONLY


def test_output_does_not_leak_candidate_geometry_or_private_input() -> None:
    candidate = _candidate("private-design-secret")
    result = _accepted(rank_surrogate_candidates((candidate,), SignoffDomain.DFM))
    serialized = json.dumps(result.to_dict(), sort_keys=True)
    for private in (
        "private-design-secret",
        "net:private-design-secret",
        "pad:private-design-secret:start",
        "vertices",
        "paths",
        "patch",
        "geometry",
        "board",
        "prompt",
        "token",
        "backend",
        "drc",
    ):
        assert private not in serialized


def test_ranking_module_has_static_import_isolation_and_no_public_root_export() -> None:
    source_path = Path(surrogate_ranking.__file__).resolve()
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    imported_modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            imported_modules.append(node.module)
    forbidden = {
        "mcp",
        "cli",
        "tools",
        "apply",
        "drc",
        "backend",
        "network",
        "subprocess",
        "filesystem",
    }
    for module in imported_modules:
        assert not any(term in module.lower() for term in forbidden), module

    import copper_mcp.routing as routing

    assert "rank_surrogate_candidates" not in vars(routing)
    assert "SurrogateRankingAccepted" not in vars(routing)
    assert "SurrogateRankingRefused" not in vars(routing)
