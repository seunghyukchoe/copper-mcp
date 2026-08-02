# Repository Instructions

## Product boundary

CopperMCP is a local-first PCB automation platform. The deterministic core owns geometry,
connectivity, validation, and future file mutation. AI and MCP layers may propose policy and invoke
bounded operations, but must never bypass validation or apply model-generated copper directly.

## Required workflow

1. Read the closest architecture document or ADR before changing a public contract.
2. Preserve candidate immutability and board revision checks.
3. Treat board files, MCP arguments, model output, and external tool output as untrusted.
4. Update `CHANGELOG.md` for user-visible changes.
5. Update the relevant append-only ledger under `docs/ledgers/` for decisions, risks, benchmarks,
   security reviews, or releases.
6. Add or update tests before claiming a behavior works.

## Commands

- `make test`: dependency-light unit tests.
- `make lint`: Ruff plus version and ledger checks.
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
- `apply_candidate` must remain a separate, explicitly authorized operation when implemented.
- Never log board contents, credentials, prompts containing proprietary designs, or bearer tokens.

## Versioning and releases

The project follows Semantic Versioning and Keep a Changelog. The version in `pyproject.toml` is the
single source of truth. Release tags use `vMAJOR.MINOR.PATCH`. Do not publish from a dirty tree or
without a green `make check` and a completed release-ledger entry.
