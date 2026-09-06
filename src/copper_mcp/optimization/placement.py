"""Bounded placement search and source-preserving private derivatives for optimization."""

from __future__ import annotations

from dataclasses import dataclass

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.kicad_placement_patch import render_kicad_placement_candidate_board
from copper_mcp.board_ir import BoardIRSnapshot, verify_snapshot
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.optimization.inputs import PreparedOptimization
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationExecutionProbe
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.placement import build_placement_view
from copper_mcp.placement.solver import PlacementSolverSettings, solve_placement


@dataclass(frozen=True)
class PrivatePlacement:
    source: bytes
    snapshot: BoardIRSnapshot
    candidate_id: str
    displacement_nm: int
    intent_residual_nm: int


def search_placements(
    prepared: PreparedOptimization, settings: Settings, probe: OptimizationExecutionProbe
) -> tuple[PrivatePlacement, ...]:
    """Use Manhattan only to explore a beam; final selection requires actual routing and DRC."""

    snapshot = prepared.snapshot
    verify_snapshot(snapshot)
    if prepared.placement_intent is None:
        # This identity operation contains no invented footprint pose or empty copper candidate.
        # Its proof is equality of the captured bytes and the verified unchanged Board IR.
        identity = digest_document(
            "optimization-identity-placement/v1",
            {"source": prepared.request.board_revision, "snapshot": snapshot.snapshot_digest},
        )
        return (PrivatePlacement(prepared.source, snapshot, identity, 0, 0),)
    limits = prepared.request.limits
    evaluations = limits.max_placement_evaluations
    remaining_checks = limits.max_obstacle_checks - probe.checkpoint().usage.obstacle_checks
    per_evaluation = min(settings.max_placement_checks, remaining_checks // (4 * evaluations))
    if per_evaluation < 1:
        raise OptimizationExecutionError("budget_exhausted")
    probe.reserve(
        ResourceUsage(
            placement_evaluations=evaluations, obstacle_checks=per_evaluation * evaluations
        )
    )
    view = build_placement_view(prepared.source, snapshot, limits=parse_limits_for(settings))
    seconds = min(60.0, settings.max_placement_seconds, probe.remaining_time_ms() / 1000)
    if seconds <= 0:
        raise OptimizationExecutionError("budget_exhausted")
    solved = solve_placement(
        prepared.placement_intent,
        snapshot,
        view,
        settings=PlacementSolverSettings(
            max_evaluations=evaluations,
            max_ranked=limits.max_candidates,
            step_nm=prepared.request.placement_scope.grid_nm,
            deadline_seconds=seconds,
            legalizer_deadline_seconds=seconds,
            legalizer_max_checks=per_evaluation,
        ),
        cancelled=probe.cancelled,
        board_path=prepared.board_path,
    )
    probe.checkpoint()
    if solved.status == "cancelled":
        # A durable cancellation is raised by checkpoint above. An isolated solver stop
        # without that durable state is a backend failure, not authority to cancel a job.
        raise OptimizationExecutionError("backend_failure")
    if solved.status in {"deadline_exhausted", "legalizer_exhausted"}:
        raise OptimizationExecutionError("budget_exhausted")
    if solved.status not in {"completed", "work_exhausted"}:
        raise OptimizationExecutionError("unsupported_geometry")
    results: list[PrivatePlacement] = []
    allowed = set(prepared.request.placement_scope.movable_footprint_refs)
    footprints = {item.id: item for item in snapshot.content.footprints}
    for ranked in solved.ranked:
        candidate = ranked.candidate
        displacement = 0
        for pose in candidate.placements:
            original = footprints[pose.ref_id]
            if pose.side != original.side.value or (
                pose.moved and (original.locked or pose.ref_id not in allowed)
            ):
                raise OptimizationExecutionError("invalid_candidate")
            if (
                pose.orientation_udeg // 1_000_000
                not in prepared.request.placement_scope.cardinal_rotations
            ):
                raise OptimizationExecutionError("unsupported_geometry")
            displacement += abs(pose.origin_x_nm - original.origin.x) + abs(
                pose.origin_y_nm - original.origin.y
            )
        if not candidate.evidence.legality.legal:
            raise OptimizationExecutionError("invalid_candidate")
        source = render_kicad_placement_candidate_board(
            prepared.source,
            snapshot,
            candidate,
            prepared.profile,
            limits=parse_limits_for(settings),
        )
        converted = parse_kicad_bytes(source, prepared.profile, parse_limits_for(settings))
        if converted.snapshot is None or converted.diagnostics:
            raise OptimizationExecutionError("invalid_candidate")
        results.append(
            PrivatePlacement(
                source,
                converted.snapshot,
                candidate.candidate_id,
                displacement,
                sum(rule.residual_nm for rule in candidate.evidence.rule_results),
            )
        )
        probe.checkpoint()
    if not results:
        raise OptimizationExecutionError("unsupported_geometry")
    return tuple(results)
