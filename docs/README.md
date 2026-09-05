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
| Drive CopperMCP as an AI agent | [Agent contract](agents.md) |
| Understand the system boundaries | [Architecture overview](architecture/overview.md) |
| Contribute a change | [`CONTRIBUTING.md`](../CONTRIBUTING.md), then [Development guide](development.md) |
| Pick up the project as a continuing maintainer or agent | [Project state](handoff/project-state.md) |

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
- [OrcaRouter advisory policy integration](integrations/orcarouter.md) — the experimental,
  direct-import-only provider boundary and its redacted usage contract.
- [OrcaRouter merge readiness](integrations/orcarouter-merge-readiness.md) — local and external
  gates for merging the optional integration without presenting it as production-ready.

## Decisions and evidence

- [Architecture Decision Records](adr/README.md) — durable decisions and their tradeoffs, immutable
  after acceptance except for status and superseding links.
- [Project ledgers](ledgers/README.md) — append-only decision, risk, security, benchmark, and
  release records, including the ID-allocation convention.
- [Research surveys](research/README.md) — the dated literature and licensing passes that ground
  each engineering arc.
- [Post-0.8.0 audit and improvement plan](audit/2026-08-14-post-0.8.0-audit.md) — a dated audit at
  the `v0.8.0` authorization commit: the correctness ranking (none of the four open issues is in the
  forbidden class), which published claims still rest on unauditable or irreproducible measurements,
  the four schema-drift instances behind
  [#172](https://github.com/seunghyukchoe/copper-mcp/issues/172), and the ordered improvement plan
  whose every item names the check that proves it done. Nine of its items carry **no estimate**, on
  the rule that converting one without running its measure-first sub-item is the finding rather than
  the fix. A snapshot, not a living document.
- [Committed mutation specs](mutants/README.md) — the anchors, replacements and mutant→killing-test
  mappings behind every mutation claim made after
  [ADR-0098](adr/0098-reproducible-mutation-evidence.md). A kill count with no spec here is prose,
  not evidence, and no count bounds the mutation space — only the mutants that were chosen.
- [Excessive-agency evaluation v1](research/excessive-agency-eval-v1.md) — the suite that attacks
  CopperMCP's central safety claim, its per-project-family results including every scenario that did
  **not** run, and the list of things a passing run does not prove. Since
  [ADR-0102](adr/0102-an-evaluation-must-observe-a-permit.md) not every row is an attack: one
  predeclared outcome is an `authorized_write` the server is supposed to permit, because a suite
  whose every row requires a refusal cannot tell correct refusal from blanket refusal.

## Operations

- [Development guide](development.md) — environment, the quality gate, and local workflow.
- [Usage guide](usage.md) — every CLI command and MCP tool, with the limits each one declares.
- [Agent contract](agents.md) — the agent-facing usage contract: tool-by-tool digest bindings, every
  refusal code stated as the action to take next, digest discipline, end-to-end workflows, and the
  one-value literals. `tests/test_agents_doc.py` fails when it drifts from the source. The root
  [`llms.txt`](../llms.txt) points here first.
- [Release process](releasing.md) — versioning, tagging, attestation, and ledger authorization.
- [GitHub repository setup checklist](github-setup.md) — the protections that live in GitHub
  settings rather than in this repository.
- [Project state](handoff/project-state.md) — the living record of current engineering state: the
  released version, the contract versions in force, the tool surface, the invariants, how a change
  ships, and the known limitations. Updated every release. Its neighbour
  [`handoff/codex-onboarding.md`](handoff/codex-onboarding.md) is a **dated 2026-08-05 record, kept
  as history and superseded** — see [the handoff index](handoff/README.md).

## Reference

- [Board IR 0.1 → 0.2 migration](migrations/board-ir-0.2.md) — why 0.1 snapshots are re-converted
  from the source board rather than auto-migrated.
- [CopperMCP 0.6.0 migration](migrations/copper-mcp-0.6.0.md) — the default-off live IPC opt-in and
  the new `unsupported.document` diagnostic code.
- [CopperMCP 0.7.0 migration](migrations/copper-mcp-0.7.0.md) — the `ROUTER_VERSION` bump that
  invalidates every stored candidate identity, two families of discriminated diagnostic codes, the
  raised parser and router defaults, and what does *not* require migration.
- [CopperMCP 0.8.0 migration](migrations/copper-mcp-0.8.0.md) — the fill-vertex budget raised as a
  denial-of-service posture decision with its price measured, three refusal messages that disappear
  from the source entirely, nineteen pad fields that stop matching the generic sentence, courtyard
  overlap now paired by drawn layer, the new `FILL_EVIDENCE_MISMATCH` code, and a published schema
  widened in place under an unchanged version.
- [CopperMCP 0.9.0 migration](migrations/copper-mcp-0.9.0.md) — `BOARD_IR_SCHEMA_VERSION` moves to
  `0.3.0` and `0.2.0` is frozen where it stands: no content address moves, a persisted `0.2.0`
  envelope stops decoding, and `0.2.0` as published spans three different accepted sets across
  `v0.5.0`–`v0.8.0`.
- [CopperMCP 0.10.0 migration](migrations/copper-mcp-0.10.0.md) — no schema version moves and no
  snapshot is re-converted; what moves is the new read-only `verify_external_route_candidate` tool,
  four preview response versions carrying a closed eight-value withheld-apply-token vocabulary, and
  the verified-fill island ceiling widening from 4,096 to a measured 500,000 vertices.
- [CopperMCP 0.11.0 migration](migrations/copper-mcp-0.11.0.md) — no schema version moves and no
  snapshot is re-converted; what moves is the `mcp` range widening to `<2.2.0` so a deployment may
  land on 2.1 where refusals now keep their reason, eleven more `setup` and `footprint` heads
  accepted as typed non-claims so previously refused boards convert, and an `unmodelled_counts`
  map that grows from six entries to nine with one existing count widened in meaning.
- [CopperMCP 0.12.0 migration](migrations/copper-mcp-0.12.0.md) — no schema version moves and no
  snapshot is re-converted; what moves is Board IR accepting `Edge.Cuts` outline arcs and stray
  footprint copper polygons so previously refused boards convert, an `unmodelled_counts` map that
  grows from nine entries to eleven with one member that is a distance and one that discloses an
  approximation, and `preview_placement` refusing a board whose outline is approximated.
- [Public media assets](assets/README.md) — project media, with provenance; not routing or
  benchmark evidence.

## Conventions

- **ADRs** record a decision once. They are not updated as the code changes; a later ADR supersedes
  an earlier one and the earlier one's status is amended to say so.
- **Ledgers** are append-only. A correction is a dated note or a new entry with a new ID that names
  what it corrects — never a row rewrite. See [ledgers/README.md](ledgers/README.md) for how IDs
  are allocated.
- **Research documents** are dated snapshots of external evidence, not maintained summaries. Each
  one records what was true when it was gathered.
- **`docs/handoff/project-state.md` is the one living document in this tree.** It asserts present
  tense on purpose and is updated in every release pull request. Everything else here is either a
  contract (ADRs, architecture, schemas), an append-only record (ledgers), or a dated snapshot
  (research, migrations, the superseded 2026-08-05 onboarding handoff). Even the living document
  ages between releases: where it and the repository disagree, the repository is right.
- Every relative link in this tree is checked by `scripts/check_doc_links.py`, which runs in
  `make lint`. It answers two questions: does the target resolve, and — when a link's label names a
  record (`ADR-NNNN`, `D-NNN`, `R-NNN`, `SEC-NNN`, `B-NNN`) and its target is a path that
  identifies a record — do the two name the same record. A label that names no record is not
  judged. Ledger IDs and ADR numbers are checked there too, by `scripts/check_ledgers.py` and
  `scripts/check_adr_numbers.py`: one entry per number, in order, with gaps reported but allowed.

## Supervised optimization release plan

The [v0.13 staged plan](plans/v0.13-supervised-optimization.md) distinguishes the
internal foundation from the unimplemented worker, backend integration and release gates.
