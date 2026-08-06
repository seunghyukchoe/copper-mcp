# Circuit Intent IR and KiCad schematic contract

Circuit Intent IR `0.1.0` is CopperMCP's small, MCP-independent logical topology contract. It keeps
AI-authored proposals outside KiCad syntax and outside Board IR: the deterministic core validates,
normalizes, hashes, and renders the accepted subset.

## Supported logical subset

| Surface | `0.1.0` support |
|---|---|
| Components | Two-pin resistors and non-polarized capacitors |
| Values | Opaque display strings; no unit parsing or value selection |
| Connectivity | Named nets with explicit component-pin membership |
| Interfaces | At most one input, output, bidirectional, or passive port per net |
| Identity | Typed IDs, unique references, canonical ordering, SHA-256 content digest |
| Topology checks | Every pin assigned exactly once; no self-short; no unknown references; one-pin nets require a port |
| Budgets | 256 kB input, depth 32, 8,192 JSON values, 64 components, 128 nets, 32 ports, 128 connections |

Unknown fields, duplicate JSON keys, unsupported versions, numeric JSON values, malformed UTF-8,
control characters, invalid references, incomplete pin assignment, and budget overruns fail closed.
Callers may tighten any operational limit but cannot raise a `0.1.0` ceiling.
The JSON Schema is published at
[`schemas/circuit-intent/0.1.0.schema.json`](../../schemas/circuit-intent/0.1.0.schema.json), while
the runtime decoder additionally owns semantic topology and digest checks.

The snapshot digest is SHA-256 over canonical `content`: UTF-8 JSON, sorted keys, compact separators,
normalized identity order, and one trailing newline. It excludes the envelope so the digest does
not hash itself.

## Deterministic KiCad derivative

`render_kicad_schematic` is a pure verified-snapshot-to-bytes adapter. It targets KiCad schematic
format `20250114`, uses `copper_mcp` as its third-party generator identifier, and returns both the
source-intent digest and the generated artifact digest. The title block embeds the source digest and
component/net/port counts, and the immutable result verifies that metadata against its fields and
content digest. The adapter:

- embeds original CopperMCP resistor and capacitor symbols, so it performs no symbol-library or
  network lookup;
- places components on a nine-column, A4-aware grid with exact 1.27 mm multiples, 25.4 mm
  horizontal and 15.24 mm vertical pitch, 5.08 mm pin-label clearance, and reference/value text
  offset beyond the symbol body;
- connects pins with deterministic local labels and emits the direction-shaped global label on
  every connection of a port-backed net;
- emits structurally RFC 4122 version-4-compatible UUIDs derived from the intent digest and object
  role for reproducible diffs; and
- leaves `Footprint` empty and sets `on_board=no`, preventing the derivative from implying PCB
  readiness.

The renderer performs no filesystem, subprocess, or network operation and caps output at 1 MB. A
committed original RC low-pass fixture is rendered byte-for-byte deterministically. The reusable
`kicad_schematic_parity` verifier first requires that exact renderer replay, then accepts only the
bounded KiCad format-E `kicadxml` export and checks component, pin, and net-node parity against the
Circuit Intent snapshot. This is a source/connectivity oracle for the passive subset, not an ERC
engine or a source-to-PCB parity proof. The reviewed KiCad 10.0.5 run reduced ERC warnings from
seven to four without changing the intended nets. The four remaining warnings are two isolated
external-port labels and two missing private-library-configuration warnings; this is warning
reduction, not an ERC-clean result.

The spacing baseline is deliberately mechanical. It keeps the reviewed passive fixture's symbol
bodies, pin labels, and visible properties distinct and keeps the 64-component schema ceiling on
the A4 drawing area. It does not optimize signal flow, functional grouping, crossings, hierarchy,
or aesthetics and must not be described as Circuit Scene observation or AI placement.

The implementation follows KiCad's official
[schematic file format](https://dev-docs.kicad.org/en/file-formats/sexpr-schematic/) and
[common S-expression rules](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html).
The real-tool check uses the documented
[KiCad 10 schematic CLI](https://docs.kicad.org/10.0/en/cli/cli.html).

## Public schematic delivery

The protocol-independent build service accepts either one strict snapshot JSON document or one
structured Circuit Intent `content` object. Structured content is validated, normalized, and
content-addressed by the service. It then renders the verified snapshot twice and rejects any
byte-level replay difference. Its normal response contains only schema/format versions, source and
artifact digests, byte and topology counts, and a verification matrix; circuit names, component
references and values, net/port names, UUIDs, source JSON, and schematic bytes remain private.
The response contract is published as
[`copper.circuit-schematic-build` `0.1.0`](../../schemas/circuit-schematic-build/0.1.0.schema.json).

`copper-mcp render-schematic INTENT.json --output NEW.kicad_sch` is the explicit durable path. The
CLI input and output paths must resolve inside the configured workspace, and the target must have
the exact lowercase `.kicad_sch` suffix. The input is validated and read through one held descriptor
without following any path-component symlink; output creation remains anchored to a held workspace
directory descriptor. The command creates exactly one new final file. Traversal, symlinks,
case-variant suffixes, existing targets, and partial final files fail closed; overwrite is not an
option.

The stdio-only `render_circuit_schematic` MCP tool accepts structured content and adds an opaque
`pcb://artifacts/schematic/{token}/circuit.kicad_sch` resource capability to the redacted metadata.
Capability access has a 15-minute absolute lifetime; reads do not renew it. The process-local store
has 16-entry and 16 MiB aggregate ceilings, a 1 MiB artifact ceiling, deterministic LRU eviction,
digest verification on read, and no listing, persistence, workspace write, or token/content logging.
Expired objects are removed lazily by later reads/insertions or process exit. The TTL prevents
resource access after the deadline but does not guarantee immediate memory erasure or removal of
copies previously returned to a host. Tokens carry at least 256 bits of randomness; a public content
digest is never used as authorization. Tool and resource registration are disabled over streamable
HTTP until principal and session isolation exists.

Each build reports topology, artifact-digest, provenance-binding, and deterministic-replay checks
as `passed`. It reports per-build KiCad parsing and the bounded schematic component/connectivity
parity oracle separately; authoritative ERC, schematic-to-PCB parity, and electrical validation
remain `not_run`, and board readiness is `false`. Retrieving the resource necessarily reveals
the accepted circuit, so the host—not CopperMCP—decides whether those bytes enter model context.

Authoritative ERC is a *separate* surface, not part of a build. `verify_circuit_schematic_erc` and
`copper-mcp schematic-erc` render the same intent and hand those exact bytes to `kicad-cli sch erc`,
then re-read them through `kicad-cli sch export netlist` and drive the parity oracle above. That
result reports `erc: completed` with `passed` and `clean` as two separate signals, and upgrades
`kicad_cli_parse` to `passed` because KiCad cannot check a schematic it failed to load.
Schematic-to-board parity, electrical validation, and board readiness stay non-claims there too.
Keeping ERC out of the build path is deliberate: rendering must remain usable with no KiCad
install, and a render is not the place to spend a subprocess budget the caller did not ask for.
See [ADR-0071](../adr/0071-authoritative-schematic-erc.md).

## Deliberate non-claims

Structural validity, the bounded component/connectivity parity oracle, and a successful KiCad
parse/netlist export do not establish electrical value correctness, authoritative ERC, simulation,
symbol certification, footprint selection, BOM quality, schematic-to-PCB parity, placement, routing,
manufacturability, fabrication safety, or measured audio behavior.
The public delivery surfaces create only a schematic derivative: they do not generate a board,
select footprints, inspect KiCad on each build, or bridge into the board routing workflow.

The high-fidelity AI north star is a future Circuit Scene IR joining bounded semantic and visual
observation with typed placement intent and immutable placement previews/candidates. Deterministic
code will remain authoritative for snapping, connectivity, clearance, provenance, validation, and
explicit apply. No Circuit Scene IR, automated placement, placement candidate, or placement apply
surface is implemented by this contract.

See [ADR-0014](../adr/0014-canonical-circuit-intent.md) for the IR and renderer decision and
[ADR-0015](../adr/0015-bounded-circuit-schematic-delivery.md) for delivery and disclosure rules.
