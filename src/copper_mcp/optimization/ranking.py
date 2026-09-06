"""Final objective ordering over fully verified composed optimization packages."""

from __future__ import annotations

from copper_mcp.optimization.contracts import OptimizationError, OptimizationRequest
from copper_mcp.optimization.package import OptimizationPackage


def rank_packages(
    packages: tuple[OptimizationPackage, ...], request: OptimizationRequest
) -> tuple[OptimizationPackage, ...]:
    """Rank only reviewable packages; hard gates cannot be traded for a soft objective.

    Congestion precedes clearance margin inside their shared tier. Intent residual and
    displacement are both nanometre penalties. No Manhattan heuristic enters final ranking.
    """

    request = OptimizationRequest.model_validate(request)
    if type(packages) is not tuple or not 1 <= len(packages) <= request.limits.max_candidates:
        raise OptimizationError("optimization candidate population is invalid")
    checked: list[OptimizationPackage] = []
    seen: set[str] = set()
    for package in packages:
        package = OptimizationPackage.model_validate(package)
        package.require_reviewable_for(request)
        if package.binding.digest in seen:
            raise OptimizationError("optimization candidates must be distinct")
        seen.add(package.binding.digest)
        checked.append(package)
    weights = request.objective_weights

    def key(package: OptimizationPackage) -> tuple[int, int, int, int, int, int, str]:
        metrics = package.metrics
        return (
            -metrics.fully_connected_target_nets,
            metrics.congestion_penalty * weights.congestion,
            -metrics.clearance_margin_nm * weights.clearance_margin,
            metrics.via_count * weights.vias,
            metrics.copper_length_nm * weights.copper_length,
            metrics.intent_residual * weights.intent_residual
            + metrics.displacement_nm * weights.displacement,
            package.binding.digest,
        )

    return tuple(sorted(checked, key=key))
