from __future__ import annotations

import dataclasses
import sys
import time

import pytest
from pin_census_fixtures import _basic_inputs
from pin_net_fixtures import _census, _pin

from copper_mcp.adapters.sexpr import QuotedAtom
from copper_mcp.engineering import component_netlist as component_xml
from copper_mcp.engineering import native_pin_net_map as native_map
from copper_mcp.engineering.native_pin_net_map import (
    NativePinNetMapError,
    parse_native_pin_net_map,
)
from copper_mcp.engineering.project_pin_census import ProjectPinCensus, derive_project_pin_census

SOURCE = "nested/board.kicad_sch"


SHEET = "/"


def _component(reference: str = "U1") -> str:
    symbol_uuid = "a00000000001" if reference != "R2" else "a00000000002"
    return (
        f'<comp ref="{reference}"><value>Part</value><libsource lib="Test" part="Part"/>'
        '<sheetpath names="/" tstamps="/"/>'
        f"<tstamps>20000000-0000-4000-8000-{symbol_uuid}</tstamps></comp>"
    )


def _node(number: str = "1", *, pin_type: str = "passive", function: str | None = "A_1") -> str:
    function_attribute = "" if function is None else f' pinfunction="{function}"'
    return f'<node ref="U1" pin="{number}" pintype="{pin_type}"{function_attribute}/>'


def _net(code: str, name: str, nodes: str | None = None) -> str:
    native_nodes = _node() if nodes is None else nodes
    return f'<net code="{code}" name="{name}" class="Default">{native_nodes}</net>'


def _payload(*, components: str | None = None, nets: str | None = None) -> bytes:
    components = _component() if components is None else components
    nets = _net("1", "/SIG") if nets is None else nets
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><export version="E"><design><source>{SOURCE}'
        '</source><tool>Eeschema 10.0.5</tool><sheet number="1" name="/" tstamps="/"/>'
        f"</design><components>{components}</components><nets>{nets}</nets></export>"
    ).encode()


def _parse(payload: bytes | None = None, census: ProjectPinCensus | None = None):
    data = _payload() if payload is None else payload
    return parse_native_pin_net_map(
        data,
        census=_census(_pin("U1", "1", "a")) if census is None else census,
        expected_source=SOURCE,
        expected_sheet_paths=(SHEET,),
        deadline=time.monotonic() + 30,
        max_bytes=len(data),
    )


def test_parses_format_e_and_preserves_explicit_no_connect() -> None:
    result = _parse(_payload(nets=_net("1", "", _node(pin_type="passive+no_connect"))))
    node = result.nodes[0]
    assert (node.net_code, node.net_name, node.electrical_type, node.no_connect) == (
        "1",
        "",
        "passive",
        True,
    )
    assert node.pinfunction == "A_1"


def test_same_net_aliases_are_retained_and_cross_net_reuse_refuses() -> None:
    first, second = _pin("U1", "1", "a"), _pin("U1", "1", "b")
    census = _census(first, second)
    result = _parse(census=census)
    assert len(result.nodes[0].aliases) == 2
    duplicated = _payload(nets=_net("1", "A") + _net("2", "B"))
    with pytest.raises(NativePinNetMapError):
        _parse(duplicated, census)


def test_alias_metadata_mismatch_refuses_before_native_node_reconciliation() -> None:
    first, second = _pin("U1", "1", "a"), _pin("U1", "1", "b", name="B")
    with pytest.raises(NativePinNetMapError) as error:
        _parse(census=_census(first, second))
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize(
    "payload,census",
    [
        (_payload(nets=""), None),
        (_payload(nets=_net("1", "A", _node() + _node())), None),
        (_payload(components=_component() + _component("U2")), None),
        (_payload(), _census(_pin("U1", "1", "a", name="B"))),
    ],
)
def test_refuses_missing_duplicate_extra_and_metadata_mismatch(payload, census) -> None:
    with pytest.raises(NativePinNetMapError):
        _parse(payload, census)


def test_virtual_pins_are_counted_but_not_required_as_native_components() -> None:
    result = _parse(census=_census(_pin("U1", "1", "a"), _pin("#PWR01", "1", "b")))
    assert result.virtual_pin_count == 1 and len(result.nodes) == 1


def test_digest_reordering_and_redaction_are_stable() -> None:
    census = _census(_pin("U1", "1", "a"), _pin("U1", "1", "b"), _pin("U1", "2", "c"))
    result = _parse(
        _payload(nets=_net("1", "A") + _net("2", "B", _node("2", function="A_2"))), census
    )
    reordered = dataclasses.replace(result, nodes=tuple(reversed(result.nodes)))
    tampered = dataclasses.replace(result.nodes[0], net_name="TAMPERED")
    assert (
        result.digest == reordered.digest
        and result.digest != dataclasses.replace(result, nodes=(tampered, *result.nodes[1:])).digest
    )
    assert repr(result) == "<NativePinNetMap redacted>"
    assert "U1" not in repr(result.nodes[0]) and "U1" not in repr(result.nodes[0].aliases[0])


def test_rejects_duplicate_alias_identity_and_bounds_and_deadline(monkeypatch) -> None:
    pin = _pin("U1", "1", "a")
    with pytest.raises(NativePinNetMapError):
        _parse(census=_census(pin, pin))
    with pytest.raises(NativePinNetMapError):
        _parse(_payload(nets=_net("2147483648", "A")))
    from copper_mcp.engineering import native_pin_net_map as module

    monkeypatch.setattr(module.time, "monotonic", lambda: 2.0)
    with pytest.raises(NativePinNetMapError, match="deadline expired"):
        parse_native_pin_net_map(
            _payload(),
            census=_census(pin),
            expected_source=SOURCE,
            expected_sheet_paths=(SHEET,),
            deadline=1.0,
            max_bytes=len(_payload()),
        )


def test_positive_net_code_refuses_before_global_integer_conversion() -> None:
    from copper_mcp.engineering import native_pin_net_map as module

    previous = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(NativePinNetMapError):
            module._positive_code("9" * 4096)
    finally:
        sys.set_int_max_str_digits(previous)


def test_rejects_node_text_without_retaining_xml_context() -> None:
    payload = _payload(nets=_net("1", "A", _node().replace("/>", ">private</node>")))
    with pytest.raises(NativePinNetMapError) as error:
        _parse(payload)
    assert error.value.__cause__ is None and error.value.__context__ is None


@pytest.mark.parametrize(
    "nets",
    (
        _net("1", "A", _node().replace("/>", ' extra="x"/>')),
        _net("1", "A").replace('class="Default"', 'class="Default" extra="x"'),
        "private" + _net("1", "A"),
        _net("1", "A", _node().replace('ref="U1"', 'ref="U2"')),
    ),
)
def test_refuses_unknown_attributes_text_and_extra_keys(nets: str) -> None:
    with pytest.raises(NativePinNetMapError):
        _parse(_payload(nets=nets))


def test_xml_directives_source_binding_and_input_bounds_refuse() -> None:
    payload = _payload()
    cases = (
        b"",
        payload + b"late input",
        payload.replace(b'<export version="E">', b'<!DOCTYPE export><export version="E">'),
    )
    for malformed in cases:
        with pytest.raises(NativePinNetMapError) as error:
            _parse(malformed)
        assert error.value.__cause__ is None and error.value.__context__ is None
    with pytest.raises(NativePinNetMapError):
        parse_native_pin_net_map(
            payload,
            census=_census(_pin("U1", "1", "a")),
            expected_source="other.kicad_sch",
            expected_sheet_paths=(SHEET,),
            deadline=time.monotonic() + 10,
            max_bytes=len(payload),
        )
    with pytest.raises(NativePinNetMapError):
        parse_native_pin_net_map(
            payload,
            census=_census(_pin("U1", "1", "a")),
            expected_source=SOURCE,
            expected_sheet_paths=(SHEET,),
            deadline=time.monotonic() + 10,
            max_bytes=len(payload) - 1,
        )


def test_malformed_census_records_refuse_without_raw_exception() -> None:
    for pins in ([], (None,), (dataclasses.replace(_pin("U1", "1", "a"), source_pin_uuid=[]),)):
        census = _census(_pin("U1", "1", "a"))
        object.__setattr__(census, "pins", pins)
        with pytest.raises(NativePinNetMapError) as error:
            _parse(census=census)
        assert error.value.__cause__ is None and error.value.__context__ is None


def test_canonical_foreign_symbol_and_pin_symbol_metadata_mismatch_refuse() -> None:
    pin = _pin("U1", "1", "a")
    foreign = dataclasses.replace(pin, source_symbol_uuid="20000000-0000-4000-8000-000000000099")
    wrong_unit = dataclasses.replace(pin, selected_unit=2)
    for tampered in (foreign, wrong_unit):
        census = _census(pin)
        object.__setattr__(census, "pins", (tampered,))
        with pytest.raises(NativePinNetMapError) as error:
            _parse(census=census)
        assert error.value.__cause__ is None and error.value.__context__ is None


def test_actual_derived_census_quoted_atoms_are_normalized_only_at_source_boundary(
    tmp_path,
) -> None:
    capture, libraries, _ = _basic_inputs(tmp_path)
    census = derive_project_pin_census(capture, libraries, deadline=time.monotonic() + 10)
    components = _component("R1").replace(
        'lib="Test" part="Part"', 'lib="Test" part="R"'
    ) + _component("R2").replace('lib="Test" part="Part"', 'lib="Test" part="R"')
    nets = "".join(
        f'<net code="{index}" name="N{index}" class="Default">'
        f'<node ref="{pin.effective_reference}" pin="{pin.pin_number}" '
        f'pintype="{pin.effective_electrical_type}"/></net>'
        for index, pin in enumerate(census.pins, start=1)
    )
    result = _parse(_payload(components=components, nets=nets), census)
    assert len(result.nodes) == 4 and {node.library_id for node in result.nodes} == {"Test:R"}


def test_digest_expires_while_streaming_second_alias(monkeypatch) -> None:
    census = _census(_pin("U1", "1", "a"), _pin("U1", "1", "b"))
    result = _parse(census=census)
    ticks = iter((0.0,) * 12 + (1.0,))
    from copper_mcp.engineering import native_pin_net_map as module

    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 1.0))
    with pytest.raises(NativePinNetMapError, match="deadline expired"):
        result._digest(0.5)


def test_digest_checks_deadline_during_sort(monkeypatch) -> None:
    result = _parse()
    ticks = iter((0.0, 1.0))
    from copper_mcp.engineering import native_pin_net_map as module

    monkeypatch.setattr(module.time, "monotonic", lambda: next(ticks, 1.0))
    with pytest.raises(NativePinNetMapError, match="deadline expired"):
        result._digest(0.5)


@pytest.mark.parametrize("phase", ("after_nets", "inside_component_hash"))
def test_late_component_digest_expiry_uses_context_free_native_error(monkeypatch, phase) -> None:
    clock = [0.0]
    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    if phase == "after_nets":
        validate_nets = native_map._validate_nets

        def expire_after_nets(*args):
            nodes = validate_nets(*args)
            clock[0] = 100.0
            return nodes

        monkeypatch.setattr(native_map, "_validate_nets", expire_after_nets)
    else:
        asdict = component_xml.asdict

        def expire_inside_hash(record):
            result = asdict(record)
            clock[0] = 100.0
            return result

        monkeypatch.setattr(component_xml, "asdict", expire_inside_hash)
    with pytest.raises(NativePinNetMapError, match="deadline expired") as error:
        _parse()
    assert type(error.value) is NativePinNetMapError
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize(
    "empty", ('<net code="2" name="EMPTY" class="Default"/>', _net("2", "EMPTY", ""))
)
def test_empty_extra_net_cannot_disappear_from_valid_evidence(empty) -> None:
    baseline = _parse()
    with pytest.raises(NativePinNetMapError):
        observed = _parse(_payload(nets=_net("1", "/SIG") + empty))
        assert observed != baseline and observed.digest != baseline.digest


def test_empty_nets_are_valid_only_without_physical_pins() -> None:
    empty = _parse(_payload(components="", nets=""), _census())
    assert empty.nodes == () and empty.virtual_pin_count == 0
    virtual = _parse(_payload(components="", nets=""), _census(_pin("#PWR01", "1", "a")))
    assert virtual.nodes == () and virtual.virtual_pin_count == 1
    with pytest.raises(NativePinNetMapError):
        _parse(_payload(nets=""))


def test_admitted_census_flags_alternate_and_bindings_remain_digest_bound() -> None:
    census = _census(_pin("U1", "1", "a"))
    baseline = _parse(census=census)
    changed = dataclasses.replace(
        census,
        capture_digest="sha256:" + "3" * 64,
        symbol_context_digest="sha256:" + "4" * 64,
        symbols=(dataclasses.replace(census.symbols[0], in_bom=False, on_board=False, dnp=True),),
        pins=(
            dataclasses.replace(census.pins[0], hidden=True, selected_alternate=QuotedAtom("A")),
        ),
    )
    observation = _parse(census=changed)
    assert observation.nodes == baseline.nodes
    assert observation.census_digest == changed.digest != baseline.census_digest
    assert observation.digest != baseline.digest


@pytest.mark.parametrize(
    ("subordinate_message", "reason"),
    (
        (component_xml._DEADLINE, "deadline expired"),
        (component_xml._BOUNDS, "exceeds its bounds"),
        ("private subordinate diagnostic", "is malformed"),
    ),
)
def test_component_hash_refusal_retains_only_fixed_reason(monkeypatch, subordinate_message, reason):
    def refused(*_args):
        raise component_xml.ComponentNetlistError(subordinate_message)

    monkeypatch.setattr(component_xml.ComponentNetlist, "_digest", refused)
    with pytest.raises(NativePinNetMapError, match=reason) as error:
        _parse()
    assert "private" not in str(error.value)
    assert error.value.__context__ is error.value.__cause__ is None


@pytest.mark.parametrize("name", ("SourcePinAlias", "NativePinNetMapError"))
def test_source_record_and_error_are_direct_reexports(name) -> None:
    from copper_mcp.engineering import _pin_net_source

    assert getattr(native_map, name) is getattr(_pin_net_source, name)
