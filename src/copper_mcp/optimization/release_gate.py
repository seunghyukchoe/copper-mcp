"""Closed v0.13 release-evidence checklist, not a substitute for verifying its artifacts.

Passing this evaluator means submitted evidence is complete and numerically meets the frozen
criteria. An independent reviewer must verify the referenced artifacts against the evaluated
commit and frozen corpus/profile digests; this module cannot authorize a tag or fabrication.
"""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import Field, StringConstraints, model_validator

from copper_mcp.optimization.contracts import ClosedModel, Counter, Digest, Verdict

Gate: TypeAlias = Literal[
    "corpus_license_provenance",
    "held_out_eligibility_frozen",
    "candidate_roundtrip_via_legality",
    "zero_unverified_clears",
    "zero_hard_candidate_drc_errors",
    "post_apply_no_drc_regression",
    "candidate_judge_determinism",
    "freerouting_live_smoke",
    "simpleroutejson_live_smoke",
    "placement_no_hard_regressions",
    "placement_route_objective_improvement",
    "missing_domains_inconclusive",
    "authorization_adversarial_tests",
    "orcarouter_reconciled",
    "server_info_docs_agree",
    "make_check",
    "independent_review",
]
GATES: tuple[Gate, ...] = (
    "corpus_license_provenance",
    "held_out_eligibility_frozen",
    "candidate_roundtrip_via_legality",
    "zero_unverified_clears",
    "zero_hard_candidate_drc_errors",
    "post_apply_no_drc_regression",
    "candidate_judge_determinism",
    "freerouting_live_smoke",
    "simpleroutejson_live_smoke",
    "placement_no_hard_regressions",
    "placement_route_objective_improvement",
    "missing_domains_inconclusive",
    "authorization_adversarial_tests",
    "orcarouter_reconciled",
    "server_info_docs_agree",
    "make_check",
    "independent_review",
)


class GateEvidence(ClosedModel):
    gate: Gate
    status: Verdict
    artifact_digest: Digest | None

    @model_validator(mode="after")
    def no_unsupported_pass(self) -> GateEvidence:
        if self.status == "pass" and self.artifact_digest is None:
            raise ValueError("a release check cannot pass without an evidence artifact")
        return self


class CorpusSummary(ClosedModel):
    manifest_digest: Digest
    frozen_eligibility_digest: Digest
    frozen_profiles_digest: Digest
    board_count: Annotated[int, Field(ge=0, le=4096)]
    project_family_count: Counter
    held_out_board_count: Counter
    copper_layer_counts: Annotated[tuple[Literal[2, 4, 6, 8], ...], Field(max_length=4)]
    eligible_held_out_target_nets: Counter
    fully_routed_held_out_target_nets: Counter
    improved_held_out_placement_cases: Counter

    @model_validator(mode="after")
    def coherent_denominators(self) -> CorpusSummary:
        if (
            self.project_family_count > self.board_count
            or self.held_out_board_count > self.board_count
            or self.fully_routed_held_out_target_nets > self.eligible_held_out_target_nets
            or self.improved_held_out_placement_cases > self.held_out_board_count
            or len(self.copper_layer_counts) > self.board_count
            or tuple(sorted(set(self.copper_layer_counts))) != self.copper_layer_counts
        ):
            raise ValueError("release corpus counts are inconsistent")
        return self


class ReleaseEvidence(ClosedModel):
    schema_version: Literal["optimization-release/v1"]
    target_version: Literal["0.13.0"]
    evaluated_commit: Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{40}$", max_length=40)]
    corpus: CorpusSummary | None
    checks: Annotated[tuple[GateEvidence, ...], Field(min_length=len(GATES), max_length=len(GATES))]

    @model_validator(mode="after")
    def every_gate(self) -> ReleaseEvidence:
        if tuple(check.gate for check in self.checks) != GATES:
            raise ValueError("release checklist must include every gate exactly once")
        return self


def release_blockers(evidence: ReleaseEvidence) -> tuple[str, ...]:
    evidence = ReleaseEvidence.model_validate(evidence)
    blockers: list[str] = [check.gate for check in evidence.checks if check.status != "pass"]
    corpus = evidence.corpus
    if corpus is None:
        blockers.append("corpus_not_measured")
    else:
        if corpus.board_count < 12:
            blockers.append("fewer_than_12_boards")
        if corpus.project_family_count < 3:
            blockers.append("fewer_than_3_project_families")
        if corpus.copper_layer_counts != (2, 4, 6, 8):
            blockers.append("declared_2_to_8_layer_coverage_unproven")
        # A zero denominator is not 100%; failed eligible nets remain in this denominator.
        if corpus.eligible_held_out_target_nets == 0:
            blockers.append("no_eligible_held_out_target_nets")
        elif (
            10 * corpus.fully_routed_held_out_target_nets < 9 * corpus.eligible_held_out_target_nets
        ):
            blockers.append("held_out_routing_below_90_percent")
        if corpus.improved_held_out_placement_cases < 3:
            blockers.append("fewer_than_3_improved_held_out_placement_cases")
    return tuple(blockers)
