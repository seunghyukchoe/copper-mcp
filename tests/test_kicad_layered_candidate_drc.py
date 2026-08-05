from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_cli as kicad_cli
from copper_mcp.adapters import (
    KiCadConstraintProfile,
    parse_kicad_bytes,
    render_kicad_layered_candidate_board,
)
from copper_mcp.board_ir import BoardIRSnapshot, NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, run_layered_route_candidate_drc
from copper_mcp.routing import (
    LayeredAStarSettings,
    LayeredBoardRouter,
    LayeredRouteCandidate,
    LayeredRouteRequest,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
F_CU = "layer:F.Cu"
B_CU = "layer:B.Cu"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate() -> tuple[
    bytes,
    KiCadConstraintProfile,
    BoardIRSnapshot,
    LayeredRouteRequest,
    LayeredRouteCandidate,
]:
    source = FIXTURE.read_bytes()
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    pads = tuple(
        pad for pad in snapshot.content.pads if pad.net_id == snapshot.content.pads[0].net_id
    )
    assert isinstance(pads[0].net_id, str)
    request = LayeredRouteRequest(
        board_revision=snapshot.snapshot_digest,
        net_id=pads[0].net_id,
        start_pad_id=pads[0].id,
        end_pad_id=pads[1].id,
        start_layer_id=F_CU,
        end_layer_id=F_CU,
        grid_step_nm=1_000,
        settings=LayeredAStarSettings(via_cost=2),
    )
    result = LayeredBoardRouter().propose(snapshot, request)
    assert result.diagnostic is None
    assert result.candidate is not None
    return source, profile, snapshot, request, result.candidate


def _finding(violation_type: str, severity: str) -> dict[str, object]:
    return {
        "type": violation_type,
        "severity": severity,
        "description": "private test finding",
        "items": [{"description": "private geometry"}],
        "excluded": False,
    }


def _report(
    source: str,
    *,
    violations: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": source,
        "date": "2026-08-05T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": violations or [],
        "unconnected_items": [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }


def _fake_completed_run(
    capture: dict[str, Any],
) -> Any:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        report_path = Path(command[command.index("--output") + 1])
        snapshot_board = Path(command[-1])
        report_path.write_text(json.dumps(_report(snapshot_board.name)), encoding="utf-8")
        capture["command"] = command
        capture["snapshot_bytes"] = snapshot_board.read_bytes()
        capture["temporary_root"] = report_path.parent
        capture["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0)

    return run


def _install_fake_kicad(
    monkeypatch: pytest.MonkeyPatch,
    run: Any,
) -> None:
    monkeypatch.setattr(
        kicad_cli,
        "discover_kicad_cli",
        lambda settings: Path("/trusted/kicad-cli"),
    )
    monkeypatch.setattr(subprocess, "run", run)


def test_binds_layered_candidate_to_private_drc_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, profile, snapshot, request, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, board)
    capture: dict[str, Any] = {}
    _install_fake_kicad(monkeypatch, _fake_completed_run(capture))

    evidence = run_layered_route_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        request=request,
    )

    rendered = render_kicad_layered_candidate_board(
        source,
        snapshot,
        candidate,
        profile,
        request=request,
    )
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.candidate_base_revision == candidate.base_revision
    assert evidence.source_revision.startswith("sha256:")
    assert evidence.patched_board_revision.startswith("sha256:")
    assert evidence.patched_drc_context_revision.startswith("sha256:")
    assert evidence.summary.base_revision == evidence.patched_board_revision
    assert evidence.summary.drc_context_revision == evidence.patched_drc_context_revision
    assert evidence.summary.passed
    assert capture["snapshot_bytes"] == rendered
    assert capture["temporary_root"].exists() is False
    assert board.read_bytes() == source


def test_warning_only_authority_is_bound_but_not_advertised_as_clean(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, profile, _, request, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, board)

    def warning_run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        del kwargs
        report_path = Path(command[command.index("--output") + 1])
        report_path.write_text(
            json.dumps(
                _report(
                    Path(command[-1]).name,
                    violations=[_finding("courtyard_overlap", "warning")],
                )
            ),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 5)

    _install_fake_kicad(monkeypatch, warning_run)
    evidence = run_layered_route_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        request=request,
    )

    assert evidence.summary.passed is True
    assert evidence.summary.clean is False
    assert evidence.summary.warning_count == 1
    assert evidence.summary.violation_type_counts == {"courtyard_overlap": 1}


def test_rejects_stale_or_malformed_layered_candidates_before_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, profile, _, request, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, board)
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    _install_fake_kicad(monkeypatch, unexpected_run)
    stale = candidate.__class__(
        candidate_id=candidate.candidate_id,
        base_revision=f"sha256:{'1' * 64}",
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        patch=candidate.patch,
        cost=candidate.cost,
        metrics=candidate.metrics,
        settings=candidate.settings,
        router_version=candidate.router_version,
        policy=candidate.policy,
        seed=candidate.seed,
    )
    with pytest.raises(KiCadCliError, match="stale"):
        run_layered_route_candidate_drc(
            board.name,
            stale,
            profile,
            Settings(workspace=tmp_path),
            request=request,
        )
    with pytest.raises(KiCadCliError, match="malformed"):
        run_layered_route_candidate_drc(
            board.name,
            object(),  # type: ignore[arg-type]
            profile,
            Settings(workspace=tmp_path),
            request=request,
        )
    assert calls == 0


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_drc_accepts_private_layered_candidate_without_source_mutation(
    tmp_path: Path,
) -> None:
    source, profile, _, request, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, board)
    before_stat = board.stat()

    evidence = run_layered_route_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI),
        request=request,
    )

    after_stat = board.stat()
    assert evidence.summary.passed
    assert evidence.summary.error_count == 0
    assert evidence.summary.warning_count == 0
    assert evidence.summary.unconnected_count == 0
    assert evidence.summary.kicad_version.startswith("10.")
    assert evidence.patched_board_revision.startswith("sha256:")
    assert board.read_bytes() == source
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
