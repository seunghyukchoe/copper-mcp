from __future__ import annotations

import hashlib
import json
import shutil
import stat
import subprocess
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_cli as kicad_cli
import copper_mcp.request_boundary as request_boundary
import copper_mcp.route_preview as route_preview
from copper_mcp.board_ir import PointNM
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
from copper_mcp.routing import RouteConnection, RouteDiagnostic, RouteFailureCode

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


def test_preview_routes_around_a_zone_outline_without_mutating_the_source(
    tmp_path: Path,
) -> None:
    fixture = FIXTURE.parent / "blocked-zone.kicad_pcb"
    board = tmp_path / fixture.name
    board.write_bytes(fixture.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    before = _entries(tmp_path)

    first = preview_route(_request(board=fixture.name), settings)
    second = preview_route(_request(board=fixture.name), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.to_dict() == second.to_dict()
    assert first.candidate is not None
    assert first.candidate.cost.bend_count > 0
    # The POWER zone spans x=18..22 mm and y=11..19 mm. Its 0.375 mm
    # centreline margin includes route half-width and the governing clearance.
    assert all(
        not (17_625_000 < point.x < 22_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in first.candidate.patch.paths[0].vertices
    )
    assert _entries(tmp_path) == before


def _copy_fixture(tmp_path: Path, name: str) -> Settings:
    board = tmp_path / name
    board.write_bytes((FIXTURE.parent / name).read_bytes())
    return Settings(workspace=tmp_path, max_drc_report_bytes=4096)


def test_preview_reports_an_already_connected_net(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "connected-net.kicad_pcb")
    before = _entries(tmp_path)

    preview = preview_route(_request(board="connected-net.kicad_pcb"), settings)
    document = preview.to_dict()

    assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert preview.connection is not None
    assert preview.candidate is None
    assert preview.diagnostic is None
    assert preview.drc_evidence is None
    assert document["status"] == "already_connected"
    assert document["connection"]["attachment_segments"] == 1
    assert document["connection"]["component_objects"] == 3
    assert document["connection"]["base_revision"] == preview.snapshot_digest
    assert document["connection"]["start_pad_id"] != document["connection"]["end_pad_id"]
    assert _entries(tmp_path) == before


def test_already_connected_preview_skips_authoritative_drc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _copy_fixture(tmp_path, "connected-net.kicad_pcb")
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    monkeypatch.setattr(
        kicad_cli, "discover_kicad_cli", lambda settings: Path("/trusted/kicad-cli")
    )
    monkeypatch.setattr(subprocess, "run", unexpected_run)

    preview = preview_route(_request(board="connected-net.kicad_pcb", include_drc=True), settings)

    assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert preview.drc_evidence is None
    assert calls == 0


def test_preview_routes_around_an_octagonal_keepout(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "octagon-keepout.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="octagon-keepout.kicad_pcb"), settings)
    second = preview_route(_request(board="octagon-keepout.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    assert first.candidate.cost.bend_count > 0
    # The octagonal rule area spans x=17..23 mm and y=11..19 mm. Its 0.375 mm centreline
    # margin is the route half width plus the routed class clearance; a keepout carries no
    # net, so no second class clearance applies.
    assert all(
        not (16_625_000 < point.x < 23_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in first.candidate.patch.paths[0].vertices
    )
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_routes_around_a_foreign_diagonal_segment(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "diagonal-blocker.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="diagonal-blocker.kicad_pcb"), settings)
    second = preview_route(_request(board="diagonal-blocker.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    # The POWER diagonal runs (18, 11) to (22, 19) and crosses the straight corridor at
    # x=20 mm, so the previously refused board now detours instead.
    assert first.candidate.cost.bend_count > 0
    assert all(
        point.y <= 15_000_000 or point.y >= 19_375_000
        for point in first.candidate.patch.paths[0].vertices
    )
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_completes_a_partial_route_from_existing_copper(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "partial-route.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="partial-route.kicad_pcb"), settings)
    second = preview_route(_request(board="partial-route.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.connection is None
    assert first.candidate is not None
    # The committed stub already spans x=10..20 mm, so only the remaining 10 mm is proposed.
    assert first.candidate.patch.paths[0].vertices == (
        PointNM(20_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert first.candidate.cost.length_nm == 10_000_000
    assert first.candidate.cost.bend_count == 0
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


def test_preview_completes_a_route_from_a_diagonal_stub(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "diagonal-stub.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="diagonal-stub.kicad_pcb"), settings)
    second = preview_route(_request(board="diagonal-stub.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    # The stub runs diagonally from the start pad at (10, 15) to (16, 19); the proposal picks
    # it up at that far end and adds 18 mm instead of the 20 mm an empty board would need.
    assert first.candidate.patch.paths[0].vertices == (
        PointNM(16_000_000, 19_000_000),
        PointNM(16_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert first.candidate.cost.length_nm == 18_000_000
    assert first.to_dict() == second.to_dict()
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


def test_preview_deadline_covers_conversion_not_only_the_search(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    monkeypatch.setattr(route_preview, "time", _AdvancingClock())

    preview = preview_route(_request(), replace(settings, max_route_preview_seconds=1))

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.CANCELLED
    assert "conversion" in preview.diagnostic.message


def test_preview_clamps_the_kicad_timeout_to_the_remaining_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, settings = _workspace(tmp_path)
    observed: dict[str, int] = {}

    def capture(path: str, candidate: object, profile: object, drc_settings: Settings) -> object:
        observed["timeout"] = drc_settings.kicad_timeout_seconds
        raise KiCadCliError("stop after capturing the clamped budget")

    monkeypatch.setattr(route_preview, "run_route_candidate_drc", capture)

    with pytest.raises(KiCadCliError):
        preview_route(
            _request(include_drc=True),
            replace(settings, kicad_timeout_seconds=120, max_route_preview_seconds=5),
        )

    assert 1 <= observed["timeout"] <= 5


def test_drc_budget_refuses_to_start_once_the_deadline_is_spent(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    spent = time.monotonic() - 1.0

    with pytest.raises(RoutePreviewError, match="deadline expired"):
        route_preview._drc_settings(settings, spent)


def test_drc_budget_never_exceeds_the_configured_kicad_timeout(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    generous = time.monotonic() + 10_000.0

    clamped = route_preview._drc_settings(replace(settings, kicad_timeout_seconds=30), generous)

    assert clamped.kicad_timeout_seconds == 30


def test_preview_errors_never_echo_caller_supplied_field_names() -> None:
    secret = "x" * 5000 + "-corporate-secret-net"

    with pytest.raises(RoutePreviewError) as raised:
        parse_route_preview_request(_request(**{secret: 1}))

    message = str(raised.value)
    assert secret not in message
    assert "corporate-secret" not in message
    assert len(message) < 500
    assert "1 unsupported field" in message


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
    assert request.profile().default_net_class_id == request_boundary.NET_CLASS_ID
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


def test_preview_record_rejects_inconsistent_connection_bindings(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    preview = preview_route(_request(), settings)
    assert preview.candidate is not None
    assert preview.snapshot_digest is not None
    connection = RouteConnection(
        base_revision=preview.snapshot_digest,
        start_pad_id=preview.candidate.start_pad_id,
        end_pad_id=preview.candidate.end_pad_id,
        attachment_segments=1,
        component_objects=3,
    )

    with pytest.raises(RoutePreviewError, match="exactly one connection"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=connection,
            candidate=preview.candidate,
        )
    with pytest.raises(RoutePreviewError, match="exactly one connection"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=connection,
            diagnostic=RouteDiagnostic(code=RouteFailureCode.NO_PATH, message="no path"),
        )
    with pytest.raises(RoutePreviewError, match="previewed Board IR snapshot"):
        RoutePreview(
            status=RoutePreviewStatus.ALREADY_CONNECTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            connection=replace(connection, base_revision=preview.board_revision),
        )
    with pytest.raises(RoutePreviewError, match="exactly one candidate"):
        RoutePreview(
            status=RoutePreviewStatus.ROUTED,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            snapshot_digest=preview.snapshot_digest,
            candidate=preview.candidate,
            connection=connection,
        )
    with pytest.raises(RoutePreviewError, match="routing outcome"):
        RoutePreview(
            status=RoutePreviewStatus.UNSUPPORTED_BOARD,
            board_path=preview.board_path,
            board_revision=preview.board_revision,
            request=preview.request,
            connection=connection,
            conversion_diagnostic_counts={"geometry.missing": 1},
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


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_existing_copper(tmp_path: Path) -> None:
    board = tmp_path / "blocked-pad.kicad_pcb"
    board.write_bytes((FIXTURE.parent / "blocked-pad.kicad_pcb").read_bytes())
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="blocked-pad.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    # A straight route would cross the 2 mm x 8 mm POWER pad centred between the endpoints.
    assert preview.candidate.cost.bend_count > 0
    assert all(
        not (18_625_000 < point.x < 21_375_000 and 10_625_000 < point.y < 19_375_000)
        for point in preview.candidate.patch.paths[0].vertices
    )
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_a_foreign_diagonal(
    tmp_path: Path,
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "diagonal-blocker.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="diagonal-blocker.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.cost.bend_count > 0
    # A straight route across this board makes KiCad report `tracks_crossing` as an error, so
    # a clean report is evidence the conservative envelope really did divert the route.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_detoured_around_an_octagonal_keepout(
    tmp_path: Path,
) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "octagon-keepout.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="octagon-keepout.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.cost.bend_count > 0
    # A straight route through this rule area makes KiCad report `items_not_allowed` as an
    # error, so a clean report here is evidence the detour is real and not self-graded.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_route_completed_off_a_diagonal_stub(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "diagonal-stub.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="diagonal-stub.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.patch.paths[0].vertices[0] == PointNM(16_000_000, 19_000_000)
    # This is the check that the under-approximating diagonal core is sound at the attachment
    # point: displacing the same proposal by 0.5 mm so it misses the stub end makes KiCad
    # report two `track_dangling` warnings and one unconnected item, so a clean report here is
    # evidence the chosen start really does sit on existing copper.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_completed_partial_route(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "partial-route.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="partial-route.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert preview.candidate.patch.paths[0].vertices == (
        PointNM(20_000_000, 15_000_000),
        PointNM(30_000_000, 15_000_000),
    )
    assert preview.drc_evidence is not None
    # New copper overlapping same-net copper is legal, and attaching at the stub's own
    # endpoint leaves no dangling tail, so KiCad reports nothing at all.
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before


COPPERTONE_BOARD = (
    Path(__file__).parent.parent / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
)


# Every `F.Cu` net the router can currently resolve, as (pad count, same-net segment count).
# The RAW nets are the ones whose copper includes diagonals; the wider ones only became
# answerable once connectivity stopped being a two-pin-only question.
COPPERTONE_CONNECTED_NETS = {
    "9V_RAW": (2, 2),
    "L_IN_RAW": (2, 3),
    "R_IN_RAW": (2, 2),
    "L_ISO": (2, 1),
    "R_ISO": (2, 1),
    "L_BUF": (3, 3),
    "R_BUF": (3, 3),
    "L_IN_BIASED": (3, 3),
    "R_IN_BIASED": (3, 3),
    "R_OUT": (3, 4),
    "VREF": (7, 10),
}
# Refused because they carry vias, which this model does not represent as connectivity.
COPPERTONE_VIA_NETS = ("GND", "L_OUT", "VCC")


@pytest.mark.parametrize(("net_name", "shape"), sorted(COPPERTONE_CONNECTED_NETS.items()))
def test_coppertone_connected_nets_report_already_connected(
    net_name: str, shape: tuple[int, int], tmp_path: Path
) -> None:
    pad_count, segments = shape
    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    request = _request(board=COPPERTONE_BOARD.name, net=net_name)

    first = preview_route(request, settings)
    second = preview_route(request, settings)

    assert first.status is RoutePreviewStatus.ALREADY_CONNECTED
    assert first.to_dict() == second.to_dict()
    assert first.connection is not None
    assert first.connection.pad_count == pad_count
    assert first.connection.attachment_segments == segments
    assert first.connection.component_objects == segments + pad_count


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_corroborates_the_coppertone_already_connected_nets(tmp_path: Path) -> None:
    """KiCad's own connectivity report is the evidence behind the already-connected claim.

    An already-connected preview emits no candidate, so there is nothing for candidate-bound
    DRC to replay. The authoritative check available is the board-level one: KiCad reporting
    zero unconnected items means every net on this board, including both ISO nets, is fully
    connected — which is exactly what the preview claims for them.
    """

    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )

    summary = kicad_cli.run_board_drc(COPPERTONE_BOARD.name, settings)

    assert summary.unconnected_count == 0
    assert summary.error_count == 0
    for net_name in COPPERTONE_CONNECTED_NETS:
        preview = preview_route(_request(board=COPPERTONE_BOARD.name, net=net_name), settings)
        assert preview.status is RoutePreviewStatus.ALREADY_CONNECTED


@pytest.mark.parametrize("net_name", COPPERTONE_VIA_NETS)
def test_coppertone_via_carrying_nets_stay_refused(net_name: str, tmp_path: Path) -> None:
    """A via is copper this model cannot see, so those nets are never claimed connected."""

    board = tmp_path / COPPERTONE_BOARD.name
    board.write_bytes(COPPERTONE_BOARD.read_bytes())
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)

    preview = preview_route(_request(board=COPPERTONE_BOARD.name, net=net_name), settings)

    assert preview.status is RoutePreviewStatus.NOT_ROUTED
    assert preview.connection is None
    assert preview.diagnostic is not None
    assert preview.diagnostic.code is RouteFailureCode.INVALID_TWO_PIN_NET


def test_preview_routes_a_four_pad_net_as_a_tree(tmp_path: Path) -> None:
    settings = _copy_fixture(tmp_path, "tree-star.kicad_pcb")
    before = _entries(tmp_path)

    first = preview_route(_request(board="tree-star.kicad_pcb"), settings)
    second = preview_route(_request(board="tree-star.kicad_pcb"), settings)

    assert first.status is RoutePreviewStatus.ROUTED
    assert first.candidate is not None
    assert first.candidate.pad_count == 4
    assert first.candidate.ordering_policy == "component-mst-v1"
    # Four isolated pads are four components, so a spanning tree is exactly three merges.
    assert len(first.candidate.patch.paths) == 3
    document = first.to_dict()
    assert len(document["candidate"]["patch"]["paths"]) == 3
    assert document["candidate"]["pad_count"] == 4
    assert first.to_dict() == second.to_dict()
    assert _entries(tmp_path) == before


@pytest.mark.skipif(
    not REAL_KICAD_CLI.is_file(),
    reason="requires a locally installed KiCad CLI",
)
def test_real_kicad_confirms_a_multi_pin_tree(tmp_path: Path) -> None:
    settings = replace(
        _copy_fixture(tmp_path, "tree-star.kicad_pcb"),
        kicad_cli=REAL_KICAD_CLI,
        max_drc_report_bytes=8 * 1024 * 1024,
    )
    before = _entries(tmp_path)

    preview = preview_route(
        _request(board="tree-star.kicad_pcb", include_drc=True),
        settings,
    )

    assert preview.status is RoutePreviewStatus.ROUTED
    assert preview.candidate is not None
    assert len(preview.candidate.patch.paths) == 3
    # Discriminating: rendering the same board with any one leg removed makes KiCad report an
    # unconnected item, so a clean report here is evidence the tree really does connect the
    # net rather than evidence that little copper was added.
    assert preview.drc_evidence is not None
    assert preview.drc_evidence.summary.error_count == 0
    assert preview.drc_evidence.summary.warning_count == 0
    assert preview.drc_evidence.summary.unconnected_count == 0
    assert preview.drc_evidence.summary.passed is True
    assert _entries(tmp_path) == before
