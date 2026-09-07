from __future__ import annotations

import dataclasses
import time
import xml.etree.ElementTree as ET

import pytest

from copper_mcp.engineering.component_netlist import (
    ComponentNetlist,
    ComponentNetlistError,
    NativeComponent,
    parse_component_netlist,
)
from copper_mcp.optimization.contracts import digest_document

CHILD_UUID = "20000000-0000-4000-8000-000000000002"
R1_UUID = "1a000000-0000-4000-8000-000000000001"
U1_A_UUID = "30000000-0000-4000-8000-000000000001"
U1_B_UUID = "30000000-0000-4000-8000-000000000002"
SOURCE = "nested/회로.kicad_sch"
SHEETS = ("/", f"/{CHILD_UUID}/")


def _component(
    reference: str = "R1",
    *,
    value: str = "10k &amp; 1%",
    library: str = "Device",
    part: str = "R",
    sheet_path: str = "/",
    symbol_uuids: str = R1_UUID,
    fields: str = (
        '<field name="Footprint">Resistor_SMD:R_0603</field>'
        '<field name="Datasheet">https://example.invalid/r?a=1&amp;b=2</field>'
        '<field name="Vendor">Acme</field>'
    ),
    direct_fields: str = (
        "<footprint>Resistor_SMD:R_0603</footprint>"
        "<datasheet>https://example.invalid/r?a=1&amp;b=2</datasheet>"
    ),
    properties: str = (
        '<property name="Sheetname" value="Root"/>'
        '<property name="Sheetfile" value="회로.kicad_sch"/>'
    ),
) -> str:
    return f"""
    <comp ref="{reference}">
      <value>{value}</value>
      {direct_fields}
      <fields>{fields}</fields>
      <libsource lib="{library}" part="{part}" description="synthetic"/>
      {properties}
      <sheetpath names="/Synthetic/" tstamps="{sheet_path}"/>
      <tstamps>{symbol_uuids}</tstamps>
      <units><unit name="A"><pins><pin num="1"/></pins></unit></units>
    </comp>
    """


def _payload(*, components: str | None = None, design: str | None = None) -> bytes:
    if components is None:
        components = _component() + _component(
            "U1",
            value="MCU",
            part="MCU_2Unit",
            sheet_path=f"/{CHILD_UUID}/",
            symbol_uuids=f"{U1_B_UUID} {U1_A_UUID}",
            fields='<field name="Footprint"/><field name="Datasheet"/>',
            direct_fields="",
            properties=(
                '<property name="exclude_from_bom"/>'
                '<property name="exclude_from_board"/>'
                '<property name="dnp"/>'
            ),
        )
    if design is None:
        design = f"""
        <design>
          <source>{SOURCE}</source>
          <date>2026-09-07T00:00:00</date>
          <tool>Eeschema 10.0.5</tool>
          <sheet number="2" name="/Child/" tstamps="/{CHILD_UUID}/"/>
          <sheet number="1" name="/" tstamps="/"/>
        </design>
        """
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<export version="E">{design}<components>{components}</components>'
        '<libparts/><libraries/><nets/><future><opaque key="value"/></future></export>'
    ).encode()


def _parse(payload: bytes | None = None) -> ComponentNetlist:
    data = payload if payload is not None else _payload()
    return parse_component_netlist(
        data,
        expected_source=SOURCE,
        expected_sheet_paths=tuple(reversed(SHEETS)),
        deadline=time.monotonic() + 60.0,
        max_bytes=len(data),
    )


@pytest.mark.parametrize("empty", (False, True))
def test_deadline_hash_preserves_v1_canonical_bytes(empty):
    parsed = _parse()
    record = dataclasses.replace(parsed.components[0], value='한글 Ω "quoted" \\')
    observed = dataclasses.replace(
        parsed,
        components=() if empty else (parsed.components[1], record),
        sheet_paths=tuple(reversed(parsed.sheet_paths)),
    )
    expected = digest_document(
        "copper-mcp/component-netlist/v1",
        {
            "backend_version": observed.backend_version,
            "components": [
                dataclasses.asdict(item)
                for item in sorted(observed.components, key=lambda item: item.reference)
            ],
            "sheet_paths": sorted(observed.sheet_paths),
        },
    )
    assert observed.digest == observed._digest(time.monotonic() + 60) == expected


def test_deadline_hash_stops_during_sort_key_extraction(monkeypatch):
    from copper_mcp.engineering import component_netlist

    observed = _parse()
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(component_netlist.time, "monotonic", lambda: next(ticks, 1.0))
    monkeypatch.setattr(component_netlist, "asdict", lambda _: pytest.fail("must not convert"))
    with pytest.raises(ComponentNetlistError, match="deadline expired"):
        observed._digest(0.5)


def test_parses_sorted_native_component_metadata_without_claiming_authority() -> None:
    result = _parse()

    assert result.backend_version == "10.0.5"
    assert result.sheet_paths == SHEETS
    assert result.components == (
        NativeComponent(
            reference="R1",
            value="10k & 1%",
            footprint="Resistor_SMD:R_0603",
            datasheet="https://example.invalid/r?a=1&b=2",
            library_id="Device:R",
            sheet_path="/",
            symbol_uuids=(R1_UUID,),
            excluded_from_bom=False,
            excluded_from_board=False,
            dnp=False,
        ),
        NativeComponent(
            reference="U1",
            value="MCU",
            footprint="",
            datasheet="",
            library_id="Device:MCU_2Unit",
            sheet_path=f"/{CHILD_UUID}/",
            symbol_uuids=(U1_A_UUID, U1_B_UUID),
            excluded_from_bom=True,
            excluded_from_board=True,
            dnp=True,
        ),
    )


def test_digest_is_deterministic_over_sorted_records_and_sheet_paths() -> None:
    result = _parse()
    reordered = ComponentNetlist(
        components=tuple(reversed(result.components)),
        sheet_paths=tuple(reversed(result.sheet_paths)),
        backend_version=result.backend_version,
    )

    assert result.digest == reordered.digest
    assert result.digest.startswith("sha256:") and len(result.digest) == 71


def test_empty_native_inventory_is_valid_without_manufacturing_a_component() -> None:
    result = _parse(_payload(components=""))

    assert result.components == ()
    assert result.sheet_paths == SHEETS


def _tail_across_feed_boundary(tail: bytes) -> bytes:
    from copper_mcp.engineering import component_netlist as module

    original = _payload()
    marker = original.index(b"</future>")
    prefix, suffix = original[:marker], original[marker:]
    padding = module._PARSE_CHUNK_BYTES - len(prefix) - len(b"<edge/>")
    assert padding > 0
    aligned = prefix + b"<x/>" * (padding // 4) + b" " * (padding % 4) + b"<edge/>"
    assert len(aligned) == module._PARSE_CHUNK_BYTES
    return aligned + tail + suffix


def test_rejects_nonwhitespace_tail_added_by_a_later_feed() -> None:
    with pytest.raises(ComponentNetlistError):
        _parse(_tail_across_feed_boundary(b"untrusted tail"))


def test_rejects_oversize_tail_added_by_a_later_feed() -> None:
    from copper_mcp.engineering import component_netlist as module

    with pytest.raises(ComponentNetlistError):
        _parse(_tail_across_feed_boundary(b" " * (module._MAX_VALUE_BYTES + 1)))


def test_final_value_budget_includes_later_tail_text(monkeypatch) -> None:
    from copper_mcp.engineering import component_netlist as module

    payload = _tail_across_feed_boundary(b" " * 1000)
    document = ET.fromstring(payload)  # noqa: S314 - trusted synthetic byte-count oracle
    total = sum(
        len(value.encode("utf-8"))
        for element in document.iter()
        for value in (*element.attrib.keys(), *element.attrib.values(), element.text, element.tail)
        if value is not None
    )
    monkeypatch.setattr(module, "_MAX_TOTAL_VALUE_BYTES", total)
    assert _parse(payload).components
    monkeypatch.setattr(module, "_MAX_TOTAL_VALUE_BYTES", total - 1)
    with pytest.raises(ComponentNetlistError):
        _parse(payload)


def test_expiry_during_completed_tail_check_refuses(monkeypatch) -> None:
    from copper_mcp.engineering import component_netlist as module

    payload = _tail_across_feed_boundary(b" " * 1000)
    clock = [0.0]
    value_bytes = module._element_value_bytes

    def expire_on_late_tail(value):
        if value == " " * 1000:
            clock[0] = 2.0
        return value_bytes(value)

    monkeypatch.setattr(module.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(module, "_element_value_bytes", expire_on_late_tail)
    with pytest.raises(ComponentNetlistError, match="deadline expired"):
        parse_component_netlist(
            payload,
            expected_source=SOURCE,
            expected_sheet_paths=SHEETS,
            deadline=1.0,
            max_bytes=len(payload),
        )


def test_results_are_frozen_and_repr_redacted() -> None:
    result = _parse()

    assert repr(result) == "<ComponentNetlist redacted>"
    assert all(repr(component) == "<NativeComponent redacted>" for component in result.components)
    assert SOURCE not in repr(result) and "R1" not in repr(result.components[0])
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.components = ()  # type: ignore[misc]


@pytest.mark.parametrize(
    "directive",
    (
        '<!DOCTYPE export [<!ENTITY private "secret">]>',
        '<!DOCTYPE export SYSTEM "file:///private/project.kicad_sch">',
        '<?xml-stylesheet href="file:///private/project.xsl"?>',
    ),
)
def test_rejects_entities_dtds_and_extra_processing_instructions(directive: str) -> None:
    payload = _payload().replace(
        b'<export version="E">', directive.encode() + b'<export version="E">'
    )

    with pytest.raises(ComponentNetlistError) as caught:
        _parse(payload)

    assert "secret" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize(
    "payload",
    (
        b"not xml",
        b'<?xml version="1.0" encoding="UTF-8"?><export version="E">',
        b'<?xml version="1.0" encoding="UTF-8"?><export version="E">\xff</export>',
    ),
)
def test_rejects_malformed_or_non_utf8_xml_without_exception_context(payload: bytes) -> None:
    with pytest.raises(ComponentNetlistError) as caught:
        _parse(payload)

    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize(
    "components",
    (
        _component() + _component(),
        _component().replace(
            '<libsource lib="Device" part="R" description="synthetic"/>',
            '<libsource lib="Device" part="R"/><libsource lib="Other" part="R"/>',
        ),
        _component().replace(
            '<sheetpath names="/Synthetic/" tstamps="/"/>',
            '<sheetpath names="/" tstamps="/"/><sheetpath names="/" tstamps="/"/>',
        ),
        _component().replace(
            f"<tstamps>{R1_UUID}</tstamps>",
            f"<tstamps>{R1_UUID}</tstamps><tstamps>{U1_A_UUID}</tstamps>",
        ),
        _component().replace("<value>10k &amp; 1%</value>", "<value>10k</value><value>11k</value>"),
    ),
)
def test_rejects_duplicate_component_nodes_and_identities(components: str) -> None:
    with pytest.raises(ComponentNetlistError):
        _parse(_payload(components=components))


def test_binds_direct_footprint_and_datasheet_to_unescaped_named_fields() -> None:
    result = _parse()

    assert result.components[0].footprint == "Resistor_SMD:R_0603"
    assert result.components[0].datasheet == "https://example.invalid/r?a=1&b=2"


@pytest.mark.parametrize(
    "component",
    (
        _component().replace(
            "<footprint>Resistor_SMD:R_0603</footprint>",
            "<footprint>Resistor_SMD:R_0603</footprint><footprint>Other</footprint>",
        ),
        _component().replace(
            "<datasheet>https://example.invalid/r?a=1&amp;b=2</datasheet>",
            "<datasheet>https://example.invalid/r?a=1&amp;b=2</datasheet>"
            "<datasheet>Other</datasheet>",
        ),
        _component().replace(
            '<field name="Footprint">Resistor_SMD:R_0603</field>',
            '<field name="Footprint">Resistor_SMD:R_0805</field>',
        ),
        _component().replace(
            '<field name="Datasheet">https://example.invalid/r?a=1&amp;b=2</field>',
            '<field name="Datasheet">https://example.invalid/other</field>',
        ),
        _component().replace("<footprint>Resistor_SMD:R_0603</footprint>", ""),
        _component().replace('<field name="Footprint">Resistor_SMD:R_0603</field>', ""),
        _component().replace("<datasheet>https://example.invalid/r?a=1&amp;b=2</datasheet>", ""),
        _component().replace(
            '<field name="Datasheet">https://example.invalid/r?a=1&amp;b=2</field>', ""
        ),
        _component().replace("<fields>", "<fields/><fields>", 1),
    ),
    ids=(
        "duplicate-footprint",
        "duplicate-datasheet",
        "conflicting-footprint",
        "conflicting-datasheet",
        "missing-direct-footprint",
        "missing-field-footprint",
        "missing-direct-datasheet",
        "missing-field-datasheet",
        "duplicate-fields-container",
    ),
)
def test_rejects_ambiguous_direct_and_named_optional_field_representations(
    component: str,
) -> None:
    with pytest.raises(ComponentNetlistError):
        _parse(_payload(components=component))


@pytest.mark.parametrize(
    "component",
    (
        _component(fields="", direct_fields="").replace("<fields></fields>", "", 1),
        _component(fields="", direct_fields=""),
        _component(fields='<field name="Footprint"/>', direct_fields=""),
        _component(fields='<field name="Datasheet"/>', direct_fields=""),
        _component(
            fields='<field name="Footprint"/><field name="Datasheet"/>',
            direct_fields="<footprint/><datasheet/>",
        ),
    ),
    ids=(
        "absent-container",
        "empty-container",
        "missing-datasheet-entry",
        "missing-footprint-entry",
        "empty-direct-elements",
    ),
)
def test_absent_or_empty_optional_representations_remain_empty(component: str) -> None:
    result = _parse(_payload(components=component))

    assert result.components[0].footprint == ""
    assert result.components[0].datasheet == ""


def test_rejects_duplicate_design_metadata_and_sections() -> None:
    payload = _payload()
    source = f"<source>{SOURCE}</source>".encode()
    tool = b"<tool>Eeschema 10.0.5</tool>"
    cases = (
        payload.replace(source, source + source, 1),
        payload.replace(tool, tool + tool, 1),
        payload.replace(b"<components>", b"<components/><components>", 1),
    )

    for candidate in cases:
        with pytest.raises(ComponentNetlistError):
            _parse(candidate)


@pytest.mark.parametrize(
    ("old", "new"),
    (
        ('version="E"', 'version="D"'),
        (f"<source>{SOURCE}</source>", "<source>other.kicad_sch</source>"),
        ("Eeschema 10.0.5", "Eeschema 10.0.6"),
        ('ref="R1"', 'ref="#PWR0101"'),
        (R1_UUID, R1_UUID.upper()),
        (R1_UUID, "not-a-uuid"),
    ),
)
def test_rejects_export_source_tool_reference_and_uuid_identity_drift(old: str, new: str) -> None:
    payload = _payload()
    assert payload.count(old.encode()) >= 1

    with pytest.raises(ComponentNetlistError):
        _parse(payload.replace(old.encode(), new.encode(), 1))


def test_requires_exact_unique_sheet_coverage_and_component_membership() -> None:
    payload = _payload()
    cases = (
        (payload, ("/",)),
        (payload, ("/", "/")),
        (
            payload.replace(
                f'tstamps="/{CHILD_UUID}/"'.encode(),
                b'tstamps="/40000000-0000-4000-8000-000000000004/"',
                1,
            ),
            SHEETS,
        ),
    )
    for candidate, expected in cases:
        with pytest.raises(ComponentNetlistError):
            parse_component_netlist(
                candidate,
                expected_source=SOURCE,
                expected_sheet_paths=expected,
                deadline=time.monotonic() + 60.0,
                max_bytes=len(candidate),
            )


def test_allows_shared_symbol_uuid_across_different_sheet_instances() -> None:
    components = _component() + _component("C1", sheet_path=f"/{CHILD_UUID}/", symbol_uuids=R1_UUID)

    result = _parse(_payload(components=components))

    assert [(item.reference, item.sheet_path, item.symbol_uuids) for item in result.components] == [
        ("C1", f"/{CHILD_UUID}/", (R1_UUID,)),
        ("R1", "/", (R1_UUID,)),
    ]


def test_requires_unique_symbol_uuids_within_component_and_same_sheet() -> None:
    duplicate_within = _component(symbol_uuids=f"{R1_UUID} {R1_UUID}")
    duplicate_across = _component() + _component("C1", symbol_uuids=R1_UUID)

    for components in (duplicate_within, duplicate_across):
        with pytest.raises(ComponentNetlistError):
            _parse(_payload(components=components))


def test_valued_custom_properties_and_fields_cannot_spoof_native_flags() -> None:
    properties = (
        '<property name="exclude_from_bom" value="yes"/>'
        '<property name="exclude_from_board" value="true"/>'
        '<property name="dnp" value="1"/>'
    )
    fields = (
        '<field name="Footprint"/>'
        '<field name="Datasheet"/>'
        '<field name="exclude_from_bom">yes</field>'
        '<field name="exclude_from_board">true</field>'
        '<field name="dnp">1</field>'
    )
    component = _component(properties=properties, fields=fields, direct_fields="")

    result = _parse(_payload(components=component))

    assert not result.components[0].excluded_from_bom
    assert not result.components[0].excluded_from_board
    assert not result.components[0].dnp


def test_rejects_unknown_or_duplicate_valueless_flag_properties() -> None:
    for properties in (
        '<property name="future_flag"/>',
        '<property name="dnp"/><property name="dnp"/>',
        '<property name="exclude_from_pos_files"/><property name="exclude_from_pos_files"/>',
    ):
        with pytest.raises(ComponentNetlistError):
            _parse(_payload(components=_component(properties=properties)))


def test_known_position_exclusion_flag_is_accepted_but_not_interpreted_as_bom_state() -> None:
    component = _component(properties='<property name="exclude_from_pos_files"/>')

    result = _parse(_payload(components=component))

    assert not result.components[0].excluded_from_bom
    assert not result.components[0].excluded_from_board
    assert not result.components[0].dnp


def test_library_segments_with_colons_cannot_collapse_to_the_same_library_id() -> None:
    components = _component(library="a:b", part="c") + _component(
        "C1", library="a", part="b:c", symbol_uuids=U1_A_UUID
    )

    with pytest.raises(ComponentNetlistError):
        _parse(_payload(components=components))


def test_enforces_byte_and_field_bounds() -> None:
    payload = _payload()
    with pytest.raises(ComponentNetlistError):
        parse_component_netlist(
            payload,
            expected_source=SOURCE,
            expected_sheet_paths=SHEETS,
            deadline=time.monotonic() + 60.0,
            max_bytes=len(payload) - 1,
        )

    oversized = _payload(components=_component(value="x" * 4097))
    with pytest.raises(ComponentNetlistError):
        _parse(oversized)


def test_enforces_xml_depth_attribute_and_component_field_bounds() -> None:
    payload = _payload()
    future = b'<future><opaque key="value"/></future>'
    deep = ("<future>" * 32 + "</future>" * 32).encode()
    attributes = " ".join(f'a{index}="x"' for index in range(17))
    over_attributed = f"<future {attributes}/>".encode()
    fields = '<field name="Footprint"/><field name="Datasheet"/>' + "".join(
        f'<field name="Custom{index}">x</field>' for index in range(255)
    )
    cases = (
        payload.replace(future, deep, 1),
        payload.replace(future, over_attributed, 1),
        _payload(components=_component(fields=fields, direct_fields="")),
    )

    for candidate in cases:
        with pytest.raises(ComponentNetlistError):
            _parse(candidate)


@pytest.mark.parametrize(
    ("deadline", "max_bytes", "sheet_paths"),
    (
        (True, 1024, SHEETS),
        (float("nan"), 1024, SHEETS),
        (float("inf"), 1024, SHEETS),
        (10**10_000, 1024, SHEETS),
        (time.monotonic() + 60.0, True, SHEETS),
        (time.monotonic() + 60.0, 1024, ["/"]),
    ),
    ids=("bool-deadline", "nan", "infinity", "overflow", "bool-bytes", "list-sheets"),
)
def test_rejects_malformed_limits_and_identity_inputs_without_context(
    deadline: object, max_bytes: object, sheet_paths: object
) -> None:
    payload = _payload()
    with pytest.raises(ComponentNetlistError) as caught:
        parse_component_netlist(
            payload,
            expected_source=SOURCE,
            expected_sheet_paths=sheet_paths,  # type: ignore[arg-type]
            deadline=deadline,  # type: ignore[arg-type]
            max_bytes=max_bytes,  # type: ignore[arg-type]
        )
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_checks_deadline_before_and_during_parse(monkeypatch: pytest.MonkeyPatch) -> None:
    from copper_mcp.engineering import component_netlist

    payload = _payload(components=_component() + "<future>" + "<n/>" * 5000 + "</future>")
    with pytest.raises(ComponentNetlistError, match="deadline expired"):
        parse_component_netlist(
            payload,
            expected_source=SOURCE,
            expected_sheet_paths=SHEETS,
            deadline=0.0,
            max_bytes=len(payload),
        )

    ticks = iter((0.0, 0.0, 0.0, 1.0))
    monkeypatch.setattr(component_netlist.time, "monotonic", lambda: next(ticks, 1.0))
    with pytest.raises(ComponentNetlistError, match="deadline expired") as caught:
        parse_component_netlist(
            payload,
            expected_source=SOURCE,
            expected_sheet_paths=SHEETS,
            deadline=0.5,
            max_bytes=len(payload),
        )
    assert caught.value.__context__ is None
