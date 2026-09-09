"""Owned two-pad KiCad agreement only, not physics calibration or unseen-rule authority."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import cast

import pytest
from test_optimization_clearance_score import Probe

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass, PadShape, PointNM, nm_to_mm
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import run_board_drc
from copper_mcp.optimization.clearance_score import measure_composed_clearance
from copper_mcp.optimization.worker import OptimizationExecutionProbe

_CLI = Path(
    os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
    or "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
)
_CLEARANCE_NM = 500_000
# Dimensional constraints need explicit units; a malformed rule can be dropped by KiCad.
_RULES = b'(version 1)\n(rule "Owned circle clearance" (constraint clearance (min 0.5mm)))\n'
_PROJECT = b'{"meta":{"version":3}}\n'


def _board_bytes(delta_nm: int) -> bytes:
    # Structure follows the owned route-candidate/two-pad.kicad_pcb KiCad 10 fixture.
    # Only each pad's net, shape and board-frame position are specific to this control.
    footprints = []
    for index, (net, x_nm) in enumerate(
        (("OWNED_A", 10_000_000), ("OWNED_B", 12_500_000 + delta_nm)), start=1
    ):
        footprints.append(
            f'''  (footprint "CopperMCP_ClearanceCircle"
    (layer "F.Cu")
    (uuid "30000000-0000-4000-8000-{index:012d}")
    (at {nm_to_mm(x_nm)} {nm_to_mm(10_000_000)} 0)
    (pad "1" smd circle
      (at 0 0 0)
      (size {nm_to_mm(2_000_000)} {nm_to_mm(2_000_000)})
      (layers "F.Cu" "F.Mask" "F.Paste")
      (net "{net}")
      (uuid "40000000-0000-4000-8000-{index:012d}")
    )
  )
'''
        )
    return (
        """(kicad_pcb
  (version 20260206)
  (generator "copper-mcp")
  (generator_version "0.1.0")
  (layers
    (0 "F.Cu" signal)
    (2 "B.Cu" signal)
    (25 "Edge.Cuts" user)
  )
"""
        + "".join(footprints)
        + """  (gr_rect
    (start 0 0)
    (end 30 20)
    (stroke (width 0.1) (type default))
    (fill no)
    (layer "Edge.Cuts")
    (uuid "50000000-0000-4000-8000-000000000001")
  )
)
"""
    ).encode("ascii")


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI.is_file(), reason="configured KiCad CLI is not installed")
@pytest.mark.parametrize("delta_nm", [-10_000, 0, 10_000], ids=["below", "at", "above"])
def test_owned_circle_clearance_agrees_with_native_rule_without_source_mutation(
    tmp_path: Path, delta_nm: int
) -> None:
    source = _board_bytes(delta_nm)
    board = tmp_path / "owned-clearance.kicad_pcb"
    rules = board.with_suffix(".kicad_dru")
    project = board.with_suffix(".kicad_pro")
    board.write_bytes(source)
    rules.write_bytes(_RULES)
    project.write_bytes(_PROJECT)
    before = {path: path.stat() for path in (board, rules, project)}

    net_class = NetClass("class:owned", "OwnedClearance", _CLEARANCE_NM, 250_000, 800_000, 400_000)
    profile = KiCadConstraintProfile(
        net_classes=(net_class,),
        default_net_class_id=net_class.id,
        net_class_by_name=(("OWNED_A", net_class.id), ("OWNED_B", net_class.id)),
    )
    converted = parse_kicad_bytes(source, profile)
    assert converted.snapshot is not None and converted.diagnostics == ()
    snapshot = converted.snapshot
    pads = snapshot.content.pads
    assert len(pads) == 2 and len({pad.net_id for pad in pads}) == 2
    assert all(pad.net_id is not None and pad.shape is PadShape.CIRCLE for pad in pads)
    assert all((pad.size_x_nm, pad.size_y_nm) == (2_000_000, 2_000_000) for pad in pads)
    assert {pad.center for pad in pads} == {
        PointNM(10_000_000, 10_000_000),
        PointNM(12_500_000 + delta_nm, 10_000_000),
    }
    assert all(
        item.net_class_id == net_class.id for item in snapshot.content.constraints.assignments
    )
    observed = measure_composed_clearance(
        snapshot,
        cast(OptimizationExecutionProbe, Probe()),
        expected_snapshot_digest=snapshot.snapshot_digest,
    )
    assert observed.status == "measured" and observed.reason is None
    assert observed.minimum_margin_nm == delta_nm
    assert observed.object_count == 2 and observed.comparable_pairs == 1

    summary = run_board_drc(board.name, Settings(workspace=tmp_path, kicad_cli=_CLI))
    assert summary.kicad_version.startswith("10.")
    assert summary.base_revision == "sha256:" + hashlib.sha256(source).hexdigest()
    assert summary.unconnected_count == 0
    if delta_nm < 0:
        assert not summary.passed and summary.error_count > 0
        assert summary.violation_type_counts.get("clearance", 0) > 0
    else:
        assert summary.passed and summary.error_count == 0
        assert summary.violation_type_counts.get("clearance", 0) == 0

    for path, content in ((board, source), (rules, _RULES), (project, _PROJECT)):
        assert path.read_bytes() == content
        after = path.stat()
        assert (after.st_ino, after.st_mtime_ns) == (before[path].st_ino, before[path].st_mtime_ns)
