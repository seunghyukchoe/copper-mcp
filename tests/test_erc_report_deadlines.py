"""Cooperative ERC report budgets preserve the existing no-deadline interpretation."""

import json

import pytest
from test_project_erc_report import _ROOT_UUID, _report, _violation

from copper_mcp import kicad_cli


def _document():
    return _report(sheets=[{"path": "/", "uuid_path": _ROOT_UUID, "violations": [_violation()]}])


def _parse(document, **kwargs):
    return kicad_cli._parse_erc_observation(
        json.dumps(document).encode(),
        return_code=5,
        expected_source="project.kicad_sch",
        **kwargs,
    )


def test_active_deadline_preserves_the_no_deadline_observation(monkeypatch):
    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: 0.0)
    assert _parse(_document()) == _parse(_document(), deadline=1.0)


def test_invalid_or_expired_deadlines_refuse(monkeypatch):
    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: 2.0)
    for deadline in (True, "bad", float("inf"), float("nan"), 10**10000):
        with pytest.raises(kicad_cli.KiCadCliError, match="deadline is malformed"):
            _parse(_document(), deadline=deadline)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline expired"):
        _parse(_document(), deadline=1.0)


@pytest.mark.parametrize("name", ("_preflight_drc_json", "_validate_drc_json_tree"))
def test_shared_json_guards_receive_the_active_deadline(monkeypatch, name):
    clock = [0.0]
    original = getattr(kicad_cli, name)

    def expiring_guard(value, *, check_deadline=None):
        assert check_deadline is not None
        clock[0] = 2.0
        return original(value, check_deadline=check_deadline)

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli, name, expiring_guard)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline expired"):
        _parse(_document(), deadline=1.0)


@pytest.mark.parametrize("phase", ("sort-key", "canonical", "hash"))
def test_normalizer_checks_after_each_expensive_phase(monkeypatch, phase):
    clock = [0.0]
    dumps = kicad_cli.json.dumps
    sha256 = kicad_cli.hashlib.sha256

    def expiring_dumps(value, *args, **kwargs):
        encoded = dumps(value, *args, **kwargs)
        if (phase == "sort-key" and "severity" in value) or (
            phase == "canonical" and "sheets" in value
        ):
            clock[0] = 2.0
        return encoded

    def expiring_hash(value):
        digest = sha256(value)
        if phase == "hash":
            clock[0] = 2.0
        return digest

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli.json, "dumps", expiring_dumps)
    monkeypatch.setattr(kicad_cli.hashlib, "sha256", expiring_hash)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline expired"):
        kicad_cli._normalized_erc_report_digest(_document(), deadline=1.0)
