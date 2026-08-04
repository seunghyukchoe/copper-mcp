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
checks versions, hashes the live board serialization, and counts board objects. The MCP response
is deliberately redacted and typed. `hardware/kicad-ipc-plugin/plugin.json` is validated against
the official KiCad API plugin schema shape and exposes the same read-only action for a KiCad PCB
Editor session.

The B-008 benchmark uses a fake `KiCad` client so CI measures deterministic behavior without
requiring a GUI, token, or global KiCad setting. The current desktop KiCad IPC server was observed
disabled, so no live-session success is claimed in that benchmark.

## Primary references

- KiCad, “For Add-on Developers,” especially API limits, socket/token variables, plugin runtime,
  and Python bindings: <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/>
- KiCad, “KiCad IPC API,” transport and request/reply design:
  <https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/>
- Official `kicad-python` repository and README: <https://gitlab.com/kicad/code/kicad-python>
- Official plugin metadata schema: <https://gitlab.com/kicad/code/kicad/-/raw/master/api/schemas/api.v1.schema.json>
- KiCad Python 0.7.1 package metadata (API build compatibility is checked at runtime):
  <https://pypi.org/project/kicad-python/>

## Non-claims

This record does not claim IPC write support, single-undo transactions, live Circuit Scene
geometry, placement, routing, DRC, ERC, schematic parity, headless KiCad 10 operation, or
production/fabrication readiness. Those require separate contracts and real-session evidence.
