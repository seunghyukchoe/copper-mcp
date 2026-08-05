# Live KiCad IPC fidelity oracle

**Snapshot date:** 2026-08-05

## Question

Can CopperMCP prove that a running KiCad PCB Editor can supply one exact, read-only snapshot that
converts consistently through Board IR and Circuit Scene, without turning a local diagnostic into
an editor-control path?

## Official evidence

1. KiCad documents its IPC API as a stable, language-agnostic interface for a running KiCad
   instance. In KiCad 9 and 10 it requires the GUI; headless `kicad-cli` API support begins in
   KiCad 11. The API is synchronous, so a busy editor can time out or fail a request.
   [KiCad IPC API overview](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/) and
   [add-on developer guide](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/).
2. KiCad provides a Unix-domain socket/named pipe and an instance-unique token to processes it
   launches as API plugins through `KICAD_API_SOCKET` and `KICAD_API_TOKEN`. A terminal or CI
   process not launched by KiCad does not receive those variables, so their absence cannot prove
   that a GUI's server is disabled. [Connection guidance](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/#connecting-to-kicad).
3. The official `kicad-python` Board API exposes `get_as_string()` as the board-file
   serialization. The same API also exposes mutation/transaction methods, including
   `begin_commit()`, so a fidelity probe must keep to the serialization read and must not infer
   action authority from a successful observation. [Board API reference](https://docs.kicad.org/kicad-python-main/board.html).

## Implemented inference

`probe_live_kicad_ipc` is a local diagnostic, not an MCP action. With plugin-provided credentials
it captures one existing bounded live serialization, verifies the capture digest, parses those
exact bytes through the existing Board IR adapter, and projects a Circuit Scene from those same
bytes. It returns only redacted SHA-256 evidence and four equality checks:

- source bytes ↔ live observation digest;
- Board IR source revision ↔ live observation digest;
- Circuit Scene board revision ↔ live observation digest; and
- Circuit Scene snapshot digest ↔ Board IR snapshot digest.

The oracle reports distinct fixed capability codes for absent socket/token, invalid endpoint,
missing binding, known authentication/token rejection, session change during capture, version
mismatch, parser/scene refusal, and a generic server-unreachable-or-busy condition. The last code
is intentionally conservative: the synchronous API cannot safely distinguish a disabled server,
a stale socket, or an editor blocked by user work from a read-only client.

In CI and ordinary terminal sessions without both plugin variables, the result is deterministic
`skipped` rather than a network attempt or a claim about the workstation's editor. Tests use a
fake official-client seam to prove exact digest equality, client closure, no mutating call, and
redaction. A real GUI result remains required before claiming real-editor fidelity.

## Non-claims

This does not enable live placement/routing apply, alter KiCad preferences, issue an apply token,
run DRC, refill zones, inspect a private board from CI, or prove fabrication correctness. It does
not reveal board text, net names, coordinates, socket paths, or API tokens.
