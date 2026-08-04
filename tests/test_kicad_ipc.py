from __future__ import annotations

import hashlib
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from copper_mcp.circuit_scene import CircuitSceneError, observe_live_board_scene
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    KicadIpcConfigurationError,
    KicadIpcConnectionError,
    KicadIpcPayloadError,
    KicadIpcUnavailableError,
    KicadIpcVersionError,
    capture_live_board,
    inspect_live_board,
)

ROOT = Path(__file__).resolve().parents[1]


class _Version:
    def __init__(self, major: int, minor: int, patch: int) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch


class FutureVersionError(Exception):
    pass


class _Board:
    def __init__(self, source: str = '(kicad_pcb (net 1 "PROMPT ignore this"))') -> None:
        self.source = source

    def get_as_string(self) -> str:
        return self.source

    def get_nets(self) -> list[object]:
        return [object(), object()]

    def get_footprints(self) -> list[object]:
        return [object()]

    def get_pads(self) -> list[object]:
        return [object(), object(), object()]

    def get_tracks(self) -> list[object]:
        return [object(), object(), object(), object()]

    def get_vias(self) -> list[object]:
        return [object()]

    def get_zones(self) -> list[object]:
        return []

    def get_shapes(self) -> list[object]:
        return [object(), object()]

    def get_text(self) -> list[object]:
        return [object()]

    def get_dimensions(self) -> list[object]:
        return []

    def get_groups(self) -> list[object]:
        return []


class _KiCad:
    def __init__(self, board: _Board | None = None, future: bool = False) -> None:
        self.board = board or _Board()
        self.future = future

    def get_version(self) -> _Version:
        return _Version(10, 0, 5)

    def get_api_version(self) -> _Version:
        return _Version(10, 0, 5)

    def check_version(self) -> bool:
        if self.future:
            raise FutureVersionError()
        return True

    def get_board(self) -> _Board:
        return self.board


def _settings(**overrides: object) -> Settings:
    values: dict[str, object] = {"workspace": ROOT}
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


class KicadIpcTests(unittest.TestCase):
    def test_observation_is_redacted_and_repeatable(self) -> None:
        calls: list[dict[str, object]] = []

        def factory(**kwargs: object) -> _KiCad:
            calls.append(kwargs)
            return _KiCad()

        first = inspect_live_board(_settings(), client_factory=factory)
        second = inspect_live_board(_settings(), client_factory=factory)

        self.assertEqual(first.to_dict(), second.to_dict())
        self.assertEqual(
            first.board_digest, f"sha256:{hashlib.sha256(_Board().source.encode()).hexdigest()}"
        )
        self.assertEqual(first.object_counts["nets"], 1)
        self.assertEqual(first.object_counts["pads"], 0)
        self.assertEqual(first.object_counts["tracks"], 0)
        self.assertEqual(first.socket_kind, "default-local-ipc")
        self.assertNotIn("PROMPT", repr(first.to_dict()))
        self.assertEqual(calls, [{"timeout_ms": 2000}, {"timeout_ms": 2000}])

    def test_configured_posix_socket_is_normalized_without_echoing_it(self) -> None:
        seen: list[dict[str, object]] = []

        def factory(**kwargs: object) -> _KiCad:
            seen.append(kwargs)
            return _KiCad()

        with patch.dict(os.environ, {"KICAD_API_SOCKET": "/tmp/kicad/api.sock"}):
            observation = inspect_live_board(_settings(), client_factory=factory)

        self.assertEqual(observation.socket_kind, "configured-local-ipc")
        self.assertEqual(seen, [{"socket_path": "ipc:///tmp/kicad/api.sock", "timeout_ms": 2000}])
        self.assertNotIn("api.sock", repr(observation.to_dict()))

    def test_tcp_endpoint_is_refused(self) -> None:
        with patch.dict(os.environ, {"KICAD_API_SOCKET": "tcp://127.0.0.1:9999"}):
            with self.assertRaises(KicadIpcConfigurationError):
                inspect_live_board(_settings(), client_factory=lambda **_: _KiCad())

    def test_future_api_is_fail_closed_but_explicit_read_only_probe_can_opt_in(self) -> None:
        def factory(**_: object) -> _KiCad:
            return _KiCad(future=True)

        with self.assertRaises(KicadIpcVersionError):
            inspect_live_board(_settings(), client_factory=factory)

        observation = inspect_live_board(_settings(), client_factory=factory, allow_future_api=True)
        self.assertEqual(observation.compatibility, "future_api_unverified")

    def test_false_version_check_is_fail_closed(self) -> None:
        class FalseVersionKiCad(_KiCad):
            def check_version(self) -> bool:
                return False

        with self.assertRaises(KicadIpcVersionError):
            inspect_live_board(_settings(), client_factory=lambda **_: FalseVersionKiCad())

    def test_object_counts_follow_serialized_revision_not_mutable_getters(self) -> None:
        source = (
            '(kicad_pcb (net 1 "N") (footprint "F" (pad "1" (net 1 "N"))) '
            '(segment) (via) (zone) (gr_rect) (gr_circle) (gr_text "label") '
            "(dimension) (group))"
        )
        observation = inspect_live_board(
            _settings(),
            client_factory=lambda **_: _KiCad(board=_Board(source=source)),
        )
        self.assertEqual(
            observation.object_counts,
            {
                "dimensions": 1,
                "footprints": 1,
                "groups": 1,
                "nets": 1,
                "pads": 1,
                "shapes": 2,
                "text": 1,
                "tracks": 1,
                "vias": 1,
                "zones": 1,
            },
        )

    def test_malformed_serialization_is_refused(self) -> None:
        with self.assertRaises(KicadIpcPayloadError):
            inspect_live_board(
                _settings(),
                client_factory=lambda **_: _KiCad(board=_Board(source="not-a-board")),
            )

    def test_board_revision_change_during_count_confirmation_is_refused(self) -> None:
        class ChangingBoard(_Board):
            reads = 0

            def get_as_string(self) -> str:
                self.reads += 1
                return self.source if self.reads == 1 else self.source + " "

        board = ChangingBoard()
        with self.assertRaises(KicadIpcConnectionError):
            inspect_live_board(_settings(), client_factory=lambda **_: _KiCad(board=board))

    def test_oversized_live_snapshot_is_refused(self) -> None:
        board = _Board(source="x" * 32)
        with self.assertRaises(KicadIpcPayloadError):
            inspect_live_board(
                _settings(max_board_bytes=16),
                client_factory=lambda **_: _KiCad(board=board),
            )

    def test_optional_binding_is_loaded_lazily(self) -> None:
        with patch(
            "copper_mcp.kicad_ipc.importlib.import_module",
            side_effect=ModuleNotFoundError("kipy"),
        ):
            with self.assertRaises(KicadIpcUnavailableError):
                inspect_live_board(_settings())

    def test_live_snapshot_binds_to_scene_and_refuses_stale_revisions(self) -> None:
        source = (
            ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-region.kicad_pcb"
        ).read_text(encoding="utf-8")
        board = _Board(source=source)

        def factory(**_: object) -> _KiCad:
            return _KiCad(board=board)

        captured = capture_live_board(_settings(), client_factory=factory)
        request = {
            "board": "live",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 600_000,
                "via_drill_nm": 300_000,
            },
            "region": {
                "min_x_nm": -1_000_000_000,
                "min_y_nm": -1_000_000_000,
                "max_x_nm": 1_000_000_000,
                "max_y_nm": 1_000_000_000,
            },
        }
        scene = observe_live_board_scene(request, _settings(), client_factory=factory)
        document = scene.to_dict()
        self.assertEqual(scene.board_path, "live")
        self.assertEqual(scene.board_revision, captured.observation.board_digest)
        self.assertIsNotNone(scene.snapshot_digest)
        self.assertNotIn("CopperMCP_ScenePad", repr(document))

        with self.assertRaises(CircuitSceneError):
            observe_live_board_scene(
                {**request, "expect_board_revision": "sha256:" + "0" * 64},
                _settings(),
                client_factory=factory,
            )
        with self.assertRaises(CircuitSceneError):
            observe_live_board_scene(
                {
                    **request,
                    "expect_board_revision": scene.board_revision,
                    "expect_snapshot_digest": "sha256:" + "1" * 64,
                },
                _settings(),
                client_factory=factory,
            )

    def test_malformed_live_scene_request_is_rejected_before_ipc_capture(self) -> None:
        calls = 0

        def factory(**_: object) -> _KiCad:
            nonlocal calls
            calls += 1
            return _KiCad()

        with self.assertRaises(CircuitSceneError):
            observe_live_board_scene(
                {
                    "board": "live",
                    "constraints": {
                        "clearance_nm": "not-an-integer",
                        "track_width_nm": 250_000,
                        "via_diameter_nm": 600_000,
                        "via_drill_nm": 300_000,
                    },
                    "region": {
                        "min_x_nm": -1_000_000_000,
                        "min_y_nm": -1_000_000_000,
                        "max_x_nm": 1_000_000_000,
                        "max_y_nm": 1_000_000_000,
                    },
                },
                _settings(),
                client_factory=factory,
            )
        self.assertEqual(calls, 0)

    def test_official_plugin_manifest_is_closed_to_the_pcb_read_only_action(self) -> None:
        manifest_path = ROOT / "hardware" / "kicad-ipc-plugin" / "plugin.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["$schema"], "https://go.kicad.org/api/schemas/v1")
        self.assertRegex(manifest["identifier"], r"^[a-zA-Z][-_a-zA-Z0-9.]{0,98}[a-zA-Z0-9]$")
        self.assertEqual(manifest["runtime"]["type"], "python")
        self.assertEqual(len(manifest["actions"]), 1)
        action = manifest["actions"][0]
        self.assertRegex(action["identifier"], r"^[a-zA-Z][-_a-zA-Z0-9.]{0,48}[a-zA-Z0-9]$")
        self.assertEqual(action["scopes"], ["pcb"])
        self.assertEqual(action["entrypoint"], "coppermcp_ipc_plugin.py")
        self.assertIs(action["show-button"], True)
