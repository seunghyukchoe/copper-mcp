"""Zero-search identity is a checked v2 source-equality case, never invented probe evidence."""

import hashlib
from pathlib import Path

import pytest
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.inputs import default_limits_v2, prepare_optimization
from copper_mcp.optimization.package import OptimizationPackageV2
from copper_mcp.request_boundary import net_class_constraints


@pytest.fixture
def connected_launch(tmp_path):
    source = (
        Path(__file__).parent / "fixtures/route-candidate/connected-net.kicad_pcb"
    ).read_bytes()
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    constraints = {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }
    nc = net_class_constraints(constraints)
    snapshot = parse_kicad_bytes(
        source, KiCadConstraintProfile(net_classes=(nc,), default_net_class_id=nc.id)
    ).snapshot
    assert snapshot is not None
    return {
        "schema_version": "optimization/v2",
        "board": "board.kicad_pcb",
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": constraints,
        "target_net_refs": [snapshot.content.pads[0].net_id],
        "movable_footprint_refs": [snapshot.content.footprints[0].id],
        "limits": {**default_limits_v2().model_dump(), "max_candidates": 1},
    }


def test_zero_probe_identity_preserves_every_original_binding(
    connected_launch, tmp_path, synthetic_authority
):
    original = (tmp_path / "board.kicad_pcb").read_bytes()
    record, retained, package = run(connected_launch, tmp_path)
    assert record.status == "awaiting_approval", record.failure_code
    assert retained[0][1] == original
    assert package.metrics.actual_route_probes == 0
    assert (
        package.metrics.displacement_nm
        == package.metrics.copper_length_nm
        == package.metrics.via_count
        == 0
    )
    assert package.binding.candidate_board_revision == package.binding.board_revision
    assert (
        package.binding.snapshot_digest
        == package.binding.placed_snapshot_digest
        == package.binding.final_snapshot_digest
    )
    assert package.comparison.outcomes[0].candidate_id == package.binding.digest
    assert package.comparison.outcomes[0].role == "identity"


@pytest.mark.parametrize(
    "mutation",
    [
        "placement_candidate_id",
        "placed_snapshot_digest",
        "final_snapshot_digest",
        "candidate_board_revision",
        "displacement_nm",
        "copper_length_nm",
        "via_count",
        "role",
    ],
)
def test_zero_probe_identity_cannot_be_used_for_changed_or_nonidentity_output(
    connected_launch, tmp_path, synthetic_authority, mutation
):
    prepared = prepare_optimization(
        connected_launch, Settings(workspace=tmp_path, max_route_preview_seconds=120)
    )
    _, _, package = run(connected_launch, tmp_path)
    assert package is not None
    fields = package.model_dump()
    if mutation == "role":
        fields["comparison"]["outcomes"][0]["role"] = "alternative"
    elif mutation.endswith("_nm") or mutation == "via_count":
        fields["metrics"][mutation] = 1
    else:
        fields["binding"][mutation] = "sha256:" + "f" * 64
    with pytest.raises((ValueError, OptimizationError)):
        altered = OptimizationPackageV2.model_validate(fields)
        altered.require_reviewable_for(prepared.request)
