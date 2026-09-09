"""Pure adversarial controls for the only permitted import normalization."""

import time
import uuid

import pytest

from copper_mcp.config import Settings
from copper_mcp.optimization import native_import
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.native_import import NativeImportError, normalize_imported_board

ORIGINAL = b'(kicad_pcb (footprint "owned" (uuid "10000000-0000-0000-0000-000000000001")))'


def converted(name="Datasheet", identity="20000000-0000-0000-0000-000000000001"):
    return (
        ORIGINAL[:-2]
        + (f'(property "{name}" "\u00b5 untouched" (uuid "{identity}"))').encode()
        + b"))"
    )


def test_only_generated_uuid_characters_change_and_unicode_regions_survive(tmp_path):
    first, count = normalize_imported_board(
        ORIGINAL, converted(), Settings(workspace=tmp_path), time.monotonic() + 5
    )
    second, other = normalize_imported_board(
        ORIGINAL,
        converted(identity=str(uuid.uuid4())),
        Settings(workspace=tmp_path),
        time.monotonic() + 5,
    )
    assert first == second and count == other == 1
    assert b"\xc2\xb5 untouched" in first
    assert ORIGINAL[ORIGINAL.index(b"(uuid") : -2] in first


@pytest.mark.parametrize(
    "mutation",
    [
        lambda: converted("Reference"),
        lambda: converted().replace(
            b"10000000-0000-0000-0000-000000000001", b"10000000-0000-0000-0000-000000000002"
        ),
        lambda: converted().replace(b"(property", b"(pad"),
        lambda: (
            converted()[:-2]
            + b'(property "Datasheet" "" (uuid "30000000-0000-0000-0000-000000000001")))'
        ),
        lambda: converted(identity="10000000-0000-0000-0000-000000000001"),
    ],
)
def test_unknown_changed_duplicate_or_reused_native_ids_refuse(tmp_path, mutation):
    with pytest.raises(ValueError):
        normalize_imported_board(
            ORIGINAL, mutation(), Settings(workspace=tmp_path), time.monotonic() + 5
        )


def test_generated_identity_collision_refuses(tmp_path, monkeypatch):
    monkeypatch.setattr(
        native_import.uuid, "uuid5", lambda *_: uuid.UUID("10000000-0000-0000-0000-000000000001")
    )
    with pytest.raises(NativeImportError, match="colliding"):
        normalize_imported_board(
            ORIGINAL, converted(), Settings(workspace=tmp_path), time.monotonic() + 5
        )


@pytest.mark.parametrize("deadline", [0.0, float("nan"), float("inf"), True, 10**1000])
def test_deadline_admission_refuses_before_import_or_capture(tmp_path, monkeypatch, deadline):
    monkeypatch.setattr(
        native_import.kicad_cli,
        "_capture_kicad_cli_executable",
        lambda *_: pytest.fail("must not execute"),
    )
    with pytest.raises(NativeImportError) as caught:
        native_import.import_native_board(
            {"board.kicad_pcb": ORIGINAL},
            "board.kicad_pcb",
            Settings(workspace=tmp_path),
            deadline=deadline,
            max_output_bytes=1024,
        )
    assert caught.value.__context__ is None
    with pytest.raises(ValueError) as caught:
        prepare_optimization({}, Settings(workspace=tmp_path), deadline=deadline)
    assert not isinstance(caught.value, OverflowError)


def test_parse_budget_and_mid_parse_deadline_are_inherited(tmp_path, monkeypatch):
    settings = Settings(workspace=tmp_path, max_board_bytes=32)
    with pytest.raises(ValueError):
        normalize_imported_board(ORIGINAL, converted(), settings, time.monotonic() + 5)
    ticks = iter([1.0, 1.0, 3.0])
    monkeypatch.setattr(native_import.time, "monotonic", lambda: next(ticks, 3.0))
    with pytest.raises(NativeImportError):
        normalize_imported_board(ORIGINAL, converted(), Settings(workspace=tmp_path), 2.0)


def test_dimension_writer_repeats_same_object_uuid_without_creating_an_identity(tmp_path):
    from test_native_import_admission import DIMENSION

    output = ORIGINAL[:-1] + DIMENSION.encode() + b")"
    # The old source may omit the nested copy of the parent's UUID. Native formatting
    # emits it twice for the same dimension object, not as a newly minted object ID.
    old = DIMENSION.replace(
        '(gr_text "dimension" (at 2 3 0) (layer "Dwgs.User")\n'
        '   (uuid "91000000-0000-0000-0000-000000000090")',
        '(gr_text "dimension" (at 2 3 0) (layer "Dwgs.User")',
    )
    original = ORIGINAL[:-1] + old.encode() + b")"
    assert original != output
    normalized, count = normalize_imported_board(
        original, output, Settings(workspace=tmp_path), time.monotonic() + 5
    )
    assert normalized == output and count == 0
    reused = output[:-1] + b'(gr_text "other" (uuid "91000000-0000-0000-0000-000000000090")))'
    with pytest.raises(NativeImportError):
        normalize_imported_board(reused, reused, Settings(workspace=tmp_path), time.monotonic() + 5)


@pytest.mark.parametrize("head", ["uuid", "tstamp"])
def test_dimension_repeated_identity_is_not_rewritten(tmp_path, head):
    from test_native_import_admission import DIMENSION

    source = ORIGINAL[:-1] + DIMENSION.replace("(uuid ", f"({head} ").encode() + b")"
    assert normalize_imported_board(
        source, source, Settings(workspace=tmp_path), time.monotonic() + 5
    ) == (source, 0)


@pytest.mark.parametrize(
    "case",
    [
        "conflict",
        "ambiguous",
        "two_texts",
        "two_dimensions",
        "footprint",
        "pad",
        "nested_reuse",
    ],
)
def test_dimension_identity_exception_cannot_hide_other_uses(tmp_path, case):
    identity = '"91000000-0000-0000-0000-000000000090"'
    different = '"91000000-0000-0000-0000-000000000091"'
    text = f'(gr_text "owned" (uuid {identity}))'
    extra = ""
    if case == "conflict":
        text = text.replace(identity, different)
    elif case == "ambiguous":
        text = text[:-1] + f"(tstamp {identity}))"
    elif case == "two_texts":
        text += text
    elif case == "nested_reuse":
        text = text[:-1] + f"(effects (uuid {identity})))"
    elif case in {"footprint", "pad"}:
        extra = f'({case} "other" (uuid {identity}))'
    dimension = f"(dimension (uuid {identity}) {text})"
    if case == "two_dimensions":
        extra = dimension
    source = ORIGINAL[:-1] + (dimension + extra).encode() + b")"
    with pytest.raises(NativeImportError):
        normalize_imported_board(source, source, Settings(workspace=tmp_path), time.monotonic() + 5)


def _owned_objects():
    def identity(number):
        return f'(uuid "10000000-0000-0000-0000-{number:012d}")'

    return (
        f'(kicad_pcb (footprint "A" {identity(1)}'
        f'(pad "1" {identity(3)})(pad "2" {identity(4)})'
        f'(property "Foo" "" {identity(5)})(property "Bar" "" {identity(6)}))'
        f'(footprint "B" {identity(2)}(pad "1" {identity(7)})'
        f'(property "Foo" "" {identity(8)})))'
    ).encode()


@pytest.mark.parametrize("left,right", [(3, 7), (3, 4), (5, 6), (5, 8), (3, 5)])
def test_existing_uuid_counts_cannot_hide_changed_owner_kind_or_name(tmp_path, left, right):
    source = _owned_objects()
    first = f"10000000-0000-0000-0000-{left:012d}".encode()
    second = f"10000000-0000-0000-0000-{right:012d}".encode()
    swapped = source.replace(first, b"SWAP").replace(second, first).replace(b"SWAP", second)
    assert swapped != source
    with pytest.raises(NativeImportError):
        normalize_imported_board(
            source, swapped, Settings(workspace=tmp_path), time.monotonic() + 5
        )


def test_unchanged_semantic_owners_and_legacy_reference_field_are_preserved(tmp_path):
    source = _owned_objects()
    assert normalize_imported_board(
        source, source, Settings(workspace=tmp_path), time.monotonic() + 5
    ) == (source, 0)
    modern = source.replace(b'(property "Foo" ""', b'(property "Reference" ""', 1)
    legacy = modern.replace(b'(property "Reference" ""', b'(fp_text reference ""', 1)
    assert normalize_imported_board(
        legacy, modern, Settings(workspace=tmp_path), time.monotonic() + 5
    ) == (modern, 0)
