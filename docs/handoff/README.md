# Handoff documents

Two documents hand this project to whoever continues it. They answer different questions, and
neither replaces the other.

| Document | Question it answers | Read it when |
|---|---|---|
| [Project state](project-state.md) | *Where does the project stand, and what must never be broken?* | You are new to CopperMCP, or returning after time away, and need the current engineering state, the invariants, the known limitations, and the codebase map. |
| [Codex onboarding](codex-onboarding.md) | *I am the continuing agent — what do I do first?* | You are an agent picking up in-flight work and need the task-first path: the working branch, how a change ships here, and the next slices in order. |

Read [project state](project-state.md) first for context, then [codex onboarding](codex-onboarding.md)
for the immediate task queue. Both defer to [`AGENTS.md`](../../AGENTS.md) for the repository
contract that binds every change, and to the [ADRs](../adr/README.md) for any decision that touches
a public contract.

Neither document is release authorization. A release is authorized only by a `Ready` row in the
[release ledger](../ledgers/release-ledger.md) naming a validated source commit.

Both documents record a state that ages. Where a handoff and the repository disagree, the repository
is right — verify branch heads, gate counts, and PR state from Git and GitHub rather than from a
handoff paragraph.
