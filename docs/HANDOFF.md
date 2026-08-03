# CopperMCP handoff — 2026-08-04

State of the project at the end of the build session that took it from a pre-alpha MVP to a
released tool that can observe, reason about, and safely modify real KiCad boards. Written for
whoever picks this up next, human or agent.

---

## 1. Where the project stands

**`main` = `14e8c4a`.** No open pull requests. Working tree clean. 850 tests plus 335 subtests
pass, including every real-KiCad node, against KiCad 10.0.5 and Python 3.12.13.

**Released:** `v0.1.0` → `v0.4.0`, all with attested wheel and sdist artifacts. `v0.4.0` is the
current release; the apply capability merged after it and is unreleased, so **the next release
should be `v0.5.0`** and its headline is `apply_candidate`.

### What an AI agent can do over MCP today

| Verb | Tool | What it is bound to |
|---|---|---|
| See (structure) | `inspect_board_ir` | counts and digests only, no geometry disclosure |
| See (semantics) | `observe_board_scene` | typed scene, stable ref ids, region-scoped, text quarantined |
| Look | `observe_board_scene` with `include_render` | normalized SVG, digest-bound, stdio only |
| Trace | `preview_route` | multi-pin trees, exact integer geometry, optional authoritative KiCad DRC evidence |
| Judge placement | `preview_placement` | seven-rule intent language, three-valued legality |
| Build | `render_circuit_schematic` | deterministic KiCad schematic from Circuit Intent IR |
| Check | `run_board_drc` | fixed-argument headless DRC, read-only |
| **Apply** | `apply_candidate` | route candidates written to the real file, **default off** |

Every one of these except `apply_candidate` is read-only or create-new. `apply_candidate` is the
only surface that modifies an existing file, and it refuses unless the operator sets
`COPPER_MCP_ALLOW_APPLY=1`.

---

## 2. The invariants that make this project what it is

These are not style preferences. Most of them were paid for with a bug. Preserve them.

1. **AI proposes, deterministic code disposes.** No model output reaches a board without being
   recomputed, replayed, and verified by code that does not consult a model.
2. **Every claim is bound to evidence or listed as an explicit non-claim.** ERC, board parity,
   electrical validation, courtyards, and fabrication readiness are all reported as `not_run` or
   `not_modelled` one-value literals rather than implied. A field that can only hold one value
   cannot be quietly upgraded.
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
- **Placement apply does not exist.** Preview and legalization only. A pose edit touches roughly
  twice as many nodes as Board IR can verify, so a serializer would be unverifiable today.
- **Courtyards are not modelled.** Board IR has no courtyard geometry; the reference board draws
  none. Recorded as a one-value literal, not a silent gap.
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

1. **Board IR 0.2 with footprint modelling.** This single dependency unblocks three deferred
   items: placement apply, courtyard legality, and placement DRC binding. It is a schema version
   bump under ADR-0005's rules — new fixtures, compatibility tests, an ADR, migration guidance —
   and it changes every zoned board's snapshot digest, so it deserves a slice of its own.
2. **A board that actually needs routing.** Either author one or adopt a real open-hardware board
   with unrouted nets, then measure and record honest coverage. This converts the routing claims
   from fixture-proven to board-proven.
3. **IPC apply (v0.2 of the apply arc).** `kicad-python`'s `begin_commit` / `push_commit` gives a
   genuine single-undo-step transaction into a running KiCad. The hard part is binding an
   in-memory document to a file digest; the research doc lays out the constraints.
4. **`v0.5.0` release** once apply has soaked, following the ledger discipline in
   `docs/ledgers/release-ledger.md`.
5. **Placement apply**, after 1.
6. **Deferred quality items**: fill-aware routing obstacles (currently connectivity only),
   durable routing jobs and candidate persistence, the `PlacementBackend` solver seam, and the
   `ordering_policy` seam for RSMT-guided topology. All are additive behind existing contracts.

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
  board_ir/                            canonical Board IR 0.1: types, validation, digests
  adapters/                            KiCad parsers and serializers, CST span splicing
  routing/                             exact-integer A*, contracts, oracle
  circuit_ir/, circuit_intent_service  Circuit Intent IR and deterministic schematic build
  circuit_scene.py, scene_render.py    typed scene observation and normalized renders
  placement/, placement_preview.py     intent language, legalizer, preview service
  apply/                               tokens, pure engine, mutating service
  zone_fill.py, kicad_cli.py           fill authority, bounded KiCad execution
docs/
  adr/                                 ADR-0001 … ADR-0025, the decision record
  architecture/                        board-ir, routing-baseline, mcp-api, security-model
  ledgers/                             decision, risk, security, release (append-only)
  research/                            literature and licensing surveys per arc
  HANDOFF.md                           this document
tests/                                 850 tests; fixtures under tests/fixtures/
```

Read in this order to get oriented: `README.md`, `AGENTS.md`, `docs/architecture/security-model.md`,
`docs/architecture/routing-baseline.md`, then the ADRs from 0016 onward for the recent arcs.

---

## 8. Public presence

Development was published as it happened, and the posts are deliberately evidence-bound — real
test counts, real DRC results, and limitations stated rather than omitted. The X account is
`@studiodawol`; the session's thread runs from the kickoff post through the v0.4.0 release and the
apply milestone, with each entry tied to a merged change.

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
