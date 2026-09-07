from __future__ import annotations

import csv
import os
import subprocess
import sys
import time
import traceback
from collections.abc import Callable, Iterable
from importlib.machinery import ModuleSpec
from types import ModuleType
from typing import Any

import pytest

from copper_mcp.engineering.bom_csv import BomCsvError, BomCsvRow, parse_bom_csv

_HEADERS = '"Refs","Value","Footprint","Qty","DNP"\n'


def parse(text: str) -> tuple[BomCsvRow, ...]:
    return parse_bom_csv(
        text.encode(), known_references=frozenset(), deadline=time.monotonic() + 10
    )


def test_reads_native_profile_rows_without_claiming_completeness() -> None:
    rows = parse(_HEADERS + '"C1","100n","","1","DNP"\n"R1","1k","","1",""\n')
    assert rows == (
        BomCsvRow(("C1",), "100n", "", 1, True, ()),
        BomCsvRow(("R1",), "1k", "", 1, False, ()),
    )


def test_header_only_input_is_empty_without_a_completion_claim() -> None:
    assert parse(_HEADERS) == ()


@pytest.mark.parametrize("ambient_limit", (0, 1, 10))
def test_ambient_csv_field_limit_does_not_change_accepted_rows(ambient_limit) -> None:
    previous = csv.field_size_limit(ambient_limit)
    original_backend = sys.modules["_csv"]
    original_reader = csv.reader
    try:
        rows = parse(_HEADERS + '"R1","abcdefghijklmnopqrst","","1",""\n')
        assert rows == (BomCsvRow(("R1",), "abcdefghijklmnopqrst", "", 1, False, ()),)
        assert csv.field_size_limit() == ambient_limit
        assert sys.modules["_csv"] is original_backend
        assert csv.reader is original_reader
        with pytest.raises(csv.Error):
            list(csv.reader(["abcdefghijklmnopqrst"]))
    finally:
        csv.field_size_limit(previous)


def test_field_limit_changes_during_parsing_do_not_affect_the_reader(monkeypatch) -> None:
    from copper_mcp.engineering import bom_csv

    previous = csv.field_size_limit()
    validate_text = bom_csv._validate_text

    def lower_ambient_limit(value):
        csv.field_size_limit(1)
        return validate_text(value)

    monkeypatch.setattr(bom_csv, "_validate_text", lower_ambient_limit)
    try:
        rows = parse(_HEADERS + '"R1","abcdefghijklmnopqrst","","1",""\n')
        assert rows[0].value == "abcdefghijklmnopqrst"
        assert csv.field_size_limit() == 1
    finally:
        csv.field_size_limit(previous)


def test_ambient_dialect_registry_is_not_used_or_modified() -> None:
    previous = csv.get_dialect("excel")
    csv.register_dialect("excel", delimiter=";", quotechar="'")
    try:
        configured = csv.get_dialect("excel")
        rows = parse(_HEADERS + '"R1","quoted, ""value""","","1",""\n')
        assert rows[0].value == 'quoted, "value"'
        assert csv.get_dialect("excel") is configured
    finally:
        csv.register_dialect("excel", previous)


@pytest.mark.parametrize("mode", ("missing-spec", "shared-module", "loader-error"))
def test_unavailable_private_backend_refuses_without_context(monkeypatch, mode) -> None:
    from copper_mcp.engineering import bom_csv

    previous = csv.field_size_limit()
    if mode == "missing-spec":
        monkeypatch.setattr(bom_csv._csv, "__spec__", None)
    elif mode == "shared-module":
        monkeypatch.setattr(bom_csv, "module_from_spec", lambda _spec: bom_csv._csv)
    else:

        def unavailable(_spec):
            raise ImportError("private model location")

        monkeypatch.setattr(bom_csv, "module_from_spec", unavailable)
    with pytest.raises(BomCsvError, match=r"^BOM CSV backend is unavailable$") as caught:
        parse(_HEADERS)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert csv.field_size_limit() == previous
    assert "private model location" not in "".join(traceback.format_exception(caught.value))


def test_backend_initialization_is_inside_the_caller_deadline(monkeypatch) -> None:
    from copper_mcp.engineering import bom_csv

    create_module = bom_csv.module_from_spec
    original_clock = time.monotonic
    expired = False

    def expire_during_initialization(spec):
        nonlocal expired
        module = create_module(spec)
        expired = True
        return module

    monkeypatch.setattr(bom_csv, "module_from_spec", expire_during_initialization)
    monkeypatch.setattr(time, "monotonic", lambda: original_clock() + (3601 if expired else 0))
    with pytest.raises(BomCsvError, match="deadline expired"):
        parse(_HEADERS)


@pytest.mark.parametrize("mode", ("exec-error", "shared-error"))
def test_private_module_execution_failure_never_changes_shared_state(monkeypatch, mode) -> None:
    from copper_mcp.engineering import bom_csv

    original_error = bom_csv._csv.Error
    previous = csv.field_size_limit(10)

    class Loader:
        def create_module(self, spec):
            return ModuleType("_csv")

        def exec_module(self, module):
            if mode == "exec-error":
                raise ImportError("private loader location")
            module.Error = original_error
            module.field_size_limit = lambda *_: pytest.fail("must not modify shared state")

    monkeypatch.setattr(bom_csv._csv, "__spec__", ModuleSpec("_csv", Loader()))
    try:
        with pytest.raises(BomCsvError, match="backend is unavailable") as caught:
            parse(_HEADERS)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert csv.field_size_limit() == 10
        assert bom_csv._csv.Error is original_error
    finally:
        csv.field_size_limit(previous)


@pytest.mark.parametrize("name", ("max_rows", "max_references"))
@pytest.mark.parametrize("value", (True, -1, 100_001, "1"))
def test_rejects_invalid_remaining_budgets(name, value) -> None:
    with pytest.raises(BomCsvError):
        parse_bom_csv(
            _HEADERS.encode(),
            known_references=frozenset(),
            deadline=time.monotonic() + 10,
            **{name: value},
        )


def test_empty_file_can_consume_zero_remaining_budget() -> None:
    assert (
        parse_bom_csv(
            _HEADERS.encode(),
            known_references=frozenset(),
            deadline=time.monotonic() + 10,
            max_rows=0,
            max_references=0,
        )
        == ()
    )


def test_remaining_row_budget_refuses_before_processing_extra_row(monkeypatch) -> None:
    from copper_mcp.engineering import bom_csv

    quantity = bom_csv._quantity
    calls = []

    def observe(value):
        calls.append(value)
        return quantity(value)

    monkeypatch.setattr(bom_csv, "_quantity", observe)
    with pytest.raises(BomCsvError):
        parse_bom_csv(
            (_HEADERS + "R1,1k,,1,\nR2,1k,,1,\n").encode(),
            known_references=frozenset(),
            deadline=time.monotonic() + 10,
            max_rows=1,
        )
    assert calls == ["1"]


def test_remaining_reference_budget_refuses_before_large_expansion() -> None:
    with pytest.raises(BomCsvError, match="exceeds limits"):
        parse_bom_csv(
            (_HEADERS + "R1-R100000,1k,,100000,\n").encode(),
            known_references=frozenset(),
            deadline=time.monotonic() + 10,
            max_references=1,
        )


def test_numeric_fields_do_not_depend_on_interpreter_digit_limit() -> None:
    script = r"""
import time
from decimal import localcontext
from copper_mcp.engineering.bom_csv import BomCsvError, parse_bom_csv
header = '"Refs","Value","Footprint","Qty","DNP"\n'
start = "1" + "0" * 640
end = start[:-1] + "1"
def read(refs, qty):
    payload = (header + f'"{refs}","1k","",{qty},""\n').encode()
    return parse_bom_csv(payload, known_references=frozenset(), deadline=time.monotonic()+10)
with localcontext() as context:
    context.prec = 1
    context.Emax = 1
    assert read("R" + start + "-R" + end, "2")[0].references == ("R" + start, "R" + end)
    assert read("R1", "0" * 640 + "1")[0].quantity == 1
    try:
        read("R1", "9" * 641)
    except BomCsvError as error:
        assert error.__context__ is None and error.__cause__ is None
    else:
        raise AssertionError("oversize quantity accepted")
print("numeric profile preserved")
"""
    result = subprocess.run(  # noqa: S603 - fixed owned script in an isolated interpreter
        [sys.executable, "-c", script],
        env={**os.environ, "PYTHONINTMAXSTRDIGITS": "640"},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert result.stdout.strip() == "numeric profile preserved"


def test_preserves_quoted_text_extra_columns_and_utf8_bom() -> None:
    rows = parse(
        '\ufeff"Footprint","Supplier","Refs","Qty","Value","DNP"\n'
        '"0805","A, ""quoted""","R1","1","1k, ""tight""",""\n'
    )
    assert rows[0].value == '1k, "tight"'
    assert rows[0].extra_fields == (("Supplier", 'A, "quoted"'),)


def test_reorders_rows_and_expands_normal_and_zero_padded_ranges() -> None:
    rows = parse(_HEADERS + '"R3-R4","1k","",2,""\n"R01-R02","1k","",2,""\n')
    assert tuple(row.references for row in rows) == (("R01", "R02"), ("R3", "R4"))


def test_reference_token_order_does_not_change_records() -> None:
    first = parse(_HEADERS + '"R3, R1-R2","1k","",3,""\n')
    reordered = parse(_HEADERS + '"R2,R3,R1","1k","",3,""\n')
    assert first == reordered
    assert first[0].references == ("R1", "R2", "R3")


def test_header_order_does_not_change_extra_fields() -> None:
    first = parse(
        _HEADERS.rstrip("\n") + ',"Supplier","MPN"\n'
        '"R1","1k","0805",1,"","supplier","part-number"\n'
    )
    reordered = parse(
        '"MPN","DNP","Qty","Value","Supplier","Refs","Footprint"\n'
        '"part-number","",1,"1k","supplier","R1","0805"\n'
    )
    assert first == reordered
    assert first[0].extra_fields == (("MPN", "part-number"), ("Supplier", "supplier"))


def test_row_order_does_not_change_records() -> None:
    first = '"R2","1k","",1,""\n'
    second = '"R1","1k","",1,""\n'
    assert parse(_HEADERS + first + second) == parse(_HEADERS + second + first)


def test_known_dashed_reference_is_literal_before_range_interpretation() -> None:
    rows = parse_bom_csv(
        (_HEADERS + '"TP-1","test","",1,""\n').encode(),
        known_references=frozenset({"TP-1"}),
        deadline=time.monotonic() + 10,
    )
    assert rows[0].references == ("TP-1",)


def test_inconsistent_range_looking_text_is_preserved_as_a_literal_reference() -> None:
    rows = parse(_HEADERS + '"R1-R03","test","",1,""\n')
    assert rows[0].references == ("R1-R03",)


@pytest.mark.parametrize(
    "text",
    (
        "",
        '"Refs","refs","Value","Footprint","Qty","DNP"\n',
        '"Refs","Value","Footprint","Qty","DNP",""\n',
        _HEADERS.rstrip("\n") + ',"MPN","MPN"\n',
        _HEADERS + '"R1","1k","",1\n',
        _HEADERS + '"R1","1k","",2,""\n',
        _HEADERS + '"R1","1k","",1,"yes"\n',
        _HEADERS + '"R4-R3","1k","",1,""\n',
        _HEADERS + '"R1","1k","",1,""\n"R1","2k","",1,""\n',
        _HEADERS + '"R1,R1","1k","",2,""\n',
        _HEADERS + '"R1-R2,R2","1k","",3,""\n',
        _HEADERS + '"R1","unterminated,"",1,""\n',
    ),
)
def test_refuses_malformed_input_without_private_context(text: str) -> None:
    with pytest.raises(BomCsvError, match="BOM CSV is malformed") as caught:
        parse(text)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "payload, known_references, deadline",
    (
        (b"private-bom-marker\xff", frozenset(), 1.0),
        (b'"private-bom-marker"x\n', frozenset(), 1.0),
        (b"", frozenset(), 1.0),
        ((_HEADERS + '"R1","private-bom-marker"x,"",1,""\n').encode(), frozenset(), 1.0),
        (_HEADERS.encode(), frozenset(), 10**10_000),
        (_HEADERS.encode(), frozenset({"private-bom-marker\ud800"}), 1.0),
    ),
    ids=("invalid-utf8", "quoted-header", "missing-header", "quoted-row", "deadline", "surrogate"),
)
def test_private_failures_have_no_exception_chain(
    payload: bytes,
    known_references: frozenset[str],
    deadline: float,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("copper_mcp.engineering.bom_csv.time.monotonic", lambda: 0.0)
    with pytest.raises(BomCsvError, match=r"^BOM CSV is malformed$") as caught:
        parse_bom_csv(payload, known_references=known_references, deadline=deadline)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert "private-bom-marker" not in str(caught.value)
    assert "private-bom-marker" not in repr(caught.value)
    assert "private-bom-marker" not in "".join(traceback.format_exception(caught.value))


def test_known_reference_length_is_checked_before_utf8_encoding() -> None:
    # Encoding would fail on the surrogate; the cheap character ceiling must win first.
    with pytest.raises(BomCsvError, match=r"^BOM CSV exceeds limits$") as caught:
        parse_bom_csv(
            _HEADERS.encode(),
            known_references=frozenset({"R" * 4096 + "\ud800"}),
            deadline=time.monotonic() + 10,
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize("value", ("x" * 4096, "é" * 2048), ids=("ascii", "utf8"))
def test_accepts_text_at_the_utf8_byte_ceiling(value: str) -> None:
    assert parse(_HEADERS + f'"R1","{value}","",1,""\n')[0].value == value


@pytest.mark.parametrize("value", ("x" * 4097, "é" * 2049), ids=("ascii", "utf8"))
def test_refuses_text_above_the_utf8_byte_ceiling(value: str) -> None:
    with pytest.raises(BomCsvError, match=r"^BOM CSV exceeds limits$") as caught:
        parse(_HEADERS + f'"R1","{value}","",1,""\n')
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_range_ceiling_is_checked_before_expansion() -> None:
    with pytest.raises(BomCsvError, match="BOM CSV exceeds limits") as caught:
        parse(_HEADERS + '"R1-R100001","1k","",100000,""\n')
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "payload, max_bytes, known_references",
    (
        (b"x" * 9, 8, frozenset()),
        (_HEADERS.encode(), 8 * 1024 * 1024 + 1, frozenset()),
        (b"\xff", 8 * 1024 * 1024, frozenset()),
        (_HEADERS.encode(), 8 * 1024 * 1024, frozenset({"R\t1"})),
    ),
)
def test_refuses_encoding_and_boundary_limits(
    payload: bytes, max_bytes: int, known_references: frozenset[str]
) -> None:
    with pytest.raises(BomCsvError) as caught:
        parse_bom_csv(
            payload,
            known_references=known_references,
            deadline=time.monotonic() + 10,
            max_bytes=max_bytes,
        )
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_deadline_is_checked_before_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("copper_mcp.engineering.bom_csv.time.monotonic", lambda: 1.0)
    with pytest.raises(BomCsvError, match="BOM CSV deadline expired") as caught:
        parse_bom_csv(_HEADERS.encode(), known_references=frozenset(), deadline=1.0)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_deadline_expiring_during_row_sort_refuses_return(monkeypatch: pytest.MonkeyPatch) -> None:
    from copper_mcp.engineering import bom_csv

    expired = False

    def delayed_sorted(
        values: Iterable[Any], *, key: Callable[[Any], Any] | None = None
    ) -> list[Any]:
        nonlocal expired
        result = sorted(values, key=key)
        if result and isinstance(result[0], BomCsvRow):
            expired = True
        return result

    monkeypatch.setattr(bom_csv, "sorted", delayed_sorted, raising=False)
    monkeypatch.setattr("copper_mcp.engineering.bom_csv.time.monotonic", lambda: float(expired))
    with pytest.raises(BomCsvError, match=r"^BOM CSV deadline expired$") as caught:
        parse_bom_csv(
            (_HEADERS + '"R2","1k","",1,""\n"R1","1k","",1,""\n').encode(),
            known_references=frozenset(),
            deadline=1.0,
        )
    assert expired
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_row_repr_is_redacted() -> None:
    row = parse(_HEADERS + '"R1","private","","1",""\n')[0]
    assert repr(row) == "<BomCsvRow redacted>"
