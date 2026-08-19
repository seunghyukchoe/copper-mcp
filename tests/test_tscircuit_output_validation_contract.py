"""Synthetic regressions for the two-stage tscircuit output-validation contract."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.benchmarks.simple_route_json import import_simple_route_json
from copper_mcp.benchmarks.simple_route_json_output import (
    OutputAdapterRefusalCode,
    SimpleRouteJsonOutputAdapterError,
    adapt_simple_route_json_output,
)
from copper_mcp.external_candidate_drc import _dispose_external_route_candidate
from copper_mcp.routing.contracts import RouteCandidate, RouteRequest
from copper_mcp.routing.external_candidate_verifier import verify_external_route_candidate
from copper_mcp.routing.physical_clearance import (
    PhysicalClearanceFailure,
    verify_negotiated_physical_clearance,
)

FIXTURES = Path(__file__).parent / "fixtures" / "tscircuit-output-validation-v1"


def _fixture(name: str) -> dict[str, Any]:
    loaded = json.loads((FIXTURES / name).read_text("utf-8"))
    assert isinstance(loaded, dict)
    return loaded


def _document_bytes(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()


def _request(problem: Any, net: Any) -> RouteRequest:
    return RouteRequest(
        board_revision=problem.snapshot.snapshot_digest,
        net_id=net.net_id,
        layer_id=net.layer_id,
        seed=0,
    )


def test_1964_shape_passes_each_per_net_gate_but_fails_the_whole_set_gate() -> None:
    fixture = _fixture("cross-net-same-layer-crossing.json")
    source = _document_bytes(fixture["source"])
    output = _document_bytes(fixture["output"])
    problem = import_simple_route_json("synthetic-1964", source)

    assert fixture["case"] == "synthetic-cross-net-same-layer-crossing"
    assert len(problem.routable_nets) == 2
    candidates: list[RouteCandidate] = []
    for net in problem.routable_nets:
        document = adapt_simple_route_json_output(source, problem, output, net_id=net.net_id)
        request = _request(problem, net)
        public_result = verify_external_route_candidate(
            problem.snapshot,
            request,
            document,
            start_pad_id=net.pad_ids[0],
            end_pad_id=net.pad_ids[-1],
            max_obstacle_checks=10_000,
            max_path_edges=64,
        )
        assert public_result.accepted is True

        # Test-only reconstruction lets the existing pairwise Copper oracle see both immutable
        # candidates. It does not expose a composite API or stand in for tscircuit's future
        # whole-output validator, whose preload and topology semantics remain a separate slice.
        disposition = _dispose_external_route_candidate(
            problem.snapshot,
            request,
            document,
            start_pad_id=net.pad_ids[0],
            end_pad_id=net.pad_ids[-1],
            max_obstacle_checks=10_000,
            max_path_edges=64,
        )
        assert disposition.verification.accepted is True
        assert disposition.candidate is not None
        candidates.append(disposition.candidate)

    ordered = tuple(sorted(candidates, key=lambda candidate: candidate.patch.net_id))
    whole_set = verify_negotiated_physical_clearance(
        problem.snapshot,
        ordered,
        layer_id=problem.routable_nets[0].layer_id,
        max_pair_checks=16,
    )

    assert whole_set.failure is PhysicalClearanceFailure.CLEARANCE_VIOLATION
    assert whole_set.violating_nets == tuple(sorted(net.net_id for net in problem.routable_nets))


def test_2058_shape_refuses_via_before_candidate_disposal() -> None:
    fixture = _fixture("via-near-pad.json")
    geometry = fixture["expected_geometry"]
    via = geometry["via_center"]
    neighbor = geometry["neighbor_pad_center"]
    assert via["x"] == neighbor["x"]
    centre_distance = abs(via["y"] - neighbor["y"])
    overlap_limit = geometry["via_outer_diameter"] / 2 + geometry["neighbor_pad_height"] / 2
    assert centre_distance < overlap_limit
    assert via["y"] < neighbor["y"] - geometry["neighbor_pad_height"] / 2

    source = _document_bytes(fixture["source"])
    output = _document_bytes(fixture["output"])
    problem = import_simple_route_json("synthetic-2058", source)
    selected = next(
        net for net in problem.routable_nets if "main-signal" in net.source_connection_names
    )

    with pytest.raises(SimpleRouteJsonOutputAdapterError) as caught:
        adapt_simple_route_json_output(source, problem, output, net_id=selected.net_id)

    assert caught.value.code is OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY
    assert caught.value.locator == "output.traces[0].route[1]"
