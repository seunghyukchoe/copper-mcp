#!/usr/bin/env python3
"""Measure the live, read-only layered route proposal contract with a fake IPC client.

The benchmark serializes one independently authored KiCad fixture through the same official-client
seam used by ``preview_live_layered_route``. It proves candidate equality with the file-backed
layered oracle, stale-CAS refusal, deterministic replay, source immutability, deadline propagation,
and client closure. KiCad, DRC, serialization, editor mutation, and a real GUI session are not
invoked or claimed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import KicadIpcConnectionError, inspect_live_board
from copper_mcp.layered_route_preview import preview_layered_route
from copper_mcp.live_layered_route_preview import preview_live_layered_route
from copper_mcp.mcp_contracts import LayeredRoutePreviewToolResponse

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "blocked-pad.kicad_pcb"
SCRIPT_PATH = Path("scripts/benchmark_live_layered_route_preview.py")
OBSERVED_EDITOR_IDENTITY = "3f2504e0-4f89-11d3-9a0c-0305e82c3301"


class _Version:
    major = 10
    minor = 0
    patch = 5


class _Board:
    def __init__(self, source: str, *, mutate_on_confirm: bool = False) -> None:
        self._source = source
        self._reads = 0
        self._mutate_on_confirm = mutate_on_confirm

    def get_as_string(self) -> str:
        self._reads += 1
        if self._mutate_on_confirm and self._reads == 2:
            return self._source + " "
        return self._source


class _FakeKipyClient:
    """Mirror the learned editor-instance identity exposed by ``kipy``."""

    def __init__(self, instance_identity: str) -> None:
        self._kicad_token = instance_identity


class _KiCad:
    def __init__(
        self,
        source: bytes,
        *,
        instance_identity: str = OBSERVED_EDITOR_IDENTITY,
        mutate_on_confirm: bool = False,
    ) -> None:
        self._board = _Board(
            source.decode("utf-8"),
            mutate_on_confirm=mutate_on_confirm,
        )
        self._client = _FakeKipyClient(instance_identity)
        self.close_calls = 0

    def get_version(self) -> _Version:
        return _Version()

    def get_api_version(self) -> _Version:
        return _Version()

    def check_version(self) -> bool:
        return True

    def get_board(self) -> _Board:
        return self._board

    def close(self) -> None:
        self.close_calls += 1


def _git_commit() -> str:
    git = shutil.which("git")
    if git is None:
        return "unknown"
    try:
        return subprocess.run(  # noqa: S603 - executable is discovered from PATH
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            capture_output=True,
            check=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _digest(source: bytes) -> str:
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:request",
        name="Request",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _request(
    start_pad_id: str,
    end_pad_id: str,
    board_revision: str,
    snapshot_digest: str,
    session_revision: str,
) -> dict[str, Any]:
    return {
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


def _run(repetitions: int) -> dict[str, Any]:
    source = FIXTURE.read_bytes()
    conversion = parse_kicad_bytes(source, _profile())
    if conversion.snapshot is None:
        raise RuntimeError("fixture did not convert to Board IR")
    pads = tuple(pad for pad in conversion.snapshot.content.pads if pad.net_id)
    if len(pads) < 2:
        raise RuntimeError("fixture lacks two connected pads")
    same_net = tuple(pad for pad in pads if pad.net_id == pads[0].net_id)
    if len(same_net) != 2:
        raise RuntimeError("fixture does not have exactly two selected-net pads")
    board_revision = _digest(source)
    snapshot_digest = conversion.snapshot.snapshot_digest
    # The benchmark drives deterministic fakes but still crosses the operator-gated production
    # capture boundary. The session revision is intentionally obtained from the same public live
    # observation a client must use: the PBKDF2 salt is process-local, and the editor identity is
    # learned from the connection rather than from CopperMCP's environment.
    settings = Settings(
        workspace=ROOT / "tests" / "fixtures" / "route-candidate",
        allow_live_ipc=True,
    )
    handshake_clients: list[_KiCad] = []

    def handshake_factory(**_: object) -> _KiCad:
        client = _KiCad(source)
        handshake_clients.append(client)
        return client

    session_revision = inspect_live_board(
        settings,
        client_factory=handshake_factory,
    ).session_revision
    if session_revision is None:
        raise RuntimeError("live observation did not publish a session revision")
    request = _request(
        same_net[0].id,
        same_net[1].id,
        board_revision,
        snapshot_digest,
        session_revision,
    )
    clients: list[_KiCad] = []

    def factory(**_: object) -> _KiCad:
        client = _KiCad(source)
        clients.append(client)
        return client

    live_responses = [
        LayeredRoutePreviewToolResponse.model_validate(
            preview_live_layered_route(request, settings, client_factory=factory)
        ).root
        for _ in range(repetitions)
    ]
    if any(response.status != "routed" for response in live_responses):
        raise RuntimeError("live layered preview did not route the via-required fixture")
    candidate_ids = [response.candidate.candidate_id for response in live_responses]
    if len(set(candidate_ids)) != 1:
        raise RuntimeError("live layered preview was not deterministic")

    file_request = dict(request)
    file_request["board"] = FIXTURE.name
    file_request.pop("expect_session_revision")
    file_response = LayeredRoutePreviewToolResponse.model_validate(
        preview_layered_route(file_request, settings)
    ).root
    if live_responses[0].candidate != file_response.candidate:
        raise RuntimeError("live candidate differs from exact file-backed oracle")

    stale = dict(request)
    stale["expect_board_revision"] = "sha256:" + "0" * 64
    stale_response = LayeredRoutePreviewToolResponse.model_validate(
        preview_live_layered_route(stale, settings, client_factory=factory)
    ).root
    if stale_response.status != "not_routed" or stale_response.diagnostic.code != "stale_revision":
        raise RuntimeError("live stale-board CAS was not refused")

    stale_session = dict(request)
    # Preserve the closed opaque wire type so this probes the CAS rather than input validation.
    stale_session["expect_session_revision"] = session_revision[:-1] + (
        "0" if session_revision[-1] != "0" else "1"
    )
    stale_session_response = LayeredRoutePreviewToolResponse.model_validate(
        preview_live_layered_route(stale_session, settings, client_factory=factory)
    ).root
    if (
        stale_session_response.status != "not_routed"
        or stale_session_response.diagnostic.code != "stale_revision"
    ):
        raise RuntimeError("live stale-session CAS was not refused")

    stale_snapshot = dict(request)
    stale_snapshot["expect_snapshot_digest"] = "sha256:" + "2" * 64
    stale_snapshot_response = LayeredRoutePreviewToolResponse.model_validate(
        preview_live_layered_route(stale_snapshot, settings, client_factory=factory)
    ).root
    if (
        stale_snapshot_response.status != "not_routed"
        or stale_snapshot_response.diagnostic.code != "stale_revision"
    ):
        raise RuntimeError("live stale-snapshot CAS was not refused")

    race_clients: list[_KiCad] = []

    def changing_factory(**_: object) -> _KiCad:
        client = _KiCad(source, mutate_on_confirm=True)
        race_clients.append(client)
        return client

    try:
        preview_live_layered_route(request, settings, client_factory=changing_factory)
    except KicadIpcConnectionError:
        # The typed IPC connection error intentionally does not cross this benchmark's public
        # metrics; only the fail-closed and closure properties are recorded.
        capture_race_refused = True
    else:
        capture_race_refused = False

    return {
        "repetitions": repetitions,
        "deterministic_candidate_ids": len(set(candidate_ids)) == 1,
        "candidate_id": candidate_ids[0],
        "candidate_matches_file_oracle": True,
        "via_count": len(live_responses[0].candidate.patch.vias),
        "schema_valid_replays": repetitions,
        "stale_board_refused": True,
        "stale_session_refused": True,
        "stale_snapshot_refused": True,
        "capture_race_refused": capture_race_refused,
        "ipc_clients_closed": (
            len(handshake_clients) == 1
            and len(clients) == repetitions + 3
            and len(race_clients) == 1
            and all(
                client.close_calls == 1 for client in (*handshake_clients, *clients, *race_clients)
            )
        ),
        "source_unchanged": FIXTURE.read_bytes() == source,
        "kicad_invoked": False,
        "drc_performed": False,
        "serialization_performed": False,
        "apply_authority": False,
        "real_gui_session": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repetitions", type=int, default=10)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if not 1 <= args.repetitions <= 100:
        raise SystemExit("--repetitions must be between 1 and 100")
    metrics = _run(args.repetitions)
    payload: dict[str, Any] = {
        "schema": "copper-mcp/benchmark/live-layered-route-preview/v1",
        "date_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "source_commit": _git_commit(),
        "environment": {"platform": platform.platform(), "python": platform.python_version()},
        "fixture": str(FIXTURE.relative_to(ROOT)),
        "script": str(SCRIPT_PATH),
        "metrics": metrics,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    payload["run_id"] = _digest(canonical)
    rendered = json.dumps(payload, indent=2, sort_keys=True) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")


if __name__ == "__main__":
    main()
