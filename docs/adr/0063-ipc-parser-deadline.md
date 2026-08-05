# ADR-0063: Carry live IPC deadlines into bounded board parsing

- **Status:** Accepted
- **Date:** 2026-08-05

## Decision

Allow the bounded S-expression parser to receive an optional cooperative deadline callback. The
live KiCad IPC counting path passes its existing operation callback into that parser, which checks
before UTF-8 decoding, after decoding, and at bounded 4 KiB scan checkpoints (including long atom
and quoted-string scans). A callback exception remains typed and is not converted into a generic
payload error.

The existing byte/token/node/depth/child limits remain the hard resource ceilings. CPython's UTF-8
decode is atomic, so a single decode call cannot be forcibly pre-empted; process isolation remains
the future hard-pre-emption gate.

## Consequences

- An expired live-board operation refuses before expensive S-expression materialization whenever
  the callback can observe the expiry.
- Existing parser callers are unchanged because the callback is optional.
- The boundary remains read-only and no board text or raw parser diagnostics cross MCP.
- B-051 measures the early refusal path; it does not claim hard wall-clock pre-emption.
