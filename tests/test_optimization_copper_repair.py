"""Private copper removal preserves locks, source semantics, budgets and declared scope."""

import hashlib
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_layered_tree_router import _profile
from test_optimization_coordinator import run
from test_optimization_coordinator import synthetic_authority as synthetic_authority
from test_optimization_mcp import app as app
from test_optimization_mcp import call
from test_optimization_zoned_workflow import board_source

from copper_mcp import kicad_cli
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.optimization import repair, routing
from copper_mcp.optimization.contracts import OptimizationError
from copper_mcp.optimization.inputs import default_limits_v2
from copper_mcp.optimization.worker import OptimizationExecutionError, OptimizationJobCancelledError
from copper_mcp.request_boundary import net_class_constraints


def _case(tmp_path, *, locked=False, grouped=False):
    source = board_source("routed-pair")
    if locked:
        source = source.replace(b"(segment (start 8 24)", b"(segment (locked yes) (start 8 24)")
    extra = b"""(segment (locked yes) (start 8 8) (end 10 8) (width 0.25)
      (layer "F.Cu") (net "GND") (uuid "67000000-0000-0000-0000-000000000001"))
      (via (at 10 24) (size 0.8) (drill 0.4) (layers "F.Cu" "B.Cu") (net "SIGNAL")
        (uuid "68000000-0000-0000-0000-000000000001"))
      (arc (start 12 24) (mid 13 23) (end 14 24) (width 0.25) (layer "F.Cu")
        (net "SIGNAL") (uuid "69000000-0000-0000-0000-000000000001"))"""
    if grouped:
        member = {"nested-via": "68", "nested-arc": "69"}.get(grouped, "66")
        group = f'''(group "keep" (uuid "6a000000-0000-0000-0000-000000000001")
          (members "{member}000000-0000-0000-0000-000000000001"))'''.encode()
        if isinstance(grouped, str):
            source = source.replace(b"(at 8 24)", b"(at 8 24)" + group, 1)
        else:
            extra += group
    source = source.replace(b'(generator "pcbnew")', '(generator "µ owned fixture")'.encode())
    source = source.rstrip()[:-1] + extra + b")\n"
    profile = _profile()
    converted = parse_kicad_bytes(source, profile)
    assert converted.snapshot is not None and not converted.diagnostics, converted.diagnostics
    snapshot = converted.snapshot
    signal = next(net.id for net in snapshot.content.nets if net.name == "SIGNAL")
    prepared = SimpleNamespace(
        request=SimpleNamespace(schema_version="optimization/v2"),
        target_net_refs=(signal,),
        profile=profile,
    )
    path = tmp_path / "board.kicad_pcb"
    path.write_bytes(source)
    charges = []
    probe = SimpleNamespace(checkpoint=lambda: None, reserve=charges.append)
    args = (
        prepared,
        source,
        snapshot,
        (signal,),
        snapshot.snapshot_digest,
        Settings(workspace=tmp_path),
        probe,
    )
    return args, path, charges


def test_repair_removes_exact_declared_copper_and_preserves_every_other_expression(tmp_path):
    args, path, charges = _case(tmp_path)
    original = path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns
    result = repair.remove_target_copper(*args)
    assert (
        result.binding.removed_segments
        == result.binding.removed_vias
        == result.binding.removed_arcs
        == 1
    )
    assert result.binding.target_net_count == 1
    assert len(result.snapshot.content.segments) == 1
    assert result.snapshot.content.segments[0].locked
    assert not result.snapshot.content.vias and not result.snapshot.content.arcs
    assert result.snapshot.content.footprints == args[2].content.footprints
    assert result.snapshot.content.zones == args[2].content.zones
    assert '(generator "µ owned fixture")'.encode() in result.source
    assert sum(charge.repair_rounds for charge in charges) == 1
    assert (path.read_bytes(), path.stat().st_ino, path.stat().st_mtime_ns) == original
    assert repair.remove_target_copper(*args) == result


@pytest.mark.parametrize(
    "protected", ["locked", "grouped", "nested-segment", "nested-via", "nested-arc"]
)
def test_repair_refuses_protected_copper_without_modifying_source(tmp_path, protected):
    args, path, _charges = _case(
        tmp_path,
        locked=protected == "locked",
        grouped=protected if protected.startswith("nested-") else protected == "grouped",
    )
    original = path.read_bytes()
    with pytest.raises(OptimizationExecutionError) as error:
        repair.remove_target_copper(*args)
    assert error.value.code == "unsupported_geometry"
    assert path.read_bytes() == original


@pytest.mark.parametrize("fault", ["scope", "source", "budget", "cancelled"])
def test_repair_failure_cannot_return_a_partial_derivative(tmp_path, monkeypatch, fault):
    original_args, path, charges = _case(tmp_path)
    args = list(original_args)
    expected = "invalid_candidate"
    if fault == "scope":
        args[3] = ("net:outside",)
    elif fault == "source":
        args[1] += b"\n"
    else:
        expected = "budget_exhausted" if fault == "budget" else "cancelled"

        def refuse(*_args):
            if fault == "cancelled":
                raise OptimizationJobCancelledError("owned cancellation")
            raise OptimizationExecutionError(expected)

        if fault == "budget":
            args[-1].reserve = refuse
            monkeypatch.setattr(
                repair, "parse_kicad_bytes", lambda *_a: pytest.fail("unbudgeted parse")
            )
        else:
            args[-1].checkpoint = refuse
    with pytest.raises(
        OptimizationJobCancelledError if fault == "cancelled" else OptimizationExecutionError
    ) as error:
        repair.remove_target_copper(*args)
    if fault != "cancelled":
        assert error.value.code == expected
    assert path.read_bytes() == original_args[1]
    if fault == "scope":
        assert not charges


def _plain_launch(tmp_path, copper):
    source = (Path(__file__).parent / "fixtures/route-candidate/two-pad.kicad_pcb").read_bytes()
    source = source.rstrip()[:-1] + copper + b")\n"
    (tmp_path / "board.kicad_pcb").write_bytes(source)
    constraints = {
        "clearance_nm": 250000,
        "track_width_nm": 250000,
        "via_diameter_nm": 800000,
        "via_drill_nm": 400000,
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
        "limits": {
            **default_limits_v2().model_dump(),
            "max_candidates": 1,
            "max_repair_rounds": 1,
            "max_runtime_ms": 120000,
        },
    }


@pytest.mark.parametrize("case", ["minor-arc", "major-arc", "rounded-endcap"])
def test_unchanged_arc_connected_net_is_not_reset_when_connectivity_is_unproven(
    tmp_path, monkeypatch, synthetic_authority, case
):
    copper = (
        {
            "minor-arc": b"(arc (start 10 15) (mid 20 5) (end 30 15)",
            "major-arc": b"(arc (start 10 15) (mid 20 3) (end 30 15)",
            "rounded-endcap": b"(segment (start 10 15) (end 28.9 15)",
        }[case]
        + b"""(width 0.25) (layer "F.Cu") (net "AUDIO")
      (uuid "6b000000-0000-0000-0000-000000000001"))"""
    )
    launch = _plain_launch(tmp_path, copper)
    before = (tmp_path / "board.kicad_pcb").read_bytes()
    monkeypatch.setattr(
        routing,
        "remove_target_copper",
        lambda *_a: pytest.fail("unproven connectivity authorized a reset"),
    )
    record, retained, package = run(launch, tmp_path)
    assert record.failure_code == "unsupported_geometry"
    assert not retained and package is None
    assert (tmp_path / "board.kicad_pcb").read_bytes() == before


def test_disjoint_arc_envelope_can_prove_disconnection_before_repair(
    tmp_path, monkeypatch, synthetic_authority
):
    launch = _plain_launch(
        tmp_path,
        b"""(arc (start 10 15) (mid 11 14) (end 12 15)
      (width 0.25) (layer "F.Cu") (net "AUDIO")
      (uuid "6b000000-0000-0000-0000-000000000001"))""",
    )
    resets = []
    original = routing.remove_target_copper

    def observed(*args):
        result = original(*args)
        resets.append(result.binding)
        return result

    monkeypatch.setattr(routing, "remove_target_copper", observed)
    record, retained, package = run(launch, tmp_path)
    assert record.status == "awaiting_approval" and len(resets) == 1
    assert len(retained) == 1 and package.repair == resets[0]
    assert package.repair.removed_arcs == 1


@pytest.mark.parametrize("failure", ["routing", "drc"])
def test_post_reset_failure_retains_no_private_candidate_or_package(
    app, tmp_path, monkeypatch, synthetic_authority, failure
):
    launch = _plain_launch(
        tmp_path,
        b"""(segment (start 10 15) (end 12 15)
      (width 0.25) (layer "F.Cu") (net "AUDIO")
      (uuid "6b000000-0000-0000-0000-000000000001"))""",
    )
    source = (tmp_path / "board.kicad_pcb").read_bytes()
    resets = []
    original = routing.remove_target_copper

    def observed(*args):
        result = original(*args)
        resets.append(result.binding)
        assert not result.snapshot.content.segments
        return result

    monkeypatch.setattr(routing, "remove_target_copper", observed)
    if failure == "routing":

        def fail_route(*_args, **_kwargs):
            raise OptimizationExecutionError("backend_failure")

        monkeypatch.setattr(routing.AStarRouter, "propose", fail_route)
    else:
        original_drc = kicad_cli._run_captured_drc

        def failing_drc(*args, **kwargs):
            return replace(
                original_drc(*args, **kwargs),
                error_count=1,
                passed=False,
                violation_type_counts={"clearance": 1},
            )

        monkeypatch.setattr(kicad_cli, "_run_captured_drc", failing_drc)
    server, gateway = app
    started = call(server, "start_optimization", launch)["record"]
    service, owner = gateway.service()
    service._jobs[started["job_id"]].future.result(timeout=120)
    record, _ = service.get(started["job_id"], owner)
    assert record.failure_code == ("backend_failure" if failure == "routing" else "judge_failed")
    assert len(resets) == 1 and record.usage.repair_rounds == 1
    assert record.package_digest is None and record.judge_digest is None
    assert all(row.candidate_id is None for row in record.comparison.outcomes)
    assert service._jobs[record.job_id].source is None
    assert not service._jobs[record.job_id].judges and not gateway._artifacts
    with pytest.raises(OptimizationError):
        service.repository.get_package(record.job_id, owner)
    assert (tmp_path / "board.kicad_pcb").read_bytes() == source
