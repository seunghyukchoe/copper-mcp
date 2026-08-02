from __future__ import annotations

import ast
import decimal
import json
import tracemalloc
from collections.abc import Callable
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from copper_mcp.board_ir import (
    Arc,
    BoardIRContent,
    BoardIRValidationError,
    ConstraintSet,
    DifferentialPairRule,
    Keepout,
    Layer,
    LengthRule,
    Net,
    NetClass,
    NetClassAssignment,
    OutlineContour,
    Pad,
    PadKind,
    PadShape,
    ParseLimits,
    PointNM,
    Ring,
    Segment,
    SourceInfo,
    Via,
    Zone,
    decode_snapshot_json,
    encode_snapshot,
    make_content,
    make_snapshot,
    mm_to_nm,
    nm_to_mm,
    normalize_rotation_udeg,
    verify_snapshot,
)

JSON_SAFE_INTEGER = (1 << 53) - 1
SOURCE_REVISION = f"sha256:{'a' * 64}"


def _ring(points: tuple[tuple[int, int], ...], *, alternate: bool = False) -> Ring:
    materialized = tuple(PointNM(x, y) for x, y in points)
    if alternate:
        materialized = tuple(reversed(materialized[1:] + materialized[:1]))
    return Ring(materialized)


def sample_content(
    *, alternate_order: bool = False, clearance_nm: int = 200_000, segment_width_nm: int = 250_000
) -> BoardIRContent:
    front = Layer(id="layer:F.Cu", name="F.Cu", index=0)
    back = Layer(id="layer:B.Cu", name="B.Cu", index=1)
    positive = Net(id="net:audio_p", name="AUDIO_P")
    negative = Net(id="net:audio_n", name="AUDIO_N")
    ground = Net(id="net:gnd", name="GND")
    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=clearance_nm,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    audio = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=250_000,
        track_width_nm=300_000,
        via_diameter_nm=900_000,
        via_drill_nm=450_000,
    )
    assignments = (
        NetClassAssignment(net_id=positive.id, net_class_id=audio.id),
        NetClassAssignment(net_id=negative.id, net_class_id=audio.id),
        NetClassAssignment(net_id=ground.id, net_class_id=default.id),
    )
    constraints = ConstraintSet(
        net_classes=(audio, default) if alternate_order else (default, audio),
        assignments=tuple(reversed(assignments)) if alternate_order else assignments,
        differential_pairs=(
            DifferentialPairRule(
                id="rule:audio_pair",
                positive_net_id=positive.id,
                negative_net_id=negative.id,
                width_nm=300_000,
                gap_nm=250_000,
                max_skew_nm=100_000,
            ),
        ),
        length_rules=(
            LengthRule(
                id="rule:audio_length",
                net_id=positive.id,
                minimum_nm=5_000_000,
                maximum_nm=20_000_000,
            ),
        ),
    )
    outline_points = ((0, 0), (40_000_000, 0), (40_000_000, 30_000_000), (0, 30_000_000))
    zone_points = (
        (1_000_000, 1_000_000),
        (8_000_000, 1_000_000),
        (8_000_000, 6_000_000),
        (1_000_000, 6_000_000),
    )
    keepout_points = (
        (10_000_000, 10_000_000),
        (12_000_000, 10_000_000),
        (12_000_000, 12_000_000),
        (10_000_000, 12_000_000),
    )
    return make_content(
        source=SourceInfo(
            format="kicad_pcb",
            revision=SOURCE_REVISION,
            format_version="20260206",
            generator="fixture",
        ),
        outline=(
            OutlineContour(
                id="contour:main",
                outer=_ring(outline_points, alternate=alternate_order),
            ),
        ),
        copper_layers=(back, front) if alternate_order else (front, back),
        nets=(ground, negative, positive) if alternate_order else (positive, negative, ground),
        constraints=constraints,
        pads=(
            Pad(
                id="pad:p1",
                net_id=positive.id,
                center=PointNM(2_000_000, 2_000_000),
                rotation_udeg=90_000_000,
                shape=PadShape.ROUNDRECT,
                kind=PadKind.SMD,
                size_x_nm=2_000_000,
                size_y_nm=1_000_000,
                roundrect_radius_nm=250_000,
                drill_x_nm=None,
                drill_y_nm=None,
                layer_ids=(front.id,),
            ),
            Pad(
                id="pad:n1",
                net_id=negative.id,
                center=PointNM(4_000_000, 2_000_000),
                rotation_udeg=0,
                shape=PadShape.CIRCLE,
                kind=PadKind.THROUGH_HOLE,
                size_x_nm=1_600_000,
                size_y_nm=1_600_000,
                roundrect_radius_nm=None,
                drill_x_nm=800_000,
                drill_y_nm=800_000,
                layer_ids=(back.id, front.id) if alternate_order else (front.id, back.id),
            ),
        ),
        vias=(
            Via(
                id="via:p1",
                net_id=positive.id,
                center=PointNM(6_000_000, 2_000_000),
                diameter_nm=800_000,
                drill_nm=400_000,
                start_layer_id=back.id if alternate_order else front.id,
                end_layer_id=front.id if alternate_order else back.id,
            ),
        ),
        segments=(
            Segment(
                id="segment:p1",
                net_id=positive.id,
                layer_id=front.id,
                start=PointNM(2_000_000, 2_000_000),
                end=PointNM(6_000_000, 2_000_000),
                width_nm=segment_width_nm,
                locked=True,
            ),
        ),
        arcs=(
            Arc(
                id="arc:n1",
                net_id=negative.id,
                layer_id=back.id,
                start=PointNM(4_000_000, 2_000_000),
                mid=PointNM(5_000_000, 3_000_000),
                end=PointNM(6_000_000, 2_000_000),
                width_nm=300_000,
            ),
        ),
        zones=(
            Zone(
                id="zone:gnd",
                net_id=ground.id,
                layer_id=back.id,
                boundary=_ring(zone_points, alternate=alternate_order),
                clearance_nm=clearance_nm,
                min_thickness_nm=200_000,
                thermal_gap_nm=300_000,
                thermal_bridge_width_nm=300_000,
            ),
        ),
        keepouts=(
            Keepout(
                id="keepout:mounting",
                layer_ids=(back.id, front.id) if alternate_order else (front.id, back.id),
                boundary=_ring(keepout_points, alternate=alternate_order),
                prohibit_tracks=True,
                prohibit_vias=True,
                prohibit_pads=False,
                prohibit_zones=True,
                prohibit_footprints=False,
            ),
        ),
    )


@pytest.mark.parametrize(
    ("token", "expected"),
    [
        ("0", 0),
        ("-0", 0),
        ("0.000001", 1),
        ("-0.000001", -1),
        ("1.234567", 1_234_567),
        ("9007199254.740991", JSON_SAFE_INTEGER),
        ("-9007199254.740991", -JSON_SAFE_INTEGER),
    ],
)
def test_exact_millimetre_conversion(token: str, expected: int) -> None:
    assert mm_to_nm(token) == expected
    assert mm_to_nm(nm_to_mm(expected)) == expected


@pytest.mark.parametrize(
    "token",
    ["+1", ".1", "01", "1e-6", "nan", "1.0000001", "9007199254.740992"],
)
def test_millimetre_conversion_rejects_rounding_and_overflow(token: str) -> None:
    with pytest.raises(ValueError):
        mm_to_nm(token)


@pytest.mark.parametrize(
    ("token", "expected"),
    [("0", 0), ("-90", 270_000_000), ("360", 0), ("450.000001", 90_000_001)],
)
def test_rotation_is_exact_and_normalized(token: str, expected: int) -> None:
    assert normalize_rotation_udeg(token) == expected


@pytest.mark.parametrize("token", ["1e-6", "0.0000001", "+90"])
def test_rotation_rejects_noncanonical_precision(token: str) -> None:
    with pytest.raises(ValueError):
        normalize_rotation_udeg(token)


def test_exact_unit_conversions_ignore_process_decimal_context() -> None:
    with decimal.localcontext() as context:
        context.prec = 3
        assert mm_to_nm("9007199254.740991") == JSON_SAFE_INTEGER
        assert nm_to_mm(JSON_SAFE_INTEGER) == "9007199254.740991"
        assert normalize_rotation_udeg("450.000001") == 90_000_001
        with pytest.raises(ValueError):
            mm_to_nm("0.00000000000000000000000000001")


# Bounded examples and deadline disabling follow Hypothesis' documented settings API:
# https://hypothesis.readthedocs.io/en/latest/reference/api.html#hypothesis.settings
@given(st.integers(min_value=-JSON_SAFE_INTEGER, max_value=JSON_SAFE_INTEGER))
@settings(max_examples=80, deadline=None)
def test_integer_unit_round_trip_property(value: int) -> None:
    assert mm_to_nm(nm_to_mm(value)) == value


@given(st.integers(min_value=-1_000_000, max_value=1_000_000))
@settings(max_examples=40, deadline=None)
def test_malformed_sizes_and_degenerate_segments_are_rejected(value: int) -> None:
    content = sample_content()
    with pytest.raises(ValueError):
        replace(content.pads[0], size_x_nm=-abs(value))

    point = PointNM(value, value)
    with pytest.raises(ValueError):
        replace(content.segments[0], start=point, end=point)


def test_pad_kind_shape_and_drill_semantics_are_explicit() -> None:
    pad = next(item for item in sample_content().pads if item.kind is PadKind.SMD)

    with pytest.raises(ValueError, match="dimensions"):
        replace(pad, shape=PadShape.CIRCLE, size_x_nm=2_000_000, size_y_nm=1_000_000)
    with pytest.raises(ValueError, match="require a drill"):
        replace(pad, kind=PadKind.THROUGH_HOLE)
    with pytest.raises(ValueError, match="electrical net"):
        replace(
            pad,
            kind=PadKind.NPTH,
            drill_x_nm=500_000,
            drill_y_nm=500_000,
        )


def test_canonical_snapshot_is_order_and_ring_invariant() -> None:
    first = make_snapshot(sample_content())
    reordered = make_snapshot(sample_content(alternate_order=True))

    assert first == reordered
    assert encode_snapshot(first) == encode_snapshot(reordered)
    assert encode_snapshot(first).endswith(b"\n")
    assert verify_snapshot(first)


def test_make_snapshot_normalizes_a_directly_reversed_via_span() -> None:
    canonical = sample_content()
    via = canonical.vias[0]
    reversed_content = replace(
        canonical,
        vias=(
            replace(
                via,
                start_layer_id=via.end_layer_id,
                end_layer_id=via.start_layer_id,
            ),
        ),
    )

    normalized = make_snapshot(reversed_content)

    assert normalized == make_snapshot(canonical)
    assert normalized.content.vias[0].start_layer_id == "layer:F.Cu"
    assert decode_snapshot_json(encode_snapshot(normalized)) == normalized


def test_board_ir_v0_1_rejects_outline_holes_and_multiple_contours() -> None:
    content = sample_content()
    hole = _ring(((1, 1), (2, 1), (2, 2), (1, 2)))

    with pytest.raises(BoardIRValidationError) as hole_error:
        make_snapshot(
            replace(
                content,
                outline=(replace(content.outline[0], holes=(hole,)),),
            )
        )
    assert hole_error.value.code == "unsupported.topology"

    with pytest.raises(BoardIRValidationError) as contour_error:
        make_snapshot(
            replace(
                content,
                outline=(
                    content.outline[0],
                    replace(content.outline[0], id="contour:second"),
                ),
            )
        )
    assert contour_error.value.code == "unsupported.topology"


def test_public_writer_rejects_schema_invalid_layer_count() -> None:
    content = sample_content()
    layers = tuple(
        Layer(id=f"layer:L{index}.Cu", name=f"L{index}.Cu", index=index) for index in range(65)
    )
    oversized = replace(
        content,
        copper_layers=layers,
        pads=(),
        vias=(),
        segments=(),
        arcs=(),
        zones=(),
        keepouts=(),
    )

    with pytest.raises(BoardIRValidationError) as caught:
        make_snapshot(oversized)

    assert caught.value.code == "schema.limit"


def test_codec_round_trip_preserves_frozen_snapshot() -> None:
    snapshot = make_snapshot(sample_content())
    encoded = encode_snapshot(snapshot)
    decoded = decode_snapshot_json(encoded)

    assert decoded == snapshot
    assert encode_snapshot(decoded) == encoded
    with pytest.raises(FrozenInstanceError):
        decoded.content.units.distance = "mm"  # type: ignore[misc]

    with pytest.raises(ValueError):
        Ring([PointNM(0, 0), PointNM(1, 0), PointNM(0, 1)])  # type: ignore[arg-type]


def test_constraint_digest_is_sensitive_only_to_constraint_projection() -> None:
    baseline = sample_content()
    changed_constraint = sample_content(clearance_nm=300_000)
    changed_geometry = sample_content(segment_width_nm=350_000)

    assert baseline.constraint_digest != changed_constraint.constraint_digest
    assert baseline.constraint_digest == changed_geometry.constraint_digest
    assert (
        make_snapshot(baseline).snapshot_digest != make_snapshot(changed_geometry).snapshot_digest
    )


def test_forged_snapshot_digest_is_rejected() -> None:
    forged = replace(make_snapshot(sample_content()), snapshot_digest=f"sha256:{'0' * 64}")

    with pytest.raises(BoardIRValidationError) as caught:
        verify_snapshot(forged)

    assert caught.value.code == "digest.snapshot_mismatch"


@pytest.mark.parametrize(
    "mutator",
    [
        lambda value: {**value, "unknown": True},
        lambda value: {
            **value,
            "content": {
                **value["content"],
                "units": {"distance": "nm", "angle": 0.0},
            },
        },
    ],
)
def test_decoder_rejects_unknown_fields_and_floats(
    mutator: Callable[[dict[str, object]], dict[str, object]],
) -> None:
    payload = json.loads(encode_snapshot(make_snapshot(sample_content())))
    malformed = mutator(payload)

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(json.dumps(malformed).encode())

    assert caught.value.code == "schema.invalid"


def test_decoder_rejects_duplicate_json_properties() -> None:
    encoded = encode_snapshot(make_snapshot(sample_content()))
    duplicate = encoded.replace(
        b'"schema":"copper.board-ir"',
        b'"schema":"copper.board-ir","schema":"copper.board-ir"',
        1,
    )

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(duplicate)

    assert caught.value.code == "schema.invalid"


def test_decoder_rejects_duplicate_property_tail_before_dom_allocation() -> None:
    payload = b"{" + b'"a":0,' * 499_999 + b'"a":0}'
    assert len(payload) == 3_000_001

    tracemalloc.start()
    try:
        with pytest.raises(BoardIRValidationError) as caught:
            decode_snapshot_json(
                payload,
                ParseLimits(max_input_bytes=4_000_000, max_children_per_list=8),
            )
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert caught.value.code == "schema.invalid"
    assert peak < 12_000_000


def test_decoder_never_echoes_attacker_controlled_property_names() -> None:
    secret = "PRIVATE_BOARD_TOKEN_" + "x" * 10_000
    payload = json.loads(encode_snapshot(make_snapshot(sample_content())))
    payload[secret] = True

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(json.dumps(payload).encode())

    rendered = str(caught.value)
    assert "PRIVATE_BOARD_TOKEN" not in rendered
    assert len(caught.value.message) <= 512


def test_decoder_never_echoes_attacker_controlled_semantic_ids() -> None:
    secret = "segment:SECRET_AUDIO_DESIGN"
    payload = json.loads(encode_snapshot(make_snapshot(sample_content())))
    payload["content"]["items"]["segments"][0]["id"] = secret
    payload["content"]["items"]["segments"][0]["net_id"] = "net:missing"

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(json.dumps(payload).encode())

    assert caught.value.code == "reference.unknown"
    assert "SECRET_AUDIO_DESIGN" not in str(caught.value)
    assert caught.value.source_locator == "content"


def test_decoder_applies_string_budget_to_property_names() -> None:
    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(b'{"ABCDE":0}', ParseLimits(max_atom_chars=4))

    assert caught.value.code == "budget.exceeded"


def test_decoder_maps_excessive_json_nesting_to_a_bounded_domain_error() -> None:
    deeply_nested = b"[" * 2_048 + b"0" + b"]" * 2_048

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(deeply_nested)

    assert caught.value.code == "budget.exceeded"


def test_geometry_package_has_no_mcp_gui_filesystem_or_adapter_imports() -> None:
    package = Path(__file__).parents[1] / "src" / "copper_mcp" / "board_ir"
    forbidden = {"mcp", "pathlib", "tkinter", "PySide6", "copper_mcp.adapters"}

    for source_path in package.glob("*.py"):
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))
        imports = {
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
        }
        imports.update(
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        )
        assert not any(
            imported == item or imported.startswith(f"{item}.")
            for imported in imports
            for item in forbidden
        ), f"forbidden Board IR import in {source_path}: {sorted(imports)}"
