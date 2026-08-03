#!/usr/bin/env python3
"""Validate committed Circuit Intent fixtures and deterministic schematic output."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from jsonschema import Draft202012Validator

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.circuit_intent_service import build_schematic_from_snapshot_json
from copper_mcp.circuit_ir import decode_snapshot_json, encode_snapshot

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = ROOT / "schemas/circuit-intent/0.1.0.schema.json"
BUILD_SCHEMA_PATH = ROOT / "schemas/circuit-schematic-build/0.1.0.schema.json"
FIXTURE_PATH = ROOT / "benchmarks/audio/fixtures/rc-low-pass-intent-v1.json"


def _load_object(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_bytes())
    if not isinstance(document, dict):
        raise ValueError(f"{path.relative_to(ROOT)} must contain one JSON object")
    return cast(dict[str, Any], document)


def main() -> int:
    """Check schema validity, canonical fixture bytes, and renderer determinism."""

    schema = _load_object(SCHEMA_PATH)
    build_schema = _load_object(BUILD_SCHEMA_PATH)
    fixture_bytes = FIXTURE_PATH.read_bytes()
    fixture = _load_object(FIXTURE_PATH)

    Draft202012Validator.check_schema(schema)
    Draft202012Validator.check_schema(build_schema)
    errors = tuple(Draft202012Validator(schema).iter_errors(fixture))
    if errors:
        path = "/".join(str(part) for part in errors[0].absolute_path) or "<root>"
        raise ValueError(f"Circuit Intent fixture violates the schema at {path}")

    snapshot = decode_snapshot_json(fixture_bytes)
    if encode_snapshot(snapshot) != fixture_bytes:
        raise ValueError("Circuit Intent fixture is not canonical")

    first = render_kicad_schematic(snapshot)
    second = render_kicad_schematic(snapshot)
    if first != second or first.intent_digest != snapshot.snapshot_digest:
        raise ValueError("KiCad schematic rendering is not deterministic or source-bound")

    build = build_schematic_from_snapshot_json(fixture_bytes)
    if build.artifact != first:
        raise ValueError("public Circuit Intent build does not match the pure renderer")
    build_errors = tuple(Draft202012Validator(build_schema).iter_errors(build.to_dict()))
    if build_errors:
        path = "/".join(str(part) for part in build_errors[0].absolute_path) or "<root>"
        raise ValueError(f"Circuit schematic build violates the response schema at {path}")

    print(
        "Circuit Intent fixtures valid: "
        f"1 fixture, {first.component_count} components, {first.net_count} nets, "
        f"{first.artifact_digest}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
