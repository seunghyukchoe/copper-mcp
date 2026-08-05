from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc_oracle import probe_live_kicad_ipc

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"


class AuthenticationError(Exception):
    pass


class FutureVersionError(Exception):
    pass


class _Board:
    def __init__(self, source: str) -> None:
        self.source = source
        self.mutating_calls = 0

    def get_as_string(self) -> str:
        return self.source

    def __getattr__(self, name: str) -> Any:
        if name in {"begin_commit", "push_commit", "update_items", "create_items", "save"}:
            self.mutating_calls += 1
            raise AssertionError(f"oracle called forbidden mutator: {name}")
        raise AttributeError(name)


class _KiCad:
    def __init__(self, board: _Board, *, future: bool = False) -> None:
        self.board = board
        self.future = future
        self.closed = False

    @staticmethod
    def get_version() -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    @staticmethod
    def get_api_version() -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    def check_version(self) -> bool:
        if self.future:
            raise FutureVersionError()
        return True

    def get_board(self) -> _Board:
        return self.board

    def close(self) -> None:
        self.closed = True


def _settings() -> Settings:
    return Settings(workspace=ROOT)


def _environment(**values: str) -> dict[str, str]:
    result = {"KICAD_API_SOCKET": "/tmp/kicad/api.sock", "KICAD_API_TOKEN": "opaque-token"}
    result.update(values)
    return result


def test_oracle_skips_deterministically_when_not_launched_by_kicad() -> None:
    with patch.dict(os.environ, {"KICAD_API_SOCKET": "", "KICAD_API_TOKEN": ""}, clear=False):
        result = probe_live_kicad_ipc(_settings())

    assert result.status == "skipped"
    assert result.capability == "kicad_plugin_environment_absent"
    assert result.to_dict()["board_digest"] is None


def test_oracle_distinguishes_missing_socket_and_token() -> None:
    with patch.dict(os.environ, _environment(KICAD_API_SOCKET=""), clear=False):
        socket = probe_live_kicad_ipc(_settings())
    with patch.dict(os.environ, _environment(KICAD_API_TOKEN=""), clear=False):
        token = probe_live_kicad_ipc(_settings())

    assert (socket.status, socket.capability) == ("skipped", "kicad_api_socket_missing")
    assert (token.status, token.capability) == ("skipped", "kicad_api_token_missing")


def test_oracle_confirms_exact_capture_board_ir_and_scene_digests_without_mutating() -> None:
    board = _Board(FIXTURE.read_text(encoding="utf-8"))
    client = _KiCad(board)
    with patch.dict(os.environ, _environment(), clear=False):
        result = probe_live_kicad_ipc(_settings(), client_factory=lambda **_: client)

    assert result.status == "ready"
    assert result.capability == "live_source_board_ir_scene_fidelity_confirmed"
    assert result.digest_matches == {
        "source_matches_observation": True,
        "board_ir_source_matches_observation": True,
        "scene_source_matches_observation": True,
        "scene_snapshot_matches_board_ir": True,
    }
    assert result.board_digest == result.exact_source_digest
    assert result.board_ir_snapshot_digest == result.scene_snapshot_digest
    assert board.mutating_calls == 0
    assert client.closed is True
    encoded = json.dumps(result.to_dict(), sort_keys=True)
    assert "CopperMCP_ScenePad" not in encoded
    assert "opaque-token" not in encoded
    assert "api.sock" not in encoded


def test_oracle_classifies_authentication_and_version_failures_without_error_text() -> None:
    def authentication_factory(**_: object) -> _KiCad:
        raise AuthenticationError("secret token must not leak")

    board = _Board(FIXTURE.read_text(encoding="utf-8"))
    with patch.dict(os.environ, _environment(), clear=False):
        authentication = probe_live_kicad_ipc(_settings(), client_factory=authentication_factory)
        version = probe_live_kicad_ipc(
            _settings(), client_factory=lambda **_: _KiCad(board, future=True)
        )

    assert (authentication.status, authentication.capability) == (
        "refused",
        "kicad_token_or_session_rejected",
    )
    assert (version.status, version.capability) == ("refused", "kicad_version_mismatch")
    assert "secret token" not in repr(authentication.to_dict())


def test_oracle_distinguishes_invalid_endpoint_and_unreachable_or_busy_server() -> None:
    with patch.dict(
        os.environ,
        _environment(KICAD_API_SOCKET="tcp://127.0.0.1:9999"),
        clear=False,
    ):
        endpoint = probe_live_kicad_ipc(_settings(), client_factory=lambda **_: _KiCad(_Board("")))

    def unreachable_factory(**_: object) -> _KiCad:
        raise ConnectionError("private endpoint detail")

    with patch.dict(os.environ, _environment(), clear=False):
        unreachable = probe_live_kicad_ipc(_settings(), client_factory=unreachable_factory)

    assert (endpoint.status, endpoint.capability) == (
        "refused",
        "kicad_endpoint_configuration_invalid",
    )
    assert (unreachable.status, unreachable.capability) == (
        "refused",
        "kicad_api_server_unreachable_or_busy",
    )
    assert "private endpoint detail" not in repr(unreachable.to_dict())


def test_oracle_detects_a_session_change_after_capture_without_board_text() -> None:
    source = FIXTURE.read_text(encoding="utf-8")

    class SessionChangingBoard(_Board):
        calls = 0

        def get_as_string(self) -> str:
            self.calls += 1
            if self.calls == 2:
                os.environ["KICAD_API_TOKEN"] = "new-session-token"
            return source

    with patch.dict(os.environ, _environment(), clear=False):
        result = probe_live_kicad_ipc(
            _settings(), client_factory=lambda **_: _KiCad(SessionChangingBoard(source))
        )

    assert (result.status, result.capability) == ("refused", "kicad_session_changed")
    assert result.to_dict()["board_digest"] is None
