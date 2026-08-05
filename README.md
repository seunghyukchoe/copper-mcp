# CopperMCP

[![CI](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml)
[![CodeQL](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/seunghyukchoe/copper-mcp)](https://github.com/seunghyukchoe/copper-mcp/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-MVP--alpha-blue.svg)](docs/roadmap.md)

**CopperMCP is a local-first, open-source PCB automation platform designed for deterministic routing,
MCP-based tools, and optional AI policy plugins.**

> [!IMPORTANT]
> CopperMCP `0.4.x` is an MVP-alpha. It provides secure board inspection, authoritative read-only
> KiCad DRC summaries, stable manifests, candidate validation, MCP contracts, a bounded
> non-mutating route preview, and bounded Circuit Intent delivery as a deterministic KiCad
> schematic. The CLI may explicitly create one new schematic or one new board render, always at a
> path that does not already exist; over stdio only, the schematic and render tools may each mint
> one short-lived, non-enumerable artifact capability. Route apply is the sole mutating path and is
> disabled by default behind explicit operator authorization; placement never mutates a board.
>
> `0.4` completes the *observation* surface and opens *placement*. A board can now be read as a
> typed Circuit Scene — region-scoped, exact integer geometry, footprint pose and pad ownership,
> objects named by stable references,
> and every string the board's author controls quarantined in a separate untrusted collection that
> is off by default. An opt-in render turns the same board into a deterministic, digest-bound SVG
> of its copper; two renders of an unchanged board are byte-identical, and silkscreen and
> fabrication layers are excluded because KiCad embeds their text literally in the output.
> Placement arrives as a typed intent language that cannot express an illegal result, judged by a
> deterministic legalizer.
>
> The released `0.4` line is non-mutating; current unreleased main adds only an operator-gated,
> token-authorized route apply. Placement apply and a placement solver still do not exist,
> courtyard overlap is not yet evaluated and is reported as `not_modelled`, and a placement
> candidate is not bound to KiCad DRC evidence. Pad overlap is deliberately three-valued, so `inconclusive` means neither
> clearance nor collision could be proven rather than that anything is wrong. Connectivity
> *recognition* from `0.3` is unchanged: a net already joined by existing copper is identified
> across any pad count, through same-net vias between layers, and — with the opt-in
> `include_fill_authority` flag — through poured zone copper whose cache KiCad has just confirmed
> still matches the board.

## Why this project exists

Existing open autorouters provide useful geometry and negotiated-congestion baselines, but there is
no broadly adopted open platform that combines reproducible routing, safe agent tools, learned
policy hooks, KiCad-native workflows, and transparent benchmarks. CopperMCP is building that layer
without putting an LLM in charge of electrical correctness.

The non-negotiable boundary is simple:

- AI may interpret constraints and propose net ordering, corridors, cost weights, and repairs.
- Deterministic code owns geometry, connectivity, DRC, provenance, and file mutation.
- Generated work remains an immutable candidate until a user validates and explicitly applies it.

## Current capabilities

- Read-only, bounded inspection of documented `.kicad_pcb` files.
- Workspace confinement, including protection against parent-path and symlink escapes.
- SHA-256 board revisions and versioned JSON schemas.
- Immutable Board IR `0.2.0` with exact integer units, typed constraints, canonical digests,
  first-class footprint pose/side/lock/pad ownership, simple orthogonal courtyard rings, and a bounded
  fail-closed converter for a documented KiCad subset. The 0.1 schema remains available as immutable
  compatibility evidence; migration re-converts the original board rather than inventing parents.
- Immutable Circuit Intent IR `0.1.0` for bounded two-pin resistor/capacitor topology, with a strict
  codec, canonical content digest, and deterministic in-memory KiCad `20250114` schematic renderer
  using original embedded symbols. The shared build service validates and normalizes structured
  content and requires two byte-identical renders. The CLI can explicitly create one new
  workspace-confined `.kicad_sch`; stdio MCP returns redacted build metadata plus an opaque,
  15-minute access capability rather than schematic bytes in normal tool output. Expiry makes the
  capability unreadable; it is not a secure memory-erasure promise.
- Fixed-argument KiCad CLI DRC with source, time, size, schema, and stale-context guards.
- Internal candidate-bound DRC evidence tying an exact replayed candidate to its Board IR base,
  original KiCad bytes, private patched board, complete patched rule/library context, and strict
  aggregate KiCad summary without writing a candidate file into the source workspace.
- Candidate-manifest validation and correctness-first comparison.
- Bounded integer A* candidates on a documented rectangular Board IR subset. A two-pad net routes as
  one path; a wider net routes as a deterministic spanning tree over its components, which connects
  every pad without claiming Steiner optimality. Routing avoids existing foreign-net pads, segments
  at any angle, through vias, rectangular and polygon track keepouts, and conservative solid-zone
  polygon envelopes under exact integer clearance, with independent lattice, search, and
  obstacle-work ceilings, plus replay-bound serialization to new disposable KiCad bytes when every
  modeled source geometry object has a native UUID/tstamp. Same-net copper is attachment rather than
  a refusal, so a partly routed net completes from what is already there. Zone fill is still not
  routing authority: verified fill informs connectivity only, and the routing obstacle model
  continues to use the conservative zone envelope.
- Connectivity recognition for a net that is already joined: across any pad count, across layers
  through same-net through vias, and — behind the opt-in `include_fill_authority` flag — through
  poured zone copper, admitted only when a fresh KiCad refill on a private disposable copy
  reproduces the board's cached fill exactly. A stale cache is refused rather than answered from.
- Read-only Board IR structural inspection that reports whether a board is representable by the
  supported subset, using counts and digests rather than geometry, names, or identities.
- A bounded, non-mutating route preview over MCP and the CLI that validates an untrusted request,
  proposes one candidate under a wall-clock deadline, and optionally binds it to aggregate
  authoritative KiCad DRC evidence. It has no durable export, persistence, job, source mutation, or
  apply path.
- Circuit Scene IR `0.2.0` observation over MCP and the CLI: a mandatory region — an exact
  nanometre box or one object reference with a radius — returns full-precision integer geometry for
  the objects that overlap it, split into `static` (outline, footprints, pads, keepouts, rules) and `mutable`
  (segments, arcs, vias, zones) so code meaning to read only the givens cannot iterate over both.
  Footprints expose revision-bound origin, rotation, side, lock state, pad IDs, and rectangular
  courtyard rings; relationship IDs consume the bounded scene-detail budget rather than expanding
  a response for free.
  Objects are named by the Board IR references they already carry, each declaring how durable that
  reference is, and truncation against object, vertex and annotation ceilings is reported
  explicitly rather than left to be inferred. Board text is off by default and, when requested,
  appears only in a separately typed `annotations` collection whose trust field has exactly one
  permitted value; net names never appear at all.
- An opt-in deterministic board render delivered as an ephemeral MCP capability or written by the
  CLI to a new workspace path. Two renders of an unchanged board are byte-identical after a named
  `title-line-v1` canonicalization, the evidence records every input that changes the bytes, and a
  truncated export is refused rather than digested. The render draws copper and the board outline
  only; it is whole-board rather than region-scoped, and it is advisory — where it and the scene
  disagree, the scene is authoritative.
- A typed placement-intent contract and deterministic legalizer, surfaced as a non-mutating
  placement preview over MCP and the CLI. Seven rule kinds name objects only by scene references
  and carry exact integer parameters; the language has no way to state an absolute coordinate or to
  permit an overlap, so every position in a response is derived by CopperMCP and snapped to an
  explicit grid. A candidate is immutable, bound to both the board snapshot and its revision-bound
  Board IR footprint view, and proves exactly three things: pad overlap, board-outline containment,
  and keepout respect. Pad overlap is three-valued, courtyard overlap is reported as `not_modelled`
  because the side-aware legality evaluator is not implemented yet, and `infeasible_constraints`
  is never conflated with `budget_exhausted`. Nothing applies a placement, there is no solver, and a placement
  candidate carries no KiCad DRC evidence. Locked footprints reject movement proposals.
- Operator-gated, token-authorized application of a route candidate to a board — the only
  mutating operation in the project. Off by default behind an exact `COPPER_MCP_ALLOW_APPLY`
  flag; over MCP it additionally needs a single-use token bound to the exact candidate, board
  revision and path, verified against a key that exists only inside the running process. The
  patch is spliced in so every untouched byte stays bit-identical, the board digest is
  compared twice, a timestamped pre-apply copy is written first, and publication is an atomic
  replace that is verified afterwards and rolled back if it fails. Route patches only: nothing
  applies a placement, there is no merge, no lock override, and no batch apply.
- MCP tools and a stable CLI over the same application services.
- An optional official `kicad-python` IPC observer and KiCad PCB-editor plugin that report only a
  live board digest, version compatibility, and bounded object counts; they never mutate KiCad or
  expose board text, net names, UUIDs, or geometry.
- A read-only `observe_live_board_scene` bridge that converts the exact active-editor snapshot into
  Circuit Scene `0.2.0` geometry under `board: "live"`, with optional stale board/snapshot digest
  checks. Live routing, placement, DRC, and apply remain separate gates.
- Professional CI, CodeQL, dependency auditing, release automation, issue forms, and project ledgers.

See the [roadmap](docs/roadmap.md) for routing and KiCad IPC milestones.

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

The [audio circuit benchmark intake](docs/research/audio-circuit-benchmarks.md) also turns public DIY
catalogs into reference-only challenge categories without copying their circuits. A checked,
network-free corpus runs original or explicitly open artifacts through the same Board IR and route
preview services used by MCP. A separate independently authored RC intent fixture now exercises
canonical logical topology and deterministic KiCad schematic rendering. It still records ERC,
source-to-board parity, value selection, board generation, electrical validation, and fabrication
readiness as missing capabilities rather than inferring them.

The longer-term MCP north star is a versioned **Circuit Scene IR** that joins semantic circuit
meaning with bounded visual observation. Models may propose placement intent and compare immutable
placement previews or candidates; deterministic code remains responsible for snapping,
connectivity, clearance, provenance, validation, and any separately authorized apply. Direct AI
mutation of KiCad files or live editor state is not part of this architecture. Circuit Scene IR,
placement preview/candidates, the read-only live IPC observer, and the read-only IPC-to-scene
bridge now exist; live placement/routing action gates, placement apply, and direct AI mutation
remain future work.

## Quick start

Prerequisites: Python 3.11 or newer.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,security]"
make check
```

To inspect a running KiCad PCB Editor through the official local IPC binding, install the optional
extra and enable KiCad's IPC server in the editor preferences:

```bash
python -m pip install -e ".[kicad]"
export COPPER_MCP_WORKSPACE=/absolute/path/to/boards
copper-mcp-server
```

Call the read-only MCP tool `inspect_live_board` for a redacted digest/metadata probe. To request
semantic geometry from the active editor, call `observe_live_board_scene` with the same constraints
and region fields as `observe_board_scene`, but set `board` to the literal `live`. KiCad 9/10
requires a running GUI session, and the tools refuse a newer KiCad than the installed
`kicad-python` binding by default. Include both returned digests on a repeat call when you need a
stale-session refusal; live routing and placement still require file-backed action gates.

Inspect a board without modifying it:

```bash
copper-mcp --workspace /absolute/path/to/boards inspect example.kicad_pcb
```

Run authoritative KiCad DRC and return only bounded aggregate evidence:

```bash
export COPPER_MCP_KICAD_CLI=/absolute/path/to/kicad-cli  # optional when discoverable
copper-mcp --workspace /absolute/path/to/boards drc example.kicad_pcb
```

The DRC adapter never accepts arbitrary KiCad flags and never requests zone refill or board save.
It mirrors the board, matching project/rule files, and workspace-local KiCad library assets into a
private snapshot through descriptor-anchored, no-symlink reads. File-table dependencies are accepted
only when they remain inside that snapshot; environment-expanded, absolute, remote, and plugin URIs
are rejected before KiCad starts. The child runs from a private working directory and is isolated
from the invoking user's global configuration and environment. Snapshot bytes and child side
effects are bounded cumulatively, report growth is limited in the child process, and results are
discarded when captured context changes. Context discovery also has file-count and wall-clock
ceilings, and the pre-run byte snapshot is released before KiCad starts. Keep KiCad projects
self-contained below the configured workspace, with any libraries referenced as project-relative
`${KIPRJMOD}/` paths from an `fp-lib-table` or `sym-lib-table` beside the board file. No other
library location is read, and design-block library entries are rejected. DRC-clean is not a
substitute for electrical, signal-integrity, manufacturability, or hardware review.

Check whether a board is representable by the supported Board IR subset:

```bash
copper-mcp --workspace /absolute/path/to/boards board-ir example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

Observe a region of a board as a typed semantic scene. The region is mandatory — either an exact
nanometre bounding box or one object reference with a radius — because full detail inside a stated
window is more useful than a summary of everything:

```bash
copper-mcp --workspace /absolute/path/to/boards observe-scene example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 \
  --region 0 0 30000000 30000000
```

Objects come back split into `static` (outline, footprints, pads, keepouts, rules) and `mutable`
(segments, arcs, vias, zones), each named by the Board IR reference it already carries so you can
refer to it in a later call. Footprints include pose, side, lock, pad ownership, and the supported
courtyard rings. Board text is omitted unless `--include-annotations` is passed, and even then it is
confined to a separate `annotations` collection marked untrusted: it is written by whoever authored
the board, and it is data to be read, never instructions to follow.

Add `--render out/board.svg` to also write a deterministic SVG of the board's copper. The path
must be new and end in lowercase `.svg`; observation never overwrites a file. Two renders of an
unchanged board are byte-identical, and the response records the digest and the exact inputs it
was taken under. The render draws copper and the board outline only - silkscreen and fabrication
layers are excluded because KiCad embeds their text literally in the SVG - and it covers the whole
board rather than the requested region. It is an orientation aid: where it and the scene disagree,
the scene is right.

Apply a previewed route candidate to a board. **This is the only command that changes a board
file**, it is disabled unless you opt in, and it applies route patches only:

```bash
COPPER_MCP_ALLOW_APPLY=1 copper-mcp --workspace /absolute/path/to/boards \
  apply-candidate example.kicad_pcb \
  --candidate preview.json --expect-board-revision sha256:... \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

Close KiCad first. A lockfile beside the board is a hard refusal that names the file and is
never removed for you, because pcbnew has no external-change watcher and would silently
overwrite the applied board the next time it saves. The board digest you pass as
`--expect-board-revision` is compared before the edit and again immediately before the file is
replaced; if anything moved, the apply is refused and **never silently re-routed**. A
timestamped pre-apply copy is written beside the board first and its path returned — **that copy
is the undo, and restoring it means copying it back yourself.** It is not a KiCad undo step, and
KiCad's own `-bak` files are never touched. Over MCP the same operation additionally requires a
single-use token issued by `preview_route`, so a model cannot apply anything you did not
preview. An applied board carries no DRC evidence: what is verified is that every untouched byte
is identical, that the result reparses, and that its Board IR is the original plus the patch.

Validate a proposed footprint placement without modifying the board:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-placement example.kicad_pcb \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 \
  --subject footprint:kicad:<uuid> --subject footprint:kicad:<uuid>
```

Placement rules and proposals name objects only by the references a scene returned - there is no
way to write an absolute coordinate - so every position in the response was derived by CopperMCP
and snapped to an explicit grid. The response proves three things and claims nothing else: pad
overlap, board-outline containment, and keepout respect. `pad_overlap` is three-valued, so
`inconclusive` means neither clearance nor collision could be proven rather than that something is
wrong. Courtyard overlap is reported as `not_modelled` because the bounded side-aware evaluator is
not implemented yet. **The preview never applies a placement and is not bound to KiCad DRC
evidence**; a
placement also invalidates any route candidate bound to the same board revision.

Preview one route without modifying the board, then optionally validate it with KiCad:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-route example.kicad_pcb \
  --net AUDIO --layer F.Cu \
  --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000 --drc
```

An AI client does not need the hidden KiCad net name. It can copy a `net_id`, `board_revision`, and
`snapshot_digest` from `observe_board_scene` into the revision-bound selector:

```bash
copper-mcp --workspace /absolute/path/to/boards preview-route example.kicad_pcb \
  --net-ref-id net:name:... \
  --expect-board-revision sha256:... --expect-snapshot-digest sha256:... \
  --layer F.Cu --clearance-nm 250000 --track-width-nm 250000 \
  --via-diameter-nm 800000 --via-drill-nm 400000
```

The preview writes no file, creates no job, and stores no candidate. A changed board or Board IR
snapshot returns `stale_revision` instead of routing against state the client has not observed. It
succeeds only for the documented Board IR and single-layer routing subset; anything else returns a
typed diagnostic or bounded conversion-code counts. The response contains the geometry CopperMCP
generated, so hosts that must not disclose generated copper to a model should not enable the
`preview_route` tool.

Build a deterministic schematic from a strict Circuit Intent snapshot. This is the only current
durable schematic operation, and the output must be a new path inside the configured workspace:

```bash
mkdir -p /absolute/path/to/boards/artifacts  # artifacts/ is ignored by this repository
copper-mcp --workspace /absolute/path/to/boards render-schematic \
  intent/rc-low-pass.json --output artifacts/rc-low-pass.kicad_sch
```

The service records topology, digest, provenance, and deterministic-replay checks as passed. It does
not run KiCad on each build and reports KiCad parsing, ERC, and schematic-to-board parity as
`not_run`; electrical validation is also `not_run`, and board readiness is false. The CLI refuses
traversal, symlinks, a suffix other than exact lowercase `.kicad_sch`, and any existing output
rather than silently overwriting it. The input is captured from one held descriptor, and output
creation stays anchored to a held workspace-directory descriptor. Schematic-to-board conversion,
footprint assignment, and placement remain manual; the generated schematic is not automatically
connected to the board-preview workflow.

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

On stdio, `render_circuit_schematic` accepts validated structured Circuit Intent content and returns
redacted metadata plus a non-enumerable `pcb://artifacts/schematic/...` capability. Its exact bytes
are accessible for at most 15 minutes in a 16-entry, 16 MiB process-local store. Expired entries are
removed lazily on later store activity or process exit, so expiry blocks access but does not promise
immediate memory erasure. Fetching the resource reveals the schematic topology, so hosts decide
whether to save it locally or disclose it to a model. Schematic artifact tools and resources are
disabled over streamable HTTP in this MVP.

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

## Documentation

- [Project handoff](docs/HANDOFF.md) — current state, invariants, and next steps
- [Codex handoff](docs/HANDOFF_CODEX.md) — task-first onboarding for a continuing agent
- [Project charter](docs/project-charter.md)
- [Architecture](docs/architecture/overview.md)
- [Board IR and KiCad adapter contract](docs/architecture/board-ir.md)
- [Circuit Intent IR and KiCad schematic contract](docs/architecture/circuit-intent.md)
- [Deterministic A* baseline](docs/architecture/routing-baseline.md)
- [MCP contract](docs/architecture/mcp-api.md)
- [Security and threat model](docs/architecture/security-model.md)
- [Development guide](docs/development.md)
- [Autorouter research](docs/research/README.md)
- [Audio Board Lab](hardware/README.md)
- [Roadmap](docs/roadmap.md)
- [Release process](docs/releasing.md)
- [Project ledgers](docs/ledgers/README.md)

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
