from __future__ import annotations

import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    KicadIpcConnectionError,
    KicadIpcPayloadError,
    capture_live_editor_context,
)
from copper_mcp.live_editor_context import (
    LiveEditorContextError,
    inspect_live_editor_context_raw,
)
from copper_mcp.request_boundary import RequestError

ROOT = Path(__file__).resolve().parents[1]


class Pad:
    def __init__(self, value: str) -> None:
        self.id = SimpleNamespace(value=value)


class Track:
    def __init__(self, value: str) -> None:
        self.id = SimpleNamespace(value=value)


class Unknown:
    def __init__(self, value: str) -> None:
        self.id = SimpleNamespace(value=value)


class _Board:
    source = "(kicad_pcb (version 20260206) (layers))"

    def __init__(self, selection: list[object] | None = None) -> None:
        self.active_layer = 0
        self.layer_name = "F.Cu"
        self.selection = selection or []
        self.writes: list[str] = []

    def get_as_string(self) -> str:
        return self.source

    def get_active_layer(self) -> int:
        return self.active_layer

    def get_layer_name(self, layer: int) -> str:
        assert layer == self.active_layer
        return self.layer_name

    def get_selection(self) -> list[object]:
        return list(self.selection)

    def get_selection_as_string(self) -> str:
        raise AssertionError("raw selection text must never be read")

    def __getattr__(self, name: str) -> object:
        if name in {"save", "set_active_layer", "update_items", "remove_items"}:
            self.writes.append(name)
            raise AssertionError(f"mutating IPC method called: {name}")
        raise AttributeError(name)


class _KiCad:
    def __init__(self, board: _Board) -> None:
        self.board = board

    def get_version(self) -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    def get_api_version(self) -> SimpleNamespace:
        return SimpleNamespace(major=10, minor=0, patch=5)

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _Board:
        return self.board


def _settings() -> Settings:
    return Settings(workspace=ROOT)


def _factory(board: _Board):
    return lambda **_: _KiCad(board)


class LiveEditorContextTests(unittest.TestCase):
    def test_deterministic_active_layer_and_native_selection(self) -> None:
        board = _Board(
            [
                Track("22222222-2222-2222-2222-222222222222"),
                Pad("11111111-1111-1111-1111-111111111111"),
            ]
        )
        captured = capture_live_editor_context(_settings(), client_factory=_factory(board))
        request = {
            "board": "live",
            "expect_board_revision": captured.board_digest,
        }
        first = inspect_live_editor_context_raw(
            request, _settings(), client_factory=_factory(board)
        )
        second = inspect_live_editor_context_raw(
            request, _settings(), client_factory=_factory(board)
        )
        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(first.active_layer_name, "F.Cu")
        self.assertEqual([item["kind"] for item in first.selection], ["pad", "segment"])
        self.assertNotIn("selection_as_string", repr(first.to_dict()))
        self.assertEqual(board.writes, [])

    def test_empty_selection_is_valid_and_context_digest_is_bindable(self) -> None:
        board = _Board()
        captured = capture_live_editor_context(_settings(), client_factory=_factory(board))
        request = {
            "board": "live",
            "expect_board_revision": captured.board_digest,
        }
        first = inspect_live_editor_context_raw(
            request, _settings(), client_factory=_factory(board)
        )
        bound = dict(request, expect_context_digest=first.context_digest)
        second = inspect_live_editor_context_raw(bound, _settings(), client_factory=_factory(board))
        self.assertEqual(second.selection, ())
        self.assertEqual(second.context_digest, first.context_digest)

    def test_stale_board_snapshot_and_context_are_refused(self) -> None:
        board = _Board([Pad("11111111-1111-1111-1111-111111111111")])
        captured = capture_live_editor_context(_settings(), client_factory=_factory(board))
        base = {
            "board": "live",
            "expect_board_revision": captured.board_digest,
        }
        context = inspect_live_editor_context_raw(base, _settings(), client_factory=_factory(board))
        for field in ("expect_board_revision", "expect_context_digest"):
            with self.subTest(field=field):
                with self.assertRaises(LiveEditorContextError):
                    inspect_live_editor_context_raw(
                        dict(base, **{field: "sha256:" + "0" * 64}),
                        _settings(),
                        client_factory=_factory(board),
                    )
        with self.assertRaises(LiveEditorContextError):
            inspect_live_editor_context_raw(
                dict(
                    base,
                    expect_context_digest=context.context_digest[:-1]
                    + ("1" if context.context_digest[-1] != "1" else "2"),
                ),
                _settings(),
                client_factory=_factory(board),
            )

    def test_scene_snapshot_digest_is_not_accepted_as_an_ipc_precondition(self) -> None:
        board = _Board()
        captured = capture_live_editor_context(_settings(), client_factory=_factory(board))
        with self.assertRaises(RequestError):
            inspect_live_editor_context_raw(
                {
                    "board": "live",
                    "expect_board_revision": captured.board_digest,
                    "expect_snapshot_digest": "sha256:" + "0" * 64,
                },
                _settings(),
                client_factory=_factory(board),
            )

    def test_unknown_empty_and_over_budget_refs_fail_closed(self) -> None:
        for item in (Unknown("11111111-1111-1111-1111-111111111111"), Pad("")):
            with self.subTest(item=type(item).__name__):
                board = _Board([item])
                with self.assertRaises(KicadIpcPayloadError):
                    capture_live_editor_context(_settings(), client_factory=_factory(board))
        board = _Board(
            [
                Pad("11111111-1111-1111-1111-111111111111"),
                Pad("22222222-2222-2222-2222-222222222222"),
            ]
        )
        with self.assertRaises(KicadIpcPayloadError):
            capture_live_editor_context(
                _settings(), client_factory=_factory(board), max_selection=1
            )

    def test_selection_or_layer_change_during_read_is_refused(self) -> None:
        class ChangingBoard(_Board):
            reads = 0

            def get_selection(self) -> list[object]:
                self.reads += 1
                return self.selection if self.reads == 1 else []

        board = ChangingBoard(
            [
                Pad("11111111-1111-1111-1111-111111111111"),
                Track("22222222-2222-2222-2222-222222222222"),
            ]
        )
        with self.assertRaises(KicadIpcConnectionError):
            capture_live_editor_context(_settings(), client_factory=_factory(board))


class LiveEditorMcpSchemaTests(unittest.TestCase):
    def test_mcp_declares_closed_read_only_context(self) -> None:
        import copper_mcp.mcp_server as server

        tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
        tool = tools["inspect_live_editor_context"]
        self.assertIs(tool.annotations.read_only_hint, True)  # type: ignore[union-attr]
        request = tool.input_schema["properties"]["request"]
        self.assertEqual(request["properties"]["board"]["const"], "live")
        self.assertEqual(
            set(request["required"]),
            {"board", "expect_board_revision"},
        )
        self.assertNotIn("get_selection_as_string", repr(tool.input_schema))
