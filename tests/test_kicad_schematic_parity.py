from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError, replace
from pathlib import Path

import pytest

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.adapters.kicad_schematic_parity import (
    MAX_KICAD_XML_DEPTH,
    MAX_KICAD_XML_NETLIST_BYTES,
    KiCadSchematicParityError,
    KiCadSchematicParityErrorCode,
    KiCadSchematicParityEvidence,
    KiCadSchematicParityLimits,
    verify_kicad_schematic_parity,
)
from copper_mcp.circuit_ir import decode_snapshot_json

ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
NETLIST = ROOT / "tests" / "fixtures" / "kicad-schematic" / "rc-low-pass-v1.kicadxml"


def _inputs():  # type: ignore[no-untyped-def]
    snapshot = decode_snapshot_json(INTENT.read_bytes())
    schematic = render_kicad_schematic(snapshot).content
    return snapshot, schematic, NETLIST.read_bytes()


def test_oracle_returns_deterministic_redacted_parity_evidence_without_mutation() -> None:
    snapshot, schematic, netlist = _inputs()
    before = (snapshot, schematic, netlist)

    first = verify_kicad_schematic_parity(snapshot, schematic, netlist)
    second = verify_kicad_schematic_parity(snapshot, schematic, netlist)

    assert first == second
    assert first == KiCadSchematicParityEvidence(
        intent_digest=snapshot.snapshot_digest,
        schematic_digest=f"sha256:{hashlib.sha256(schematic).hexdigest()}",
        netlist_digest=f"sha256:{hashlib.sha256(netlist).hexdigest()}",
        netlist_format_version="E",
        component_count=2,
        net_count=3,
        connection_count=4,
        source_replay="passed",
        component_parity="passed",
        connectivity_parity="passed",
    )
    assert (snapshot, schematic, netlist) == before
    assert "AUDIO" not in repr(first)
    assert "R1" not in repr(first)
    with pytest.raises(FrozenInstanceError):
        first.net_count = 4  # type: ignore[misc]


def test_oracle_rejects_any_schematic_edit_before_accepting_connectivity() -> None:
    snapshot, schematic, netlist = _inputs()
    edited = schematic.replace(b'(title "', b'(title "edited-', 1)

    with pytest.raises(KiCadSchematicParityError) as raised:
        verify_kicad_schematic_parity(snapshot, edited, netlist)

    assert raised.value.code is KiCadSchematicParityErrorCode.SOURCE_MISMATCH
    assert "edited" not in str(raised.value)


@pytest.mark.parametrize(
    ("old", "new", "code"),
    [
        (
            b"Circuit Intent source: sha256:06383cab",
            b"Circuit Intent source: sha256:16383cab",
            KiCadSchematicParityErrorCode.SOURCE_MISMATCH,
        ),
        (
            b"<value>1k</value>",
            b"<value>2k</value>",
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
        ),
        (
            b'<libsource lib="CopperMCP" part="R"',
            b'<libsource lib="CopperMCP" part="C"',
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
        ),
        (
            b'<node ref="R1" pin="2" pintype="passive"/>',
            b'<node ref="R1" pin="1" pintype="passive"/>',
            KiCadSchematicParityErrorCode.CONNECTIVITY_MISMATCH,
        ),
        (
            b'<node ref="C1" pin="1" pintype="passive"/>',
            b'<node ref="C1" pin="1" pintype="output"/>',
            KiCadSchematicParityErrorCode.COMPONENT_MISMATCH,
        ),
    ],
)
def test_oracle_fails_closed_on_source_component_and_net_drift(
    old: bytes,
    new: bytes,
    code: KiCadSchematicParityErrorCode,
) -> None:
    snapshot, schematic, netlist = _inputs()
    assert netlist.count(old) == 1

    with pytest.raises(KiCadSchematicParityError) as raised:
        verify_kicad_schematic_parity(snapshot, schematic, netlist.replace(old, new, 1))

    assert raised.value.code is code


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (
            b'<?xml version="1.0" encoding="UTF-8"?>\n'
            b'<!DOCTYPE export [<!ENTITY x "expanded">]><export version="E">&x;</export>',
            KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
        ),
        (
            b'<?xml version="1.0" encoding="UTF-8"?>\n<export version="E">',
            KiCadSchematicParityErrorCode.XML_INVALID,
        ),
    ],
)
def test_oracle_rejects_xml_directives_and_malformed_xml(
    payload: bytes,
    code: KiCadSchematicParityErrorCode,
) -> None:
    snapshot, schematic, _ = _inputs()

    with pytest.raises(KiCadSchematicParityError) as raised:
        verify_kicad_schematic_parity(snapshot, schematic, payload)

    assert raised.value.code is code


def test_oracle_rejects_unknown_xml_structure_and_duplicate_connectivity() -> None:
    snapshot, schematic, netlist = _inputs()
    cases = (
        netlist.replace(b'<export version="E">', b'<export version="E" vendor="x">', 1),
        netlist.replace(b"  <libraries/>", b"  <libraries/><vendor/>", 1),
        netlist.replace(
            b'<node ref="C1" pin="2" pintype="passive"/>',
            b'<node ref="C1" pin="2" pintype="passive"/>\n'
            b'      <node ref="C1" pin="2" pintype="passive"/>',
            1,
        ),
    )

    expected_codes = (
        KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
        KiCadSchematicParityErrorCode.XML_UNSUPPORTED,
        KiCadSchematicParityErrorCode.CONNECTIVITY_MISMATCH,
    )
    for payload, expected_code in zip(cases, expected_codes, strict=True):
        with pytest.raises(KiCadSchematicParityError) as raised:
            verify_kicad_schematic_parity(snapshot, schematic, payload)
        assert raised.value.code is expected_code


def test_oracle_budgets_are_tighten_only_and_enforced() -> None:
    snapshot, schematic, netlist = _inputs()

    for limits in (
        KiCadSchematicParityLimits(max_netlist_bytes=len(netlist) - 1),
        KiCadSchematicParityLimits(max_depth=3),
        KiCadSchematicParityLimits(max_elements=8),
        KiCadSchematicParityLimits(max_text_bytes=32),
    ):
        with pytest.raises(KiCadSchematicParityError) as raised:
            verify_kicad_schematic_parity(snapshot, schematic, netlist, limits)
        assert raised.value.code is KiCadSchematicParityErrorCode.BUDGET_EXCEEDED

    with pytest.raises(ValueError, match="positive integers"):
        KiCadSchematicParityLimits(max_depth=True)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="cannot exceed"):
        KiCadSchematicParityLimits(max_depth=MAX_KICAD_XML_DEPTH + 1)
    with pytest.raises(ValueError, match="cannot exceed"):
        KiCadSchematicParityLimits(max_netlist_bytes=MAX_KICAD_XML_NETLIST_BYTES + 1)


def test_oracle_rejects_mutable_or_wrong_public_inputs() -> None:
    snapshot, schematic, netlist = _inputs()
    cases = (
        (object(), schematic, netlist, None),
        (snapshot, bytearray(schematic), netlist, None),
        (snapshot, schematic, bytearray(netlist), None),
        (snapshot, schematic, netlist, object()),
    )
    for candidate_snapshot, candidate_schematic, candidate_netlist, limits in cases:
        with pytest.raises(KiCadSchematicParityError) as raised:
            verify_kicad_schematic_parity(  # type: ignore[arg-type]
                candidate_snapshot,
                candidate_schematic,
                candidate_netlist,
                limits,
            )
        assert raised.value.code is KiCadSchematicParityErrorCode.INPUT_INVALID


def test_evidence_cannot_be_constructed_with_a_failed_gate() -> None:
    snapshot, schematic, netlist = _inputs()
    evidence = verify_kicad_schematic_parity(snapshot, schematic, netlist)

    with pytest.raises(ValueError, match="cannot represent a failed gate"):
        replace(evidence, source_replay="failed")  # type: ignore[arg-type]
