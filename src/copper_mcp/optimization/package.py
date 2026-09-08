"""Selected candidate package metadata; geometry retention/export remains a separate gate."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter, model_validator

from copper_mcp.optimization.contracts import (
    AnyOptimizationRequest,
    Backend,
    BackendVersion,
    ClosedModel,
    Counter,
    Digest,
    OptimizationError,
    ResourceLimitsV2,
    digest_document,
)
from copper_mcp.optimization.judge import JudgeReport, JudgeReportV2


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


class CandidateBindingV2(CandidateBinding):
    identity_namespace = "copper-mcp/optimization/v2/candidate"
    final_snapshot_digest: Digest
    constraint_digest: Digest
    drc_profile_digest: Digest


SignedMargin = Annotated[int, Field(ge=-((1 << 53) - 1), le=(1 << 53) - 1)]


class ClearanceObservation(BaseModel):
    model_config = ClosedModel.model_config
    status: Literal["measured", "unavailable"]
    reason: (
        Literal[
            "unsupported_geometry",
            "unsupported_rules",
            "unsupported_layers",
            "unassigned_copper",
            "no_comparable_pair",
        ]
        | None
    )
    minimum_margin_nm: SignedMargin | None
    snapshot_digest: Digest
    constraint_digest: Digest
    method_version: Literal["composed-ir-class-clearance/v1"]
    object_count: Counter
    pair_checks: Counter
    comparable_pairs: Counter
    geometry_checks: Counter
    digest: Digest

    @model_validator(mode="after")
    def availability(self) -> ClearanceObservation:
        if (self.status == "measured") != (self.minimum_margin_nm is not None) or (
            self.status == "measured"
        ) != (self.reason is None):
            raise ValueError("clearance availability is inconsistent")
        if self.comparable_pairs > self.pair_checks or (
            self.status == "measured" and self.comparable_pairs == 0
        ):
            raise ValueError("clearance population is inconsistent")
        if self.digest != digest_document(
            "copper-mcp/composed-clearance/v1", self.model_dump(exclude={"digest"})
        ):
            raise ValueError("clearance observation digest is invalid")
        return self


class ObjectiveMetricsV2(ClosedModel):
    hard_legality_errors: Counter
    hard_drc_errors: Counter
    target_net_count: Annotated[int, Field(ge=1, le=4096)]
    fully_connected_target_nets: Counter
    congestion_penalty: Counter
    clearance: ClearanceObservation
    via_count: Counter
    copper_length_nm: Counter
    displacement_nm: Counter
    intent_residual: Counter
    actual_route_probes: Counter

    @model_validator(mode="after")
    def connectivity(self) -> ObjectiveMetricsV2:
        if self.fully_connected_target_nets > self.target_net_count:
            raise ValueError("connected target count exceeds scope")
        return self


class SlotBudget(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v2/slot-budget"
    expansions: Counter
    route_attempts: Counter
    repair_rounds: Counter
    routing_checks: Counter
    validation_checks: Counter
    wall_ms: Counter


class SlotWork(ClosedModel):
    """Conservative charged search/replay ceilings and completed measurement work."""

    expansions: Counter = 0
    route_attempts: Counter = 0
    repair_rounds: Counter = 0
    routing_checks: Counter = 0
    validation_checks: Counter = 0
    search_accounting: Literal["conservative-proposal-replay-reservations/v1"] = (
        "conservative-proposal-replay-reservations/v1"
    )


class AllocationBasis(ClosedModel):
    limits: ResourceLimitsV2
    used_expansions: Counter
    used_route_attempts: Counter
    used_repair_rounds: Counter
    used_obstacle_checks: Counter
    used_placement_evaluations: Counter
    used_candidates: Counter
    remaining_ms: Counter

    @model_validator(mode="after")
    def bounded(self) -> AllocationBasis:
        for field in (
            "expansions",
            "route_attempts",
            "repair_rounds",
            "obstacle_checks",
            "placement_evaluations",
            "candidates",
        ):
            if getattr(self, "used_" + field) > getattr(self.limits, "max_" + field):
                raise ValueError("allocation basis exceeds root limits")
        if not 1 <= self.remaining_ms <= self.limits.max_runtime_ms:
            raise ValueError("allocation time is invalid")
        return self

    def per_slot(self, count: int) -> SlotBudget:
        if (
            type(count) is not int
            or not 1 <= count <= self.limits.max_candidates - self.used_candidates
        ):
            raise ValueError("allocation population exceeds root limits")
        checks = (self.limits.max_obstacle_checks - self.used_obstacle_checks) // 2 // count
        return SlotBudget(
            expansions=(self.limits.max_expansions - self.used_expansions) // count,
            route_attempts=(self.limits.max_route_attempts - self.used_route_attempts) // count,
            repair_rounds=(self.limits.max_repair_rounds - self.used_repair_rounds) // count,
            routing_checks=checks,
            validation_checks=checks,
            wall_ms=self.remaining_ms // count,
        )


class ComparisonOutcome(ClosedModel):
    placement_candidate_id: Digest
    role: Literal["identity", "alternative"]
    status: Literal[
        "reviewable",
        "backend_failure",
        "budget_exhausted",
        "unsupported_geometry",
        "invalid_candidate",
        "judge_failed",
        "required_domain_inconclusive",
    ]
    candidate_id: Digest | None
    metrics: ObjectiveMetricsV2 | None
    route_probes: Counter | None
    charged: SlotWork

    @model_validator(mode="after")
    def observed(self) -> ComparisonOutcome:
        ready = self.status == "reviewable"
        if ready != (self.metrics is not None) or ready != (self.candidate_id is not None):
            raise ValueError("comparison outcome invents missing observations")
        if ready != (self.route_probes is not None):
            raise ValueError("failed actual search counts are unavailable")
        if self.metrics is not None and self.metrics.actual_route_probes != self.route_probes:
            raise ValueError("successful probe accounting is inconsistent")
        if self.route_probes is not None and self.route_probes > self.charged.route_attempts:
            raise ValueError("comparison work was not charged")
        return self


class Comparison(ClosedModel):
    allocation: AllocationBasis
    budget: SlotBudget
    outcomes: Annotated[tuple[ComparisonOutcome, ...], Field(min_length=1, max_length=32)]
    clearance_ranking: Literal["included", "omitted_unavailable"]
    improvement: Literal["not_claimed"] = "not_claimed"

    @model_validator(mode="after")
    def population(self) -> Comparison:
        if (
            self.budget != self.allocation.per_slot(len(self.outcomes))
            or min(
                self.budget.wall_ms,
                self.budget.expansions,
                self.budget.route_attempts,
                self.budget.routing_checks,
                self.budget.validation_checks,
            )
            < 1
        ):
            raise ValueError("comparison allocations are inconsistent")
        for row in self.outcomes:
            if any(
                getattr(row.charged, field) > getattr(self.budget, field)
                for field in (
                    "expansions",
                    "route_attempts",
                    "repair_rounds",
                    "routing_checks",
                    "validation_checks",
                )
            ):
                raise ValueError("comparison slot exceeds its allocation")
        if self.outcomes[0].role != "identity" or any(
            row.role != "alternative" for row in self.outcomes[1:]
        ):
            raise ValueError("comparison must retain exactly one identity control")
        if len({row.placement_candidate_id for row in self.outcomes}) != len(self.outcomes):
            raise ValueError("comparison placements must be distinct")
        available = all(
            row.metrics is not None and row.metrics.clearance.status == "measured"
            for row in self.outcomes
        )
        if (self.clearance_ranking == "included") != available:
            raise ValueError("comparison clearance interpretation is inconsistent")
        return self


class _OptimizationPackageFields(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v1/package"
    schema_version: Literal["optimization/v1", "optimization/v2"]
    request_digest: Digest
    binding: CandidateBinding
    alternate_candidate_ids: Annotated[tuple[Digest, ...], Field(max_length=31)]
    metrics: ObjectiveMetrics | ObjectiveMetricsV2
    judge: JudgeReport | JudgeReportV2
    backend_provenance: Annotated[tuple[BackendProvenance, ...], Field(min_length=1, max_length=32)]

    @model_validator(mode="after")
    def final_candidate_binding(self) -> _OptimizationPackageFields:
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

    def _allows_zero_probe_identity(self, request: AnyOptimizationRequest) -> bool:
        return False

    def require_reviewable_for(self, request: AnyOptimizationRequest) -> None:
        """Check selection gates, not proof of provenance and not permission to apply."""

        if (
            self.schema_version != request.schema_version
            or self.judge.schema_version != self.schema_version
            or self.request_digest != request.digest
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
                and not self._allows_zero_probe_identity(request)
            )
        ):
            raise OptimizationError("optimization package is not reviewable")


class OptimizationPackage(_OptimizationPackageFields):
    schema_version: Literal["optimization/v1"]
    metrics: ObjectiveMetrics
    judge: JudgeReport


class OptimizationPackageV2(_OptimizationPackageFields):
    identity_namespace = "copper-mcp/optimization/v2/package"
    schema_version: Literal["optimization/v2"]
    binding: CandidateBindingV2
    metrics: ObjectiveMetricsV2
    judge: JudgeReportV2
    comparison: Comparison

    def _allows_zero_probe_identity(self, request: AnyOptimizationRequest) -> bool:
        """Structural half of the v2 identity exception; the worker also checks raw equality."""
        identity = digest_document(
            "optimization-identity-placement/v2",
            {"source": request.board_revision, "snapshot": request.snapshot_digest},
        )
        row = self.comparison.outcomes[0]
        return (
            request.schema_version == "optimization/v2"
            and self.binding.placement_candidate_id == identity
            and row.role == "identity"
            and row.placement_candidate_id == identity
            and row.candidate_id == self.binding.digest
            and row.metrics == self.metrics
            and row.status == "reviewable"
            and self.binding.candidate_board_revision
            == self.binding.board_revision
            == request.board_revision
            and self.binding.snapshot_digest
            == self.binding.placed_snapshot_digest
            == self.binding.route_bundle_base_digest
            == self.binding.final_snapshot_digest
            == request.snapshot_digest
            and self.metrics.displacement_nm
            == self.metrics.copper_length_nm
            == self.metrics.via_count
            == self.metrics.actual_route_probes
            == 0
        )

    @model_validator(mode="after")
    def measured_binding(self) -> OptimizationPackageV2:
        if self.binding.drc_profile_digest != self.judge.drc_profile_digest:
            raise ValueError("candidate DRC profile differs from its judge")
        measurement = self.metrics.clearance
        if (
            measurement.snapshot_digest != self.binding.final_snapshot_digest
            or measurement.constraint_digest != self.binding.constraint_digest
        ):
            raise ValueError("clearance describes another composed snapshot")
        selected = [
            row for row in self.comparison.outcomes if row.candidate_id == self.binding.digest
        ]
        if (
            len(selected) != 1
            or selected[0].metrics != self.metrics
            or selected[0].status != "reviewable"
        ):
            raise ValueError("selected comparison result is inconsistent")
        others = tuple(
            sorted(
                row.candidate_id
                for row in self.comparison.outcomes
                if row.candidate_id is not None and row.candidate_id != self.binding.digest
            )
        )
        if others != self.alternate_candidate_ids:
            raise ValueError("alternate comparison results are inconsistent")
        identity = digest_document(
            "optimization-identity-placement/v2",
            {"source": self.binding.board_revision, "snapshot": self.binding.snapshot_digest},
        )
        if self.comparison.outcomes[0].placement_candidate_id != identity:
            raise ValueError("identity comparison describes another source")
        return self

    def require_reviewable_for(self, request: AnyOptimizationRequest) -> None:
        super().require_reviewable_for(request)
        if (
            request.schema_version != "optimization/v2"
            or self.comparison.allocation.limits != request.limits
        ):
            raise OptimizationError("optimization comparison budget is invalid")
        if self.binding.drc_profile_digest != request.drc_profile_digest:
            raise OptimizationError("optimization DRC profile was changed")
        if self.judge.project_required != (request.project_capture_digest is not None):
            raise OptimizationError("optimization project requirement was changed")
        if (
            self.judge.project_evidence is not None
            and self.judge.project_evidence.libraries_digest != request.project_libraries_digest
        ):
            raise OptimizationError("optimization project libraries were changed")
        if any(
            row.metrics is not None
            and (
                row.metrics.target_net_count != request.target_net_count
                or row.metrics.fully_connected_target_nets != request.target_net_count
                or row.metrics.hard_legality_errors
                or row.metrics.hard_drc_errors
            )
            for row in self.comparison.outcomes
        ):
            raise OptimizationError("optimization comparison contains unreviewable metrics")


AnyOptimizationPackage = OptimizationPackage | OptimizationPackageV2
_PACKAGE_ADAPTER: TypeAdapter[AnyOptimizationPackage] = TypeAdapter(AnyOptimizationPackage)


def validate_package(value: object) -> AnyOptimizationPackage:
    return _PACKAGE_ADAPTER.validate_python(value)


def decode_package(value: str | bytes) -> AnyOptimizationPackage:
    return _PACKAGE_ADAPTER.validate_json(value)
