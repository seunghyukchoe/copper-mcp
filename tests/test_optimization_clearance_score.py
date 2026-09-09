"""Owned exact-geometry controls; no native DRC or held-out measurement."""

from __future__ import annotations

from dataclasses import FrozenInstanceError, replace
from typing import cast

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import QuotedAtom
from copper_mcp.board_ir import canonical
from copper_mcp.board_ir import types as ir
from copper_mcp.optimization import clearance_score as score
from copper_mcp.optimization.lifecycle import ResourceUsage
from copper_mcp.optimization.worker import OptimizationExecutionProbe

F, INNER, B = "layer:f", "layer:inner", "layer:b"
N1, N2 = "net:first", "net:second"


class Probe:
    def __init__(self, maximum: int = 10_000_000) -> None:
        self.maximum = maximum
        self.used = 0
        self.stopped = False

    def checkpoint(self) -> None:
        if self.stopped:
            raise RuntimeError("PRIVATE cancellation or deadline")

    def reserve(self, usage: ResourceUsage) -> None:
        self.checkpoint()
        self.used += usage.obstacle_checks
        if self.used > self.maximum:
            raise ValueError("PRIVATE exhausted cumulative budget")


def _pad(
    name: str,
    net: str | None,
    x: int,
    y: int,
    *,
    shape: ir.PadShape = ir.PadShape.CIRCLE,
    size: tuple[int, int] = (10, 10),
    rotation: int = 0,
    layers: tuple[str, ...] = (F,),
    radius: int | None = None,
) -> ir.Pad:
    return ir.Pad(
        name,
        net,
        ir.PointNM(x, y),
        rotation,
        shape,
        ir.PadKind.SMD,
        *size,
        radius,
        None,
        None,
        layers,
    )


def _track(
    name: str,
    net: str | None,
    start: tuple[int, int],
    end: tuple[int, int],
    width: int = 2,
    layer: str = F,
) -> ir.Segment:
    return ir.Segment(name, net, layer, ir.PointNM(*start), ir.PointNM(*end), width)


def _snapshot(
    *objects: ir.Pad | ir.Segment | ir.Via | ir.Arc | ir.Zone,
    clearances: tuple[int, int] = (10, 10),
    layers: tuple[str, ...] = (F, B),
    rules: tuple[ir.LengthRule, ...] = (),
) -> ir.BoardIRSnapshot:
    pads = tuple(item for item in objects if isinstance(item, ir.Pad))
    content = canonical.make_content(
        source=ir.SourceInfo("kicad", "sha256:" + "a" * 64, "20260101"),
        outline=(
            ir.OutlineContour(
                "contour:outline",
                ir.Ring(
                    tuple(
                        ir.PointNM(*p)
                        for p in ((-1000, -1000), (1000, -1000), (1000, 1000), (-1000, 1000))
                    )
                ),
            ),
        ),
        copper_layers=tuple(ir.Layer(layer, layer, index) for index, layer in enumerate(layers)),
        nets=(ir.Net(N1, "FIRST"), ir.Net(N2, "SECOND")),
        constraints=ir.ConstraintSet(
            tuple(
                ir.NetClass(f"class:c{i}", f"C{i}", clearance, 2, 10, 2)
                for i, clearance in enumerate(clearances)
            ),
            (ir.NetClassAssignment(N1, "class:c0"), ir.NetClassAssignment(N2, "class:c1")),
            length_rules=rules,
        ),
        footprints=(
            (
                ir.Footprint(
                    "footprint:all",
                    ir.PointNM(0, 0),
                    0,
                    ir.FootprintSide.FRONT,
                    tuple(pad.id for pad in pads),
                ),
            )
            if pads
            else ()
        ),
        pads=pads,
        segments=tuple(item for item in objects if isinstance(item, ir.Segment)),
        vias=tuple(item for item in objects if isinstance(item, ir.Via)),
        arcs=tuple(item for item in objects if isinstance(item, ir.Arc)),
        zones=tuple(item for item in objects if isinstance(item, ir.Zone)),
    )
    return canonical.make_snapshot(content)


def _measure(snapshot: ir.BoardIRSnapshot, probe: Probe | None = None, **limits: int):
    return score.measure_composed_clearance(
        snapshot,
        cast(OptimizationExecutionProbe, probe or Probe()),
        expected_snapshot_digest=snapshot.snapshot_digest,
        **limits,
    )


def _fixed(error: BaseException) -> None:
    assert error.__cause__ is None and error.__context__ is None
    assert "PRIVATE" not in str(error)


@pytest.mark.parametrize("distance,expected", [(19, -1), (20, 0), (21, 1), (0, -10)])
def test_circle_exact_boundary_and_overlap_is_not_penetration(distance, expected):
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, distance, 0))
    observed = _measure(board)
    assert observed.status == "measured" and observed.reason is None
    assert observed.minimum_margin_nm == expected
    assert observed.pair_checks == observed.comparable_pairs == 1


@pytest.mark.parametrize("separation,expected", [(13, -1), (14, 0), (15, 1)])
def test_odd_track_widths_round_down(separation, expected):
    observed = _measure(
        _snapshot(
            _track("segment:a", N1, (0, 0), (20, 0), 3),
            _track("segment:b", N2, (0, separation), (20, separation), 4),
        )
    )
    assert observed.minimum_margin_nm == expected


@pytest.mark.parametrize("point,expected", [((5, 0), 1), ((-3, -4), 2), ((6, 8), -1)])
def test_diagonal_point_segment_distance(point, expected):
    observed = _measure(
        _snapshot(
            _track("segment:a", N1, (0, 0), (12, 16)),
            _pad("pad:b", N2, *point, size=(2, 2)),
            clearances=(1, 1),
        )
    )
    assert observed.minimum_margin_nm == expected


@pytest.mark.parametrize("shape", [ir.PadShape.RECT, ir.PadShape.OVAL, ir.PadShape.ROUNDRECT])
@pytest.mark.parametrize("turns", [0, 1, 2, 3])
def test_all_quarter_turn_pad_cores(shape, turns):
    observed = _measure(
        _snapshot(
            _pad(
                "pad:a",
                N1,
                0,
                0,
                shape=shape,
                size=(20, 10),
                rotation=turns * 90_000_000,
                radius=2 if shape is ir.PadShape.ROUNDRECT else None,
            ),
            _pad("pad:b", N2, 0, 20, size=(2, 2)),
            clearances=(1, 1),
        )
    )
    assert observed.minimum_margin_nm == (8 if turns % 2 else 13)


@pytest.mark.parametrize(
    "shape,radius,expected",
    [(ir.PadShape.RECT, None, 3), (ir.PadShape.ROUNDRECT, 2, 3), (ir.PadShape.OVAL, None, 5)],
)
def test_rounded_corners_are_measured_not_bounding_boxes(shape, radius, expected):
    observed = _measure(
        _snapshot(
            _pad("pad:a", N1, 0, 0, shape=shape, size=(20, 10), radius=radius),
            _pad("pad:b", N2, 13, 9, size=(2, 2)),
            clearances=(1, 1),
        )
    )
    assert observed.minimum_margin_nm == expected


@pytest.mark.parametrize("size,radius", [((20, 10), 5), ((10, 10), 5)])
def test_degenerate_roundrect_inner_core(size, radius):
    board = _snapshot(
        _pad("pad:a", N1, 0, 0, shape=ir.PadShape.ROUNDRECT, size=size, radius=radius),
        _pad("pad:b", N2, 0, 20, size=(2, 2)),
        clearances=(1, 1),
    )
    assert _measure(board).minimum_margin_nm == 13


def test_containment_and_crossing_have_zero_physical_separation():
    rectangle = _pad("pad:a", N1, 0, 0, shape=ir.PadShape.RECT, size=(20, 20))
    for other in (
        _pad("pad:b", N2, 0, 0, size=(2, 2)),
        _track("segment:b", N2, (-50, -50), (50, 50)),
        _pad("pad:b", N2, 0, 0, shape=ir.PadShape.RECT, size=(2, 2)),
    ):
        assert _measure(_snapshot(rectangle, other)).minimum_margin_nm == -10


def test_layer_and_same_net_pairs_are_charged_but_not_compared():
    probe = Probe()
    board = _snapshot(
        _pad("pad:a", N1, 0, 0), _pad("pad:b", N1, 0, 0), _pad("pad:c", N2, 0, 0, layers=(B,))
    )
    observed = _measure(board, probe)
    assert observed.status == "unavailable" and observed.reason == "no_comparable_pair"
    assert observed.minimum_margin_nm is None
    assert observed.pair_checks == 3 and observed.comparable_pairs == 0 and probe.used >= 3


def test_through_via_compares_on_inner_layer_and_uses_stricter_class():
    via = ir.Via("via:a", N1, ir.PointNM(0, 0), 10, 2, F, B)
    board = _snapshot(
        via, _pad("pad:b", N2, 21, 0, layers=(INNER,)), clearances=(1, 10), layers=(F, INNER, B)
    )
    assert _measure(board).minimum_margin_nm == 1


@pytest.mark.parametrize("kind", ["custom", "rotation", "netless", "arc", "zone", "rules", "layer"])
def test_unsupported_content_never_yields_partial_minimum(kind):
    first = _pad("pad:a", N1, 0, 0)
    second = _pad("pad:b", N2, 21, 0)
    board = _snapshot(first, second)
    if kind == "custom":
        board = _snapshot(
            replace(first, copper_envelope=ir.PadCopperEnvelope(-6, -6, 6, 6)), second
        )
    elif kind == "rotation":
        board = _snapshot(replace(first, shape=ir.PadShape.RECT, rotation_udeg=45_000_000), second)
    elif kind == "netless":
        board = _snapshot(first, second, _track("segment:orphan", None, (50, 50), (60, 60)))
    elif kind == "arc":
        board = _snapshot(
            first,
            second,
            ir.Arc("arc:a", N1, F, ir.PointNM(50, 0), ir.PointNM(60, 10), ir.PointNM(70, 0), 2),
        )
    elif kind == "zone":
        board = _snapshot(
            first, second, ir.Zone("zone:a", N1, F, board.content.outline[0].outer, 10, 2, 2, 2)
        )
    elif kind == "rules":
        board = _snapshot(first, second, rules=(ir.LengthRule("rule:length", N1, 1, 100),))
    else:
        content = replace(
            board.content,
            copper_layers=(
                replace(board.content.copper_layers[0], kind="plane"),
                board.content.copper_layers[1],
            ),
        )
        board = canonical.make_snapshot(content)
    result = _measure(board)
    assert result.status == "unavailable" and result.minimum_margin_nm is None
    assert result.comparable_pairs == 0 and result.reason != "no_comparable_pair"


def test_circle_rotation_and_empty_population():
    assert (
        _measure(
            _snapshot(_pad("pad:a", N1, 0, 0, rotation=45_000_000), _pad("pad:b", N2, 21, 0))
        ).minimum_margin_nm
        == 1
    )
    assert _measure(_snapshot()).reason == "no_comparable_pair"


@pytest.mark.parametrize(
    "field,value",
    [
        ("max_objects", True),
        ("max_objects", 0),
        ("max_objects", 4097),
        ("max_pair_checks", False),
        ("max_pair_checks", float("nan")),
        ("max_pair_checks", float("inf")),
        ("max_pair_checks", 1_000_001),
    ],
)
def test_invalid_limits_are_fixed_refusals(field, value):
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(_snapshot(), **{field: value})
    _fixed(error.value)


def test_stale_snapshot_and_constraint_digest_refuse():
    board = _snapshot()
    for stale in (
        replace(board, snapshot_digest="sha256:" + "b" * 64),
        replace(board, content=replace(board.content, constraint_digest="sha256:" + "b" * 64)),
    ):
        with pytest.raises(score.ClearanceMeasurementError) as error:
            _measure(stale)
        _fixed(error.value)
    with pytest.raises(score.ClearanceMeasurementError):
        score.measure_composed_clearance(
            board,
            cast(OptimizationExecutionProbe, Probe()),
            expected_snapshot_digest="sha256:" + "b" * 64,
        )


def test_bounds_and_hostile_nested_records_refuse_before_hash(monkeypatch):
    def hashed(*args, **kwargs):
        pytest.fail("unadmitted input reached hashing")

    monkeypatch.setattr(score, "_hash_document", hashed)
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    with pytest.raises(score.ClearanceMeasurementError):
        _measure(board, max_objects=1)

    class Hostile:
        def __len__(self):
            pytest.fail("hostile length callback")

    malformed = replace(board)
    object.__setattr__(malformed, "content", Hostile())
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(malformed)
    _fixed(error.value)
    malformed = replace(board, content=replace(board.content))
    object.__setattr__(malformed.content.pads[0], "center", Hostile())
    with pytest.raises(score.ClearanceMeasurementError):
        _measure(malformed)


def test_pair_limit_and_cumulative_probe_exhaustion_refuse():
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0), _pad("pad:c", N2, 30, 0))
    for probe, limit in ((Probe(), 2), (Probe(1), 100)):
        with pytest.raises(score.ClearanceMeasurementError) as error:
            _measure(board, probe, max_pair_checks=limit)
        _fixed(error.value)
    probe = Probe()
    _measure(board, probe)
    probe.maximum = probe.used
    with pytest.raises(score.ClearanceMeasurementError):
        _measure(board, probe)


def test_late_stop_after_final_hash_withholds_result(monkeypatch):
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    probe = Probe()
    original = score._hash_document

    def stopped(document, *args, **kwargs):
        result = original(document, *args, **kwargs)
        if "method_version" in document:
            probe.stopped = True
        return result

    monkeypatch.setattr(score, "_hash_document", stopped)
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board, probe)
    _fixed(error.value)


def test_measurement_is_immutable_redacted_and_content_bound():
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    first, second = _measure(board), _measure(board)
    assert first == second and first.digest == second.digest
    assert first.snapshot_digest == board.snapshot_digest
    assert first.constraint_digest == board.content.constraint_digest
    assert repr(first) == "<ComposedClearance redacted>"
    with pytest.raises(FrozenInstanceError):
        first.minimum_margin_nm = 100
    changed = _measure(_snapshot(*board.content.pads, clearances=(9, 9)))
    assert changed.digest != first.digest and changed.minimum_margin_nm == 2


@pytest.mark.parametrize(
    "start,end,expected",
    [
        ((12, 0), (12, 10), 0),
        ((12, 0), (12, 20), -1),
        ((30, 40), (42, 56), 27),
        ((0, 5), (12, 21), 0),
    ],
)
def test_arbitrary_track_pairs_use_finite_segments(start, end, expected):
    board = _snapshot(
        _track("segment:a", N1, (0, 0), (12, 16)),
        _track("segment:b", N2, start, end),
        clearances=(1, 1),
    )
    assert _measure(board).minimum_margin_nm == expected


def test_rectangles_compare_edge_interiors_and_large_integer_coordinates():
    board = _snapshot(
        _pad("pad:a", N1, 0, 0, shape=ir.PadShape.RECT, size=(20, 4)),
        _pad("pad:b", N2, 5, 5, shape=ir.PadShape.RECT, size=(20, 4)),
        clearances=(0, 1),
    )
    assert _measure(board).minimum_margin_nm == 0
    x = ir.JSON_SAFE_INTEGER - 100
    board = _snapshot(
        _track("segment:a", N1, (x, 0), (x + 20, 0), 3),
        _track("segment:b", N2, (x, 14), (x + 20, 14), 4),
    )
    assert _measure(board).minimum_margin_nm == 0


@pytest.mark.parametrize(
    "kind",
    [
        "missing_class",
        "duplicate_pad",
        "bool_width",
        "nan_coordinate",
        "nested_tuple",
        "oversized_ring",
    ],
)
def test_invalid_ir_records_refuse_without_context(kind, monkeypatch):
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    if kind == "missing_class":
        board = replace(
            board,
            content=replace(
                board.content,
                constraints=ir.ConstraintSet(
                    board.content.constraints.net_classes, board.content.constraints.assignments[:1]
                ),
            ),
        )
    elif kind == "duplicate_pad":
        board = replace(board, content=replace(board.content, pads=(board.content.pads[0],) * 2))
    elif kind == "bool_width":
        object.__setattr__(board.content.pads[0], "size_x_nm", True)
    elif kind == "nan_coordinate":
        object.__setattr__(board.content.pads[0].center, "x", float("nan"))
    elif kind == "nested_tuple":

        class HostileTuple(tuple):
            def __len__(self):
                pytest.fail("subclass length must not be invoked")

        object.__setattr__(board.content.pads[0], "layer_ids", HostileTuple((F,)))
    else:
        object.__setattr__(board.content.outline[0].outer, "points", (ir.PointNM(0, 0),) * 257)
    monkeypatch.setattr(score, "_hash_document", lambda *a, **k: pytest.fail("must not hash"))
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board)
    _fixed(error.value)


@pytest.mark.parametrize("unavailable", [True, False])
def test_stop_during_result_hash_withholds_measured_and_unavailable(unavailable, monkeypatch):
    objects = (_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    board = _snapshot() if unavailable else _snapshot(*objects)
    probe = Probe()
    original = score._hash_document
    reached = []

    def expire(document, *args, **kwargs):
        if "method_version" in document:
            reached.append(True)
            probe.stopped = True
        return original(document, *args, **kwargs)

    monkeypatch.setattr(score, "_hash_document", expire)
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board, probe)
    _fixed(error.value)
    assert reached


def test_stop_during_geometry_withholds_result(monkeypatch):
    board = _snapshot(_pad("pad:a", N1, 0, 0), _pad("pad:b", N2, 21, 0))
    probe = Probe()
    original = score._segment_distance_squared

    def stop(*args):
        measured = original(*args)
        probe.stopped = True
        return measured

    monkeypatch.setattr(score, "_segment_distance_squared", stop)
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board, probe)
    _fixed(error.value)


def test_all_copper_pairs_and_full_layers_determine_the_minimum():
    via = ir.Via("via:a", N1, ir.PointNM(0, 0), 10, 2, F, B)
    board = _snapshot(
        via,
        _pad("pad:b", N2, 21, 0, layers=(F, B)),
        _track("segment:c", N2, (-10, 20), (10, 20)),
        clearances=(1, 1),
    )
    result = _measure(board)
    assert result.object_count == 3 and result.pair_checks == 3 and result.comparable_pairs == 2
    assert result.minimum_margin_nm == 10
    assert result.geometry_checks > result.comparable_pairs


@pytest.mark.parametrize("delta_nm", [-10_000, 0, 10_000])
def test_parser_produced_ir_preserves_quoted_atoms_and_source_identity(delta_nm):
    # Reuse the exact native fixture bytes, but do not invoke its native DRC test.
    from test_optimization_clearance_native import _board_bytes

    source = _board_bytes(delta_nm)
    net_class = ir.NetClass("class:owned", "OwnedClearance", 500_000, 250_000, 800_000, 400_000)
    converted = parse_kicad_bytes(source, KiCadConstraintProfile((net_class,), net_class.id))
    assert converted.snapshot is not None and converted.diagnostics == ()
    snapshot = converted.snapshot
    generator = snapshot.content.source.generator
    assert type(generator) is QuotedAtom
    assert all(type(layer.name) is QuotedAtom for layer in snapshot.content.copper_layers)
    assert all(type(net.name) is QuotedAtom for net in snapshot.content.nets)
    before = canonical.encode_snapshot(snapshot)

    observed = _measure(snapshot)

    assert observed.status == "measured" and observed.minimum_margin_nm == delta_nm
    assert observed.snapshot_digest == snapshot.snapshot_digest
    assert observed.constraint_digest == snapshot.content.constraint_digest
    assert canonical.encode_snapshot(snapshot) == before
    assert snapshot.content.source.generator is generator
    assert (
        parse_kicad_bytes(source, KiCadConstraintProfile((net_class,), net_class.id)).snapshot
        == snapshot
    )


@pytest.mark.parametrize("base", [str, QuotedAtom])
def test_unknown_string_subclasses_refuse_without_callbacks_or_hash(base, monkeypatch):
    callbacks = []

    class HostileString(base):
        def __len__(self):
            callbacks.append("length")
            raise RuntimeError("PRIVATE string callback")

        def encode(self, *args, **kwargs):
            callbacks.append("encode")
            raise RuntimeError("PRIVATE encoding callback")

        def __str__(self):
            callbacks.append("coercion")
            raise RuntimeError("PRIVATE coercion callback")

    board = _snapshot()
    object.__setattr__(board.content.source, "generator", HostileString("PRIVATE"))
    monkeypatch.setattr(score, "_hash_document", lambda *a, **k: pytest.fail("must not hash"))
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board)
    _fixed(error.value)
    assert callbacks == []


def test_unknown_ir_subclasses_still_refuse_without_field_callbacks(monkeypatch):
    callbacks = []

    class HostileSource(ir.SourceInfo):
        def __getattribute__(self, name):
            callbacks.append(name)
            raise RuntimeError("PRIVATE field callback")

    board = _snapshot()
    object.__setattr__(board.content, "source", object.__new__(HostileSource))
    monkeypatch.setattr(score, "_hash_document", lambda *a, **k: pytest.fail("must not hash"))
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board)
    _fixed(error.value)
    assert callbacks == []


@pytest.mark.parametrize("value", [QuotedAtom("x" * 4097), QuotedAtom("\ud800")])
def test_quoted_atoms_keep_bounded_utf8_admission(value, monkeypatch):
    board = _snapshot()
    object.__setattr__(board.content.source, "generator", value)
    monkeypatch.setattr(score, "_hash_document", lambda *a, **k: pytest.fail("must not hash"))
    with pytest.raises(score.ClearanceMeasurementError) as error:
        _measure(board)
    _fixed(error.value)
