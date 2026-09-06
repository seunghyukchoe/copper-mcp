"""Promote the existing ordered-layer core through its disposable serialization boundary."""

import hashlib

import pytest
from test_kicad_layered_route_patch import B_CU, F_CU, FIXTURE, _profile

from copper_mcp.adapters import parse_kicad_bytes, render_kicad_layered_candidate_board
from copper_mcp.routing import LayeredAStarSettings, LayeredBoardRouter, LayeredRouteRequest


@pytest.mark.parametrize("count", [2, 4, 6, 8])
def test_full_stack_vias_roundtrip_on_each_supported_layer_count(count):
    original = FIXTURE.read_bytes()
    inner = b"".join(
        f'    ({2 + 2 * index} "In{index}.Cu" signal)\n'.encode() for index in range(1, count - 1)
    )
    source = original.replace(b'    (2 "B.Cu" signal)\n', inner + b'    (2 "B.Cu" signal)\n')
    original_digest = hashlib.sha256(source).hexdigest()
    profile = _profile()
    converted = parse_kicad_bytes(source, profile)
    assert converted.snapshot is not None and not converted.diagnostics
    snapshot = converted.snapshot
    net_id = snapshot.content.pads[0].net_id
    pads = [pad for pad in snapshot.content.pads if pad.net_id == net_id]
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=250_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, request)
    assert result.candidate is not None
    rendered = render_kicad_layered_candidate_board(
        source, snapshot, result.candidate, profile, request=request
    )
    assert rendered == render_kicad_layered_candidate_board(
        source, snapshot, result.candidate, profile, request=request
    )
    reparsed = parse_kicad_bytes(rendered, profile)
    assert reparsed.snapshot is not None and not reparsed.diagnostics
    assert len(reparsed.snapshot.content.copper_layers) == count
    assert reparsed.snapshot.content.vias
    assert all(
        {via.start_layer_id, via.end_layer_id} == {F_CU, B_CU}
        for via in reparsed.snapshot.content.vias
    )
    assert hashlib.sha256(source).hexdigest() == original_digest
