# MCP API Contract

## Design rules

- MCP is a thin adapter over pure application services.
- Tools use structured inputs and outputs; large artifacts use resource URIs.
- Read-only capabilities ship before mutation.
- Ephemeral artifact creation is distinct from durable filesystem or board mutation.
- Long-running work is backed by a durable internal job model, regardless of MCP client support.
- Network and authorization concerns never enter the geometry layer.

## Implemented tools

| Tool | Side effect | Description |
|---|---|---|
| `server_info` | None | Version, maturity, and honest capability inventory. |
| `inspect_board` | None | Bounded read-only inspection inside the configured workspace. |
| `run_board_drc` | Temporary report only | Fixed-argument KiCad DRC with a bounded, redacted summary. |
| `inspect_board_ir` | None | Read-only Board IR conversion check and structural description. |
| `preview_route` | None, or a temporary report when `include_drc` is set | Bounded, non-mutating two-pin route proposal on the documented Board IR subset. |
| `render_circuit_schematic` | Process-local artifact only; stdio only | Validate structured Circuit Intent, require deterministic replay, and issue one opaque schematic resource capability. |
| `validate_candidate` | None | Validate and normalize candidate metadata. |
| `compare_candidates` | None | Correctness-first deterministic ranking. |

The static resource `pcb://server/manifest` exposes stable server metadata. On stdio only, a
successful `render_circuit_schematic` result also links one non-enumerable dynamic resource of the
form `pcb://artifacts/schematic/{opaque-token}/circuit.kicad_sch`.

`run_board_drc` returns the board SHA-256 revision, a DRC-context revision covering the board,
matching project/custom-rule files, and workspace-local KiCad library assets; KiCad/schema versions;
severity and connectivity counts; violation-type counts; and a hard-correctness pass flag. It
also reports how many DRC check classes KiCad marks ignored, while deliberately omitting their raw
descriptions along with net names, UUIDs, and coordinates. Exit codes `0` (clean) and `5`
(findings reported) are valid only when they agree with the strict report's finding collections;
other exit codes or process/report disagreement fail the tool. Warning-only and exclusion-only
findings can retain a hard-correctness pass flag while still requiring exit code `5`. The snapshot
and report have independent size ceilings, and report growth is limited before KiCad starts.

`inspect_board_ir` takes a workspace-relative `board` and integer `constraints`, and answers
whether the board converts to the supported Board IR before a caller commits to a preview. It
returns the board revision, snapshot and constraint digests, Board IR schema and units, copper
layer identities, and per-collection object counts, or bounded conversion diagnostic-code counts
when the board is outside the subset. It never returns coordinates, net names, pad or net
identities, UUIDs, or source bytes.

`preview_route` takes one request object with a workspace-relative `board`, a KiCad `net` name, a
copper `layer` name, integer `constraints` for the applied net class, and optional `seed`,
`settings`, and `include_drc` fields. Unknown fields, non-integer or out-of-range budgets, booleans
supplied as integers, control characters, and unsupported layer names are rejected before any file
is read. Every response carries a `status` of `routed`, `already_connected`, `not_routed`, or
`unsupported_board`, the board revision, the Board IR snapshot digest when conversion succeeded, and
the validated request. A routed response includes the candidate ID, endpoint pad IDs, integer
geometry, exact cost decomposition, deterministic search metrics, and the resource ceilings that
produced it. Geometry is carried as `patch.paths`, a list of polylines: a two-pin proposal has one,
and a multi-pin proposal has one per merged component, together forming a tree over the net. The
response also reports `pad_count` and the `ordering_policy` that fixed the merge order. An unrouted response carries one typed, non-echoing diagnostic; an unsupported board
carries bounded conversion diagnostic-code counts instead of raw adapter text.

`already_connected` is a terminal success, not a failure: the two pads already share one copper
component on the selected layer, so there is nothing to propose. Its `connection` object carries the
Board IR base revision it is bound to, both endpoint pad IDs, and integer counts of the attachment
segments, component objects, pads and vias involved. A non-zero `vias` count means the connection
was established across copper layers through those vias, so the evidence is multilayer even though
the request names a single layer. It returns no geometry and no diagnostic, and the outcome
deliberately has no `RouteFailureCode`. Clients that switch exhaustively over the previous three
statuses need a fourth branch.

Setting `include_fill_authority` allows poured zone copper to count as connectivity evidence. KiCad
refills a private disposable copy and the recomputed pour must reproduce the board's cache exactly;
matching returns the claim with a `fill_authority` record carrying both digests, the KiCad version
and the island and vertex counts, while a mismatch is refused with the typed `stale_fill` diagnostic
rather than answering from either version. The workspace board is never refilled. The flag is opt-in
because it spawns KiCad, and it changes nothing for a board without zones on the requested net.

Setting `include_drc` binds the proposal to candidate-bound authoritative KiCad DRC evidence, which
returns the same aggregate, redacted summary as `run_board_drc` plus the candidate, source, patched
board, and patched context revisions. The call fails rather than returning a candidate whose
requested evidence is missing or does not bind. On an `already_connected` net the flag is skipped
and `drc_evidence` is `null`, because that rule protects a proposal and none is being made. Preview
writes no file, creates no job, and never returns source board bytes; it does return the geometry it
generated, so a host that must not disclose generated copper to a model should not enable this tool.

`render_circuit_schematic` takes one structured Circuit Intent `content` object. The shared service
performs bounded semantic validation and normalization, computes the intent digest, renders the
accepted snapshot twice, and fails unless both immutable artifacts are byte-identical. Callers do
not submit KiCad S-expressions or calculate the content digest. Ordinary tool output is a redacted
`copper.circuit-schematic-build` record containing schema and format versions, intent and artifact
digests, byte and topology counts, and a resource URI. It omits titles, component references and
values, net and port names, UUIDs, source JSON, and schematic bytes.
Its versioned JSON Schema is
[`schemas/circuit-schematic-build/0.1.0.schema.json`](../../schemas/circuit-schematic-build/0.1.0.schema.json).

The verification matrix reports `intent_topology`, `artifact_digest`, `provenance_binding`, and
`deterministic_replay` as `passed`. It reports `kicad_cli_parse`, `erc`,
`schematic_board_parity`, and `electrical_validation` as `not_run`, with `board_ready` set to
`false`. A repository fixture's separate KiCad integration evidence does not upgrade the result of
an individual build.

The capability token has at least 256 bits of randomness; the artifact digest identifies content
but is not authorization. Access expires absolutely 15 minutes after insertion and a read does not
renew it. The thread-safe process-local store permits at most 16 entries, 16 MiB in total, and
1 MB (1,000,000 bytes) per artifact, evicts deterministically by least-recently-used order, rechecks the digest on every
read, and gives the same unavailable result for malformed, expired, evicted, and unknown tokens.
Expired entries are reclaimed lazily on a later store read/insertion or process exit. This access
TTL is not a secure-erasure guarantee for stale allocator memory, crash/swap artifacts, or byte
copies already returned to a host. The store has no listing API, persistence, filesystem write, or
content/token logging. Reading the resource reveals the accepted logical topology, so the MCP host
decides whether the bytes may enter model context.

The tool changes ephemeral server state and is therefore non-read-only and non-idempotent, although
it is non-destructive and performs no network access. Both the tool and dynamic resource are
disabled for streamable HTTP until authenticated principals, session isolation, authorization, and
per-principal quotas exist. Rendering over MCP never writes into the configured workspace.

Candidate persistence, durable routing jobs, route/evidence resource exposure, export, and apply
remain deferred to the planned routing-service contract.

## Planned tools

`analyze_routability`, `start_routing`, `get_routing_job`, `cancel_routing_job`,
`validate_route_candidate`, `explain_routing_failure`, `export_candidate`, and finally a separately
authorized `apply_candidate`.

Routing jobs will always have ordinary start/get/cancel tools. MCP Tasks may map onto the same job
records when both peers advertise support; Tasks will not become the only compatibility path.

## AI boundary

The host agent may interpret design intent and call tools. Optional learned policies may rank nets,
suggest corridors, choose repair neighborhoods, or score candidates. Neither may provide geometry
that bypasses the deterministic router and validators. Model output is untrusted input.

The planned Circuit Scene IR will add bounded semantic and visual observation plus typed placement
intent. Models may request immutable placement previews/candidates; deterministic services own
snapping, connectivity, clearance, provenance, and validation, and any eventual apply remains a
separate explicit capability. Direct model-authored KiCad mutation is never an MCP shortcut. This is
a high-fidelity north star; no Circuit Scene IR or placement tool is implemented today.

## Compatibility

Public tools and schemas are versioned independently of implementation backends. Before `1.0.0`, a
minor release may change experimental contracts, but the changelog and migration notes must explain
the impact. Stable tools should be additive whenever possible.
