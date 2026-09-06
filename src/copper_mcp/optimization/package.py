"""Selected candidate package metadata; geometry retention/export remains a separate gate."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import Field, model_validator

from copper_mcp.optimization.contracts import (
    Backend,
    BackendVersion,
    ClosedModel,
    Counter,
    Digest,
    OptimizationError,
    OptimizationRequest,
)
from copper_mcp.optimization.judge import JudgeReport


class CandidateBinding(ClosedModel):
    """Bind routing to the placed snapshot, never accidentally to the unplaced source."""

    identity_namespace = "copper-mcp/optimization/v1/candidate"
    board_revision: Digest
    snapshot_digest: Digest
    placement_candidate_id: Digest
    placed_snapshot_digest: Digest
    route_bundle_id: Digest
    route_bundle_base_digest: Digest
    candidate_board_revision: Digest
    rule_context_digest: Digest

    @model_validator(mode="after")
    def composed_revision(self) -> CandidateBinding:
        if self.route_bundle_base_digest != self.placed_snapshot_digest:
            raise ValueError("route bundle does not target the selected placed snapshot")
        return self


class ObjectiveMetrics(ClosedModel):
    hard_legality_errors: Counter
    hard_drc_errors: Counter
    target_net_count: Annotated[int, Field(ge=1, le=4096)]
    fully_connected_target_nets: Counter
    congestion_penalty: Counter
    clearance_margin_nm: Counter
    via_count: Counter
    copper_length_nm: Counter
    displacement_nm: Counter
    intent_residual: Counter
    actual_route_probes: Counter

    @model_validator(mode="after")
    def bounded_connectivity(self) -> ObjectiveMetrics:
        if self.fully_connected_target_nets > self.target_net_count:
            raise ValueError("connected target count exceeds the declared target set")
        return self


class BackendProvenance(ClosedModel):
    backend: Backend
    version: BackendVersion
    executable_digest: Digest
    command_digest: Digest
    settings_digest: Digest
    input_digest: Digest
    source_board_revision: Digest
    placed_snapshot_digest: Digest
    route_bundle_id: Digest
    normalized_output_digest: Digest


class OptimizationPackage(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v1/package"
    schema_version: Literal["optimization/v1"]
    request_digest: Digest
    binding: CandidateBinding
    alternate_candidate_ids: Annotated[tuple[Digest, ...], Field(max_length=31)]
    metrics: ObjectiveMetrics
    judge: JudgeReport
    backend_provenance: Annotated[tuple[BackendProvenance, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def final_candidate_binding(self) -> OptimizationPackage:
        if tuple(sorted(set(self.alternate_candidate_ids))) != self.alternate_candidate_ids:
            raise ValueError("alternate candidates must be unique and sorted")
        if self.binding.digest in self.alternate_candidate_ids:
            raise ValueError("selected candidate cannot also be an alternate")
        if (
            self.judge.candidate_id != self.binding.digest
            or self.judge.board_revision != self.binding.board_revision
            or self.judge.input_digest != self.binding.candidate_board_revision
            or self.judge.rule_context_digest != self.binding.rule_context_digest
            or any(
                row.source_board_revision != self.binding.board_revision
                or row.placed_snapshot_digest != self.binding.placed_snapshot_digest
                or row.route_bundle_id != self.binding.route_bundle_id
                or row.input_digest != self.binding.placed_snapshot_digest
                or row.normalized_output_digest != self.binding.candidate_board_revision
                for row in self.backend_provenance
            )
        ):
            raise ValueError("judge does not describe the selected composed candidate")
        return self

    def document(self) -> dict[str, object]:
        return {
            **self.model_dump(mode="json"),
            "candidate_id": self.binding.digest,
            "judge": self.judge.document(),
            "apply_authority": "none",
        }

    def require_reviewable_for(self, request: OptimizationRequest) -> None:
        """Check selection gates, not proof of provenance and not permission to apply."""

        if (
            self.request_digest != request.digest
            or self.binding.board_revision != request.board_revision
            or self.binding.snapshot_digest != request.snapshot_digest
            or self.judge.settings_digest != request.judge_profile_digest
            or self.judge.required_domains != request.required_domains
            or self.judge.electrical_inputs_digest != request.electrical_inputs_digest
            or self.metrics.target_net_count != request.target_net_count
            or any(row.backend not in request.allowed_backends for row in self.backend_provenance)
            or any(
                row.settings_digest != request.routing_profile_digest
                for row in self.backend_provenance
            )
            or len(self.alternate_candidate_ids) + 1 > request.limits.max_candidates
        ):
            raise OptimizationError("optimization package binding is invalid")
        if (
            self.metrics.hard_legality_errors != 0
            or self.metrics.hard_drc_errors != 0
            or self.metrics.fully_connected_target_nets != self.metrics.target_net_count
            or not self.judge.reviewable
            or (
                request.placement_scope.movable_footprint_refs
                and self.metrics.actual_route_probes == 0
            )
        ):
            raise OptimizationError("optimization package is not reviewable")
