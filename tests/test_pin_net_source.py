"""Direct source admission controls; no pin-net XML parser or executor imports."""

from __future__ import annotations

import dataclasses
import time

import pytest
from pin_net_fixtures import _census, _pin

from copper_mcp.adapters.sexpr import QuotedAtom
from copper_mcp.engineering import _pin_net_source as source
from copper_mcp.engineering import component_netlist as component_xml
from copper_mcp.engineering import project_pin_census as census_module
from copper_mcp.engineering._pin_net_source import NativePinNetMapError, SourcePinAlias
from copper_mcp.engineering.project_pin_census import (
    PlacedPinOccurrence,
    PlacedSymbolOccurrence,
    ProjectPinCensus,
)


@pytest.mark.parametrize("quoted", (False, True))
def test_oversized_source_text_refuses_before_conversion_or_scan(monkeypatch, quoted) -> None:
    value = "x" * (component_xml._MAX_VALUE_BYTES + 1)
    if quoted:
        value = QuotedAtom(value)

    def forbidden(*_args, **_kwargs):
        pytest.fail("oversized source text reached conversion or scanning")

    monkeypatch.setattr(QuotedAtom, "__str__", forbidden)
    monkeypatch.setattr(component_xml, "_bounded_text", forbidden)
    with pytest.raises(NativePinNetMapError, match="exceeds its bounds") as error:
        source._source_text(value)
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize(
    ("owner", "field"),
    [
        (owner, field.name)
        for owner, record in (
            ("census", ProjectPinCensus),
            ("symbol", PlacedSymbolOccurrence),
            ("pin", PlacedPinOccurrence),
        )
        for field in dataclasses.fields(record)
    ],
)
def test_every_hashed_census_field_is_admitted_before_asdict(monkeypatch, owner, field) -> None:
    census = _census(_pin("U1", "1", "a"))
    record = (
        census if owner == "census" else census.symbols[0] if owner == "symbol" else census.pins[0]
    )
    object.__setattr__(record, field, [False] * 8192)

    def forbidden(_record):
        pytest.fail("unadmitted census field reached asdict")

    monkeypatch.setattr(census_module, "asdict", forbidden)
    with pytest.raises(NativePinNetMapError) as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize("field", ("capture_digest", "symbol_context_digest"))
@pytest.mark.parametrize("value", ("fake", "sha256:" + "G" * 64, "sha256:" + "1" * 65))
def test_fake_census_digests_refuse_before_hash(monkeypatch, field, value) -> None:
    census = _census(_pin("U1", "1", "a"))
    object.__setattr__(census, field, value)
    monkeypatch.setattr(census_module, "asdict", lambda _: pytest.fail("fake digest reached hash"))
    with pytest.raises(NativePinNetMapError) as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize(
    ("owner", "field"),
    (
        ("symbol", "sheet_uuid_path"),
        ("symbol", "source_symbol_uuid"),
        ("symbol", "effective_reference"),
        ("symbol", "library_id"),
        ("pin", "sheet_uuid_path"),
        ("pin", "source_symbol_uuid"),
        ("pin", "source_pin_uuid"),
        ("pin", "effective_reference"),
        ("pin", "library_id"),
        ("pin", "pin_number"),
        ("pin", "raw_declared_name"),
        ("pin", "declared_name"),
        ("pin", "effective_name"),
        ("pin", "declared_electrical_type"),
        ("pin", "effective_electrical_type"),
        ("pin", "selected_alternate"),
    ),
)
def test_oversized_census_fields_keep_bounds_reason_before_hash(monkeypatch, owner, field) -> None:
    census = _census(_pin("U1", "1", "a"))
    record = census.symbols[0] if owner == "symbol" else census.pins[0]
    object.__setattr__(record, field, QuotedAtom("x" * (component_xml._MAX_VALUE_BYTES + 1)))
    monkeypatch.setattr(
        census_module, "asdict", lambda _: pytest.fail("oversized field reached hash")
    )
    with pytest.raises(NativePinNetMapError, match="exceeds its bounds") as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


def test_source_text_preserves_byte_bounds_and_xml_exact_string_admission() -> None:
    limit = component_xml._MAX_VALUE_BYTES
    assert source._source_text("x" * limit) == "x" * limit
    assert type(source._source_text(QuotedAtom("A"))) is str
    with pytest.raises(NativePinNetMapError, match="exceeds its bounds") as error:
        source._source_text(QuotedAtom("한" * (limit // 3 + 1)))
    assert error.value.__context__ is error.value.__cause__ is None
    with pytest.raises(component_xml.ComponentNetlistError):
        component_xml._bounded_text(QuotedAtom("A"))


@pytest.mark.parametrize("field", ("capture_digest", "symbol_context_digest"))
def test_oversized_digest_string_refuses_before_census_hash(monkeypatch, field) -> None:
    census = _census(_pin("U1", "1", "a"))
    object.__setattr__(census, field, "x" * (component_xml._MAX_VALUE_BYTES + 1))
    monkeypatch.setattr(ProjectPinCensus, "_digest", lambda *_: pytest.fail("must not hash"))
    with pytest.raises(NativePinNetMapError, match="exceeds its bounds") as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize("field", ("in_bom", "on_board", "dnp", "hidden"))
@pytest.mark.parametrize("value", (0, 1, None, "false"))
def test_census_flags_require_exact_booleans(monkeypatch, field, value) -> None:
    census = _census(_pin("U1", "1", "a"))
    record = census.pins[0] if field == "hidden" else census.symbols[0]
    object.__setattr__(record, field, value)
    monkeypatch.setattr(ProjectPinCensus, "_digest", lambda *_: pytest.fail("must not hash"))
    with pytest.raises(NativePinNetMapError) as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


def test_expired_deadline_precedes_census_text_admission(monkeypatch) -> None:
    census = _census(_pin("U1", "1", "a"))
    object.__setattr__(census.pins[0], "effective_name", "x" * 8192)
    monkeypatch.setattr(time, "monotonic", lambda: 100.0)
    monkeypatch.setattr(source, "_source_text", lambda *_: pytest.fail("expired admission"))
    with pytest.raises(NativePinNetMapError, match="deadline expired") as error:
        source._validate_census(census, 99.0)
    assert error.value.__context__ is error.value.__cause__ is None


def test_source_admission_preserves_complete_alias_group_and_virtual_count() -> None:
    first = _pin("U1", "1", "a")
    second = dataclasses.replace(_pin("U1", "1", "b"), selected_unit=2)
    virtual = _pin("#PWR01", "1", "c")
    census = _census(second, virtual, first)

    groups, components, virtual_count, digest = source._validate_census(
        census, time.monotonic() + 30
    )

    assert set(groups) == {("U1", "1")}
    group = groups[("U1", "1")]
    assert group.aliases == (
        SourcePinAlias(first.sheet_uuid_path, first.source_symbol_uuid, first.source_pin_uuid),
        SourcePinAlias(second.sheet_uuid_path, second.source_symbol_uuid, second.source_pin_uuid),
    )
    assert (group.library_id, group.effective_name, group.electrical_type) == (
        "Test:Part",
        "A",
        "passive",
    )
    assert components == {("U1", "Test:Part")} and virtual_count == 1
    assert digest == census.digest
    assert all(repr(alias) == "<SourcePinAlias redacted>" for alias in group.aliases)


@pytest.mark.parametrize("reference", ("U1", "#PWR01"))
def test_duplicate_pin_number_within_one_occurrence_refuses_before_hash(monkeypatch, reference):
    first = _pin(reference, "1", "a")
    second = dataclasses.replace(
        _pin(reference, "1", "b"), source_symbol_uuid=first.source_symbol_uuid
    )
    assert first.source_pin_uuid != second.source_pin_uuid
    census = _census(first, second)
    monkeypatch.setattr(ProjectPinCensus, "_digest", lambda *_: pytest.fail("must not hash"))
    with pytest.raises(NativePinNetMapError) as error:
        source._validate_census(census, time.monotonic() + 30)
    assert error.value.__context__ is error.value.__cause__ is None


def test_different_pin_numbers_in_one_occurrence_remain_supported():
    first = _pin("U1", "1", "a")
    second = dataclasses.replace(_pin("U1", "2", "b"), source_symbol_uuid=first.source_symbol_uuid)
    groups, _, _, _ = source._validate_census(_census(first, second), time.monotonic() + 30)
    assert set(groups) == {("U1", "1"), ("U1", "2")}


def test_source_working_record_representations_do_not_expose_private_fields():
    group = source._SourcePinGroup((), "PrivateLibrary:PrivatePart", "PrivateSignal", "passive")
    context = source._SourceSymbolContext("PrivateReference", "PrivateLibrary:PrivatePart", 1, 1)
    assert repr(group) == "<SourcePinGroup redacted>"
    assert repr(context) == "<SourceSymbolContext redacted>"
