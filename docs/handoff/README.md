# Handoff documents

One document hands this project to whoever continues it. The other records how that handoff read on
one particular day and is kept as history.

| Document | Kind | Question it answers | Read it when |
|---|---|---|---|
| [Project state](project-state.md) | **Living** — updated every release | *Where does the project stand, and what must never be broken?* | You are new to CopperMCP, or returning after time away, and need the current engineering state, the shipping rules, the invariants, the known limitations, and the codebase map. |
| [Codex onboarding](codex-onboarding.md) | **Dated 2026-08-05 — superseded** | *What did the handoff say on 2026-08-05?* | Only when you want the historical record. It stands on a branch that no longer exists and names a release three versions behind; it is not maintained. |

Read [project state](project-state.md). It defers to [`AGENTS.md`](../../AGENTS.md) for the
repository contract that binds every change, and to the [ADRs](../adr/README.md) for any decision
that touches a public contract.

Why one living document and not two: the two documents said overlapping things about the same
state, and both went stale across three releases because neither had a single owner. The durable
content that used to sit in the onboarding document — how a change ships here, and the separate
adversarial review that destructive capability requires — now lives in
[project state](project-state.md) §3.

Neither document is release authorization. A release is authorized only by a `Ready` row in the
[release ledger](../ledgers/release-ledger.md) naming a validated source commit.

The living document still records a state that ages between releases. Where it and the repository
disagree, the repository is right — verify branch heads, gate counts, issue state, and PR state
from Git and GitHub rather than from a handoff paragraph.
