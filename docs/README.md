# CopperMCP documentation

Start here. This index says what each document owns, so you can go straight to the one that answers
your question instead of reading the tree.

The repository root holds the front door ([`README.md`](../README.md)), the agent contract
([`AGENTS.md`](../AGENTS.md)), and the community files
([`CONTRIBUTING.md`](../CONTRIBUTING.md), [`GOVERNANCE.md`](../GOVERNANCE.md),
[`SECURITY.md`](../SECURITY.md), [`SUPPORT.md`](../SUPPORT.md),
[`CODE_OF_CONDUCT.md`](../CODE_OF_CONDUCT.md)). Everything below is under `docs/`.

## If you are new

| You want to | Read |
|---|---|
| Understand what CopperMCP is and try it | [`README.md`](../README.md) at the repository root |
| Use the CLI and MCP tools in depth | [Usage guide](usage.md) |
| Understand the system boundaries | [Architecture overview](architecture/overview.md) |
| Contribute a change | [`CONTRIBUTING.md`](../CONTRIBUTING.md), then [Development guide](development.md) |
| Pick up the project as a continuing maintainer or agent | [Handoff documents](handoff/README.md) |

## Product and direction

- [Project charter](project-charter.md) — mission, scope, and the non-goals that bound it.
- [Roadmap](roadmap.md) — milestones as outcomes, each gated on tests, docs, ledgers, and evidence.

## Architecture and contracts

These describe what the deterministic core owns. Read the closest one before changing a public
contract.

- [Architecture overview](architecture/overview.md) — system boundaries and layer responsibilities.
- [Board IR and KiCad adapter contract](architecture/board-ir.md) — the canonical board snapshot
  (`copper.board-ir` `0.2.0`), its codec, schema, and the narrow read-only KiCad converter.
- [Circuit Intent IR and KiCad schematic contract](architecture/circuit-intent.md) — the
  MCP-independent logical topology contract and deterministic schematic rendering.
- [Deterministic A* routing baseline](architecture/routing-baseline.md) — the candidate-only CPU
  reference router and its serialization bridge.
- [MCP API contract](architecture/mcp-api.md) — tool surface, structured inputs and outputs, and
  resource URIs.
- [Security and threat model](architecture/security-model.md) — assets, adversaries, boundaries,
  and the invariants that keep untrusted input contained.

## Decisions and evidence

- [Architecture Decision Records](adr/README.md) — durable decisions and their tradeoffs, immutable
  after acceptance except for status and superseding links.
- [Project ledgers](ledgers/README.md) — append-only decision, risk, security, benchmark, and
  release records, including the ID-allocation convention.
- [Research surveys](research/README.md) — the dated literature and licensing passes that ground
  each engineering arc.

## Operations

- [Development guide](development.md) — environment, the quality gate, and local workflow.
- [Usage guide](usage.md) — every CLI command and MCP tool, with the limits each one declares.
- [Release process](releasing.md) — versioning, tagging, attestation, and ledger authorization.
- [GitHub repository setup checklist](github-setup.md) — the protections that live in GitHub
  settings rather than in this repository.
- [Handoff documents](handoff/README.md) — current engineering state and continuing-agent
  onboarding.

## Reference

- [Board IR 0.1 → 0.2 migration](migrations/board-ir-0.2.md) — why 0.1 snapshots are re-converted
  from the source board rather than auto-migrated.
- [Public media assets](assets/README.md) — project media, with provenance; not routing or
  benchmark evidence.

## Conventions

- **ADRs** record a decision once. They are not updated as the code changes; a later ADR supersedes
  an earlier one and the earlier one's status is amended to say so.
- **Ledgers** are append-only. Corrections are dated notes, never row rewrites. See
  [ledgers/README.md](ledgers/README.md) for how IDs are allocated.
- **Research documents** are dated snapshots of external evidence, not maintained summaries. Each
  one records what was true when it was gathered.
- **Handoff documents** record a state that ages. Where a handoff and the repository disagree, the
  repository is right.
- Every relative link in this tree is checked by `scripts/check_doc_links.py`, which runs in
  `make lint`. Ledger IDs and ADR numbers are checked there too, by `scripts/check_ledgers.py` and
  `scripts/check_adr_numbers.py`: one entry per number, in order, with gaps reported but allowed.
