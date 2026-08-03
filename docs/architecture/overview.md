# Architecture Overview

## System boundaries

CopperMCP separates the stable deterministic domain from transports and experimental policies.

```text
adapters (CLI, MCP, KiCad, files)
                 |
application services and versioned Board IR
                 |
routing contracts + deterministic validation
                 |
backend implementations (CPU first; Rust/GPU later)
                 |
immutable candidate store and provenance
```

MCP does not call geometry primitives directly. It invokes the same application services used by
the CLI and tests. A future KiCad plugin snapshots editor state, releases the synchronous IPC
connection while routing runs, and applies only a validated candidate tied to the unchanged base
revision.

## Components

| Component | Responsibility |
|---|---|
| `config.py` | Validate process settings and establish the workspace boundary. |
| `security.py` | Resolve, constrain, and size-limit untrusted filesystem inputs. |
| `models.py` | Stable board and candidate contract models. |
| `kicad_file.py` | Read-only MVP inspection; never used to write geometry. |
| `board_ir/` | Canonical integer board snapshots, strict codec, geometry validation, and digests. |
| `adapters/kicad_board_ir.py` | Bounded, read-only conversion of the documented KiCad subset. |
| `adapters/kicad_route_patch.py` | Pure replay-bound serialization to new disposable KiCad bytes. |
| `kicad_cli.py` | Fixed-argument ordinary and candidate-bound DRC over private context snapshots. |
| `tools.py` | Pure application services shared by adapters. |
| `routing/contracts.py` | Exact candidate, cost, settings, result, and backend-neutral contracts. |
| `routing/astar.py` | Bounded integer two-pin A* reference; candidate-only and fail-closed. |
| `request_boundary.py` | Shared untrusted-request validation primitives for every public service. |
| `board_ir_service.py` | Read-only Board IR conversion check and structural description. |
| `route_preview.py` | The public non-mutating route preview service. |
| `mcp_server.py` | MCP tools/resources and transport configuration. |

Board IR `0.1.0` is the domain and source-adapter foundation. A narrow deterministic
[two-pin routing baseline](routing-baseline.md) now produces immutable in-memory candidates for
supported synthetic Board IR inputs. The pure adapter can serialize an exact replayed candidate in
memory, an internal service binds that private derivative to strict aggregate KiCad DRC evidence,
`preview_route` exposes that pipeline as a bounded, non-mutating public proposal, and
`inspect_board_ir` reports whether a board is representable at all. No durable
export, MCP Board IR, route/evidence resource, routing job, candidate persistence, source mutation,
or apply path is implemented. See
[Board IR and KiCad adapter contracts](board-ir.md),
[ADR-0005](../adr/0005-canonical-board-ir.md),
[ADR-0006](../adr/0006-bounded-deterministic-astar.md),
[ADR-0007](../adr/0007-disposable-kicad-candidate-snapshot.md),
[ADR-0008](../adr/0008-candidate-bound-kicad-drc.md),
[ADR-0009](../adr/0009-non-mutating-route-preview.md), and
[ADR-0010](../adr/0010-board-ir-inspection-service.md).

## Candidate lifecycle

1. Capture an immutable board revision.
2. Validate constraints and scope.
3. Produce one or more route candidates with complete provenance.
4. Run internal connectivity/geometry checks.
5. Run authoritative KiCad DRC and future physics/DFM checks.
6. Compare candidates with hard correctness first.
7. Recheck the live board revision.
8. Apply one approved patch as a single undoable operation.

No lifecycle stage may mutate the base snapshot. See [ADR-0001](../adr/0001-candidate-first.md).

## Performance evolution

The reference implementation begins in Python to keep contracts executable and easy to review.
Profiling will identify kernels that earn a Rust implementation. GPU work begins only after a CPU
baseline, deterministic benchmarks, and end-to-end profiling exist. Backends implement the same
`RoutingBackend` contract rather than leaking hardware details into MCP tools.
