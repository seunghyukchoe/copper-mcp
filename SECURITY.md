# Security Policy

PCB files may contain proprietary product information, and MCP servers can bridge an AI agent to a
local filesystem. CopperMCP treats both as high-value security boundaries.

## Supported versions

| Version | Supported |
|---|---|
| Latest release | Yes |
| `main` | Best effort until the next release |
| Older releases | No, unless explicitly listed in a security advisory |

## Reporting a vulnerability

Use GitHub Private Vulnerability Reporting from the repository's **Security** tab. Do not open a
public issue and do not attach a real customer board, credential, private key, or access token.

Include:

- A concise impact statement.
- A minimal synthetic reproduction.
- The affected version or commit.
- Required privileges and deployment mode.
- Suggested mitigations, if known.

Maintainers aim to acknowledge a report within five business days. Timelines for fixes and
disclosure depend on severity and coordination needs. No bounty program is currently offered.

## Secret handling

- `.env`, local MCP configuration, private keys, credentials, private boards, job databases, and
  generated artifacts are ignored by default.
- Store credentials in an operating-system keychain, CI secret store, or dedicated secret manager.
- Never include secrets or proprietary board contents in prompts, logs, traces, fixtures, issues, or
  pull requests.
- If a secret reaches Git history or a remote, revoke and rotate it immediately. Removing the line
  is not sufficient.

## Deployment guidance

- Prefer the local `stdio` transport.
- Keep Streamable HTTP bound to loopback unless it is protected by TLS, authentication,
  authorization, rate limits, and an explicit workspace allowlist.
- Run the service as an unprivileged user with access only to the necessary project directory.
- Treat all MCP parameters, KiCad files, plugin responses, AI output, and candidate files as
  untrusted input.
- Keep `apply_candidate` and `apply_placement_candidate` — the only operations that write to a
  board file — separately authorized and auditable: default-off behind `COPPER_MCP_ALLOW_APPLY`,
  each requiring its own single-use token.

See the complete [security model](docs/architecture/security-model.md).
