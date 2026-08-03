# MCP API Contract

## Design rules

- MCP is a thin adapter over pure application services.
- Tools use structured inputs and outputs; large artifacts use resource URIs.
- Read-only capabilities ship before mutation.
- Long-running work is backed by a durable internal job model, regardless of MCP client support.
- Network and authorization concerns never enter the geometry layer.

## Implemented tools

| Tool | Side effect | Description |
|---|---|---|
| `server_info` | None | Version, maturity, and honest capability inventory. |
| `inspect_board` | None | Bounded read-only inspection inside the configured workspace. |
| `run_board_drc` | Temporary report only | Fixed-argument KiCad DRC with a bounded, redacted summary. |
| `preview_route` | None, or a temporary report when `include_drc` is set | Bounded, non-mutating two-pin route proposal on the documented Board IR subset. |
| `validate_candidate` | None | Validate and normalize candidate metadata. |
| `compare_candidates` | None | Correctness-first deterministic ranking. |

The implemented resource `pcb://server/manifest` exposes stable server metadata.

`run_board_drc` returns the board SHA-256 revision, a DRC-context revision covering the board,
matching project/custom-rule files, and workspace-local KiCad library assets; KiCad/schema versions;
severity and connectivity counts; violation-type counts; and a hard-correctness pass flag. It
also reports how many DRC check classes KiCad marks ignored, while deliberately omitting their raw
descriptions along with net names, UUIDs, and coordinates. Exit codes `0` (clean) and `5`
(findings reported) are valid only when they agree with the strict report's finding collections;
other exit codes or process/report disagreement fail the tool. Warning-only and exclusion-only
findings can retain a hard-correctness pass flag while still requiring exit code `5`. The snapshot
and report have independent size ceilings, and report growth is limited before KiCad starts.

`preview_route` takes one request object with a workspace-relative `board`, a KiCad `net` name, a
copper `layer` name, integer `constraints` for the applied net class, and optional `seed`,
`settings`, and `include_drc` fields. Unknown fields, non-integer or out-of-range budgets, booleans
supplied as integers, control characters, and unsupported layer names are rejected before any file
is read. Every response carries a `status` of `routed`, `not_routed`, or `unsupported_board`, the
board revision, the Board IR snapshot digest when conversion succeeded, and the validated request.
A routed response includes the candidate ID, endpoint pad IDs, integer geometry, exact cost
decomposition, deterministic search metrics, and the resource ceilings that produced it. An unrouted
response carries one typed, non-echoing diagnostic; an unsupported board carries bounded conversion
diagnostic-code counts instead of raw adapter text.

Setting `include_drc` binds the proposal to candidate-bound authoritative KiCad DRC evidence, which
returns the same aggregate, redacted summary as `run_board_drc` plus the candidate, source, patched
board, and patched context revisions. The call fails rather than returning a candidate whose
requested evidence is missing or does not bind. Preview writes no file, creates no job, and never
returns source board bytes; it does return the geometry it generated, so a host that must not
disclose generated copper to a model should not enable this tool.

Candidate persistence, durable routing jobs, resource exposure, export, and apply remain deferred to
the planned routing-service contract.

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

## Compatibility

Public tools and schemas are versioned independently of implementation backends. Before `1.0.0`, a
minor release may change experimental contracts, but the changelog and migration notes must explain
the impact. Stable tools should be additive whenever possible.
