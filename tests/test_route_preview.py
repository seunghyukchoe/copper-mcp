from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_cli as kicad_cli
import copper_mcp.route_preview as route_preview
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, RouteCandidateDrcEvidence
from copper_mcp.models import DrcSummary
from copper_mcp.route_preview import (
    RoutePreview,
    RoutePreviewError,
    RoutePreviewStatus,
    parse_route_preview_request,
    preview_route,
)
from copper_mcp.routing import RouteDiagnostic, RouteFailureCode

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "board": "two-pad.kicad_pcb",
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }
    request.update(overrides)
    return request


def _workspace(tmp_path: Path, *, source: bytes | None = None) -> tuple[Path, Settings]:
    board = tmp_path / "two-pad.kicad_pcb"
    board.write_bytes(source if source is not None else FIXTURE.read_bytes())
    return board, Settings(workspace=tmp_path, max_drc_report_bytes=4096)


def _entries(root: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _report(source: str) -> dict[str, object]:
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": source,
        "date": "2026-08-03T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": [],
        "unconnected_items": [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }


def _install_fake_kicad(
    monkeypatch: pytest.MonkeyPatch,
    capture: dict[str, Any] | None = None,
) -> None:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert "--save-board" not in command
        assert "--refill-zones" not in command
        snapshot_board = Path(command[-1])
        report_path = Path(command[command.index("--output") + 1])
        report_path.write_text(json.dumps(_report(snapshot_board.name)), encoding="utf-8")
        if capture is not None:
            capture["snapshot_bytes"] = snapshot_board.read_bytes()
            capture["temporary_mode"] = stat.S_IMODE(report_path.parent.stat().st_mode)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        kicad_cli, "discover_kicad_cli", lambda settings: Path("/trusted/kicad-cli")
    )
    monkeypatch.setattr(subprocess, "run", run)


def test_previews_a_deterministic_candidate_bound_to_the_board_revision(tmp_path: Path) -> None:
    board, settings = _workspace(tmp_path)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.diagnostic is None
    assert preview.drc_evidence is None
    assert preview.board_path == "two-pad.kicad_pcb"
    assert preview.board_revision == f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    assert preview.snapshot_digest == preview.candidate.base_revision
    assert preview.board_revision != preview.snapshot_digest
    assert preview.candidate.patch.layer_id == "layer:F.Cu"
    assert preview.candidate.metrics.hard_internal_violations == 0
    assert preview_route(_request(), settings).to_dict() == preview.to_dict()


def test_preview_serialization_is_detached_from_validated_state(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(), settings)
    document = preview.to_dict()
    document["status"] = "tampered"
    document["candidate"]["cost"]["total_cost_nm"] = 1

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.to_dict()["status"] == "routed"
    assert preview.to_dict()["candidate"]["cost"]["total_cost_nm"] != 1
    with pytest.raises(TypeError):
        preview.conversion_diagnostic_counts["injected"] = 1  # type: ignore[index]


def test_preview_does_not_touch_the_workspace(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    (tmp_path / "two-pad.kicad_dru").write_text("(version 1)", encoding="utf-8")
    before = _entries(tmp_path)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert _entries(tmp_path) == before


def test_preview_reports_a_diagnostic_instead_of_routing_off_grid(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(settings={"grid_step_nm": 300_000}), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.candidate is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.OFF_GRID
    assert preview.snapshot_digest is not None
    assert preview.to_dict()["diagnostic"]["code"] == "off_grid"


def test_preview_reports_a_diagnostic_for_an_unknown_net(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    preview = preview_route(_request(net="MISSING"), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.INVALID_TWO_PIN_NET


class _AdvancingClock:
    """A monotonic clock that always advances past any preview deadline."""

    def __init__(self) -> None:
        self.value = 0.0

    def monotonic(self) -> float:
        self.value += 10.0
        return self.value


def test_preview_cancels_deterministically_at_the_configured_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(route_preview, "time", _AdvancingClock())

    preview = preview_route(_request(), replace(settings, max_route_preview_seconds=1))

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.CANCELLED


def test_preview_fails_closed_on_a_board_outside_the_supported_subset(tmp_path: Path) -> None:
    unsupported = FIXTURE.read_bytes().replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    _, settings = _workspace(tmp_path, source=unsupported)

    preview = preview_route(_request(), settings)

    assert preview.status is RoutePreviewStatus.UNSUPPORTED_BOARD
    assert preview.candidate is None
    assert preview.diagnostic is None
    assert preview.snapshot_digest is None
    assert dict(preview.conversion_diagnostic_counts) == {"geometry.missing": 1}


def test_preview_rejects_boards_outside_the_workspace(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    with pytest.raises(ValueError, match="workspace"):
        preview_route(_request(board="../two-pad.kicad_pcb"), settings)


@pytest.mark.parametrize(
    "payload",
    [
        "not-an-object",
        {"net": "AUDIO", "layer": "F.Cu", "constraints": {}},
        _request(unexpected=1),
        _request(board=""),
        _request(net="AUD\x00IO"),
        _request(layer="F.Silkscreen"),
        _request(layer="F.Cu\n"),
        _request(seed=-1),
        _request(seed=True),
        _request(seed="23"),
        _request(include_drc="yes"),
        _request(constraints={"clearance_nm": 1}),
        _request(constraints={"clearance_nm": 1, "extra": 2}),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": 0,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            }
        ),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 400_000,
                "via_drill_nm": 400_000,
            }
        ),
        _request(settings={"unknown_budget": 1}),
        _request(settings={"grid_step_nm": 0}),
        _request(settings={"max_expansions": 1 << 40}),
        _request(settings={"grid_step_nm": True}),
        _request(settings=[]),
    ],
)
def test_preview_rejects_malformed_requests(payload: Any) -> None:
    with pytest.raises(RoutePreviewError):
        parse_route_preview_request(payload)


def test_request_normalization_exposes_only_validated_fields() -> None:
    request = parse_route_preview_request(_request())

    assert request.layer_id == "layer:F.Cu"
    assert request.net_id.startswith("net:name:")
    assert request.profile().default_net_class_id == route_preview.PREVIEW_NET_CLASS_ID
    assert set(request.to_dict()) == {
        "board",
        "net",
        "layer",
        "seed",
        "include_drc",
        "constraints",
        "settings",
    }


def test_preview_binds_optional_authoritative_drc_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, settings = _workspace(tmp_path)
    capture: dict[str, Any] = {}
    _install_fake_kicad(monkeypatch, capture)
    before = _entries(tmp_path)

    preview = preview_route(_request(include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.candidate_id == preview.candidate.candidate_id
    assert preview.drc_evidence.source_revision == preview.board_revision
    assert preview.drc_evidence.summary.passed is True
    assert capture["snapshot_bytes"] != board.read_bytes()
    assert capture["temporary_mode"] == 0o700
    assert _entries(tmp_path) == before


def test_preview_fails_closed_when_authoritative_drc_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(
        kicad_cli,
        "discover_kicad_cli",
        _raise_missing_kicad,
    )

    with pytest.raises(KiCadCliError):
        preview_route(_request(include_drc=True), settings)


def _raise_missing_kicad(settings: Settings) -> Path:
    raise KiCadCliError("KiCad CLI was not found; set COPPER_MCP_KICAD_CLI")


def test_preview_record_rejects_inconsistent_bindings(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    preview = preview_route(_request(), settings)
    assert preview.candidate is not None

    with pytest.raises(RoutePreviewError, match="Board IR snapshot"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.board_revision,
            candidate=preview.candidate,
        )
    with pytest.raises(RoutePreviewError, match="exactly one candidate"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
        )
    with pytest.raises(RoutePreviewError, match="conversion diagnostics"):
        RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
        )
    with pytest.raises(RoutePreviewError, match="routed candidate"):
        RoutePreview(
            status=RoutePreviewStatus.NOT_ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            diagnostic=RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path"),
            drc_evidence=_evidence(preview),
        )


def _evidence(preview: RoutePreview) -> Any:
    assert preview.candidate is not None
    return RouteCandidateDrcEvidence(
        candidate_id=preview.candidate.candidate_id,
        candidate_base_revision=preview.candidate.base_revision,
        source_revision=preview.board_revision,
        patched_board_revision=preview.board_revision,
        patched_drc_context_revision=preview.board_revision,
        summary=DrcSummary(
            base_revision=preview.board_revision,
            drc_context_revision=preview.board_revision,
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
        ),
    )


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_the_previewed_candidate_without_mutating_the_source(
    tmp_path: Path,
) -> None:
    _, settings = _workspace(tmp_path)
    settings = replace(settings, kicad_cli=REAL_KICAD_CLI, max_drc_report_bytes=8 * 1024 * 1024)
    before = _entries(tmp_path)

    preview = preview_route(_request(include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before
