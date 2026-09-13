"""Complete zoned-board optimization through MCP; native and owned synthetic backends."""

import asyncio
import hashlib
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import types
from pydantic import ValidationError
from test_optimization_mcp import modern_wire_call

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.mcp_server import CopperMCPServer
from copper_mcp.optimization.mcp import register_optimization_tools
from copper_mcp.optimization.package import OptimizationPackageV2
from copper_mcp.optimization.zone_fill import replace_fill_cache
from copper_mcp.request_boundary import net_class_constraints

FIXTURE = Path(__file__).parent / "fixtures/route-candidate/zone-fill-fresh.kicad_pcb"
NATIVE = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


def board_source(mixed):
    if mixed == "partial-pair":
        return board_source("routed-pair").replace(b"(end 30 24)", b"(end 12 24)")
    if mixed == "routed-pair":
        source = board_source(True)
        return (
            source.rstrip()[:-1]
            + b"""(segment (start 8 24) (end 30 24) (width 0.25)
          (layer "F.Cu") (net "SIGNAL") (uuid "66000000-0000-0000-0000-000000000001"))\n)\n"""
        )
    if mixed == "tree":
        source = FIXTURE.with_name("layered-tree-ordinary-4layer.kicad_pcb").read_bytes()
        source = source.replace(b'(net "POWER")', b'(net "BLOCKER_F")', 1).replace(
            b'(net "POWER")', b'(net "BLOCKER_B")', 1
        )
        additions = []
        for index, x in enumerate((8, 12), 1):
            additions.append(f"""(footprint "Owned:Ground" (layer "F.Cu")
              (uuid "63000000-0000-0000-0000-00000000000{index}") (at {x} 5)
              (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1)
                (layers "*.Cu" "*.Mask") (net "GND")
                (uuid "64000000-0000-0000-0000-00000000000{index}")))""")
        additions.append("""(zone (net "GND") (layer "F.Cu")
          (uuid "65000000-0000-0000-0000-000000000001") (hatch edge 0.5)
          (connect_pads (clearance 0.25)) (min_thickness 0.25)
          (fill yes (thermal_gap 0.3) (thermal_bridge_width 0.3))
          (polygon (pts (xy 4 2) (xy 16 2) (xy 16 8) (xy 4 8))))""")
        return source.rstrip()[:-1] + "\n".join(additions).encode() + b")\n"
    source = replace_fill_cache(FIXTURE.read_bytes(), None, ParseLimits(), checkpoint=lambda: None)
    assert b"(filled_polygon" not in source
    if mixed:
        footprints = []
        for index, x in enumerate((8, 30), 1):
            footprints.append(f"""(footprint "Owned:Signal" (layer "F.Cu")
              (uuid "61000000-0000-0000-0000-00000000000{index}") (at {x} 24)
              (pad "1" thru_hole circle (at 0 0) (size 2 2) (drill 1)
                (layers "*.Cu" "*.Mask") (net "SIGNAL")
                (uuid "62000000-0000-0000-0000-00000000000{index}")))""")
        source = source.rstrip()[:-1] + "\n".join(footprints).encode() + b")\n"
    return source


def synthetic_cli(tmp_path):
    script = tmp_path / "synthetic-fill-cli"
    src = str(Path(__file__).resolve().parents[1] / "src")
    script.write_text(
        f"#!{sys.executable}\nimport sys\nsys.path.insert(0, {src!r})\n"
        + """
import json,pathlib
from copper_mcp.adapters.sexpr import parse_sexpr,children,child,atoms
from copper_mcp.adapters.cst import span,Splice,apply_splices
args=sys.argv[1:]
if args==['--version']:
 print('10.0.5');sys.exit(0)
if args[:2]!=['pcb','drc']:sys.exit(2)
board=pathlib.Path(args[-1])
if '--refill-zones' in args:
 text=board.read_text();root=parse_sexpr(text.encode());edits=[]
 for zone in children(root,'zone'):
  for cached in children(zone,'filled_polygon'):
   a,b=span(cached,text);edits.append(Splice(a,b,''))
  points=child(child(zone,'polygon'),'pts');a,b=span(points,text)
  layer=atoms(child(zone,'layer'))[0]
  _,end=span(zone,text)
  cache='(filled_polygon (layer '+json.dumps(layer)+') '+text[a:b]+')'
  edits.append(Splice(end-1,end-1,cache))
 board.write_text(apply_splices(text,edits))
 board.with_suffix('.kicad_prl').write_text('{}')
report={'$schema':'https://schemas.kicad.org/drc.v1.json','source':board.name,
 'date':'2026-09-09T00:00:00Z','coordinate_units':'mm','kicad_version':'10.0.5',
 'violations':[],'unconnected_items':[],'schematic_parity':[],
 'included_severities':['error','warning','exclusion'],'ignored_checks':[]}
pathlib.Path(args[args.index('--output')+1]).write_text(json.dumps(report))
"""
    )
    script.chmod(0o700)
    return script


@pytest.mark.parametrize(
    "backend", ["synthetic", pytest.param("native", marks=pytest.mark.real_kicad)]
)
@pytest.mark.parametrize(
    "mixed",
    [False, True, "tree", "routed-pair", "partial-pair"],
    ids=[
        "ground-fill",
        "placement-routing-and-fill",
        "cross-layer-tree-placement-and-fill",
        "existing-copper-placement-and-repair",
        "partial-copper-routing-repair",
    ],
)
def test_mcp_compares_and_checks_complete_fresh_zoned_candidates(tmp_path, backend, mixed):
    if backend == "native" and not NATIVE:
        pytest.skip("requires explicitly configured native KiCad")
    source = board_source(mixed)
    movement = bool(mixed) and mixed != "partial-pair"
    board = tmp_path / "board.kicad_pcb"
    board.write_bytes(source)
    before = board.stat()
    constraints = {
        "clearance_nm": 250000,
        "track_width_nm": 250000,
        "via_diameter_nm": 800000,
        "via_drill_nm": 400000,
    }
    net_class = net_class_constraints(constraints)
    profile = KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)
    converted = parse_kicad_bytes(source, profile)
    assert converted.snapshot is not None and not converted.diagnostics
    snapshot = converted.snapshot
    request = {
        "schema_version": "optimization/v2",
        "board": board.name,
        "expect_board_revision": "sha256:" + hashlib.sha256(source).hexdigest(),
        "expect_snapshot_digest": snapshot.snapshot_digest,
        "constraints": constraints,
        "target_net_refs": [
            net.id
            for net in snapshot.content.nets
            if sum(pad.net_id == net.id for pad in snapshot.content.pads) >= 2
        ],
        "routing_settings": {
            "grid_step_nm": 1000000,
            "max_expansions": 50000,
            "max_obstacle_checks": 100000,
            "max_grid_nodes": 10000,
        },
        "limits": {
            "max_runtime_ms": 120000,
            "max_candidates": 2 if movement else 1,
            "max_placement_evaluations": 8,
            "max_route_attempts": 64,
            "max_repair_rounds": {"routed-pair": 2, "partial-pair": 1}.get(mixed, 0),
            "max_expansions": 1000000,
            "max_obstacle_checks": 10000000,
            "max_external_output_bytes": 2097152,
        },
    }
    if movement:
        subject = (
            "footprint:kicad:70000000-0000-0000-0000-000000000005"
            if mixed == "tree"
            else "footprint:kicad:61000000-0000-0000-0000-000000000002"
        )
        (tmp_path / "intent.json").write_text(
            json.dumps(
                {
                    "proposals": [
                        {
                            "subject": subject,
                            "offset_x_nm": -4000000 if mixed == "routed-pair" else -2000000,
                            **({"offset_y_nm": 2000000} if mixed == "routed-pair" else {}),
                        }
                    ]
                }
            )
        )
        request.update(
            movable_footprint_refs=[subject],
            placement_intent_path="intent.json",
            placement_grid_nm=1000000,
        )
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=Path(NATIVE) if backend == "native" else synthetic_cli(tmp_path),
        max_route_preview_seconds=120,
        optimization_host_confirmation=False,
    )
    server = CopperMCPServer(name="Owned complete zoned optimization")
    gateway = register_optimization_tools(server, lambda: settings)

    async def call(name, payload):
        result = await modern_wire_call(server, name, payload, advertise_elicitation=False)
        assert isinstance(result, types.CallToolResult) and not result.is_error
        return result.structured_content

    async def exercise():
        started = await call("start_optimization", request)
        job = started["record"]["job_id"]
        service, _ = gateway.service()
        await asyncio.to_thread(service._jobs[job].future.result, timeout=150)
        status = await call("get_optimization_job", {"job_id": job})
        record = status["record"]
        assert record["status"] == "awaiting_approval", (
            record["failure_code"],
            status["judge_reports"],
        )
        exported = await call(
            "export_optimization_package",
            {
                "job_id": job,
                "expected_record_revision": record["revision"],
                "expected_package_digest": record["package_digest"],
                "include_geometry": False,
            },
        )
        package = exported["package"]
        fill = package["zone_fill"]
        assert fill["output_board_revision"] == package["binding"]["candidate_board_revision"]
        assert fill["fill_digest"] == fill["repeat_fill_digest"]
        assert fill["island_count"] > 0 and package["fill_round_count"] > 0
        assert package["metrics"]["fully_connected_target_nets"] == (2 if mixed else 1)
        assert package["metrics"]["hard_drc_errors"] == 0
        assert package["metrics"]["clearance"]["status"] == "unavailable"
        assert (
            exported["required_status"] == "pass" and exported["aggregate_status"] == "inconclusive"
        )
        assert status["apply_authority"] == "none"
        assert b"(filled_polygon" in service._jobs[job].source
        if mixed == "tree":
            assert package["fill_round_count"] >= 2
            assert package["binding"]["fill_history_digest"] is not None
            assert package["metrics"]["via_count"] > 0
            assert package["metrics"]["actual_route_probes"] >= 4
            tree = next(net.id for net in snapshot.content.nets if net.name == "TREE")
            assert sum(pad.net_id == tree for pad in snapshot.content.pads) == 3
        if movement:
            outcomes = package["comparison"]["outcomes"]
            assert len(outcomes) == 2 and all(row["status"] == "reviewable" for row in outcomes)
            assert package["metrics"]["displacement_nm"] > 0
            assert (
                package["metrics"]["copper_length_nm"] < outcomes[0]["metrics"]["copper_length_nm"]
            )
            if mixed == "routed-pair":
                assert outcomes[0]["metrics"]["actual_route_probes"] == 0
                assert outcomes[0]["charged"]["repair_rounds"] == 0
                assert outcomes[1]["charged"]["repair_rounds"] == 1
                assert package["repair"]["removed_segments"] == 1
                assert package["repair"]["removed_vias"] == 0
                assert package["binding"]["repair_digest"] is not None
                changed = json.loads(json.dumps(package))
                changed["repair"]["removed_segments"] += 1
                with pytest.raises(ValidationError):
                    OptimizationPackageV2.model_validate_json(json.dumps(changed))
                changed["repair"] = None
                with pytest.raises(ValidationError):
                    OptimizationPackageV2.model_validate_json(json.dumps(changed))
        elif mixed == "partial-pair":
            assert package["metrics"]["actual_route_probes"] > 0
            assert package["metrics"]["displacement_nm"] == 0
            assert package["repair"]["removed_segments"] == 1
            assert package["comparison"]["outcomes"][0]["charged"]["repair_rounds"] == 1
        else:
            assert package["metrics"]["actual_route_probes"] == 0
        assert not gateway._artifacts

    try:
        asyncio.run(exercise())
    finally:
        gateway._service.close()
        gateway._service.repository.close()
    after = board.stat()
    assert (board.read_bytes(), after.st_ino, after.st_mtime_ns) == (
        source,
        before.st_ino,
        before.st_mtime_ns,
    )


def test_fill_cache_splice_is_idempotent_and_preserves_uncached_source():
    source = FIXTURE.read_bytes()
    stripped = replace_fill_cache(source, None, ParseLimits(), checkpoint=lambda: None)
    filled = replace_fill_cache(stripped, source, ParseLimits(), checkpoint=lambda: None)
    assert replace_fill_cache(filled, source, ParseLimits(), checkpoint=lambda: None) == filled
    assert replace_fill_cache(filled, None, ParseLimits(), checkpoint=lambda: None) == stripped
    with pytest.raises(ValueError):
        replace_fill_cache(
            source,
            source.replace(
                b"51000000-0000-0000-0000-0000000000a0", b"51000000-0000-0000-0000-0000000000a1"
            ),
            ParseLimits(),
            checkpoint=lambda: None,
        )
