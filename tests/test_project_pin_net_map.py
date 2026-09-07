"""Owned real pin/net controls and fail-closed execution-boundary tests."""

from __future__ import annotations

import dataclasses
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from pin_census_fixtures import (
    PIN_UUIDS,
    ROOT_UUID,
    SYMBOL_1_UUID,
    SYMBOL_2_UUID,
    _capture,
    _library,
    _path,
    _pin,
    _placed,
    _raw_pin,
    _source,
)
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering import project_pin_net_map as maps

_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")


def _alias_project(tmp_path, *, different_labels=False, no_connect=False):
    def body(name):
        first = _pin("1", name="A").replace("(at 0 0 0)", "(at -5 0 0)")
        second = _pin("2", name="B").replace("(at 0 0 0)", "(at -5 0 0)")
        return (
            f'(symbol "{name}" (symbol "M_0_1" {_pin("3", name="COMMON")})'
            f'(symbol "M_1_1" {first})(symbol "M_2_1" {second}))'
        )

    symbols, labels = [], []
    for index, symbol_uuid in enumerate((SYMBOL_1_UUID, SYMBOL_2_UUID)):
        unit, x = str(index + 1), 20 + index * 20
        raw = tuple(_raw_pin(str(pin + 1), PIN_UUIDS[index * 3 + pin]) for pin in range(3))
        symbol = _placed(
            symbol_uuid,
            "U1",
            raw,
            library_id="Test:M",
            global_unit=unit,
            paths=(_path(f"/{ROOT_UUID}", "U1", unit),),
            body_style="1",
        ).replace(f"(unit {unit})", f"(at {x} 20 0) (unit {unit})", 1)
        symbols.append(symbol)
        label = ("LEFT" if index == 0 else "RIGHT") if different_labels else "SHARED"
        labels.append(
            f'(label "{label}" (at {x} 20 0) (effects (font (size 1.27 1.27)))'
            f' (uuid "40000000-0000-4000-8000-{index + 1:012d}"))'
        )
    if no_connect:
        labels.append('(no_connect (at 15 20) (uuid "50000000-0000-4000-8000-000000000001"))')
    return _capture(tmp_path, _source(body("Test:M"), *symbols, *labels), _library(body("M")))


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_native_rc_map_repeats_without_source_mutation_or_authority(tmp_path):
    capture, libraries, sources = build_project(tmp_path)
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=45)
    first = maps.run_project_pin_net_map(capture, libraries, settings)
    second = maps.run_project_pin_net_map(capture, libraries, settings)
    assert first == second and first.digest == second.digest
    assert len(first.mapping.nodes) == 4
    document = first.document()
    assert document["source_pin_occurrence_count"] == 4
    assert document["model_validation"] == document["engineering_validation"] == "not_run"
    assert document["apply_authority"] == "none"
    assert "AUDIO_IN" not in json.dumps(document) and "R1" not in repr(first)
    assert {name: (tmp_path / name).read_bytes() for name in sources} == sources


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
@pytest.mark.parametrize("no_connect", (False, True))
def test_native_shared_aliases_and_explicit_no_connect_are_preserved(tmp_path, no_connect):
    capture, libraries = _alias_project(tmp_path, no_connect=no_connect)
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=45)
    result = maps.run_project_pin_net_map(capture, libraries, settings)
    by_pin = {node.pin_number: node for node in result.mapping.nodes}
    assert set(by_pin) == {"1", "2", "3"}
    assert by_pin["3"].net_name == "/SHARED" and len(by_pin["3"].aliases) == 2
    assert {alias.source_symbol_uuid for alias in by_pin["3"].aliases} == {
        SYMBOL_1_UUID,
        SYMBOL_2_UUID,
    }
    assert by_pin["1"].no_connect is no_connect
    assert by_pin["2"].no_connect is False  # A single-pin net is not an explicit NC decision.
    assert result.document()["source_pin_occurrence_count"] == 4


@pytest.mark.real_kicad
@pytest.mark.skipif(not _CLI, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_native_conflicting_shared_pin_nets_cannot_produce_a_map(tmp_path):
    capture, libraries = _alias_project(tmp_path, different_labels=True)
    settings = Settings(workspace=tmp_path, kicad_cli=Path(_CLI), kicad_timeout_seconds=45)
    with pytest.raises(maps.ProjectPinNetMapError) as error:
        maps.run_project_pin_net_map(capture, libraries, settings)
    assert error.value.__context__ is None
    assert "LEFT" not in str(error.value) and "RIGHT" not in str(error.value)


@pytest.mark.parametrize("value", (0, True, float("inf"), "45"))
def test_invalid_limits_refuse_before_native_execution(tmp_path, monkeypatch, value):
    capture, libraries, _ = build_project(tmp_path)
    settings = dataclasses.replace(Settings(workspace=tmp_path), kicad_timeout_seconds=value)
    monkeypatch.setattr(maps, "_execute", lambda *_: pytest.fail("must refuse before execution"))
    with pytest.raises(maps.ProjectPinNetMapError) as error:
        maps.run_project_pin_net_map(capture, libraries, settings)
    assert error.value.__context__ is None


@pytest.mark.parametrize("deadline", (True, float("nan"), float("inf"), "later", 10**1000))
def test_invalid_deadline_is_context_free(tmp_path, monkeypatch, deadline):
    capture, libraries, _ = build_project(tmp_path)
    monkeypatch.setattr(maps, "_execute", lambda *_: pytest.fail("must refuse before execution"))
    with pytest.raises(maps.ProjectPinNetMapError) as error:
        maps.run_project_pin_net_map(
            capture, libraries, Settings(workspace=tmp_path), deadline=deadline
        )
    assert error.value.__context__ is None


def test_source_change_during_final_hash_refuses_delivery(tmp_path, monkeypatch):
    capture, libraries, sources = build_project(tmp_path)

    def changed_hash(_deadline):
        (tmp_path / capture.root_path).write_bytes(b"owned synthetic changed source")
        return "sha256:" + "0" * 64

    monkeypatch.setattr(maps, "_execute", lambda *_: SimpleNamespace(_digest=changed_hash))
    with pytest.raises(maps.ProjectPinNetMapError) as error:
        maps.run_project_pin_net_map(capture, libraries, Settings(workspace=tmp_path))
    assert error.value.__context__ is None
    assert (tmp_path / capture.root_path).read_bytes() != sources[capture.root_path]
