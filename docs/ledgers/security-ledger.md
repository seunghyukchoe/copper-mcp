# Security Review Ledger

| Review ID | Date | Scope | Evidence | Findings / disposition | Reviewer |
|---|---|---|---|---|---|
| SEC-001 | 2026-08-03 | Initial filesystem, MCP, secrets, and AI trust boundaries | Path and symlink escape tests; bounded-read tests; secret scan; threat model | Read-only MVP accepted. Remote exposure and mutation remain blocked pending dedicated reviews. | Initial maintainer |
| SEC-002 | 2026-08-03 | Python dependency audit | `pip-audit` reported `PYSEC-2026-1845` in pytest 8.4.2; the post-upgrade audit found no known vulnerabilities | Raised the development dependency floor to pytest 9.0.3; finding remediated and verified. | Initial maintainer |

Security reviews are required before adding remote authentication, provider integrations, uploaded
files, persistent multi-user jobs, or candidate application.
