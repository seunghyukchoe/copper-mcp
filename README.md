# CopperMCP

[![CI](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml)
[![CodeQL](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/seunghyukchoe/copper-mcp)](https://github.com/seunghyukchoe/copper-mcp/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-MVP--alpha-blue.svg)](docs/roadmap.md)

**CopperMCP is a local-first, open-source PCB automation platform designed for deterministic routing,
MCP-based tools, and optional AI policy plugins.**

It lets an AI client read a KiCad board as typed, exact-integer geometry, propose routes and
placements, and validate them against authoritative KiCad DRC — without ever letting the model
write copper. Every generated result is an immutable candidate bound to an exact board revision
until a human explicitly applies it.

> [!IMPORTANT]
> CopperMCP `0.4.x` is an **MVP-alpha**. The released `0.4` line is entirely non-mutating; current
> unreleased `main` adds one operator-gated, token-authorized route apply. Read
> [What CopperMCP does not claim](#what-coppermcp-does-not-claim) before relying on any result.

## Why this project exists

Existing open autorouters provide useful geometry and negotiated-congestion baselines, but there is
no broadly adopted open platform that combines reproducible routing, safe agent tools, learned
policy hooks, KiCad-native workflows, and transparent benchmarks. CopperMCP is building that layer
without putting an LLM in charge of electrical correctness.

The non-negotiable boundary is simple:

- AI may interpret constraints and propose net ordering, corridors, cost weights, and repairs.
- Deterministic code owns geometry, connectivity, DRC, provenance, and file mutation.
- Generated work remains an immutable candidate until a user validates and explicitly applies it.

## Quick start

Prerequisites: Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,security]"
make check
```

Inspect a board without modifying it:

```bash
copper-mcp --workspace /absolute/path/to/boards inspect example.kicad_pcb
```

Start the local MCP server over standard input/output:

```bash
export COPPER_MCP_WORKSPACE=/absolute/path/to/boards
copper-mcp-server
```

Example MCP client configuration:

```json
{
  "mcpServers": {
    "copper-mcp": {
      "command": "copper-mcp-server",
      "env": {
        "COPPER_MCP_WORKSPACE": "/absolute/path/to/boards",
        "COPPER_MCP_TRANSPORT": "stdio"
      }
    }
  }
}
```

Never place provider keys or proprietary board contents in committed MCP configuration. See
[`.env.example`](.env.example) and the [security policy](SECURITY.md).

**The [usage guide](docs/usage.md) covers every command and MCP tool** — DRC, scene observation,
deterministic renders, route and placement previews, schematic building, route apply, and the
read-only live KiCad IPC observer — with the limits each one declares.

## What it can do today

Each capability below is bound to tests and, where it touches KiCad, to recorded evidence.

**Read a board.** Bounded, read-only inspection of documented `.kicad_pcb` files under workspace
confinement, including protection against parent-path and symlink escapes. SHA-256 board revisions
and versioned JSON schemas throughout.

**Represent a board exactly.** Immutable Board IR `0.2.0` with exact integer units, typed
constraints, canonical digests, first-class footprint pose/side/lock/pad ownership, simple orthogonal
courtyard rings, and a bounded fail-closed converter for a documented KiCad subset. The 0.1 schema
remains available as immutable compatibility evidence; [migration](docs/migrations/board-ir-0.2.md)
re-converts the original board rather than inventing parents.

**Observe a board semantically.** Circuit Scene IR `0.2.0` over MCP and the CLI. A mandatory region
returns full-precision integer geometry for overlapping objects, split into `static` (outline,
footprints, pads, keepouts, rules) and `mutable` (segments, arcs, vias, zones) so code meaning to
read only the givens cannot iterate over both. Objects are named by the Board IR references they
already carry, each declaring how durable that reference is, and truncation is reported explicitly.
Board text is off by default and, when requested, appears only in a separately typed `annotations`
collection marked untrusted; net names never appear at all.

**Render a board deterministically.** An opt-in SVG of the board's copper, delivered as an ephemeral
MCP capability or written by the CLI to a new workspace path. Two renders of an unchanged board are
byte-identical, and the evidence records every input that changes the bytes.

**Route.** Bounded integer A* candidates on a documented rectangular Board IR subset. A two-pad net
routes as one path; a wider net routes as a deterministic spanning tree over its components. Routing
avoids existing foreign-net pads, segments at any angle, through vias, rectangular and polygon track
keepouts, and conservative solid-zone polygon envelopes under exact integer clearance, with
independent lattice, search, and obstacle-work ceilings. Same-net copper is attachment rather than a
refusal, so a partly routed net completes from what is already there.

**Recognize existing connectivity.** A net already joined by existing copper is identified across any
pad count, through same-net through vias between layers, and — behind the opt-in
`include_fill_authority` flag — through poured zone copper, admitted only when a fresh KiCad refill
on a private disposable copy reproduces the board's cached fill exactly. A stale cache is refused
rather than answered from.

**Validate with real KiCad.** Fixed-argument KiCad CLI DRC with source, time, size, schema, and
stale-context guards. Internal candidate-bound DRC evidence ties an exact replayed candidate to its
Board IR base, original KiCad bytes, private patched board, and complete patched rule/library
context, without writing a candidate file into the source workspace.

**Judge a placement.** A typed placement-intent contract and deterministic legalizer, surfaced as a
non-mutating preview. Seven rule kinds name objects only by scene references and carry exact integer
parameters; the language has no way to state an absolute coordinate or to permit an overlap. A
candidate proves exactly three things: pad overlap, board-outline containment, and keepout respect.

**Build a schematic.** Immutable Circuit Intent IR `0.1.0` for bounded two-pin resistor/capacitor
topology, with a strict codec, canonical content digest, and deterministic in-memory KiCad
`20250114` schematic renderer using original embedded symbols. The shared build service requires two
byte-identical renders.

**Apply a route — the only mutating operation.** Off by default behind an exact
`COPPER_MCP_ALLOW_APPLY` flag; over MCP it additionally needs a single-use token bound to the exact
candidate, board revision, and path, verified against a key that exists only inside the running
process. The patch is spliced in so every untouched byte stays bit-identical, the board digest is
compared twice, a timestamped pre-apply copy is written first, and publication is an atomic replace
that is verified afterwards and rolled back if it fails.

**Watch a live editor, read-only.** An optional official `kicad-python` IPC observer and KiCad
PCB-editor plugin that report only a live board digest, version compatibility, and bounded object
counts, plus an `observe_live_board_scene` bridge that converts the exact active-editor snapshot
into Circuit Scene `0.2.0` geometry. They never mutate KiCad or expose board text, net names, UUIDs,
or geometry beyond the scene contract.

Plus professional CI, CodeQL, dependency auditing, release automation, issue forms, and
[project ledgers](docs/ledgers/README.md). See the [roadmap](docs/roadmap.md) for what comes next.

## What CopperMCP does not claim

This section is deliberately as prominent as the capability list. In this project every claim is
bound to evidence or listed here as an explicit non-claim, and a value that cannot be verified is
modelled as a one-value literal (`not_run`, `not_modelled`, `inconclusive`) rather than implied.

**Routing.**

- **Nothing has been routed by CopperMCP on a real board that needed it.** Every net on the
  reference board was already routed by its designer. Routing is proven on purpose-built fixtures
  with real KiCad DRC, not yet on a board genuinely requiring new copper. This is the project's
  largest empirical gap.
- Multi-pin nets route as a deterministic spanning tree. **Steiner optimality is not claimed.**
- Zone fill is **not** routing authority. Verified fill informs connectivity only; the routing
  obstacle model continues to use the conservative zone envelope.
- Routing succeeds only for the documented Board IR and single-layer subset. Anything else returns a
  typed diagnostic rather than a guess.

**Placement.**

- **There is no placement solver, and nothing applies a placement** through the released surface.
- Courtyard overlap is reported as `not_modelled` — the side-aware legality evaluator is not
  implemented.
- Pad overlap is deliberately three-valued: `inconclusive` means neither clearance nor collision
  could be proven, not that something is wrong.
- A placement candidate is **not** bound to KiCad DRC evidence.

**Apply.**

- Route patches only. There is no merge, no lock override, and no batch apply.
- The pre-apply copy is **not a KiCad undo step**. Restoring it means copying it back yourself.
- An applied board carries **no DRC evidence**. What is verified is that every untouched byte is
  identical, that the result reparses, and that its Board IR is the original plus the patch.

**Schematics and validation.**

- Schematic builds report KiCad parsing, ERC, and schematic-to-board parity as `not_run`. Electrical
  validation is `not_run` and board readiness is false.
- Schematic-to-board conversion, footprint assignment, and placement remain manual.
- **DRC-clean is not electrical, signal-integrity, manufacturability, or hardware review.**

**Renders and evidence.**

- Renders are whole-board even for a windowed scene, and are advisory. Where a render and the scene
  disagree, **the scene is authoritative**.
- Candidate DRC evidence is an unsigned, redacted in-toto Statement payload. It is deterministic and
  machine-checkable, but **not signed, persisted, or wrapped in DSSE**.
- The ledgers are a transparency record, **not a cryptographic transparency log**. Git history is
  the only integrity mechanism.
- Artifact capability expiry blocks access but is **not a secure memory-erasure promise**.
- Unsafe-filesystem detection is best effort: a negative means *not detected*, never *known safe*.

**Scope.**

- Direct AI mutation of KiCad files or live editor state is **not part of this architecture** and is
  not planned.
- Live routing, placement, DRC, and apply against a running editor remain separate, unimplemented
  gates.

The [project state handoff](docs/handoff/project-state.md) records these limitations in engineering
detail, and the [risk register](docs/ledgers/risk-register.md) tracks the open ones.

## Architecture

```text
KiCad IPC / board files        MCP clients / CLI
           \                       /
            \                     /
      versioned IRs + services
                      |
           deterministic router contract
                      |
          immutable candidate + provenance
                      |
        internal checks + authoritative KiCad DRC
                      |
               explicit user apply
```

MCP is an external adapter, not an internal dependency of the routing engine. The reference core is
currently Python so it is executable and reviewable everywhere; performance-critical Rust or GPU
backends will implement the same stable routing contract. Read the
[architecture overview](docs/architecture/overview.md) and [ADRs](docs/adr/README.md) before changing
this boundary.

## Audio Board Lab

![CopperTone stereo line-buffer engineering preview](hardware/coppertone-buffer/media/coppertone-buffer-top.png)

The Audio Board Lab publishes open KiCad designs that exercise CopperMCP against real audio-PCB
workflows. **Lab #001 — [CopperTone](hardware/coppertone-buffer/README.md)** is a 52 mm × 30 mm,
two-layer OPA1656 stereo line-buffer preview with checked-in board source, BOM, Gerbers, drill files,
STEP assembly, renders, constraints, provenance, and a one-command KiCad 10 validation gate. The
recorded KiCad 10.0.5 run reports 0 DRC violations, 0 unconnected items, and 0 unrouted items.

CopperTone is a board-first engineering preview, not a fabrication-approved or electrically
validated product. It has no source schematic, ERC, assembled prototype, or audio measurements yet;
its hardware sources are separately licensed under CERN-OHL-S-2.0. CopperMCP inspected and validated
the artifact but did not autoroute or apply its copper.

## Research direction

The [open autorouter research package](docs/research/README.md) compares current open routing tools
and records the evidence behind CopperMCP's CPU-first roadmap: exact integer geometry, A*/maze search,
PathFinder-style negotiated congestion, conflict-aware parallelism, bounded exact repair, profiled GPU
kernels, and optional typed ML policy hooks. Deterministic code and KiCad validation remain the
authority for every copper result.

The [audio circuit benchmark intake](docs/research/audio-circuit-benchmarks.md) turns public DIY
catalogs into reference-only challenge categories without copying their circuits. A checked,
network-free corpus runs original or explicitly open artifacts through the same Board IR and route
preview services used by MCP.

The longer-term MCP north star is a versioned **Circuit Scene IR** that joins semantic circuit
meaning with bounded visual observation. Models may propose placement intent and compare immutable
placement previews or candidates; deterministic code remains responsible for snapping, connectivity,
clearance, provenance, validation, and any separately authorized apply. Circuit Scene IR, placement
preview/candidates, the read-only live IPC observer, and the read-only IPC-to-scene bridge now
exist; live placement/routing action gates, placement apply, and direct AI mutation remain future
work.

## Documentation

**[Start at the documentation index](docs/README.md)** — it says what every document owns.

The most-used entry points:

| | |
|---|---|
| [Usage guide](docs/usage.md) | Every CLI command and MCP tool |
| [Architecture overview](docs/architecture/overview.md) | System boundaries |
| [Security and threat model](docs/architecture/security-model.md) | Assets, adversaries, invariants |
| [ADRs](docs/adr/README.md) | Durable decisions and their tradeoffs |
| [Roadmap](docs/roadmap.md) | What comes next |
| [Handoff](docs/handoff/README.md) | Current state, for a continuing maintainer or agent |

## Contributing

Contributions are welcome, particularly reproducible boards, geometry tests, routing algorithms,
KiCad integration, benchmark infrastructure, and documentation. Please read
[CONTRIBUTING.md](CONTRIBUTING.md), the [Code of Conduct](CODE_OF_CONDUCT.md), and existing ADRs first.

Private or customer PCB designs must not be attached to public issues. Use minimal synthetic
reproductions or sanitized open designs.

## Versioning and status

CopperMCP follows [Semantic Versioning](https://semver.org/) and
[Keep a Changelog](https://keepachangelog.com/). Before `1.0.0`, minor releases may intentionally
change experimental contracts with migration notes. See [CHANGELOG.md](CHANGELOG.md) and the
[release ledger](docs/ledgers/release-ledger.md).

## License

Except where a directory says otherwise, CopperMCP software and documentation are licensed under the
[Apache License 2.0](LICENSE). Audio Board Lab hardware sources carry their own clearly identified
open-hardware license; CopperTone uses [CERN-OHL-S-2.0](hardware/coppertone-buffer/LICENSE). Test
fixtures and contributed datasets must include compatible provenance and licensing metadata.
