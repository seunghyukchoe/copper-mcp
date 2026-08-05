# Circuit Scene to route action closure

Evidence behind [ADR-0028](../adr/0028-revision-bound-scene-route-references.md), gathered and
reproduced 2026-08-04. External references are official specifications or maintainer sources; no
external implementation code is copied.

## Question

Can an MCP client use only the structured values returned by `observe_board_scene` to request the
same deterministic route it would receive if it knew the private KiCad net name?

Before this change, no. Circuit Scene returned `net:name:<digest>` identities while
`preview_route` accepted only a raw name and derived that identity internally. Feeding the scene
identity into the raw-name field derived a new identity from the identity string.

## Reproducible experiment

`scripts/benchmark_scene_action_closure.py` exercises `mcp.call_tool` rather than calling the route
service directly. It loads the committed, catalogue-validated `rc-low-pass-routing-v1` fixture,
observes a complete bounded region, and evaluates every one of its three observed net references
in three modes:

| Mode | Selector | Oracle |
| --- | --- | --- |
| Legacy counterfactual | Scene `net_id` placed in the old `net` field | Must reproduce the pre-fix failure |
| Reference contract | Scene `net_id` plus both scene revisions | Must route and remain deterministic |
| Hidden-name oracle | Reviewed fixture's source net name | Candidate must exactly equal reference mode |

The two-repetition regression oracle measures 0/3 legacy-actionable nets, 3/3
reference-actionable nets, 3/3 hidden-name-actionable nets, and identical canonical candidate JSON
for all three reference/name pairs. All three deliberately stale board references and all three
deliberately stale snapshot references are refused with `stale_revision`; both scene replays and
all eighteen route replays are deterministic; and the private benchmark has an identical final
file tree. The committed B-007 artifact repeats the run ten times after one warmup and records
environment and dependency versions, source, fixture, script and output-schema digests, schema
evidence, latencies, payload sizes, and explicit non-claims.

The result establishes referential closure for one small licensed audio fixture and this supported
router subset. It is not evidence of whole-board completion, multilayer search, KiCad DRC,
placement, live KiCad IPC, electrical performance, or fabrication readiness.

## MCP contract evidence

The Model Context Protocol tool specification says a tool can declare `outputSchema` and, when it
does, its structured result must conform. The official Python SDK documents that typed return
models generate a structured schema and validate the result. CopperMCP therefore advertises:

- a closed top-level `{request}` wrapper;
- a closed two-variant request union, so `net` and `net_ref_id` cannot coexist;
- mandatory board and snapshot preconditions on the reference variant; and
- a closed five-way status union that excludes contradictory outcomes, with every record closed
  down through candidate, patch, path, cost, metrics, DRC, fill and diagnostics.

The SDK-facing input annotation uses the exact JSON Schema for discovery but defers malformed
runtime values to CopperMCP's fixed, non-echoing request boundary. Successful output still passes
the generated Pydantic contract before the SDK publishes `structuredContent`.

## Why two revisions

`board_revision` is the SHA-256 digest of the exact source bytes observed. `snapshot_digest` is the
digest of the canonical Board IR produced from those bytes and the caller's supplied constraints.
Checking both prevents a reference from silently crossing either a file edit or a change in the
converted routing context. A file mismatch is refused before Board IR conversion and reports the
current board revision with a null snapshot digest; a converted-context mismatch reports both
current values. In either case the caller must re-observe explicitly rather than silently retrying
against unseen state.

## Primary sources

- Model Context Protocol, [Tools specification, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), especially output schemas and structured results.
- Model Context Protocol, [schema reference, 2026-07-28](https://modelcontextprotocol.io/specification/2026-07-28/schema), for the protocol-level `Tool` and result structures.
- Model Context Protocol Python SDK, [server tool structured output](https://github.com/modelcontextprotocol/python-sdk), for typed Pydantic return models and generated schemas.
- KiCad, [IPC API for add-on developers](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/), relevant to the next live-editor observation milestone but not invoked by this benchmark.
