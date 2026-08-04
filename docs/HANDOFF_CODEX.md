# Codex handoff — CopperMCP

You are picking up CopperMCP. This document is the fast path for the Codex agent that continues the
work. Read `docs/HANDOFF.md` for the full state; read `AGENTS.md` for the repository contract you
must obey on every change. This file tells you where you are standing right now and exactly what to
do first.

## Right now

- Default branch `main` includes merged PR #47: **Board IR 0.2 with revision-bound footprint
  modelling**. The current development arc is Placement 0.2's versioned rectangular-courtyard
  legalizer, grounded in KiCad 10.0.5 source and a nanometre-boundary oracle.
- KiCad 10.0.5 is required for the external-oracle benchmark and real-KiCad nodes; dependency-light
  CI skips those nodes where the executable is unavailable.
- Check the current branch, PR, hosted CI, and review-bot threads before choosing the next item.

## The one rule that matters most

**AI proposes, deterministic code disposes.** Nothing a model emits reaches a board without being
recomputed, replayed, and verified by code that does not consult a model. Every capability in this
repo is built on that line. If you are ever tempted to trust a model-supplied value directly —
don't; recompute it and compare.

The corollary you will feel constantly: **every claim is bound to a test or declared a non-claim.**
When you cannot verify something, model it as a one-value literal (`not_run`, `not_modelled`,
`inconclusive`) rather than implying it. Two findings were fixed in this very branch because a
contract made a promise the code did not keep — a non-rectangular courtyard that validated, a
padless footprint reported as "does not exist," and intersecting same-footprint rectangles treated
as unrelated solids even when KiCad merged them or interpreted a hole. These were honesty bugs,
and they are the kind of thing to watch for.

## How every change ships here

1. **Design before code** for anything touching a public contract. Read the closest ADR
   (`docs/adr/`) first. For a new capability, write the design as a report, not as code.
2. **Research-ground new arcs.** Each major slice starts from a current-literature pass in
   `docs/research/` with licences and per-item implications, cited from that slice's ADR. Do not
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

`apply_candidate` is the only surface that writes to a user's file. It is **default-off**
(`COPPER_MCP_ALLOW_APPLY`). When you extend anything that writes, deletes, or authorizes:

- Run a **dedicated adversarial review** on the diff, separate from the normal gate. The apply PR's
  adversarial pass found eleven real defects — including a lock that was documented but did not
  exist, an auto-restore that destroyed concurrent writes, and post-write failures reported as
  "nothing changed." None were caught by the ordinary tests.
- The safety property is absolute: an operation either changes the board exactly as previewed, or
  refuses and leaves it byte-identical, or truthfully reports partial verification. It must hold
  under a concurrent KiCad save. Prove concurrency with genuine under-lock contention, never a
  wall-clock timeout (a flaky safety test is worse than none — it will be ignored).

## What to build next, in order

1. **Finish and merge Placement 0.2 courtyard legality.** Require 9/9 agreement with the isolated
   KiCad 10.0.5 overlap oracle, local topology-oracle regressions for strict disjoint/touch/merge/
   hole semantics, a green full gate, and review-bot triage. Do not widen the claim to custom
   `courtyard_clearance`, general topology, rectangles below the measured 10,051 nm per-axis floor,
   full DRC binding, or apply.
2. **Generalize footprint/courtyard fidelity.** Capture revision-bound custom rule context and add
   source-oracle fixtures before back-side or line/polygon/arc import. KiCad can place front and
   back courtyard sets on one footprint, so a future general Board IR must attach layer/side to
   each courtyard rather than infer it forever from the footprint side.
3. **Close the footprint replay gap before placement apply.** Board IR still omits author text,
   fabrication graphics, library identity, properties, and 3D-model pose. Model and verify every
   pose-carrying node before extending the destructive apply engine; footprint pose splices need a
   stricter assertion than route append.
4. **A board that actually needs routing.** The single most important empirical gap: every net on
   the reference board was already routed by its designer, so the router has recognized coverage
   but never *produced* new copper on a real board. Author or adopt one, measure honestly, record
   it in `docs/architecture/routing-baseline.md`.
5. **IPC apply (v0.2 of the apply arc).** `kicad-python`'s `begin_commit`/`push_commit` gives a
   real single-undo-step transaction into a running KiCad. See `docs/research/safe-apply-references.md`
   for the constraints — the hard part is binding an in-memory document to a file digest.
6. **`v0.5.0` release** once apply has soaked.

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
- Development is published on X as `@studiodawol` with evidence-bound posts. Continue only if you
  intend to keep the honesty bar — real numbers, stated limitations.

## Start here

Read `docs/HANDOFF.md` §2 (invariants) and §5 (next steps), `AGENTS.md`, and `ADR-0024`/`ADR-0025`
(placement and apply, the two active frontiers). Then merge PR #47 and begin placement apply. The
architecture is proven end-to-end; what remains is breadth, not a missing pillar.
