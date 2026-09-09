"""Equal, non-transferable candidate allocations and measured composed evaluation."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import asdict, replace
from math import isqrt
from typing import cast

from copper_mcp.adapters.kicad_layered_route_patch import KiCadLayeredRoutePatchError
from copper_mcp.adapters.kicad_layered_tree_patch import KiCadLayeredTreePatchError
from copper_mcp.adapters.kicad_route_patch import KiCadRoutePatchError
from copper_mcp.config import Settings
from copper_mcp.optimization.clearance_score import (
    ClearanceMeasurementError,
    measure_composed_clearance,
)
from copper_mcp.optimization.congestion_score import track_via_density
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import AnyJudgeReport, JudgeReportV2
from copper_mcp.optimization.lifecycle import AnyOptimizationJobRecord, ResourceUsage
from copper_mcp.optimization.package import (
    AllocationBasis,
    AnyOptimizationPackage,
    BackendProvenance,
    CandidateBindingV2,
    ClearanceObservation,
    Comparison,
    ComparisonOutcome,
    ObjectiveMetricsV2,
    OptimizationPackageV2,
    SlotBudget,
    SlotWork,
)
from copper_mcp.optimization.placement import search_placements_v2
from copper_mcp.optimization.repair import CopperRepairBinding
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe
from copper_mcp.optimization.zone_fill import CandidateFillBinding, CandidateFillError
from copper_mcp.routing.contracts import AStarSettings


class SlotProbe(OptimizationExecutionProbe):
    """Charges the root before work, while a private allocation limits each slot."""

    def __init__(self, root: OptimizationExecutionProbe, budget: SlotBudget) -> None:
        self.root = root
        self.budget = budget
        self.usage = ResourceUsage()
        self.routing_checks = 0
        self.validation_checks = 0
        self.validation = False
        self.deadline = time.monotonic() + budget.wall_ms / 1000
        self.stopped = False

    def charged_work(self) -> SlotWork:
        return SlotWork(
            expansions=self.usage.expansions,
            route_attempts=self.usage.route_attempts,
            repair_rounds=self.usage.repair_rounds,
            routing_checks=self.routing_checks,
            validation_checks=self.validation_checks,
            external_output_bytes=self.usage.external_output_bytes,
        )

    def remaining_time_ms(self) -> int:
        return min(
            self.root.remaining_time_ms(), max(0, int((self.deadline - time.monotonic()) * 1000))
        )

    def cancelled(self) -> bool:
        return self.root.cancelled() or self.remaining_time_ms() <= 0

    def checkpoint(self) -> AnyOptimizationJobRecord:
        record = self.root.checkpoint()
        if self.remaining_time_ms() <= 0:
            self.stopped = True
            raise OptimizationExecutionError("budget_exhausted")
        return type(record).model_validate(
            {**record.model_dump(), "usage": self.usage.model_dump()}
        )

    def reserve(self, charge: ResourceUsage, **kwargs: object) -> AnyOptimizationJobRecord:
        if kwargs:
            raise OptimizationExecutionError("invalid_candidate")
        self.checkpoint()
        prospective = self.usage.plus(charge)
        checks = (
            self.validation_checks if self.validation else self.routing_checks
        ) + charge.obstacle_checks
        cap = self.budget.validation_checks if self.validation else self.budget.routing_checks
        if (
            checks > cap
            or prospective.expansions > self.budget.expansions
            or prospective.route_attempts > self.budget.route_attempts
            or prospective.repair_rounds > self.budget.repair_rounds
            or prospective.external_output_bytes > self.budget.external_output_bytes
        ):
            self.stopped = True
            raise OptimizationExecutionError("budget_exhausted")
        record = self.root.reserve(charge)
        if record.status in {"budget_exhausted", "cancelled", "failed"}:
            raise OptimizationExecutionError("budget_exhausted")
        self.usage = prospective
        if self.validation:
            self.validation_checks = checks
        else:
            self.routing_checks = checks
        return self.checkpoint()

    def reserve_search(self, settings: AStarSettings, attempts: int = 1) -> AStarSettings:
        expansions = min(
            settings.max_expansions, (self.budget.expansions - self.usage.expansions) // 2
        )
        checks = min(
            settings.max_obstacle_checks, (self.budget.routing_checks - self.routing_checks) // 2
        )
        if expansions < 1 or checks < 1:
            raise OptimizationExecutionError("budget_exhausted")
        self.reserve(
            ResourceUsage(
                route_attempts=2 * attempts, expansions=2 * expansions, obstacle_checks=2 * checks
            )
        )
        return replace(settings, max_expansions=expansions, max_obstacle_checks=checks)


def allocate_slots(
    prepared: PreparedOptimization, probe: OptimizationExecutionProbe, count: int
) -> tuple[AllocationBasis, SlotBudget]:
    if prepared.request.schema_version != "optimization/v2":
        raise OptimizationExecutionError("invalid_candidate")
    usage = probe.checkpoint().usage
    allocation = AllocationBasis(
        limits=prepared.request.limits,
        used_expansions=usage.expansions,
        used_route_attempts=usage.route_attempts,
        used_repair_rounds=usage.repair_rounds,
        used_obstacle_checks=usage.obstacle_checks,
        used_placement_evaluations=usage.placement_evaluations,
        used_candidates=usage.candidates,
        remaining_ms=probe.remaining_time_ms(),
        used_external_output_bytes=usage.external_output_bytes,
    )
    budget = allocation.per_slot(count)
    if min(budget.expansions, budget.route_attempts, budget.routing_checks, budget.wall_ms) < 1:
        raise OptimizationExecutionError("budget_exhausted")
    return allocation, budget


def coordinate_v2(
    prepared: PreparedOptimization,
    settings: Settings,
    probe: OptimizationExecutionProbe,
    *,
    retain_private_result: Callable[[AnyOptimizationPackage, bytes], None],
    observe_judge: Callable[[AnyJudgeReport], None],
) -> OptimizationPackageV2:
    import sys
    from pathlib import Path

    from copper_mcp import __version__
    from copper_mcp.kicad_cli import _context_revision
    from copper_mcp.optimization.contracts import digest_document
    from copper_mcp.optimization.evaluation import (
        composition_context,
        judge_composition,
        verify_original_context,
    )
    from copper_mcp.optimization.provenance import bounded_file_digest, native_implementation_digest
    from copper_mcp.optimization.routing import route_targets

    if prepared.request.allowed_backends != ("internal-layered-v1",):
        raise OptimizationExecutionError("backend_failure")
    verify_original_context(prepared, settings)
    probe.advance("placing")
    placements = search_placements_v2(prepared, settings, probe)
    allocation, budget = allocate_slots(prepared, probe, len(placements))
    probe.advance("evaluating")
    outcomes: list[ComparisonOutcome] = []
    evaluated: list[tuple[CandidateBindingV2, ObjectiveMetricsV2, JudgeReportV2, bytes]] = []
    route_evidence: dict[
        str, tuple[CandidateFillBinding | None, int, CopperRepairBinding | None]
    ] = {}
    last_failure = OptimizationExecutionError("backend_failure")
    for index, placed in enumerate(placements):
        probe.reserve(ResourceUsage(candidates=1))
        slot = SlotProbe(probe, budget)
        try:
            patch_failed = False
            try:
                routed = route_targets(prepared, placed.source, placed.snapshot, settings, slot)
            except (
                KiCadRoutePatchError,
                KiCadLayeredRoutePatchError,
                KiCadLayeredTreePatchError,
                CandidateFillError,
            ):
                patch_failed = True
            if patch_failed:
                probe.checkpoint()
                slot.checkpoint()
                verify_original_context(prepared, settings, deadline=slot.deadline)
                raise OptimizationExecutionError("invalid_candidate")
            if tuple(sorted(routed.connected_targets)) != prepared.target_net_refs:
                raise OptimizationExecutionError("invalid_candidate")
            if routed.route_probes == 0:
                identity = digest_document(
                    "optimization-identity-placement/v2",
                    {
                        "source": prepared.request.board_revision,
                        "snapshot": prepared.snapshot.snapshot_digest,
                    },
                )
                if (
                    index != 0
                    or placed.candidate_id != identity
                    or placed.source != prepared.source
                    or placed.snapshot != prepared.snapshot
                    or routed.base_snapshot_digest != prepared.snapshot.snapshot_digest
                    or placed.displacement_nm
                    or routed.wire_length_nm
                    or routed.vias
                    or routed.candidate_ids
                ):
                    raise OptimizationExecutionError("invalid_candidate")
                if routed.fill is None:
                    if routed.source != prepared.source or routed.snapshot != prepared.snapshot:
                        raise OptimizationExecutionError("invalid_candidate")
                elif (
                    routed.fill_rounds != 1
                    or routed.fill.input_board_revision != prepared.snapshot.content.source.revision
                    or routed.fill.input_snapshot_digest != prepared.snapshot.snapshot_digest
                    or routed.fill.output_board_revision != routed.snapshot.content.source.revision
                ):
                    raise OptimizationExecutionError("invalid_candidate")
            slot.validation = True
            context = composition_context(prepared, routed.source, settings, deadline=slot.deadline)
            binding = CandidateBindingV2(
                board_revision=prepared.request.board_revision,
                snapshot_digest=prepared.snapshot.snapshot_digest,
                placement_candidate_id=placed.candidate_id,
                placed_snapshot_digest=placed.snapshot.snapshot_digest,
                route_bundle_id=routed.digest,
                route_bundle_base_digest=routed.base_snapshot_digest,
                candidate_board_revision=routed.snapshot.content.source.revision,
                rule_context_digest=_context_revision(context),
                final_snapshot_digest=routed.snapshot.snapshot_digest,
                constraint_digest=routed.snapshot.content.constraint_digest,
                drc_profile_digest=prepared.request.drc_profile_digest
                if prepared.request.schema_version == "optimization/v2"
                else "",
                native_import_digest=None
                if prepared.native_import is None
                else prepared.native_import.digest,
                fill_history_digest=routed.fill_history_digest,
                repair_digest=None if routed.repair is None else routed.repair.digest,
            )
            judge = cast(
                JudgeReportV2, judge_composition(prepared, binding, routed.source, settings, slot)
            )
            slot.checkpoint()
            observe_judge(judge)
            if not judge.reviewable:
                raise OptimizationExecutionError(
                    "judge_failed"
                    if judge.aggregate_status == "fail"
                    or (
                        judge.project_evidence is not None
                        and judge.project_evidence.parity_status == "fail"
                    )
                    else "required_domain_inconclusive"
                )
            try:
                observation = measure_composed_clearance(
                    routed.snapshot, slot, expected_snapshot_digest=routed.snapshot.snapshot_digest
                )
            except ClearanceMeasurementError:
                probe.checkpoint()
                slot.checkpoint()
                raise OptimizationExecutionError(
                    "budget_exhausted" if slot.stopped else "invalid_candidate"
                ) from None
            scope = set(prepared.target_net_refs)
            copper = routed.snapshot.content
            slot.reserve(
                ResourceUsage(
                    obstacle_checks=len(copper.segments) + len(copper.vias) + len(copper.arcs)
                )
            )
            if any(arc.net_id in scope for arc in copper.arcs):
                raise OptimizationExecutionError("unsupported_geometry")
            total_length = 0
            for segment in copper.segments:
                slot.checkpoint()
                if segment.net_id in scope:
                    total_length += isqrt(
                        (segment.end.x - segment.start.x) ** 2
                        + (segment.end.y - segment.start.y) ** 2
                    )
            metrics = ObjectiveMetricsV2(
                hard_legality_errors=0,
                hard_drc_errors=0,
                target_net_count=prepared.request.target_net_count,
                fully_connected_target_nets=len(routed.connected_targets),
                congestion_penalty=track_via_density(
                    routed.snapshot, prepared.routing_settings.grid_step_nm * 4, slot
                ),
                clearance=ClearanceObservation.model_validate(asdict(observation)),
                via_count=sum(via.net_id in scope for via in copper.vias),
                copper_length_nm=total_length,
                added_via_count=routed.vias,
                added_copper_length_nm=routed.wire_length_nm,
                displacement_nm=placed.displacement_nm,
                intent_residual=placed.intent_residual_nm,
                actual_route_probes=routed.route_probes,
            )
            slot.checkpoint()
            outcomes.append(
                ComparisonOutcome(
                    placement_candidate_id=placed.candidate_id,
                    role="identity" if index == 0 else "alternative",
                    status="reviewable",
                    candidate_id=binding.digest,
                    metrics=metrics,
                    route_probes=routed.route_probes,
                    charged=slot.charged_work(),
                )
            )
            evaluated.append((binding, metrics, judge, routed.source))
            route_evidence[binding.digest] = (routed.fill, routed.fill_rounds, routed.repair)
        except OptimizationExecutionError as error:
            if error.code in {"cancelled", "stale_revision", "interrupted"}:
                raise
            last_failure = error
            probe.checkpoint()
            outcomes.append(
                ComparisonOutcome.model_validate(
                    {
                        "placement_candidate_id": placed.candidate_id,
                        "role": "identity" if index == 0 else "alternative",
                        "status": error.code,
                        "candidate_id": None,
                        "metrics": None,
                        "route_probes": None,
                        "charged": slot.charged_work(),
                    }
                )
            )
    comparison = Comparison(
        allocation=allocation,
        budget=budget,
        outcomes=tuple(outcomes),
        clearance_ranking="included"
        if all(
            row.metrics is not None and row.metrics.clearance.status == "measured"
            for row in outcomes
        )
        else "omitted_unavailable",
    )
    probe.record_comparison(comparison)
    if not evaluated:
        raise last_failure
    weights = prepared.request.objective_weights

    def rank(
        row: tuple[CandidateBindingV2, ObjectiveMetricsV2, JudgeReportV2, bytes],
    ) -> tuple[int, int, int, int, int, int, str]:
        binding, metrics, _, _ = row
        margin = metrics.clearance.minimum_margin_nm
        return (
            -metrics.fully_connected_target_nets,
            metrics.congestion_penalty * weights.congestion,
            -margin * weights.clearance_margin
            if comparison.clearance_ranking == "included" and margin is not None
            else 0,
            metrics.via_count * weights.vias,
            metrics.copper_length_nm * weights.copper_length,
            metrics.intent_residual * weights.intent_residual
            + metrics.displacement_nm * weights.displacement,
            binding.digest,
        )

    ordered = sorted(evaluated, key=rank)
    binding, metrics, judge, source = ordered[0]
    fill, fill_rounds, repair = route_evidence[binding.digest]
    package = OptimizationPackageV2(
        schema_version="optimization/v2",
        request_digest=prepared.request.digest,
        binding=binding,
        alternate_candidate_ids=tuple(sorted(row[0].digest for row in ordered[1:])),
        metrics=metrics,
        judge=judge,
        comparison=comparison,
        native_import=prepared.native_import,
        zone_fill=fill,
        fill_round_count=fill_rounds,
        repair=repair,
        backend_provenance=(
            BackendProvenance(
                backend="internal-layered-v1",
                version=__version__,
                executable_digest=bounded_file_digest(Path(sys.executable).resolve()),
                command_digest=digest_document(
                    "optimization-native-call/v2", {"operation": "route_targets"}
                ),
                settings_digest=prepared.request.routing_profile_digest,
                input_digest=binding.placed_snapshot_digest,
                source_board_revision=binding.board_revision,
                placed_snapshot_digest=binding.placed_snapshot_digest,
                route_bundle_id=binding.route_bundle_id,
                normalized_output_digest=binding.candidate_board_revision,
            ),
        ),
    )
    package.require_reviewable_for(prepared.request)
    probe.checkpoint()
    verify_original_context(prepared, settings)
    if native_implementation_digest() != prepared.implementation_digest:
        raise OptimizationExecutionError("invalid_candidate")
    probe.checkpoint()
    retain_private_result(package, source)
    return package
