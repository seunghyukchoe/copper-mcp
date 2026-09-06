from __future__ import annotations

import json

import pytest

from copper_mcp.engineering.project_settings import (
    ProjectSettingsDeadlineError,
    ProjectSettingsError,
    extract_project_variables,
    parse_project_document,
)


def test_extracts_a_copy_of_text_variables_without_claiming_other_settings() -> None:
    payload = b'{"board":{"unvalidated":true},"text_variables":{"SUBDIR":"nested","A":"${B}"}}'
    variables = extract_project_variables(payload)
    assert variables == {"SUBDIR": "nested", "A": "${B}"}
    variables["SUBDIR"] = "changed"
    assert extract_project_variables(payload)["SUBDIR"] == "nested"
    assert parse_project_document(payload)["board"] == {"unvalidated": True}


@pytest.mark.parametrize(
    "payload",
    [
        b"not json",
        b"[]",
        b'{"text_variables":[],"text_variables":{}}',
        b'{"number":NaN}',
        b'{"text_variables":{"A":1}}',
        b'{"text_variables":{"": "value"}}',
        b'{"text_variables":{"not a KiCad variable": "value"}}',
    ],
)
def test_refuses_malformed_project_documents(payload: bytes) -> None:
    with pytest.raises(ProjectSettingsError, match="project settings are malformed") as caught:
        parse_project_document(payload)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_refuses_depth_value_and_text_variable_bounds() -> None:
    deep: object = "leaf"
    for _ in range(65):
        deep = [deep]
    for payload in (
        json.dumps(deep).encode(),
        json.dumps({"text_variables": {str(index): "x" for index in range(129)}}).encode(),
        json.dumps({"text_variables": {"A" * 129: "x"}}).encode(),
        json.dumps({"text_variables": {"A": "x" * 4097}}).encode(),
        json.dumps({"values": [0] * 250_001}).encode(),
        b" " * (32 * 1024 * 1024 + 1),
    ):
        with pytest.raises(ProjectSettingsError, match="project settings are malformed"):
            parse_project_document(payload)


def test_preflight_checks_deadline_before_json_decode(monkeypatch):
    from copper_mcp.engineering import project_settings

    clock = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(project_settings.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(json, "loads", lambda *_a, **_k: pytest.fail("expired preflight must stop"))
    with pytest.raises(ProjectSettingsDeadlineError) as caught:
        parse_project_document(b'{"value":"' + b"x" * 10_000 + b'"}', deadline=1.0)
    assert caught.value.__context__ is None


def test_tree_validation_keeps_deadline_specific_refusal(monkeypatch):
    from copper_mcp import kicad_cli
    from copper_mcp.engineering import project_settings

    payload = json.dumps({"values": [0] * 5_000}).encode()
    clock = iter((0.0, 0.0, 0.0, 1.0))
    monkeypatch.setattr(project_settings.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(kicad_cli, "_preflight_drc_json", lambda *_a, **_k: None)
    with pytest.raises(ProjectSettingsDeadlineError) as caught:
        parse_project_document(payload, deadline=1.0)
    assert caught.value.__context__ is None


@pytest.mark.parametrize(
    "deadline",
    (True, float("nan"), float("inf"), 10**10_000),
    ids=("bool", "nan", "inf", "overflow"),
)
def test_malformed_deadline_is_redacted(deadline):
    with pytest.raises(ProjectSettingsError) as caught:
        parse_project_document(b"{}", deadline=deadline)
    assert caught.value.__context__ is None
