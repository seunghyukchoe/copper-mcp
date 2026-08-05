"""Focused tests for the redacted unsigned candidate DRC Statement payload."""

from __future__ import annotations

import json

import pytest

from copper_mcp.attestation import (
    AttestationError,
    build_candidate_drc_statement,
    canonical_statement_bytes,
)
from copper_mcp.kicad_cli import LayeredRouteCandidateDrcEvidence, RouteCandidateDrcEvidence
from copper_mcp.mcp_contracts import InTotoDrcStatementContract
from copper_mcp.models import DrcSummary


def _digest(fill: str) -> str:
    return f"sha256:{fill * 64}"


def _summary(*, base_revision: str, context_revision: str) -> DrcSummary:
    return DrcSummary(
        base_revision=base_revision,
        drc_context_revision=context_revision,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=1,
        warning_count=1,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={"clearance": 1, "silk_overlap": 1},
        passed=False,
    )


def _evidence() -> RouteCandidateDrcEvidence:
    candidate_base = _digest("a")
    source = _digest("b")
    patched = _digest("c")
    context = _digest("d")
    return RouteCandidateDrcEvidence(
        candidate_id=_digest("e"),
        candidate_base_revision=candidate_base,
        source_revision=source,
        patched_board_revision=patched,
        patched_drc_context_revision=context,
        summary=_summary(base_revision=patched, context_revision=context),
    )


def test_statement_uses_standard_shape_and_digest_bindings() -> None:
    evidence = _evidence()

    statement = evidence.to_statement()
    assert statement["_type"] == "https://in-toto.io/Statement/v1"
    assert statement["predicateType"] == "https://in-toto.io/attestation/link/v0.3"
    assert statement["subject"] == [{"name": "route-candidate", "digest": {"sha256": "e" * 64}}]
    assert [material["name"] for material in statement["predicate"]["materials"]] == [
        "board-ir-base",
        "board-source",
        "patched-board",
        "patched-drc-context",
    ]
    assert statement["predicate"]["byproducts"]["drc_summary"] == evidence.summary.to_dict()
    assert statement["predicate"]["environment"] == {
        "tool": "kicad-cli",
        "kicad_version": "10.0.5",
        "drc_schema": "https://schemas.kicad.org/drc.v1.json",
        "coordinate_units": "mm",
    }
    InTotoDrcStatementContract.model_validate(statement)


def test_statement_serialization_is_deterministic_and_detached() -> None:
    evidence = _evidence()
    first = evidence.to_statement()
    second = evidence.to_statement()

    assert first == second
    assert evidence.canonical_statement_bytes() == canonical_statement_bytes(second)
    assert json.loads(evidence.canonical_statement_bytes()) == first
    assert b" " not in evidence.canonical_statement_bytes()

    first["subject"][0]["name"] = "tampered"
    assert evidence.to_statement()["subject"][0]["name"] == "route-candidate"


def test_statement_rejects_an_empty_or_unknown_digest_object() -> None:
    statement = _evidence().to_statement()
    statement["subject"][0]["digest"] = {}
    with pytest.raises(ValueError):
        InTotoDrcStatementContract.model_validate(statement)

    statement = _evidence().to_statement()
    statement["subject"][0]["digest"] = {"sha256": "a" * 64, "sha512": "b" * 128}
    with pytest.raises(ValueError):
        InTotoDrcStatementContract.model_validate(statement)


def test_invalid_binding_and_digest_are_rejected() -> None:
    evidence = _evidence()
    with pytest.raises(AttestationError, match="candidate_id"):
        build_candidate_drc_statement(
            candidate_id="sha256:invalid",
            candidate_base_revision=evidence.candidate_base_revision,
            source_revision=evidence.source_revision,
            patched_board_revision=evidence.patched_board_revision,
            patched_drc_context_revision=evidence.patched_drc_context_revision,
            summary=evidence.summary,
        )
    with pytest.raises(AttestationError, match="patched board revision"):
        build_candidate_drc_statement(
            candidate_id=evidence.candidate_id,
            candidate_base_revision=evidence.candidate_base_revision,
            source_revision=evidence.source_revision,
            patched_board_revision=_digest("f"),
            patched_drc_context_revision=evidence.patched_drc_context_revision,
            summary=evidence.summary,
        )


def test_statement_is_redacted_and_layered_evidence_uses_same_contract() -> None:
    evidence = _evidence()
    layered = LayeredRouteCandidateDrcEvidence(
        candidate_id=evidence.candidate_id,
        candidate_base_revision=evidence.candidate_base_revision,
        source_revision=evidence.source_revision,
        patched_board_revision=evidence.patched_board_revision,
        patched_drc_context_revision=evidence.patched_drc_context_revision,
        summary=evidence.summary,
    )
    output = json.dumps(layered.to_dict(), sort_keys=True)
    for private_value in (
        "/private/designs/amp.kicad_pcb",
        "Net-AUDIO-OUT",
        "UUID-PRIVATE-FINDING",
        "clearance violation at (12.5, 4.0)",
    ):
        assert private_value not in output
    InTotoDrcStatementContract.model_validate(layered.to_dict()["statement"])
