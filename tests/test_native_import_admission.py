"""Closed source admission for imported drawing dimensions and filter metadata."""

from dataclasses import replace

import pytest
from test_kicad_placement_patch import FIXTURE, _profile

from copper_mcp.adapters.kicad_board_ir import parse_kicad_bytes

DIMENSION = """(dimension (type aligned) (layer "Dwgs.User")
 (uuid "91000000-0000-0000-0000-000000000090")
 (pts (xy 1 2) (xy 3 4)) (height 2)
 (format (prefix "") (suffix "") (units 3) (units_format 1) (precision 2))
 (style (thickness 0.15) (arrow_length 1.27) (text_position_mode 0)
   (arrow_direction outward) (extension_height 0.5) (extension_offset 0.5) (keep_text_aligned yes))
 (gr_text "dimension" (at 2 3 0) (layer "Dwgs.User")
   (uuid "91000000-0000-0000-0000-000000000090")
   (effects (font (size 1 1) (thickness 0.15)))))"""


def _source(extra):
    source = FIXTURE.read_bytes()
    return source.rstrip()[:-1] + extra.encode() + b")\n"


def _dimension(kind):
    result = DIMENSION.replace("(type aligned)", f"(type {kind})")
    if kind == "orthogonal":
        return result.replace("(height 2)", "(height -2) (orientation 1)")
    if kind == "leader":
        return (
            result.replace("(height 2)", "")
            .replace("(arrow_direction outward)", "(text_frame 0)")
            .replace("(extension_height 0.5)", "")
        )
    return result


@pytest.mark.parametrize("kind", ["aligned", "orthogonal", "leader"])
def test_import_profile_admits_drawing_dimension_without_widening_default(kind):
    source = _source(_dimension(kind))
    assert parse_kicad_bytes(source, _profile()).snapshot is None
    assert (
        parse_kicad_bytes(source, replace(_profile(), native_import_metadata=True)).snapshot
        is not None
    )


@pytest.mark.parametrize(
    "kind,old,new",
    [
        ("orthogonal", "(orientation 1)", "(orientation 2)"),
        ("orthogonal", "(orientation 1)", ""),
        ("orthogonal", "(height -2)", ""),
        ("leader", "(text_frame 0)", "(text_frame 4)"),
        ("leader", "(text_frame 0)", ""),
        ("leader", "(type leader)", "(type leader) (height 2)"),
        ("aligned", "(height 2)", "(height 2) (orientation 0)"),
    ],
)
def test_dimension_type_specific_fields_stay_closed(kind, old, new):
    result = parse_kicad_bytes(
        _source(_dimension(kind).replace(old, new)),
        replace(_profile(), native_import_metadata=True),
    )
    assert result.snapshot is None


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace('"Dwgs.User"', '"F.Cu"'),
        lambda s: s.replace('"Dwgs.User"', '"Edge.Cuts"'),
        lambda s: s.replace("(type aligned)", "(type future)"),
        lambda s: s.replace("(height 2)", "(height 2 3)"),
        lambda s: s.replace("(style ", "(style (unknown 1) "),
        lambda s: s.replace(
            '(gr_text "dimension"', '(gr_text "dimension" (render_cache "cache" 0)'
        ),
    ],
)
def test_dimension_unsupported_or_routing_semantics_refuse(change):
    assert (
        parse_kicad_bytes(
            _source(change(DIMENSION)), replace(_profile(), native_import_metadata=True)
        ).snapshot
        is None
    )


def test_native_filter_metadata_is_not_a_layerless_graphic():
    source = FIXTURE.read_bytes().replace(
        b"(at 45 15 90)", b'(property ki_fp_filters "R_*") (at 45 15 90)'
    )
    assert parse_kicad_bytes(source, _profile()).snapshot is None
    assert (
        parse_kicad_bytes(source, replace(_profile(), native_import_metadata=True)).snapshot
        is not None
    )
    for token in (
        b'(property ki_fp_filters "R_*" extra)',
        b'(property ki_keywords "R_*")',
        b'(property "future" "R_*")',
    ):
        malformed = source.replace(b'(property ki_fp_filters "R_*")', token)
        assert (
            parse_kicad_bytes(malformed, replace(_profile(), native_import_metadata=True)).snapshot
            is None
        )


@pytest.mark.parametrize(
    "change",
    [
        lambda s: s.replace('(at 2 3 0) (layer "Dwgs.User")', '(at 2 3 0) (layer "F.Cu")'),
        lambda s: s.replace('(at 2 3 0) (layer "Dwgs.User")', '(at 2 3 0) (layer "Edge.Cuts")'),
        lambda s: s.replace('(at 2 3 0) (layer "Dwgs.User")', "(at 2 3 0)"),
        lambda s: s.replace('(gr_text "dimension"', '(gr_text "dimension" (locked yes)'),
        lambda s: s.replace("(dimension ", "(dimension (locked yes) "),
        lambda s: s.replace(
            '(uuid "91000000-0000-0000-0000-000000000090")',
            '(uuid "91000000-0000-0000-0000-000000000091")',
            1,
        ),
        lambda s: s + s,
        lambda s: (
            s + '(gr_text "duplicate" (layer "Dwgs.User") '
            '(uuid "91000000-0000-0000-0000-000000000090"))'
        ),
    ],
)
@pytest.mark.parametrize("kind", ["aligned", "orthogonal", "leader"])
def test_native_dimension_nested_object_cannot_override_identity_or_contact(change, kind):
    assert (
        parse_kicad_bytes(
            _source(change(_dimension(kind))), replace(_profile(), native_import_metadata=True)
        ).snapshot
        is None
    )


def test_native_dimension_agreeing_explicit_locks_preserve_same_object():
    for lock in ("yes", "no"):
        dimension = DIMENSION.replace("(dimension ", f"(dimension (locked {lock}) ").replace(
            '(gr_text "dimension"', f'(gr_text "dimension" (locked {lock})'
        )
        assert (
            parse_kicad_bytes(
                _source(dimension), replace(_profile(), native_import_metadata=True)
            ).snapshot
            is not None
        )
