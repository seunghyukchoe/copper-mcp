# CopperMCP project state

**This is a living document. It is current as of the `0.8.0` release line and must be updated in
the release pull request of every subsequent release** — the version, the contract versions, the
tool counts, the milestone state, and the limitations all move, and this document asserts them in
the present tense. Last verified against the repository on **2026-08-13**, at `main` `73334b0`.

It is not release authorization. A release is authorized only by a `Ready` row in the
[release ledger](../ledgers/release-ledger.md) naming a validated source commit.

Where this document and the repository disagree, the repository is right. Branch heads, gate
counts, PR state, and issue state must be read from Git and GitHub rather than from a paragraph
here.

---

## 1. Where the project stands

**Released:** `v0.1.0` → `v0.7.0`, with attested wheel and sdist artifacts. `v0.7.0` was tagged and
published on 2026-08-12 from the `Ready` authorization at `09deaaf`, and
[the `0.7.0` migration notes](../migrations/copper-mcp-0.7.0.md) shipped with it. `pyproject.toml`
still reads `0.7.0`; the `0.8.0` bump belongs to the release pull request, not here.

**Two published-release rows are outstanding**, and this document does not supply them: the
[release ledger](../ledgers/release-ledger.md)'s published table records `0.1.0`–`0.4.0` and
`0.6.0` and has no row for `0.5.0` or for `0.7.0`, even though both tags and both GitHub releases
exist. That is an open post-release step in the ledger, recorded here so it is not mistaken for a
publication that never happened. **The release ledger, not this document and not the changelog,
authorizes a release** — but where publication is concerned it is currently behind the repository,
and `gh release list` is the observation to trust.

**Contract versions in force.** These are the numbers a caller pins, and they move independently of
the package version:

| Contract | Version | Constant that decides it | Note |
|---|---|---|---|
| Board IR | `0.3.0` | `board_ir.types.BOARD_IR_SCHEMA_VERSION` | `0.2.0` and `0.1.0` remain as **immutable legacy** schemas, kept as compatibility evidence; `0.2.0` is byte-frozen by [ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md) and, as published, spans three accepted sets across `v0.5.0`–`v0.8.0`. Old snapshots are re-converted from the source board, never auto-migrated — see [the 0.1 → 0.2 migration](../migrations/board-ir-0.2.md), [the 0.9.0 note](../migrations/copper-mcp-0.9.0.md) and [the Board IR contract](../architecture/board-ir.md). |
| Circuit Scene | `0.3.0` | `circuit_scene.SCENE_VERSION` | There is no compatibility mode. A truncated scene now withholds a whole object kind rather than returning an empty array. |
| Circuit Intent IR | `0.1.0` | `circuit_ir.types.CIRCUIT_INTENT_SCHEMA_VERSION` | See [the Circuit Intent contract](../architecture/circuit-intent.md). |
| Router | `astar-grid/0.7.0` | `routing.astar.ROUTER_VERSION` | Advanced in `0.7.0`; every stored candidate and bundle identity must be re-derived. No path geometry changed. Candidates recorded under `0.4.0`–`0.6.0` still select their historical search behaviour for replay. |

**Tool surface.** Measured by calling `list_tools()` under each transport rather than by counting a
list: **28 tools on `stdio`, 27 on `streamable-http`.** The single difference is
`render_circuit_schematic`, which is registered only under `stdio` because it delivers an opaque
resource. Two resources (`scene_render`, `schematic_artifact`) are likewise stdio-only.
[The agent contract](../agents.md) and [the usage guide](../usage.md) own the tool-by-tool
detail; what follows is the shape, not the contract.

| Verb | Tools | What it is bound to |
|---|---|---|
| See (structure) | `inspect_board`, `inspect_board_ir` | counts and digests only, no geometry disclosure |
| See (semantics) | `observe_board_scene`, `observe_live_board_scene`, `observe_post_placement` | Circuit Scene `0.3.0`, region-scoped, stable ref ids, board text quarantined |
| See (live editor) | `inspect_live_board`, `inspect_live_editor_context` | read-only local KiCad IPC, operator-gated, redacted |
| Look | `observe_board_scene` with `include_render` | normalized SVG, digest-bound, **stdio only** |
| Trace | `preview_route`, `preview_layered_route`, `preview_route_bundle`, `preview_live_route`, `preview_live_layered_route` | exact integer geometry, revision-bound, optional authoritative KiCad DRC evidence |
| Judge placement | `preview_placement`, `preview_live_placement` | Board IR-projected subjects, three-valued courtyard overlap, locked-move refusal |
| Build | `render_circuit_schematic` (**stdio only**), `verify_circuit_schematic_erc`, `verify_source_to_board_parity` | deterministic schematic from Circuit Intent IR; authoritative `kicad-cli` ERC and DRC parity evidence |
| Check | `run_board_drc`, `validate_candidate`, `compare_candidates` | fixed-argument headless DRC, read-only; candidate normalization and ranking |
| Queue | `start_routing`, `get_routing_job`, `cancel_routing_job`, `export_routing_candidate` | durable file-backed layered proposals; geometry export is separately authorized |
| **Apply route** | `apply_candidate` | route candidates written to the real file, **default off** (`COPPER_MCP_ALLOW_APPLY=1`) |
| **Apply placement** | `apply_placement_candidate` | bounded front-side pose candidates, placement-scoped token, CAS, backup, atomic replacement, **default off** |
| Apply live (**refuses**) | `apply_live_candidate` | verifies every precondition for a one-undo-commit apply into a running KiCad and then answers `capability_not_implemented`. It is not a mutation surface; it names exactly which checks ran. |

Everything except the two file apply tools is read-only, create-new, or a declared refusal. Each
apply tool requires its own single-use token in addition to its operator gate, and
`apply_live_candidate` needs **both** `COPPER_MCP_ALLOW_LIVE_APPLY=1` and
`COPPER_MCP_ALLOW_LIVE_IPC=1`; neither is implied by `COPPER_MCP_ALLOW_APPLY`.

**Milestone state,** read from the milestone API on 2026-08-13:

| Milestone | Closed | Open | Remaining |
|---|---|---|---|
| M1 — KiCad inspection completion | 7 | 1 | [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116), the conversion tracker |
| M2 — Routing depth | 4 | 1 | [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65), open-baseline comparison |
| M3 — Safe application completion | 0 | 1 | [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68), IPC one-undo-commit apply |
| M4 — Scene, policy, and evaluation | 3 | 0 | **complete as an accounting fact**, not as a claim that every `[~]` under it is finished |
| M5 — Performance and physics | 0 | 6 | nothing has landed |

M1's sole remaining tracked issue is #116, retitled from the original real-board conversion survey
to the M1 conversion tracker it had become ([D-191](../ledgers/decision-ledger.md)). The two
conversion gaps that kept it open —
[#152](https://github.com/seunghyukchoe/copper-mcp/issues/152) and
[#153](https://github.com/seunghyukchoe/copper-mcp/issues/153) — are now **closed**, and
[#141](https://github.com/seunghyukchoe/copper-mcp/issues/141) is answered by decision and staying
refused. **Unmilestoned work is still invisible to every count above**: #141, #164, #166, #167,
#170 and #172 are open and carry no milestone. [The roadmap](../roadmap.md) describes each
milestone as outcomes; **GitHub is the source of truth over both**, so read it with
`gh issue list -R seunghyukchoe/copper-mcp` and
`gh api repos/seunghyukchoe/copper-mcp/milestones` rather than trusting a checkbox.

**Record ranges.** ADR-0001 … ADR-0107, next unused **0108**; six numbers (0027, 0082, 0083, 0085,
0086, 0105) are spent or held by an open branch and never recycled. Ledgers: `D-199`, `R-153`,
`SEC-146`, `B-110` are the highest allocated (`B-109` was declined under rule 4 and is spent). Allocate in the pull request that lands the entry, never before, per
[the ID convention](../ledgers/README.md) — and read the two checkers' own output
(`scripts/check_adr_numbers.py`, `scripts/check_ledgers.py`) rather than this paragraph, which is
one release away from being wrong by construction.

---

## 2. The invariants that make this project what it is

These are not style preferences. Most of them were paid for with a bug. Preserve them.

1. **AI proposes, deterministic code disposes.** No model output reaches a board without being
   recomputed, replayed, and verified by code that does not consult a model. If you are ever
   tempted to trust a model-supplied value directly — don't; recompute it and compare.
2. **Every claim is bound to evidence or listed as an explicit non-claim.** ERC, board parity,
   electrical validation, and fabrication readiness are all reported as `not_run` or `not_modelled`
   one-value literals rather than implied. A field that can only hold one value cannot be quietly
   upgraded — and when an evaluator does land, the literal is widened deliberately rather than
   reinterpreted. Courtyard legality is the worked example: it was a one-value non-claim until the
   bounded same-side orthogonal evaluator existed, then two-valued, and is now three-valued —
   widened again when measurement showed KiCad's contracted courtyard cache disagrees with raw
   ring geometry below 10,000 nm of penetration
   ([ADR-0075](../adr/0075-courtyard-oracle-parity.md)). Its scope is still stated rather than
   implied.
3. **Direction of error is a design decision, stated everywhere.** Obstacles over-approximate;
   connectivity proofs under-approximate; the board outline is routing *room*, so it may only be
   under-approximated. A wrong answer must cost a redundant route, never a false connection.
   Legality verdicts are three-valued because `inconclusive` is honest and `proven_clear` would
   not be.
4. **Fail closed, with typed non-echoing diagnostics.** Refusals never echo caller input and never
   guess. `infeasible_constraints` (a proof) and `budget_exhausted` (an admission of ignorance)
   must never collapse into each other. As of `0.7.0` a refusal also carries what a designer needs
   to act on it: an `off_grid` refusal names the pad, the pitch and the exact miss
   ([ADR-0093](../adr/0093-actionable-off-grid-refusals.md)), and root Board IR refusals no longer
   share the constant locator `kicad_pcb.unsupported`.
5. **Bounded everything.** Lattice nodes, expansions, obstacle work, vertices, objects,
   annotations, file sizes, deadlines. New work charges an existing budget or declares its own.
   Parse budgets are operator-configurable as of `0.7.0`, as a set that moves together
   ([ADR-0079](../adr/0079-discriminated-configurable-parse-budgets.md)).
6. **Exact integers.** Nanometres and micro-degrees, rational comparisons, no floating point in
   any geometric predicate.
7. **Append-only ledgers.** Decision, risk, security, benchmark, release. Corrections are new
   entries with new IDs, never row rewrites. `scripts/check_ledgers.py` enforces the shape and the
   identifier allocation: duplicates fail, gaps are reported, and six historical collisions
   (`D-137`, `D-139`, `D-140`, `B-076`, `B-078`, `B-082`) are recorded rather than repaired.
8. **Release authorization is a separate, deliberate act.** A `Ready` row naming a validated source
   commit, then a metadata commit touching only the ledger and changelog, then the tag.
9. **The board's own text is untrusted data.** Silkscreen, fab text, properties, and net names are
   quarantined structurally, never interpolated into instruction-bearing fields.

---

## 3. How every change ships here

1. **Design before code** for anything touching a public contract. Read the closest
   [ADR](../adr/README.md) first. For a new capability, write the design as a report, not as code.
2. **Research-ground new arcs.** Each major slice starts from a current-literature pass in
   [`docs/research/`](../research/README.md) with licences and per-item implications, cited from
   that slice's ADR. Do not skip this for algorithmic work — it is how the licensing landmines stay
   out of the tree.
3. **Slice small.** Land the pure/verifiable part first (a parser, an engine, an assertion), then
   the surface. The apply arc was five slices for exactly this reason.
4. **Test before claiming.** Add the regression test, then mutation-check it: revert the fix, watch
   the test fail, restore. A test that never fails proves nothing. **Since
   [ADR-0098](../adr/0098-reproducible-mutation-evidence.md) a mutation claim is admissible only if
   the repository can re-run it**: mutants go through `scripts/mutation_harness.py`, the spec is
   committed under [`docs/mutants/`](../mutants/README.md) with anchors and a mutant→killing-test
   mapping, and "N mutants, 0 survivors" bounds the N chosen and never the mutation space. The
   audit in that ADR classified every earlier claim as `safe`, `exposed` or `unauditable`; cite an
   old one with its literal. See [the development guide](../development.md#mutation-evidence).
5. **Full gate, both mypy generations.** `make check` locally; CI floats mypy to the newest
   version, so type-check against a scratch install of the latest mypy too.
6. **PR, then resolve every review thread.** Branch protection requires conversation resolution.
   The `chatgpt-codex-connector[bot]` reviews every PR; triage each finding against current code,
   fix the real ones with regression tests, reply with the commit, resolve the thread.
7. **Ledgers are append-only.** Record decisions, risks, benchmarks, security reviews, and releases
   under [`docs/ledgers/`](../ledgers/README.md). A correction is a new ID that names what it
   corrects.
8. **Releases are a deliberate act.** See [the release process](../releasing.md);
   `scripts/check_version.py --tag` gates it.

**Destructive capability is different.** `apply_candidate` and `apply_placement_candidate` are the
only surfaces that write to a user's file. When you extend anything that writes, deletes, or
authorizes, run a **dedicated adversarial review** on the diff, separate from the normal gate. The
apply PR's adversarial pass found eleven real defects — including a lock that was documented but
did not exist, an auto-restore that destroyed concurrent writes, and post-write failures reported
as "nothing changed." None were caught by the ordinary tests. The safety property is absolute: an
operation either changes the board exactly as previewed, or refuses and leaves it byte-identical,
or truthfully reports partial verification. It must hold under a concurrent KiCad save. Prove
concurrency with genuine under-lock contention, never a wall-clock timeout — a flaky safety test is
worse than none, because it will be ignored.

---

## 4. Standing systems left running

- **Codex review remediation routine** — a cloud agent (`trig_01WkyDsdY8wmEfu1Pm2WwtfP`, every two
  hours, Opus 5, no connectors) polls open PRs for unaddressed `chatgpt-codex-connector[bot]`
  comments, triages against current branch code, fixes real findings with regression tests, runs
  the non-KiCad gate, pushes, and replies with Fixed/Refuted/Superseded. Manage or disable it at
  <https://claude.ai/code/routines/trig_01WkyDsdY8wmEfu1Pm2WwtfP>. It cannot claim KiCad-verified
  results — the cloud sandbox has no KiCad, and its prompt says so. **Known gap:** it only sees
  open PRs, so comments landing after a merge need a manual sweep.
- **Research-backed iteration** — every slice starts from a current-literature pass recorded in
  [`docs/research/`](../research/README.md) with licences and per-item implications, cited from
  that slice's ADR. The tree now holds dozens of such notes;
  [`audit-2026-08-03.md`](../research/audit-2026-08-03.md) is an independent audit of the stack as
  it stood on that date.
- **Adversarial review for destructive capability** — see §3. Repeat it for anything that writes,
  deletes, or authorizes.

---

## 5. Known limitations, stated plainly

- **Not every real board converts, and a refusal names only the *first* blocker.** Re-measured on
  the private working corpus on 2026-08-13 (`B-107`, reproducing `B-103`): **13 of the 18 saves in
  that corpus convert.** The 18 files hold **17 distinct board contents** — one pair is
  byte-identical across two save directories, and that pair is not among the boards that moved — so
  the same result is 13 of 17 distinct boards; state which denominator you mean. Neither is the
  frozen 12-board set the survey measured, and **no count from this corpus is stable**: it is a live
  tree the designer edits during long runs, so every measurement is bracketed by a conversion-only
  digest sweep and reported with the digests. B-107 excluded one save outright because the designer
  saved it twice mid-run. Five saves refuse, each with one typed refusal: a custom-shape SMD pad on
  four ([#153](https://github.com/seunghyukchoe/copper-mcp/issues/153),
  [ADR-0100](../adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md)) and copper text on
  one ([#141](https://github.com/seunghyukchoe/copper-mcp/issues/141), answered by
  [ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md) and refused **by decision**, not
  by omission). **The five is measured; the four-and-one split is composed from two runs** — ADR-0100
  measured three custom-pad refusals, and ADR-0099 then converted one pad-`property` save and
  advanced the other onto the same custom pad. **Read a refusal as an existential, never a
  universal**: conversion stops at the first error, so "and nothing else" cannot be inferred from
  one diagnostic — every gap closed since the survey advanced a refusal on at least one board rather
  than converting it, five times in three days. **No "converts every board" result is claimed at any
  count**, converting is not routing, placing or appliability, and no further count should be stated
  until a re-measured survey supports it.
- **Real-board routing is a small, candidate-only result, not a product claim — and every figure
  from this corpus has a short shelf life.** The current measurement is `B-107`, 2026-08-13, over
  the 13 converting saves: **16 of 465 first-40-net previews routed**, with **324
  `already_connected`**, 46 `invalid_two_pin_net`, 35 `off_grid`, 31 `no_path`, 6
  `no_path_in_region`, 3 `obstacle_budget_exceeded`, 3 `unsupported_geometry` and 1
  `search_budget_exceeded`. It **supersedes** the two figures before it — `B-096`'s 14 of 385, and
  `B-099`'s survey close-out replay of 0 of 425 with 320 `already_connected` — and the movement
  between them is the corpus, not the code: the designer routes these boards between runs, and a
  preview cannot route what is already routed. **Cite none of the three as a capability.** Every
  one of these previews runs **one net at a time on `F.Cu` against the unrouted snapshot**, so the
  candidates are **not mutually compatible** and this is **not** a whole-board completion result.
  Nothing was DRC-checked, applied, or written. Wider evidence comes from imported external geometry
  (LLM-generated two-layer tscircuit boards), which does not generalise to production PCB design.
- **DRC counts from this corpus are not comparative evidence at all.** B-107 ran the same code over
  the same bytes twice and nine boards' DRC sections still differed — an `error_count` of 936 versus
  941, `hole_clearance` 201 versus 202, a whole `tracks_crossing` type present in one run and absent
  from the other. Authoritative KiCad DRC counts are **not reproducible run to run on identical
  bytes** ([#170](https://github.com/seunghyukchoe/copper-mcp/issues/170), open), so any published
  count is a claim about one invocation and never about the board. Every differential in B-107 and
  B-108 excludes the DRC section for this reason.
- **Placement preview measures legality-as-found, not quality.** The real-board sweep ran without
  rules, so a clean verdict means *legal as the board already is* and says nothing about whether
  the placement is any good. B-099's headline that the supported subset "never binds on this
  corpus" (991 of 991 previewed, 0 refusals) no longer holds either: its 2026-08-13 close-out replay
  records 1,127 previewed and **156 `refused/illegal_placement`** on one board — 156 courtyard
  overlaps already present in the board, surfaced by the widened courtyard model (ADR-0080,
  ADR-0097), not 156 bad candidates. Those totals are from the 12-converting replay; B-107 confirms
  the placement records are unchanged board by board but does not re-total them at 13.
- **Most real boards are unappliable through the file apply gates**, wherever an `Edge.Cuts` outline
  is assembled from `gr_line` segments and any member lacks a single distinct native identity. The
  composite native identity in [ADR-0087](../adr/0087-composite-native-identity-for-assembled-outlines.md)
  moved both gates from 0 of 11 to 3 of 11 on the surveyed corpus; the current figure is **5 of 13
  appliable** (`B-107`, 2026-08-13), and the rest remain refused by design.
- **Placement apply is deliberately narrow.** The file-backed service applies only replay-verified
  front-side, orthogonal, native-identity footprints with supported rectangular `F.CrtYd` syntax.
  Author text, fabrication graphics, library identity, properties, 3D-model pose, side flips,
  post-apply DRC/scene evidence, undo transactions, and live IPC mutation remain outside the gate.
- **Live apply does not exist.** `apply_live_candidate` verifies every precondition and then
  refuses with `capability_not_implemented`. Read it as a designed refusal, not a stub.
- **Apply gives a pre-apply copy, not a KiCad undo step.** Restoring is manual. IPC-based
  one-undo-commit apply is designed and deferred
  ([#68](https://github.com/seunghyukchoe/copper-mcp/issues/68)).
- **Whole-board Circuit Scene requests truncate.** As of `0.7.0` a truncated scene withholds a
  whole object kind rather than returning a misleading empty array
  ([ADR-0088](../adr/0088-complete-or-withheld-scene-kinds.md)) — that is a correction of the
  report, not an increase in capacity.
- **Renders are whole-board even for a windowed scene**, and are advisory, never geometric
  authority.
- **No successful real-editor IPC oracle run has been recorded.** The live surfaces are exercised
  through a fake official-client seam; the workstation IPC server is disabled.
- **`R-033`**: the committed CopperTone board still carries mounting-hole keepout octagons
  inscribed at 2.85 mm, so edges sit 0.2169 mm inside the requirement. The generator is fixed;
  regenerating the board invalidates every recorded measurement, so it needs its own slice.
- **Unsafe-filesystem detection is best effort.** A negative means not detected, never known safe.

---

## 6. What to do next, in priority order

1. **Decide what closing [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116) now
   requires.** All five gaps it originally named are closed, every number in its original title is
   wrong, and both gaps that kept it open after that — #152 and #153 — are closed too. It stays open
   as the **M1 real-board conversion tracker** ([D-191](../ledgers/decision-ledger.md)) because it
   is the only issue carrying the milestone, and closing it marks M1 complete. **Do not close it on
   a target count**: one of the five remaining refusals is permanent by decision, so the corpus can
   never reach 18 of 18, and a completion criterion phrased as a count would be unreachable by
   construction. The honest close-out is a fresh survey plus an explicit statement of what stays
   refused and why. **Expect a stack** if you take another construct: every gap closed since the
   survey advanced the refusal on at least one board instead of converting it — #116's own courtyard
   causes, then #140, then #151, then the pad `property` field, and now the custom pad behind it,
   five times in three days. **#141 is answered and is not a gap to take**: copper text has no
   envelope derivable from the board document, measured against `kicad-cli`, so it stays refused by
   decision rather than by omission
   ([ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md)) — the board it blocks stays
   blocked and the count is unchanged. Treat a stack of blockers as the default and measure after
   each, never before.
2. **Make the real-board routing result mean something.** 16 of 465 is a floor, not a capability,
   and 324 of those previews are `already_connected` — this corpus is mostly routed, so it can no
   longer answer the question, and the figure has now moved three times without the router changing.
   The next step is DRC evidence and cross-net compatibility on a real board with genuinely open
   nets, not a larger sweep over boards the designer has finished. Note that DRC evidence from this
   corpus needs [#170](https://github.com/seunghyukchoe/copper-mcp/issues/170) answered first:
   KiCad's own counts do not reproduce run to run on identical bytes.
3. **Close placement data-fidelity and post-action gates.** Model and replay author text,
   fabrication graphics, library identity, properties, and 3D-model pose affected by a move, then
   add post-placement KiCad DRC/scene evidence, undo semantics, and live-editor CAS as separate
   bounded contracts.
4. **Generalize courtyard and side-aware geometry.** Add source-oracle fixtures for arc and
   multi-loop topology, nonzero clearance, and safe side flips before widening mutation support.
   Note that per-layer courtyards now exist ([ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md)):
   a side flip has to swap a footprint's two courtyard sets as well as mirror them, and the
   source-preserving serializer still refuses every board carrying a far-side courtyard rectangle.
5. **IPC apply ([#68](https://github.com/seunghyukchoe/copper-mcp/issues/68)).**
   `kicad-python`'s `begin_commit` / `push_commit` gives a genuine single-undo-step transaction
   into a running KiCad. The hard part is binding an in-memory document to a file digest;
   [the research note](../research/safe-apply-references.md) lays out the constraints.
6. **Open-baseline comparison ([#65](https://github.com/seunghyukchoe/copper-mcp/issues/65))**
   remains unmeasured: FreeRouting is GPL-3.0 and absent from the recording environment, and every
   baseline is recorded `not_run` rather than estimated.
7. **Deferred quality items**: durable single-layer and live routing jobs, the `PlacementBackend`
   solver seam, and higher-degree RSMT-guided topology behind the existing `ordering_policy` seam.

---

## 7. Operational knowledge worth keeping

**Environment.** Use `.venv/bin/python`; system `python` does not exist. `make test` runs
`PYTHONPATH=src $(PYTHON) -m pytest` and that works, but **invoking the `pytest` console script
yourself needs `PYTHONPATH=src:.`** — `python -m pytest` prepends the working directory to
`sys.path` and the entry point does not, so `tests/test_audio_routing_gap.py` cannot import
`scripts.benchmark_audio_routing_gap` and one collection error empties the whole run. See
[the development guide](../development.md#running-pytest-directly-pythonpathsrc). `make check` is
the full gate: ruff lint and format, the checker scripts, mypy, pytest, secret scan, pip-audit,
isolated build. KiCad 10.0.5 lives at
`/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`; real-KiCad tests skip without it.

**Repo gotchas.**

- Pre-commit auto-formats. Run `ruff format` and `git add -A` **before** committing, or the hook
  stashes unstaged changes and the commit rolls back with a confusing conflict.
- Branch protection requires **conversation resolution**: reply to and resolve every review thread
  before a PR will merge.
- Force-push is blocked by the permission classifier. To reconcile a stacked branch after its base
  squash-merges, **merge main into the branch** — the tree is identical, so the merge is a no-op
  in content.
- `gh pr merge` may delete the branch even when the merge itself fails. If a PR closes
  unexpectedly, re-push the branch and `gh pr reopen`.
- Mypy floats to the newest version in CI. The floor is pinned at 2.3 to match, which ended a
  period of version-skew failures that passed locally and failed hosted.

**KiCad behaviours discovered the hard way** (each is load-bearing somewhere in the code):

- Pad `(at x y angle)` angles are **absolute in the board frame**; adding the footprint rotation
  double-counts and transposes non-square pads. Pinned by an SVG-oracle test.
- KiCad stores boards y-down while its angles read counter-clockwise on screen, so a quarter turn
  is `(x, y) → (y, −x)`.
- Back-side footprint-local coordinates in a saved KiCad board are already flipped. Do not add a
  second mirror when extending the adapter; require source-oracle fixtures before accepting them.
- Copper layer IDs come from KiCad's own declaration order, not from arithmetic: `F.Cu=0`,
  `B.Cu=2`, `In{N}.Cu=2+2N`, so a four-layer board's IDs deliberately do not ascend. Synthesizing
  the rule instead refused every real board with more than two copper layers.
- SVG export is byte-deterministic **except** one `<title>` line carrying a wall clock and the
  output filename. Canonicalize that line before digesting.
- Silkscreen strings appear **twice in literal, greppable form** — an invisible zero-opacity
  `<text>` and a `<desc>` — beside the stroked paths. Excluding text layers is the only real
  control; node filtering is not.
- `kicad-cli` **exits 0 after writing a truncated file** when it hits a size ceiling. Verify
  document completeness before trusting a digest.
- `.lck` lockfiles carry username and host, no PID, and leak. Treat as a hint that a GUI may be
  open; refuse, never override.
- KiCad's writer interleaves segments and vias in runs, so there is no canonical "segment section"
  — the root close is the only insertion point that modifies no existing span.
- KiCad chains its own `Edge.Cuts` outline with a non-zero chaining epsilon and closes sub-tolerance
  gaps. CopperMCP refuses the near-miss instead, because closing a gap adds routing room no segment
  encloses ([ADR-0076](../adr/0076-segment-assembled-edge-cuts-outline.md)).

---

## 8. Map of the codebase

```
src/copper_mcp/
  mcp_server.py, tools.py, cli.py      surfaces (MCP tools/resources, CLI commands)
  mcp_contracts.py                     closed Pydantic contracts for tool responses
  request_boundary.py, security.py     untrusted input validation; workspace confinement,
                                       descriptor-anchored reads, create-only and replace writes
  config.py, parse_budgets.py          settings and the discriminated, operator-set parse budgets
  board_ir/, board_ir_service.py       canonical Board IR 0.2: footprints, geometry, validation, digests
  adapters/                            KiCad parsers and serializers, CST span splicing, ERC/parity
  routing/                             exact-integer A*, layered A*, congestion, jobs, oracle
  circuit_ir/, circuit_intent_service  Circuit Intent IR and deterministic schematic build
  circuit_scene.py, scene_render.py    typed scene observation (0.3.0) and normalized renders
  placement/, placement_preview.py     intent language, legalizer, route-aware scoring, preview
  apply/, live_apply.py                tokens, pure engines, mutating file service, live refusal
  kicad_ipc*.py, live_*.py             read-only IPC observation and live proposal surfaces
  zone_fill.py, kicad_cli.py           fill authority, bounded KiCad execution
docs/
  README.md                            documentation index; start here
  adr/                                 ADR-0001 … ADR-0104, the decision record
  mutants/                             committed mutation specs; a claim without one is prose
  architecture/                        overview, board-ir, circuit-intent, routing-baseline,
                                       mcp-api, security-model
  ledgers/                             decision, risk, security, benchmark, release (append-only)
  research/                            dated literature, licensing, and measurement notes per arc
  migrations/                          per-release upgrade notes
  handoff/                             project-state.md (this document, living),
                                       codex-onboarding.md (dated 2026-08-05, superseded)
tests/                                 regression and integration fixtures under tests/fixtures/
scripts/                               the gate checkers: version, ledgers, ADR numbers, doc links
```

Read in this order to get oriented: `README.md`, `AGENTS.md`,
[`architecture/security-model.md`](../architecture/security-model.md),
[`architecture/board-ir.md`](../architecture/board-ir.md),
[`architecture/routing-baseline.md`](../architecture/routing-baseline.md), then
[the ADR index](../adr/README.md) — the recent arcs are
[ADR-0094](../adr/0094-root-board-properties-as-metadata.md) onward, which is where the whole
`0.8.0` line lives.

---

## 9. Public presence

External social posting is paused by maintainer instruction. Keep public project communication in
GitHub issues, pull requests, ledgers, release notes, and repository documentation. Any future
social update must be explicitly re-authorized and remain evidence-bound — real test counts, real
DRC results, and limitations stated rather than omitted.

Repository discoverability: topics cover `mcp`, `model-context-protocol`, `kicad`, `pcb`,
`pcb-automation`, `eda`, `autorouter`, `autorouting-research`, `ai-agents`, `llm-tools`,
`pcb-design`, `open-hardware`, `audio-electronics`, `python`. Releases carry attested artifacts and
a board render. Traction is early — the honest-evidence posture is the differentiator worth
keeping, since the crowded comparison set (cloud AI autorouters) publishes no peer-reviewed
evidence at all, and no other KiCad MCP server offers a validated intent contract or retained DRC
evidence.

## 10. Licensing boundaries

CopperMCP is Apache-2.0. **freerouting is GPL-3.0** — concepts from the literature only, never
code, and it is deliberately absent from the benchmarking environment. **GeoSteiner and FLUTE**
carry non-commercial encumbrances and **REST** uses CUHK's non-OSI CU-SD licence; none may become
dependencies. **TritonRoute, InstantGR, `kicad-python`, and OmniParser** are BSD-3 or MIT and are
legitimate references. [`docs/research/`](../research/README.md) records the full survey with
per-item verdicts.
