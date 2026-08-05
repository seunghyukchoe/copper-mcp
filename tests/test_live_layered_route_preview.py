from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_ipc as kicad_ipc
import copper_mcp.live_layered_route_preview as live_preview
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.layered_route_preview import LayeredRoutePreviewError, preview_layered_route

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
SESSION_TOKEN = "copper-mcp-test-kicad-session"


@pytest.fixture(autouse=True)
def _fake_kicad_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_KEY", b"\x42" * 32)


class _FakeVersion:
    major = 10
    minor = 0
    patch = 5


class _FakeLiveBoard:
    def __init__(self, source: str) -> None:
        self._source = source

    def get_as_string(self) -> str:
        return self._source


class _FakeLiveKiCad:
    def __init__(self, source: str) -> None:
        self._board = _FakeLiveBoard(source)

    def get_version(self) -> _FakeVersion:
        return _FakeVersion()

    def get_api_version(self) -> _FakeVersion:
        return _FakeVersion()

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _FakeLiveBoard:
        return self._board


def _workspace(
    tmp_path: Path,
    fixture: Path = FIXTURE,
) -> tuple[Settings, str, str, str, str]:
    source = fixture.read_bytes()
    board = tmp_path / fixture.name
    board.write_bytes(source)
    constraints = {
        "clearance_nm": 250_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 800_000,
        "via_drill_nm": 400_000,
    }
    profile = KiCadConstraintProfile(
        net_classes=(NetClass(id="class:request", name="Request", **constraints),),
        default_net_class_id="class:request",
    )
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    pads = conversion.snapshot.content.pads
    board_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    return (
        Settings(workspace=tmp_path),
        pads[0].id,
        pads[1].id,
        board_revision,
        conversion.snapshot.snapshot_digest,
    )


def _request(
    start_pad_id: str,
    end_pad_id: str,
    board_revision: str,
    snapshot_digest: str,
    **overrides: Any,
) -> dict[str, object]:
    session_revision = kicad_ipc._session_revision()
    assert session_revision is not None
    request: dict[str, object] = {
        "board": "live",
        "start_pad_id": start_pad_id,
        "end_pad_id": end_pad_id,
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
        "expect_board_revision": board_revision,
        "expect_snapshot_digest": snapshot_digest,
        "expect_session_revision": session_revision,
        "grid_step_nm": 250_000,
        "seed": 23,
    }
    request.update(overrides)
    return request


def test_live_session_revision_is_keyed_stable_and_token_distinct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    first = kicad_ipc._session_revision()
    second = kicad_ipc._session_revision()
    assert first is not None
    assert first == second
    assert first.startswith("hmac-sha256:")
    assert first != "sha256:" + hashlib.sha256(SESSION_TOKEN.encode()).hexdigest()

    monkeypatch.setenv("KICAD_API_TOKEN", "other-kicad-session")
    assert kicad_ipc._session_revision() != first


def test_live_preview_refuses_a_session_revision_from_a_rotated_process_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    previous = kicad_ipc._session_revision()
    assert previous is not None
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_KEY", b"\x43" * 32)
    request = _request(
        start,
        end,
        board_revision,
        snapshot_digest,
        expect_session_revision=previous,
    )

    def unexpected_conversion(*_: object, **__: object) -> object:
        raise AssertionError("rotated process key must refuse before Board IR conversion")

    monkeypatch.setattr(live_preview, "parse_kicad_bytes", unexpected_conversion)
    result = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(FIXTURE.read_bytes()),
    )

    assert result["status"] == "not_routed"
    assert result["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]


def test_live_request_refuses_legacy_unkeyed_session_revision() -> None:
    with pytest.raises(LayeredRoutePreviewError, match="hmac-sha256"):
        live_preview.parse_live_layered_route_preview_request(
            {
                "board": "live",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
                "expect_session_revision": "sha256:" + "0" * 64,
            }
        )


def _factory(source: bytes):
    text = source.decode("utf-8")
    return lambda **_: _FakeLiveKiCad(text)


def test_live_layered_preview_reuses_exact_ipc_snapshot_and_is_deterministic(
    tmp_path: Path,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, board_revision, snapshot_digest)
    source = FIXTURE.read_bytes()

    first = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(source),
    )
    second = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(source),
    )

    assert first == second
    assert first["status"] == "routed"
    assert first["board_path"] == "live"
    assert first["board_revision"] == board_revision
    assert first["snapshot_digest"] == snapshot_digest
    assert first["request"]["board"] == "live"  # type: ignore[index]
    assert first["candidate"] is not None
    assert SESSION_TOKEN not in str(first)

    file_request = dict(request)
    file_request["board"] = FIXTURE.name
    file_request.pop("expect_session_revision")
    file_settings = Settings(workspace=tmp_path)
    file_result = preview_layered_route(file_request, file_settings)
    assert first["candidate"] == file_result["candidate"]


def test_live_layered_preview_can_propose_a_two_layer_via_route(tmp_path: Path) -> None:
    fixture = FIXTURE.parent / "blocked-pad.kicad_pcb"
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path, fixture)

    result = live_preview.preview_live_layered_route(
        _request(start, end, board_revision, snapshot_digest),
        settings,
        client_factory=_factory(fixture.read_bytes()),
    )

    assert result["status"] == "routed"
    candidate = result["candidate"]
    assert isinstance(candidate, dict)
    patch = candidate["patch"]
    assert isinstance(patch, dict)
    assert len(patch["vias"]) == 2
    assert all(via["start_layer_id"] != via["end_layer_id"] for via in patch["vias"])


def test_live_preview_refuses_stale_board_before_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, _, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, "sha256:" + "0" * 64, snapshot_digest)
    source = FIXTURE.read_bytes()

    def unexpected_conversion(*_: object, **__: object) -> object:
        raise AssertionError("stale live source must be refused before Board IR conversion")

    monkeypatch.setattr(live_preview, "parse_kicad_bytes", unexpected_conversion)
    result = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(source),
    )

    assert result["status"] == "not_routed"
    assert result["snapshot_digest"] is None
    assert result["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]


def test_live_preview_rejects_authoritative_drc_opt_in(
    tmp_path: Path,
) -> None:
    _settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, board_revision, snapshot_digest, include_drc=True)

    with pytest.raises(LayeredRoutePreviewError, match="cannot request authoritative DRC"):
        live_preview.parse_live_layered_route_preview_request(request)


def test_live_preview_refuses_stale_session_before_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, board_revision, snapshot_digest)
    request["expect_session_revision"] = "hmac-sha256:" + "0" * 64

    def unexpected_conversion(*_: object, **__: object) -> object:
        raise AssertionError("stale live session must be refused before Board IR conversion")

    monkeypatch.setattr(live_preview, "parse_kicad_bytes", unexpected_conversion)
    result = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(FIXTURE.read_bytes()),
    )

    assert result["status"] == "not_routed"
    assert result["snapshot_digest"] is None
    assert result["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]


def test_live_preview_checks_snapshot_cas_after_conversion(tmp_path: Path) -> None:
    settings, start, end, board_revision, _ = _workspace(tmp_path)
    request = _request(start, end, board_revision, "sha256:" + "0" * 64)

    result = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(FIXTURE.read_bytes()),
    )

    assert result["status"] == "not_routed"
    assert result["snapshot_digest"] is not None
    assert result["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]


def test_live_preview_does_not_write_workspace(tmp_path: Path) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    before = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }

    live_preview.preview_live_layered_route(
        _request(start, end, board_revision, snapshot_digest),
        settings,
        client_factory=_factory(FIXTURE.read_bytes()),
    )

    after = {
        path.relative_to(tmp_path).as_posix(): path.read_bytes()
        for path in tmp_path.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_live_preview_passes_remaining_route_budget_to_ipc(tmp_path: Path) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    settings = Settings(workspace=tmp_path, max_route_preview_seconds=1)
    seen: dict[str, object] = {}

    def factory(**kwargs: object) -> _FakeLiveKiCad:
        seen.update(kwargs)
        return _FakeLiveKiCad(FIXTURE.read_text(encoding="utf-8"))

    live_preview.preview_live_layered_route(
        _request(start, end, board_revision, snapshot_digest),
        settings,
        client_factory=factory,
    )

    timeout_ms = seen["timeout_ms"]
    assert isinstance(timeout_ms, int)
    assert 1 <= timeout_ms <= 1_000


def test_live_request_requires_exact_live_sentinel() -> None:
    with pytest.raises(LayeredRoutePreviewError, match="board to 'live'"):
        live_preview.parse_live_layered_route_preview_request(
            {
                "board": "board.kicad_pcb",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
            }
        )


def test_live_request_rejects_raw_net_selector() -> None:
    with pytest.raises(LayeredRoutePreviewError):
        live_preview.parse_live_layered_route_preview_request(
            {
                "board": "live",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
                "net": "PRIVATE_NET",
            }
        )


def test_live_request_requires_session_cas_before_ipc() -> None:
    with pytest.raises(LayeredRoutePreviewError, match="expect_session_revision"):
        live_preview.parse_live_layered_route_preview_request(
            {
                "board": "live",
                "start_pad_id": "pad:a",
                "end_pad_id": "pad:b",
                "constraints": {
                    "clearance_nm": 250_000,
                    "track_width_nm": 250_000,
                    "via_diameter_nm": 800_000,
                    "via_drill_nm": 400_000,
                },
                "expect_board_revision": "sha256:" + "0" * 64,
                "expect_snapshot_digest": "sha256:" + "0" * 64,
            }
        )
