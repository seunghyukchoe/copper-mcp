from __future__ import annotations

import hashlib
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_ipc as kicad_ipc
import copper_mcp.live_layered_route_preview as live_preview
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import Layer, NetClass, make_snapshot
from copper_mcp.circuit_scene import observe_live_board_scene
from copper_mcp.config import Settings
from copper_mcp.layered_route_preview import LayeredRoutePreviewError, preview_layered_route

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
SESSION_TOKEN = "copper-mcp-test-kicad-session"
# The per-process identity KiCad's API server returns in every response envelope. The
# session revision is derived from this, not from CopperMCP's own environment block.
EDITOR_INSTANCE_TOKEN = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"
RESTARTED_EDITOR_INSTANCE_TOKEN = "9c5b94b1-35ad-49bb-b118-8e8fc24abf80"


@pytest.fixture(autouse=True)
def _fake_kicad_session(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KICAD_API_TOKEN", SESSION_TOKEN)
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x42" * 32)


class FutureVersionError(Exception):
    """Stands in for ``kipy.errors.FutureVersionError``.

    The name is load-bearing: the adapter matches this exception by class *name* because
    ``kicad-python`` is an optional dependency that cannot be imported for an isinstance check.
    """


class _FakeVersion:
    def __init__(self, major: int = 10, minor: int = 0, patch: int = 5) -> None:
        self.major = major
        self.minor = minor
        self.patch = patch


class _FakeLiveBoard:
    def __init__(self, source: str) -> None:
        self._source = source

    def get_as_string(self) -> str:
        return self._source


class _FakeKipyClient:
    """Mirror of ``kipy.client.KiCadClient``'s learned-instance-token attribute."""

    def __init__(self, instance_token: str) -> None:
        self._kicad_token = instance_token


class _FakeLiveKiCad:
    def __init__(
        self,
        source: str,
        instance_token: str = EDITOR_INSTANCE_TOKEN,
        kicad: tuple[int, int, int] = (10, 0, 5),
        api: tuple[int, int, int] = (10, 0, 5),
    ) -> None:
        self._board = _FakeLiveBoard(source)
        self._client = _FakeKipyClient(instance_token)
        self._kicad = kicad
        self._api = api

    def get_version(self) -> _FakeVersion:
        return _FakeVersion(*self._kicad)

    def get_api_version(self) -> _FakeVersion:
        return _FakeVersion(*self._api)

    def check_version(self) -> bool:
        # Reproduces kipy 0.7.1's asymmetry: raises only for a strictly newer editor.
        if self._kicad > self._api:
            raise FutureVersionError()
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
        # Live IPC is operator-gated and off by default; these tests exercise the enabled path.
        Settings(workspace=tmp_path, allow_live_ipc=True),
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
    session_revision = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
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


def test_live_session_revision_is_pbkdf2_stable_and_token_distinct() -> None:
    first = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
    second = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
    assert first is not None
    assert first == second
    assert first.startswith("pbkdf2-hmac-sha256:")
    assert len(first) == len("pbkdf2-hmac-sha256:") + 64
    assert set(first.removeprefix("pbkdf2-hmac-sha256:")) <= set("0123456789abcdef")
    expected = hashlib.pbkdf2_hmac(
        "sha256",
        EDITOR_INSTANCE_TOKEN.encode(),
        kicad_ipc._SESSION_REVISION_SALT_DOMAIN + kicad_ipc._SESSION_REVISION_SALT,
        kicad_ipc._SESSION_REVISION_ITERATIONS,
        dklen=kicad_ipc._SESSION_REVISION_DKLEN,
    ).hex()
    assert first == f"pbkdf2-hmac-sha256:{expected}"
    assert first != "sha256:" + hashlib.sha256(EDITOR_INSTANCE_TOKEN.encode()).hexdigest()

    # A different running editor derives a different revision, and no environment variable of
    # ours participates in that derivation.
    assert kicad_ipc._session_revision(RESTARTED_EDITOR_INSTANCE_TOKEN) != first
    assert kicad_ipc._session_revision(None) is None


def test_live_preview_refuses_a_session_revision_from_a_rotated_process_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    previous = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
    assert previous is not None
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x43" * 32)
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
    with pytest.raises(LayeredRoutePreviewError, match="pbkdf2-hmac-sha256"):
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


def test_live_request_refuses_legacy_hmac_session_revision() -> None:
    with pytest.raises(LayeredRoutePreviewError, match="pbkdf2-hmac-sha256"):
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
                "expect_session_revision": "hmac-sha256:" + "0" * 64,
            }
        )


def test_live_session_revision_pbkdf2_work_is_fixed_and_bounded() -> None:
    assert kicad_ipc._SESSION_REVISION_ITERATIONS == 200_000
    assert kicad_ipc._SESSION_REVISION_ITERATIONS <= 500_000
    assert kicad_ipc._SESSION_REVISION_DKLEN == 32

    started = time.monotonic()
    revision = kicad_ipc._session_revision(EDITOR_INSTANCE_TOKEN)
    elapsed_seconds = time.monotonic() - started

    assert revision is not None
    # Broad regression guard: this local CAS derivation must not consume a route-sized budget.
    assert elapsed_seconds < 5.0


def _factory(
    source: bytes,
    instance_token: str = EDITOR_INSTANCE_TOKEN,
    kicad: tuple[int, int, int] = (10, 0, 5),
    api: tuple[int, int, int] = (10, 0, 5),
):
    text = source.decode("utf-8")
    return lambda **_: _FakeLiveKiCad(text, instance_token, kicad, api)


@pytest.mark.parametrize(
    ("kicad", "api"),
    [((10, 0, 5), (10, 0, 1)), ((10, 0, 0), (10, 0, 1)), ((10, 0, 1), (10, 0, 1))],
)
def test_live_preview_runs_across_the_whole_declared_window(
    tmp_path: Path, kicad: tuple[int, int, int], api: tuple[int, int, int]
) -> None:
    """ADR-0128: a read surface accepts every pair inside the window, drifted or not.

    Before ADR-0128 the first of these pairs -- the one B-138 measured against a real editor --
    reached a `KicadIpcVersionError` here, so the preview could not run against the KiCad the
    operator actually had installed.
    """

    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    result = live_preview.preview_live_layered_route(
        _request(start, end, board_revision, snapshot_digest),
        settings,
        client_factory=_factory(FIXTURE.read_bytes(), kicad=kicad, api=api),
    )
    # The version binding is not what decides the route; it decides whether we get to try.
    assert result["status"] in {"routed", "not_routed"}
    assert result["diagnostic"] is None or result["diagnostic"]["code"] != "unsupported_version"


def test_live_preview_refuses_a_major_boundary_before_it_converts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)

    def unexpected_conversion(*_: object, **__: object) -> object:
        raise AssertionError("a refused version must not reach Board IR conversion")

    monkeypatch.setattr(live_preview, "parse_kicad_bytes", unexpected_conversion)
    with pytest.raises(kicad_ipc.KicadIpcVersionError) as error:
        live_preview.preview_live_layered_route(
            _request(start, end, board_revision, snapshot_digest),
            settings,
            client_factory=_factory(FIXTURE.read_bytes(), kicad=(9, 0, 0), api=(10, 0, 1)),
        )
    assert "9.0.0" in str(error.value)
    assert "10.0.1" in str(error.value)


def test_public_live_observation_scene_and_preview_outputs_compose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A client needs no private helper to carry the three live CAS values forward."""

    settings, _, _, _, _ = _workspace(tmp_path)
    source = FIXTURE.read_bytes()
    salt_canary = b"session-salt-must-not-escape-0000"
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", salt_canary)

    observation = kicad_ipc.inspect_live_board(settings, client_factory=_factory(source)).to_dict()
    scene = observe_live_board_scene(
        {
            "board": "live",
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "region": {
                "min_x_nm": -1_000_000_000,
                "min_y_nm": -1_000_000_000,
                "max_x_nm": 1_000_000_000,
                "max_y_nm": 1_000_000_000,
            },
        },
        settings,
        client_factory=_factory(source),
    ).to_dict()
    pads = scene["static"]["pads"]
    assert isinstance(pads, list) and len(pads) == 2
    session_revision = observation["session_revision"]
    assert isinstance(session_revision, str)

    result = live_preview.preview_live_layered_route(
        {
            "board": "live",
            "start_pad_id": pads[0]["ref_id"],
            "end_pad_id": pads[1]["ref_id"],
            "constraints": {
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "expect_board_revision": scene["board_revision"],
            "expect_snapshot_digest": scene["snapshot_digest"],
            "expect_session_revision": session_revision,
            "grid_step_nm": 250_000,
            "seed": 23,
        },
        settings,
        client_factory=_factory(source),
    )

    public_outputs = repr((observation, scene, result))
    assert result["status"] == "routed"
    assert SESSION_TOKEN not in public_outputs
    # The editor's own instance identity is a credential too: the published revision is a
    # PBKDF2 handle for it, never the value itself.
    assert EDITOR_INSTANCE_TOKEN not in public_outputs
    assert salt_canary.hex() not in public_outputs


def test_public_session_revision_refuses_token_change_and_process_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    source = FIXTURE.read_bytes()
    observation = kicad_ipc.inspect_live_board(settings, client_factory=_factory(source)).to_dict()
    session_revision = observation["session_revision"]
    assert isinstance(session_revision, str)
    request = {
        "board": "live",
        "start_pad_id": start,
        "end_pad_id": end,
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

    # A restarted editor reports a different instance identity. CopperMCP's environment is
    # untouched, because a restarting KiCad cannot write to it.
    changed_token = live_preview.preview_live_layered_route(
        request,
        settings,
        client_factory=_factory(source, RESTARTED_EDITOR_INSTANCE_TOKEN),
    )
    assert changed_token["status"] == "not_routed"
    assert changed_token["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]

    # A restarted *CopperMCP* rotates the process salt, which is the second, independent
    # staleness source stacked on the editor identity.
    monkeypatch.setattr(kicad_ipc, "_SESSION_REVISION_SALT", b"\x99" * 32)
    restarted_process = live_preview.preview_live_layered_route(
        request, settings, client_factory=_factory(source)
    )
    assert restarted_process["status"] == "not_routed"
    assert restarted_process["diagnostic"]["code"] == "stale_revision"  # type: ignore[index]


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


def test_live_preview_refuses_internal_three_layer_router_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, start, end, board_revision, _ = _workspace(tmp_path)
    source = FIXTURE.read_bytes()
    profile = KiCadConstraintProfile(
        net_classes=(
            NetClass(
                id="class:request",
                name="Request",
                clearance_nm=250_000,
                track_width_nm=250_000,
                via_diameter_nm=800_000,
                via_drill_nm=400_000,
            ),
        ),
        default_net_class_id="class:request",
    )
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    front, back = snapshot.content.copper_layers
    internal_snapshot = make_snapshot(
        replace(
            snapshot.content,
            copper_layers=(
                front,
                Layer(id="layer:In1.Cu", name="In1.Cu", index=1),
                replace(back, index=2),
            ),
        )
    )
    monkeypatch.setattr(
        live_preview,
        "parse_kicad_bytes",
        lambda *_args, **_kwargs: replace(conversion, snapshot=internal_snapshot),
    )

    result = live_preview.preview_live_layered_route(
        _request(start, end, board_revision, internal_snapshot.snapshot_digest),
        settings,
        client_factory=_factory(source),
    )

    assert result["status"] == "unsupported_board"
    assert result["candidate"] is None
    assert result["diagnostic"]["code"] == "unsupported_geometry"  # type: ignore[index]


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


def test_live_preview_rejects_zone_fill_authority_opt_in(
    tmp_path: Path,
) -> None:
    """Pinned rather than defaulted: a live proposal has no file whose cache could be proved.

    Zone fill authority refills a private disposable copy of a *board file* and compares it to
    that file's cache. A live proposal routes an IPC snapshot of a possibly unsaved editor, so
    accepting the flag could only mean ignoring it, which is a silently unhonoured authority
    request (ADR-0106).
    """

    _settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, board_revision, snapshot_digest, include_fill_authority=True)

    with pytest.raises(LayeredRoutePreviewError, match="cannot request zone fill authority"):
        live_preview.parse_live_layered_route_preview_request(request)

    # Not vacuous: the same request with the flag explicitly false parses, and the parsed value
    # is the pinned one rather than a dropped key.
    parsed = live_preview.parse_live_layered_route_preview_request(
        _request(start, end, board_revision, snapshot_digest, include_fill_authority=False)
    )
    assert parsed.include_fill_authority is False


def test_live_preview_refuses_stale_session_before_conversion(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings, start, end, board_revision, snapshot_digest = _workspace(tmp_path)
    request = _request(start, end, board_revision, snapshot_digest)
    request["expect_session_revision"] = "pbkdf2-hmac-sha256:" + "0" * 64

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
    settings = Settings(workspace=tmp_path, max_route_preview_seconds=1, allow_live_ipc=True)
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
