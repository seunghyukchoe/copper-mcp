# MCP excessive-agency evaluation

## Decision

CopperMCP evaluates its placement, routing, inspection, and separately authorized apply
boundaries with the offline `mcp-agency/v1` harness. It treats board text and all model-facing
arguments as untrusted data; the harness measures containment/refusal at the real MCP adapter,
including its public route and placement apply handlers, instead of asking a model to self-report
that it followed instructions.

The evaluation is deliberately a regression test, not a claim that a local stdio server is
remotely deployable or that a board is electrically, mechanically, or fabrication-safe. It invokes
no model, network client, KiCad process, or board mutation and stores no board text, geometry,
URL, token, or exception payload in its report.

## External basis

- OWASP's [LLM06:2025 Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/2_0_vulns/LLM06_ExcessiveAgency.html)
  identifies excessive functionality, permissions, and autonomy as distinct causes, and recommends
  narrow extensions, complete mediation, and approval for high-impact actions. CopperMCP maps this
  to closed request schemas, no generic file/URL tool, server-enforced capability checks, and a
  separately operator-gated apply path.
- The official [MCP Security Best Practices](https://modelcontextprotocol.io/docs/2026-07-28/tutorials/security/security_best_practices)
  describes state-handle hijacking and requires authorization to be checked on inbound requests;
  this motivates testing stale revisions and non-authorizing identifiers/capabilities at the server
  boundary rather than trusting an agent's chain of tool calls.
- The official [MCP Authorization specification](https://github.com/modelcontextprotocol/modelcontextprotocol/blob/main/docs/specification/2025-03-26/basic/authorization.mdx)
  states that HTTP authorization is optional but, when implemented, must use OAuth 2.1 protections;
  CopperMCP remains local-first and does not represent this offline harness as HTTP authorization
  coverage.

## Predeclared threat matrix

| Threat | Boundary exercised | Required result |
|---|---|---|
| Board-author prompt injection | `observe_board_scene` annotations | Contained in `annotations` with `trust: untrusted_board_author`; absent from structural fields. |
| Model-supplied path, URL, token, or policy geometry | `preview_route` and `preview_placement` schemas | Refused without echoing the canary. |
| Apply without a capability | public `apply_candidate` handler with apply enabled | A syntactically valid token from a foreign authority is refused as structured `invalid_token` before source access or mutation. |
| Stale board state | `preview_route` revision CAS | `stale_revision` before Board IR conversion. |
| Excess annotation volume | `observe_board_scene` quota | Contained by an explicit annotation ceiling and omission count. |
| Output/report disclosure | default scene output and report serialization | Board-author canary absent unless annotations were explicitly requested; absent from report. |
| Tool chaining | public `apply_placement_candidate` handler with apply enabled | A route-domain token bound to the placement candidate, board revision, and path is refused as structured `invalid_token` before source access or mutation. |

The harness records attempted, blocked, refused, contained, and leaked counts. `blocked` means
every predeclared scenario reached its expected safe disposition; it is not evidence that unknown
attacks are impossible. A `leaked` count above zero or a changed temporary workspace fails the
evaluation. The same-run workspace comparison includes relative path, file type/symlink status,
mode, size, content digest, nanosecond mtime, and inode. The report serializes only stable
`unchanged` assertions, never those dynamic metadata values; its deterministic run identifier
therefore covers only redacted case metadata and evidence provenance.

## Limits and next work

This test validates CopperMCP's current local boundaries only. Remote streamable-HTTP deployment
still needs principal-bound OAuth, scope minimization, rate limits, and a separate adversarial test
environment. It also cannot prove a host will not present quarantined annotation text to a model;
the server's responsibility is to keep it typed, labelled, and unable to create apply authority.

This evaluates output and generated-report disclosure only. The current application source has no
application logger sink, so logging sinks, host-process logs, and model-provider telemetry are not
evaluated or covered by this result.
