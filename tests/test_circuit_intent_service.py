from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

import copper_mcp.circuit_intent_service as circuit_intent_service
from copper_mcp.circuit_intent_service import (
    CircuitSchematicBuild,
    build_schematic_from_content,
    build_schematic_from_snapshot_json,
)
from copper_mcp.circuit_ir import CircuitIntentValidationError, CircuitParseLimits

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
SCHEMA = ROOT / "schemas" / "circuit-schematic-build" / "0.1.0.schema.json"


def _document() -> dict[str, Any]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _content() -> dict[str, Any]:
    content = _document()["content"]
    assert isinstance(content, dict)
    return content


def _expected_metadata(build: CircuitSchematicBuild) -> dict[str, Any]:
    artifact = build.artifact
    return {
        "schema": "copper.circuit-schematic-build",
        "schema_version": "0.1.0",
        "status": "rendered",
        "intent": {
            "schema": "copper.circuit-intent",
            "schema_version": "0.1.0",
            "intent_digest": artifact.intent_digest,
            "counts": {"components": 2, "nets": 3, "ports": 3},
        },
        "artifact": {
            "kind": "kicad_schematic",
            "mime_type": "application/x-kicad-schematic",
            "format_version": artifact.format_version,
            "artifact_digest": artifact.artifact_digest,
            "intent_digest": artifact.intent_digest,
            "size_bytes": len(artifact.content),
        },
        "verification": {
            "intent_topology": "passed",
            "artifact_digest": "passed",
            "provenance_binding": "passed",
            "deterministic_replay": "passed",
            "kicad_cli_parse": "not_run",
            "erc": "not_run",
            "schematic_board_parity": "not_run",
            "electrical_validation": "not_run",
            "board_ready": False,
        },
    }


def test_snapshot_and_structured_content_build_the_same_deterministic_artifact() -> None:
    fixture_before = FIXTURE.read_bytes()
    content = _content()
    content_before = copy.deepcopy(content)

    from_snapshot = build_schematic_from_snapshot_json(fixture_before)
    replay = build_schematic_from_snapshot_json(fixture_before)
    from_content = build_schematic_from_content(content)

    assert isinstance(from_snapshot, CircuitSchematicBuild)
    assert from_snapshot == replay == from_content
    assert from_snapshot.artifact.content == from_content.artifact.content
    assert from_snapshot.artifact.artifact_digest == (
        f"sha256:{hashlib.sha256(from_snapshot.artifact.content).hexdigest()}"
    )
    assert from_snapshot.artifact.intent_digest == _document()["snapshot_digest"]
    assert from_snapshot.to_dict() == _expected_metadata(from_snapshot)
    assert content == content_before
    assert FIXTURE.read_bytes() == fixture_before


def test_build_metadata_matches_the_published_schema() -> None:
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    metadata = build_schematic_from_snapshot_json(FIXTURE.read_bytes()).to_dict()

    assert list(Draft202012Validator(schema).iter_errors(metadata)) == []


def test_build_metadata_is_detached_frozen_and_redacted() -> None:
    build = build_schematic_from_snapshot_json(FIXTURE.read_bytes())
    metadata = build.to_dict()
    serialized = json.dumps(metadata, sort_keys=True)
    schematic_text = build.artifact.content.decode("utf-8")

    for private_value in (
        "rc-low-pass-v1",
        "Original low-voltage RC audio low-pass intent",
        "component:c-filter",
        "component:r-input",
        "100n",
        "1k",
        "AUDIO_IN",
        "AUDIO_OUT",
        "GND",
        "R1",
        "C1",
        "(kicad_sch",
    ):
        assert private_value not in serialized
    assert "AUDIO_IN" in schematic_text
    assert "100n" in schematic_text

    metadata["status"] = "tampered"
    metadata["artifact"]["size_bytes"] = 0
    assert build.to_dict() == _expected_metadata(build)
    with pytest.raises(FrozenInstanceError):
        build.artifact = build.artifact  # type: ignore[misc]


def test_service_fails_when_the_required_deterministic_replay_differs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = build_schematic_from_snapshot_json(FIXTURE.read_bytes()).artifact
    changed = _content()
    changed["title"] = "A distinct valid title"
    other = build_schematic_from_content(changed).artifact
    rendered = iter((original, other))
    monkeypatch.setattr(
        circuit_intent_service,
        "render_kicad_schematic",
        lambda snapshot: next(rendered),
    )

    with pytest.raises(RuntimeError, match="deterministic schematic replay did not match"):
        build_schematic_from_snapshot_json(FIXTURE.read_bytes())


def test_snapshot_service_keeps_strict_json_and_digest_checks() -> None:
    fixture = FIXTURE.read_bytes()
    duplicate_key = b'{"schema":"copper.circuit-intent",' + fixture[1:]
    tampered = _document()
    tampered["content"]["title"] = "A different valid title"
    tampered_bytes = json.dumps(tampered, separators=(",", ":")).encode()

    with pytest.raises(CircuitIntentValidationError) as duplicate_error:
        build_schematic_from_snapshot_json(duplicate_key)
    with pytest.raises(CircuitIntentValidationError) as digest_error:
        build_schematic_from_snapshot_json(tampered_bytes)

    assert duplicate_error.value.code == "schema.invalid"
    assert digest_error.value.code == "digest.mismatch"


@pytest.mark.parametrize(
    "mutation",
    [
        lambda content: content.update({"unsupported": "SECRET_UNSUPPORTED_FIELD"}),
        lambda content: content.update({"title": 1.5}),
        lambda content: content["nets"][0]["connections"][0].update(
            {"component_id": "component:secret-missing"}
        ),
    ],
)
def test_structured_content_rejects_malformed_values_without_disclosure(
    mutation: Any,
) -> None:
    content = _content()
    mutation(content)

    with pytest.raises(CircuitIntentValidationError) as raised:
        build_schematic_from_content(content)

    assert raised.value.code in {"schema.invalid", "reference.unknown"}
    assert "SECRET_UNSUPPORTED_FIELD" not in str(raised.value)
    assert "secret-missing" not in str(raised.value)


def test_service_honors_tighter_caller_limits() -> None:
    with pytest.raises(CircuitIntentValidationError) as raised:
        build_schematic_from_content(
            _content(),
            CircuitParseLimits(max_components=1),
        )

    assert raised.value.code == "budget.exceeded"
