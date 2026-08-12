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
    ConversionResult,
    Diagnostic,
    DifferentialPairRule,
    Footprint,
    FootprintSide,
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
    Severity,
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
    validate_content,
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
    amplifier_courtyard = (
        (1_000_000, 1_000_000),
        (7_000_000, 1_000_000),
        (7_000_000, 4_000_000),
        (1_000_000, 4_000_000),
    )
    mechanical_courtyard = (
        (28_000_000, 18_000_000),
        (32_000_000, 18_000_000),
        (32_000_000, 22_000_000),
        (28_000_000, 22_000_000),
    )
    footprints = (
        Footprint(
            id="footprint:amplifier",
            origin=PointNM(3_000_000, 2_000_000),
            rotation_udeg=90_000_000,
            side=FootprintSide.FRONT,
            pad_ids=("pad:n1", "pad:p1") if alternate_order else ("pad:p1", "pad:n1"),
            courtyards=(_ring(amplifier_courtyard, alternate=alternate_order),),
        ),
        Footprint(
            id="footprint:mechanical",
            origin=PointNM(30_000_000, 20_000_000),
            rotation_udeg=270_000_000,
            side=FootprintSide.BACK,
            pad_ids=(),
            courtyards=(_ring(mechanical_courtyard, alternate=alternate_order),),
            locked=True,
        ),
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
        footprints=tuple(reversed(footprints)) if alternate_order else footprints,
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


def test_footprint_domain_fields_are_exact_typed_and_frozen() -> None:
    content = sample_content()
    amplifier = next(item for item in content.footprints if item.id == "footprint:amplifier")
    mechanical = next(item for item in content.footprints if item.id == "footprint:mechanical")

    assert amplifier.origin == PointNM(3_000_000, 2_000_000)
    assert amplifier.rotation_udeg == 90_000_000
    assert amplifier.side is FootprintSide.FRONT
    assert amplifier.pad_ids == ("pad:n1", "pad:p1")
    assert len(amplifier.courtyards) == 1
    assert mechanical.side is FootprintSide.BACK
    assert mechanical.locked is True

    with pytest.raises(FrozenInstanceError):
        amplifier.locked = True  # type: ignore[misc]
    with pytest.raises(ValueError, match="footprint ID"):
        replace(amplifier, id="pad:not-a-footprint")
    with pytest.raises(ValueError, match="footprint rotation"):
        replace(amplifier, rotation_udeg=360_000_000)
    with pytest.raises(ValueError, match="footprint side"):
        replace(amplifier, side="front")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="footprint pad ID"):
        replace(amplifier, pad_ids=("segment:not-a-pad",))


def test_canonical_snapshot_is_order_and_ring_invariant() -> None:
    first = make_snapshot(sample_content())
    reordered = make_snapshot(sample_content(alternate_order=True))

    assert first == reordered
    assert encode_snapshot(first) == encode_snapshot(reordered)
    assert encode_snapshot(first).endswith(b"\n")
    assert verify_snapshot(first)
    assert [item.id for item in first.content.footprints] == [
        "footprint:amplifier",
        "footprint:mechanical",
    ]
    assert first.content.footprints[0].pad_ids == ("pad:n1", "pad:p1")


def test_every_pad_has_exactly_one_first_class_footprint_owner() -> None:
    content = sample_content()
    amplifier, mechanical = content.footprints
    malformed = (
        (
            replace(
                content,
                footprints=(replace(amplifier, pad_ids=("pad:p1",)), mechanical),
            ),
            "reference.unowned",
        ),
        (
            replace(
                content,
                footprints=(
                    replace(amplifier, pad_ids=(*amplifier.pad_ids, "pad:missing")),
                    mechanical,
                ),
            ),
            "reference.unknown",
        ),
        (
            replace(
                content,
                footprints=(amplifier, replace(mechanical, pad_ids=("pad:p1",))),
            ),
            "identity.duplicate",
        ),
        (
            replace(
                content,
                footprints=(
                    replace(amplifier, pad_ids=(*amplifier.pad_ids, "pad:p1")),
                    mechanical,
                ),
            ),
            "identity.duplicate",
        ),
    )

    for candidate, expected_code in malformed:
        with pytest.raises(BoardIRValidationError) as caught:
            validate_content(candidate)
        assert caught.value.code == expected_code


def test_duplicate_geometry_ids_are_still_refused_after_the_uuid_reuse_fallback() -> None:
    """The adapter stopped *creating* duplicate geometry IDs; the invariant that rejects them stays.

    Issue #116's `identity.duplicate` refusal was fixed in the KiCad converter, which must not
    project a reused KiCad UUID as an identity.  This control exists so that fix cannot be
    mistaken for licence to relax the rule: content that genuinely names two objects the same is
    still refused, with the invariant named and no board-derived text in the message.
    """

    content = sample_content()
    duplicates = (
        replace(content, segments=(content.segments[0], replace(content.segments[0]))),
        replace(content, vias=(content.vias[0], replace(content.vias[0]))),
        replace(content, arcs=(content.arcs[0], replace(content.arcs[0]))),
        replace(content, zones=(content.zones[0], replace(content.zones[0]))),
    )

    for candidate in duplicates:
        with pytest.raises(BoardIRValidationError) as caught:
            validate_content(candidate)
        assert caught.value.code == "identity.duplicate"
        assert caught.value.message == "duplicate geometry ID"


def test_validation_messages_name_invariants_without_echoing_board_content() -> None:
    """Every refusal message the adapter surfaces has to be a fixed string this module chose.

    The adapter now appends the validation message to its refusal, so a message built from an
    object ID would leak board content into a diagnostic that deliberately drops the locator.
    """

    content = sample_content()
    amplifier, mechanical = content.footprints
    leaky = (
        replace(
            content,
            footprints=(
                replace(amplifier, pad_ids=(*amplifier.pad_ids, amplifier.pad_ids[0])),
                mechanical,
            ),
        ),
        replace(
            content,
            pads=(
                replace(content.pads[0], layer_ids=(content.copper_layers[0].id,) * 2),
                *content.pads[1:],
            ),
        ),
    )
    expected = ("duplicate pad ownership within one footprint", "duplicate layer reference")

    for candidate, fragment in zip(leaky, expected, strict=True):
        with pytest.raises(BoardIRValidationError) as caught:
            validate_content(candidate)
        assert caught.value.message.startswith(fragment)
        assert ":" not in caught.value.message
        assert caught.value.source_locator != caught.value.message


def test_footprints_and_courtyards_are_charged_to_validation_budgets() -> None:
    content = sample_content()
    object_groups = (
        content.outline,
        content.copper_layers,
        content.nets,
        content.constraints.net_classes,
        content.constraints.assignments,
        content.constraints.differential_pairs,
        content.constraints.length_rules,
        content.footprints,
        content.pads,
        content.vias,
        content.segments,
        content.arcs,
        content.zones,
        content.keepouts,
    )
    object_count = sum(len(group) for group in object_groups)
    assert object_count == 22
    validate_content(content, ParseLimits(max_objects=object_count))
    with pytest.raises(BoardIRValidationError) as object_error:
        validate_content(content, ParseLimits(max_objects=object_count - 1))
    assert object_error.value.code == "budget.exceeded.objects"

    total_vertices = (
        sum(len(contour.outer.points) for contour in content.outline)
        + sum(len(zone.boundary.points) for zone in content.zones)
        + sum(len(keepout.boundary.points) for keepout in content.keepouts)
        + sum(
            len(courtyard.points)
            for footprint in content.footprints
            for courtyard in footprint.courtyards
        )
    )
    assert total_vertices == 20
    validate_content(content, ParseLimits(max_total_vertices=total_vertices))
    with pytest.raises(BoardIRValidationError) as vertex_error:
        validate_content(content, ParseLimits(max_total_vertices=total_vertices - 1))
    assert vertex_error.value.code == "budget.exceeded.total_vertices"


def test_one_footprint_may_have_at_most_64_courtyard_rings() -> None:
    content = sample_content()
    amplifier, mechanical = content.footprints
    courtyard = amplifier.courtyards[0]
    at_limit = replace(
        content,
        footprints=(replace(amplifier, courtyards=(courtyard,) * 64), mechanical),
    )
    validate_content(at_limit)

    over_limit = replace(
        content,
        footprints=(replace(amplifier, courtyards=(courtyard,) * 65), mechanical),
    )
    with pytest.raises(BoardIRValidationError) as caught:
        validate_content(over_limit)
    assert caught.value.code == "schema.limit"


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


def test_board_ir_v0_2_rejects_outline_holes_and_multiple_contours() -> None:
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
        footprints=(),
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

    assert caught.value.code == "budget.exceeded.atom_chars"


def test_decoder_maps_excessive_json_nesting_to_a_bounded_domain_error() -> None:
    deeply_nested = b"[" * 2_048 + b"0" + b"]" * 2_048

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(deeply_nested)

    assert caught.value.code == "budget.exceeded.depth"


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


def test_board_ir_v0_2_accepts_only_octilinear_courtyard_rings() -> None:
    """Externally-produced Board IR must obey the adapter's exact courtyard subset.

    The adapter can import a simple closed line-chain or unfilled polygon whose edges are
    horizontal, vertical, or exact 45-degree chamfers.  Validation must accept the same bounded
    topology while continuing to refuse arbitrary-slope geometry, otherwise a hand-written JSON
    snapshot could claim a collision region that the deterministic legalizer does not model.
    """

    content = sample_content()
    footprint = content.footprints[0]

    # Every edge of this triangle is horizontal or an exact 45-degree diagonal, so it is inside
    # the octilinear subset and must round-trip - it used to be refused when the subset was
    # orthogonal-only.
    triangle = _ring(((1_000_000, 1_000_000), (7_000_000, 1_000_000), (4_000_000, 4_000_000)))
    chamfer_snapshot = make_snapshot(
        replace(
            content,
            footprints=(replace(footprint, courtyards=(triangle,)), *content.footprints[1:]),
        )
    )
    assert decode_snapshot_json(encode_snapshot(chamfer_snapshot)) == chamfer_snapshot

    concave_orthogonal = _ring(
        (
            (1_000_000, 1_000_000),
            (7_000_000, 1_000_000),
            (7_000_000, 7_000_000),
            (5_000_000, 7_000_000),
            (5_000_000, 4_000_000),
            (3_000_000, 4_000_000),
            (3_000_000, 7_000_000),
            (1_000_000, 7_000_000),
        )
    )
    snapshot = make_snapshot(
        replace(
            content,
            footprints=(
                replace(footprint, courtyards=(concave_orthogonal,)),
                *content.footprints[1:],
            ),
        )
    )
    assert decode_snapshot_json(encode_snapshot(snapshot)) == snapshot

    # A four-vertex ring is not enough: an edge of arbitrary slope stays outside the subset,
    # exactly as a rectangle rotated by a non-multiple of 45 degrees would.
    skewed = _ring(
        (
            (1_000_000, 1_000_000),
            (7_000_000, 1_500_000),
            (7_000_000, 4_000_000),
            (1_000_000, 4_000_000),
        )
    )
    with pytest.raises(BoardIRValidationError) as skew_error:
        make_snapshot(
            replace(
                content,
                footprints=(replace(footprint, courtyards=(skewed,)), *content.footprints[1:]),
            )
        )
    assert skew_error.value.code == "unsupported.topology"

    # The refusal names the contract, never the caller's geometry.
    assert "1000000" not in skew_error.value.message


def test_board_ir_v0_2_accepts_orthogonal_courtyards_at_every_quarter_turn() -> None:
    """Guard the guard: quarter turns must preserve valid orthogonal courtyard topology."""

    content = sample_content()
    footprint = content.footprints[0]
    corners = (
        (1_000_000, 2_000_000),
        (7_000_000, 2_000_000),
        (7_000_000, 5_000_000),
        (1_000_000, 5_000_000),
    )

    for turn in range(4):
        rotated: list[tuple[int, int]] = []
        for x, y in corners:
            for _ in range(turn):
                x, y = y, -x
            rotated.append((x, y))
        snapshot = make_snapshot(
            replace(
                content,
                footprints=(
                    replace(footprint, courtyards=(_ring(tuple(rotated)),)),
                    *content.footprints[1:],
                ),
            )
        )
        assert decode_snapshot_json(encode_snapshot(snapshot)) == snapshot


def test_a_conversion_group_count_must_be_a_non_negative_integer() -> None:
    """The unmodelled-group count is a count, and a bool is not one.

    ``isinstance(True, int)`` is true in Python, so a validator that only tested ``int`` would
    accept ``True`` as "one group" and ``False`` as "no groups". The same trap the rounding
    beside it avoids.
    """

    snapshot = make_snapshot(sample_content())

    assert ConversionResult(snapshot=snapshot, unmodelled_group_count=0).unmodelled_group_count == 0
    assert ConversionResult(snapshot=snapshot, unmodelled_group_count=3).unmodelled_group_count == 3
    for bad in (True, False, -1, 1.0, "1", None):
        with pytest.raises(ValueError, match="group count"):
            ConversionResult(snapshot=snapshot, unmodelled_group_count=bad)  # type: ignore[arg-type]


def test_a_refused_conversion_cannot_report_a_group_count() -> None:
    """A refusal converted nothing, so it accepted no group and must not claim one."""

    refusal = (
        Diagnostic(
            code="unsupported.construct",
            severity=Severity.ERROR,
            message="root expression contains an unsupported semantic construct",
            source_locator="kicad_pcb.child[3]",
        ),
    )

    assert ConversionResult(snapshot=None, diagnostics=refusal).unmodelled_group_count == 0
    with pytest.raises(ValueError, match="cannot report a group count"):
        ConversionResult(snapshot=None, diagnostics=refusal, unmodelled_group_count=1)


def test_a_conversion_board_property_count_must_be_a_non_negative_integer() -> None:
    """The unmodelled board-property count is held to the same rule as the two counts above it."""

    snapshot = make_snapshot(sample_content())

    assert (
        ConversionResult(
            snapshot=snapshot, unmodelled_board_property_count=0
        ).unmodelled_board_property_count
        == 0
    )
    assert (
        ConversionResult(
            snapshot=snapshot, unmodelled_board_property_count=2
        ).unmodelled_board_property_count
        == 2
    )
    for bad in (True, False, -1, 1.0, "1", None):
        with pytest.raises(ValueError, match="board property count"):
            ConversionResult(snapshot=snapshot, unmodelled_board_property_count=bad)  # type: ignore[arg-type]


def test_a_refused_conversion_cannot_report_a_board_property_count() -> None:
    """A refusal converted nothing, so it accepted no board property and must not claim one."""

    refusal = (
        Diagnostic(
            code="unsupported.construct",
            severity=Severity.ERROR,
            message="root expression contains an unsupported semantic construct",
            source_locator="kicad_pcb.child[3]",
        ),
    )

    assert ConversionResult(snapshot=None, diagnostics=refusal).unmodelled_board_property_count == 0
    with pytest.raises(ValueError, match="cannot report a board property count"):
        ConversionResult(snapshot=None, diagnostics=refusal, unmodelled_board_property_count=1)
