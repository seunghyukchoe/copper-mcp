# KiCad session-revision HMAC boundary

**Snapshot date:** 2026-08-05

> **Superseded implementation record — 2026-08-05:** D-129/SEC-105/R-104 replace this HMAC wire
> derivation with `pbkdf2-hmac-sha256` to satisfy the current CodeQL limited-input-secret rule.
> This document is retained unchanged below as historical HMAC remediation evidence; see
> [the PBKDF2 boundary](./kicad-session-revision-pbkdf2.md) for the current contract.

## Question

How can a read-only live KiCad route proposal distinguish a restarted editor session with identical
board bytes without publishing an offline-testable fingerprint of KiCad's API credential?

## Official evidence

1. KiCad gives a launched IPC plugin `KICAD_API_TOKEN`; it is unique to the running KiCad instance
   and can detect a mid-session KiCad restart. The token is a request credential, not a value for
   publication. [KiCad connection guidance](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/for-addon-developers/#connecting-to-kicad).
2. Python documents HMAC as keyed hashing. Its `compare_digest()` is specifically recommended for
   externally supplied digest verification to avoid content-dependent timing behavior.
   [Python `hmac`](https://docs.python.org/3/library/hmac.html).
3. Python documents `secrets.token_bytes()` for cryptographically strong token material and notes
   that 32 bytes (256 bits) is sufficient for typical token use cases.
   [Python `secrets`](https://docs.python.org/3/library/secrets.html).
4. GitHub CodeQL's `py/weak-sensitive-data-hashing` guidance explains that SHA-256 alone is not
   suitable for limited-input secrets because it is not deliberately expensive against brute force.
   A public SHA-256 fingerprint of the token therefore creates an unnecessary offline-guessing
   oracle even though SHA-256 remains appropriate for non-secret board-content digests.
   [CodeQL query help](https://codeql.github.com/codeql-query-help/python/py-weak-sensitive-data-hashing/).

## Implemented boundary

At process initialization, CopperMCP creates one non-persistent 256-bit secret with
`secrets.token_bytes(32)`. For each validated KiCad token it computes:

```text
hmac-sha256:<HMAC-SHA256(process_key, "copper-mcp:kicad-ipc-session-revision:v1\\0" || token)>
```

The public contract accepts only that fixed lowercase 64-hex wire type. It compares a supplied
value with the current capture using `hmac.compare_digest()` after fixed-format validation. The
same process and token generate the same value; a different token, capture-time token change, or
fresh process key generates a different value and refuses before Board IR conversion/routing.

This is a narrow same-process CAS binding, not an authentication protocol or a persistent session
identifier. It intentionally makes old requests stale after CopperMCP restart and does not change
the source/snapshot revision checks, client-closure rule, routing budgets, candidate immutability,
or prohibition on KiCad mutation.

## Regression evidence

Focused fake-IPC tests prove stable same-process results, distinct token results, key-rotation
stale refusal before conversion, malformed legacy `sha256:` refusal, constant-format MCP schema,
and no token in a successful result. `LiveBoardSnapshot` also rejects a forged legacy wire value.

## Non-claims

The HMAC tag does not prove caller identity, authenticate a remote client, prevent compromised
same-process code from reading process memory, make synchronous KiCad IPC pre-emptible, prevent
KiCad's documented board-revision ABA possibility, or authorize editor mutation, DRC, electrical,
or fabrication actions.
