# Security Review Ledger

| Review ID | Date | Scope | Evidence | Findings / disposition | Reviewer |
|---|---|---|---|---|---|
| SEC-001 | 2026-08-03 | Initial filesystem, MCP, secrets, and AI trust boundaries | Path and symlink escape tests; bounded-read tests; secret scan; threat model | Read-only MVP accepted. Remote exposure and mutation remain blocked pending dedicated reviews. | Initial maintainer |
| SEC-002 | 2026-08-03 | Python dependency audit | `pip-audit` reported `PYSEC-2026-1845` in pytest 8.4.2; the post-upgrade audit found no known vulnerabilities | Raised the development dependency floor to pytest 9.0.3; finding remediated and verified. | Initial maintainer |
| SEC-003 | 2026-08-03 | KiCad CLI DRC boundary | Injection; process/discovery timeout; in-process report, cumulative-byte, file-count, and non-overlapping snapshot-memory bounds; malformed/incomplete/schema-skew reports; stale-context; side-effect containment; redaction; and real KiCad 10.0.5 integration tests | Read-only fixed-argument adapter accepted; a POSIX wrapper sets the file ceiling before KiCad starts and execution occurs on a path-preserving private snapshot so `.kicad_prl` never enters the source workspace. Unsupported platforms fail closed. Arbitrary arguments, save/refill, and raw finding disclosure remain prohibited. | Initial maintainer |
| SEC-004 | 2026-08-03 | GitHub Actions supply chain | Reviewed upstream major-version changes, pinned each action to a full commit SHA, replaced the deprecated provenance wrapper with `actions/attest`, and retained least-privilege job permissions | CI, security, release, labeler, CodeQL, and Scorecard workflows accepted pending current-main GitHub checks; future minor/patch updates are grouped while majors remain isolated for review. | Initial maintainer |

Security reviews are required before adding remote authentication, provider integrations, uploaded
files, persistent multi-user jobs, or candidate application.
