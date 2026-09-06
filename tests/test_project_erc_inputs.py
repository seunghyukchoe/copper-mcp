"""Preparation is a private, explicit derivative, never a user-project rewrite."""

import dataclasses
import hashlib
import json
import time
from pathlib import Path

import pytest

from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.erc_profile import NATIVE_PIN_MAP, OUTSIDE_CONNECTIVITY_SCOPE
from copper_mcp.engineering.project_erc_inputs import (
    ProjectErcInputError,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)


def _sha(payload):
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _capture(tmp_path: Path, project=None, *, instance_extra=""):
    source = b"""(kicad_sch (version 20250114) (generator "copper_mcp_test")
      (uuid "10000000-0000-4000-8000-000000000001") (lib_symbols (symbol "Test:R"))
      (symbol (lib_id "Test:R") (uuid "20000000-0000-4000-8000-000000000001")))"""
    source = source.replace(b'(lib_id "Test:R")', b'(lib_id "Test:R")' + instance_extra.encode())
    settings = json.dumps({} if project is None else project).encode()
    (tmp_path / "root.kicad_sch").write_bytes(source)
    (tmp_path / "root.kicad_pro").write_bytes(settings)
    return capture_schematic_project(
        tmp_path,
        "root.kicad_sch",
        (
            ProjectFileBinding("root.kicad_sch", _sha(source)),
            ProjectFileBinding("root.kicad_pro", _sha(settings)),
        ),
    )


def _library(name="Test", symbol="R"):
    data = f'(kicad_symbol_lib (version 20241209) (generator "test") (symbol "{symbol}"))'.encode()
    return SymbolLibraryInput(name, data, _sha(data))


def _prepare(capture, libraries=None, **kwargs):
    return prepare_project_erc(
        capture,
        (_library(),) if libraries is None else libraries,
        limits=kwargs.get("limits", CaptureLimits()),
        deadline=kwargs.get("deadline", time.monotonic() + 5),
    )


def test_strict_derivative_is_explicit_and_source_bytes_are_unchanged(tmp_path):
    project = {
        "erc": {
            "rule_severities": {"pin_not_connected": "ignore"},
            "pin_map": [[0] * 12 for _ in range(12)],
            "erc_exclusions": ["waiver"],
        }
    }
    capture = _capture(tmp_path, project)
    before = (tmp_path / "root.kicad_pro").read_bytes()
    prepared = _prepare(capture)
    executed = json.loads(dict(prepared.files)["root.kicad_pro"])
    assert executed["erc"]["rule_severities"]["pin_not_connected"] == "error"
    assert tuple(map(tuple, executed["erc"]["pin_map"])) == NATIVE_PIN_MAP
    assert executed["erc"]["erc_exclusions"] == []
    assert {
        key for key, value in executed["erc"]["rule_severities"].items() if value == "ignore"
    } == OUTSIDE_CONNECTIVITY_SCOPE
    assert "pin_not_connected:ignore->error" in prepared.rule_changes
    assert prepared.original_exclusion_count == 1
    assert (tmp_path / "root.kicad_pro").read_bytes() == before
    assert capture._files[0].content in (before, (tmp_path / "root.kicad_sch").read_bytes())
    assert "root.kicad" not in repr(prepared)
    assert prepared == _prepare(capture)


@pytest.mark.parametrize(
    "project",
    [
        {"schematic": {"legacy_lib_dir": "/private"}},
        {"schematic": {"page_layout_descr_file": "outside.kicad_wks"}},
        {"schematic": {"top_level_sheets": [["id", "outside.kicad_sch"]]}},
        {"schematic": {"variants": None}},
        {"text_variables": {"COPPER_MCP_ERC_LIBDIR": "/private"}},
        {"erc": {"rule_severities": {"invented": "ignore"}}},
        {"erc": {"rule_severities": {"pin_not_connected": False}}},
        {"erc": {"pin_map": [[False] * 12 for _ in range(12)]}},
        {"erc": {"erc_exclusions": [4]}},
        {"meta": {"version": 2}},
        {"schematic": {"meta": {"version": True}}},
        {"erc": {"meta": {"version": 1}}},
        {"text_variables": {"LABEL": "${CURRENT_DATE}"}},
    ],
)
def test_unsupported_or_ambiguous_settings_refuse(tmp_path, project):
    with pytest.raises(ProjectErcInputError):
        _prepare(_capture(tmp_path, project))


@pytest.mark.parametrize(
    "libraries",
    [
        (),
        (_library("Wrong"),),
        (_library(symbol="Missing"),),
        (_library(), _library()),
        (dataclasses.replace(_library(), digest="sha256:" + "0" * 64),),
    ],
)
def test_library_closure_is_exact_and_content_bound(tmp_path, libraries):
    with pytest.raises(ProjectErcInputError):
        _prepare(_capture(tmp_path), libraries)


def test_reviewed_native_legacy_rules_are_explicitly_recorded_not_silently_flattened(tmp_path):
    rules = {"conflicting_netclasses": "error", "global_label_dangling": "warning"}
    capture = _capture(tmp_path, {"erc": {"rule_severities": rules}})
    prepared = _prepare(capture)
    execution = json.loads(dict(prepared.files)["root.kicad_pro"])
    assert not set(rules) & execution["erc"]["rule_severities"].keys()
    assert all(
        f"{key}:{value}->legacy-key-ignored-by-native" in prepared.rule_changes
        for key, value in rules.items()
    )
    assert json.loads((tmp_path / "root.kicad_pro").read_bytes())["erc"]["rule_severities"] == rules


def test_capture_tampering_and_bounds_refuse(tmp_path):
    capture = _capture(tmp_path)
    with pytest.raises(ProjectErcInputError):
        _prepare(dataclasses.replace(capture, digest="sha256:" + "0" * 64))
    for deadline in (float("nan"), time.monotonic() - 1, True):
        with pytest.raises(ProjectErcInputError):
            _prepare(capture, deadline=deadline)
    with pytest.raises(ProjectErcInputError):
        _prepare(capture, limits=CaptureLimits(max_total_bytes=1))


@pytest.mark.parametrize("file_count", (0, 66))
def test_capture_file_count_refuses_before_binding_expansion(tmp_path, monkeypatch, file_count):
    from copper_mcp.engineering import project_erc_inputs

    capture = _capture(tmp_path)
    forged = dataclasses.replace(capture, _files=(capture._files[0],) * file_count)

    def forbidden(*args, **kwargs):
        pytest.fail("oversized or empty captured-file set reached binding expansion")

    monkeypatch.setattr(project_erc_inputs, "_validate_bindings", forbidden)
    with pytest.raises(ProjectErcInputError, match="captured files"):
        _prepare(forged)


def test_native_unconnected_pin_matrix_value_is_preserved(tmp_path):
    matrix = [list(row) for row in NATIVE_PIN_MAP]
    matrix[0][0] = 3
    capture = _capture(tmp_path, {"erc": {"pin_map": matrix}})
    prepared = _prepare(capture)
    execution = json.loads(dict(prepared.files)["root.kicad_pro"])
    assert execution["erc"]["pin_map"] == matrix
    assert json.loads((tmp_path / "root.kicad_pro").read_bytes())["erc"]["pin_map"] == matrix


def test_oversized_integer_deadline_has_a_fixed_preparation_error(tmp_path):
    with pytest.raises(ProjectErcInputError, match="bounds are malformed") as caught:
        _prepare(_capture(tmp_path), deadline=10**10000)
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None


@pytest.mark.parametrize("override", ('(lib_name "Test:R")', '(lib_name "Actual:R")'))
def test_native_cached_symbol_name_override_cannot_bypass_equivalence(tmp_path, override):
    with pytest.raises(ProjectErcInputError, match="cached-name override"):
        _prepare(_capture(tmp_path, instance_extra=override))


@pytest.mark.parametrize(
    "body",
    (
        '(symbol "R" (extends "missing"))',
        '(symbol "R" (extends "P")) (symbol "P" (extends "R"))',
        '(symbol "R") (embedded_files (file "uncaptured"))',
    ),
)
def test_library_dependencies_must_be_contained_and_acyclic(tmp_path, body):
    payload = f'(kicad_symbol_lib (version 20241209) (generator "test") {body})'.encode()
    with pytest.raises(ProjectErcInputError):
        _prepare(_capture(tmp_path), (SymbolLibraryInput("Test", payload, _sha(payload)),))


def test_contained_inheritance_cannot_claim_cached_body_equivalence(tmp_path):
    payload = b'(kicad_symbol_lib (symbol "R" (extends "P")) (symbol "P"))'
    with pytest.raises(ProjectErcInputError, match="inherited symbol equivalence"):
        _prepare(_capture(tmp_path), (SymbolLibraryInput("Test", payload, _sha(payload)),))


def test_unused_inheritance_checks_the_deadline_before_each_symbol(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc_inputs

    capture = _capture(tmp_path)
    inherited = "".join(f'(symbol "Unused{index}" (extends "Parent"))' for index in range(32))
    payload = f'(kicad_symbol_lib (symbol "R") {inherited} (symbol "Parent"))'.encode()
    libraries = (SymbolLibraryInput("Test", payload, _sha(payload)),)
    assert _prepare(capture, libraries).expected_uuid_paths

    clock = [100.0]
    parse = project_erc_inputs._parse_source
    children = project_erc_inputs.children

    def expiring_parse(*args, **kwargs):
        parsed = parse(*args, **kwargs)
        if parsed.head == "kicad_symbol_lib":
            clock[0] = 106.0
        return parsed

    def guarded_children(node, name):
        if (
            node.head == "symbol"
            and isinstance(node.items[1], str)
            and node.items[1].startswith("Unused")
        ):
            pytest.fail("expired preparation traversed the unused inheritance block")
        return children(node, name)

    monkeypatch.setattr(project_erc_inputs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(project_erc_inputs, "_parse_source", expiring_parse)
    monkeypatch.setattr(project_erc_inputs, "children", guarded_children)
    with pytest.raises(ProjectErcInputError, match="deadline expired"):
        _prepare(capture, libraries)


@pytest.mark.parametrize("boundary", ("bindings", "settings", "settings-deadline", "hierarchy"))
def test_subordinate_refusals_are_normalized_without_private_context(
    tmp_path, monkeypatch, boundary
):
    from copper_mcp.engineering import project_erc_inputs, project_settings, schematic_hierarchy
    from copper_mcp.engineering.schematic_project_capture import SchematicProjectCaptureError

    capture = _capture(tmp_path)
    function, error = {
        "bindings": ("_validate_bindings", SchematicProjectCaptureError),
        "settings": ("parse_project_document", project_settings.ProjectSettingsError),
        "settings-deadline": (
            "parse_project_document",
            project_settings.ProjectSettingsDeadlineError,
        ),
        "hierarchy": ("derive_schematic_hierarchy", schematic_hierarchy.SchematicHierarchyError),
    }[boundary]

    def refused(*args, **kwargs):
        raise error("private subordinate detail") from ValueError("private cause")

    monkeypatch.setattr(project_erc_inputs, function, refused)
    with pytest.raises(ProjectErcInputError) as caught:
        _prepare(capture)
    assert "private" not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


def test_project_normalization_stops_during_tree_traversal(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc_inputs

    capture = _capture(tmp_path, {"unused": ["must-not-visit", "expire-now"]})
    clock = [100.0]
    pattern = project_erc_inputs._UNBOUND_VARIABLE

    class ExpiringPattern:
        def search(self, value):
            if value == "expire-now":
                clock[0] = 106.0
            if value == "must-not-visit":
                pytest.fail("project normalization kept traversing after expiry")
            return pattern.search(value)

    monkeypatch.setattr(project_erc_inputs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(project_erc_inputs, "_UNBOUND_VARIABLE", ExpiringPattern())
    with pytest.raises(ProjectErcInputError, match="deadline expired"):
        _prepare(capture)


def test_project_normalization_checks_expiry_after_serialization(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc_inputs

    capture = _capture(tmp_path)
    clock = [100.0]
    dumps = project_erc_inputs.json.dumps
    digest = project_erc_inputs.digest_document

    def expiring_dumps(value, *args, **kwargs):
        result = dumps(value, *args, **kwargs)
        if isinstance(value, dict) and "erc" in value:
            clock[0] = 106.0
        return result

    def guarded_digest(schema, value):
        if schema == "copper-mcp/project-erc-execution-context/v1":
            pytest.fail("expired serialized settings reached execution identity construction")
        return digest(schema, value)

    monkeypatch.setattr(project_erc_inputs.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(project_erc_inputs.json, "dumps", expiring_dumps)
    monkeypatch.setattr(project_erc_inputs, "digest_document", guarded_digest)
    with pytest.raises(ProjectErcInputError, match="deadline expired"):
        _prepare(capture)
