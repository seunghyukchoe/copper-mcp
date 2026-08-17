"""Focused tests for the deferred, private authoritative-signoff core seam."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from copper_mcp.routing.authoritative_signoff import (
    _RESULT_CAPABILITY,
    AuthoritativeEvidence,
    AuthoritativeSignoffResult,
    CandidateBinding,
    SignoffCode,
    SignoffDomain,
    SignoffStatus,
    SurrogateAdvisory,
    _validate_evidence_binding,
    evaluate_authoritative_signoff,
    parse_authoritative_evidence,
    parse_candidate_binding,
    parse_surrogate_advisory,
)


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _candidate(*, candidate: str = "a", revision: str = "b") -> CandidateBinding:
    return CandidateBinding(candidate_id=_digest(candidate), base_revision=_digest(revision))


def _evidence(
    candidate: CandidateBinding,
    *,
    domain: SignoffDomain = SignoffDomain.THERMAL,
    backend_id: str = "copper-mcp-authoritative-v1",
    backend_version: str = "1",
    evidence_revision: str = "c",
    completed: bool = True,
    passed: bool = True,
) -> AuthoritativeEvidence:
    return AuthoritativeEvidence(
        backend_id=backend_id,
        backend_version=backend_version,
        domain=domain,
        candidate_id=candidate.candidate_id,
        base_revision=candidate.base_revision,
        authoritative_output=(
            f"authoritative-output:{evidence_revision}:{domain.value}:{completed}:{passed}".encode()
        ),
        completed=completed,
        passed=passed,
    )


def test_missing_authority_is_deterministic_non_claim_and_surrogate_stays_advisory() -> None:
    candidate = _candidate()

    absent = evaluate_authoritative_signoff(candidate, SignoffDomain.SI)
    surrogate = evaluate_authoritative_signoff(
        candidate,
        SignoffDomain.SI,
        surrogate=SurrogateAdvisory(rank=1, score_milli=975),
    )

    assert absent == evaluate_authoritative_signoff(candidate, "si")
    assert absent.status is SignoffStatus.NON_CLAIM
    assert absent.code is SignoffCode.NO_AUTHORITATIVE_BACKEND
    assert surrogate.status is SignoffStatus.NON_CLAIM
    assert surrogate.code is SignoffCode.SURROGATE_ONLY
    assert surrogate.claimed is False
    assert surrogate.to_dict()["advisory_present"] is True


def test_caller_selected_backend_is_blocked_without_invocation() -> None:
    candidate = _candidate()
    calls = 0

    def mint(*args: object, **kwargs: object) -> object:
        nonlocal calls
        calls += 1
        return _evidence(candidate, domain=SignoffDomain.SI)

    result = evaluate_authoritative_signoff(candidate, SignoffDomain.SI, backend=mint)

    assert result.status is SignoffStatus.REFUSED
    assert result.code is SignoffCode.INVALID_BACKEND
    assert result.claimed is False
    assert calls == 0


def test_test_backend_injection_is_not_an_evaluation_api() -> None:
    candidate = _candidate()
    with pytest.raises(TypeError):
        evaluate_authoritative_signoff(  # type: ignore[call-arg]
            candidate,
            SignoffDomain.SI,
            test_backend=lambda *_args: _evidence(candidate, domain=SignoffDomain.SI),
        )


def test_result_cannot_be_directly_constructed_as_signed_off() -> None:
    candidate = _candidate()
    with pytest.raises(TypeError):
        AuthoritativeSignoffResult(
            status=SignoffStatus.SIGNED_OFF,
            domain=SignoffDomain.SI,
            candidate_id=candidate.candidate_id,
            base_revision=candidate.base_revision,
            backend_id="copper-mcp-authoritative-v1",
            evidence_digest=_digest("e"),
        )


def test_private_capability_cannot_mint_a_claim_before_authority_exists() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="claims are deferred"):
        AuthoritativeSignoffResult._create(
            capability=_RESULT_CAPABILITY,
            status=SignoffStatus.SIGNED_OFF,
            domain=SignoffDomain.SI,
            candidate_id=candidate.candidate_id,
            base_revision=candidate.base_revision,
            backend_id="copper-mcp-authoritative-v1",
            evidence_digest=_digest("e"),
        )


def test_evidence_is_closed_content_bound_and_never_serializes_private_output() -> None:
    candidate = _candidate()
    first = _evidence(candidate)
    second = _evidence(candidate)
    assert first.evidence_revision == second.evidence_revision
    assert first.evidence_digest == second.evidence_digest
    assert first.to_dict() == second.to_dict()
    serialized = json.dumps(first.to_dict(), sort_keys=True)
    for private in ("coordinates", "board_bytes", "prompt", "token", "mutation", "geometry"):
        assert private not in serialized
    assert parse_authoritative_evidence(first.to_dict()) is None


def test_altered_authoritative_output_changes_content_revision_and_digest() -> None:
    candidate = _candidate()
    original = _evidence(candidate, evidence_revision="original")
    altered = replace(original, authoritative_output=b"altered-authoritative-output")

    assert altered.evidence_revision != original.evidence_revision
    assert altered.evidence_digest != original.evidence_digest


@pytest.mark.parametrize(
    ("expected_code", "evidence"),
    [
        (SignoffCode.CANDIDATE_MISMATCH, _evidence(_candidate(candidate="d"))),
        (SignoffCode.STALE_REVISION, _evidence(_candidate(revision="e"))),
        (
            SignoffCode.BACKEND_MISMATCH,
            _evidence(_candidate(), backend_id="unregistered-backend"),
        ),
        (SignoffCode.INCOMPLETE_EVIDENCE, _evidence(_candidate(), completed=False)),
        (SignoffCode.FAILED_EVIDENCE, _evidence(_candidate(), passed=False)),
    ],
)
def test_pure_evidence_binding_validation_is_not_an_execution_path(
    expected_code: SignoffCode, evidence: AuthoritativeEvidence
) -> None:
    candidate = _candidate()
    assert _validate_evidence_binding(candidate, SignoffDomain.THERMAL, evidence) is expected_code


def test_pure_evidence_binding_detects_altered_content_digest() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)
    altered = replace(evidence, authoritative_output=b"altered-authoritative-output")

    assert (
        _validate_evidence_binding(
            candidate,
            SignoffDomain.THERMAL,
            altered,
            expected_digest=evidence.evidence_digest,
        )
        is SignoffCode.EVIDENCE_MISMATCH
    )


@pytest.mark.parametrize(("completed", "passed"), [(False, True), (True, False)])
def test_evidence_vocabulary_records_non_claimable_states(completed: bool, passed: bool) -> None:
    evidence = _evidence(_candidate(), completed=completed, passed=passed)
    assert evidence.completed is completed
    assert evidence.passed is passed
    assert evidence.evidence_revision.startswith("sha256:")


def test_cancellation_and_deadline_are_cooperative_and_redacted() -> None:
    candidate = _candidate()
    cancelled = evaluate_authoritative_signoff(candidate, SignoffDomain.SI, cancelled=lambda: True)
    deadline = evaluate_authoritative_signoff(candidate, SignoffDomain.SI, deadline=lambda: True)

    assert cancelled.code is SignoffCode.CANCELLED
    assert deadline.code is SignoffCode.DEADLINE_EXCEEDED
    assert cancelled.claimed is False
    assert deadline.claimed is False


def test_closed_parsers_reject_unknown_keys_hostile_types_and_bounds() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)
    candidate_doc = candidate.to_dict()
    candidate_doc["geometry"] = "secret"
    evidence_doc = evidence.to_dict()
    evidence_doc["token"] = "secret"

    assert parse_candidate_binding(candidate_doc) is None
    assert (
        parse_candidate_binding({"candidate_id": True, "base_revision": candidate.base_revision})
        is None
    )
    assert parse_authoritative_evidence(evidence_doc) is None
    assert parse_authoritative_evidence({"schema": "bad"}) is None
    assert parse_surrogate_advisory({"rank": 1, "score_milli": 1, "prompt": "secret"}) is None
    assert parse_surrogate_advisory({"rank": 1_000_001, "score_milli": 1}) is None
    with pytest.raises(ValueError):
        SurrogateAdvisory(rank=0, score_milli=1_000_001)


def test_invalid_inputs_never_echo_or_claim() -> None:
    invalid = evaluate_authoritative_signoff(
        {"candidate_id": "secret-geometry", "base_revision": "secret-revision"},
        "unknown-secret-domain",
        object(),
    )
    assert invalid.status is SignoffStatus.REFUSED
    assert invalid.code is SignoffCode.UNSUPPORTED_DOMAIN
    assert invalid.claimed is False
    assert "secret" not in json.dumps(invalid.to_dict())
