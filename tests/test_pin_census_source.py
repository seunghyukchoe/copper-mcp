"""Direct tests for the bounded pin-census captured-source interpreter."""

from __future__ import annotations

import time

import pytest
from pin_census_fixtures import (
    PIN_UUIDS,
    ROOT_UUID,
    SYMBOL_1_UUID,
    SYMBOL_2_UUID,
    _multi_body,
    _path,
    _placed,
    _raw_pin,
    _resistor_body,
    _source,
)

from copper_mcp.engineering import _pin_census_source as source
from copper_mcp.engineering._pin_census_source import ProjectPinCensusError
from copper_mcp.engineering.capture import CaptureLimits


def _interpret(
    payload: bytes, budget: source._Budget | None = None
) -> tuple[source._PlacedTemplate, ...]:
    active_budget = source._Budget() if budget is None else budget
    return source._parse_source(
        payload,
        CaptureLimits(),
        active_budget,
        time.monotonic() + 5,
    )


def test_common_style_hidden_and_alternate_records_are_interpreted_directly() -> None:
    style_one = _placed(
        SYMBOL_1_UUID,
        "U1",
        (
            _raw_pin("C", PIN_UUIDS[0]),
            _raw_pin("1", PIN_UUIDS[1], alternate="ALT"),
            _raw_pin("2", PIN_UUIDS[2]),
        ),
        library_id="Test:M",
        body_style="1",
    )
    style_two = _placed(
        SYMBOL_2_UUID,
        "U2",
        (
            _raw_pin("C", PIN_UUIDS[3]),
            _raw_pin("3", PIN_UUIDS[4]),
            _raw_pin("4", PIN_UUIDS[5]),
        ),
        library_id="Test:M",
        body_style="2",
    )

    first, second = _interpret(_source(_multi_body("Test:M"), style_one, style_two))

    assert first.body is second.body
    assert (first.body_style, second.body_style) == (1, 2)
    common = next(pin for pin in first.body.pins if pin.number == "C")
    alternate_pin = next(pin for pin in first.body.pins if pin.number == "1")
    assert (common.unit, common.body_style, common.hidden) == (0, 0, True)
    assert [(item.name, item.electrical_type) for item in alternate_pin.alternates] == [
        ("ALT", "output")
    ]
    assert next(pin for pin in first.raw_pins if pin.number == "1").selected_alternate == "ALT"


@pytest.mark.parametrize("kind", ("cache", "symbol-uuid", "raw-number"))
def test_duplicate_source_identities_refuse_directly(kind: str) -> None:
    cache = _resistor_body("Test:R")
    first = _placed(
        SYMBOL_1_UUID,
        "R1",
        (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
    )
    if kind == "cache":
        payload = _source(cache + cache, first)
    elif kind == "symbol-uuid":
        second = _placed(
            SYMBOL_1_UUID,
            "R2",
            (_raw_pin("1", PIN_UUIDS[2]), _raw_pin("2", PIN_UUIDS[3])),
        )
        payload = _source(cache, first, second)
    else:
        duplicate = _placed(
            SYMBOL_1_UUID,
            "R1",
            (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("1", PIN_UUIDS[1])),
        )
        payload = _source(cache, duplicate)

    with pytest.raises(ProjectPinCensusError, match="ambiguous"):
        _interpret(payload)


@pytest.mark.parametrize("kind", ("missing", "extra"))
def test_raw_pin_inventory_mismatch_refuses_directly(kind: str) -> None:
    raw = [_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])]
    if kind == "missing":
        raw.pop()
    else:
        raw.append(_raw_pin("3", PIN_UUIDS[2]))
    placed = _placed(SYMBOL_1_UUID, "R1", tuple(raw))

    with pytest.raises(ProjectPinCensusError, match="pin inventory"):
        _interpret(_source(_resistor_body("Test:R"), placed))


@pytest.mark.parametrize(
    "payload",
    (
        b"(",
        b"(not_kicad_sch (version 20250114) (lib_symbols))",
        _source(
            _resistor_body("Test:R"),
            _placed(
                "ABCDEF00-0000-4000-8000-000000000001",
                "R1",
                (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
            ),
        ),
        _source(
            _resistor_body("Test:R"),
            _placed(
                SYMBOL_1_UUID,
                "R1",
                (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
                paths=(_path(f"/{ROOT_UUID}", "R1", "01"),),
            ),
        ),
    ),
    ids=("syntax", "header", "uuid", "integer"),
)
def test_malformed_source_refuses_directly(payload: bytes) -> None:
    with pytest.raises(ProjectPinCensusError):
        _interpret(payload)


def test_budget_is_cumulative_when_one_interpreter_budget_is_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    placed = _placed(
        SYMBOL_1_UUID,
        "R1",
        (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1])),
    )
    payload = _source(_resistor_body("Test:R"), placed)
    budget = source._Budget()
    _interpret(payload, budget)
    consumed = budget.work
    monkeypatch.setattr(source, "_MAX_AGGREGATE_WORK", consumed)

    with pytest.raises(ProjectPinCensusError, match="aggregate work"):
        _interpret(payload, budget)
    assert budget.work == consumed + 1


@pytest.mark.parametrize(
    ("name", "message"),
    (
        ("_MAX_LIBRARY_PINS_PER_BODY", "library pin traversal"),
        ("_MAX_SAVED_PIN_RECORDS", "saved pin record"),
        ("_MAX_FIELDS", "field budget"),
        ("_MAX_AGGREGATE_WORK", "aggregate work"),
    ),
)
def test_source_interpreter_enforces_each_owned_work_ceiling(monkeypatch, name, message):
    placed = _placed(
        SYMBOL_1_UUID, "R1", (_raw_pin("1", PIN_UUIDS[0]), _raw_pin("2", PIN_UUIDS[1]))
    )
    monkeypatch.setattr(source, name, 1)
    with pytest.raises(ProjectPinCensusError, match=message):
        _interpret(_source(_resistor_body("Test:R"), placed))
