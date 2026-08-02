# CopperMCP

[![CI](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/ci.yml)
[![CodeQL](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml/badge.svg)](https://github.com/seunghyukchoe/copper-mcp/actions/workflows/codeql.yml)
[![Release](https://img.shields.io/github/v/release/seunghyukchoe/copper-mcp)](https://github.com/seunghyukchoe/copper-mcp/releases/latest)
[![License](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue.svg)](pyproject.toml)
[![Status](https://img.shields.io/badge/status-pre--alpha-orange.svg)](docs/roadmap.md)

**CopperMCP is a local-first, open-source PCB automation platform designed for deterministic routing,
MCP-based tools, and optional AI policy plugins.**

> [!IMPORTANT]
> CopperMCP is pre-alpha. The current `0.1.x` foundation provides secure board inspection,
> authoritative read-only KiCad DRC summaries, stable manifests, candidate validation, MCP
> contracts, and a narrow candidate-only two-pin A* reference. It does not route or modify
> production boards.

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
- Immutable Board IR `0.1.0` with exact integer units, typed constraints, canonical digests, and a
  bounded fail-closed converter for a documented KiCad subset.
- Fixed-argument KiCad CLI DRC with source, time, size, schema, and stale-context guards.
- Candidate-manifest validation and correctness-first comparison.
- Bounded integer A* candidates for one two-pad net on a documented rectangular Board IR subset,
  with independent lattice, search, and obstacle-work ceilings, plus replay-bound serialization to
  new disposable KiCad bytes when every modeled source geometry object has a native UUID/tstamp.
  This synthetic-domain reference has no durable export, authoritative candidate-bound DRC evidence,
  preview, MCP route tool, source mutation, or apply path.
- MCP tools and a stable CLI over the same application services.
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

Run authoritative KiCad DRC and return only bounded aggregate evidence:

```bash
export COPPER_MCP_KICAD_CLI=/absolute/path/to/kicad-cli  # optional when discoverable
copper-mcp --workspace /absolute/path/to/boards drc example.kicad_pcb
```

The DRC adapter never accepts arbitrary KiCad flags and never requests zone refill or board save.
It mirrors the board, matching project/rule files, and workspace-local KiCad library assets into a
private snapshot; bounds that snapshot cumulatively; limits report growth in the child process; and
rejects results when any captured context changes during execution. Context discovery also has file
count and wall-clock ceilings, and the pre-run byte snapshot is released before KiCad starts. Keep
KiCad projects and their project-relative libraries self-contained below the configured workspace.
DRC-clean is not a substitute for electrical, signal-integrity, manufacturability, or hardware
review.

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

## Architecture

```text
KiCad IPC / board files        MCP clients / CLI
           \                       /
            \                     /
             Board IR + services
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

- [Project charter](docs/project-charter.md)
- [Architecture](docs/architecture/overview.md)
- [Board IR and KiCad adapter contract](docs/architecture/board-ir.md)
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
