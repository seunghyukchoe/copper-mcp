# OrcaRouter advisory policy integration

CopperMCP includes an experimental, opt-in adapter at
`copper_mcp.routing.orcarouter_policy`. It lets OrcaRouter suggest the order of known nets and
select coordinator-owned corridor or repair-window options. It does not create route geometry,
validate copper, run KiCad, apply a candidate, or expose a new MCP tool. It is not production-ready
and is not a correctness, DRC, electrical, manufacturing, or provider-availability guarantee.

The [merge-readiness checklist](orcarouter-merge-readiness.md) records the gates that must pass
before this optional integration can merge. The checklist deliberately leaves the live-provider
and commercial gates explicit rather than treating offline tests as evidence for them.

## Local usage

The adapter reads `ORCA_KEY` only when `OrcaRouterPolicy.from_env()` is called. The key must use
OrcaRouter's `sk-orca-` format. `ORCAROUTER_MODEL` is optional and defaults to
`orcarouter/auto`; set an explicit provider/model ID when repeatability and cost predictability
matter.

```python
from copper_mcp.routing.orcarouter_policy import OrcaRouterPolicy
from copper_mcp.routing.policy import evaluate_policy

policy = OrcaRouterPolicy.from_env()
decision = evaluate_policy(policy, policy_input)
```

`policy_input` must already be the immutable `RoutingPolicyInput` supplied by CopperMCP's
coordinator. The result must remain an advisory input to the deterministic routing core. Do not
pass the adapter through `negotiate_routes(policy_profile=...)`: that registry is intentionally
closed to local deterministic profiles.

The integration is off by default: no standard CopperMCP path constructs this adapter. To disable
it, do not instantiate `OrcaRouterPolicy` and unset `ORCA_KEY`; setting `ORCAROUTER_MODEL` alone
has no effect.

The adapter calls the non-streaming OpenAI-compatible Chat Completions endpoint at
`https://api.orcarouter.ai/v1/chat/completions`. It uses one required function tool,
`select_routing_policy`, and accepts only one assistant tool call. OrcaRouter's documented
OpenAI-compatible surface supports tool calling across chat-capable providers; this integration
uses tool calling rather than depending on `response_format`, because the documented structured
output support is not uniform across providers.

## Data boundary

Before the request leaves the process, CopperMCP replaces every net, corridor, and repair-window
identity with a per-request alias (`n0`, `c0`, `r0`). Only bounded integer scalar features are
sent, alongside one fixed protocol instruction. Board revision digests, raw net names, bounds,
coordinates, pads, copper, Board IR, route geometry, caller-supplied or proprietary prompt
content, apply tokens, and filesystem data are not sent. The response contains aliases only;
CopperMCP maps them back to the exact coordinator-owned immutable objects and computes the local
input binding.

The request and response are bounded, duplicate-key rejecting, non-finite-number rejecting, and
fail closed on an unknown tool, unknown alias, missing/repeated net, extra fields, multiple
choices, multiple tool calls, oversized data, transport errors, or redirects. There are no
automatic retries. A refusal is the fixed `orcarouter_policy_rejected` error and does not include
provider response text or credentials.

## Data handling and commercial disclosure

OrcaRouter's published data-handling documentation says that prompt and model-output content is
not persisted, while request metadata such as model, token counts, latency, status, and source IP
is retained. It also states that request content is forwarded to the selected upstream provider
under that provider's terms. CopperMCP therefore keeps this adapter's outbound payload to aliases
and bounded scalar features; users must not treat the adapter as suitable for proprietary board
content without their own data-handling approval.

The maintainer may receive compensation from eligible OrcaRouter usage under a separate commercial
arrangement. The percentage, eligible plans/models, attribution, payout, and public-disclosure
terms are not asserted by this repository until the provider agreement authorizes those statements.
Users may use CopperMCP without OrcaRouter and should choose a provider based on technical and
privacy requirements, not on this disclosure.

## OrcaRouter MCP connectivity

CopperMCP already supports the `streamable-http` MCP transport, while its default host remains
loopback. OrcaRouter's Firewall MCP feature accepts a remote Streamable HTTP MCP endpoint, so an
operator who wants inbound aggregation must place CopperMCP behind their own authenticated TLS
endpoint and explicitly configure the deployment. This branch does not change the loopback
default, publish an endpoint, configure credentials, or grant OrcaRouter board-write authority.

## Source documentation

- [OrcaRouter introduction](https://docs.orcarouter.ai/introduction)
- [OpenAI-compatible quickstart](https://docs.orcarouter.ai/getting-started/quickstart)
- [OpenAI SDK compatibility](https://docs.orcarouter.ai/compatibility/openai-sdk)
- [Tool calling](https://docs.orcarouter.ai/advanced/tool-calling)
- [Structured outputs](https://docs.orcarouter.ai/advanced/structured-outputs)
- [Errors](https://docs.orcarouter.ai/operations/errors)
- [Data handling](https://docs.orcarouter.ai/operations/data-handling)
- [Firewall MCP](https://docs.orcarouter.ai/features/firewall-mcp)

No live API call is made by the test suite. A real key, provider quota, external endpoint, and
operator-approved data-handling decision are required for a live smoke test.
