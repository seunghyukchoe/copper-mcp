"""Private placed-pin census tests; no native process or authority is involved."""

from __future__ import annotations

import dataclasses
import math
import subprocess
import time
from pathlib import Path
from typing import Any, NoReturn

import pytest
from pin_census_fixtures import (
    CHILD_UUID,
    PIN_UUIDS,
    ROOT_UUID,
    SHEET_A_UUID,
    SHEET_B_UUID,
    SYMBOL_1_UUID,
    SYMBOL_2_UUID,
    SYMBOL_3_UUID,
    _basic_inputs,
    _capture,
    _library,
    _multi_body,
    _path,
    _pin,
    _placed,
    _raw_pin,
    _resistor_body,
    _sha,
    _source,
)

from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.project_pin_census import (
    ProjectPinCensus,
    ProjectPinCensusError,
    derive_project_pin_census,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.optimization.contracts import digest_document


def _derive(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    **kwargs: Any,
) -> ProjectPinCensus:
    return derive_project_pin_census(
        capture,
        libraries,
        deadline=kwargs.get("deadline", time.monotonic() + 5),
        limits=kwargs.get("limits"),
    )


def test_error_is_reexported_without_wrapping() -> None:
    from copper_mcp.engineering import _pin_census_source as source

    assert ProjectPinCensusError is source.ProjectPinCensusError


def test_derive_constructs_exactly_one_shared_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copper_mcp.engineering import _pin_census_source as source

    capture, libraries, _ = _basic_inputs(tmp_path)
    original_budget = source._Budget
    created: list[source._Budget] = []

    def make_budget() -> source._Budget:
        budget = original_budget()
        created.append(budget)
        return budget

    monkeypatch.setattr(source, "_Budget", make_budget)
    result = derive_project_pin_census(capture, libraries, deadline=time.monotonic() + 5)
    assert len(created) == 1
    assert created[0].symbol_occurrences == len(result.symbols)
    assert created[0].pin_occurrences == len(result.pins)


def test_owned_rc_census_is_exact_immutable_redacted_and_never_executes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture, libraries, source = _basic_inputs(tmp_path)
    before = (tmp_path / "root.kicad_sch").read_bytes()

    def forbidden(*_args: object, **_kwargs: object) -> NoReturn:
        pytest.fail("placed-pin census attempted native execution")

    monkeypatch.setattr(subprocess, "Popen", forbidden)
    result = _derive(capture, libraries)
    assert len(result.symbols) == 2
    assert len(result.pins) == 4
    assert [(item.effective_reference, item.source_symbol_uuid) for item in result.symbols] == [
        ("R1", SYMBOL_1_UUID),
        ("R2", SYMBOL_2_UUID),
    ]
    assert [
        (item.effective_reference, item.pin_number, item.source_pin_uuid) for item in result.pins
    ] == [
        ("R1", "1", PIN_UUIDS[0]),
        ("R1", "2", PIN_UUIDS[1]),
        ("R2", "1", PIN_UUIDS[2]),
        ("R2", "2", PIN_UUIDS[3]),
    ]
    assert {item.raw_declared_name for item in result.pins} == {"~"}
    assert {item.declared_name for item in result.pins} == {""}
    assert result == _derive(capture, libraries)
    assert (tmp_path / "root.kicad_sch").read_bytes() == before == source
    with pytest.raises((AttributeError, TypeError)):
        result.__setattr__("symbols", ())
    assert repr(result) == "<ProjectPinCensus redacted>"
    assert all("R1" not in repr(item) for item in (*result.symbols, *result.pins))

    changed_pin = dataclasses.replace(result.pins[0], effective_name="tampered")
    tampered = dataclasses.replace(result, pins=(changed_pin, *result.pins[1:]))
    assert tampered.digest != result.digest
    assert result.document() == {
        "capture_digest": capture.digest,
        "symbol_context_digest": result.symbol_context_digest,
        "census_digest": result.digest,
        "symbol_count": 2,
        "pin_count": 4,
        "native_validation": "not_run",
        "model_validation": "not_run",
        "engineering_validation": "not_run",
        "apply_authority": "none",
    }
    assert "status" not in result.document() and "pass" not in str(result.document())


def test_shared_child_occurrences_use_full_path_instance_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copper_mcp.engineering import _pin_census_source as source

    empty_root = f'''(kicad_sch (version 20250114) (generator "test")
      (uuid "{ROOT_UUID}") (lib_symbols)
      (sheet (uuid "{SHEET_A_UUID}") (property "Sheetname" "A")
        (property "Sheetfile" "child.kicad_sch"))
      (sheet (uuid "{SHEET_B_UUID}") (property "Sheetname" "B")
        (property "Sheetfile" "child.kicad_sch")))'''.encode()
    paths = (
        _path(f"/{ROOT_UUID}/{SHEET_A_UUID}", "R10", "1"),
        _path(f"/{ROOT_UUID}/{SHEET_B_UUID}", "R20", "1"),
    )
    child_symbol = _placed(
        SYMBOL_1_UUID,
        "DISPLAY",
        (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
        paths=paths,
        global_unit="2",
    )
    child = _source(_resistor_body("Test:R"), child_symbol, root_uuid=CHILD_UUID)
    capture, libraries = _capture(tmp_path, empty_root, _library(_resistor_body("R")), child=child)
    parse_instances = source._parse_instances
    lookups = 0

    class IndexedOnly(dict):
        def get(self, key, default=None):
            nonlocal lookups
            lookups += 1
            return super().get(key, default)

        def __iter__(self):
            pytest.fail("validated instance indexes must not be rescanned")

        items = values = __iter__

    monkeypatch.setattr(
        source, "_parse_instances", lambda *args: IndexedOnly(parse_instances(*args))
    )
    result = _derive(capture, libraries)
    assert lookups == len(paths)
    assert [item.effective_reference for item in result.symbols] == ["R10", "R20"]
    assert [item.selected_unit for item in result.symbols] == [1, 1]
    assert {item.sheet_uuid_path for item in result.pins} == {
        f"/{ROOT_UUID}/{SHEET_A_UUID}",
        f"/{ROOT_UUID}/{SHEET_B_UUID}",
    }


def test_units_common_pins_body_styles_alternates_hidden_and_flags(tmp_path: Path) -> None:
    common = (_raw_pin("C", PIN_UUIDS[0]),)
    first = _placed(
        SYMBOL_1_UUID,
        "#PWR01",
        (*common, _raw_pin("1", PIN_UUIDS[1], alternate="ALT"), _raw_pin("2", PIN_UUIDS[2])),
        library_id="Test:M",
        body_style="1",
        in_bom="no",
        on_board="no",
        dnp="yes",
    )
    second = _placed(
        SYMBOL_2_UUID,
        "U2",
        (
            _raw_pin("C", PIN_UUIDS[3]),
            _raw_pin("1", PIN_UUIDS[4]),
            _raw_pin("2", PIN_UUIDS[5]),
        ),
        library_id="Test:M",
        paths=(_path(f"/{ROOT_UUID}", "U2", "2"),),
        body_style="1",
    )
    third = _placed(
        SYMBOL_3_UUID,
        "U3",
        (
            _raw_pin("C", PIN_UUIDS[6]),
            _raw_pin("3", PIN_UUIDS[7]),
            _raw_pin("4", PIN_UUIDS[8]),
        ),
        library_id="Test:M",
        body_style="2",
    )
    capture, libraries = _capture(
        tmp_path,
        _source(_multi_body("Test:M"), first, second, third),
        _library(_multi_body("M")),
    )
    result = _derive(capture, libraries)
    assert [(item.effective_reference, item.pin_number) for item in result.pins] == [
        ("#PWR01", "1"),
        ("#PWR01", "C"),
        ("U2", "2"),
        ("U2", "C"),
        ("U3", "3"),
        ("U3", "C"),
    ]
    virtual = result.symbols[0]
    assert (virtual.effective_reference, virtual.in_bom, virtual.on_board, virtual.dnp) == (
        "#PWR01",
        False,
        False,
        True,
    )
    alternate = next(
        item
        for item in result.pins
        if item.effective_reference == "#PWR01" and item.pin_number == "1"
    )
    assert (alternate.declared_name, alternate.effective_name) == ("A", "ALT")
    assert (alternate.declared_electrical_type, alternate.effective_electrical_type) == (
        "passive",
        "output",
    )
    assert all(item.hidden for item in result.pins if item.pin_number == "C")
    assert all(
        item.effective_electrical_type == "no_connect"
        for item in result.pins
        if item.pin_number == "C"
    )


@pytest.mark.parametrize(("unit", "style", "raw_number"), (("2", "1", "1"), ("1", "2", "2")))
def test_unit_and_body_style_must_exist_as_a_joint_selector(
    tmp_path: Path, unit: str, style: str, raw_number: str
) -> None:
    def body(name: str) -> str:
        return f'''(symbol "{name}"
          (symbol "M_0_0" {_pin("C")})
          (symbol "M_1_1" {_pin("1")})
          (symbol "M_2_2" {_pin("2")}))'''

    placed = _placed(
        SYMBOL_1_UUID,
        "U1",
        (_raw_pin("C", PIN_UUIDS[0]), _raw_pin(raw_number, PIN_UUIDS[1])),
        library_id="Test:M",
        paths=(_path(f"/{ROOT_UUID}", "U1", unit),),
        body_style=style,
    )
    capture, libraries = _capture(tmp_path, _source(body("Test:M"), placed), _library(body("M")))
    with pytest.raises(ProjectPinCensusError, match="combination"):
        _derive(capture, libraries)


@pytest.mark.parametrize("case", ("common-style", "common-only", "declared-empty-body"))
def test_joint_selector_preserves_supported_common_and_graphical_bodies(tmp_path: Path, case: str):
    specific = (
        f'(symbol "M_2_0" {_pin("2")})'
        if case == "common-style"
        else '(symbol "M_2_1")'
        if case == "declared-empty-body"
        else ""
    )

    def body(name: str) -> str:
        return f'(symbol "{name}" (symbol "M_0_0" {_pin("C")} ){specific})'

    raw = (_raw_pin("C", PIN_UUIDS[0]),)
    if case == "common-style":
        raw += (_raw_pin("2", PIN_UUIDS[1]),)
    unit = "1" if case == "common-only" else "2"
    placed = _placed(
        SYMBOL_1_UUID,
        "U1",
        raw,
        library_id="Test:M",
        paths=(_path(f"/{ROOT_UUID}", "U1", unit),),
        body_style="1",
    )
    capture, libraries = _capture(tmp_path, _source(body("Test:M"), placed), _library(body("M")))
    result = _derive(capture, libraries)
    assert tuple(pin.pin_number for pin in result.pins) == (
        ("2", "C") if case == "common-style" else ("C",)
    )


@pytest.mark.parametrize(
    ("mutation", "match"),
    [
        ("missing-instance", "current instance"),
        ("duplicate-instance", "instance paths"),
        ("unknown-unit", "selected unit"),
        ("unknown-body", "body style"),
        ("missing-pin", "pin inventory"),
        ("extra-pin", "pin inventory"),
        ("duplicate-pin-number", "placed pins"),
        ("unresolved-alternate", "alternate"),
    ],
)
def test_missing_ambiguous_or_unknown_occurrence_data_refuses(
    tmp_path: Path, mutation: str, match: str
) -> None:
    cache = _resistor_body("Test:R")
    raw = [_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])]
    paths: tuple[str, ...] = (_path(f"/{ROOT_UUID}", "R1"),)
    body_style = None
    if mutation == "missing-instance":
        paths = (_path(f"/{CHILD_UUID}", "R1"),)
    elif mutation == "duplicate-instance":
        paths = (paths[0], paths[0])
    elif mutation == "unknown-unit":
        paths = (_path(f"/{ROOT_UUID}", "R1", "2"),)
    elif mutation == "unknown-body":
        body_style = "2"
    elif mutation == "missing-pin":
        raw.pop()
    elif mutation == "extra-pin":
        raw.append(_raw_pin("3", PIN_UUIDS[2]))
    elif mutation == "duplicate-pin-number":
        raw[1] = _raw_pin("1", PIN_UUIDS[1])
    elif mutation == "unresolved-alternate":
        raw[0] = _raw_pin("1", PIN_UUIDS[0], alternate="MISSING")
    placed = _placed(
        SYMBOL_1_UUID,
        "R1",
        tuple(raw),
        paths=paths,
        body_style=body_style,
    )
    capture, libraries = _capture(tmp_path, _source(cache, placed), _library(_resistor_body("R")))
    with pytest.raises(ProjectPinCensusError, match=match):
        _derive(capture, libraries)


def test_duplicate_library_pin_numbers_refuse_without_arbitrary_pairing(tmp_path: Path) -> None:
    placed = _placed(
        SYMBOL_1_UUID,
        "R1",
        (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
    )
    capture, libraries = _capture(
        tmp_path,
        _source(_resistor_body("Test:R", duplicate_numbers=True), placed),
        _library(_resistor_body("R", duplicate_numbers=True)),
    )
    with pytest.raises(ProjectPinCensusError, match="library pin numbers"):
        _derive(capture, libraries)


@pytest.mark.parametrize("case", ("duplicate-pin-uuid", "legacy-overbar-version"))
def test_reused_capture_guards_refuse_before_census_parsing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, case: str
) -> None:
    from copper_mcp.engineering import _pin_census_source as source

    capture, libraries, source_bytes = _basic_inputs(tmp_path)
    altered = (
        source_bytes.replace(PIN_UUIDS[1].encode(), PIN_UUIDS[0].encode())
        if case == "duplicate-pin-uuid"
        else source_bytes.replace(b"20250114", b"20210605", 1)
    )
    files = tuple(
        dataclasses.replace(item, content=altered, digest=_sha(altered))
        if item.path == "root.kicad_sch"
        else item
        for item in capture._files
    )
    capture_digest = digest_document(
        "copper-mcp/schematic-project-capture/v1",
        {
            "root_path": capture.root_path,
            "files": [
                {"path": item.path, "digest": item.digest, "size": len(item.content)}
                for item in sorted(files, key=lambda item: item.path)
            ],
        },
    )
    forged = dataclasses.replace(capture, _files=files, digest=capture_digest)
    monkeypatch.setattr(
        source,
        "_parse_source",
        lambda *_: pytest.fail("capture refusal must precede census parsing"),
    )
    with pytest.raises(ProjectPinCensusError):
        _derive(forged, libraries)


@pytest.mark.parametrize("mutation", ("uppercase-uuid", "control-reference", "leading-zero-unit"))
def test_malformed_uuid_text_and_strict_integer_refuse(tmp_path: Path, mutation: str) -> None:
    symbol_uuid = (
        "ABCDEF00-0000-4000-8000-000000000001" if mutation == "uppercase-uuid" else SYMBOL_1_UUID
    )
    reference = "R\\n1" if mutation == "control-reference" else "R1"
    unit = "01" if mutation == "leading-zero-unit" else "1"
    placed = _placed(
        symbol_uuid,
        reference,
        (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
        paths=(_path(f"/{ROOT_UUID}", reference, unit),),
    )
    capture, libraries = _capture(
        tmp_path,
        _source(_resistor_body("Test:R"), placed),
        _library(_resistor_body("R")),
    )
    with pytest.raises(ProjectPinCensusError):
        _derive(capture, libraries)


def test_capture_library_bindings_and_flat_equivalence_are_revalidated(tmp_path: Path) -> None:
    capture, libraries, _source_bytes = _basic_inputs(tmp_path)
    with pytest.raises(ProjectPinCensusError):
        _derive(dataclasses.replace(capture, digest="sha256:" + "0" * 64), libraries)
    bad_digest = dataclasses.replace(libraries[0], digest="sha256:" + "0" * 64)
    with pytest.raises(ProjectPinCensusError):
        _derive(capture, (bad_digest,))
    different = _library(_resistor_body("R").replace("pin passive", "pin input", 1))
    with pytest.raises(ProjectPinCensusError):
        _derive(capture, (different,))


@pytest.mark.parametrize(
    "deadline",
    (True, float("nan"), math.inf, 10**10_000),
    ids=("bool", "nan", "infinity", "overflowing-integer"),
)
def test_malformed_deadlines_refuse_without_context(tmp_path: Path, deadline: object) -> None:
    capture, libraries, _source_bytes = _basic_inputs(tmp_path)
    with pytest.raises(ProjectPinCensusError, match="controls") as caught:
        derive_project_pin_census(capture, libraries, deadline=deadline)  # type: ignore[arg-type]
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_malformed_limits_and_expired_or_late_deadlines_refuse(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from copper_mcp.engineering import _pin_census_source as source

    capture, libraries, _source_bytes = _basic_inputs(tmp_path)
    with pytest.raises(ProjectPinCensusError, match="controls"):
        derive_project_pin_census(
            capture,
            libraries,
            deadline=time.monotonic() + 5,
            limits=object(),  # type: ignore[arg-type]
        )
    damaged = CaptureLimits()
    object.__setattr__(damaged, "max_total_bytes", 0)
    with pytest.raises(ProjectPinCensusError, match="controls"):
        derive_project_pin_census(capture, libraries, deadline=time.monotonic() + 5, limits=damaged)
    with pytest.raises(ProjectPinCensusError, match="deadline expired"):
        _derive(capture, libraries, deadline=time.monotonic() - 1)

    clock = [100.0]
    parse = source._parse_source

    def late_parse(*args: Any, **kwargs: Any) -> Any:
        result = parse(*args, **kwargs)
        clock[0] = 106.0
        return result

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(source, "_parse_source", late_parse)
    with pytest.raises(ProjectPinCensusError, match="deadline expired"):
        derive_project_pin_census(capture, libraries, deadline=105.0)


@pytest.mark.parametrize(
    ("constant", "value", "match"),
    [
        ("_MAX_SYMBOL_OCCURRENCES", 1, "symbol occurrence"),
        ("_MAX_PIN_OCCURRENCES", 3, "pin occurrence"),
        ("_MAX_SAVED_PIN_RECORDS", 3, "saved pin record"),
        ("_MAX_LIBRARY_PINS_PER_BODY", 1, "library pin traversal"),
        ("_MAX_FIELDS", 1, "field budget"),
        ("_MAX_AGGREGATE_WORK", 1, "aggregate work"),
    ],
)
def test_cumulative_budgets_refuse_with_small_monkeypatched_ceilings(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    constant: str,
    value: int,
    match: str,
) -> None:
    from copper_mcp.engineering import _pin_census_source as source

    capture, libraries, _source_bytes = _basic_inputs(tmp_path)
    monkeypatch.setattr(source, constant, value)
    with pytest.raises(ProjectPinCensusError, match=match):
        _derive(capture, libraries)
