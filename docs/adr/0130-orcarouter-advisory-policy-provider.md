# ADR-0130: Use OrcaRouter only as a redacted advisory policy provider

- Status: Proposed
- Date: 2026-09-04
- Owners: @seunghyukchoe
- Related: [OrcaRouter integration guide](../integrations/orcarouter.md),
  [AI routing-policy boundary](../research/ai-routing-policy-boundary.md),
  [OrcaRouter introduction](https://docs.orcarouter.ai/introduction),
  [OrcaRouter tool calling](https://docs.orcarouter.ai/advanced/tool-calling),
  [D-247](../ledgers/decision-ledger.md), [R-193](../ledgers/risk-register.md),
  [SEC-175](../ledgers/security-ledger.md), [SEC-176](../ledgers/security-ledger.md)

## Context

An external ecosystem requested an OrcaRouter integration. CopperMCP already has a closed
`RoutingPolicy` contract that can order known nets and select coordinator-owned windows, but the
negotiated-routing dispatcher intentionally admits only fixed local deterministic profiles. A
remote model must not become a caller-selected route authority, receive proprietary board data, or
turn a policy response into geometry.

OrcaRouter documents an OpenAI-compatible endpoint at `https://api.orcarouter.ai/v1`, provider/
model IDs, `orcarouter/auto`, and OpenAI-style tool calling. Its structured-output feature is not
uniform across providers, so a cross-provider adapter needs a required function tool and a local
closed decoder rather than a provider-specific response-format assumption.

## Decision

Add `src/copper_mcp/routing/orcarouter_policy.py` as a direct-import-only implementation of
`RoutingPolicy`.

1. The adapter makes one bounded, non-streaming HTTPS Chat Completions request to the fixed
   `api.orcarouter.ai/v1` host. It sends an explicit `ORCA_KEY` credential only in the
   `Authorization` header, defaults to `orcarouter/auto`, caps completion tokens and payloads,
   rejects redirects, and performs no automatic retries.
2. The request contains only per-request aliases for nets and coordinator-supplied candidates,
   plus bounded integer priority/congestion/demand/detour/conflict features. It omits the board
   revision digest, raw identities, bounds, coordinates, pads, copper, route geometry, Board IR,
   caller-supplied or proprietary prompt content, apply tokens, and filesystem content; the only
   instruction text is a fixed protocol message owned by this adapter.
3. OrcaRouter must return exactly one `select_routing_policy` function tool call. The decoder
   rejects duplicate keys, non-finite numbers, oversized or nested JSON, unknown/duplicate/missing
   aliases, extra decision fields, repeated/missing nets, multiple choices, and multiple tool
   calls. It maps accepted aliases back to the original immutable local options and binds the
   returned decision to CopperMCP's locally computed input digest.
4. The adapter is not exported from `copper_mcp.routing`, not registered in the closed
   `negotiate_routes` policy-profile registry, and not exposed through MCP or CLI. Callers must
   pass its result through `evaluate_policy`; deterministic routing, candidate validation,
   authoritative KiCad DRC, and explicit apply authorization remain downstream authorities.
5. Transport and provider failures become the fixed, non-echoing `orcarouter_policy_rejected`
   refusal. The module never logs or persists provider payloads. A live smoke test is explicitly
   outside the offline test suite and requires an operator-approved key, quota, endpoint, and
   data-handling decision.

## Consequences

The integration is small, dependency-free, and compatible with OrcaRouter's documented
OpenAI-style tool calling while keeping raw board content out of the request. The alias mapping
means the provider cannot name an unseen net or fabricate a window, and the local decision keeps
the existing revision/deterministic-core gates.

This is not a hard-preemptible isolated worker: the direct-import call uses a bounded standard-
library network timeout, so callers must not place it on a latency-sensitive server path without
their own process boundary. Provider availability, quota, metadata retention, provider-side
forwarding, and policy quality remain external or unverified. No routing-quality, DRC, electrical,
manufacturing, or apply claim follows from an accepted policy decision.

## Alternatives considered

- Register OrcaRouter as a `negotiate_routes` profile. Rejected because that would widen the
  closed profile-admission boundary and silently give a remote evaluator access to a production
  coordinator without an isolated worker contract.
- Send `RoutingPolicyInput.as_json()` directly. Rejected because it exposes raw identities,
  revision text, and coordinator window bounds to an external provider.
- Ask for arbitrary JSON or use provider-specific `response_format`. Rejected because the
  documented cross-provider guarantee is the tool-calling surface, while structured-output
  support differs by provider.
- Allow a caller-supplied endpoint, retry policy, prompt, or model-generated geometry. Rejected
  as unnecessary SSRF, quota, prompt-injection, authority, and validation surface.
