"""Native differential controls over owned synthetic sources, not physics evidence."""

from __future__ import annotations

import dataclasses
import os
import subprocess
import time
from collections import Counter
from pathlib import Path

import pytest
from pin_census_fixtures import (
    PIN_UUIDS,
    ROOT_UUID,
    SYMBOL_1_UUID,
    _capture,
    _library,
    _multi_body,
    _path,
    _placed,
    _raw_pin,
    _source,
)
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering import component_netlist as xml
from copper_mcp.engineering import kicad_project_execution as execution
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.project_erc_inputs import prepare_project_erc
from copper_mcp.engineering.project_pin_census import PlacedPinOccurrence, derive_project_pin_census
from copper_mcp.security import read_workspace_file

_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


def _assert_exact_agreement(pins, expected, library_rows, node_rows):
    assert Counter((pin.effective_reference, pin.pin_number) for pin in pins) == Counter(expected)
    assert len({key for key, _ in library_rows}) == len(library_rows)
    assert Counter(key for key, _ in node_rows) == Counter(expected)
    library = dict(library_rows)
    nodes = dict(node_rows)
    for pin in pins:
        assert library[(pin.library_id, pin.pin_number)] == (
            pin.declared_name,
            pin.declared_electrical_type,
        )
        # Pinned KiCad netlist_exporter_xml.cpp:1310: shown name + '_' + pin number;
        # empty names omit pinfunction for these non-stacked synthetic controls.
        function = f"{pin.effective_name}_{pin.pin_number}" if pin.effective_name else None
        assert nodes[(pin.effective_reference, pin.pin_number)] == (
            pin.effective_electrical_type,
            function,
        )


@pytest.mark.parametrize(
    "fault", ("duplicate-census", "duplicate-library", "duplicate-node", "wrong-name")
)
def test_native_comparison_rejects_lossy_occurrence_or_alternate_evidence(fault):
    pin = PlacedPinOccurrence(
        f"/{ROOT_UUID}",
        SYMBOL_1_UUID,
        PIN_UUIDS[0],
        "U1",
        "Test:M",
        1,
        1,
        "1",
        "A",
        "A",
        "ALT",
        "passive",
        "output",
        False,
        "ALT",
    )
    pins = [pin]
    expected = {("U1", "1")}
    library = [(("Test:M", "1"), ("A", "passive"))]
    nodes = [(("U1", "1"), ("output", "ALT_1"))]
    _assert_exact_agreement(pins, expected, library, nodes)
    if fault == "duplicate-census":
        pins.append(pin)
    elif fault == "duplicate-library":
        library.extend(library)
    elif fault == "duplicate-node":
        nodes.extend(nodes)
    else:
        pins[0] = dataclasses.replace(pin, effective_name="A")
    with pytest.raises(AssertionError):
        _assert_exact_agreement(pins, expected, library, nodes)


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
@pytest.mark.parametrize("case", ("rc", "unit-override", "style-2", "alternate"))
def test_captured_pin_occurrences_agree_with_fixed_native_exports(tmp_path, case):
    if case == "rc":
        capture, libraries, sources = build_project(tmp_path)
        expected = {("C1", "1"), ("C1", "2"), ("R1", "1"), ("R1", "2")}
    else:
        numbers = ("C", "3", "4") if case == "style-2" else ("C", "1", "2")
        raw = tuple(
            _raw_pin(
                number,
                pin_uuid,
                alternate="ALT" if case == "alternate" and number == "1" else None,
            )
            for number, pin_uuid in zip(numbers, PIN_UUIDS, strict=False)
        )
        selected_unit = "2" if case == "unit-override" else "1"
        style = "2" if case == "style-2" else "1"
        symbol = _placed(
            SYMBOL_1_UUID,
            "U1",
            raw,
            library_id="Test:M",
            paths=(_path(f"/{ROOT_UUID}", "U1", selected_unit),),
            global_unit="1",
            body_style=style,
        )
        capture, libraries = _capture(
            tmp_path,
            _source(_multi_body("Test:M"), symbol),
            _library(_multi_body("M")),
        )
        sources = {item.path: item.content for item in capture._files}
        selected_pin = "2" if case == "unit-override" else "3" if case == "style-2" else "1"
        expected = {("U1", "C"), ("U1", selected_pin)}

    deadline = time.monotonic() + 45
    limits = CaptureLimits(max_capture_seconds=30)
    census = derive_project_pin_census(capture, libraries, limits=limits, deadline=deadline)
    assert Counter((pin.effective_reference, pin.pin_number) for pin in census.pins) == Counter(
        expected
    )
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=Path(_CLI),
        kicad_timeout_seconds=45,
        max_drc_report_bytes=1024 * 1024,
    )
    prepared = prepare_project_erc(capture, libraries, limits=limits, deadline=deadline)
    source_files = tuple(sources.items())
    execution._verify_workspace_source(tmp_path, source_files, deadline)
    observations = []
    with execution.open_project_execution_context(prepared, settings, deadline) as native:
        for index in range(2):
            output = native.temporary / f"pin-census-{index}.xml"
            diagnostics = native.temporary / f"pin-census-{index}.log"
            with diagnostics.open("wb") as stream:
                code = execution._invoke(
                    [
                        *native.base_command,
                        str(settings.max_drc_report_bytes),
                        str(native.executable),
                        "sch",
                        "export",
                        "netlist",
                        "--format",
                        "kicadxml",
                        "--output",
                        str(output),
                        str(native.snapshot / prepared.root_path),
                    ],
                    settings=settings,
                    environment=native.environment,
                    deadline=deadline,
                    stdout=stream,
                    stderr=subprocess.STDOUT,
                )
            native.verify()
            diagnostic = read_workspace_file(
                native.temporary,
                diagnostics.name,
                allowed_suffixes={".log"},
                max_bytes=64 * 1024,
            ).content
            assert code == 0 and diagnostic == b""
            payload = read_workspace_file(
                native.temporary,
                output.name,
                allowed_suffixes={".xml"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            xml._reject_xml_directives(payload, deadline)
            root = xml._parse_xml(payload, deadline)
            native_pins = [
                (
                    (part.attrib["lib"] + ":" + part.attrib["part"], pin.attrib["num"]),
                    (pin.attrib["name"], pin.attrib["type"]),
                )
                for part in root.findall("./libparts/libpart")
                for pin in part.findall("./pins/pin")
            ]
            nodes = [
                (
                    (node.attrib["ref"], node.attrib["pin"]),
                    (node.attrib["pintype"], node.attrib.get("pinfunction")),
                )
                for node in root.findall("./nets/net/node")
            ]
            _assert_exact_agreement(census.pins, expected, native_pins, nodes)
            observations.append((native_pins, nodes))
    assert observations[0] == observations[1]
    execution._verify_workspace_source(tmp_path, source_files, deadline)
    assert {name: (tmp_path / name).read_bytes() for name in sources} == sources
    assert census.document()["native_validation"] == "not_run"
    assert census.document()["apply_authority"] == "none"
