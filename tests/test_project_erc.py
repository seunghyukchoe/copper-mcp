"""Real hierarchical ERC evidence, using only CopperMCP-authored synthetic symbols."""

import dataclasses
import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from copper_mcp.adapters.kicad_schematic import _library_symbol, render_kicad_schematic
from copper_mcp.adapters.sexpr import atoms, child, parse_sexpr
from copper_mcp.circuit_ir import ComponentKind, decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.engineering.erc_profile import NATIVE_PIN_MAP, OUTSIDE_CONNECTIVITY_SCOPE
from copper_mcp.engineering.project_erc import ProjectErcError, run_project_erc
from copper_mcp.engineering.project_erc_inputs import SymbolLibraryInput
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)

ROOT = Path(__file__).resolve().parents[1]
_CONFIGURED_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
CLI = Path(_CONFIGURED_CLI) if _CONFIGURED_CLI else None


def _sha(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def build_project(
    tmp_path,
    *,
    duplicate_names=False,
    variable_reference=False,
    library_name="CopperMCP",
    nested=False,
    project_settings=None,
):
    intent = decode_snapshot_json(
        (ROOT / "benchmarks/audio/fixtures/rc-low-pass-intent-v1.json").read_bytes()
    )
    rendered = render_kicad_schematic(intent).content
    root_uuid = atoms(child(parse_sexpr(rendered), "uuid"))[0]
    name = intent.content.project_name
    child_source = b"""(kicad_sch (version 20250114) (generator "copper_mcp_test")
      (uuid "10000000-0000-4000-8000-000000000001") (paper "A4") (lib_symbols)
      (sheet_instances (path "/" (page "1"))))"""
    sheets = []
    for index in (2, 3):
        sheet_name = "Child" if duplicate_names else f"Child{index}"
        reference = "${CHILD}" if variable_reference else "child.kicad_sch"
        sheets.append(f'''(sheet (at 150 {index * 20}) (size 25 15)
          (stroke (width 0.1524) (type default)) (fill (color 0 0 0 0))
          (uuid "20000000-0000-4000-8000-{index:012d}")
          (property "Sheetname" "{sheet_name}" (at 150 {index * 20 - 1} 0)
            (effects (font (size 1.27 1.27)) (justify left bottom)))
          (property "Sheetfile" "{reference}" (at 150 {index * 20 + 16} 0)
            (effects (font (size 1.27 1.27)) (justify left top)))
          (instances (project "{name}" (path "/{root_uuid}" (page "{index}")))))''')
    source = rendered.rstrip()[:-1] + "\n".join(sheets).encode() + b")\n"
    source = source.replace(b"CopperMCP:", (library_name + ":").encode())
    # Fixtures define their own library, rather than passing cached user symbols as authority.
    library_symbols = []
    for kind, symbol_name in (
        (ComponentKind.RESISTOR, "R"),
        (ComponentKind.CAPACITOR_UNPOLARIZED, "C"),
    ):
        library_symbols.append(
            "\n".join(_library_symbol(kind)).replace(
                f'"CopperMCP:{symbol_name}"', f'"{symbol_name}"', 1
            )
        )
    library = (
        '(kicad_symbol_lib (version 20241209) (generator "copper_mcp_test")'
        + "\n".join(library_symbols)
        + ")"
    ).encode()
    settings_document = dict(project_settings or {})
    if variable_reference:
        settings_document["text_variables"] = {"CHILD": "child.kicad_sch"}
    context = {
        f"{name}.kicad_sch": source,
        "child.kicad_sch": child_source,
        f"{name}.kicad_pro": json.dumps(settings_document).encode(),
    }
    prefix = "nested/" if nested else ""
    context = {prefix + path: data for path, data in context.items()}
    for path, data in context.items():
        (tmp_path / path).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / path).write_bytes(data)
    capture = capture_schematic_project(
        tmp_path,
        prefix + f"{name}.kicad_sch",
        tuple(ProjectFileBinding(path, _sha(data)) for path, data in context.items()),
    )
    return capture, (SymbolLibraryInput(library_name, library, _sha(library)),), context


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend")
def test_real_project_variables_unicode_library_and_repeat_digest(tmp_path):
    capture, libraries, _ = build_project(
        tmp_path, variable_reference=True, library_name="사용자", nested=True
    )
    settings = Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30)
    first = run_project_erc(capture, libraries, settings)
    second = run_project_erc(capture, libraries, settings)
    assert first.status == second.status == "pass"
    assert first.digest == second.digest


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend")
def test_real_native_unconnected_pin_matrix_value_produces_a_hard_finding(tmp_path):
    matrix = [list(row) for row in NATIVE_PIN_MAP]
    # KiCad 10.0.5's native table uses index 4 for passive pins. Value 3 is an
    # accepted UNCONNECTED conflict and must not disappear from the execution derivative.
    matrix[4][4] = 3
    capture, libraries, context = build_project(
        tmp_path, project_settings={"erc": {"pin_map": matrix}}
    )
    settings = Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30)
    report = run_project_erc(capture, libraries, settings)
    assert report.status == "fail"
    assert len(report.samples) == 2 and report.samples[0] == report.samples[1]
    assert report.samples[0].error_count > 0
    assert dict(report.samples[0].violation_type_counts)["pin_to_pin"] > 0
    assert {name: (tmp_path / name).read_bytes() for name in context} == context


@pytest.mark.parametrize("value", (0, True, float("inf"), "30"))
def test_invalid_operator_bounds_refuse_before_execution(tmp_path, monkeypatch, value):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    settings = dataclasses.replace(Settings(workspace=tmp_path), kicad_timeout_seconds=value)
    monkeypatch.setattr(
        project_erc, "_execute", lambda *_args: pytest.fail("must refuse before execution")
    )
    with pytest.raises(ProjectErcError):
        run_project_erc(capture, libraries, settings)


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend")
@pytest.mark.parametrize("duplicate_names", (False, True))
def test_real_hierarchical_project_erc_is_bound_and_preserves_source(tmp_path, duplicate_names):
    capture, libraries, context = build_project(tmp_path, duplicate_names=duplicate_names)
    before = {name: (data, (tmp_path / name).stat().st_mtime_ns) for name, data in context.items()}
    settings = Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30)
    report = run_project_erc(capture, libraries, settings)
    assert report.status == ("fail" if duplicate_names else "pass")
    assert len(report.samples) == 2 and report.samples[0] == report.samples[1]
    assert report.samples[0].sheet_count == 3
    assert set(report.samples[0].ignored_check_keys) == OUTSIDE_CONNECTIVITY_SCOPE
    if duplicate_names:
        assert dict(report.samples[0].violation_type_counts)["duplicate_sheet_names"] == 1
    else:
        assert report.samples[0].error_count == 0
        assert "lib_symbol_issues" not in dict(report.samples[0].violation_type_counts)
    assert report.capture_digest == capture.digest
    document = report.document()
    assert document["apply_authority"] == "none" and document["simulation_validation"] == "not_run"
    assert "root_path" not in document and "CopperMCP:R" not in json.dumps(document)
    assert {
        name: ((tmp_path / name).read_bytes(), (tmp_path / name).stat().st_mtime_ns)
        for name in before
    } == before


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend")
def test_real_disabled_hard_rule_cannot_hide_a_known_bad_project(tmp_path):
    capture, libraries, context = build_project(
        tmp_path,
        duplicate_names=True,
        project_settings={"erc": {"rule_severities": {"duplicate_sheet_names": "ignore"}}},
    )
    report = run_project_erc(
        capture, libraries, Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30)
    )
    assert report.status == "fail"
    assert "duplicate_sheet_names:ignore->error" in report.rule_changes
    assert all((tmp_path / name).read_bytes() == content for name, content in context.items())


def test_disagreeing_symbol_library_refuses_before_native_execution(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    original = libraries[0]
    changed = original.content.replace(b"(pin passive", b"(pin input")
    assert changed != original.content
    library = dataclasses.replace(original, content=changed, digest=_sha(changed))
    monkeypatch.setattr(
        project_erc, "_execute", lambda *_args: pytest.fail("mismatched library must not execute")
    )
    with pytest.raises(ProjectErcError):
        run_project_erc(
            capture,
            (library,),
            Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30),
        )


def test_unavailable_or_tampered_inputs_cannot_produce_a_report(tmp_path):
    capture, libraries, _ = build_project(tmp_path)
    settings = Settings(workspace=tmp_path, kicad_cli=tmp_path / "missing")
    for changed, deadline in (
        (capture, time.monotonic() - 1),
        (dataclasses.replace(capture, digest="sha256:" + "0" * 64), None),
    ):
        with pytest.raises(ProjectErcError) as caught:
            run_project_erc(changed, libraries, settings, deadline=deadline)
        assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 test backend")
@pytest.mark.parametrize(
    "extra", (b'(unsupported_native_token "x")', b"(wire (pts (xy nope 0) (xy 1 1)))")
)
def test_native_child_load_errors_cannot_become_clean_project_reports(tmp_path, extra):
    capture, libraries, context = build_project(tmp_path)
    context["child.kicad_sch"] = context["child.kicad_sch"].rstrip()[:-1] + extra + b")"
    (tmp_path / "child.kicad_sch").write_bytes(context["child.kicad_sch"])
    capture = capture_schematic_project(
        tmp_path,
        capture.root_path,
        tuple(ProjectFileBinding(path, _sha(data)) for path, data in context.items()),
    )
    with pytest.raises(ProjectErcError):
        run_project_erc(
            capture,
            libraries,
            Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=60),
        )
    assert all((tmp_path / path).read_bytes() == data for path, data in context.items())
