from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

import pytest

from copper_mcp import kicad_cli
from copper_mcp.config import Settings
from copper_mcp.kicad_drc_rule_liveness import (
    KiCadDrcRuleLivenessError,
    RuleLivenessWitness,
    build_rule_liveness_context,
    require_rule_liveness_witness,
)
from copper_mcp.models import DrcSummary

WITNESS_UUID = "90a174b8-70db-49e8-ab53-cf60b051a7b2"
WITNESS_MARKER = "__copper_mcp_rule_liveness_90a174b8_70db_49e8_ab53_cf60b051a7b2"


def _context() -> dict[str, bytes]:
    return {
        "board.kicad_pcb": b'(kicad_pcb (version 20240108) (property "x" "quote\\""))',
        "board.kicad_dru": (
            b"\xef\xbb\xbf(version 1)\n# retained comment ( and )\n"
            b'(rule "complex" (condition "A.Type == \'Track\'") '
            b"(constraint clearance (min ${CLEARANCE})))"
        ),
        "board.kicad_pro": json.dumps(
            {
                "meta": {"version": 3},
                "text_variables": {"CLEARANCE": "0.5mm", "QUOTED": 'a"b'},
                "board": {
                    "design_settings": {
                        "rule_severities": {
                            "assertion_failure": "ignore",
                            "clearance": "warning",
                        }
                    }
                },
            }
        ).encode(),
    }


def _build(context: dict[str, bytes], **overrides):
    return build_rule_liveness_context(
        context,
        "board.kicad_pcb",
        max_file_bytes=overrides.get("max_file_bytes", 1_000_000),
        max_context_files=overrides.get("max_context_files", 16),
        max_context_bytes=overrides.get("max_context_bytes", 4_000_000),
        deadline=overrides.get("deadline", time.monotonic() + 10),
    )


def _report(witness: RuleLivenessWitness, **changes) -> bytes:
    finding = {
        "type": changes.get("type", "assertion_failure"),
        "description": "untrusted native description",
        "severity": changes.get("severity", "error"),
        "items": [{"uuid": changes.get("uuid", witness.item_uuid)}],
    }
    if "excluded" in changes:
        finding["excluded"] = changes["excluded"]
    return json.dumps({"violations": [finding]}).encode()


def _summary(revision: str) -> DrcSummary:
    return DrcSummary(
        base_revision=revision,
        drc_context_revision=revision,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={},
        passed=True,
    )


def test_derivative_preserves_original_rules_variables_and_context(monkeypatch) -> None:
    context = _context()
    context["board.kicad_pcb"] += b"\n\t"
    original = dict(context)
    monkeypatch.setattr(
        "copper_mcp.kicad_drc_rule_liveness.uuid.uuid4",
        lambda: uuid.UUID(WITNESS_UUID),
    )
    derivative, witness = _build(context)
    assert context == original
    assert witness.item_uuid == WITNESS_UUID
    assert derivative["board.kicad_dru"].startswith(original["board.kicad_dru"])
    assert WITNESS_UUID.encode() in derivative["board.kicad_pcb"]
    assert derivative["board.kicad_pcb"].endswith(b")\n\t")
    project = json.loads(derivative["board.kicad_pro"])
    assert project["text_variables"] == {"CLEARANCE": "0.5mm", "QUOTED": 'a"b'}
    assert derivative["board.kicad_pro"] == original["board.kicad_pro"]


def test_probe_does_not_create_a_missing_project_file() -> None:
    context = _context()
    del context["board.kicad_pro"]
    derivative, _witness = _build(context)
    assert "board.kicad_pro" not in derivative


@pytest.mark.parametrize(
    ("rules", "adds_version"),
    [
        (b"", True),
        (b"# comment only without newline", True),
        (b'(rule "missing version" (constraint clearance (min 0.5mm)))', False),
        (b'(version 1)\n(rule "partial" (constraint clearance (min 0.5mm))', False),
        (b"\xef\xbb\xbf", False),
    ],
)
def test_probe_only_versions_token_empty_rules_without_repairing_other_input(
    rules: bytes, adds_version: bool
) -> None:
    context = _context()
    context["board.kicad_dru"] = rules
    derivative, _witness = _build(context)
    assert derivative["board.kicad_dru"].startswith(rules + b"\n")
    assert derivative["board.kicad_dru"].count(b"(version 1)") == rules.count(b"(version 1)") + int(
        adds_version
    )


@pytest.mark.parametrize(
    "changes",
    [
        {"uuid": "00000000-0000-0000-0000-000000000000"},
        {"type": "clearance"},
        {"severity": "warning"},
        {"excluded": True},
    ],
)
def test_witness_requires_exact_unexcluded_error_assertion(changes: dict[str, object]) -> None:
    witness = RuleLivenessWitness(WITNESS_UUID, WITNESS_MARKER)
    require_rule_liveness_witness(_report(witness), witness, deadline=time.monotonic() + 10)
    with pytest.raises(KiCadDrcRuleLivenessError, match="not established"):
        require_rule_liveness_witness(
            _report(witness, **changes), witness, deadline=time.monotonic() + 10
        )


def test_derivative_bounds_bad_project_and_deadline_are_fixed_refusals() -> None:
    context = _context()
    with pytest.raises(KiCadDrcRuleLivenessError):
        _build(context, max_context_bytes=sum(map(len, context.values())))
    context["board.kicad_pro"] = b'{"board":{"design_settings":[]}}'
    with pytest.raises(KiCadDrcRuleLivenessError):
        _build(context)
    with pytest.raises(KiCadDrcRuleLivenessError, match="deadline"):
        _build(_context(), deadline=time.monotonic() - 1)


def test_captured_runner_consumes_derivative_then_untouched_original(
    tmp_path: Path, monkeypatch
) -> None:
    context = _context()
    original = dict(context)
    calls: list[tuple[dict[str, bytes], RuleLivenessWitness | None]] = []

    def run_pass(payload, *, witness=None, **_kwargs):
        captured = dict(payload)
        calls.append((captured, witness))
        payload.clear()
        return _summary(kicad_cli._context_revision(captured))

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    result = kicad_cli._run_captured_drc(
        context,
        board_relative="board.kicad_pcb",
        settings=Settings(workspace=tmp_path, kicad_timeout_seconds=10),
    )
    assert result == _summary(kicad_cli._context_revision(original))
    assert context == {}
    assert len(calls) == 2 and calls[0][1] is not None and calls[1][1] is None
    assert calls[1][0] == original
    assert calls[0][0]["board.kicad_dru"].startswith(original["board.kicad_dru"])
    assert calls[0][0]["board.kicad_pro"] == original["board.kicad_pro"]
    assert calls[1][0]["board.kicad_pro"] == original["board.kicad_pro"]


def test_context_without_custom_rule_companion_keeps_single_pass(monkeypatch) -> None:
    context = _context()
    del context["board.kicad_dru"]
    calls = 0

    def run_pass(payload, **_kwargs):
        nonlocal calls
        calls += 1
        captured = dict(payload)
        payload.clear()
        return _summary(kicad_cli._context_revision(captured))

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    kicad_cli._run_captured_drc(
        context,
        board_relative="board.kicad_pcb",
        settings=Settings(workspace=Path.cwd()),
    )
    assert calls == 1 and context == {}
