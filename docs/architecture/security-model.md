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
| Router → KiCad | Stale state, partial writes, unsafe copper | Revision recheck, exact DRC, immutable patch, single undoable commit |
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
afterward. These controls do not make arbitrary remote exposure safe.

## Security acceptance for future mutation

An `apply_candidate` implementation is blocked until it has authorization tests, revision-race
tests, one-commit undo behavior, complete audit metadata, KiCad DRC verification, cancellation tests,
and a security-ledger review.
