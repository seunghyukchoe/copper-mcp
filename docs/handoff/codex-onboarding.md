# Codex handoff — CopperMCP, 2026-08-05 (superseded)

> **This is a dated record, not the current state.** It was written on 2026-08-05 to hand one
> in-flight branch to one continuing agent, and it is kept because that is what it is: a record of
> where the work stood that day. It is **not** maintained, and several statements below are now
> false — it names `v0.4.0` as the last released posture (the last published release is `v0.6.0`,
> and a `0.7.0` section is written in `CHANGELOG.md`), and it stands on the working branch
> `codex/live-route-proposal`, which no longer exists on the remote.
>
> **Superseded by [`project-state.md`](project-state.md)**, which is the living handoff: it carries
> the current versions, contract versions, tool counts, milestone state, limitations, and the
> "how every change ships here" and destructive-capability rules that used to live in this file.
> A continuing agent should read that document and `AGENTS.md`, and use this one only to see what
> was believed on 2026-08-05.
>
> Nothing below is edited. Like a research note or a ledger row, a dated record is superseded
> rather than rewritten.

You are picking up CopperMCP. This document is the fast path for the Codex agent that continues the
work. Read [`project-state.md`](project-state.md) for the full state; read `AGENTS.md` for the
repository contract you
must obey on every change. This file tells you where you are standing right now and exactly what to
do first.

## Right now

- Default branch `main` remains the project's last released posture (`v0.4.0` plus the merged
  `apply_candidate` capability). Working branch `codex/live-route-proposal` carries the stacked
  Board IR/placement observation and bounded placement-apply slices; read GitHub for exact PR
  heads and checks before integration.
- The placement-apply slice is implemented behind an explicit placement-scoped token and the
  operator gate. The supported file-level subset is front-side, orthogonal, native-identity,
  rectangular-courtyard pose replay with CAS, lock refusal, backup, and atomic replacement.
- Focused placement-apply tests, Ruff, and strict mypy pass; several existing real-KiCad tests may
  be unavailable on hosts where the managed KiCad process aborts, and must be reported rather than
  silently treated as green.

## The one rule that matters most

**AI proposes, deterministic code disposes.** Nothing a model emits reaches a board without being
recomputed, replayed, and verified by code that does not consult a model. Every capability in this
repo is built on that line. If you are ever tempted to trust a model-supplied value directly —
don't; recompute it and compare.

The corollary you will feel constantly: **every claim is bound to a test or declared a non-claim.**
When you cannot verify something, model it as a one-value literal (`not_run`, `not_modelled`,
`inconclusive`) rather than implying it. Two findings were fixed in this very branch because a
contract made a promise the code did not keep — a non-rectangular courtyard that validated, and a
padless footprint reported as "does not exist." Both were honesty bugs, and both are the kind of
thing to watch for.

## How every change ships here

1. **Design before code** for anything touching a public contract. Read the closest ADR
   ([`docs/adr/`](../adr/README.md)) first. For a new capability, write the design as a report, not
   as code.
2. **Research-ground new arcs.** Each major slice starts from a current-literature pass in
   [`docs/research/`](../research/README.md) with licences and per-item implications, cited from
   that slice's ADR. Do not
   skip this for algorithmic work — it is how the licensing landmines (GeoSteiner, FLUTE, REST are
   all encumbered; freerouting is GPL) stay out of the tree.
3. **Slice small.** Land the pure/verifiable part first (a parser, an engine, an assertion),
   then the surface. The apply arc was five slices for exactly this reason.
4. **Test before claiming.** Add the regression test, then mutation-check it: revert the fix, watch
   the test fail, restore. A test that never fails proves nothing.
5. **Full gate, both mypy generations.** `make check` locally; CI floats mypy to the newest
   version, so type-check against a scratch install of the latest mypy too, or you will get
   version-skew failures that pass locally.
6. **PR, then resolve every review thread.** Branch protection requires conversation resolution.
   The `chatgpt-codex-connector[bot]` reviews every PR; triage each finding against current code,
   fix the real ones with regression tests, reply with the commit, resolve the thread.
7. **Ledgers are append-only.** Record decisions, risks, security reviews, and releases under
   `docs/ledgers/`. Corrections are dated notes below the table, never row rewrites.
8. **Releases are a deliberate act.** A `Ready` row in the release ledger naming a validated source
   commit, then a metadata-only commit, then the tag. `scripts/check_version.py --tag` gates it.

## Destructive capability is different

`apply_candidate` and `apply_placement_candidate` are the only surfaces that write to a user's file.
They are **default-off** (`COPPER_MCP_ALLOW_APPLY`). When you extend anything that writes, deletes,
or authorizes:

- Run a **dedicated adversarial review** on the diff, separate from the normal gate. The apply PR's
  adversarial pass found eleven real defects — including a lock that was documented but did not
  exist, an auto-restore that destroyed concurrent writes, and post-write failures reported as
  "nothing changed." None were caught by the ordinary tests.
- The safety property is absolute: an operation either changes the board exactly as previewed, or
  refuses and leaves it byte-identical, or truthfully reports partial verification. It must hold
  under a concurrent KiCad save. Prove concurrency with genuine under-lock contention, never a
  wall-clock timeout (a flaky safety test is worse than none — it will be ignored).

## What to build next, in order

1. **Close placement apply's remaining gates.** Add fidelity for author/fabrication/library/
   property/3D-model nodes, post-placement DRC/scene verification, undo semantics, and live-editor
   CAS as independently authorized slices.
2. **Generalize courtyard legality and side-aware placement.** Add line-chain/polygon/arc topology,
   configurable clearance, and safe side-flip source oracles before widening mutation support.
3. **A board that actually needs routing.** The single most important empirical gap: every net on
   the reference board was already routed by its designer, so the router has recognized coverage
   but never *produced* new copper on a real board. Author or adopt one, measure honestly, record
   it in `docs/architecture/routing-baseline.md`.
4. **IPC apply (v0.2 of the apply arc).** `kicad-python`'s `begin_commit`/`push_commit` gives a
   real single-undo-step transaction into a running KiCad. See
   [`safe-apply-references.md`](../research/safe-apply-references.md) for the constraints — the hard
   part is binding an in-memory document to a file digest.
5. **`v0.5.0` release** once both apply surfaces have soaked and the release ledger names a green
   source commit.

## KiCad facts that will bite you if you forget them

Each is load-bearing somewhere in the code; rediscovering them costs hours.

- Pad `(at x y angle)` angles are **absolute in the board frame** — do not add the footprint
  rotation. Pinned by an SVG-oracle test.
- Boards are stored **y-down**; a quarter turn is `(x, y) → (y, −x)`.
- SVG export is byte-deterministic except one `<title>` line (wall clock + filename). Canonicalize
  it before digesting.
- Silkscreen strings appear **twice in literal form** (invisible `<text opacity="0">` and `<desc>`);
  excluding text layers is the only real quarantine, byte-grep is the check.
- `kicad-cli` **exits 0 after a truncated write** at a size ceiling. Verify document completeness.
- `.lck` files carry user+host, no PID, and leak. A hint a GUI may be open — refuse, never override.
- KiCad interleaves segments and vias, so there is no "segment section"; append at the root close.

## Repo mechanics

- `.venv/bin/python`, `PYTHONPATH=src`. Pre-commit auto-formats — `ruff format` and `git add -A`
  before committing or the hook conflicts and rolls back.
- Force-push is blocked; reconcile a stacked branch by merging `main` into it (identical tree = no-op
  content).
- `gh pr merge` can delete a branch even when the merge fails; if a PR closes unexpectedly, re-push
  and `gh pr reopen`.

## Standing systems you inherit

- A **Codex review remediation routine** runs every two hours against open PRs
  (`trig_01WkyDsdY8wmEfu1Pm2WwtfP`). It cannot claim KiCad-verified results (no KiCad in the cloud
  sandbox) and only sees open PRs, so sweep for post-merge comments manually.
- External social posting is paused by maintainer instruction. Keep updates in GitHub issues, pull
  requests, ledgers, and release notes unless posting is explicitly re-authorized.

## Start here

Read [`project-state.md`](project-state.md) §2 (invariants) and §5 (next steps), `AGENTS.md`, and
[ADR-0059](../adr/0059-separately-authorized-placement-apply.md). Check the
stacked PR chain and hosted review state before changing public contracts. Protected-main merges
require direct maintainer approval; do not merge them implicitly.
