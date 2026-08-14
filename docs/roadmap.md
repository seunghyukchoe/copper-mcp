# Roadmap

Roadmap items describe outcomes, not promises about dates. Each milestone requires tests,
documentation, ledger updates, and benchmark evidence.

## Milestone state

**The GitHub milestones are the source of truth, not the checkboxes below.** A checkbox records
engineering sub-state and is written by hand; a milestone's issue counts are derived. Where the two
disagree, believe this table and file the discrepancy. Read live with
`gh issue list -R seunghyukchoe/copper-mcp` and
`gh api repos/seunghyukchoe/copper-mcp/milestones`.

As of 2026-08-14, after the dispositions recorded in
[the post-0.8.0 audit](audit/2026-08-14-post-0.8.0-audit.md):

| Milestone | Closed | Open | State |
|---|---|---|---|
| M1 — KiCad inspection completion | 7 | 2 | Open: [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116), the conversion tracker retitled from the original real-board survey ([D-191](ledgers/decision-ledger.md)), and [#172](https://github.com/seunghyukchoe/copper-mcp/issues/172), the schema-versioning decision that now gates the custom-pad conversion arc. |
| M2 — Routing depth | 4 | 3 | Open: [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) benchmark comparison against open baselines, [#164](https://github.com/seunghyukchoe/copper-mcp/issues/164) the layered fill-authority contract, and [#53](https://github.com/seunghyukchoe/copper-mcp/issues/53) parked behind an operator gate. [#63](https://github.com/seunghyukchoe/copper-mcp/issues/63) closed as already delivered ([ADR-0101](adr/0101-fill-currency-is-not-in-the-document.md)). |
| M3 — Safe application completion | 0 | 3 | Open: [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68) IPC one-undo-commit apply, [#170](https://github.com/seunghyukchoe/copper-mcp/issues/170) DRC reproducibility, and [#52](https://github.com/seunghyukchoe/copper-mcp/issues/52) placement apply, whose file-backed half has shipped. **Entry criteria, not a start date** — see [M3](#m3--safe-candidate-application). |
| M4 — Scene, policy, and evaluation | 3 | 1 | **No longer at zero open.** [#110](https://github.com/seunghyukchoe/copper-mcp/issues/110) — give the excessive-agency evaluation a reachable externally authored project family — was filed unmilestoned and belongs here ([R-148](ledgers/risk-register.md)). |
| M5 — Verification and physics | 2 | 5 | Renamed from "Performance and physics". Open: [#87](https://github.com/seunghyukchoe/copper-mcp/issues/87) re-scoped to profiling, [#90](https://github.com/seunghyukchoe/copper-mcp/issues/90) and [#91](https://github.com/seunghyukchoe/copper-mcp/issues/91) parked behind [#99](https://github.com/seunghyukchoe/copper-mcp/issues/99), which is now the milestone's centre, and [#167](https://github.com/seunghyukchoe/copper-mcp/issues/167). [#88](https://github.com/seunghyukchoe/copper-mcp/issues/88) and [#89](https://github.com/seunghyukchoe/copper-mcp/issues/89) are closed by their own profiling gates. |
| Audio Board Lab #001 — Physical validation | 0 | 1 | Open: [#8](https://github.com/seunghyukchoe/copper-mcp/issues/8). See [the Audio Board Lab gate](#audio-board-lab-001--physical-validation) — as written the issue would validate the board, and the thing that needs validating is the tool. |

Three cautions about this table, because the first two have misled a reader before:

- **A milestone's open count is an accounting fact about the tracker, not a statement about the
  section below it.** M4 stood at zero open until #110 was filed into it on 2026-08-14, and nothing
  about the engineering changed that day. Several M4 `[~]` items genuinely remain — broader source
  fidelity, editor authority, solving for a placement, and the policy-plugin work — and they are
  untracked rather than done.
- Work filed outside a milestone appears in none of the counts above, and the M1 conversion arc was
  the standing example. That gap is now mostly closed: of the gaps filed after the M1 survey,
  [#152](https://github.com/seunghyukchoe/copper-mcp/issues/152) and
  [#153](https://github.com/seunghyukchoe/copper-mcp/issues/153) closed
  ([ADR-0099](adr/0099-pad-fabrication-properties-and-named-pad-refusals.md),
  [ADR-0100](adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md)),
  [#141](https://github.com/seunghyukchoe/copper-mcp/issues/141) closed with its decision label, and
  #164, #167, #170 and #172 all now carry milestones. See
  [what does not convert](#what-does-not-convert).
- **One issue is deliberately unmilestoned:**
  [#166](https://github.com/seunghyukchoe/copper-mcp/issues/166), the single-layer `verified_fill`
  seam's missing shape validation. It needs a decision before it needs a milestone, and the decision
  may be to close it. Containment is present on both paths, so the dangerous direction is already
  gated; what is missing is type and size validation, whose failure mode is a loud exception rather
  than wrong copper.

## M0 — Repository foundation (`0.1.x`, complete)

- [x] Secure workspace and file boundary.
- [x] Content-addressed board manifests.
- [x] Candidate schemas and comparison.
- [x] MCP and CLI adapters over shared services.
- [x] Governance, security, CI, releases, and ledgers.
- [x] Reproducible KiCad audio-board preview and artifact-validation workflow.
- [x] Licence-aware, network-free audio capability corpus with original/open fixtures and
  reference-only external source metadata.
- [x] First public GitHub release.

## M1 — KiCad inspection and validation (current)

### What does not convert

The one open M1 issue is [#116](https://github.com/seunghyukchoe/copper-mcp/issues/116), the
real-board conversion tracker. Re-measured on the private working corpus on 2026-08-13 (`B-107`,
reproducing `B-103`): **13 of the 18 saves in that corpus convert.** Those 18 files hold **17
distinct board contents** — one pair is byte-identical across two save directories, and that pair is
not among the boards that moved — so the same result is 13 of 17 distinct boards. Five saves refuse,
each for exactly one named construct:

| Refusing saves | Construct | Issue |
|---|---|---|
| 4 | a custom-shape SMD pad — an envelope for it *is* derivable from the document, but a Board IR `Pad` is read over-approximating for its obstacle and under-approximating for its attachment core from the same three fields, and no single rectangle can be both ([ADR-0100](adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md)) | [#153](https://github.com/seunghyukchoe/copper-mcp/issues/153), closed |
| 1 | a root copper graphic (`gr_text` on `F.Cu`) — **decided and staying refused**, see [ADR-0095](adr/0095-copper-text-has-no-derivable-envelope.md) | [#141](https://github.com/seunghyukchoe/copper-mcp/issues/141), closed |

The count of refusing saves is measured; **the split between the two constructs is composed from two
runs and not from one.** ADR-0100 measured three saves refusing for the custom pad, and ADR-0099
then converted one of the two pad-`property` saves and advanced the other onto the same custom pad
([#152](https://github.com/seunghyukchoe/copper-mcp/issues/152) and
[#159](https://github.com/seunghyukchoe/copper-mcp/issues/159), both closed). No single run has
measured the corpus with both landed and broken the five down by construct.

All five gaps #116 originally named are closed: chamfered and circular courtyards
([ADR-0080](adr/0080-chamfered-and-circular-courtyards.md)) closed two of them, roundrect radius
rounding ([ADR-0077](adr/0077-roundrect-corner-radius-rounding.md)) a third, net-0 copper as an
obstacle ([ADR-0078](adr/0078-netless-copper-as-obstacle.md)) a fourth, and reused KiCad UUIDs
([D-158](ledgers/decision-ledger.md)) the fifth.

**Neither refusal is permanent, and neither is open work.** #141 closed on 2026-08-14 carrying the
label the record supports — *decided by ADR-0095; five exit conditions written, none met, none in
progress* — with the five conditions enumerated in the closing comment so that reopening is
mechanical rather than a re-reading of the ADR. #153 closed the same way against ADR-0100. Because
both are closed by decision, **no open issue owns reopening either**, and that is why each closure
carries its exit conditions rather than only its verdict.

**No "converts every board" result is claimed at any count**, and none should be stated until a
re-measured survey supports it — nor is a target count a completion criterion, since both remaining
refusals are answered by decision rather than by missing work. The counts above supersede earlier
survey figures — including
#116's own original title, whose every number was wrong by the time it was read — rather than
correcting them in place. Dated research notes and benchmark-ledger rows keep whatever they measured
on the day they measured it. The corpus is a **live tree the designer edits during long runs**, so
any count from it is only as good as the digest sweep bracketing the run that produced it.

- [x] Official `kicad-python` IPC plugin and redacted live-board observer. The optional
  `inspect_live_board` MCP tool and `hardware/kicad-ipc-plugin` action use only local KiCad IPC,
  refuse future binding versions by default, and return a digest plus bounded counts without
  board text, net names, UUIDs, or geometry. Live editor-to-Circuit-Scene binding remains a
  separate item.
- [x] Canonical Circuit Intent IR `0.1.0` and deterministic in-memory KiCad schematic generation
  contract for a bounded two-pin passive subset.
- [x] Bounded Circuit Intent build service, explicit create-new CLI schematic export, and
  stdio-only opaque MCP resource delivery with redacted verification metadata.
- [x] Deterministic passive-layout readability baseline with wider grid placement, extended leads,
  separated labels/properties, real KiCad SVG inspection, and a structural regression.
- [x] Descriptor-anchored workspace reads and exact-lowercase create-only schematic output.
- [x] Schematic round trip, authoritative ERC, and source-to-board connectivity parity
  (issue #66, closed). The
  bounded passive subset now has exact deterministic schematic replay, authoritative `kicad-cli sch
  erc` evidence bound to the generated schematic digest, and a live KiCad round trip that exports a
  `kicadxml` netlist and checks recovered components and nets against the source intent through the
  reusable `kicad_schematic_parity` verifier. Source-to-board connectivity parity now ships as
  `verify_source_to_board_parity`, running the authoritative `kicad-cli pcb drc --schematic-parity`
  against a **board-eligible projection** of the intent under its own digest, and refusing a
  verdict outright unless KiCad demonstrably accounted for every component — an empty parity array
  is also what a check that never ran produces. A `passed` verdict is deliberately **not** a claim
  that the delivered schematic file matches the board: that file marks every symbol `on_board no`
  and never enters KiCad's board-side netlist. Broader symbol/library coverage remains open, and
  parity is not ERC, footprint correctness, electrical validation, or board readiness.
- [~] Live KiCad IPC snapshot to Circuit Scene and route-proposal binding. `observe_live_board_scene`
  converts the exact captured IPC serialization through Board IR, and `preview_live_route` now
  returns a deterministic read-only candidate from a scene `net_ref_id` with both stale-session
  digests. `preview_live_placement` now reuses the same exact snapshot → Board IR → legalizer
  path for a ref-anchored, read-only placement candidate. `preview_live_layered_route` now adds a
  session-token, source-digest, and Board IR-digest-bound via-capable proposal with fake-IPC
  replay evidence. A local fidelity oracle now exercises source → Board IR → Circuit Scene digest
  binding, redacted capability outcomes, and one cooperative deadline through a fake official-client
  seam; the ordinary shell receives the canonical plugin-credential-absent skip. No successful
  real-editor oracle run has been recorded, and live action compare-and-swap before placement or
  routing remains open because the workstation IPC server is disabled.
- [x] Canonical Board IR v0.2 contract with integer units, typed constraints, strict codecs,
  content digests, first-class footprint pose/side/lock/pad ownership, and bounded simple closed
  courtyard shapes: unfilled `fp_rect`, `fp_poly`, and unordered complete `fp_line` cycles whose
  every edge is horizontal, vertical, or an exact 45-degree chamfer, plus unfilled `fp_circle`
  outlines of exact integer radius (ADR-0080). The immutable v0.1 schema remains as legacy
  compatibility evidence.
- [x] Board IR application-service and MCP exposure as a read-only structural summary.
- [x] Broader KiCad geometry and rule coverage (issue #67, closed). Foreign-net arc tracks spanning
  at most half a turn are a conservative integer polygon envelope obstacle on the single-layer
  router, with distinct typed refusals for an arc past half a turn and for an arc on the routed
  net. Since the issue was written the adapter has additionally accepted oval pad drills,
  45-degree-chamfered and exact-integer-radius circular courtyards (ADR-0080), copper carrying no
  routable net as a netless obstacle (ADR-0078), net-tie `fp_poly` copper as netless obstacle
  copper (ADR-0092), unlocked root groups (ADR-0090), attaching pad `zone_connect` overrides
  (ADR-0091), root board `property` text variables (ADR-0094), `connect`-kind edge-connector pads as
  SMD (ADR-0096), courtyards on the layer opposite a footprint's side (ADR-0097), and seven of
  KiCad's eight `PAD_PROP` pad fabrication tokens (ADR-0099). **Still refused:** curved board
  outlines (`Edge.Cuts` arcs, circles, polygons, béziers), pad shapes outside
  `circle`/`rect`/`oval`/`roundrect` — `custom` and `trapezoid` each under their own sentence
  (ADR-0100) — custom pad primitives, `pad_prop_castellated`, placement-enabled rule areas,
  `fp_arc` courtyards, outline holes, and blind/buried/microvias. The residual real-board
  conversion gaps are counted in [what does not convert](#what-does-not-convert), and the
  authoritative accepted/rejected matrix is
  [the Board IR contract](architecture/board-ir.md#kicad-read-only-subset), not this list.
- [x] Copper stack validated against KiCad's own layer numbering rather than a synthesized
  arithmetic rule, correcting a defect that had refused every real board with more than two copper
  layers behind the same fail-closed diagnostic a two-layer board also uses. Declaration position
  fixes the layer name; the name fixes KiCad's own declared ID (`F.Cu=0`, `B.Cu=2`,
  `In{N}.Cu=2+2N`), so a four-layer board's IDs deliberately do not ascend. Board IR's own
  `Layer.index` and every two-layer content address are unchanged.
- [x] Board outlines assembled from `Edge.Cuts` `gr_line` segments. An outline may now be one
  unfilled `gr_rect` **or** `gr_line` segments that chain, by exact endpoint coincidence, into one
  closed simple loop, taking a composite native identity from its members' UUIDs (ADR-0076,
  ADR-0087, issue #111). The outline is routing room, so it is never repaired into something
  larger than what was drawn: an open contour, a near-miss gap, a spur, a duplicate or zero-length
  segment, a self-intersection, or two disjoint loops each still refuse.
- [x] Operator-configurable parse budgets. Six structural budgets — `max_tokens`, `max_nodes`,
  `max_children_per_list`, `max_objects`, `max_total_vertices`, `max_intersection_tests` — are now
  taken as configured through the matching `COPPER_MCP_MAX_PARSE_*` variables and one
  `parse_limits_for()` seam, so a budget moves for every board-reading service at once or for none
  (ADR-0079, issue #112). Previously thirteen call sites hardcoded them and only the byte ceiling
  moved. `max_input_bytes` deliberately keeps `min` semantics.
- [x] Headless `kicad-cli pcb drc --format json` validation.
- [x] Minimal KiCad child environment, private working directory, bounded private
  global-configuration/state roots, and snapshot-confined file-table dependencies.
- [x] Candidate preview without mutation.
- [x] Version-skew and stale-board tests for the DRC adapter.
- [ ] **Decide how a published schema changes** ([#172](https://github.com/seunghyukchoe/copper-mcp/issues/172)).
  This is the gate on M1's one remaining conversion arc, not hygiene:
  [ADR-0100](adr/0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md) makes the custom-pad
  envelope a `Pad` **type** fact, a type fact needs a schema bump, and a schema bump is exactly the
  decision #172 owns. #149 already measured what a bump costs — envelope rather than payload, about
  five tests and one codec call site. The post-0.8.0 audit swept every schema at every released tag
  and found the practice is **four instances across three releases in two directions**, not one:
  `audio-benchmark-catalog` at `v0.3.0` (a *required* key added — a narrowing), `board-ir` `0.2.0`
  at `v0.7.0` (courtyard circles; `net_id` widened to accept `null`), `drc-summary` at `v0.7.0`
  (a *required* key added — a narrowing), and `board-ir` `0.2.0` again at `v0.8.0` (the far-side
  courtyard keys, #172's own instance). So a `0.2.0` document could have been produced under three
  different accepted sets spanning `v0.5.0`–`v0.8.0`, and because the `footprint` definition carries
  `additionalProperties: false`, a document produced under the newest is *rejected* by the oldest.
  The decision is to bump to `0.3.0`, freeze `0.2.0` as published, correct nothing retroactively,
  and say in the migration note that `0.2.0`-as-published spans three accepted sets — and it must
  cover `drc-summary` and `audio-benchmark-catalog` too, since a Board IR-only decision fixes one of
  three affected files. Two of the four instances break in the **narrowing** direction that #172's
  own text does not discuss, so the drift gate that follows the decision has to fire both ways.
- [ ] **Disclose the conversion counters an MCP client cannot see.** `ConversionResult` carries five
  measured counts — roundrect rounding, unmodelled groups, edge-connector pads, unmodelled board
  properties, unmodelled pad properties — and `BoardIrSummary` carries none of them. Four of the
  five are the *disclosure* that four risk rows depend on
  ([R-134](ledgers/risk-register.md), R-139, R-141 and R-144 each record that the conversion loses a
  token and that the count is how a caller finds out), so a count that never reaches a client makes
  those mitigations partial. The direction of error is under-disclosure. The fix is one
  `unmodelled_counts` map on `BoardIrSummary` rather than five fields, gated by a contract test
  asserting that every measured field on `ConversionResult` appears in it — so a sixth counter
  becomes an entry rather than a contract change, which is precisely how this grew from three to
  five without anyone noticing.

## M2 — Deterministic routing baseline

Tracker state: 4 closed, 3 open — [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65)
benchmark comparison against open baselines,
[#164](https://github.com/seunghyukchoe/copper-mcp/issues/164) the layered fill-authority contract,
and [#53](https://github.com/seunghyukchoe/copper-mcp/issues/53) parked.
[#63](https://github.com/seunghyukchoe/copper-mcp/issues/63) closed on 2026-08-13 as **already
delivered** by ADR-0039, ADR-0040 and ADR-0070 rather than by new capability: fill currency is not
recoverable from the board document, so the model stays as it is and the single-layer core gained
the two fail-closed shape gates the layered adapter already had
([ADR-0101](adr/0101-fill-currency-is-not-in-the-document.md)).

### What closes M2

Four conditions, replacing the earlier reading that M2 was "#65 only".

1. **No capability the public surface cannot speak about.** The layered seam is the standing
   example and [#164](https://github.com/seunghyukchoe/copper-mcp/issues/164) is the item.
2. **A cross-router comparison artifact carrying typed results**, including `not_run` with a
   reason. A typed `not_run` is a result; a missing row is not. `B-069` and `B-088` already record
   FreeRouting as `not_run`, so what is owed is the artifact shape, not a causal comparison.
   **Delivered** by [`B-113`](ledgers/benchmark-ledger.md) (`D-204`): a declared roster of every
   router baseline the licensing determination names, each row typed, each `not_run` carrying the
   licence or environment fact behind it and the checkable conditions that would change it, beside
   CopperMCP's measured row. The artifact records `measured_rows: 1` and
   `comparison_supported: false`, because one measured row supports no comparison.
3. **The closing measurement taken on a frozen, redistributable corpus** rather than on the
   designer's live tree. [R-146](ledgers/risk-register.md) is why: the corpus grows and is edited
   during long runs, so every figure taken from it decays silently.
   **Satisfied for the routing measurement** by the committed MIT SimpleRouteJson corpus, which is
   frozen in the sense this condition requires — digest-verified before every run, under a subset
   rule fixed in advance — and is what `B-113` measures on. It is not an externally authored KiCad
   family, so it does not discharge [#110](https://github.com/seunghyukchoe/copper-mcp/issues/110);
   [`B-114`](ledgers/benchmark-ledger.md) measures why that one is still open, and `R-157` records
   that the blocker there is geometry coverage rather than licensing or the board format era.
4. **No routing sentence claiming more than the 16-of-465 discipline supports** — `B-107`'s 465
   route verdicts, of which 16 `routed` and 324 `already_connected`.

**M2 can close without [#53](https://github.com/seunghyukchoe/copper-mcp/issues/53)**, whose own
text says it does not authorize the operator's container-runtime installation, and **without
[#167](https://github.com/seunghyukchoe/copper-mcp/issues/167)**: the corpus runner behind `B-107`
never sets `include_fill_authority`, and the public `preview_layered_route` seam never supplies
`verified_fill` at all, so the per-island ceiling never enters M2's closing measurement.

**#164 carries an ordering constraint.** The layered candidate records no fill binding, so
[ADR-0103](adr/0103-a-candidate-records-the-model-that-produced-it.md)'s replay invariant — a
candidate records the model that produced it and refuses a replay under any other — is unenforced on
that path. One of ADR-0103's two named triggers has already fired: the vertex-budget raise
([ADR-0104](adr/0104-fill-vertex-budget-behind-a-parse.md)) landed in the same release. So the
binding must land **with or before** any public `include_fill_authority` on the layered seam;
shipping the flag first would make the divergence reachable through the public surface. No
route-quality claim attaches to the work — `B-105` measured zero changed verdicts.

- [~] Single two-pin A* routing with exact connectivity.
  - [x] Candidate-only integer four-neighbour reference with exact revision binding, deterministic
    identity, rectangular keepouts, bounded search, and cancellation.
  - [x] Reproducible synthetic A*/Dijkstra completion and optimal-cost oracle baseline.
  - [x] Replay-bound, read-only disposable KiCad patch serialization with Board IR round-trip checks.
  - [x] Internal candidate-bound authoritative KiCad DRC evidence over a private disposable board.
  - [x] Bounded, non-mutating public route preview over MCP and the CLI, with opt-in authoritative
    candidate DRC evidence.
  - [~] Durable routing jobs, candidate persistence, and export.
    - [x] Transport-independent redacted job records with revision CAS, SQLite reopen, bounded
      TTL/capacity, idempotent creation, and cooperative cancellation.
    - [~] Worker execution/leases, candidate persistence, durable export, and ordinary MCP
      start/get/cancel tools.
      - [x] Single-worker CAS leases, cooperative cancellation, stale-lease recovery, and
        fail-closed invalid-candidate publication are covered by ADR-0046 and B-028.
      - [x] Bounded redacted candidate-manifest persistence with restart, TTL, tamper, and
        RoutingJobSpec binding checks is covered by ADR-0047 and B-029.
      - [x] Bounded file-backed layered request/result persistence, authorization-bound route-
        geometry export, and ordinary MCP `start_routing`/`get_routing_job`/`cancel_routing_job`
        tools. Single-layer/live jobs and MCP Tasks negotiation remain open.
- [x] Existing selected-layer copper as exact rectangular obstacles.
- [x] Via obstacles on the selected layer, the first limit a real board hit.
- [x] Conservative polygon zone-boundary envelope obstacles with exact integer concave/diagonal
  geometry and vertex-level work accounting.
- [x] Non-rectangular selected-layer track keepouts, including the octagonal mounting-hole rule
  areas KiCad emits, under that same envelope model.
- [x] Diagonal foreign-net copper as conservative exact-integer swept-square envelopes.
- [x] Diagonal copper on the routed net as attachment copper, using a chain of exact integer squares
  that is provably inside the track and provably self-connected.
- [x] Foreign-net KiCad arc tracks spanning at most half a turn as a conservative integer polygon
  envelope obstacle on the single-layer router, using the same swept-square construction as
  diagonal segments with a larger radius (ADR-0072). An arc past half a turn and an arc on the
  routed net are each a distinct typed refusal; the layered proposal adapter's blanket arc refusal
  is unchanged, since its obstacle model is rectangles only.
- [x] Freshness-bound zone fill authority: cached fill is admitted as connectivity evidence only
  when a fresh KiCad refill on a disposable copy reproduces it exactly.
- [x] Fill-aware zone *routing*: the deterministic A* core replaces a matching foreign-zone
  envelope with freshness-verified exact fill islands, fails closed on stale/unmatched evidence,
  and the public preview advertises the evidence on routed candidates with a typed
  `routing_effect`; B-021 measures the route-quality improvement and B-022 measures the MCP
  provenance contract. Issue #63 closed against this item on 2026-08-13: the shrink is gated rather
  than widened (ADR-0101), a candidate now records the fill that produced it and refuses a replay
  under any other model (`fill_evidence_mismatch`, ADR-0103), and `preview_route` withholds the
  apply token for a fill-shaped candidate — so a fill-routed candidate is previewable and
  DRC-checkable but **not appliable**. `max_fill_vertices` was recalibrated 50,000 → 500,000 from a
  measured density rather than from one board's pour (ADR-0104); the per-island ceiling behind it is
  [#167](https://github.com/seunghyukchoe/copper-mcp/issues/167), open.
- [~] Multi-pin nets, since most real nets have more than two pads.
  - [x] Connectivity analysis for nets of any width, so an already-connected multi-pin net
    is recognised rather than refused.
  - [x] Routing a multi-pin net as a deterministic spanning tree over its components
    (`component-mst-v1`); Steiner-optimal topology is explicitly not claimed.
  - [x] Bounded one-Steiner-quality topology ordering behind the recorded `ordering_policy` seam
    (`batched-1-steiner-v1` for at most nine evolving components, with a measured 12.5% wire-
    length reduction on the four-pad fixture). This is not a FLUTE implementation or optimality
    claim; higher-degree decomposition and learned policies remain future work.
  - [x] Original audio multi-pin service benchmark: an Apache-2.0, zero-copper NE5532-class
    topology has 14 synthetic footprints, 35 pads, 11 nets, and eight independently replayed
    public-service candidates (four two-pad and four three-/four-pad). B-074 pins their identities
    and route metrics; optional KiCad 10.0.5 DRC runs only on independent disposable derivatives,
    reducing the selected net's unconnected count but remaining non-clean and non-combined.
- [x] Via-aware connectivity, so a net joined through another layer is recognised rather than
  refused. On CopperTone this resolved `VCC` and `L_OUT`; `GND` remains refused because it carries a
  same-net zone, whose fill is not trusted.
- [ ] Routing *through* vias, which needs a layer-aware lattice, a via-insertion cost, and a
  via-placement contract. Connectivity is multilayer today; path search is not.
  - [x] First algorithmic acceleration milestone: bounded two-signal-layer `(x, y, layer)` A*
    with explicit through-via transitions, positive via cost, per-layer obstacles, and
    deterministic budgets, delivered as an internal oracle only. Board IR binding, physical
    clearance/via legality, KiCad serialization, and DRC remain required before this becomes a
    routing capability.
  - [x] Internal Board-IR ordered-stack proposal seam for two through eight all-signal copper
    layers: deterministic full-stack-through-via transitions, layer-scoped track/via keepouts,
    stack/via/search budgets, topology replay, and a committed three-layer completion oracle
    (B-076). Omitted via policy preserves the historical two-layer behavior while 3..8 layers
    receive a deterministic 64-via effective cap. File, live, and durable public entry points
    explicitly reject non-two-layer stacks; generalized serialization, DRC, refill, and apply
    remain explicit promotion gates.
  - [x] Public, file-backed `preview_layered_route` MCP proposal with pad-reference net inference,
    double CAS, closed structured output, candidate-only full-stack vias, and explicit opt-in
    candidate-bound authoritative DRC evidence. This remains a read-only two-signal-layer surface;
    durable export, multilayer generalization, refill, and apply remain open.
  - [x] Live `preview_live_layered_route` proposal over the exact byte-confirmed KiCad IPC
    snapshot, with a redacted `KICAD_API_TOKEN` session CAS and file-oracle equality benchmark;
    real GUI, live DRC, serializer, and apply evidence remain open.
  - [x] Structural candidate verification and endpoint-via avoidance now gate the internal
    serializer; exact padstack treatment, edge/hole clearance, refill, and fabrication evidence
    remain open.
- [~] Board IR-bound ordered-layer proposal adapter (currently two through eight all-signal
  layers): exact grid attachment, conservative foreign
  copper/zone/pad envelopes, separate track/via keepouts, immutable candidate digests, and
  fail-closed stale/off-grid/unsupported diagnostics are benchmarked in B-018. Source-preserving
  segment/via serialization and Board IR replay are covered by an internal disposable serializer;
  B-020 now binds that exact replay to private authoritative KiCad DRC, and B-024 exposes a
  separate read-only candidate preview through MCP. A bounded topology verifier now gates the
  serializer and refuses endpoint-via geometry; exact padstack/clearance, durable export,
  generalized KiCad serialization/DRC, and apply authority remain open gates. B-032 now covers the narrow
  public file-backed DRC evidence binding.
  - [x] Candidate topology gate: revision/endpoint binding, path-via adjacency, ordered-stack
    full-stack transitions, duplicate/crossing rejection, bounded pair checks, and explicit
    physical-validation non-claim.
  - [x] Freshness-verified same-layer zone fill now replaces the layered adapter's conservative
    zone-outline envelope with verified fill islands, carried as bounding boxes since the layered
    lattice model is rectangular, behind four ordered fail-closed gates (ADR-0070, B-086). A public
    `preview_layered_route` fill-authority contract with an ADR-0040-style `routing_effect` label,
    same-net poured attachment in the layered seam, and exact polygon layered collision remain
    open.
- [x] Attachment to existing same-net copper and bounded partial-route completion.
- [ ] Multilayer vias and keepouts.
- [x] Negotiated-congestion multi-net routing (issue #62, closed). The sub-items below record what
  the closed issue delivered and, where a sub-item is `[~]`, what it explicitly did not.
  - [x] Read-only route-bundle preview: two through eight known net references now compose into
    one immutable revision-bound plan only after deterministic whole-composition replay and the
    existing exact cross-net clearance gate. B-079 records a KiCad-checked private combined
    derivative; apply/export, multilayer, and general-board claims remain separate.
  - [x] Bounded candidate-only present/history congestion ledger with exact lattice edge/vertex
    occupancy, deterministic rip-up order, policy-digest-bound candidates, cancellation, and
    fixed iteration/routing budgets.
  - [x] B-036 KiCad-fixture replay: sequential baseline overflow `1` versus negotiated overflow
    `0` across three deterministic replays; physical clearance, multilayer capacity, KiCad DRC,
    and held-out corpus comparison remain open.
  - [x] Bounded same-layer candidate-pair physical acceptance gate: exact integer swept-disc
    clearance over orthogonal segments, assigned-width checks, the stricter pairwise net-class
    clearance, pair-check/cancellation budgets, and atomic discard of an invalid allocation.
    Generic router output is independently replayed by the deterministic reference core under a
    shared half-budget before publication. B-058 records a lattice-clean synthetic pair with
    `300,000 nm` available copper-edge clearance versus `500,000 nm` governing clearance,
    rejected deterministically.
    KiCad DRC, existing-board copper, pads, vias, zones, custom rules, multilayer geometry, and
    physical-conflict-guided rerouting remain open.
  - [x] Three separately declared, separately digest-bound negotiation policy slots — net order,
    per-iteration cost-update rule, and rip-up selection — each a closed enumeration member plus
    bounded integer weights that compose into one plan digest the published evidence re-derives
    (ADR-0073). This is opt-in behind a distinct `negotiated-congestion-plan-v4` identity; absent a
    declared plan, the existing coordinator, ordering, accounting, and `negotiated-congestion-v2`
    identities are byte-for-byte unchanged. B-087 sweeps ten declared plans over 330 replays with
    zero divergence and finds six worse than the default on the one fixture where negotiation
    genuinely iterates, including both rip-up rules and history decay, which fail to converge;
    multilayer/via negotiation, KiCad DRC, electrical, and fabrication authority are unchanged.
- [x] Incremental spatial index and bounded rip-up/reroute (issue #64, closed).
  - [x] Immutable conservative obstacle index for exact A*/Dijkstra query narrowing, with
    canonical linear fallback, differential route tests, and B-033 evidence.
  - [x] Candidate-only bounded rip-up/reroute coordination is covered by the negotiated
    congestion ledger and B-036; incremental obstacle updates and broader multi-net repair remain
    open.
- [~] Benchmark comparison against established open baselines. B-069 records one real smoke run
  using the official FreeRouting v2.2.2 JAR and real KiCad 10.0.5 GUI DRC on a licensed,
  CopperMCP-original two-pad fixture. Both observed output boards had zero hard violations, zero
  unconnected items, zero vias, and 20.0 mm routed length. The source/report and source/DSN-export
  relationships are `self_attested_unverified` and non-causal, as are the import and runner
  workflow receipts; CopperMCP's result came from a pure-kernel runner rather than MCP or the
  authorized apply service. The artifact therefore retains `comparison_closed=false` and
  `unavailable_or_incomplete`. A harness-owned SES-import transaction, constrained candidate
  runner, broader common corpus, and equivalent performance protocol remain required; bounded
  external execution is not sandbox containment.
  - [x] External-corpus intake and an in-repo harness. A benchmark-only SimpleRouteJson import seam
    converts tscircuit interchange problems into ordinary verified Board IR snapshots and route
    requests, over-approximating every obstacle and refusing typed rather than dropping anything it
    cannot represent. 20 of the 36 MIT-licensed `dwiel/tscircuit-benchmark` boards ship under
    `benchmarks/corpora/` with attribution and digests for all 36; `tscircuit/autorouting` carries
    no licence at all and is cited but not redistributed, and PCBWorld is announced but unreleased.
    B-088 records the first run on data this project did not author: 70 of 117 nets routed at
    1.1711× a provable lower bound, with every two-pin net refused because the reference lattice
    requires the pad-centre delta to divide by the grid step. **The cross-router comparison this
    item asks for is still not measured** — FreeRouting is recorded as `not_run`, and a
    SimpleRouteJson-to-DSN bridge, a common corpus neither router helped define, and an equivalent
    performance protocol all remain required.
  - [ ] Position CopperMCP as a verification harness for externally generated route candidates
    (starting with tscircuit / SimpleRouteJson solution output): convert an external candidate to
    CopperMCP's own candidate identity and run the full verification stack — exact clearance,
    structural verification, real KiCad DRC — returning accepted/refused with typed diagnostics
    and evidence, never a silently repaired route.

- [~] Emit candidate DRC evidence as a deterministic, unsigned in-toto Statement payload using
  Link v0.3, with digest-bound subjects/materials and aggregate redacted byproducts. DSSE signing,
  verification, persistence, and remote transport remain open before this roadmap item can close.

## M3 — Safe candidate application

Tracker state: 3 open — [#68](https://github.com/seunghyukchoe/copper-mcp/issues/68) the IPC
one-undo-commit apply, [#170](https://github.com/seunghyukchoe/copper-mcp/issues/170) DRC
reproducibility, and [#52](https://github.com/seunghyukchoe/copper-mcp/issues/52) placement apply.
Both file-backed applies ship; the live one does not.

### Entry criteria, not a start date

**M3 does not start next, and the earlier 50–80 hour estimate for it is retired.** Three
independent reasons, none of which is a re-estimate:

- **The scope half-shipped.** File-backed route apply and file-backed placement apply both landed,
  so an estimate for "M3" now names a different milestone than the one that was estimated. #52 is
  split rather than open: its file-backed half meets four of its six acceptance criteria, and its
  live half waits on #68.
- **Two of its criteria were never measured**, so part of the number was estimating work nobody had
  characterised.
- **The unit does not survive contact.** 50–80 hours is 68–109 agent runs against an observed 13
  merges in the week after `v0.7.0`. The figure was never in the same currency as the delivery rate.

What replaces it is four entry criteria. **Two of them are not agent-executable.**

| | Criterion | Why it gates apply |
|---|---|---|
| **E1** | Legible reasons for a withheld apply token, as a closed literal set | [R-149](ledgers/risk-register.md): a caller cannot today distinguish "apply is off", "this board cannot be applied to", and "this candidate was routed under fill". An apply surface whose refusals are indistinguishable cannot be driven by an agent. |
| **E2** | A DRC reproducibility policy ([#170](https://github.com/seunghyukchoe/copper-mcp/issues/170)) | An apply's evidence is DRC evidence, and `B-107` found DRC counts differing between two runs of identical bytes at the same commit. |
| **E3** | One real-editor IPC observation, **or** a recorded park of #68 | `docs/handoff/project-state.md` records that no successful real-editor IPC oracle run has ever happened. **May need the operator** — the workstation IPC server is disabled, and enabling it is a change to the operator's machine. |
| **E4** | Appliability re-measured on the frozen corpus | The current figure, 5 of 13 converting saves, is argued forward from a run on a tree that is not the current one. |

The recommendation on E3, which the operator may take or decline, is to **park** #68: the mutation
would be built on a transport this project has never successfully spoken to, and the protocol
exposes no revision, no dirty flag and no conditional write. #68 is parked and deliberately **not
closed**, because the decision is the operator's.

- [x] Durable routing jobs and cancellation. The bounded internal ledger, single-worker lease
  recovery, redacted candidate manifests, file-backed layered request/result persistence,
  authorization-bound geometry export, and ordinary MCP tools now exist. Single-layer/live jobs
  and MCP Tasks negotiation remain open. Candidate artifacts are preflighted against immutable job
  bindings and the exact `RUNNING` revision before storage, then persisted before the completion
  CAS; capacity or persistence failure cannot create a completed job without its export. A later
  completion/cancellation race can still leave only an inaccessible TTL-bounded orphan, and TTL is
  not secure erasure.
- [~] MCP Tasks progressive enhancement. The reference environment observed `mcp 2.0.0` while
  the supported dependency range remains `<3`; a runtime probe finds generic extension support
  but no compatible current Tasks wire/dispatcher contract. A bounded, process-local 256-bit
  owner-context-bound handle broker is available as a future seam, but it is not durable and no
  `io.modelcontextprotocol/tasks` methods or advertisements ship. A pinned client/server matrix,
  session-authenticated owner binding, and durable task-handle repository remain required; the
  ordinary routing-job tools are the fallback. See [MCP Tasks compatibility research](research/mcp-tasks-compatibility.md).
- [x] Immutable route patch format. A byte-preserving span-splice CST and a pure apply engine:
  given board bytes and a verified candidate they return the bytes an apply would write, proven
  by a three-part assertion (untouched bytes bit-identical, result reparses fail-closed,
  resulting Board IR equals source plus patch exactly).
- [x] Explicit, separately authorized `apply_candidate`, for **route patches only**. Operator
  opt-in flag defaulting off, single-use HMAC apply token issued by the preview and enforced
  server-side, `.lck` hard refusal, double compare-and-swap on the file and Board IR digests,
  timestamped pre-apply copy, atomic replace with fsync on both file and directory, and
  restore-and-report if post-publication verification fails. Verified against real KiCad: the
  applied board opens, the previously unconnected net becomes connected, and no DRC error is
  introduced.
- [~] Revision-race protection is implemented (double compare-and-swap, typed `stale_candidate`,
  never auto-refreshed). **One KiCad undo commit is not**: the pre-apply copy is a file the user
  restores manually and never appears in KiCad's undo stack. A real single-undo transaction needs
  the IPC API's `begin_commit`/`push_commit`/`create_items`/`update_items` primitives, which are
  documented and not experimental. This corrects an earlier assumption: an in-memory document
  *can* be bound to a content identity through `get_as_string`, just never to the on-disk file
  digest, since the gap between the two is exactly the dirty flag the protocol omits (ADR-0074).
  `apply_live_candidate` now ships every precondition a live mutation would need — an operator
  opt-in `COPPER_MCP_ALLOW_LIVE_APPLY` deliberately independent of the two existing consent flags,
  a session/board/snapshot triple compare-and-swap bound under its own HMAC domain, and a full
  candidate replay against the live board — then refuses with `capability_not_implemented` from
  the exact point `begin_commit` would be called. The mutation itself still waits on adversarial
  review: the IPC protocol exposes no revision, dirty flag, or conditional write, and `kipy`
  discards the per-item status a caller would need to confirm a partial write, so these hazards are
  recorded rather than mitigated.
- [~] Placement apply. `apply_placement_candidate` is now separately authorized from route apply:
  file-backed previews may explicitly request a placement-scoped single-use token, and the pure
  source-preserving replay plus atomic file service applies front-side orthogonal footprints with
  native identity and supported rectangular `F.CrtYd` syntax. Unsupported properties/text/
  fabrication graphics/library identity/3D-model pose, side flips, live-editor DRC/scene evidence,
  and live IPC mutation remain fail-closed open gates.

## M4 — High-fidelity Circuit Scene and AI policy plugins

Tracker state: 3 closed, 1 open. Issues #69, #72 and #74 are closed;
[#110](https://github.com/seunghyukchoe/copper-mcp/issues/110) — give the excessive-agency
evaluation a reachable externally authored project family — was filed unmilestoned on 2026-08-13 and
moved here on 2026-08-14. **The milestone stood at zero open until then, and nothing about the
engineering changed on the day it did**; read the count as an accounting fact about the tracker, not
as a claim about the `[~]` items below, which remain untracked rather than done.

[R-148](ledgers/risk-register.md) is what #110 answers: the excessive-agency suite can stop
exercising its authorized path without failing, because every row degrades to `not_run` when its
precondition is absent — honest per row and dangerous in aggregate. Measured, not hypothesised: 90
passes become 76 with an empty `failures` list. The permit is evidenced on **one** held-out family,
so a second independently authored family is what makes the aggregate legible.

Circuit Scene IR covers both semantic and visual observation, and placement has a public preview
surface judged by a deterministic legalizer. The bounded file-backed placement apply gate is now
implemented; what remains is broader source fidelity, post-action/editor authority, solving for a
placement, and the policy-plugin work.

- [x] Versioned Circuit Scene IR for bounded semantic and visual observation. Semantic observation
  is `observe_board_scene` (Circuit Scene IR 0.3.0): region-scoped, exact integer geometry,
  first-class footprint pose/pad ownership/courtyard observation, a static/mutable partition,
  stable Board IR references with declared durability, relationship-aware explicit truncation,
  and board text quarantined in a separately typed untrusted collection. Visual
  observation is the opt-in `include_render` flag: a deterministic, digest-bound, copper-only SVG
  delivered as an ephemeral capability, subordinate to the scene by construction and whole-board
  rather than region-scoped. A human-facing thumbnail remains unimplemented.
- [x] Referentially closed Scene-to-route MCP edge. `preview_route` accepts an observed opaque net
  reference without requiring the hidden KiCad net name, binds it to both scene revisions, refuses
  stale captures before candidate work, and advertises closed input/output schemas. B-007 measures
  0/3 actionable references through the former name-only shape versus 3/3 through the reference
  shape, with exact candidate equality to the hidden-name oracle on one licensed audio fixture.
- [x] Typed placement-intent contract and immutable placement preview/candidates. The seven-rule
  intent language, the revision-bound Board IR footprint view, the deterministic legalizer, the
  dual-digest-bound `PlacementCandidate` and the `preview_placement` MCP tool and CLI command are
  implemented. A locked footprint cannot be moved. `preview_live_placement` adds the same
  candidate-only pipeline over a byte-confirmed active KiCad snapshot; it requires both scene
  digests and never writes or grants apply authority. File-backed placement apply is tracked as a
  separately authorized gate below; live placement remains proposal-only.
- [~] Authoritative KiCad DRC binding for placement candidates. The narrow internal
  source-preserving serializer covers front-side, orthogonal, unfilled-courtyard footprints and
  reparses its disposable result; `run_placement_candidate_drc` now binds that exact replay to a
  private KiCad 10.0.5 DRC context with source/context CAS and redacted aggregate evidence.
  File-backed `preview_placement` now exposes this evidence only through explicit
  `include_drc: true`, with candidate/source/patched-board/context bindings and distinct hard-gate
  `passed` versus warning-aware `clean` semantics. Unsupported properties/text/fabrication
  graphics/library identity/3D-model pose, live compare-and-swap, placement apply, and broader
  geometry remain open gates.
- [~] Deterministic snapping, connectivity, clearance, rule, provenance, and revision validation for
  every placement candidate. Grid snapping, rule residuals, three-valued pad overlap, outline
  containment, keepout respect, and dual-digest binding are implemented, including stationary
  supported courtyards from padless footprints while keeping those footprints out of candidate
  manifests. Courtyard overlap is now three-valued (`proven_clear`/`violated`/
  `inconclusive`), paired **by courtyard layer rather than by footprint side** (ADR-0097), and bound
  to what KiCad 10.0.5 actually compares — a cached `SHAPE_POLY_SET`
  contracted by a 5,000 nm `BuildCourtyardCaches` inset, so a collision needs 10,000 nm of nominal
  penetration, and an even-odd ring-nesting rule under which a donut courtyard's centre is
  occupiable (ADR-0075, closing issues #72 and #74). This corrects, rather than restates, an
  earlier "exact" claim: the prior model treated every courtyard ring as an independent solid and
  falsely refused donut courtyards, a topology most RF shield-can footprints use. B-089 records 0
  false-positive violations and 0 false-negative clears over 15 real-KiCad cases, with the
  sub-10,000 nm band where the two models genuinely disagree reported as `inconclusive` rather than
  rounded either way. Board IR accepts unfilled `fp_rect`, `fp_poly`, and unordered complete
  `fp_line` cycles only when they normalize to simple closed horizontal/vertical rings; B-073
  measures the resulting exact positive-area legality. Pad-net connectivity after a placement,
  nonzero custom clearance, arcs and non-orthogonal courtyard geometry, and general topology remain
  future work.
- [~] Broaden courtyard geometry and side-aware placement safely. Bounded `F.Cu`/`B.Cu`
  observation imports simple closed orthogonal rectangles, polygons, and unordered line cycles
  without a second mirror; malformed chains and unsupported fields fail closed. A courtyard on the
  layer *opposite* its footprint's side is no longer refused — it converts as the footprint's
  far-side keep-out and constrains that layer (ADR-0097), which also means a side flip must swap a
  footprint's two courtyard sets as well as mirror them. Diagonal edges, curves, arcs, fills,
  open/branching/self-intersecting contours, holes or multi-loop contour semantics, nonzero custom
  clearance, GUI-authored flip serialization, side-aware placement apply, and a live desktop
  no-second-mirror oracle remain open; the source-preserving placement serializer still refuses
  every board carrying a far-side courtyard rectangle.
- [~] Separately authorized placement apply; direct AI mutation of KiCad remains prohibited. The
  bounded file-level surface is implemented and measured, and file-backed post-placement
  DRC/scene observation is revision-bound; general footprint fidelity and live-editor action CAS
  remain open.
- [~] Heuristic policy baseline and trace dataset. An internal deterministic local/beam placement
  search evaluates legalizer-issued immutable candidates only and improves the same-net Manhattan
  proxy by `2,000,000 nm` (`12,000,000 → 10,000,000 nm`) on three deterministic committed-fixture
  replays (B-059). Its `O(n log n)` scoring, deadline, cancellation, and pad-subject normalization
  are bounded; it is not an optimizer, KiCad mutation path, DRC result, or routing-quality claim.
  - [x] An explicit private `route-aware-astar-v1` scoring policy now independently probes a
    bounded A* route on an in-memory Board IR projection of each already-legal placement candidate,
    after identity and exact snapshot/view binding checks. One operation-wide probe meter caps all
    solver evaluations. The default Manhattan ranking and public candidate shape remain unchanged.
    B-078 records three deterministic replays of the original Apache-2.0 NE5532 fixture: the
    selected candidate reduced the **single** independent probe's exact routed length from
    `42,000,000 nm` to `32,000,000 nm` (23.8095%) with zero unrouted probes **on that one net**.
    B-082 corrects the interpretation: the two policies run two different bounded searches rather
    than re-ranking one shared set, and probed against all eleven of the fixture's probeable nets
    both chosen candidates leave four unrouted and the ordering reverses. This is not combined-net
    routing, congestion/overflow, KiCad DRC, external-router validation, placement optimality, or
    apply authority.
- [~] Typed net-ordering, corridor, and repair policy interface. The closed advisory
  `routing.policy` contract has deterministic reference decisions, bounded hostile JSON handling,
  canonical digest binding, and redacted ordinal-only action traces (B-060). It can only order
  known nets and select coordinator-supplied options; it cannot emit copper. The exact internal
  `deterministic-reference-v1` profile and its separately named fixed-worker equivalent
  `deterministic-reference-worker-v1` may influence only the initial negotiated net order;
  no-profile v2 result shape and candidate identity are unchanged, and retry ordering remains
  coordinator-owned. The worker receives only the same neutral scalar, no-window input and fails
  closed before router construction. It is defense in depth for the fixed reference backend, not
  an OS sandbox or admission path for a model, plugin, endpoint, corridor, repair, MCP, or apply
  authority. The content digests are linkable bindings, not secret redactions. B-063 records only
  the in-process profile's synthetic order effect, not worker performance or routing quality.
- [ ] Optional local GNN/RL reference policy.
- [~] Prompt-injection and excessive-agency tests. Board-author text is quarantined in the scene
  and asserted absent by a whole-response grep against a hostile fixture, and every request
  boundary has a hostile-input suite. B-065 now adds seven deterministic offline MCP cases for
  schema closure, stale revisions, quotas, report disclosure, and unauthorized route/placement
  apply; all 7/7 reached their predeclared safe disposition with no workspace change. This remains
  partial because no model, network, remote principal/authorization, application logger, host log,
  provider telemetry, or unknown-attack campaign was evaluated.
- [~] Held-out project-family evaluation. B-066 records three exact replays over one independently
  authored Apache-2.0 audio family that is hash-separated from the declared training family; the
  evaluator reads neither training nor tuning fixtures. It is a first split/evidence baseline, not
  a corpus: more independently licensed families, a frozen policy comparison, and separate KiCad
  DRC evidence are required before this item can close.

## M5 — Verification and physics

Renamed on 2026-08-14 from "Performance and physics". The old name promised acceleration work that
nothing in the milestone had begun and that its own issues gated behind a profile nobody had taken.
What the milestone actually contains, once those gates are honoured, is verification.

Tracker state: 2 closed, 5 open. [#99](https://github.com/seunghyukchoe/copper-mcp/issues/99) is now
the centre; [#88](https://github.com/seunghyukchoe/copper-mcp/issues/88) and
[#89](https://github.com/seunghyukchoe/copper-mcp/issues/89) are closed **by their own stated
gates**, not by a judgement against multicore or GPU work, and each names the evidence that reopens
it.

- [~] **Profile-guided acceleration, re-scoped to profiling**
  ([#87](https://github.com/seunghyukchoe/copper-mcp/issues/87)). B-068 establishes only the
  clean-worktree measurement prerequisite: fixed routing, placement, and Circuit Scene fixtures;
  invariant output digests; unprofiled timing samples; and a separate bounded cumulative profile. No
  Rust, SIMD, GPU, speedup, cross-machine comparison, or public-contract change exists. The
  re-scoping is forced by a measurement: [D-195](ledgers/decision-ledger.md) recorded **20.9 s of a
  complete read's 24.2 s** on the largest corpus board spent in the parse rather than in the router,
  and B-068's fixtures omit the parser entirely — so the milestone's one measurement prerequisite
  does not measure the dominant cost. Extending the fixture set to cover the parse path, and
  recording a profile that attributes cost by stage, is what this item is now.
- [x] ~~Conflict-aware multicore scheduling~~ — **closed as a non-goal by its own gate**
  (#88, closed 2026-08-14). The issue was "blocked on profiling evidence from the Rust-acceleration
  issue to know where parallelism pays", and that evidence does not exist. Reopens if the profile
  lands and shows candidate work dominating and parallelisable.
- [x] ~~Optional GPU candidate search~~ — **closed as a non-goal by its own gate** (#89, closed
  2026-08-14). It was "explicitly gated behind end-to-end profiling evidence", and neither conjunct
  of that gate — candidate generation dominating, the CPU path failing to close the gap — is
  established, because no end-to-end profile exists. Reopens on the profile plus a CPU attempt.
- [~] **Verification harness for externally generated candidates**
  ([#99](https://github.com/seunghyukchoe/copper-mcp/issues/99)) — the milestone's centre. Two
  things are true today and both shape the first slice. The **disposer seam does not exist yet**:
  `validate_candidate` normalizes an untrusted manifest and returns `valid` without reading
  geometry, consulting a board or producing a verdict. And the committed tscircuit corpus
  **carries no solution traces** — all 20 samples hold obstacles, connections, bounds, layer count
  and minimum trace width, and no routed result — so the first foreign candidate must be
  re-expressed from a run this project already made rather than read out of the corpus. The first
  slice re-expresses B-088's 70 routed nets as foreign input, and its predeclared result has two
  halves: every unperturbed candidate accepted, **and** four predeclared perturbations each refused
  with a **distinct** typed code — a 1 nm shift into an obstacle, a dropped segment, a wrong-pad
  endpoint, an undeclared-layer via. **The acceptance count alone is explicitly not the result**: a
  seam that accepts all 70 and refuses nothing has demonstrated only that it parses.
- [~] **Local exact repair for bounded congestion windows, parked behind #99**
  ([#90](https://github.com/seunghyukchoe/copper-mcp/issues/90)). The standalone deterministic
  operator and predeclared 5 × 5 detour regression exist (B-067), but negotiated-router integration
  does not. Parked rather than scheduled because repair is a **proposer**, and integrating one into
  the deterministic core before the core can dispose a proposal inverts the invariant the project is
  built on. Coordinator-derived window provenance, Board IR/candidate binding, physical-clearance
  and reference replay gates, plus broader held-out evidence, all remain required afterwards.
- [ ] **SI/PI/thermal/DFM surrogate hooks with authoritative signoff, parked behind #99**
  ([#91](https://github.com/seunghyukchoe/copper-mcp/issues/91)). The half of this that carries the
  safety property is the signoff, not the surrogate — any claim surfaced to a caller comes from an
  authoritative tool run or is declared a non-claim — and that is the same evidence-binding contract
  #99 has to define first. Design the hook contract against #99's vocabulary rather than inventing a
  second one.
- [ ] **The ordered-layer per-island fill ceiling**
  ([#167](https://github.com/seunghyukchoe/copper-mcp/issues/167)), parked with a paired-calibration
  requirement: the per-island ceiling and `max_obstacle_checks` must be sized **together**, in the
  style of B-094 and B-108, because the pairwise-contact cost is linear in ring vertices per test.
  **No route-quality justification is admissible** — B-105 measured the single-layer fill shrink's
  benefit on the corpus at zero (2 connectivity improvements, 6 budget regressions, 0 routes
  unlocked). One correction to the issue's own text, recorded in its comment thread: an over-ceiling
  island does **not** fall back to the zone's conservative outline envelope — the layered adapter
  validates fill at the input boundary before any snapshot work, so a single over-ceiling island
  refuses the **whole request**. The direction stays refusal-side and safe, but the graceful
  per-island degradation the issue describes would have to be built, not preserved.

## Audio Board Lab #001 — Physical validation

Tracker state: 0 closed, 1 open — [#8](https://github.com/seunghyukchoe/copper-mcp/issues/8),
validate the CopperTone stereo line buffer.

### The gate, and why the issue as written does not carry it

**As written, #8 would validate the board. The thing that needs validating is the tool.** A
physical board that works proves the designer's work; it proves nothing about CopperMCP unless
CopperMCP contributed a capability the tool owns. The excessive-agency evaluation makes the gap
concrete: it records `no_apply_capability_available` on seven of its eight not-run rows, so the
apply path — the one capability whose correctness a physical board could actually witness — is not
being exercised at all.

Four pre-conditions, in order.

- [ ] **Close [R-033](ledgers/risk-register.md) first.** The committed CopperTone board's
  mounting-hole rule areas were generated as octagons **inscribed** at the required 2.85 mm keep-out
  radius, so their edges sit 0.2169 mm inside the requirement and copper may legally approach closer
  than the constraint intends. The generator is already fixed; the committed board is not.
  Regeneration moves coordinates, and **that invalidates every measurement recorded against the
  board's current revision digest** — so the enumeration of those records comes first and the
  regeneration is sized from it, not before it.
- [ ] **Give CopperTone a capability the tool owns.** Concretely: a derivative with named nets left
  unrouted → `preview_route` → apply under a real token, so the copper on the physical board came
  from CopperMCP. The DRC half of that evidence is qualified under the reproducibility policy
  ([#170](https://github.com/seunghyukchoe/copper-mcp/issues/170)), not published as a bare count.
- [ ] **Decide `ComponentKind`.** It admits exactly `resistor` and `capacitor_unpolarized` today, so
  an OPA1656 is inexpressible in Circuit Intent. Either widen the kind or record the manual path —
  but record which, because the lab's build steps depend on the answer.
- [ ] **Write the gate into #8 itself**, so the issue's acceptance criteria ask for tool validation
  rather than board validation.

One claim this section deliberately does not make: **"tier1-rev-a converts" is not verifiable from
the published records**, which name no boards on purpose. Nothing above depends on it.

## M6 — Sustainable open-source project and supply-chain maturity (OpenSSF-informed)

This is a cross-cutting milestone for the 0.5.x+ line. OpenSSF Criticality Score is useful as a
diagnostic for project activity and adoption, but it is not a quality target by itself. The work
below is deliberately tied to healthier engineering, contributor experience, and release safety;
synthetic commits, releases, issues, or comments are prohibited. The baseline and source links are
recorded in [the OpenSSF research note](research/openssf-criticality-and-supply-chain.md).

- [x] Record a dated, reproducible baseline: an estimated Criticality Score of about `0.23/1.00`
  from the available public signals (the official public row did not yet include this new
  repository) and a distinct OpenSSF Scorecard snapshot of `5.8/10` on 2026-08-04. Keep the two
  measures separate and label estimates as estimates.
- [~] Make contribution onboarding real rather than nominal. `CONTRIBUTING.md`, the Code of
  Conduct, issue templates, labels, CODEOWNERS, reproducible fixtures, and good-first issues exist;
  add a small contributor validation path, explicit maintainer/reviewer roles, and documented
  review expectations as the contributor base grows.
- [~] Operate issues and discussions as an engineering feedback loop. Keep triage labels and
  response/closure reasons, publish a lightweight monthly review of open/closed/security issues,
  and use Discussions or an equivalent RFC surface for public-contract and KiCad compatibility
  questions without closing issues merely to improve a metric.
- [~] Make every release useful and verifiable. Continue SemVer, Keep a Changelog, release-ledger,
  CI, dependency updates, and tag-only attestations; add signed-tag or independently verifiable
  release provenance, a documented support window, and a material-change release cadence before
  treating the release gate as complete.
- [~] Close repository-level supply-chain gaps surfaced by Scorecard: configure and continuously
  verify branch protection/code-owner review, improve packaging metadata, add signed-release
  verification, and complete the CII/OpenSSF best-practices profile. Do not claim a check is fixed
  until a hosted run or API response proves it.
- [x] Ship a tested agent-facing usage contract, `docs/agents.md`, that restates every typed
  refusal as the next action an agent should take — for example, `stale_revision` means re-observe
  and rebuild the candidate, and `apply_disabled` is a question for the operator, never something
  to route around. `tests/test_agents_doc.py` mechanically asserts every MCP tool it lists is still
  registered, every registered tool appears in it, and every diagnostic code it names still exists,
  so the document cannot silently drift from the implementation; a root `llms.txt` points an LLM at
  it first.
- [x] Package the KiCad IPC plugin for the official Plugin and Content Manager (PCM), so
  `hardware/kicad-ipc-plugin` no longer needs a manual install, while keeping the
  token-never-leaves-the-plugin property and `COPPER_MCP_ALLOW_LIVE_IPC` default-off documented in
  the listing (issue #98, closed). It installs as
  `com.github.seunghyukchoe.coppermcp-live-observer`, `kicad_version` `9.0.1`, built reproducibly
  by `make pcm`. Installing it grants nothing on its own: the operator flag still has to be set in
  the environment KiCad was launched from.
- [ ] Build an adoption and evidence path: versioned audio-board examples, reproducible benchmark
  commands, downstream smoke tests, citations, and a small set of independent users or projects
  that can validate the documented MCP/KiCad contracts without uploading proprietary boards.
- [ ] Reduce maintainer bus factor for security and releases with at least two active reviewers,
  documented succession/security contacts, and CODEOWNERS coverage for routing, MCP, KiCad, and
  release workflows. This is a people/process gate, not something an agent can self-certify.
- [ ] Add a monthly project-health snapshot to the append-only ledgers: official Criticality Score
  when published, otherwise the exact input snapshot and reconstruction method; Scorecard result;
  CI/release/issue/contributor counts; and links to the underlying public evidence. Never put
  credentials, private boards, or private contributor data in the snapshot.

### 0.4 target and exit criteria

The project may report “Criticality Score ≥ 0.4” only after the official public service reports at
least `0.40` for two consecutive monthly snapshots, or after two independently reproducible
reconstructions are available while the official row is still delayed. The milestone is not closed
by the number alone: the contributor, review, release-provenance, supply-chain, and adoption gates
above must have current evidence, and the project must remain useful even if OpenSSF changes its
formula or publication cadence.
