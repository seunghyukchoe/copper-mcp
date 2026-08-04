# KiCad IPC observer research

**Snapshot date:** 2026-08-04

## Findings

1. KiCad's official IPC API is available from KiCad 9 onward and is designed as a more stable,
   language-agnostic boundary than the legacy SWIG Python bindings. KiCad 9/10 requires a running
   GUI session; headless `kicad-cli` API-server support is a KiCad 11 feature.
2. The transport is request/reply over a local Unix-domain socket on macOS/Linux (named pipe on
   Windows). KiCad supplies `KICAD_API_SOCKET` and `KICAD_API_TOKEN` to plugins. The adapter must
   reject TCP URLs and must never include the token or socket path in MCP output.
3. The official Python wrapper is the `kicad-python` package, imported as `kipy`. Its `KiCad`
   object exposes version checks, an open-board handle, `get_as_string`, and read APIs such as
   `get_nets`, `get_footprints`, `get_pads`, `get_tracks`, `get_vias`, and `get_zones`.
4. Version compatibility is not cosmetic. The 0.7.1 wheel reports an API build based on KiCad
   10.0.1, while this workstation has KiCad 10.0.5. The adapter therefore refuses future versions
   by default and labels an explicit development override `future_api_unverified`.
5. IPC is synchronous and KiCad 9/10 has no schematic-editor IPC surface. Live board observation
   can close the editor-state loop for PCB work, but it cannot claim schematic parity, ERC,
   electrical validation, or a stable live scene until a separate binding is implemented.

## Implemented boundary

`copper_mcp.kicad_ipc.inspect_live_board` loads `kipy` lazily, normalizes a bounded local socket,
checks versions, hashes the live board serialization, and counts board objects from that same
serialization. It then requests a second serialization and refuses if the bytes changed during
observation. The MCP response is deliberately redacted and typed. `hardware/kicad-ipc-plugin/plugin.json`
is validated against the official KiCad API plugin schema shape and exposes the same read-only
action for a KiCad PCB Editor session.

The wrapper's `get_as_string` call is synchronous and returns a complete string before Python can
measure it; no count-only or streaming request is exposed. This is a residual allocation risk,
not a claim of a hard pre-allocation ceiling. The adapter refuses an oversized response as soon
as it returns, avoids ten additional collection materializations, and applies bounded
S-expression input/token/node limits while deriving counts. An isolated worker is still required
for a hard memory boundary around an untrusted or remote session.

The count grammar is intentionally conservative: only direct `kicad_pcb` `(net ...)` declarations
contribute to `nets`; nested pad/copper net references are not declarations, and the supported
graphical heads include `gr_circle`. B-012 covers this topology oracle with a nested-net fixture.

The B-008 benchmark uses a fake `KiCad` client so CI measures deterministic behavior without
requiring a GUI, token, or global KiCad setting. The current desktop KiCad IPC server was observed
disabled, so no live-session success is claimed in that benchmark.

## Snapshot-to-scene binding

The next bridge is now implemented as `capture_live_board` plus the read-only
`observe_live_board_scene` MCP tool. The internal capture pairs the exact UTF-8 serialization with
the redacted digest and checks their byte count and SHA-256 equality before Board IR conversion.
The scene uses the literal `board: "live"`, preserves the existing exact integer geometry and
quarantined annotation contract, and can require both an expected board digest and expected Board
IR snapshot digest. A mismatch refuses before the scene is returned. B-009 measures deterministic
fake-client conversion and stale-digest refusals; it intentionally does not claim a live GUI
session because the local KiCad IPC server is disabled.

The live-scene service performs the same bounded request parse before `capture_live_board`.
Malformed constraints, regions, layer filters, unknown fields, invalid expected digests, and the
unsupported render flag therefore fail without opening the IPC client; B-011 records the zero-call
preflight oracle.

## Read-only route proposal

`preview_live_route` is the next narrow bridge: it accepts only a `net_ref_id` copied from a
revision-bound Circuit Scene and both compare-and-swap digests, captures one exact IPC snapshot,
converts it through the same Board IR adapter, and returns the existing immutable route candidate.
The application boundary rejects DRC, zone refill, and apply-token flags before IPC. B-013 measures
candidate equality with the file-backed oracle, deterministic replay, stale-session refusal, and
zero-call action preflight. This closes the observe-to-propose loop, not the live action loop.

## Read-only placement proposal

`preview_live_placement` is the next safe action edge. It accepts the same ref-anchored rules and
proposals as the file-backed placement surface, but binds them to `board: "live"` and the two
digests emitted by Circuit Scene. The service captures one exact serialization, converts that
source through Board IR 0.2, builds the placement view from the same snapshot, and runs the
existing deterministic legalizer. A stale board digest is refused before conversion; a stale
snapshot digest is refused before placement projection. The response contains only a candidate or
typed refusal and uses no KiCad write, DRC, fill, apply-token, selection, or raw-source API.

This follows the official Board API boundary: `get_as_string()` is the complete board
serialization, while `update_items()`, `push_commit()`, and `save()` are mutation APIs and are
therefore deliberately outside this proposal contract. The fake-client B-014 oracle proves
candidate equality with the file-backed placement oracle and zero mutating calls; a real GUI
session, KiCad DRC, undo transaction, and placement apply remain unclaimed.

## Primary references

- KiCad, “For Add-on Developers,” especially API limits, socket/token variables, plugin runtime,
  and Python bindings: <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/>
- KiCad, “KiCad IPC API,” transport and request/reply design:
  <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/>
- Official `kicad-python` repository and README: <https://gitlab.com/kicad/code/kicad-python>
- Official plugin metadata schema: <https://gitlab.com/kicad/code/kicad/-/raw/master/api/schemas/api.v1.schema.json>
- KiCad Python 0.7.1 package metadata (API build compatibility is checked at runtime):
  <https://pypi.org/project/kicad-python/>
- Official Board API reference for `get_as_string`, `get_active_layer`, `get_selection`, and
  mutation methods: <https://docs.kicad.org/kicad-python-main/board.html>

## Non-claims

This record does not claim IPC write support, single-undo transactions, live GUI-session success,
live placement/routing action authority, DRC, ERC, schematic parity, headless KiCad 10 operation,
or production/fabrication readiness. Those require separate contracts and real-session evidence.
