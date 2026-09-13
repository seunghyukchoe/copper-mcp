"""Selected candidate package metadata; geometry retention/export remains a separate gate."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import (
    BaseModel,
    Field,
    SerializerFunctionWrapHandler,
    TypeAdapter,
    model_serializer,
    model_validator,
)

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
    routing_backend_order,
)
from copper_mcp.optimization.judge import JudgeReport, JudgeReportV2
from copper_mcp.optimization.native_import import NativeImportBinding
from copper_mcp.optimization.repair import CopperRepairBinding
from copper_mcp.optimization.zone_fill import CandidateFillBinding


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


class CoordinateQuantizationV2(ClosedModel):
    """Declared coordinate conversion, not a clearance or physical-accuracy claim."""

    method: Literal["nearest-nm-half-away-from-zero/v1"] = "nearest-nm-half-away-from-zero/v1"
    grid_nm: Literal[1] = 1
    maximum_coordinate_error_pm: Literal[500] = 500
    coordinate_count: Annotated[int, Field(ge=1, le=32768)]
    rounded_coordinate_count: Counter

    @model_validator(mode="after")
    def bounded_count(self) -> CoordinateQuantizationV2:
        if self.rounded_coordinate_count > self.coordinate_count:
            raise ValueError("rounded coordinate count exceeds the converted population")
        return self


class SpecctraDisposalV2(ClosedModel):
    """Whole-board proposal cost and the strictly narrower admitted copper scope."""

    method: Literal["preserve-original-target-only/v1"] = "preserve-original-target-only/v1"
    proposal_scope: Literal["whole-board"] = "whole-board"
    original_target_scope_digest: Digest
    routing_target_scope_digest: Digest
    original_target_count: Annotated[int, Field(ge=1, le=4096)]
    routing_target_count: Annotated[int, Field(ge=1, le=4096)]
    retained_copper_count: Counter
    accepted_copper_count: Counter
    discarded_copper_count: Counter
    accepted_copper_digest: Digest
    discarded_copper_digest: Digest

    @model_validator(mode="after")
    def narrowed_scope(self) -> SpecctraDisposalV2:
        if self.routing_target_count > self.original_target_count:
            raise ValueError("routing scope exceeds original target scope")
        if (self.routing_target_count == self.original_target_count) != (
            self.routing_target_scope_digest == self.original_target_scope_digest
        ):
            raise ValueError("routing scope cardinality and identity disagree")
        return self


class BackendProvenanceV2(BackendProvenance):
    identity_namespace = "copper-mcp/optimization/v2/backend-provenance"
    image_digest: Digest | None = None
    router_input_digest: Digest | None = None
    router_output_digest: Digest | None = None
    converter_digest: Digest | None = None
    coordinate_quantization: CoordinateQuantizationV2 | None = None
    specctra_disposal: SpecctraDisposalV2 | None = None

    @model_validator(mode="after")
    def external_execution(self) -> BackendProvenanceV2:
        external = self.backend != "internal-layered-v1"
        bound = (
            self.image_digest,
            self.router_input_digest,
            self.router_output_digest,
            self.converter_digest,
        )
        if external != all(value is not None for value in bound) or (
            not external and any(value is not None for value in bound)
        ):
            raise ValueError("backend execution provenance is incomplete")
        if (self.coordinate_quantization is not None) != (self.backend == "simpleroutejson-v1"):
            raise ValueError("SRJ coordinate quantization must be disclosed")
        if (self.specctra_disposal is not None) != (self.backend == "freerouting-dsn-ses-v1"):
            raise ValueError("Specctra whole-board proposal disposal must be disclosed")
        return self


class CandidateBindingV2(CandidateBinding):
    identity_namespace = "copper-mcp/optimization/v2/candidate"
    final_snapshot_digest: Digest
    constraint_digest: Digest
    drc_profile_digest: Digest
    native_import_digest: Digest | None = None
    fill_history_digest: Digest | None = None
    repair_digest: Digest | None = None


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
    added_copper_length_nm: Counter = 0
    added_via_count: Counter = 0
    copper_scope: Literal["target-net-straight-traces/v1"] = "target-net-straight-traces/v1"

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
    external_output_bytes: Counter = 0


class SlotWork(ClosedModel):
    """Conservative charged search/replay ceilings and completed measurement work."""

    expansions: Counter = 0
    route_attempts: Counter = 0
    repair_rounds: Counter = 0
    routing_checks: Counter = 0
    validation_checks: Counter = 0
    external_output_bytes: Counter = 0
    search_accounting: Literal["conservative-proposal-replay-reservations/v1"] = (
        "conservative-proposal-replay-reservations/v1"
    )


class ExternalRunV2(ClosedModel):
    """Serialized validated `ContainerRunRecord` plus consumer-side bindings."""

    identity_namespace = "copper-mcp/optimization/v2/external-run"
    backend: Backend
    version: BackendVersion
    status: Literal[
        "success",
        "daemon_unavailable",
        "image_unavailable",
        "cancelled",
        "deadline_exceeded",
        "output_limit_exceeded",
        "empty_output",
        "process_io_failed",
        "exited_nonzero",
        "launch_failed",
        "cleanup_failed",
    ]
    image_digest: Digest
    image_identity_kind: Literal["local_image_id", "repo_digest"]
    command_digest: Digest | None
    input_digest: Digest | None
    output_digest: Digest | None
    input_bytes: Counter
    output_bytes: Counter
    exit_code: Annotated[int, Field(ge=-255, le=255)] | None
    executable_digest: Digest | None
    settings_digest: Digest
    converter_digest: Digest | None = None
    normalized_route_digest: Digest | None = None
    coordinate_quantization: CoordinateQuantizationV2 | None = None
    specctra_disposal: SpecctraDisposalV2 | None = None
    charge_status: Literal["charged"] = "charged"

    @model_validator(mode="after")
    def normalized_pair(self) -> ExternalRunV2:
        if (self.converter_digest is None) != (self.normalized_route_digest is None):
            raise ValueError("external run normalization binding is incomplete")
        if (self.coordinate_quantization is not None) != (
            self.backend == "simpleroutejson-v1" and self.normalized_route_digest is not None
        ):
            raise ValueError("normalized SRJ coordinate quantization must be disclosed")
        if (self.specctra_disposal is not None) != (
            self.backend == "freerouting-dsn-ses-v1" and self.normalized_route_digest is not None
        ):
            raise ValueError("normalized Specctra proposal disposal must be disclosed")
        if (
            self.specctra_disposal is not None
            and self.specctra_disposal.accepted_copper_digest != self.normalized_route_digest
        ):
            raise ValueError("Specctra accepted copper differs from the normalized route")
        if self.status == "success":
            if (
                any(
                    value is None
                    for value in (
                        self.command_digest,
                        self.input_digest,
                        self.output_digest,
                        self.exit_code,
                        self.executable_digest,
                    )
                )
                or self.exit_code != 0
                or self.input_bytes < 1
                or self.output_bytes < 1
            ):
                raise ValueError("successful external run evidence is incomplete")
        elif self.normalized_route_digest is not None:
            raise ValueError("failed external run claims normalized copper")
        return self


class AllocationBasis(ClosedModel):
    limits: ResourceLimitsV2
    used_expansions: Counter
    used_route_attempts: Counter
    used_repair_rounds: Counter
    used_obstacle_checks: Counter
    used_placement_evaluations: Counter
    used_candidates: Counter
    remaining_ms: Counter
    used_external_output_bytes: Counter = 0

    @model_validator(mode="after")
    def bounded(self) -> AllocationBasis:
        for field in (
            "expansions",
            "route_attempts",
            "repair_rounds",
            "obstacle_checks",
            "placement_evaluations",
            "candidates",
            "external_output_bytes",
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
            external_output_bytes=(
                self.limits.max_external_output_bytes - self.used_external_output_bytes
            )
            // count,
        )


class RoutingAttemptV2(ClosedModel):
    """One routing-stage attempt; composition is not engineering approval."""

    backend: Backend
    outcome: Literal[
        "composed",
        "backend_failure",
        "budget_exhausted",
        "unsupported_geometry",
        "invalid_candidate",
    ]
    composition_digest: Digest | None
    external_run_digest: Digest | None
    charged: SlotWork

    @model_validator(mode="after")
    def composition(self) -> RoutingAttemptV2:
        if (self.outcome == "composed") != (self.composition_digest is not None):
            raise ValueError("routing attempt invents a composition")
        if self.backend == "internal-layered-v1" and self.external_run_digest is not None:
            raise ValueError("internal routing invents external execution")
        if (
            self.backend != "internal-layered-v1"
            and self.outcome == "composed"
            and self.external_run_digest is None
        ):
            raise ValueError("external composition lacks execution evidence")
        return self


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
    external_runs: Annotated[tuple[ExternalRunV2, ...], Field(max_length=2)] = ()
    routing_attempts: Annotated[tuple[RoutingAttemptV2, ...], Field(max_length=3)] = ()

    @model_serializer(mode="wrap")
    def preserve_legacy_document(self, handler: SerializerFunctionWrapHandler) -> dict[str, object]:
        document: dict[str, object] = handler(self)
        if not self.routing_attempts:
            document.pop("routing_attempts", None)
        return document

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
        if self.routing_attempts:
            attempts = self.routing_attempts
            if len({attempt.backend for attempt in attempts}) != len(attempts):
                raise ValueError("routing backend was repeated")
            if any(
                attempt.outcome in {"composed", "budget_exhausted"} for attempt in attempts[:-1]
            ) or (ready and attempts[-1].outcome != "composed"):
                raise ValueError("routing continued after composition or invented success")
            # The slot/freshness deadline can expire after a real failed attempt, before
            # another backend starts. Preserve that attempt rather than inventing one.
            if attempts[-1].outcome != "composed" and self.status not in {
                attempts[-1].outcome,
                "budget_exhausted",
            }:
                raise ValueError("routing refusal differs from the comparison outcome")
            for field in (
                "expansions",
                "route_attempts",
                "repair_rounds",
                "routing_checks",
                "validation_checks",
                "external_output_bytes",
            ):
                total = sum(getattr(attempt.charged, field) for attempt in attempts)
                recorded = getattr(self.charged, field)
                if total > recorded or (field != "validation_checks" and total != recorded):
                    raise ValueError("routing attempt work was not charged")
            runs = {run.digest: run for run in self.external_runs}
            bindings = [
                attempt.external_run_digest
                for attempt in attempts
                if attempt.external_run_digest is not None
            ]
            if (
                len(runs) != len(self.external_runs)
                or len(set(bindings)) != len(bindings)
                or set(bindings) != set(runs)
            ):
                raise ValueError("external execution is missing or unaccounted")
            for attempt in attempts:
                if attempt.external_run_digest is not None:
                    run = runs[attempt.external_run_digest]
                    if (
                        run.backend != attempt.backend
                        or run.output_bytes > attempt.charged.external_output_bytes
                        or attempt.charged.route_attempts < 1
                    ):
                        raise ValueError("routing attempt differs from its external execution")
                    if attempt.outcome == "composed" and run.normalized_route_digest is None:
                        raise ValueError("external composition was not normalized")
        elif self.external_runs:
            if len(self.external_runs) != 1:
                raise ValueError("multiple external executions lack attempt evidence")
            run = self.external_runs[0]
            if (
                self.charged.route_attempts < 1
                or self.charged.external_output_bytes < run.output_bytes
                # Normalization may succeed before connectivity or engineering checks refuse.
                # Retain that execution evidence without making the failed slot reviewable.
                or (ready and run.normalized_route_digest is None)
            ):
                raise ValueError("comparison external run is inconsistent")
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
                    "external_output_bytes",
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
    native_import: NativeImportBinding | None = None
    zone_fill: CandidateFillBinding | None = None
    fill_round_count: Counter = 0
    repair: CopperRepairBinding | None = None
    backend_provenance: Annotated[
        tuple[BackendProvenanceV2, ...], Field(min_length=1, max_length=32)
    ]

    def _allows_zero_probe_identity(self, request: AnyOptimizationRequest) -> bool:
        """Structural half of the v2 identity exception; the worker also checks raw equality."""
        identity = digest_document(
            "optimization-identity-placement/v2",
            {"source": request.board_revision, "snapshot": request.snapshot_digest},
        )
        row = self.comparison.outcomes[0]
        baseline = (
            request.board_revision
            if self.native_import is None
            else self.native_import.imported_board_revision
        )
        final_revision = baseline
        final_snapshot = request.snapshot_digest
        if self.zone_fill is not None:
            if (
                self.fill_round_count != 1
                or self.zone_fill.input_board_revision != baseline
                or self.zone_fill.input_snapshot_digest != request.snapshot_digest
            ):
                return False
            final_revision = self.zone_fill.output_board_revision
            final_snapshot = self.zone_fill.output_snapshot_digest
        return (
            request.schema_version == "optimization/v2"
            and self.repair is None
            and self.binding.placement_candidate_id == identity
            and row.role == "identity"
            and row.placement_candidate_id == identity
            and row.candidate_id == self.binding.digest
            and row.metrics == self.metrics
            and row.status == "reviewable"
            and self.binding.candidate_board_revision == final_revision
            and self.binding.board_revision == request.board_revision
            and self.binding.snapshot_digest
            == self.binding.placed_snapshot_digest
            == self.binding.route_bundle_base_digest
            == request.snapshot_digest
            and self.binding.final_snapshot_digest == final_snapshot
            and self.metrics.displacement_nm
            == self.metrics.added_copper_length_nm
            == self.metrics.added_via_count
            == self.metrics.actual_route_probes
            == 0
        )

    @model_validator(mode="after")
    def measured_binding(self) -> OptimizationPackageV2:
        if self.binding.repair_digest != (None if self.repair is None else self.repair.digest):
            raise ValueError("candidate copper repair binding is inconsistent")
        if self.repair is not None and (
            self.repair.base_snapshot_digest != self.binding.placed_snapshot_digest
            or self.repair.target_net_count > self.metrics.target_net_count
            or self.metrics.actual_route_probes < 1
            or not any(
                row.candidate_id == self.binding.digest and row.charged.repair_rounds >= 1
                for row in self.comparison.outcomes
            )
        ):
            raise ValueError("candidate copper repair lacks a charged complete reroute")
        if (self.zone_fill is None) != (self.fill_round_count == 0) or (self.zone_fill is None) != (
            self.binding.fill_history_digest is None
        ):
            raise ValueError("candidate fill evidence is incomplete")
        if self.zone_fill is not None and (
            self.zone_fill.output_board_revision != self.binding.candidate_board_revision
            or self.zone_fill.output_snapshot_digest != self.binding.final_snapshot_digest
        ):
            raise ValueError("candidate fill describes another board")
        if self.binding.native_import_digest != (
            None if self.native_import is None else self.native_import.digest
        ) or (
            self.native_import is not None
            and self.native_import.original_board_revision != self.binding.board_revision
        ):
            raise ValueError("candidate native import binding is inconsistent")
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
        if selected[0].routing_attempts:
            last = selected[0].routing_attempts[-1]
            if (
                len(self.backend_provenance) != 1
                or last.backend != self.backend_provenance[0].backend
                or last.composition_digest != self.binding.route_bundle_id
            ):
                raise ValueError("selected routing attempt differs from its bound composition")
        external = tuple(
            row for row in self.backend_provenance if row.backend != "internal-layered-v1"
        )
        if external:
            provenance = external[0] if len(external) == 1 else None
            selected_runs = tuple(
                run
                for run in selected[0].external_runs
                if provenance is not None and run.backend == provenance.backend
            )
            run = selected_runs[0] if len(selected_runs) == 1 else None
            if (
                run is None
                or provenance is None
                or run.normalized_route_digest is None
                or run.backend != provenance.backend
                or run.version != provenance.version
                or run.executable_digest != provenance.executable_digest
                or run.image_digest != provenance.image_digest
                or run.command_digest != provenance.command_digest
                or run.settings_digest != provenance.settings_digest
                or run.input_digest != provenance.router_input_digest
                or run.output_digest != provenance.router_output_digest
                or run.converter_digest != provenance.converter_digest
                or run.coordinate_quantization != provenance.coordinate_quantization
                or run.specctra_disposal != provenance.specctra_disposal
            ):
                raise ValueError("selected external attempt lacks bound execution provenance")
            if any(
                row.status == "reviewable"
                and not row.routing_attempts
                and not (
                    len(row.external_runs) == 1
                    and row.external_runs[0].normalized_route_digest is not None
                )
                for row in self.comparison.outcomes
            ):
                raise ValueError("external comparison run evidence is inconsistent")
        elif any(
            row.external_runs and not row.routing_attempts for row in self.comparison.outcomes
        ):
            raise ValueError("internal comparison invents external attempts")
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
        if (
            request.native_import_digest != self.binding.native_import_digest
            or request.imported_board_revision
            != (None if self.native_import is None else self.native_import.imported_board_revision)
        ):
            raise OptimizationError("optimization native import was changed")
        if self.judge.project_required != (request.project_capture_digest is not None):
            raise OptimizationError("optimization project requirement was changed")
        if (
            self.judge.project_evidence is not None
            and self.judge.project_evidence.libraries_digest != request.project_libraries_digest
        ):
            raise OptimizationError("optimization project libraries were changed")
        if any(
            run.backend not in request.allowed_backends
            or run.settings_digest != request.routing_profile_digest
            or (
                run.specctra_disposal is not None
                and (
                    run.specctra_disposal.original_target_count != request.target_net_count
                    or run.specctra_disposal.original_target_scope_digest
                    != request.target_net_scope_digest
                )
            )
            for row in self.comparison.outcomes
            for run in row.external_runs
        ):
            raise OptimizationError("optimization comparison execution differs from its request")
        order = routing_backend_order(request.allowed_backends)
        for row in self.comparison.outcomes:
            attempted = tuple(attempt.backend for attempt in row.routing_attempts)
            if attempted != order[: len(attempted)] or (
                len(order) > 1
                and not attempted
                and not (
                    row.status == "budget_exhausted"
                    and row.charged == SlotWork()
                    and not row.external_runs
                )
            ):
                raise OptimizationError(
                    "optimization routing attempts differ from the declared policy"
                )
            if (
                attempted
                and len(attempted) < len(order)
                and row.status != "budget_exhausted"
                and row.routing_attempts[-1].outcome != "composed"
            ):
                raise OptimizationError("optimization routing history is incomplete")
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
