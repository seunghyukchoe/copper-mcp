"""Recognize the private derivative in legacy fake engines, not a native rule validator."""

from __future__ import annotations

import re
from pathlib import Path


def make_fake_kicad_cli(root: Path, name: str = "kicad-cli") -> Path:
    """Create one inert executable used only as a stable unit-test identity."""

    executable = root / name
    executable.write_bytes(b"#!/bin/sh\nexit 0\n")
    executable.chmod(0o700)
    return executable.resolve()


def rule_liveness_report(board: Path) -> dict[str, object] | None:
    payload = board.read_bytes()
    if b"__copper_mcp_rule_liveness_" not in payload:
        return None
    matches = re.findall(
        rb'\(gr_text "(__copper_mcp_rule_liveness_[0-9a-f_]+)"'
        rb' \(at -100 -100\) \(layer "F\.Cu"\)\s+'
        rb'\(uuid "([0-9a-f-]{36})"\)',
        payload,
    )
    assert len(matches) == 1, "fake engine expected one private derivative item"
    marker, item_uuid = (value.decode("ascii") for value in matches[0])
    assert marker == "__copper_mcp_rule_liveness_" + item_uuid.replace("-", "_")
    rules = board.with_suffix(".kicad_dru").read_bytes()
    assert f'(rule "CopperMCP rule loading probe {item_uuid}"'.encode() in rules
    assert f"A.Type == 'Text' && A.Text == '{marker}'".encode() in rules
    assert b"(severity error)" in rules and b'(constraint assertion "0 == 1")' in rules
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": board.name,
        "date": "2026-08-03T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": [
            {
                "type": "assertion_failure",
                "description": "private derivative witness",
                "severity": "error",
                "excluded": False,
                "items": [{"uuid": item_uuid}],
            }
        ],
        "unconnected_items": [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }
