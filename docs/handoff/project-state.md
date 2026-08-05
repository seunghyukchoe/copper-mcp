# CopperMCP handoff — 2026-08-05

State of the project after the bounded placement-apply slice. This records the unreleased
engineering state for whoever picks it up next, human or agent; it is not release authorization.

---

## 1. Where the project stands

The current work is on **`codex/live-route-proposal`** as a stacked PR chain. Its exact head,
full-gate count, hosted-CI result, and PR state must be read from Git/GitHub rather than inferred
from this document.

**Released:** `v0.1.0` → `v0.4.0`, all with attested wheel and sdist artifacts. `v0.4.0` is the
current release. The route and bounded placement apply capabilities, plus the Board IR/Circuit Scene
0.2 work, are unreleased; a
future `v0.5.0` must follow the changelog, full-gate, and release-ledger process rather than treating
this handoff as approval.

### What an AI agent can do over MCP today

| Verb | Tool | What it is bound to |
|---|---|---|
| See (structure) | `inspect_board_ir` | counts and digests only, no geometry disclosure |
| See (semantics) | `observe_board_scene` | Circuit Scene 0.2, footprint pose/ownership/courtyards, stable ref ids, region-scoped, text quarantined |
| Look | `observe_board_scene` with `include_render` | normalized SVG, digest-bound, stdio only |
| Trace | `preview_route` | multi-pin trees, exact integer geometry, optional authoritative KiCad DRC evidence |
| Judge placement | `preview_placement` | Board IR-projected subjects, locked-move refusal, seven-rule intent language, three-valued legality |
| Build | `render_circuit_schematic` | deterministic KiCad schematic from Circuit Intent IR |
| Check | `run_board_drc` | fixed-argument headless DRC, read-only |
| **Apply route** | `apply_candidate` | route candidates written to the real file, **default off** |
| **Apply placement** | `apply_placement_candidate` | bounded front-side pose candidates with placement-scoped token, CAS, backup, and atomic replacement, **default off** |

Every one of these except the two apply tools is read-only or create-new. Both apply tools refuse
unless the operator sets `COPPER_MCP_ALLOW_APPLY=1`, and each requires its own single-use token.

---

## 2. The invariants that make this project what it is

These are not style preferences. Most of them were paid for with a bug. Preserve them.

1. **AI proposes, deterministic code disposes.** No model output reaches a board without being
   recomputed, replayed, and verified by code that does not consult a model.
2. **Every claim is bound to evidence or listed as an explicit non-claim.** ERC, board parity,
   electrical validation, courtyard legality, and fabrication readiness are all reported as
   `not_run` or `not_modelled` one-value literals rather than implied. Board IR carrying a supported
   courtyard contour is not evidence that an overlap evaluator ran. A field that can only hold one
   value cannot be quietly upgraded.
3. **Direction of error is a design decision, stated everywhere.** Obstacles over-approximate;
   connectivity proofs under-approximate. A wrong answer must cost a redundant route, never a
   false connection. Legality verdicts are three-valued because `inconclusive` is honest and
   `proven_clear` would not be.
4. **Fail closed, with typed non-echoing diagnostics.** Refusals never echo caller input and never
   guess. `infeasible_constraints` (a proof) and `budget_exhausted` (an admission of ignorance)
   must never collapse into each other.
5. **Bounded everything.** Lattice nodes, expansions, obstacle work, vertices, objects,
   annotations, file sizes, deadlines. New work charges an existing budget or declares its own.
6. **Exact integers.** Nanometres and micro-degrees, rational comparisons, no floating point in
   any geometric predicate.
7. **Append-only ledgers.** Decision, risk, security, release. Corrections are dated notes below
   the table; rows are never rewritten. `scripts/check_ledgers.py` enforces the shape.
8. **Release authorization is a separate, deliberate act.** A `Ready` row naming a validated source
   commit, then a metadata commit touching only the ledger and changelog, then the tag.
9. **The board's own text is untrusted data.** Silkscreen, fab text, properties, and net names are
   quarantined structurally, never interpolated into instruction-bearing fields.

---

## 3. Standing systems left running

- **Codex review remediation routine** — a cloud agent (`trig_01WkyDsdY8wmEfu1Pm2WwtfP`, every two
  hours, Opus 5, no connectors) polls open PRs for unaddressed `chatgpt-codex-connector[bot]`
  comments, triages against current branch code, fixes real findings with regression tests, runs
  the non-KiCad gate, pushes, and replies with Fixed/Refuted/Superseded. Manage or disable it at
  <https://claude.ai/code/routines/trig_01WkyDsdY8wmEfu1Pm2WwtfP>. It cannot claim KiCad-verified
  results — the cloud sandbox has no KiCad, and its prompt says so. **Known gap:** it only sees
  open PRs, so comments landing after a merge need a manual sweep.
- **Research-backed iteration** — every slice starts from a current-literature pass recorded in
  `docs/research/` with licences and per-item implications, cited from that slice's ADR. Six such
  documents exist; `audit-2026-08-03.md` is an independent audit of the shipped stack.
- **Adversarial review for destructive capability** — the apply PR got a dedicated attack pass on
  top of the normal gate. It found eleven real defects. Repeat this for anything that writes,
  deletes, or authorizes.

---

## 4. Known limitations, stated plainly

- **Nothing has been routed on a real board by us.** All fourteen CopperTone nets were already
  routed by its designer, and the router correctly recognizes that. Routing is proven on
  purpose-built fixtures with real KiCad DRC, not yet on a board that genuinely needed new copper.
  Finding or building that board is the missing empirical validation.
- **Placement apply is deliberately narrow.** The file-backed service now applies only
  replay-verified front-side, orthogonal, native-identity footprints with supported rectangular
  `F.CrtYd` syntax. Author text, fabrication graphics, library identity, properties, 3D-model pose,
  side flips, post-apply DRC/scene evidence, undo transactions, and live IPC mutation remain
  outside the gate.
- **Courtyard coverage is deliberately narrow.** The adapter observes bounded front/back
  orthogonal footprints with matching unfilled `fp_rect`, orthogonal `fp_poly`, or closed
  orthogonal `fp_line` courtyard layers, while the placement serializer and apply path remain
  front-side-only and rectangle-only. Arcs, curves, diagonals, filled, open, branching, mixed, or
  mismatched courtyard topology fails closed. Same-side simple-orthogonal overlap is evaluated
  exactly; configurable clearance and general topology remain open.
- **Apply gives a pre-apply copy, not a KiCad undo step.** Restoring is manual. IPC-based
  one-undo-commit apply is designed and deferred.
- **Renders are whole-board even for a windowed scene**, and are advisory, never geometric
  authority.
- **`R-033`**: the committed CopperTone board still carries mounting-hole keepout octagons
  inscribed at 2.85 mm, so edges sit 0.2169 mm inside the requirement. The generator is fixed;
  regenerating the board invalidates every recorded measurement, so it needs its own slice.
- **Unsafe-filesystem detection is best effort.** A negative means not detected, never known safe.

---

## 5. What to do next, in priority order

1. **Close placement data-fidelity and post-action gates.** Model and replay author text,
   fabrication graphics, library identity, properties, and 3D-model pose affected by a move, then
   add post-placement KiCad DRC/scene evidence, undo semantics, and live-editor CAS as separate
   bounded contracts.
2. **Generalize courtyard and side-aware geometry.** Add source-oracle fixtures for line/polygon/
   arc and multi-loop topology, nonzero clearance, and safe side flips before widening mutation
   support.
3. **A board that actually needs routing.** Either author one or adopt a real open-hardware board
   with unrouted nets, then measure and record honest coverage. This converts the routing claims
   from fixture-proven to board-proven.
4. **IPC apply (v0.2 of the apply arc).** `kicad-python`'s `begin_commit` / `push_commit` gives a
   genuine single-undo-step transaction into a running KiCad. The hard part is binding an
   in-memory document to a file digest; the research doc lays out the constraints.
5. **`v0.5.0` release** once the unreleased surfaces have soaked, following the ledger discipline in
   `docs/ledgers/release-ledger.md`.
6. **Deferred quality items**: durable routing jobs and candidate persistence, negotiated
   congestion/rip-up, the `PlacementBackend` solver seam, and the `ordering_policy` seam for
   RSMT-guided topology. Fill-aware routing obstacles and their opt-in MCP provenance are now
   complete for the bounded single-layer contract; all remaining items are additive behind
   existing contracts.

`R-033` (board regeneration) and issues #8 and #11 remain open on GitHub.

---

## 6. Operational knowledge worth keeping

**Environment.** Use `.venv/bin/python`; system `python` does not exist. Tests need
`PYTHONPATH=src`. `make check` is the full gate: ruff lint and format, the four checker scripts,
mypy, pytest, secret scan, pip-audit, isolated build. KiCad 10.0.5 lives at
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
- Mypy floats to the newest version in CI. The floor is now pinned at 2.3 to match, which ended a
  period of version-skew failures that passed locally and failed hosted.

**KiCad behaviours discovered the hard way** (each is load-bearing somewhere in the code):
- Pad `(at x y angle)` angles are **absolute in the board frame**; adding the footprint rotation
  double-counts and transposes non-square pads. Pinned by an SVG-oracle test.
- KiCad stores boards y-down while its angles read counter-clockwise on screen, so a quarter turn
  is `(x, y) → (y, −x)`.
- Back-side footprint-local coordinates in a saved KiCad board are already flipped. Do not add a
  second mirror when extending the adapter; require source-oracle fixtures before accepting them.
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

**Publishing to X** (account `@studiodawol`): the reliable path is `https://x.com/intent/post`
with pre-filled `text` and optional `in_reply_to`, then a single click on Post or Reply. The
in-page composer loses focus unpredictably. To attach an image, put it on the clipboard with
AppleScript (`set the clipboard to (read (POSIX file "…") as «class PNGf»)`) and paste with
`cmd+v` — the extension's file upload rejects paths outside the session share.

---

## 7. Map of the codebase

```
src/copper_mcp/
  mcp_server.py, tools.py, cli.py      surfaces (MCP tools/resources, CLI commands)
  mcp_contracts.py                     closed Pydantic contracts for tool responses
  request_boundary.py, security.py     untrusted input validation; workspace confinement,
                                       descriptor-anchored reads, create-only and replace writes
  board_ir/                            canonical Board IR 0.2: footprints, geometry, validation, digests
  adapters/                            KiCad parsers and serializers, CST span splicing
  routing/                             exact-integer A*, contracts, oracle
  circuit_ir/, circuit_intent_service  Circuit Intent IR and deterministic schematic build
  circuit_scene.py, scene_render.py    typed scene observation and normalized renders
  placement/, placement_preview.py     intent language, legalizer, preview service
  apply/                               tokens, pure engine, mutating service
  zone_fill.py, kicad_cli.py           fill authority, bounded KiCad execution
docs/
  README.md                            documentation index; start here
  adr/                                 ADR-0001 … ADR-0065, the decision record
  architecture/                        overview, board-ir, circuit-intent, routing-baseline,
                                       mcp-api, security-model
  ledgers/                             decision, risk, security, benchmark, release (append-only)
  research/                            literature and licensing surveys per arc
  handoff/                             project-state.md (this document), codex-onboarding.md
tests/                                 regression and integration fixtures under tests/fixtures/
```

Read in this order to get oriented: `README.md`, `AGENTS.md`, `docs/architecture/security-model.md`,
`docs/architecture/board-ir.md`, `docs/architecture/routing-baseline.md`, then the ADRs from 0016
through 0026 for the recent arcs.

---

## 8. Public presence

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

## 9. Licensing boundaries

CopperMCP is Apache-2.0. **freerouting is GPL-3.0** — concepts from the literature only, never
code. **GeoSteiner and FLUTE** carry non-commercial encumbrances and **REST** uses CUHK's
non-OSI CU-SD licence; none may become dependencies. **TritonRoute, InstantGR, `kicad-python`, and
OmniParser** are BSD-3 or MIT and are legitimate references. `docs/research/` records the full
survey with per-item verdicts.
