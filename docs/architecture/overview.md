# Architecture Overview

## System boundaries

CopperMCP separates the stable deterministic domain from transports and experimental policies.

```text
adapters (CLI, MCP, KiCad, files)
                 |
application services and versioned logical/physical IRs
                 |
routing contracts + deterministic validation
                 |
backend implementations (CPU first; Rust/GPU later)
                 |
immutable candidate store and provenance
```

MCP does not call geometry primitives directly. It invokes the same application services used by
the CLI and tests. The KiCad plugin and live-scene adapter snapshot only through bounded local IPC;
the scene is read-only and any future action must keep a validated candidate tied to an unchanged
live revision before it can release the synchronous connection.

## Components

| Component | Responsibility |
|---|---|
| `config.py` | Validate process settings and establish the workspace boundary. |
| `security.py` | Descriptor-anchor bounded workspace reads and create-only outputs without following path-component symlinks. |
| `models.py` | Stable board and candidate contract models. |
| `kicad_file.py` | Read-only MVP inspection; never used to write geometry. |
| `board_ir/` | Canonical integer board snapshots, strict codec, geometry validation, and digests. |
| `circuit_ir/` | Canonical logical topology snapshots, strict codec, semantic validation, and digests. |
| `adapters/kicad_board_ir.py` | Bounded, read-only conversion of the documented KiCad subset. |
| `adapters/kicad_route_patch.py` | Pure replay-bound serialization to new disposable KiCad bytes. |
| `adapters/kicad_placement_patch.py` | Internal, source-preserving projection of supported placement candidates to disposable KiCad bytes. |
| `adapters/kicad_schematic.py` | Pure deterministic rendering of the Circuit Intent subset to new in-memory KiCad schematic bytes. |
| `circuit_intent_service.py` | Validate or normalize Circuit Intent and require byte-identical double rendering before delivery. |
| `schematic_artifacts.py` | Bounded process-local capability store for stdio schematic resource delivery. |
| `kicad_cli.py` | Fixed-argument ordinary and candidate-bound DRC over private snapshots, confined file-table dependencies, environment/state roots, and working directory. |
| `kicad_ipc.py` | Optional official `kicad-python` adapter for redacted observation and exact source hand-off over local IPC. |
| `live_layered_route_preview.py` | Session-, source-, and Board IR-bound live two-layer route proposal; candidate-only and no editor mutation. |
| `tools.py` | Pure application services shared by adapters. |
| `routing/contracts.py` | Exact candidate, cost, settings, result, and backend-neutral contracts. |
| `routing/astar.py` | Bounded integer two-pin A* reference; candidate-only and fail-closed. |
| `routing/layered_astar.py` | Internal abstract two-layer A* oracle; not a Board IR or KiCad candidate surface. |
| `request_boundary.py` | Shared untrusted-request validation primitives for every public service. |
| `board_ir_service.py` | Read-only Board IR conversion check and structural description. |
| `circuit_scene.py` | Bounded, region-scoped Board IR observation with typed references and quarantined author text. |
| `placement/` | Revision-bound footprint views, typed placement intent, and deterministic preview/legalization. |
| `route_preview.py` | The public non-mutating route preview service. |
| `routing/jobs.py` / `routing/job_worker.py` / `routing/candidate_store.py` | Transport-independent revision-safe job records, bounded single-worker lease/recovery, and redacted candidate manifests; no route-geometry export or MCP Tasks adapter. |
| `mcp_server.py` | MCP tools/resources and transport configuration. |

Board IR `0.2.0` is the domain and source-adapter foundation. It adds immutable footprint pose,
side, lock state, total pad ownership, and bounded board-frame rectangular courtyard rings to the
geometry already used by routing. A narrow deterministic
[two-pin routing baseline](routing-baseline.md) now produces immutable in-memory candidates for
supported synthetic Board IR inputs. The pure adapter can serialize an exact replayed candidate in
memory, an internal service binds that private derivative to strict aggregate KiCad DRC evidence,
`preview_route` exposes that pipeline as a bounded, non-mutating public proposal, and
`inspect_board_ir` reports whether a board is representable at all. The separately authorized,
default-off `apply_candidate` and `apply_placement_candidate` surfaces apply replay-verified route
patches and a bounded placement-pose subset respectively. An internal, bounded SQLite routing-job
ledger now persists redacted revision-safe records, but it does not execute work, retain candidate
geometry, expose an MCP task handle, or grant export authority. No durable candidate export, raw
Board IR MCP resource, route/evidence resource, or live-editor apply path is implemented. The live IPC observer and
`observe_live_board_scene` bridge are read-only and do not change this mutation boundary; live
route/placement action authority is not implemented. See
[Board IR and KiCad adapter contracts](board-ir.md),
[ADR-0005](../adr/0005-canonical-board-ir.md),
[ADR-0026](../adr/0026-first-class-footprints-in-board-ir.md),
[ADR-0006](../adr/0006-bounded-deterministic-astar.md),
[ADR-0007](../adr/0007-disposable-kicad-candidate-snapshot.md),
[ADR-0008](../adr/0008-candidate-bound-kicad-drc.md),
[ADR-0009](../adr/0009-non-mutating-route-preview.md),
[ADR-0010](../adr/0010-board-ir-inspection-service.md), and
[ADR-0030](../adr/0030-live-ipc-circuit-scene-binding.md).

Circuit Intent IR `0.1.0` is a separate logical contract for a bounded two-pin passive subset. Its
pure adapter produces a new content-addressed KiCad schematic derivative with embedded original
symbols, empty footprints, and no board eligibility. A protocol-independent service validates and
normalizes the logical content, renders twice, and returns a redacted build record. The CLI may
explicitly create one new workspace schematic without overwrite; the stdio-only MCP adapter returns
the same metadata plus one opaque, expiring resource capability. Neither delivery path performs a
per-build KiCad parse, ERC, electrical validation, or schematic-to-board parity check; the reusable
passive-subset verifier can separately check exact render replay plus KiCad component/connectivity
parity. Capability
access expires after 15 minutes, but expired bytes are reclaimed lazily on later store activity or
process exit; no secure memory-erasure claim is made. See the
[Circuit Intent and schematic contract](circuit-intent.md) and
[ADR-0014](../adr/0014-canonical-circuit-intent.md), and
[ADR-0015](../adr/0015-bounded-circuit-schematic-delivery.md).

## High-fidelity circuit perception and placement north star

Circuit Scene IR `0.2.0` is the current bounded structured board observation contract. It exposes
revision-bound footprint pose, side, lock state, pad ownership, and supported courtyard rings beside
the existing pads and copper. Region scoping, typed reference durability, object/detail ceilings,
and quarantined board-author text keep the observation explicit; an optional normalized render is
an advisory orientation aid, never geometry authority. It does not yet join Board IR with logical
Circuit Intent, so that semantic fusion remains a north star rather than a current claim.

Observed net identities are directly actionable by `preview_route`: the caller copies a scene
`net_id` together with that scene's board revision and snapshot digest, and the router uses the
identity without exposing or reconstructing the private KiCad net name. Both revisions are checked
before candidate work, so a stale observation cannot silently route against unseen state. The MCP
surface advertises this as an exclusive, closed request union and returns a fully typed structured
result. This closes one file-backed observation-to-action edge. The read-only live IPC bridge now
converts the exact captured editor serialization into the same Circuit Scene contract and refuses
caller-supplied stale board or snapshot digests; it still does not grant live route/placement
authority, a policy solver, or an autonomous whole-board loop. `preview_live_route` now adds a
read-only proposal edge over that exact snapshot: it accepts only a scene net reference and both
digests, then returns the deterministic candidate without DRC, fill, apply-token issuance, or
editor mutation. A live action compare-and-swap remains a separate future contract.
`preview_live_placement` now adds the equivalent ref-anchored placement proposal edge over the
same snapshot and deterministic legalizer, with both scene digests required and no editor-write
authority. `preview_live_layered_route` now adds the via-capable two-signal-layer edge over the
same exact serialization, requiring an opaque process-local HMAC-SHA256 session revision over
`KICAD_API_TOKEN` in addition to source and Board IR CAS. It closes official IPC clients and
passes the remaining bounded deadline,
but remains candidate-only: endpoint-via legality, DRC, serializer/export, apply, and real-GUI
success are separate future gates. A live action compare-and-swap remains a separate future
contract.

Placement preview resolves its subjects from the same Board IR snapshot and refuses source bytes
whose revision does not match. Observation now accepts the bounded front- and back-side,
orthogonal, unfilled-rectangular-courtyard subset and imports KiCad's authored child coordinates
without a second mirror. The placement derivative remains deliberately stricter: front side only,
with unsupported topology and back-side edits failing closed. A locked footprint cannot be moved.
`courtyard_overlap` is exact for the bounded rectangular Board IR subset: same-side rings return
`proven_clear` or `violated`, while front/back rings are evaluated independently. Unsupported
topology fails closed before the view exists. Padless footprints remain unplaceable, while their
supported rectangular courtyards stay in the stationary collision envelope. File-backed placement
apply now exists as a separate,
default-off, placement-token-scoped operation for the front-side orthogonal source-preserving
subset; models never write KiCad syntax, mutate a live editor, or bypass deterministic candidate
validation and explicit revision-checked authorization.

An internal placement projection now follows the route adapter's source-preserving pattern for
the supported front-side, orthogonal, unfilled-courtyard subset. It splices only changed footprint
poses and owned absolute pad angles, preserves padless mechanical footprints and all unrelated
bytes, and reparses the disposable result against the expected Board IR transform. This is a
candidate derivative and bounded apply input: placement DRC is private, while side flips, live
compare-and-swap, KiCad undo, general source fidelity, and post-action observation remain separate
gates.

The internal layered search seam now has a narrow Board IR-bound proposal adapter in addition to
the abstract maze oracle: it resolves integer width/clearance/via geometry, conservative foreign
obstacles, and separate track/via keepouts into immutable content-addressed candidates. It remains
proposal-only and is not exported through MCP or the KiCad serializer; source-preserving
segment/via serialization, round-trip checks, and authoritative KiCad DRC are still required
before the production route contract can leave its single-layer boundary. The disposable
serializer now runs the pure bounded `verify_layered_candidate` topology gate first and refuses
endpoint-via geometry until padstack treatment is represented. See [ADR-0036](../adr/0036-board-ir-layered-proposal-adapter.md) and [ADR-0045](../adr/0045-layered-candidate-topology-verifier.md).

## Candidate lifecycle

1. Capture an immutable board revision.
2. Validate constraints and scope.
3. Produce one or more route candidates with complete provenance.
4. Run internal connectivity/geometry checks.
5. Run authoritative KiCad DRC and future physics/DFM checks.
6. Compare candidates with hard correctness first.
7. Recheck the live board revision.
8. Apply one approved patch through a separate, explicitly authorized, revision-checked operation.

No candidate-building lifecycle stage may mutate the base snapshot. Route and bounded placement
apply write recoverable pre-apply copies but are not KiCad undo transactions. See
[ADR-0001](../adr/0001-candidate-first.md).

## Performance evolution

The reference implementation begins in Python to keep contracts executable and easy to review.
Profiling will identify kernels that earn a Rust implementation. GPU work begins only after a CPU
baseline, deterministic benchmarks, and end-to-end profiling exist. Backends implement the same
`RoutingBackend` contract rather than leaking hardware details into MCP tools.
