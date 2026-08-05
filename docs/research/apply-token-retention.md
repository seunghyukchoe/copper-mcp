# Apply-token retention and expiry

## Scope

This note records the narrow retention rule for CopperMCP's process-local, HMAC-protected apply
capabilities. It does not expand which operations can be authorized, the bindings a token covers,
or the separate authorization required to apply a candidate.

## Project contract

[ADR-0059](../adr/0059-separately-authorized-placement-apply.md) requires each consumed nonce to
remain replay-protected until that token's individual expiry. It explicitly forbids count-based
eviction of live nonce state because restoring a pre-apply copy can recreate the exact board
revision a live capability authorizes. `max_consumed` is therefore only a validated compatibility
hint; admission control must happen before capability issuance when a deployment needs a hard
memory limit.

## Expiry source and implementation consequence

[RFC 7519, section 4.1.4](https://www.rfc-editor.org/rfc/rfc7519.html#section-4.1.4) specifies
that a token carrying an expiration time must not be accepted on or after that time. CopperMCP's
binary HMAC token is not a JWT, but applies the same expiry boundary: `expires_at` is fixed when
the capability is issued and authenticated as part of its binding. Consequently, sweep examines
every consumed nonce rather than assuming consumption insertion order matches issue-time expiry.
This reclaims each expired record while retaining every live nonce for replay refusal.
