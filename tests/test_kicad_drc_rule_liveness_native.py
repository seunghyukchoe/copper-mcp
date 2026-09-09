"""Opt-in KiCad 10.0.5 controls for custom-rule liveness; never run by unit tests."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, run_board_drc

CLI_ENV = "COPPER_MCP_TEST_KICAD_RULE_LIVENESS"
CLI = Path(os.environ.get(CLI_ENV, "/nonexistent/kicad-cli"))
FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"

pytestmark = [
    pytest.mark.real_kicad,
    pytest.mark.skipif(
        not os.environ.get(CLI_ENV),
        reason=f"set {CLI_ENV} to the reviewed KiCad 10.0.5 CLI",
    ),
]


def _workspace(tmp_path: Path, rules: bytes, *, with_project: bool = True) -> Settings:
    (tmp_path / "board.kicad_pcb").write_bytes(FIXTURE.read_bytes())
    (tmp_path / "board.kicad_dru").write_bytes(rules)
    if with_project:
        (tmp_path / "board.kicad_pro").write_text(
            json.dumps(
                {
                    "meta": {"version": 3},
                    "text_variables": {"CLEARANCE": "0.5mm"},
                    "board": {
                        "design_settings": {
                            "rule_severities": {
                                "assertion_failure": "error",
                                "clearance": "error",
                            }
                        }
                    },
                }
            ),
            encoding="utf-8",
        )
    return Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=30)


@pytest.mark.parametrize(
    "rules",
    [
        b"",
        b"# comment-only custom rule file",
        b"(version 1)\n",
        b'(version 1)\n# comment\n(rule "Valid" '
        b"(condition \"A.Type == 'Pad'\") "
        b"(constraint clearance (min ${CLEARANCE})))\n",
        b'(version 1)\n(rule "Ignored original assertion" (severity ignore) '
        b'(condition "A.Type == \'Pad\'") (constraint assertion "0 == 1"))\n',
        b'(version 1)\n(rule "Error original assertion" (severity error) '
        b'(condition "A.Type == \'Pad\'") (constraint assertion "0 == 1"))\n',
    ],
)
def test_valid_rule_sets_produce_witness_then_original_report(tmp_path: Path, rules: bytes) -> None:
    summary = run_board_drc("board.kicad_pcb", _workspace(tmp_path, rules))
    assert summary.kicad_version == "10.0.5"


@pytest.mark.parametrize(
    "rules",
    [
        b"\xef\xbb\xbf(version 1)\n",
        b'(rule "Missing version" (constraint clearance (min 0.5mm)))\n',
        b'(version 1)\n(rule "Partial" (constraint clearance (min 0.5mm))',
        b'(version 1)\n(rule "Bad" (constraint clearance (min 0.5)))\n',
        b'(version 1)\n(rule "Bad" (constraint nonexistent (min 0.5mm)))\n',
        b'(version 1)\n(rule "Bad" (condition "A.Nonexistent == 1") '
        b"(constraint clearance (min 0.5mm)))\n",
    ],
)
def test_malformed_rule_fallback_cannot_return_default_rule_success(
    tmp_path: Path, rules: bytes
) -> None:
    with pytest.raises(KiCadCliError, match="not proven live"):
        run_board_drc("board.kicad_pcb", _workspace(tmp_path, rules))


def test_valid_rules_without_project_file_still_produce_witness(tmp_path: Path) -> None:
    summary = run_board_drc(
        "board.kicad_pcb",
        _workspace(tmp_path, b"(version 1)\n", with_project=False),
    )
    assert summary.kicad_version == "10.0.5"


def test_original_assertion_findings_are_retained_in_final_report(tmp_path: Path) -> None:
    rules = b'(version 1)\n(rule "Original assertion" (severity error) '
    rules += b'(condition "A.Type == \'Pad\'") (constraint assertion "0 == 1"))\n'
    summary = run_board_drc("board.kicad_pcb", _workspace(tmp_path, rules))
    assert summary.violation_type_counts.get("assertion_failure", 0) > 0
    assert not summary.passed
