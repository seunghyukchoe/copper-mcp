# Migrating a deployment from CopperMCP 0.11.0 to 0.12.0

This note is the deployer's delta, audited item by item against the `0.12.0` CHANGELOG section.
Every entry there is classified below as either a caller-visible change with an action, or an
explicit non-claim with the reason it needs none.

## 0. No schema version moves, and no snapshot needs re-conversion

**`BOARD_IR_SCHEMA_VERSION` does not move in this release. It is `0.4.0` at `v0.11.0` and `0.4.0`
at `0.12.0`.** This was checked against the constant itself (`src/copper_mcp/board_ir/types.py`,
line 10) at both points, not inferred from the absence of a CHANGELOG entry. `schemas/` has no
diff at all across `v0.11.0..0.12.0`, so no published schema file's accepted set moves either.

The consequence:

- **do not** re-convert persisted Board IR;
- **do not** invalidate snapshot digests, candidate caches or scene caches on account of the
  version;
- `inspect_board_ir` continues to report `ir_schema_version: "0.4.0"`;
- `schemas/board-ir/0.4.0.schema.json` is the active schema and is byte-unchanged. `0.1.0`,
  `0.2.0` and `0.3.0` remain frozen legacy files.

This holds even though §1 and §2 widen what the adapter *accepts*, and the reason is the same one
[ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md) gave at the 0.11.0 cut:
the accepted set that governs a schema version is the **emitted document's**. `scripts/check_schema_sets.py`
passes with no exemption added; that is the mechanical confirmation rather than this paragraph.

There is, however, a **sharper distinction at this cut than at the last one**, and a deployer
should read it rather than carry 0.11.0's sentence forward. At 0.11.0 every newly accepted field
was validated and *discarded* — nothing accepted entered `BoardIRSnapshot` at all. That is no
longer true in one place. §2's footprint copper polygon becomes a **netless `Segment` in the
snapshot**: a real modelled object, in an existing collection, in an existing field, of an
existing type. The schema version does not move because the *shape* of the emitted document is
unchanged — no new field, no new type, no new enum member — and a `Segment` with `net_id: None`
was already representable and already emitted for net-tie copper under
[ADR-0092](../adr/0092-net-tie-copper-is-modelled-without-a-net.md). What changed is which boards
produce one, not what a produced document may contain. **A consumer validating against
`0.4.0.schema.json` is unaffected. A consumer that assumed netless segments only ever came from
net ties is not** — see §2.

0.9.0 required a two-hop `0.2.0` → `0.4.0` re-conversion
([the 0.9.0 note](copper-mcp-0.9.0.md), [board-ir-0.4](board-ir-0.4.md)). If that migration is
still outstanding it remains outstanding — installing 0.12.0 neither performs nor excuses it, and
neither did 0.10.0 or 0.11.0.

`SCENE_VERSION`, `NEGOTIATED_ROUTER_VERSION`, `ROUTER_VERSION`, the candidate versions, the four
preview response versions 0.10.0 moved and every published schema file are likewise unmoved.
**What moves in 0.12.0 is the set of boards that convert, the size and kind of one disclosure map,
one placement refusal, and a family of refusal message texts — not the IR and not any response
version.** No dependency range moves at this cut; `mcp>=2.0.0,<2.2.0` is unchanged from 0.11.0.

## 1. Board IR accepts `Edge.Cuts` outline **arcs**, so more boards convert

[The dedicated note](edge-cuts-outline-arcs.md) is the short form. This is an **acceptance
widening**: a board whose outline is drawn with a rounded corner — which is what a `gr_arc` on
`Edge.Cuts` is — refused in 0.11.0 and converts now.

A root `gr_arc` is chained with `gr_line` segments into the same single closed loop and modelled
as an **inscribed** polyline. Every vertex is an exact integer-nanometre point inside the region
the arc and its chord bound, so **the modelled board is never larger than the drawn one**.
Subdivision is sized by KiCad's own `maxError`, `5,000` nm (`pcbIUScale.mmToIU(0.005)`), applied
strictly inward rather than two-sided — the same constant KiCad polygonises the outline with for
its own design-rule checks, so a caller comparing against KiCad is not handed a coarser board than
KiCad works with. The difference is the **sign**
([ADR-0124](../adr/0124-an-outline-arc-is-inscribed-and-a-cut-is-refused.md), `D-229`, `R-180`,
`SEC-166`, `B-134`, `B-135`, [issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188)).

**What a deployer must act on is the direction of the change**, the same shape as 0.11.0 §2 and
§3: a deployment that special-cased, suppressed, alerted on or routed around an outline-arc
refusal will now receive a converted board where it received an error. Retire the workaround; a
code path that treats "conversion succeeded" as unexpected will misbehave.

**This converts no public board, and that is stated as a measurement rather than a disclaimer.**
The ten-board licence-clean cohort was 0/10 before and is 0/10 after; the eight boards that
refused *at* this gate now refuse *behind* it, six of them at ADR-0095's copper-text wall. That
was predicted in writing before the adapter was touched and measured after (`B-134`, `B-135`). The
frozen own 18-save corpus stays 15/18, with the new number `0` on all 15. **A deployer should not
expect this release to convert a board that did not convert before** unless that board's only
blocker was an outline arc — which no public board's was.

## 2. Board IR accepts a footprint's stray **copper polygons**, and models them as a superset

[The dedicated note](footprint-copper-graphics.md) is the short form. This is the second
acceptance widening and the one with the most consequence for a caller, because unlike §1 it
**puts an object into the snapshot**.

A filled `fp_poly` on one declared copper layer, in a footprint that declares no net tie, becomes
a **netless `Segment`** whose modelled extent is the polygon's board-coordinate vertex bounding
box inflated by the stroke half width. That is a **proven superset** of the drawn copper, so the
model can refuse a route and can never permit one through the polygon
([ADR-0125](../adr/0125-stray-footprint-copper-is-bounded-because-no-fill-rule-is-written-down.md),
`D-230`, `R-181`, `SEC-167`).

**The bounding box is not a shortcut, and the reason matters to anyone tempted to ask for a
tighter model.** The census measured **0 of 56** of these polygons with an all-distinct vertex
ring, so a `Ring` cannot represent one of them at all; and **26 of 56** are self-intersecting,
whose filled area even-odd and non-zero winding disagree about while the document names neither
fill rule. There is therefore no exact region to model until a fill rule is chosen, and a box is
correct under every rule. `B-136` is the closed graphic census over the two public boards `B-133`
found blocked here; `B-137` is the before/after.

### Three deployer-visible consequences

- **A board with stray footprint copper converts and routes, but cannot be written back.** The
  identity is revision-derived, which is the contract net-tie copper has had since ADR-0092. A
  deployment that assumed "converts ⟹ applyable" is wrong for this class of board. This is not new
  machinery; it is a new population reaching existing machinery.
- **Connectivity claims nothing.** `net_id` stays `None`. A consumer that treated every netless
  `Segment` as a net tie will now mis-attribute stray copper. Branch on the disclosure count in
  §3, not on the absence of a net.
- **A routable board may now report unroutable.** The envelope is looser than the copper it stands
  for. That direction is safe — it cannot permit an illegal route — but it is not free, and §3 is
  how a caller detects it.

**Copper-graphic vertices are now charged against the caller's `max_total_vertices` budget**,
exactly as reduced custom-pad primitive vertices already were. A caller sitting close to its
vertex ceiling on a board with stray footprint copper may now exceed it where it did not before.
Raise the budget or expect the refusal; it is a budget refusal, not a geometry one.

## 3. `unmodelled_counts` grows from nine entries to eleven, and one entry is a new *kind*

`inspect_board_ir`'s `unmodelled_counts` map gains two keys:

| Key | What it reports | Kind |
|---|---|---|
| `outline_inward_deviation_nm` | an upper bound, in nanometres, on how far inside the drawn boundary the modelled one runs (§1) | a **distance** |
| `footprint_copper_graphic_envelope_count` | how many footprint copper polygons were modelled as bounding-box envelopes (§2) | an **approximation** count |

**A client that asserted `len(unmodelled_counts) == 9`, or compared the map against a hard-coded
nine-key set, must widen it to eleven.** A client that reads keys by name is unaffected. Both keys
are present on every supported board, zeros included. This is the third consecutive release to
grow this map — six → nine at 0.11.0, nine → eleven here — and a client still asserting a fixed
size should stop rather than re-pin.

Two things about these entries are genuinely different from the nine that preceded them, and a
reconciliation script written against the old semantics will be wrong about both:

- **`outline_inward_deviation_nm` is a distance, not a count.** It is the same shape as
  `max_roundrect_rounding_nm`, which is the existing precedent for a non-count member of this map.
  Summing the map, averaging it, or treating every member as a cardinality was already wrong and
  is now wrong on two members. It is **0** for every rectangle- or segment-drawn outline, so **no
  board that converted before 0.12.0 reports anything new here** — a non-zero value is only ever
  reachable through §1's newly accepted arcs.
- **`footprint_copper_graphic_envelope_count` discloses an approximation, not an erasure.** Every
  other member of this map counts something Board IR *discarded*. This one counts something Board
  IR *kept, loosely*. A non-zero value means that many obstacles are looser than the copper they
  stand for. It cannot permit an illegal route; it can make a routable board report unroutable.
  **A caller investigating an unexpected routing refusal should read this key first.**

## 4. `preview_placement` and `preview_live_placement` refuse an approximated outline

This is **the one place in this release where a request that was answered before now returns an
error**, and it is the item most likely to surface as an incident. It is a direct consequence of
§1: the acceptance widening that lets more boards convert also creates boards whose outline is
inscribed rather than exact, and placement is the one consumer that cannot take an
under-approximated boundary.

**Behaviour.** When `conversion.outline_inward_deviation_nm` is non-zero, both tools return
`status: "refused"` with `PlacementFailureCode.UNSUPPORTED_GEOMETRY` and the message
`placement needs an exact board outline and this board's is approximated`.

**Why a refusal rather than a degraded verdict.** `PlacementLegality`'s `outline_containment`
publishes in **both** directions — `proven_inside` from an over-approximating pad box, and
`violated` from an under-approximating pad core crossing the boundary — and only the first
survives a boundary that is itself under-approximated. Copper sitting in the sliver between the
inscribed polygon and the true arc is inside the fabricated board and would be reported as
crossing its edge. Edge and region rules measure *against* the same boundary and have no
`inconclusive` value to degrade into. Three verdicts quietly weakened is a false claim and not a
conservative one, so the request is refused by name. ADR-0124 records the exit condition.

**Refusal ordering is contract, and it did not change.** The new refusal sits *after* the snapshot
compare-and-set, deliberately. A caller holding a stale snapshot digest has a wrong world-view and
must learn *that* first; told `unsupported_geometry` instead, it would conclude the board it
thinks it has cannot be placed — a false statement about a board that may place fine. **A caller
must not reorder its own handling to check geometry before staleness.**

**Routing is unaffected.** Less room is never a false claim about where copper may go, so the
router, DRC and every other outline consumer take the inscribed boundary without complaint. This
asymmetry is intentional and is the reason the refusal is scoped to placement alone.

## 5. Refusal message and locator changes that a caller can observe

**No refusal message is a contract and none ever was** — the golden set is a regression detector,
and the instruction to branch on typed codes and locators rather than prose is unchanged. These
are listed because a deployment that matched prose anyway will notice. The golden table grew from
7 tables / 35 entries / 33 distinct messages to **11 tables / 46 entries / 40 distinct messages**.

### Two shared `Edge.Cuts` strings are retired

Both `Edge.Cuts outline arcs, circles and curves are unsupported` and
`Edge.Cuts outline arcs, circles, polygons and curves are unsupported` are **gone from the
source**. A caller matching either string will stop matching. They are replaced by per-primitive
sentences:

| Head | New message |
|---|---|
| `gr_circle` | `Edge.Cuts outline circles are unsupported` |
| `gr_curve`, `gr_bezier` | `Edge.Cuts outline Bezier curves are unsupported` |
| `gr_poly` | `Edge.Cuts outline polygons are unsupported` |

and by three new arc-specific refusals that a reader of §1 might expect to convert:

- **`Edge.Cuts outline arcs cutting into the board are unsupported`.** An arc that cuts *into* the
  board still refuses, and now says so. Its safe polyline runs *outside* the circle in a region
  that is **not convex**, so it needs an exact per-edge distance test rather than the two
  per-vertex integer predicates the inscribed case decides with — a different proof obligation,
  measured at **zero conversions** on the public cohort, refused by name with the exit condition
  recorded. This is a deliberate scope boundary, not an oversight.
- **`Edge.Cuts outline major arcs are unsupported`.**
- **`Edge.Cuts outline arc is degenerate`**, alongside the existing zero-length-segment,
  duplicate-segment, budget and single-closed-loop refusals.

### Footprint copper graphics now name their own primitive kind

`footprint graphic on a copper layer is unmodelled copper` survives **only as the fallback** for a
head outside the table. Six primitives and three text-bearing heads get their own sentence:

| Head | New message |
|---|---|
| `fp_line` / `fp_arc` / `fp_rect` / `fp_circle` / `fp_curve` / `point` | `footprint copper <kind> is unmodelled copper` |
| `fp_text`, `fp_text_box`, `property` | `footprint copper text has no envelope derivable from the board and is unsupported` |

**No verdict changes — every one of these already refused.** What changed is that the messages now
say which of three different unmet conditions each is waiting on: a graphic awaiting a model, text
awaiting ADR-0095's envelope, or a head nobody has classified. The footprint-scoped copper-text
spelling is deliberately **distinct from the root one**, so a caller aggregating on the root
string will under-count.

**No token read from a board is ever interpolated into any of these messages.** Every one is a
literal selected by lookup from a closed table — a privacy property, not a stability promise.

## 6. Internal routing gains multi-pin nets and one-branch repair, behind no new surface

Two substantial routing changes land in this release. **Neither adds, removes or changes an MCP
tool, a CLI command, a schema, a persisted record, a job, an external-document contract, an apply
path, an editor surface or a board-write authority.** They are listed here so the audit is
complete rather than selective, and because a deployer reading the CHANGELOG will want to know
whether they can be observed. They cannot, except as more requests succeeding.

- **Bounded multi-pin local lattices.** Internal negotiated routing now admits **2–32
  selected-layer pads per net**, preserving each request's own lattice origin while retaining one
  common signal layer and grid step. Exact legacy two-pad identities remain pinned, so an existing
  two-pad request produces the byte-identical candidate it did at 0.11.0. Malformed 1- or 33-pad
  requests refuse before router work. Complete pad connectivity, trusted endpoint binding,
  deterministic replay and the mandatory whole-set physical-clearance gate remain atomic. `B-140`
  measured the intended admission change on the fixed `B-088` population: **16 of 20** boards are
  admitted and all 16 reach a complete-allocation physical-clearance trigger, while the other four
  cannot form a two-request envelope. **None of those 16 contains a violating two-pad target**,
  which names the capability added rather than a quality result
  ([ADR-0126](../adr/0126-negotiated-routing-admits-bounded-multi-pin-nets-on-request-local-lattices.md),
  `D-233`, `D-234`, `R-184`, `SEC-169`, `SEC-170`, `B-140`,
  [issue #90](https://github.com/seunghyukchoe/copper-mcp/issues/90)).
  ADR-0055 is **partially superseded** by this: only its first Decision bullet's two-pad and
  single-world-origin request shape. Its bounded coordinator, deterministic ledger,
  immutable-policy identity, cancellation behaviour and separate physical-clearance authority
  remain in force.
- **Opt-in one-branch local repair.** Repair can now replace exactly one **physically responsible
  branch** of a 3–32-pad candidate, deriving private capability-bound provenance from the first
  deterministic pair that reproduces the whole-set violation, blocking every unselected target
  branch outside its existing attachment endpoints, and preserving every unselected path and
  non-derived candidate field. Eligible two-pad targets retain precedence and their prior
  identities. Topology, accounting, budget, validator, cancellation and final-clearance refusals
  discard the reconstructed candidate and all repair evidence **atomically**
  ([ADR-0127](../adr/0127-negotiated-repair-replaces-one-proven-responsible-branch.md), `D-235`,
  `R-185`, `SEC-171`, `SEC-172`).
- The custom-router seam additionally **rejects and replay-compares `fill_binding`**, preventing
  unverified fill provenance from being attached to a re-hashed candidate.

**Stated as a non-claim, because the evidence boundary is narrower than the capability.**
Synthetic 3-pad and 32-pad fixtures prove the repair capability; `B-140` itself ran **without**
repair enabled, so [issue #90](https://github.com/seunghyukchoe/copper-mcp/issues/90) remains open
for a predeclared repair-enabled held-out differential. **No routing-quality claim follows from
either change.** The mutation harness kills 35/35 attempts to weaken responsibility, provenance,
untouched-path preservation, accounting, cancellation or final physical publication, with zero
survivors — that is a claim about the tests, not about route quality.

## 7. The first live-editor observation, and why it changes nothing you deploy

A new read-only probe, `scripts/probe_live_text_shapes.py`, produced this project's first
observation of a **real running KiCad editor** through CopperMCP's own IPC transport, together
with the ADR-0095 text-to-shape measurement (`B-138`, `B-139`, `D-231`, `R-182`, `SEC-168`;
roadmap M3 entry criterion E3). It is an instrument, not a feature: it is gated behind
`COPPER_MCP_ALLOW_LIVE_IPC`, has no write path, and **refuses loudly, publishing no artifact,
without a live session**.

**No deployment action follows from the measurement.** ADR-0095's copper-text refusal is
unchanged, no shipped code path consumes anything measured here, and no conversion, routing, DRC,
apply or board-write behaviour moves. The measurement moved exactly one of ADR-0095's five exit
conditions and only partially — glyph extents become a *read* value over a live session — and it
**refuted** the lead that motivated the probe: `GetTextAsShapes` renders `${…}` literally and
carries no document reference. `D-231` is `Proposed`, with the recommendation being that live
evidence may **not** bind an offline conversion.

**One deployment action does follow, and it is an operator action rather than a code one.**
`SEC-168` records what enabling KiCad's IPC API server actually widens on a workstation, measured
rather than read from documentation: a **Unix domain socket** at `/tmp/kicad/api.sock` at mode
`srwxr-xr-x`, with **no TCP listener created** — so this is local-only and does not expose the
machine to the network. Within the owning uid there is **no authentication whatsoever**.
`KICAD_API_TOKEN` is **not** an access control and must not be read as one: it is a per-instance
`KIID` the server stamps into replies so a client can detect that KiCad restarted. It identifies
the *server* to the client, never the client to the server.

The consequence an operator must act on: **the same socket that answers read-only calls also
exposes `create_items`, `update_items`, `delete_items` and the commit primitives.** Any process
running as that user can modify the open board, and the operator's only signal is the editor's own
undo stack. CopperMCP's own transport is gated, read-only and redacting — but **those are
properties of this client, not of the socket**, and no other client on the machine is bound by
them. **Recommendation: the IPC server is not a setting to leave on. Disable it when a live
session is not actively needed.** The exposure lasts exactly as long as the server does.

`R-182` additionally records that `B-138`/`B-139` are the first evidence in this repository that
**cannot be replayed from committed bytes** — they exist only because a particular editor was
running on a particular machine at a particular moment. A later reader must not treat these
numbers the way this repository's offline corpus numbers may be treated.

## 8. Changes that are real but reach no caller

Each of these has a `0.12.0` CHANGELOG entry and is listed here so the audit is complete rather
than selective. None requires deployment action.

- **A closed, read-only root-`zone` field census instrument** fixes the next measurement behind
  `B-137`'s two zone-container refusals. Its selection commitment is deliberately **unassigned**
  until the exact digest-bound cohort is restored, so both the measurement API and the CLI
  **refuse instead of publishing a synthetic result**. No artifact or benchmark row is added, and
  it accepts no zone field (`D-232`, `R-183`,
  [issue #231](https://github.com/seunghyukchoe/copper-mcp/issues/231)).
- **The `B-015` live editor-context and `B-026` live layered-route benchmark runners execute
  against the current closed contracts again.** The editor-context replay no longer sends the
  retired snapshot field; the layered replay obtains its opaque session revision from a
  same-process live observation of the fake editor instance instead of constructing the retired
  `sha256:` form from ambient configuration. Both keep explicit live-IPC opt-in, verify fake-client
  closure, and publish no editor identity, token, board text, geometry or new production
  capability claim. These are benchmark runners with no MCP tool, CLI or apply peer.

## 9. What this release explicitly does not change

Stated as non-claims, because an absent entry and a verified absence are not the same thing:

- **no schema version moves** — Board IR stays `0.4.0`; Scene, router and candidate versions are
  unmoved, `schemas/` has no diff, and no published schema file's accepted set changes;
- **no response version moves** — the four 0.10.0 moved (`preview_route` `1.1`,
  `preview_layered_route` `1.1`, `preview_placement` `0.2.0`, placement candidates `0.1.0`) stay
  where 0.10.0 left them, and no preview, bundle or durable-job response gains or loses a field;
- **no MCP tool is added or removed.** A client enumerating tools sees the same list it saw at
  0.11.0;
- **no dependency range moves.** `mcp>=2.0.0,<2.2.0` is unchanged from 0.11.0, and 0.11.0 §1's
  guidance — including the `UnexpectedToolError` subclass warning for the 2.1 line — carries
  forward verbatim. `<2.2.0` remains the tested boundary, not a prediction;
- **no persisted artifact needs migration** — no snapshot, candidate, scene or job record;
- **no apply or write authority is added, widened or relaxed** anywhere. Apply flags, single-use
  tokens, revision checks and every board-write gate are untouched by §1, §2 and §6 alike. In
  particular, accepting a construct is not modelling it exactly, and is never an authorization —
  §2's stray-copper boards are converted **and unwritable**, which is the point rather than a
  limitation;
- **no refusal message text is promised** — §5 lists what moved precisely because the golden set
  is a regression detector rather than a contract. Continue branching on typed codes and locators,
  not prose;
- **no routing-quality claim** follows from §6. `B-140` is an admission measurement on a fixed
  population, explicitly not a quality result, and the repair path's own held-out differential is
  still outstanding under issue #90;
- **no electrical, SI, PI, EMC, thermal, fabrication or hardware claim** is made by any entry;
- **no exact-geometry claim** follows from §1 or §2. Both are deliberate approximations with
  proven **direction**: §1's outline is never larger than drawn, §2's copper envelope is never
  smaller. Neither is equal to the drawn shape, and §3's two new keys exist so a caller can tell;
- **no copper-text support** follows from §7. ADR-0095's refusal is unchanged, and the six public
  boards that now refuse behind §1's gate refuse at exactly that wall.

## 10. CI and release-operator behavior

Only operators carrying the upstream workflows or running `make check` are affected. Everything
0.11.0 §9 said still holds. Three things move:

- **`.github/ci-budget-calibration.json` is re-recorded at this boundary**, per its own `update`
  rule, because the suite grew again — from 3,583 tests at the v0.11.0 boundary to **3,799**
  collected. **No budget is raised**: CI remains 120 minutes, release verification 120, release
  publication 10. Re-record from `success` conclusions only.
- **The half-rule margin narrowed sharply, and an operator should read this rather than skim it.**
  The worst `ci.yml:test` leg moved from **2,332 s to 3,302 s (+41.6 %)** in one wave — the largest
  single-boundary jump this file has recorded. The half rule needs 110.07 min against the
  120-minute ceiling, so it clears with **9.93 minutes of headroom**: 3,302 s is **91.7 %** of the
  3,600 s limit. **The longest duration in the file changed hands at this boundary**: `ci.yml:test`
  is now the binding measurement rather than `release.yml:verify`, which shrank slightly to
  2,325 s because the hosted release gate skips the real-KiCad nodes the matrix legs run. A
  further ~9 % growth in the worst leg breaches the rule. **The next release wave to grow this job
  must re-measure it first and expect to confront the ceiling rather than re-record past it.**
- **`scripts/check_schema_sets.py` gains `v0.12.0` in `RELEASE_TAGS`.** During the cut this is the
  one listed tag that does not yet exist; every earlier listed tag must already exist, and any
  repository tag not listed still fails.

The release environment must still install `.[dev,security]` — `pip-audit` is in the `security`
extra, not `dev`.

## 11. Deployment checklist

Before switching traffic to 0.12.0:

- **workarounds that suppressed, alerted on or routed around `Edge.Cuts` outline-arc and
  footprint-copper-graphic conversion refusals are retired**, and no code path treats a successful
  conversion of a previously refused board as unexpected;
- **any placement caller handles a new `unsupported_geometry` refusal** on boards whose outline is
  approximated — this is the one previously-answered request that now errors, and it is reachable
  only on boards §1 newly converts. Do **not** reorder geometry checks ahead of the snapshot
  staleness check;
- **any consumer that treated a netless `Segment` as necessarily a net tie is corrected** — stray
  footprint copper now produces netless segments too. Branch on
  `footprint_copper_graphic_envelope_count`, not on the absence of a net;
- **any deployment that assumed "converts ⟹ applyable" is corrected** for boards carrying stray
  footprint copper: they convert and route, and cannot be written back;
- **callers near their `max_total_vertices` ceiling re-check it** — copper-graphic vertices are
  now charged against that budget;
- clients reading `unmodelled_counts` as a fixed-size map widen it from nine keys to **eleven**,
  and stop treating every member as a cardinality: `outline_inward_deviation_nm` is a
  **distance**, and `footprint_copper_graphic_envelope_count` discloses an **approximation**
  rather than an erasure;
- **anything matching `Edge.Cuts outline arcs, circles and curves are unsupported` or
  `Edge.Cuts outline arcs, circles, polygons and curves are unsupported` is retired** — both
  strings are gone, replaced by per-primitive sentences;
- anything matching `footprint graphic on a copper layer is unmodelled copper` is retired in
  favour of the typed code — it now survives only as the fallback for an unclassified head, and
  the footprint-scoped copper-text spelling is distinct from the root one;
- **operators who enabled KiCad's IPC API server disable it when a live session is not actively
  needed** (§7, `SEC-168`) — the socket is a full-privilege mutation surface with no
  authentication within the owning uid, and `KICAD_API_TOKEN` is not an access control;
- **no Board IR re-conversion is scheduled for this release** — if one is pending it is 0.9.0's;
- release operators keep `.github/ci-budget-calibration.json` synchronized with successful hosted
  durations, install `.[dev,security]`, and **treat §10's 9.93-minute half-rule headroom as the
  standing watch item for the next wave**.
