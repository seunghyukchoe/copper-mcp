#!/usr/bin/env python3
"""Measure ADR-0129's live version binding against one real KiCad editor, read-only.

[B-138](../docs/ledgers/benchmark-ledger.md) measured this project's live surfaces refusing a real
KiCad 10.0.5 editor: `inspect_live_board` on its default path and `inspect_live_editor_context`
both raised `KicadIpcVersionError`, and only a non-MCP development flag could observe anything.
ADR-0129 replaced that with a declared major-version window. **This instrument asks whether the
replacement actually works against the same editor, which no offline test can answer.**

It records exactly four things:

1. **Both previously-refusing read surfaces now observe.** The default `inspect_live_board` path
   and the MCP-shaped `inspect_live_editor_context` path, neither passing any override, because
   no override exists any more.
2. **The verdict is `future_api_unverified` against a real `FutureVersionError`.** The raw binding
   is asked separately whether it still raises, so the disclosure is measured against the
   condition that produces it rather than asserted alongside it.
3. **`net_declarations` reads 0 while the editor's own netlist reports a different number.** The
   rename's whole premise, checked against `Board.get_nets()` -- the oracle the old name implied
   and never consulted.
4. **The binding-agreement check does not misfire.** `check_version()` re-reads the version over
   IPC, so on a real editor it is a genuine second reading; ADR-0129 refuses when it disagrees
   with the pair this adapter read, and that must not fire on a healthy session.

**Read-only discipline, inherited rather than reimplemented.** The revision binding, wall-clock
budget, and `--board` validation are imported from `probe_live_text_shapes`, which review of #234
hardened; re-implementing them here would risk a weaker copy of a checked thing. This instrument
never calls a mutating API, never saves, never issues an editor command, never changes editor
state, and never calls `get_selection_as_string`. The board digest is bound before the first
measurement and re-verified after the last; a move refuses and publishes nothing.

**Redaction.** No net name, no UUID, no coordinate, no board text, no socket path and no token
reaches the artifact. Net evidence is a *cardinality* only. The committed board is digested from
the file on disk, never exported over IPC.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    KicadIpcError,
    _socket_path,
    capture_live_editor_context,
    inspect_live_board,
)
from copper_mcp.kicad_ipc_oracle import probe_live_kicad_ipc
from copper_mcp.live_editor_context import (
    LiveEditorContextError,
    inspect_live_editor_context_raw,
)
from scripts.probe_live_text_shapes import (
    Budget,
    ProbeRefusal,
    bind_live_revision,
    require_same_revision,
    validate_board_argument,
)

SCHEMA_VERSION = "0.1.0"
_TIMEOUT_MS = 10_000
DEFAULT_BOARD = Path("hardware/coppertone-buffer/coppertone-buffer.kicad_pcb")


def _repository_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _require_live_binding() -> tuple[Any, str]:
    """Return a connected raw client, or refuse by name before anything claims a measurement."""

    try:
        import kipy
    except ModuleNotFoundError as error:  # pragma: no cover - environment refusal
        raise ProbeRefusal(
            "REFUSED: kicad-python (kipy) is not installed; this instrument needs a live editor"
        ) from error
    # Resolve the endpoint through CopperMCP's own validated resolver rather than letting `kipy`
    # pick its default. Coherence is the reason: the raw facts below and the surface results must
    # describe the *same* editor, and on a host running both the KiCad project manager and a
    # standalone `pcbnew` those are two different processes on two different sockets. The default
    # endpoint reaches the project manager, which answers `get_version` and `ping` but has no PCB
    # document handler -- so a run that mixed the two would attribute one process's version to
    # another process's board.
    socket_path, socket_kind = _socket_path()
    try:
        client = (
            kipy.KiCad(timeout_ms=_TIMEOUT_MS)
            if socket_path is None
            else kipy.KiCad(socket_path=socket_path, timeout_ms=_TIMEOUT_MS)
        )
        client.get_version()
        # Prove a PCB document handler is actually registered before anything claims a
        # measurement. `get_version` alone does not: the project manager answers it too.
        client.get_board()
    except Exception as error:
        raise ProbeRefusal(
            "REFUSED: no running KiCad PCB editor answered the local IPC socket "
            f"({type(error).__name__}). A KiCad that answers `get_version` but not `get_board` is "
            "the project manager without an open PCB editor, which is not a session this "
            "instrument can measure. This instrument publishes no artifact without a session."
        ) from error
    return client, socket_kind


def _raw_binding_facts(client: Any, budget: Budget) -> dict[str, Any]:
    """Ask the binding directly what it reports and whether it still refuses.

    Item 2 is a claim about a *disclosure being produced by a real refusal condition*, so the
    condition has to be observed rather than assumed from the version numbers. This is the only
    place the raw binding is used; every surface result below comes through CopperMCP.
    """

    budget.check("nothing yet", "the raw binding facts and all live surfaces")
    version = client.get_version()
    api_version = client.get_api_version()
    kicad_version = f"{version.major}.{version.minor}.{version.patch}"
    api = f"{api_version.major}.{api_version.minor}.{api_version.patch}"

    budget.check("the reported versions", "the check_version() behaviour and all live surfaces")
    raised: str | None = None
    returned: Any = None
    try:
        returned = client.check_version()
    except Exception as error:
        raised = type(error).__name__
    return {
        "kicad_version": kicad_version,
        "api_version": api,
        "check_version_raised": raised,
        "check_version_returned": returned if raised is None else None,
        # The premise ADR-0129 rests on, restated as an observation rather than a citation.
        "editor_is_newer_than_binding": tuple(int(p) for p in kicad_version.split("."))
        > tuple(int(p) for p in api.split(".")),
    }


def _editor_net_count(client: Any, budget: Budget) -> int:
    """Read the editor's own netlist cardinality -- the oracle the old key name implied.

    Only ``len`` of the result is kept. Net names are board content and never reach the artifact.
    """

    budget.check("the raw binding facts", "the editor netlist and all live surfaces")
    board = client.get_board()
    return len(board.get_nets())


def _live_surfaces(settings: Settings, budget: Budget) -> dict[str, Any]:
    """Exercise every live read surface with no override, because no override exists."""

    surfaces: dict[str, Any] = {}

    budget.check("the raw binding facts", "all four live surfaces")
    try:
        observation = inspect_live_board(settings, timeout_ms=_TIMEOUT_MS)
        surfaces["inspect_live_board"] = {
            "verdict": "observed",
            "observation": observation.to_dict(),
        }
    except KicadIpcError as error:
        surfaces["inspect_live_board"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }

    budget.check("the default observation surface", "the oracle and editor-context surfaces")
    oracle = probe_live_kicad_ipc(settings, timeout_ms=_TIMEOUT_MS)
    surfaces["probe_live_kicad_ipc"] = {"verdict": oracle.status, "result": oracle.to_dict()}

    budget.check("the observation surface and oracle", "the editor-context surfaces")
    revision: str | None = None
    try:
        snapshot = capture_live_editor_context(settings, timeout_ms=_TIMEOUT_MS)
        surfaces["capture_live_editor_context"] = {
            "verdict": "observed",
            "board_revision": snapshot.board_digest,
            "board_bytes": snapshot.board_bytes,
            "active_layer_index": snapshot.active_layer_index,
            "active_layer_name": snapshot.active_layer_name,
            "selection_count": len(snapshot.selection),
            "kicad_version": snapshot.kicad_version,
            "api_version": snapshot.api_version,
            "compatibility": snapshot.compatibility,
            "document_binding": snapshot.document_binding,
        }
        revision = snapshot.board_digest
    except KicadIpcError as error:
        surfaces["capture_live_editor_context"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }

    if revision is None:
        surfaces["inspect_live_editor_context"] = {
            "verdict": "not_attempted",
            "reason": "the capture it compare-and-swaps against did not produce a revision",
        }
        return surfaces

    budget.check("three live surfaces", "the MCP-shaped editor-context surface")
    try:
        context = inspect_live_editor_context_raw(
            {"board": "live", "expect_board_revision": revision},
            settings,
        ).to_dict()
        # Native selection refs are KiCad KIIDs: board identities, not measurements.
        context.pop("selection", None)
        surfaces["inspect_live_editor_context"] = {"verdict": "observed", "context": context}
    except (KicadIpcError, LiveEditorContextError) as error:
        surfaces["inspect_live_editor_context"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }
    return surfaces


def _runner_digest() -> str:
    return f"sha256:{hashlib.sha256(Path(__file__).resolve().read_bytes()).hexdigest()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--board", type=Path, default=None)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    root = _repository_root()
    settings = Settings.from_env()
    if not settings.allow_live_ipc:
        raise ProbeRefusal(
            "REFUSED: COPPER_MCP_ALLOW_LIVE_IPC is not 1. This instrument reads a live editor "
            "and will not do so without the operator opt-in."
        )

    board_argument = args.board if args.board is not None else root / DEFAULT_BOARD
    board_path, board_relative = validate_board_argument(board_argument, root)
    committed = board_path.read_bytes()

    budget = Budget()
    client, socket_kind = _require_live_binding()

    # Bind first. Every measurement below belongs to this revision or the run publishes nothing.
    bound = bind_live_revision(settings)

    raw = _raw_binding_facts(client, budget)
    editor_nets = _editor_net_count(client, budget)
    require_same_revision(bound, bind_live_revision(settings), checkpoint="after the raw binding")

    surfaces = _live_surfaces(settings, budget)
    require_same_revision(bound, bind_live_revision(settings), checkpoint="after the live surfaces")

    observed = surfaces["inspect_live_board"]
    live_counts = (
        observed["observation"]["object_counts"] if observed["verdict"] == "observed" else {}
    )
    artifact: dict[str, Any] = {
        "schema": "copper.live-version-binding-probe",
        "schema_version": SCHEMA_VERSION,
        "recorded_at_utc": datetime.now(UTC).isoformat(),
        "runner_digest": _runner_digest(),
        "commit": os.environ.get("COPPER_MCP_PROBE_COMMIT", "unrecorded"),
        "environment": {
            "python": platform.python_version(),
            "platform": platform.platform(),
        },
        "committed_board": {
            "path": str(board_relative),
            "digest": f"sha256:{hashlib.sha256(committed).hexdigest()}",
            "bytes": len(committed),
        },
        "socket_kind": socket_kind,
        "revision_binding": bound,
        "raw_binding": raw,
        "surfaces": surfaces,
        "net_evidence": {
            "editor_get_nets_count": editor_nets,
            "live_object_counts_net_declarations": live_counts.get("net_declarations"),
            "old_key_nets_present": "nets" in live_counts,
        },
        "budget": {"seconds": budget.elapsed, "ipc_calls": budget.calls},
        "committed_board_text_strings": 0,
        "read_only": True,
    }
    # The canonical form `scripts/check_ledgers.py` recomputes: compact separators, sorted keys,
    # `run_id` itself excluded. Matching it here is what lets the gate verify the self-digest
    # rather than take it on trust.
    payload = json.dumps(artifact, sort_keys=True, separators=(",", ":"), allow_nan=False).encode(
        "utf-8"
    )
    artifact["run_id"] = f"sha256:{hashlib.sha256(payload).hexdigest()}"

    out = Path(os.path.abspath(args.out))  # noqa: PTH100
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
