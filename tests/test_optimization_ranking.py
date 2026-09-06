"""Synthetic final-ranking behavior, independent of route search or live DRC claims."""

import pytest
from test_optimization_foundation import digest, package, request

from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.package import CandidateBinding, OptimizationPackage
from copper_mcp.optimization.ranking import rank_packages


def alternative(character: str, **metrics: int) -> OptimizationPackage:
    document = package().model_dump()
    document["binding"]["placement_candidate_id"] = digest(character)
    binding = CandidateBinding.model_validate(document["binding"])
    document["judge"]["candidate_id"] = binding.digest
    for domain in document["judge"]["domains"]:
        if domain["evidence"] is not None:
            domain["evidence"]["candidate_id"] = binding.digest
    document["metrics"].update(metrics)
    return OptimizationPackage.model_validate(document)


@pytest.mark.parametrize(
    ("better", "worse"),
    [
        ({"congestion_penalty": 0, "via_count": 100}, {"congestion_penalty": 1}),
        ({"clearance_margin_nm": 2000, "via_count": 100}, {"clearance_margin_nm": 1000}),
        ({"via_count": 0, "copper_length_nm": 100_000_000}, {"via_count": 1}),
        ({"copper_length_nm": 1, "displacement_nm": 100_000_000}, {"copper_length_nm": 2}),
        (
            {"intent_residual": 0, "displacement_nm": 1},
            {"intent_residual": 1, "displacement_nm": 1},
        ),
    ],
)
def test_quality_tiers_cannot_be_bought_with_a_lower_priority_objective(better, worse):
    first, second = alternative("0", **better), alternative("1", **worse)
    assert rank_packages((second, first), request()) == (first, second)


@pytest.mark.parametrize(
    "metrics",
    [
        {"hard_legality_errors": 1},
        {"hard_drc_errors": 1},
        {"fully_connected_target_nets": 1},
        {"actual_route_probes": 0},
    ],
)
def test_short_routes_cannot_make_unverified_packages_selectable(metrics):
    with pytest.raises(OptimizationError):
        rank_packages((alternative("0", copper_length_nm=0, **metrics),), request())


def test_candidate_identity_breaks_exact_ties_independently_of_input_order():
    first, second = alternative("0"), alternative("1")
    expected = tuple(sorted((first, second), key=lambda item: item.binding.digest))
    assert rank_packages((first, second), request()) == expected
    assert rank_packages((second, first), request()) == expected
    with pytest.raises(OptimizationError):
        rank_packages((first, first), request())
