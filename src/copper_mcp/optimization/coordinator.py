"""Supervised native placement/routing/judge loop; every board derivative stays private."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path

from copper_mcp import __version__
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, _context_revision
from copper_mcp.optimization.congestion_score import track_via_density
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.evaluation import (
    composition_context,
    judge_composition,
    verify_original_context,
)
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.judge import JudgeReport
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.package import (
    BackendProvenance,
    CandidateBinding,
    ObjectiveMetrics,
    OptimizationPackage,
)
from copper_mcp.optimization.placement import PrivatePlacement, search_placements
from copper_mcp.optimization.provenance import bounded_file_digest, native_implementation_digest
from copper_mcp.optimization.ranking import rank_packages
from copper_mcp.optimization.routing import PrivateRouteComposition, route_targets
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe


def _package(
    prepared: PreparedOptimization,
    placed: PrivatePlacement,
    routed: PrivateRouteComposition,
    settings: Settings,
    probe: OptimizationExecutionProbe,
    observe_judge: Callable[[JudgeReport], None],
) -> OptimizationPackage:
    if tuple(sorted(routed.connected_targets)) != prepared.target_net_refs:
        raise OptimizationExecutionError("invalid_candidate")
    context = composition_context(prepared, routed.source, settings)
    binding = CandidateBinding(
        board_revision=prepared.request.board_revision,
        snapshot_digest=prepared.snapshot.snapshot_digest,
        placement_candidate_id=placed.candidate_id,
        placed_snapshot_digest=placed.snapshot.snapshot_digest,
        route_bundle_id=routed.digest,
        route_bundle_base_digest=routed.base_snapshot_digest,
        candidate_board_revision=routed.snapshot.content.source.revision,
        rule_context_digest=_context_revision(context),
    )
    judge = judge_composition(prepared, binding, routed.source, settings, probe)
    observe_judge(judge)
    if not judge.reviewable:
        raise OptimizationExecutionError(
            "judge_failed" if judge.aggregate_status == "fail" else "required_domain_inconclusive"
        )
    return OptimizationPackage(
        schema_version="optimization/v1",
        request_digest=prepared.request.digest,
        binding=binding,
        alternate_candidate_ids=(),
        metrics=ObjectiveMetrics(
            hard_legality_errors=0,
            hard_drc_errors=0,
            target_net_count=prepared.request.target_net_count,
            fully_connected_target_nets=len(routed.connected_targets),
            # Conservative guaranteed excess-clearance lower bound after mandatory DRC.
            # This baseline does not claim to measure unused clearance headroom.
            clearance_margin_nm=0,
            congestion_penalty=track_via_density(
                routed.snapshot, prepared.routing_settings.grid_step_nm * 4, probe
            ),
            via_count=routed.vias,
            copper_length_nm=routed.wire_length_nm,
            displacement_nm=placed.displacement_nm,
            intent_residual=placed.intent_residual_nm,
            actual_route_probes=routed.route_probes,
        ),
        judge=judge,
        backend_provenance=(
            BackendProvenance(
                backend="internal-layered-v1",
                version=__version__,
                executable_digest=bounded_file_digest(Path(sys.executable).resolve()),
                command_digest=digest_document(
                    "optimization-native-call/v1", {"operation": "route_targets"}
                ),
                settings_digest=prepared.request.routing_profile_digest,
                input_digest=placed.snapshot.snapshot_digest,
                source_board_revision=prepared.request.board_revision,
                placed_snapshot_digest=placed.snapshot.snapshot_digest,
                route_bundle_id=routed.digest,
                normalized_output_digest=routed.snapshot.content.source.revision,
            ),
        ),
    )


def coordinate_optimization(
    prepared: PreparedOptimization,
    settings: Settings,
    probe: OptimizationExecutionProbe,
    *,
    retain_private_result: Callable[[OptimizationPackage, bytes], None],
    observe_judge: Callable[[JudgeReport], None] = lambda _report: None,
) -> OptimizationPackage:
    """Return a verified package for host review, without invoking any apply surface."""

    if prepared.request.allowed_backends != ("internal-layered-v1",):
        # External engines require the operator-installed container runtime plus conversion.
        # Never silently substitute a native run for a requested hybrid experiment.
        raise OptimizationExecutionError("backend_failure")
    if prepared.snapshot.content.zones:
        # Placement/routing changes invalidate cached fill. The optimization composition
        # needs its own candidate-bound refill transaction before DRC can use that copper.
        raise OptimizationExecutionError("unsupported_geometry")
    try:
        verify_original_context(prepared, settings)
    except KiCadCliError:
        raise OptimizationExecutionError("stale_revision") from None
    probe.advance("placing")
    placements = search_placements(prepared, settings, probe)
    probe.advance("routing")
    routed_results: list[tuple[PrivatePlacement, PrivateRouteComposition]] = []
    last_failure = "backend_failure"
    for ordinal, placed in enumerate(placements):
        probe.reserve(ResourceUsage(candidates=1, route_attempts=int(ordinal > 0)))
        try:
            routed_results.append(
                (placed, route_targets(prepared, placed.source, placed.snapshot, settings, probe))
            )
        except OptimizationExecutionError as error:
            last_failure = error.code
            probe.checkpoint()
            if error.code == "budget_exhausted":
                break
    if not routed_results:
        if last_failure == "budget_exhausted":
            raise OptimizationExecutionError("budget_exhausted")
        raise OptimizationExecutionError("backend_failure")
    probe.advance("judging")
    verified: list[OptimizationPackage] = []
    sources: dict[str, bytes] = {}
    for placed, routed in routed_results:
        try:
            package = _package(prepared, placed, routed, settings, probe, observe_judge)
        except OptimizationExecutionError as error:
            last_failure = error.code
            probe.checkpoint()
            continue
        if package.judge.reviewable:
            package.require_reviewable_for(prepared.request)
            verified.append(package)
            sources[package.binding.digest] = routed.source
        elif package.judge.aggregate_status == "fail":
            last_failure = "judge_failed"
        else:
            last_failure = "required_domain_inconclusive"
    if not verified:
        if last_failure == "judge_failed":
            raise OptimizationExecutionError("judge_failed")
        raise OptimizationExecutionError("required_domain_inconclusive")
    ranked = rank_packages(tuple(verified), prepared.request)
    selected = OptimizationPackage.model_validate(
        {
            **ranked[0].model_dump(),
            "alternate_candidate_ids": tuple(sorted(item.binding.digest for item in ranked[1:])),
        }
    )
    probe.checkpoint()
    if native_implementation_digest() != prepared.implementation_digest:
        raise OptimizationExecutionError("invalid_candidate")
    try:
        verify_original_context(prepared, settings)
    except KiCadCliError:
        raise OptimizationExecutionError("stale_revision") from None
    retain_private_result(selected, sources[selected.binding.digest])
    return selected
