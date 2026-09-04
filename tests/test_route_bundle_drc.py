"""Authoritative DRC evidence for composed route bundles.

The composition serializer itself is covered in ``test_route_bundle``. What matters here is
the evidence boundary: a routed plan continued through one fixed KiCad DRC run on the
composed board, with bundle-bound aggregate evidence, while every structural problem
refuses before a subprocess starts. Tests that need a live ``kicad-cli`` skip by name
when it is absent; everything else runs offline, including the proof that structural
refusals never reach for KiCad at all.
"""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, RouteBundleDrcEvidence, run_route_bundle_drc
from copper_mcp.mcp_contracts import (
    InTotoDrcStatementContract,
    RouteBundleDrcEvidenceContract,
    RouteBundleToolResponse,
)
from copper_mcp.models import DrcSummary
from copper_mcp.route_bundle import (
    RouteBundleError,
    RouteBundlePreview,
    RouteBundleStatus,
    parse_route_bundle_request,
    preview_route_bundle,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)
requires_kicad = pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(), reason="needs a real kicad-cli for authoritative DRC"
)


def _constraints() -> dict[str, int]:
    return {
        "clearance_nm": 100_000,
        "track_width_nm": 200_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    }


def _payload(board: str, source: bytes, **overrides: Any) -> dict[str, Any]:
    from copper_mcp.adapters import KiCadConstraintProfile, net_id_for_name, parse_kicad_bytes
    from copper_mcp.board_ir import NetClass

    net_class = NetClass(id="class:request", name="Request", **_constraints())
    converted = parse_kicad_bytes(
        source,
        KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id),
    )
    assert converted.snapshot is not None
    assert not converted.diagnostics
    payload: dict[str, Any] = {
        "board": board,
        "layer": "F.Cu",
        "constraints": _constraints(),
        "net_ref_ids": [net_id_for_name("HORIZONTAL"), net_id_for_name("VERTICAL")],
        "expect_board_revision": f"sha256:{hashlib.sha256(source).hexdigest()}",
        "expect_snapshot_digest": converted.snapshot.snapshot_digest,
        "seed": 7,
        "settings": {
            "grid_step_nm": 1_000_000,
            "bend_penalty_nm": 500_000,
            "proximity_penalty_nm": 0,
            "max_grid_nodes": 512,
            "max_expansions": 20_000,
            "max_obstacles": 128,
            "max_obstacle_checks": 200_000,
        },
    }
    payload.update(overrides)
    return payload


def _plan(tmp_path: Path) -> Any:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    result = preview_route_bundle(_payload(board.name, source), Settings(workspace=tmp_path))
    assert result.status is RouteBundleStatus.ROUTED
    assert result.plan is not None
    return result.plan


def _summary(**overrides: Any) -> DrcSummary:
    fields: dict[str, Any] = {
        "base_revision": "sha256:" + "b" * 64,
        "drc_context_revision": "sha256:" + "c" * 64,
        "kicad_version": "10.0.5",
        "drc_schema": "https://schemas.kicad.org/drc.v1.json",
        "coordinate_units": "mm",
        "error_count": 0,
        "warning_count": 0,
        "exclusion_count": 0,
        "ignored_check_count": 0,
        "unconnected_count": 0,
        "violation_type_counts": {},
        "passed": True,
    }
    fields.update(overrides)
    return DrcSummary(**fields)


def test_include_drc_defaults_off_and_parses_when_set(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    assert parse_route_bundle_request(_payload(board.name, source)).include_drc is False
    assert (
        parse_route_bundle_request(_payload(board.name, source, include_drc=True)).include_drc
        is True
    )
    with pytest.raises(RouteBundleError):
        parse_route_bundle_request(_payload(board.name, source, include_drc="yes"))


def test_routed_response_without_drc_carries_schema_1_1_and_no_evidence(
    tmp_path: Path,
) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    document = preview_route_bundle(
        _payload(board.name, source), Settings(workspace=tmp_path)
    ).to_dict()
    validated = RouteBundleToolResponse.model_validate(document)
    assert document["schema_version"] == "1.1"
    assert document["status"] == "routed"
    assert document["drc_evidence"] is None
    assert validated is not None


def test_structural_refusal_fires_before_any_kicad_execution(tmp_path: Path) -> None:
    """No kicad-cli exists on some machines; a structural refusal must not need one."""

    plan = _plan(tmp_path)
    # A plan is immutable and self-consistent by construction, so staleness is produced the
    # honest way: the board moves under it, and the captured snapshot no longer matches.
    other = ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
    (tmp_path / FIXTURE.name).write_bytes(other.read_bytes())
    with pytest.raises(KiCadCliError, match="stale for the captured Board IR snapshot"):
        run_route_bundle_drc(
            FIXTURE.name,
            plan,
            _profile(),
            Settings(workspace=tmp_path),
        )
    with pytest.raises(KiCadCliError, match="malformed"):
        run_route_bundle_drc(
            FIXTURE.name,
            object(),
            _profile(),
            Settings(workspace=tmp_path),
        )


def _profile() -> Any:
    from copper_mcp.adapters import KiCadConstraintProfile
    from copper_mcp.board_ir import NetClass

    net_class = NetClass(id="class:request", name="Request", **_constraints())
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def test_evidence_binding_rejects_forged_subjects() -> None:
    digest = "sha256:" + "d" * 64
    evidence = RouteBundleDrcEvidence(
        bundle_id=digest,
        bundle_base_revision="sha256:" + "e" * 64,
        candidate_ids=("sha256:" + "f" * 64, "sha256:" + "a" * 64),
        source_revision="sha256:" + "b" * 63 + "0",
        patched_board_revision="sha256:" + "b" * 64,
        patched_drc_context_revision="sha256:" + "c" * 64,
        summary=_summary(),
    )
    document = evidence.to_dict()
    validated = RouteBundleDrcEvidenceContract.model_validate(document)
    assert validated.bundle_id == digest
    assert validated.candidate_ids == ["sha256:" + "f" * 64, "sha256:" + "a" * 64]
    statement = InTotoDrcStatementContract.model_validate(document["statement"])
    assert statement.subject[0].name == "route-bundle"
    assert statement.predicate.byproducts.candidate_ids == sorted(validated.candidate_ids)
    with pytest.raises(ValueError, match="distinct digests"):
        replace(evidence, candidate_ids=("sha256:" + "f" * 64,))
    with pytest.raises(ValueError, match="bound to the patched board"):
        replace(evidence, patched_board_revision="sha256:" + "9" * 64)


@requires_kicad
def test_routed_bundle_with_drc_binds_one_composed_evidence(tmp_path: Path) -> None:
    source = FIXTURE.read_bytes()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    settings = Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI)
    result = preview_route_bundle(_payload(board.name, source, include_drc=True), settings)
    assert result.status is RouteBundleStatus.ROUTED
    assert result.plan is not None
    document = result.to_dict()
    validated = RouteBundleToolResponse.model_validate(document)
    assert validated is not None
    evidence = document["drc_evidence"]
    assert evidence is not None
    assert evidence["bundle_id"] == result.plan.bundle_id
    assert evidence["bundle_base_revision"] == result.plan.base_revision
    assert evidence["candidate_ids"] == [item.candidate_id for item in result.plan.candidates]
    assert evidence["source_revision"] == document["board_revision"]
    assert evidence["summary"]["base_revision"] == evidence["patched_board_revision"]
    RouteBundleDrcEvidenceContract.model_validate(evidence)
    InTotoDrcStatementContract.model_validate(evidence["statement"])


def test_response_rejects_evidence_bound_to_another_plan(tmp_path: Path) -> None:
    plan = _plan(tmp_path)
    first = plan.candidates[0].candidate_id
    forged_first = "sha256:" + ("0" if first[7] != "0" else "1") + first[8:]
    evidence = RouteBundleDrcEvidence(
        bundle_id=plan.bundle_id,
        bundle_base_revision=plan.base_revision,
        candidate_ids=tuple(
            sorted([forged_first] + [item.candidate_id for item in plan.candidates[1:]])
        ),
        source_revision=plan.base_revision,
        patched_board_revision="sha256:" + "b" * 64,
        patched_drc_context_revision="sha256:" + "c" * 64,
        summary=_summary(),
    )
    with pytest.raises(RouteBundleError, match="not bound to this plan"):
        RouteBundlePreview(
            status=RouteBundleStatus.ROUTED,
            board_path="test.kicad_pcb",
            board_revision=plan.base_revision,
            request=parse_route_bundle_request(_payload("test.kicad_pcb", FIXTURE.read_bytes())),
            snapshot_digest=plan.base_revision,
            plan=plan,
            drc_evidence=evidence,
        )


def test_machine_contract_defaults_include_drc_off() -> None:
    from copper_mcp.mcp_contracts import RouteBundleRequestContract

    assert (
        RouteBundleRequestContract.model_validate(
            {
                "board": "example.kicad_pcb",
                "layer": "F.Cu",
                "constraints": _constraints(),
                "net_ref_ids": ["net:name:" + "a" * 32, "net:name:" + "b" * 32],
                "expect_board_revision": "sha256:" + "a" * 64,
                "expect_snapshot_digest": "sha256:" + "b" * 64,
            }
        ).include_drc
        is False
    )
