"""Fail-closed conversion tests for routed SimpleRouteJson output."""

from __future__ import annotations

import json
from typing import Any

import pytest

from copper_mcp.benchmarks import simple_route_json_output as output_adapter
from copper_mcp.benchmarks.simple_route_json import ImportedProblem, import_simple_route_json
from copper_mcp.benchmarks.simple_route_json_output import (
    OutputAdapterRefusalCode,
    SimpleRouteJsonOutputAdapterError,
    SimpleRouteJsonOutputLimits,
    adapt_simple_route_json_output,
)
from copper_mcp.routing.contracts import RouteRequest
from copper_mcp.routing.external_candidate_verifier import (
    EXTERNAL_ROUTE_CANDIDATE_SCHEMA,
    EXTERNAL_ROUTE_PATCH_SCHEMA,
    verify_external_route_candidate,
)


def _obstacle(x: int, y: int, point_id: str) -> dict[str, Any]:
    return {
        "type": "rect",
        "center": {"x": x, "y": y},
        "width": 1,
        "height": 1,
        "layers": ["top"],
        "connectedTo": [point_id],
    }


def _source(*, three_pad: bool = False, duplicate_names: bool = False) -> bytes:
    obstacles = [_obstacle(-4, 0, "a"), _obstacle(4, 0, "b")]
    connections: list[dict[str, Any]] = [
        {
            "name": "signal",
            "pointsToConnect": [
                {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                {"x": 4, "y": 0, "layer": "top", "pointId": "b"},
            ],
        }
    ]
    if three_pad:
        obstacles.insert(1, _obstacle(0, 4, "c"))
        connections = [
            {
                "name": "first",
                "rootConnectionName": "signal-root",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": 0, "y": 4, "layer": "top", "pointId": "c"},
                ],
            },
            {
                "name": "second",
                "rootConnectionName": "signal-root",
                "pointsToConnect": [
                    {"x": 0, "y": 4, "layer": "top", "pointId": "c"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "b"},
                ],
            },
        ]
    elif duplicate_names:
        obstacles.extend([_obstacle(-4, 4, "c"), _obstacle(4, 4, "d")])
        connections.append(
            {
                "name": "signal",
                "pointsToConnect": [
                    {"x": -4, "y": 4, "layer": "top", "pointId": "c"},
                    {"x": 4, "y": 4, "layer": "top", "pointId": "d"},
                ],
            }
        )
    return json.dumps(
        {
            "layerCount": 2,
            "minTraceWidth": 0.1,
            "bounds": {"minX": -10, "maxX": 10, "minY": -10, "maxY": 10},
            "obstacles": obstacles,
            "connections": connections,
        }
    ).encode()


def _wire(x: int | float, y: int | float, **updates: object) -> dict[str, object]:
    item: dict[str, object] = {
        "route_type": "wire",
        "x": x,
        "y": y,
        "width": 0.1,
        "layer": "top",
    }
    item.update(updates)
    return item


def _trace(
    route: list[dict[str, object]],
    *,
    trace_id: str = "trace-1",
    connection_name: str = "signal",
) -> dict[str, object]:
    return {
        "type": "pcb_trace",
        "pcb_trace_id": trace_id,
        "connection_name": connection_name,
        "route": route,
    }


def _output(source: bytes, traces: list[dict[str, object]]) -> bytes:
    payload = json.loads(source)
    payload["traces"] = traces
    return json.dumps(payload).encode()


def _adapt(
    source: bytes, output: bytes, *, net_index: int = 0
) -> tuple[ImportedProblem, dict[str, object]]:
    problem = import_simple_route_json("fixture", source)
    document = adapt_simple_route_json_output(
        source,
        problem,
        output,
        net_id=problem.nets[net_index].net_id,
    )
    return problem, document


def _dispose(problem: ImportedProblem, document: dict[str, object], *, net_index: int = 0):
    net = problem.nets[net_index]
    request = RouteRequest(
        board_revision=problem.snapshot.snapshot_digest,
        net_id=net.net_id,
        layer_id=net.layer_id,
        seed=0,
    )
    return verify_external_route_candidate(
        problem.snapshot,
        request,
        document,
        start_pad_id=net.pad_ids[0],
        end_pad_id=net.pad_ids[-1],
        max_obstacle_checks=10_000,
        max_path_edges=64,
    )


def _refusal(source: bytes, output: bytes, code: OutputAdapterRefusalCode) -> None:
    problem = import_simple_route_json("fixture", source)
    with pytest.raises(SimpleRouteJsonOutputAdapterError) as caught:
        adapt_simple_route_json_output(source, problem, output, net_id=problem.nets[0].net_id)
    assert caught.value.code is code


def test_two_pad_output_becomes_v1_and_disposes_with_repeatable_identity() -> None:
    source = _source()
    output = _output(source, [_trace([_wire(-4, 0), _wire(4, 0)])])

    problem, first = _adapt(source, output)
    _, second = _adapt(source, output)

    assert first == second
    assert first["schema"] == EXTERNAL_ROUTE_CANDIDATE_SCHEMA
    assert first["problem_revision"] == problem.snapshot.snapshot_digest
    assert first["vias"] == []
    first_result = _dispose(problem, first)
    second_result = _dispose(problem, second)
    assert first_result.accepted is True
    assert first_result.candidate_id == second_result.candidate_id


def test_three_pad_tree_becomes_v2_without_deleting_paths() -> None:
    source = _source(three_pad=True)
    output = _output(
        source,
        [
            _trace(
                [_wire(-4, 0), _wire(0, 0), _wire(4, 0)],
                trace_id="trace-horizontal",
                connection_name="signal-root",
            ),
            _trace(
                [_wire(0, 4), _wire(0, 0)],
                trace_id="trace-vertical",
                connection_name="second",
            ),
        ],
    )

    problem, document = _adapt(source, output)

    assert document["schema"] == EXTERNAL_ROUTE_PATCH_SCHEMA
    paths = document["paths"]
    assert isinstance(paths, list)
    assert len(paths) == 2
    assert _dispose(problem, document).accepted is True


def test_three_pad_backtrack_is_not_misclassified_as_an_acyclic_tree() -> None:
    source = _source(three_pad=True)
    output = _output(
        source,
        [
            _trace(
                [_wire(-4, 0), _wire(0, 0), _wire(4, 0), _wire(0, 0)],
                trace_id="trace-backtrack",
                connection_name="signal-root",
            ),
            _trace(
                [_wire(0, 4), _wire(0, 0)],
                trace_id="trace-vertical",
                connection_name="second",
            ),
        ],
    )

    _refusal(source, output, OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY)


@pytest.mark.parametrize(
    ("route", "code"),
    [
        ([_wire(-4, 0), _wire(4, 1)], OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY),
        ([_wire(-3, 0), _wire(4, 0)], OutputAdapterRefusalCode.ENDPOINT_MISMATCH),
        (
            [
                _wire(-4, 0),
                {"route_type": "via", "x": 0, "y": 0, "from_layer": "top", "to_layer": "bottom"},
                _wire(4, 0),
            ],
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
        ),
        (
            [_wire(-4, 0), {"route_type": "jumper"}, _wire(4, 0)],
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
        ),
        (
            [_wire(-4, 0), {"route_type": "through_obstacle"}, _wire(4, 0)],
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
        ),
        ([_wire(-4, 0)], OutputAdapterRefusalCode.DISCONTINUOUS_PATH),
        (
            [_wire(-4, 0), _wire(4, 0, layer="missing")],
            OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
        ),
    ],
)
def test_geometry_outside_the_initial_subset_refuses_before_disposal(
    route: list[dict[str, object]], code: OutputAdapterRefusalCode
) -> None:
    source = _source()
    _refusal(source, _output(source, [_trace(route)]), code)


def test_source_mutation_and_unbound_or_ambiguous_relations_fail_closed() -> None:
    source = _source()
    changed = json.loads(source)
    changed["bounds"]["maxX"] = 11
    changed["traces"] = [_trace([_wire(-4, 0), _wire(4, 0)])]
    _refusal(
        source,
        json.dumps(changed).encode(),
        OutputAdapterRefusalCode.SOURCE_MISMATCH,
    )

    _refusal(
        source,
        _output(source, [_trace([_wire(-4, 0), _wire(4, 0)], connection_name="unknown")]),
        OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
    )

    ambiguous = _source(duplicate_names=True)
    _refusal(
        ambiguous,
        _output(ambiguous, [_trace([_wire(-4, 0), _wire(4, 0)])]),
        OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
    )


def test_duplicate_keys_and_byte_budget_are_typed_refusals() -> None:
    source = _source()
    malformed = source[:-1] + b',"traces":[],"traces":[]}'
    _refusal(source, malformed, OutputAdapterRefusalCode.MALFORMED_DOCUMENT)

    problem = import_simple_route_json("fixture", source)
    with pytest.raises(SimpleRouteJsonOutputAdapterError) as caught:
        adapt_simple_route_json_output(
            source,
            problem,
            _output(source, [_trace([_wire(-4, 0), _wire(4, 0)])]),
            net_id=problem.nets[0].net_id,
            limits=SimpleRouteJsonOutputLimits(max_document_bytes=32),
        )
    assert caught.value.code is OutputAdapterRefusalCode.BUDGET_EXCEEDED


def test_source_budget_refuses_before_replay(monkeypatch: pytest.MonkeyPatch) -> None:
    source = _source()
    problem = import_simple_route_json("fixture", source)

    def replay_must_not_run(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("source replay ran before the adapter byte budget")

    monkeypatch.setattr(output_adapter, "import_simple_route_json", replay_must_not_run)
    with pytest.raises(SimpleRouteJsonOutputAdapterError) as caught:
        adapt_simple_route_json_output(
            source,
            problem,
            b"{}",
            net_id=problem.nets[0].net_id,
            limits=SimpleRouteJsonOutputLimits(max_document_bytes=32),
        )
    assert caught.value.code is OutputAdapterRefusalCode.BUDGET_EXCEEDED


def test_relation_tokens_must_all_resolve_to_the_same_net() -> None:
    source_payload = json.loads(_source(duplicate_names=True))
    source_payload["connections"][1]["name"] = "other"
    source = json.dumps(source_payload).encode()

    for relation in ("unknown-relation", "other"):
        trace = _trace([_wire(-4, 0), _wire(4, 0)])
        trace["connectsTo"] = [relation]
        _refusal(
            source,
            _output(source, [trace]),
            OutputAdapterRefusalCode.AMBIGUOUS_NET_OWNERSHIP,
        )


def test_caller_cannot_widen_server_owned_limits() -> None:
    with pytest.raises(ValueError):
        SimpleRouteJsonOutputLimits(max_document_bytes=4_000_001)


def test_unknown_root_field_and_quoted_coordinate_do_not_widen_the_shape() -> None:
    source = _source()
    with_extra = json.loads(_output(source, [_trace([_wire(-4, 0), _wire(4, 0)])]))
    with_extra["solverMetadata"] = {"accepted": True}
    _refusal(
        source,
        json.dumps(with_extra).encode(),
        OutputAdapterRefusalCode.SOURCE_MISMATCH,
    )

    quoted = _trace([_wire(-4, 0), _wire(4, 0)])
    quoted_route = quoted["route"]
    assert isinstance(quoted_route, list)
    assert isinstance(quoted_route[0], dict)
    quoted_route[0]["x"] = "-4"
    _refusal(
        source,
        _output(source, [quoted]),
        OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
    )


def test_nonfinite_and_sub_nanometre_route_numbers_refuse() -> None:
    source = _source()
    nonfinite = _trace([_wire(-4, 0), _wire(float("nan"), 0)])
    _refusal(
        source,
        _output(source, [nonfinite]),
        OutputAdapterRefusalCode.MALFORMED_DOCUMENT,
    )

    sub_nanometre = _trace([_wire(-4, 0), _wire(4.0000001, 0)])
    _refusal(
        source,
        _output(source, [sub_nanometre]),
        OutputAdapterRefusalCode.UNSUPPORTED_GEOMETRY,
    )


def test_adapter_remains_outside_tools_server_and_routing_core() -> None:
    from pathlib import Path

    import copper_mcp

    package = Path(copper_mcp.__file__).resolve().parent
    offenders = [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if path.parent.name != "benchmarks"
        and "simple_route_json_output" in path.read_text("utf-8")
    ]
    assert offenders == []
