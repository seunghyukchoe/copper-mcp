"""Conversion, refusal, and direction-of-error tests for the SimpleRouteJson import seam."""

from __future__ import annotations

import json
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.benchmarks import simple_route_json as adapter
from copper_mcp.benchmarks.simple_route_json import (
    ImportPolicy,
    ImportRefusalCode,
    SimpleRouteJsonImportError,
    SimpleRouteJsonImportLimits,
    import_simple_route_json,
)
from copper_mcp.board_ir import PadShape, mm_to_nm, verify_snapshot

LIMITS = SimpleRouteJsonImportLimits()


def _obstacle(
    *,
    kind: str = "rect",
    x: float | str = 0,
    y: float | str = 0,
    width: float | str = 1,
    height: float | str = 1,
    layers: list[str] | None = None,
    connected_to: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": kind,
        "center": {"x": x, "y": y},
        "width": width,
        "height": height,
        "layers": layers if layers is not None else ["top"],
        "connectedTo": connected_to if connected_to is not None else [],
    }


def _document(
    *,
    layer_count: int = 2,
    min_trace_width: float | str = 0.1,
    bounds: dict[str, Any] | None = None,
    obstacles: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "layerCount": layer_count,
        "minTraceWidth": min_trace_width,
        "bounds": bounds or {"minX": -10, "maxX": 10, "minY": -10, "maxY": 10},
        "obstacles": obstacles if obstacles is not None else [],
        "connections": connections if connections is not None else [],
    }
    return json.dumps(payload).encode("utf-8")


def _two_pad_document() -> bytes:
    """A minimal, fully exact two-pin problem: two owned pads and one foreign blocker."""

    return _document(
        obstacles=[
            _obstacle(x=-4, y=0, width=1, height=1, connected_to=["a"]),
            _obstacle(x=4, y=0, width=1, height=1, connected_to=["b"]),
            _obstacle(x=0, y=0, width=2, height=2),
        ],
        connections=[
            {
                "name": "net-a-b",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "b"},
                ],
            }
        ],
    )


def test_the_import_seam_is_not_reachable_from_the_tool_surface() -> None:
    """The adapter is a benchmark seam; wiring it into a tool would be a contract change.

    Checked by reading the shipped sources rather than by import graph, so a module that reaches
    the seam through a lazy import inside a function is caught too.
    """

    package = Path(adapter.__file__).resolve().parents[1]
    offenders = [
        path.relative_to(package).as_posix()
        for path in sorted(package.rglob("*.py"))
        if path.parent.name != "benchmarks" and "copper_mcp.benchmarks" in path.read_text("utf-8")
    ]

    assert offenders == []


def test_exact_rect_obstacles_convert_without_any_rounding() -> None:
    problem = import_simple_route_json("two-pad", _two_pad_document())

    assert verify_snapshot(problem.snapshot) is True
    assert problem.statistics.max_outward_rounding_nm == 0
    assert problem.statistics.over_approximated_obstacles == 0
    content = problem.snapshot.content
    assert [pad.id for pad in content.pads] == ["pad:o0", "pad:o1"]
    assert [keepout.id for keepout in content.keepouts] == ["keepout:o2"]
    first, second = content.pads
    assert (first.center.x, first.center.y) == (-4_000_000, 0)
    assert (first.size_x_nm, first.size_y_nm) == (1_000_000, 1_000_000)
    assert first.shape is PadShape.RECT
    assert second.center.x == 4_000_000
    blocker = content.keepouts[0]
    assert blocker.prohibit_tracks is True
    assert {(point.x, point.y) for point in blocker.boundary.points} == {
        (-1_000_000, -1_000_000),
        (1_000_000, -1_000_000),
        (1_000_000, 1_000_000),
        (-1_000_000, 1_000_000),
    }


def test_import_is_byte_deterministic_and_content_addressed() -> None:
    document = _two_pad_document()

    first = import_simple_route_json("two-pad", document)
    second = import_simple_route_json("two-pad", document)

    assert first.snapshot.snapshot_digest == second.snapshot.snapshot_digest
    assert first.nets == second.nets
    assert first.snapshot.content.source.revision == f"sha256:{first.document_sha256}"


def test_track_width_and_lower_bound_are_exact_integers() -> None:
    problem = import_simple_route_json("two-pad", _two_pad_document())

    assert problem.track_width_nm == 100_000
    net = problem.nets[0]
    # Pads span [-4.5, -3.5] and [3.5, 4.5] mm, so any tree touching both spans 7 mm in x.
    assert net.pad_gap_lower_bound_nm == 7_000_000
    assert net.two_pin_centre_manhattan_nm == 8_000_000
    assert net.pad_gap_lower_bound_nm <= net.two_pin_centre_manhattan_nm


def test_millimetre_rule_agrees_with_the_board_ir_conversion() -> None:
    for token in ("0", "1", "-1", "0.1", "12.345678", "-0.000001", "999.5"):
        converted = adapter.mm_token_to_nm(token, "probe", LIMITS)
        assert converted == Decimal(mm_to_nm(token))


def test_sub_nanometre_tokens_round_copper_outward_and_the_outline_inward() -> None:
    # 2.9000000000000004 is what a JavaScript pipeline writes for 2.9; the low edge must floor.
    document = _document(
        bounds={"minX": -10.0000000001, "maxX": 10.0000000001, "minY": -10, "maxY": 10},
        obstacles=[_obstacle(x="2.9000000000000004", y=0, width=1, height=1)],
    )

    problem = import_simple_route_json("rounded", document)

    keepout = problem.snapshot.content.keepouts[0]
    xs = sorted({point.x for point in keepout.boundary.points})
    assert xs == [2_400_000, 3_400_001]
    assert problem.statistics.max_outward_rounding_nm == 1
    assert problem.statistics.over_approximated_obstacles == 1
    outline_xs = sorted({point.x for point in problem.snapshot.content.outline[0].outer.points})
    # Inward: the outline never gains a nanometre the document did not grant.
    assert outline_xs == [-10_000_000, 10_000_000]


def test_oval_obstacles_keep_their_shape_so_attachment_stays_inscribed() -> None:
    document = _document(
        obstacles=[
            _obstacle(kind="oval", x=-4, y=0, width=2, height=1, connected_to=["a"]),
            _obstacle(kind="oval", x=4, y=0, width=2, height=1, connected_to=["b"]),
            _obstacle(kind="oval", x=0, y=0, width=2, height=2),
        ],
        connections=[
            {
                "name": "oval-net",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "b"},
                ],
            }
        ],
    )

    problem = import_simple_route_json("ovals", document)

    assert all(pad.shape is PadShape.OVAL for pad in problem.snapshot.content.pads)
    # An unowned oval blocks as its bounding box, which strictly contains the oval.
    keepout = problem.snapshot.content.keepouts[0]
    assert sorted({point.x for point in keepout.boundary.points}) == [-1_000_000, 1_000_000]
    assert problem.statistics.oval_obstacles == 3
    assert problem.statistics.over_approximated_obstacles == 3


def test_an_obstacle_naming_an_undeclared_layer_blocks_the_whole_stack() -> None:
    document = _document(
        layer_count=2, obstacles=[_obstacle(layers=["top", "inner1", "inner2", "bottom"])]
    )

    problem = import_simple_route_json("widened", document)

    assert problem.snapshot.content.keepouts[0].layer_ids == ("layer:top", "layer:bottom")
    assert problem.statistics.layer_widened_obstacles == 1


def test_connections_sharing_a_point_collapse_into_one_net() -> None:
    document = _document(
        obstacles=[
            _obstacle(x=-4, y=0, connected_to=["a"]),
            _obstacle(x=0, y=4, connected_to=["b"]),
            _obstacle(x=4, y=0, connected_to=["c"]),
        ],
        connections=[
            {
                "name": "first",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": 0, "y": 4, "layer": "top", "pointId": "b"},
                ],
            },
            {
                "name": "second",
                "pointsToConnect": [
                    {"x": 0, "y": 4, "layer": "top", "pointId": "b"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "c"},
                ],
            },
        ],
    )

    problem = import_simple_route_json("shared-point", document)

    assert len(problem.nets) == 1
    net = problem.nets[0]
    assert net.pad_count == 3
    assert net.source_connection_names == ("first", "second")
    assert net.two_pin_centre_manhattan_nm is None


def test_a_single_point_connection_yields_a_net_with_no_routing_work() -> None:
    document = _document(
        obstacles=[_obstacle(x=1, y=1, connected_to=["only"])],
        connections=[
            {
                "name": "lonely",
                "pointsToConnect": [{"x": 1, "y": 1, "layer": "top", "pointId": "only"}],
            }
        ],
    )

    problem = import_simple_route_json("lonely", document)

    assert problem.nets[0].pad_count == 1
    assert problem.routable_nets == ()
    # The pad is still imported, so it still blocks every other net.
    assert len(problem.snapshot.content.pads) == 1


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"{", ImportRefusalCode.MALFORMED_DOCUMENT),
        (b'{"layerCount": 2, "layerCount": 3}', ImportRefusalCode.MALFORMED_DOCUMENT),
        (b"[]", ImportRefusalCode.MALFORMED_DOCUMENT),
        (b"\xff\xfe", ImportRefusalCode.MALFORMED_DOCUMENT),
    ],
)
def test_malformed_documents_refuse_with_a_typed_code(payload: bytes, code: str) -> None:
    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("bad", payload)

    assert raised.value.code == code


def test_non_finite_coordinates_refuse_before_any_conversion() -> None:
    payload = (
        b'{"layerCount": 2, "minTraceWidth": 0.1, '
        b'"bounds": {"minX": -1, "maxX": NaN, "minY": -1, "maxY": 1}, '
        b'"obstacles": [], "connections": []}'
    )

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("nan", payload)

    assert raised.value.code == ImportRefusalCode.UNSUPPORTED_UNIT


def test_an_unknown_obstacle_type_refuses_the_whole_document() -> None:
    document = _document(obstacles=[_obstacle(kind="polygon")])

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("polygon", document)

    assert raised.value.code == ImportRefusalCode.UNSUPPORTED_OBSTACLE
    assert raised.value.locator == "obstacles[0]"


def test_an_unanchored_connection_point_refuses_rather_than_inventing_a_pad() -> None:
    document = _document(
        obstacles=[_obstacle(x=-4, y=0, connected_to=["a"])],
        connections=[
            {
                "name": "dangling",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": 9, "y": 9, "layer": "top", "pointId": "nowhere"},
                ],
            }
        ],
    )

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("dangling", document)

    assert raised.value.code == ImportRefusalCode.UNANCHORED_CONNECTION_POINT


def test_an_obstacle_claimed_by_two_nets_refuses_instead_of_guessing() -> None:
    document = _document(
        obstacles=[
            _obstacle(x=-4, y=0, connected_to=["a"]),
            _obstacle(x=-2, y=0, connected_to=["b"]),
            _obstacle(x=2, y=0, connected_to=["c"]),
            _obstacle(x=4, y=0, connected_to=["d", "a", "c"]),
        ],
        connections=[
            {
                "name": "first",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "a"},
                    {"x": -2, "y": 0, "layer": "top", "pointId": "b"},
                ],
            },
            {
                "name": "second",
                "pointsToConnect": [
                    {"x": 2, "y": 0, "layer": "top", "pointId": "c"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "d"},
                ],
            },
        ],
    )

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("ambiguous", document)

    assert raised.value.code == ImportRefusalCode.AMBIGUOUS_NET_OWNERSHIP


def test_a_point_on_an_undeclared_layer_refuses() -> None:
    document = _document(
        layer_count=2,
        obstacles=[_obstacle(x=0, y=0, connected_to=["a"])],
        connections=[
            {
                "name": "inner",
                "pointsToConnect": [{"x": 0, "y": 0, "layer": "inner1", "pointId": "a"}],
            }
        ],
    )

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("inner-point", document)

    assert raised.value.code == ImportRefusalCode.UNSUPPORTED_CONNECTION_POINT


def test_net_copper_outside_the_declared_bounds_refuses() -> None:
    document = _document(
        bounds={"minX": -1, "maxX": 1, "minY": -1, "maxY": 1},
        obstacles=[_obstacle(x=0.9, y=0, width=1, height=1, connected_to=["a"])],
        connections=[
            {
                "name": "edge",
                "pointsToConnect": [{"x": 0.9, "y": 0, "layer": "top", "pointId": "a"}],
            }
        ],
    )

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("edge", document)

    assert raised.value.code == ImportRefusalCode.PAD_OUTSIDE_BOUNDS


@pytest.mark.parametrize(
    ("layer_count", "code"),
    [
        (0, ImportRefusalCode.UNSUPPORTED_LAYER_COUNT),
        (99, ImportRefusalCode.UNSUPPORTED_LAYER_COUNT),
    ],
)
def test_unsupported_layer_counts_refuse(layer_count: int, code: str) -> None:
    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("stack", _document(layer_count=layer_count))

    assert raised.value.code == code


def test_budgets_refuse_before_conversion_work() -> None:
    tight = SimpleRouteJsonImportLimits(max_obstacles=1)

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json(
            "budget", _document(obstacles=[_obstacle(), _obstacle()]), limits=tight
        )

    assert raised.value.code == ImportRefusalCode.BUDGET_EXCEEDED


def test_an_out_of_range_coordinate_refuses_rather_than_clamping() -> None:
    document = _document(bounds={"minX": -1, "maxX": 100_000, "minY": -1, "maxY": 1})

    with pytest.raises(SimpleRouteJsonImportError) as raised:
        import_simple_route_json("huge", document)

    assert raised.value.code == ImportRefusalCode.BUDGET_EXCEEDED


def test_a_long_number_token_refuses_before_decimal_parsing() -> None:
    with pytest.raises(SimpleRouteJsonImportError) as raised:
        adapter.mm_token_to_nm("1." + "0" * 60, "probe", LIMITS)

    assert raised.value.code == ImportRefusalCode.BUDGET_EXCEEDED


def test_the_import_policy_reaches_the_constraint_set() -> None:
    policy = ImportPolicy(clearance_nm=321_000, via_diameter_nm=700_000, via_drill_nm=350_000)

    problem = import_simple_route_json("policy", _two_pad_document(), policy=policy)

    net_class = problem.snapshot.content.constraints.net_classes[0]
    assert net_class.clearance_nm == 321_000
    assert net_class.via_diameter_nm == 700_000
    assert problem.policy == policy


# --- the over-approximation guard, and a mutation check that the guard actually bites -----------

_ROUNDING_CASES: tuple[tuple[str, str, str, str], ...] = (
    ("2.9000000000000004", "0", "1", "1"),
    ("-1.5299999999999998", "0.40700000000000003", "0.566", "0.54"),
    ("5.529999999999999", "-0.7670000000000001", "0.875", "0.95"),
    ("0.040000000000000036", "1.1309999999999998", "1.5", "1.5"),
    ("0", "0", "0.0000005", "0.0000005"),
)


def _exact_bounds(
    centre_x: str, centre_y: str, width: str, height: str
) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    scale = Decimal(1_000_000)
    cx, cy = Decimal(centre_x) * scale, Decimal(centre_y) * scale
    half_x, half_y = Decimal(width) * scale / 2, Decimal(height) * scale / 2
    return cx - half_x, cy - half_y, cx + half_x, cy + half_y


def _containment_violations() -> list[tuple[str, str, str, str]]:
    """Return every case whose mapped rectangle fails to contain the exact source rectangle."""

    violations = []
    for centre_x, centre_y, width, height in _ROUNDING_CASES:
        document = _document(
            bounds={"minX": -50, "maxX": 50, "minY": -50, "maxY": 50},
            obstacles=[_obstacle(x=centre_x, y=centre_y, width=width, height=height)],
        )
        mapped = import_simple_route_json("guard", document).snapshot.content.keepouts[0]
        xs = sorted({point.x for point in mapped.boundary.points})
        ys = sorted({point.y for point in mapped.boundary.points})
        low_x, low_y, high_x, high_y = _exact_bounds(centre_x, centre_y, width, height)
        if not (xs[0] <= low_x and high_x <= xs[1] and ys[0] <= low_y and high_y <= ys[1]):
            violations.append((centre_x, centre_y, width, height))
    return violations


def test_every_mapped_obstacle_rectangle_contains_its_exact_source_rectangle() -> None:
    assert _containment_violations() == []


def test_the_containment_guard_detects_an_inward_rounding_mutation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Round the low edge the wrong way and the guard above must fail.

    Without this, ``test_every_mapped_obstacle_rectangle_contains_its_exact_source_rectangle``
    could pass simply because every fixture happens to be exact at nanometre resolution, and the
    over-approximation claim would rest on nothing.
    """

    monkeypatch.setattr(adapter, "_floor_nm", adapter._ceil_nm)

    assert _containment_violations() != []
