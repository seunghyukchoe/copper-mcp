"""Focused tests for the private authoritative-signoff core seam and its closed claim path."""

from __future__ import annotations

import json
from dataclasses import replace

import pytest

from copper_mcp.routing.authoritative_signoff import (
    _EVIDENCE_CAPABILITY,
    _RESULT_CAPABILITY,
    AuthoritativeEvidence,
    AuthoritativeSignoffResult,
    CandidateBinding,
    SignoffCode,
    SignoffComparability,
    SignoffDomain,
    SignoffStatus,
    SurrogateAdvisory,
    _validate_evidence_binding,
    evaluate_authoritative_signoff,
    parse_authoritative_evidence,
    parse_candidate_binding,
    parse_surrogate_advisory,
    registered_signoff_domains,
)


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _candidate(*, candidate: str = "a", revision: str = "b") -> CandidateBinding:
    return CandidateBinding(candidate_id=_digest(candidate), base_revision=_digest(revision))


def _evidence(
    candidate: CandidateBinding,
    *,
    domain: SignoffDomain = SignoffDomain.DFM,
    backend_id: str = "copper-mcp-authoritative-v1",
    backend_version: str = "1",
    evidence_revision: str = "c",
    completed: bool = True,
    passed: bool = True,
    suppressed: bool = False,
    comparability: SignoffComparability = SignoffComparability.REPEATED_AGREEMENT,
    repetitions: int = 2,
) -> AuthoritativeEvidence:
    return AuthoritativeEvidence(
        _EVIDENCE_CAPABILITY,
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
        suppressed=suppressed,
        comparability=comparability,
        repetitions=repetitions,
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


def test_private_capability_cannot_mint_a_claim_for_an_unregistered_domain() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="unregistered backend"):
        AuthoritativeSignoffResult._create(
            capability=_RESULT_CAPABILITY,
            status=SignoffStatus.SIGNED_OFF,
            domain=SignoffDomain.SI,
            candidate_id=candidate.candidate_id,
            base_revision=candidate.base_revision,
            backend_id="copper-mcp-authoritative-v1",
            evidence_digest=_digest("e"),
            comparability=SignoffComparability.REPEATED_AGREEMENT,
            repetitions=2,
        )


def test_private_capability_cannot_mint_a_claim_from_one_invocation() -> None:
    candidate = _candidate()
    with pytest.raises(ValueError, match="repeated agreement"):
        AuthoritativeSignoffResult._create(
            capability=_RESULT_CAPABILITY,
            status=SignoffStatus.SIGNED_OFF,
            domain=SignoffDomain.DFM,
            candidate_id=candidate.candidate_id,
            base_revision=candidate.base_revision,
            backend_id="copper-mcp-authoritative-v1",
            evidence_digest=_digest("e"),
            comparability=SignoffComparability.SINGLE_INVOCATION,
            repetitions=1,
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
def test_evidence_binding_denies_a_claim_before_it_reads_the_verdict(
    expected_code: SignoffCode, evidence: AuthoritativeEvidence
) -> None:
    candidate = _candidate()
    assert _validate_evidence_binding(candidate, SignoffDomain.DFM, evidence) is expected_code


def test_evidence_binding_detects_altered_content_digest() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)
    altered = replace(evidence, authoritative_output=b"altered-authoritative-output")

    assert (
        _validate_evidence_binding(
            candidate,
            SignoffDomain.DFM,
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


def test_only_dfm_is_registered_and_the_registry_is_read_only() -> None:
    admitted = registered_signoff_domains()

    assert admitted == frozenset({SignoffDomain.DFM})
    assert isinstance(admitted, frozenset)
    # A read that hands back a mutable view would be a registration seam wearing a getter's name.
    assert registered_signoff_domains() is not admitted or admitted == registered_signoff_domains()
    for unregistered in (SignoffDomain.SI, SignoffDomain.PI, SignoffDomain.THERMAL):
        result = evaluate_authoritative_signoff(_candidate(), unregistered)
        assert result.status is SignoffStatus.NON_CLAIM
        assert result.code is SignoffCode.NO_AUTHORITATIVE_BACKEND


def test_registered_backend_evidence_earns_a_bound_redacted_claim() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)

    result = evaluate_authoritative_signoff(candidate, SignoffDomain.DFM, evidence=evidence)

    assert result.status is SignoffStatus.SIGNED_OFF
    assert result.claimed is True
    assert result.code is None
    assert result.candidate_id == candidate.candidate_id
    assert result.base_revision == candidate.base_revision
    assert result.evidence_digest == evidence.evidence_digest
    payload = result.to_dict()
    assert payload["comparability"] == SignoffComparability.REPEATED_AGREEMENT.value
    assert payload["repetitions"] == 2
    # The claim carries what it rests on and never the bytes it rests on.
    assert evidence.authoritative_output.decode() not in json.dumps(payload)


def test_a_claim_names_the_exact_candidate_and_revision_it_was_taken_over() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)

    other_candidate = evaluate_authoritative_signoff(
        _candidate(candidate="d"), SignoffDomain.DFM, evidence=evidence
    )
    other_revision = evaluate_authoritative_signoff(
        _candidate(revision="e"), SignoffDomain.DFM, evidence=evidence
    )

    assert other_candidate.code is SignoffCode.CANDIDATE_MISMATCH
    assert other_revision.code is SignoffCode.STALE_REVISION
    assert (other_candidate.claimed, other_revision.claimed) == (False, False)


def test_evidence_cannot_be_constructed_without_the_executor_capability() -> None:
    candidate = _candidate()
    for forged in (None, object(), _RESULT_CAPABILITY, "copper-mcp-authoritative-v1"):
        with pytest.raises(ValueError, match="capability is invalid"):
            AuthoritativeEvidence(
                forged,
                backend_id="copper-mcp-authoritative-v1",
                backend_version="1",
                domain=SignoffDomain.DFM,
                candidate_id=candidate.candidate_id,
                base_revision=candidate.base_revision,
                authoritative_output=b"forged-authoritative-output",
                completed=True,
                passed=True,
                suppressed=False,
                comparability=SignoffComparability.REPEATED_AGREEMENT,
                repetitions=2,
            )


def test_evidence_capability_is_not_exported_through_supported_routing_surface() -> None:
    import copper_mcp.routing as routing

    assert "_EVIDENCE_CAPABILITY" not in vars(routing)


@pytest.mark.parametrize(
    ("comparability", "repetitions"),
    [
        (SignoffComparability.SINGLE_INVOCATION, 1),
        (SignoffComparability.REPEATED_DISAGREEMENT, 2),
        (SignoffComparability.REPEATED_DISAGREEMENT, 5),
    ],
)
def test_a_count_that_did_not_repeat_is_refused_rather_than_weakly_claimed(
    comparability: SignoffComparability, repetitions: int
) -> None:
    candidate = _candidate()
    evidence = _evidence(candidate, comparability=comparability, repetitions=repetitions)

    result = evaluate_authoritative_signoff(candidate, SignoffDomain.DFM, evidence=evidence)

    assert result.status is SignoffStatus.REFUSED
    assert result.code is SignoffCode.UNCOMPARABLE_EVIDENCE
    assert result.to_dict()["diagnostic"] == "authoritative evidence is not repeatable"


def test_evidence_comparability_must_agree_with_its_own_repetition_count() -> None:
    candidate = _candidate()
    for comparability, repetitions in (
        (SignoffComparability.SINGLE_INVOCATION, 2),
        (SignoffComparability.REPEATED_AGREEMENT, 1),
    ):
        with pytest.raises(ValueError, match="does not match its repetition count"):
            _evidence(candidate, comparability=comparability, repetitions=repetitions)


def test_a_run_that_skipped_checks_cannot_sign_off_even_when_it_passed() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate, suppressed=True, passed=True)

    result = evaluate_authoritative_signoff(candidate, SignoffDomain.DFM, evidence=evidence)

    assert result.status is SignoffStatus.REFUSED
    assert result.code is SignoffCode.SUPPRESSED_EVIDENCE


def test_a_registered_domain_without_evidence_says_so_distinctly() -> None:
    result = evaluate_authoritative_signoff(_candidate(), SignoffDomain.DFM)

    assert result.status is SignoffStatus.NON_CLAIM
    assert result.code is SignoffCode.NO_AUTHORITATIVE_EVIDENCE
    assert result.claimed is False


def test_serialized_evidence_is_refused_rather_than_re_admitted() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)

    for forged in (evidence.to_dict(), json.dumps(evidence.to_dict()), object(), True):
        result = evaluate_authoritative_signoff(candidate, SignoffDomain.DFM, evidence=forged)
        assert result.status is SignoffStatus.REFUSED
        assert result.code is SignoffCode.INVALID_EVIDENCE
        assert result.claimed is False


def test_a_surrogate_never_signs_off_a_registered_domain_either() -> None:
    candidate = _candidate()

    result = evaluate_authoritative_signoff(
        candidate, SignoffDomain.DFM, surrogate=SurrogateAdvisory(rank=1, score_milli=1_000)
    )

    assert result.status is SignoffStatus.NON_CLAIM
    assert result.code is SignoffCode.SURROGATE_ONLY
    assert result.to_dict()["advisory_present"] is True


def test_a_deadline_that_trips_while_evidence_is_read_withdraws_the_claim() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)
    observations = iter([False, True])

    result = evaluate_authoritative_signoff(
        candidate, SignoffDomain.DFM, evidence=evidence, deadline=lambda: next(observations)
    )

    assert result.status is SignoffStatus.REFUSED
    assert result.code is SignoffCode.DEADLINE_EXCEEDED
    assert result.claimed is False


def test_a_claim_still_refuses_a_caller_supplied_backend_and_an_expected_digest_mismatch() -> None:
    candidate = _candidate()
    evidence = _evidence(candidate)

    with_backend = evaluate_authoritative_signoff(
        candidate, SignoffDomain.DFM, lambda: evidence, evidence=evidence
    )
    wrong_digest = evaluate_authoritative_signoff(
        candidate,
        SignoffDomain.DFM,
        evidence=evidence,
        expected_evidence_digest=_digest("f"),
    )

    assert with_backend.code is SignoffCode.INVALID_BACKEND
    assert wrong_digest.code is SignoffCode.EVIDENCE_MISMATCH
