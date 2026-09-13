# ADR-0157: Verified-host confirmation is challenge-bound

- Status: Proposed; isolated consent controls reviewed
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md)

## Decision

Keep the existing five optimization MCP tools, v1 schemas and digest meanings unchanged. Human
confirmation stays default-off and requires operator attestation of the host's human UI; protocol
capability and accepted client data alone do not prove a human replied. A process-local store holds
at most128 pending challenges, each expiring after120 seconds. Each binds owner, distinct disclosure
or review-approval purpose, job identity, job-record revision, package digest and judge digest.
Expiry, replay, restart, cancellation, malformed state and invalid delivery remain refusals.

Use the SDK Resolve/Elicit dependency path on both legacy2025 and current2026 connections. The
SDK carries the question through the connection's supported mechanism. Pass the resolver-observed
challenge to the handler: an answer to challengeA cannot consume or delete replacementB, even for
the same command. Consume the exact matching pending challenge atomically once.

Recheck owner, job-record revision, package/judge and validated private delivery before asking
and after acceptance, before disclosing or approving. Approval additionally retains the existing
service's current-board/snapshot, rule-context and bound-intent checks before issuing its review
capability and committing approval. Geometry disclosure concerns the exact immutable package;
it does not certify current workspace freshness. Metadata-only export needs no confirmation.
Private board bytes, credentials and issued capabilities stay on the server; client continuation
state is not approval authority, even when SDK-protected.

The reviewed P2 correction maps expected resolver failures to fixed ToolError responses rather than
UnexpectedToolError. Isolated controls passed against installed SDK 2.0.0 and 2.1.1; normalized
schemas were exactly 60,042 bytes with digest
sha256:b6f0bc2d53d688bf4922ef17ca6c88cb9f8dbcc3eddf8b1bc4e474d3fb86627f.

## Non-authority and evidence limits

Consent authorizes only its specified geometry-disclosure or review-approval purpose. It does not
apply a board, establish physical correctness, prove human understanding, expose a provider or add
a tool. Existing approval capabilities and repository CAS still govern review completion.

Sources: [MCP2026 elicitation](https://modelcontextprotocol.io/specification/2026-07-28/client/elicitation),
[SDK resolver/elicitation guidance](https://py.sdk.modelcontextprotocol.io/v2/es/handlers/elicitation/),
and the checked installed SDK2.0.0/2.1.1 behavior.

Full-repository validation, native end-to-end integration and verified-human UI interaction
remain pending; synthetic SDK wire tests establish protocol mechanics only.
