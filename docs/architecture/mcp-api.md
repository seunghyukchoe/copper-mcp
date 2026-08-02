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
| `validate_candidate` | None | Validate and normalize candidate metadata. |
| `compare_candidates` | None | Correctness-first deterministic ranking. |

The implemented resource `pcb://server/manifest` exposes stable server metadata.

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
