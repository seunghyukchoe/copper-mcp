# Security and Threat Model

## Assets

- Proprietary PCB geometry, net names, constraints, stackups, and component choices.
- Filesystem integrity and unsaved KiCad editor state.
- MCP and model-provider credentials.
- Candidate provenance and benchmark integrity.
- Compute budgets for routing and AI inference.

## Trust boundaries

| Boundary | Representative threats | Required controls |
|---|---|---|
| MCP input → server | Path traversal, oversized payloads, excessive agency | Typed validation, workspace allowlist, size/budget limits, separate apply permission |
| Board file → parser | Malformed data, parser DoS, hidden secrets | Bounded reads, fuzz/property tests, no execution, generic errors |
| Server → KiCad CLI | Argument injection, hangs, oversized or incompatible reports, context-file floods, stale evidence | Validated executable, fixed argument vector, POSIX file ceiling, cumulative byte/file-count bounds, discovery/process timeouts, strict contract, revision recheck |
| AI output → policy | Prompt injection, invalid commands, cost exhaustion | Allowlisted typed actions, deterministic validation, token/iteration budgets |
| Router → KiCad | Candidate/source/context misbinding, stale state, unsafe copper | Exact replay, immutable multi-revision evidence, live-context recheck, private candidate snapshot, separate apply authorization |
| Remote client → HTTP | Spoofing, token theft, cross-tenant access | TLS, OAuth, scoped authorization, per-principal jobs, rate limits |
| Dependency → build | Compromise, typosquat, vulnerable package | Locking, Dependabot, CodeQL, dependency audit, build attestations |

## STRIDE summary

- **Spoofing:** remote deployment requires authenticated principals; a job ID is not authorization.
- **Tampering:** board and candidate content hashes detect stale or modified inputs.
- **Repudiation:** future mutations record principal, revision, policy, model digest, seed, and result.
- **Information disclosure:** local-first defaults, redacted logs, ignored private workspaces, and no
  automatic model upload.
- **Denial of service:** bounded file sizes, candidate counts, runtime, memory, net counts, and model
  loops.
- **Elevation of privilege:** read, route, export, cancel, and apply remain separate capabilities.

## Current controls

The `0.1.x` surface is read-only. Path resolution rejects parent and symlink escapes, board reads are
bounded, network transport binds to loopback, secret patterns are scanned, and no AI provider is
enabled. KiCad DRC runs with fixed arguments against a path-preserving private context snapshot and
emits a bounded aggregate summary; save/refill flags and raw finding details are not exposed. The
child process receives a file-size ceiling before KiCad starts, and the source context is re-hashed
afterward. For route candidates, only one captured source is parsed, the candidate must match its
Board IR base and exact deterministic replay, and only an in-memory board payload is replaced before
the same DRC path runs. Evidence binds candidate, Board IR, original source, patched board, patched
context, and nested summary revisions; copied violation counts are immutable, must sum to the
aggregate findings, and KiCad's exit code must agree with report finding presence. The private
board is part of a complete private input-context recapture after KiCad exits, and any private or live
board/rule/library change discards the result. Candidate bytes, raw findings, and this internal
operation are not exposed through MCP or CLI. These controls do not make arbitrary remote exposure
safe.

## Security acceptance for future mutation

An `apply_candidate` implementation is blocked until it has authorization tests, revision-race
tests, one-commit undo behavior, complete audit metadata, KiCad DRC verification, cancellation tests,
and a security-ledger review.
