# CopperMCP project state

**This is a living document. It is current as of the `0.12.0` release line and must be updated in
the release pull request of every subsequent release** — the version, the contract versions, the
tool counts, the milestone state, and the limitations all move, and this document asserts them in
the present tense. Last verified against the repository on **2026-09-04**, at `main` `1bb3b6f`.

It is not release authorization. A release is authorized only by a `Ready` row in the
[release ledger](../ledgers/release-ledger.md) naming a validated source commit.

Where this document and the repository disagree, the repository is right. Branch heads, gate
counts, PR state, and issue state must be read from Git and GitHub rather than from a paragraph
here.

---

## 1. Where the project stands

**Released:** `v0.1.0` → `v0.12.0`, with provenance that is not uniform across that span.
Every release except **`0.7.0`** carries a verified build-provenance attestation; `0.7.0` has
**none** for either asset. A KiCad PCM package and its metadata are release assets only from
`0.8.0` onward — the ledger records attestation verified for all four assets from `0.9.0`, and
for the wheel and sdist at `0.8.0`. Which assets an attestation covers varies by release, so
[the release ledger](../ledgers/release-ledger.md)'s Security column, not this sentence, is the
authority. `v0.12.0` was tagged and published on 2026-09-01 from the `Ready`
authorization at `56248fb`, and
[the `0.12.0` migration notes](../migrations/copper-mcp-0.12.0.md) shipped with it.
`pyproject.toml` reads `0.12.0`; the next version bump belongs to the next release
pull request, not here.

**The release ledger is current through `0.12.0`.** The two once-outstanding
published-release rows the 0.8.0 line of this document recorded as missing are now
filed: `0.5.0` (verified attestations, tag ancestor of `main`) and `0.7.0`
(recorded with an explicitly weaker provenance posture -- locally built assets
after the release workflow was cancelled, no Sigstore bundle -- rather than
corrected, because replacing a published asset is the larger action). **The
release ledger, not this document and not the changelog, authorizes a release**
— and where publication is concerned `gh release list` remains the observation
to trust over any paragraph here.

**Contract versions in force.** These are the numbers a caller pins, and they move independently of
the package version:

| Contract | Version | Constant that decides it | Note |
|---|---|---|---|
| Board IR | `0.4.0` | `board_ir.types.BOARD_IR_SCHEMA_VERSION` | `0.3.0` and earlier remain immutable legacy schemas. Custom pads now carry separate anchor/core and conservative copper-envelope roles; old snapshots are re-converted from source, never auto-migrated — see [the 0.4 migration](../migrations/board-ir-0.4.md) and [ADR-0111](../adr/0111-custom-pad-anchor-and-envelope.md). |
| Circuit Scene | `0.4.0` | `circuit_scene.SCENE_VERSION` | Custom-pad envelopes are explicitly labelled in pad-local coordinates. There is no compatibility mode. |
| Circuit Intent IR | `0.1.0` | `circuit_ir.types.CIRCUIT_INTENT_SCHEMA_VERSION` | See [the Circuit Intent contract](../architecture/circuit-intent.md). |
| Router | `astar-grid/0.7.0` | `routing.astar.ROUTER_VERSION` | Advanced in `0.7.0`; every stored candidate and bundle identity must be re-derived. No path geometry changed. Candidates recorded under `0.4.0`–`0.6.0` still select their historical search behaviour for replay. |

**Tool surface.** Measured by calling `list_tools()` under each transport rather than by counting a
list: **29 tools on `stdio`, 28 on `streamable-http`.** The single difference is
`render_circuit_schematic`, which is registered only under `stdio` because it delivers an opaque
resource. Two resources (`scene_render`, `schematic_artifact`) are likewise stdio-only.
[The agent contract](../agents.md) and [the usage guide](../usage.md) own the tool-by-tool
detail; what follows is the shape, not the contract.

| Verb | Tools | What it is bound to |
|---|---|---|
| Identify | `server_info` | server name, version, `maturity`, and the implemented/planned capability lists; also published as the `pcb://server/manifest` resource |
| See (structure) | `inspect_board`, `inspect_board_ir` | counts and digests only, no geometry disclosure |
| See (semantics) | `observe_board_scene`, `observe_live_board_scene`, `observe_post_placement` | Circuit Scene `0.4.0`, region-scoped, stable ref ids, board text quarantined |
| See (live editor) | `inspect_live_board`, `inspect_live_editor_context` | read-only local KiCad IPC, operator-gated, redacted |
| Look | `observe_board_scene` with `include_render` | normalized SVG, digest-bound, **stdio only** |
| Trace | `preview_route`, `preview_layered_route`, `preview_route_bundle`, `preview_live_route`, `preview_live_layered_route` | exact integer geometry, revision-bound, optional authoritative KiCad DRC evidence |
| Judge placement | `preview_placement`, `preview_live_placement` | Board IR-projected subjects, three-valued courtyard overlap, locked-move refusal |
| Build | `render_circuit_schematic` (**stdio only**), `verify_circuit_schematic_erc`, `verify_source_to_board_parity` | deterministic schematic from Circuit Intent IR; authoritative `kicad-cli` ERC and DRC parity evidence |
| Check | `run_board_drc`, `validate_candidate`, `compare_candidates` | fixed-argument headless DRC, read-only; candidate normalization and ranking |
| Verify foreign | `verify_external_route_candidate` | versioned, reference-only, read-only MCP disposal of one closed v1/v2 foreign route through bounded Board IR validation and mandatory authoritative KiCad DRC; no CLI, persistence, repair, apply, or live-IPC peer |
| Queue | `start_routing`, `get_routing_job`, `cancel_routing_job`, `export_routing_candidate` | durable file-backed layered proposals; geometry export is separately authorized |
| **Apply route** | `apply_candidate` | route candidates written to the real file, **default off** (`COPPER_MCP_ALLOW_APPLY=1`) |
| **Apply placement** | `apply_placement_candidate` | bounded front-side pose candidates, placement-scoped token, CAS, backup, atomic replacement, **default off** |
| Apply live (**refuses**) | `apply_live_candidate` | verifies every precondition for a one-undo-commit apply into a running KiCad and then answers `capability_not_implemented`. It is not a mutation surface; it names exactly which checks ran. |

Everything except the two file apply tools is read-only, create-new, or a declared refusal. Each
apply tool requires its own single-use token in addition to its operator gate, and
`apply_live_candidate` needs **both** `COPPER_MCP_ALLOW_LIVE_APPLY=1` and
`COPPER_MCP_ALLOW_LIVE_IPC=1`; neither is implied by `COPPER_MCP_ALLOW_APPLY`.

**Milestone state,** read from the milestone API on 2026-09-04:

| Milestone | Closed | Open | Remaining |
|---|---|---|---|
| M1 — KiCad inspection completion | 11 | 1 | [#215](https://github.com/seunghyukchoe/copper-mcp/issues/215), the closed public setup-field census; #188 (the third-party conversion wall) is closed |
| M2 — Routing depth | 6 | 1 | the milestone itself is closed; [#53](https://github.com/seunghyukchoe/copper-mcp/issues/53) remains open and operator-blocked for a contained FreeRouting comparison provider |
| M3 — Safe application completion | 2 | 2 | [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68), IPC one-undo-commit apply, and [#52](https://github.com/seunghyukchoe/copper-mcp/issues/52), placement apply (file-backed halves shipped; live halves wait on a real-editor operator gate) |
| M4 — Scene, policy, and evaluation | 4 | 0 | **complete as an accounting fact**, not as a claim that every `[~]` under it is finished |
| M5 — Verification and physics | 6 | 2 | [#90](https://github.com/seunghyukchoe/copper-mcp/issues/90), negotiated repair integration, and [#91](https://github.com/seunghyukchoe/copper-mcp/issues/91), SI/PI/thermal/DFM surrogate hooks (`dfm` sign-off reachable, SI/PI/thermal unbacked) |

M1's only open tracked issue is #215, and its acceptance is already satisfied on
`main`: the closed public setup-field census instrument, its 43-test synthetic
specification, the B-130/B-131 evidence, and the ADR-0122 follow-on decision have
all landed. It reads as close-ready pending maintainer close-out; closing it marks
M1 complete. #188 (the third-party conversion wall: `Edge.Cuts` curves plus copper
text) is closed: outline arcs shipped in `0.12.0` converting nothing new by
predeclared measurement (B-134/B-135), and copper text stays refused by decision
(ADR-0095). [The roadmap](../roadmap.md)
describes each milestone as outcomes; **GitHub is the source of truth over both**, so read it with
`gh issue list -R seunghyukchoe/copper-mcp` and
`gh api repos/seunghyukchoe/copper-mcp/milestones` rather than trusting a checkbox.

**Record ranges.** ADR-0001 … ADR-0129, next unused **0130**; five numbers (0027, 0082, 0083, 0085,
0086) are spent and never recycled. Ledgers: `D-244`, `R-188`, `SEC-174`, `B-143` are the highest
allocated (`B-109` was declined under rule 4 and is spent). Allocate in the pull request that
lands the entry, never before, per
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

- **Codex review per pull request** — reviews arrive on each pull request from the GitHub
  `chatgpt-codex-connector`, and remediation is done per PR by the lane agent that owns it:
  triage each finding against the current branch code, fix the real ones with regression tests,
  run the gate, push, and reply with Fixed/Refuted/Superseded. There is **no standing cloud
  remediation routine**; the one this section used to describe has been retired. **Known gap:**
  a review comment landing after a merge belongs to no open lane, so it needs a manual sweep.
- **Research-backed iteration** — every slice starts from a current-literature pass recorded in
  [`docs/research/`](../research/README.md) with licences and per-item implications, cited from
  that slice's ADR. The tree now holds dozens of such notes;
  [`audit-2026-08-03.md`](../research/audit-2026-08-03.md) is an independent audit of the stack as
  it stood on that date.
- **Adversarial review for destructive capability** — see §3. Repeat it for anything that writes,
  deletes, or authorizes.

---

## 5. Known limitations, stated plainly

- **Not every real board converts, and a refusal names only the *first* blocker.** B-117 measured
  the frozen private selection on 2026-08-15 at **15 of 18 saves**, the predicted 13→15 change,
  with all source hashes equal before and after. The two newly converting saves use Board IR 0.4's
  anchor plus `copper_envelope_frame: "pad_local"` envelope model. Three saves refuse at the newly
  exposed topology blockers: one disjoint `Edge.Cuts` loop set and two courtyard shapes. Copper
  text remains refused by ADR-0095 on the separate third-party corpus. **Read a refusal as
  existential, never universal**: conversion stops at the first error, so "and nothing else" cannot be inferred from
  one diagnostic — every gap closed since the survey advanced a refusal on at least one board rather
  than converting it, five times in three days. **No "converts every board" result is claimed at any
  count**, converting is not routing, placing or appliability, and no further count should be stated
  until a re-measured survey supports it. On the separate ten-board public cohort the figure is
  **0 of 10 before and after** the `0.12.0` outline-arc and stray-copper slices (B-134/B-135/B-137):
  the eight boards stopping at the outline gate now stop behind it, six at ADR-0095's copper-text
  wall. That zero-conversion outcome was predicted in writing before either adapter was touched.
- **Real-board routing is a small, candidate-only result, not a product claim — and every figure
  from this corpus has a short shelf life.** The latest routing-only measurement remains `B-107`,
  2026-08-13, over
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
  bytes**. [#170](https://github.com/seunghyukchoe/copper-mcp/issues/170) is now closed by
  ADR-0109's comparability policy, so any published count is a claim about one invocation unless
  repeated runs agree exactly. Every differential in B-107 and B-108 excludes the DRC section for
  this reason.
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
  moved both gates from 0 of 11 to 3 of 11 on the surveyed corpus. B-128 now measures the current
  production gates at a clean commit over the frozen 18-save selection: 15 convert, route patching
  is structurally appliable on **5**, and placement replay renders on **0**. The remaining cases
  are refused by design; no apply or DRC ran.
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
- **Two real-editor IPC observations now exist, and the second discharges the accept path the
  first left open.** `B-138` records a read-only observation of a running KiCad 10.0.5 (`kipy`
  0.7.1) with the CopperTone board open, taken through CopperMCP's own transport after the
  operator enabled the workstation IPC server. It is one session, one board, one build, one host
  — not an apply and not `#68`. As measured at B-138, three of the four live surfaces refused:
  `inspect_live_board` refused by default with `KicadIpcVersionError`
  because the connected KiCad (10.0.5) is newer than the installed binding's API (10.0.1);
  `inspect_live_editor_context` refused identically and passed no `allow_future_api` override at
  all; and the `probe_live_kicad_ipc` oracle returned
  `skipped`/`kicad_plugin_environment_absent` because it requires the KiCad-launched plugin
  environment. **`ADR-0129` then replaced that binding with a declared major-version window, and
  `B-142` asked the same question of the same editor, board and host**: both surfaces that refused
  B-138 answer `observed`, carrying `future_api_unverified` across a genuine `FutureVersionError`,
  and neither passes any override, because `allow_future_api` no longer exists. The same audit
  found the reverse defect — an editor a whole major *behind* the binding was being published as
  `compatible`, and is now refused. The oracle is `skipped` again at B-142, as
  `kicad_api_token_missing` rather than `kicad_plugin_environment_absent`; both report the same
  underlying fact and neither is a version result. **`R-188` is narrowed, not closed:** the accept
  path is discharged against a real KiCad 10.0.5, but the `legacy_api_unverified` direction and the
  major-boundary refusal have never met a real editor — that needs KiCad builds this host does not
  have — so both stay fake-only, exercised through a fake official-client seam that reproduces
  `check_version()`'s measured asymmetry. Neither observation is an apply, and the operator's park
  decision on `#68` stands. See also `SEC-168` for
  what enabling the server exposes, and disable it when a live session is not needed.
- **`R-033`**: the committed CopperTone board still carries mounting-hole keepout octagons
  inscribed at 2.85 mm, so edges sit 0.2169 mm inside the requirement. The generator is fixed;
  regenerating the board invalidates every recorded measurement, so it needs its own slice.
- **Unsafe-filesystem detection is best effort.** A negative means not detected, never known safe.

---

## 6. What to do next, in priority order

1. **Close [#215](https://github.com/seunghyukchoe/copper-mcp/issues/215) and mark M1 complete.**
   [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116) is closed, and so is #188:
   `Edge.Cuts` arcs shipped in `0.12.0` converting nothing new by predeclared measurement
   (B-134/B-135), and copper text stays refused by decision rather than by omission
   ([ADR-0095](../adr/0095-copper-text-has-no-derivable-envelope.md)) — the board it blocks stays
   blocked and the count is unchanged. **Expect a stack** if you take another construct: every gap
   closed since the survey advanced the refusal on at least one board instead of converting it.
   Treat a stack of blockers as the default and measure after each, never before. **Do not close
   anything on a target count**: one third-party refusal is permanent by decision, so a count-based
   completion criterion is unreachable by construction.
2. **Make the real-board routing result mean something.** 16 of 465 is a floor, not a capability,
   and 324 of those previews are `already_connected` — this corpus is mostly routed, so it can no
   longer answer the question, and the figure has now moved three times without the router changing.
   The next step is DRC evidence and cross-net compatibility on a real board with genuinely open
   nets, not a larger sweep over boards the designer has finished. DRC evidence from this
   corpus now has its policy ([#170](https://github.com/seunghyukchoe/copper-mcp/issues/170) is
   closed by ADR-0109): every published count carries its comparability, and no differential may
   cite one that is not `repeated_agreement` — KiCad's own counts do not reproduce run to run on
   identical bytes.
3. **Close placement data-fidelity and post-action gates.** Model and replay author text,
   fabrication graphics, library identity, properties, and 3D-model pose affected by a move, then
   add post-placement KiCad DRC/scene evidence, undo semantics, and live-editor CAS as separate
   bounded contracts.
4. **Generalize courtyard and side-aware geometry.** Add source-oracle fixtures for arc and
   multi-loop topology, nonzero clearance, and safe side flips before widening mutation support.
   Note that per-layer courtyards now exist ([ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md)):
   a side flip has to swap a footprint's two courtyard sets as well as mirror them, and the
   source-preserving serializer still refuses every board carrying a far-side courtyard rectangle.
5. **IPC apply ([#68](https://github.com/seunghyukchoe/copper-mcp/issues/68)) stays parked.**
   `kicad-python`'s `begin_commit` / `push_commit` gives a genuine single-undo-step transaction
   into a running KiCad. The hard part is binding an in-memory document to a file digest;
   [the research note](../research/safe-apply-references.md) lays out the constraints. Since this
   section was last written the park case has grown stronger, not weaker: B-138 observed a real
   editor and found the default MCP path refusing it on API version; ADR-0129 now binds live
   IPC to a declared major-version window with acceptances structurally distinct from proofs; and
   B-142 measured that window against the same editor, where both refusing surfaces answer
   `observed` as `future_api_unverified`, leaving `R-188` narrowed to the `legacy_api_unverified`
   direction and the major-boundary refusal. None of it is an apply.
   The mutation itself still waits on adversarial review, and the operator's park decision stands.
   The adjacent file-backed halves of M3 have both shipped.
6. **Advance #91 through its surrogate half, not its sign-off half.**
   [ADR-0128](../adr/0128-private-surrogate-ranking-is-bounded-and-never-signs-off.md) lands the
   private, direct-import-only deterministic ranking seam: fixed integer scoring under
   32-candidate and 16,384-vertex ceilings, redacted advisory output, ranking only and never
   approval. DFM sign-off remains the coordinator-owned repeated-DRC path and means "KiCad DRC
   found nothing", which is narrower than manufacturable (R-174); SI, PI and thermal have no
   adapter and no authority, so they stay unregistered non-claims.
7. **Open-baseline comparison is unmeasured, and its tracking has moved.**
   [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) is **closed**; the work is now
   carried by [#53](https://github.com/seunghyukchoe/copper-mcp/issues/53), the contained causal
   comparison provider, which is operator-blocked rather than agent-executable. The measurement
   facts are unchanged: FreeRouting is GPL-3.0 and absent from the recording environment, and every
   baseline is recorded `not_run` rather than estimated.
8. **Deferred quality items**: durable single-layer and live routing jobs, the `PlacementBackend`
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
  board_ir/, board_ir_service.py       canonical Board IR 0.4.0: footprints, geometry, validation, digests
  adapters/                            KiCad parsers and serializers, CST span splicing, ERC/parity
  routing/                             exact-integer A*, layered A*, congestion, jobs, oracle
  circuit_ir/, circuit_intent_service  Circuit Intent IR and deterministic schematic build
  circuit_scene.py, scene_render.py    typed scene observation (0.4.0) and normalized renders
  placement/, placement_preview.py     intent language, legalizer, route-aware scoring, preview
  apply/, live_apply.py                tokens, pure engines, mutating file service, live refusal
  kicad_ipc*.py, live_*.py             read-only IPC observation and live proposal surfaces
  zone_fill.py, kicad_cli.py           fill authority, bounded KiCad execution
docs/
  README.md                            documentation index; start here
  adr/                                 ADR-0001 … ADR-0129, the decision record
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
[ADR-0121](../adr/0121-a-refusal-is-an-answer-and-a-crash-is-not.md) onward, which is where the
whole `0.11.0`–`0.12.0` line lives: refusal classification and setup/footprint acceptance
(ADR-0121 … ADR-0123) shipped in `0.11.0`, and outline arcs, stray copper, and multi-pin
negotiation and repair (ADR-0124 … ADR-0127) in `0.12.0`. Authoritative signoff is earlier —
ADR-0118 and ADR-0119, shipped in `0.10.0` — and ADR-0128 and ADR-0129 are on `main` but sit under
`[Unreleased]` in the changelog, so they belong to no published version yet.

---

## 9. Public presence

External social posting is paused by maintainer instruction. Keep public project communication in
GitHub issues, pull requests, ledgers, release notes, and repository documentation. Any future
social update must be explicitly re-authorized and remain evidence-bound — real test counts, real
DRC results, and limitations stated rather than omitted.

Repository discoverability: topics cover `mcp`, `model-context-protocol`, `kicad`, `pcb`,
`pcb-automation`, `eda`, `autorouter`, `autorouting-research`, `ai-agents`, `llm-tools`,
`pcb-design`, `open-hardware`, `audio-electronics`, `python`. Releases carry attested artifacts (every release except `0.7.0`, per the release ledger) and
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
