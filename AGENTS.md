# Repository Instructions

## Product boundary

CopperMCP is a local-first PCB automation platform. The deterministic core owns geometry,
connectivity, validation, and file mutation. AI and MCP layers may propose policy and invoke
bounded operations, but must never bypass validation or apply model-generated copper directly.

## Required workflow

1. Read the closest architecture document or ADR before changing a public contract.
2. Preserve candidate immutability and board revision checks.
3. Treat board files, MCP arguments, model output, and external tool output as untrusted.
4. Update `CHANGELOG.md` for user-visible changes.
5. Update the relevant append-only ledger under `docs/ledgers/` for decisions, risks, benchmarks,
   security reviews, or releases.
6. Add or update tests before claiming a behavior works.

## Agent coordination

- Use only Codex's built-in subagent and collaboration capabilities for delegated work.
- Do not invoke or depend on the external Orca application, CLI, runtime, or orchestration skills.
- The primary agent creates and supervises every worker directly; nested delegation is not allowed.

## Commands

- `make test`: dependency-light unit tests.
- `make lint`: Ruff plus the version, ledger, ADR-number, documentation-link, schema-set,
  CI-budget, audio-benchmark, and Circuit Intent checkers.
- `make typecheck`: strict mypy.
- `make security`: repository secret scan and dependency audit.
- `make build`: build the source distribution and wheel.
- `make check`: full release-oriented validation.

Use Python 3.11 or newer. The current reference implementation is Python; performance-critical Rust
crates may be introduced only behind the stable contracts in `src/copper_mcp/routing/contracts.py`.

## Security invariants

- Never commit `.env`, credentials, private keys, customer boards, generated private candidates, or
  model-provider tokens.
- Keep network transports bound to loopback by default.
- Validate every external input at its boundary and cap file, job, model, and iteration budgets.
- `apply_candidate` and `apply_placement_candidate` are the only surfaces that write to a user's
  board file. They must remain separate, explicitly authorized operations: default-off behind
  `COPPER_MCP_ALLOW_APPLY`, each requiring its own single-use token.
- Never log board contents, credentials, prompts containing proprietary designs, or bearer tokens.

## Versioning and releases

The project follows Semantic Versioning and Keep a Changelog. The version in `pyproject.toml` is the
single source of truth. Release tags use `vMAJOR.MINOR.PATCH`. Do not publish from a dirty tree or
without a green `make check` and a completed release-ledger entry.
