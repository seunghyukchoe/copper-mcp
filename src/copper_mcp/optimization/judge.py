"""Read-only all-domain evidence envelopes, not an engineering-signoff authority.

Only DRC, ERC and DRC-backed DFM have declared authority kinds in this v1 envelope. The four
physics domains remain inconclusive. Shape validation cannot authenticate an invocation: the
future worker must obtain observations from coordinator-owned executors, never a request or AI.
The existing authoritative_signoff module and its evidence capability are unchanged.
"""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from copper_mcp.optimization.contracts import (
    DOMAINS,
    BackendVersion,
    ClosedModel,
    Counter,
    Digest,
    Domain,
    OptimizationRequest,
    Verdict,
)

Authority = Literal["kicad-drc-v1", "kicad-erc-v1", "kicad-drc-dfm-v1"]
Reason = Literal[
    "verified",
    "check_failed",
    "backend_unavailable",
    "insufficient_inputs",
    "not_requested",
    "not_run",
    "evidence_disagreement",
    "suppressed_checks",
    "backend_failure",
]
_AUTHORITY_DOMAINS: dict[Authority, Domain] = {
    "kicad-drc-v1": "DRC",
    "kicad-erc-v1": "ERC",
    "kicad-drc-dfm-v1": "DFM",
}


class EvidenceSample(ClosedModel):
    verdict: Literal["pass", "fail"]
    normalized_result_digest: Digest


class EvidenceBinding(ClosedModel):
    candidate_id: Digest
    board_revision: Digest
    input_digest: Digest
    settings_digest: Digest
    backend: Authority
    backend_version: BackendVersion
    executable_digest: Digest
    command_digest: Digest
    rule_context_digest: Digest
    samples: Annotated[tuple[EvidenceSample, ...], Field(min_length=1, max_length=8)]
    suppressed_check_count: Counter

    @property
    def repeated_agreement(self) -> bool:
        return len(self.samples) >= 2 and all(sample == self.samples[0] for sample in self.samples)


class DomainResult(ClosedModel):
    domain: Domain
    status: Verdict
    reason: Reason
    evidence: EvidenceBinding | None

    @model_validator(mode="after")
    def consistent_claim(self) -> DomainResult:
        evidence = self.evidence
        if self.domain in ("SI", "PI", "thermal", "EMC") and (
            self.status != "inconclusive"
            or self.reason not in ("backend_unavailable", "insufficient_inputs")
            or evidence is not None
        ):
            raise ValueError("physics authority is unavailable in this contract")
        if self.status in ("pass", "fail"):
            if evidence is None or _AUTHORITY_DOMAINS[evidence.backend] != self.domain:
                raise ValueError("domain claim lacks a declared evidence authority")
            if not evidence.repeated_agreement or evidence.suppressed_check_count:
                raise ValueError("domain claim requires unsuppressed repeated agreement")
            if self.status != evidence.samples[0].verdict:
                raise ValueError("domain claim disagrees with its observations")
            expected = "verified" if self.status == "pass" else "check_failed"
            if self.reason != expected:
                raise ValueError("domain reason disagrees with its status")
        elif self.status == "not_run":
            if evidence is not None or self.reason not in ("not_run", "not_requested"):
                raise ValueError("not-run domain cannot carry a claim")
        else:
            if self.reason in ("verified", "check_failed", "not_run", "not_requested"):
                raise ValueError("inconclusive domain reason is invalid")
            if evidence is not None:
                if _AUTHORITY_DOMAINS[evidence.backend] != self.domain:
                    raise ValueError("domain evidence authority does not match")
                if self.reason == "evidence_disagreement":
                    if len(evidence.samples) < 2 or evidence.repeated_agreement:
                        raise ValueError("disagreement requires distinct repeated observations")
                elif self.reason == "suppressed_checks":
                    if evidence.suppressed_check_count == 0:
                        raise ValueError("suppressed evidence must name omitted checks")
                else:
                    raise ValueError("unavailable authority cannot carry observations")
        return self


def aggregate_verdict(results: tuple[DomainResult, ...]) -> Verdict:
    if any(result.status == "fail" for result in results):
        return "fail"
    if not results or any(result.status != "pass" for result in results):
        return "inconclusive"
    return "pass"


class JudgeReport(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v1/judge"
    schema_version: Literal["optimization/v1"]
    candidate_id: Digest
    board_revision: Digest
    input_digest: Digest
    settings_digest: Digest
    electrical_inputs_digest: Digest | None
    rule_context_digest: Digest
    required_domains: Annotated[tuple[Domain, ...], Field(min_length=1, max_length=7)]
    domains: Annotated[tuple[DomainResult, ...], Field(min_length=7, max_length=7)]

    @model_validator(mode="after")
    def complete_and_bound(self) -> JudgeReport:
        if tuple(result.domain for result in self.domains) != DOMAINS:
            raise ValueError("judge must contain every domain exactly once in canonical order")
        if "DRC" not in self.required_domains:
            raise ValueError("judge must require DRC")
        if (
            tuple(domain for domain in DOMAINS if domain in self.required_domains)
            != self.required_domains
        ):
            raise ValueError("judge required domains are not canonical")
        for result in self.domains:
            evidence = result.evidence
            if evidence is not None and (
                evidence.candidate_id != self.candidate_id
                or evidence.board_revision != self.board_revision
                or evidence.input_digest
                != (self.electrical_inputs_digest if result.domain == "ERC" else self.input_digest)
                or evidence.rule_context_digest != self.rule_context_digest
                or evidence.settings_digest != self.settings_digest
            ):
                raise ValueError("judge evidence is bound to another candidate or context")
        return self

    @property
    def aggregate_status(self) -> Verdict:
        """All seven domains: never an all-engineering pass while physics is unavailable."""

        return aggregate_verdict(self.domains)

    @property
    def required_status(self) -> Verdict:
        return aggregate_verdict(
            tuple(row for row in self.domains if row.domain in self.required_domains)
        )

    @property
    def inconclusive_domains(self) -> tuple[Domain, ...]:
        return tuple(
            row.domain for row in self.domains if row.status in ("inconclusive", "not_run")
        )

    @property
    def reviewable(self) -> bool:
        return self.required_status == "pass" and self.aggregate_status != "fail"

    def document(self) -> dict[str, object]:
        return {
            **self.model_dump(mode="json"),
            "aggregate_status": self.aggregate_status,
            "required_status": self.required_status,
            "inconclusive_domains": list(self.inconclusive_domains),
        }


def unavailable_report(
    request: OptimizationRequest,
    *,
    candidate_id: str,
    candidate_input_digest: str,
    rule_context_digest: str,
) -> JudgeReport:
    """An honest initial envelope; never manufactures a successful DRC/ERC invocation."""

    results: list[DomainResult] = []
    for domain in DOMAINS:
        if domain in ("SI", "PI", "thermal", "EMC"):
            status: Verdict = "inconclusive"
            reason: Reason = "backend_unavailable"
        elif domain == "ERC" and request.electrical_inputs_digest is None:
            status, reason = "inconclusive", "insufficient_inputs"
        else:
            status, reason = "not_run", "not_run"
        results.append(DomainResult(domain=domain, status=status, reason=reason, evidence=None))
    return JudgeReport(
        schema_version="optimization/v1",
        candidate_id=candidate_id,
        board_revision=request.board_revision,
        input_digest=candidate_input_digest,
        settings_digest=request.judge_profile_digest,
        electrical_inputs_digest=request.electrical_inputs_digest,
        rule_context_digest=rule_context_digest,
        required_domains=request.required_domains,
        domains=tuple(results),
    )
