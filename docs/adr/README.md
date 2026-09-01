# Architecture Decision Records

ADRs record durable decisions and their tradeoffs. They are immutable after acceptance except for
status and links to superseding records.

## Adding an ADR

1. Copy [`template.md`](template.md) and assign the next unused number — currently **0130**.
2. Fill in `Status`, `Date`, `Owners`, and `Related` as bullets at the top, before `## Context`.
3. Link the ADR from the [decision ledger](../ledgers/decision-ledger.md) in the same pull request.

Numbers are never reused, including for an ADR that is withdrawn before merge. Allocate the number
in the pull request that lands the ADR, not before, so two concurrent branches cannot claim the same
one. See the [ledger ID convention](../ledgers/README.md#allocating-ids) for the same rule applied to
ledger entries.

`scripts/check_adr_numbers.py` enforces this mechanically, in `make lint` and in CI. It fails the
build when two files claim one number, when an ADR's own `# ADR-NNNN:` heading disagrees with its
filename, when an ADR is missing from the index below or indexed twice, when an index row points at
a file that does not exist, and when the advertised next unused number above is not the highest
allocated plus one. Gaps are reported as information and never fail. Keeping the next number on one
line is deliberate: two branches that both allocate it now conflict textually, so Git refuses the
merge instead of accepting it.

**0128 is a live claim, not a gap.** It is held by [#242](https://github.com/seunghyukchoe/copper-mcp/pull/242),
open on this same base, so ADR-0129 stepped over it rather than racing it — rule 1 of the
[ledger ID convention](../ledgers/README.md#allocating-ids) applied to ADR numbers, and the fourth
consecutive round in which stepping over a live claim costs nothing when the claim lands. The
advertised next unused number is **0130** for the same reason: if #242 lands first it will have
written **0129** on that one line, and Git will refuse the merge textually instead of accepting
two ADRs numbered 0129. If #242 is abandoned, 0128 becomes a permanent gap under rule 2.

**Known gaps:** there is no ADR-0027, ADR-0082, ADR-0083, ADR-0085, or ADR-0086. Every
one of them is **spent, not free**. Recycling a number would silently repoint every external citation of the
draft that reserved it at an unrelated decision, which is the exact failure the never-reuse rule
exists to prevent.

- **0027** was allocated on a branch whose ADR never landed.
- **0082** was allocated by the branch that first wrote the net-tie copper decision. That branch sat
  ungated while ADR-0084 and later ADR-0087 each took a number above it, and when the work finally
  landed it landed as [ADR-0092](0092-net-tie-copper-as-netless-obstacle.md) — a new number, not the
  one it had reserved. **0083** was claimed by a branch that has not landed, and is spent on the
  same terms whether or not it ever does.
- **0085** and **0086** were claimed by two branches still open when ADR-0088 landed, so ADR-0088
  took a number above both rather than the next one.

- **0105** was a live claim held by the branch for [issue #172](https://github.com/seunghyukchoe/copper-mcp/issues/172) while the records for [issue #164](https://github.com/seunghyukchoe/copper-mcp/issues/164) (0106) and [issue #110](https://github.com/seunghyukchoe/copper-mcp/issues/110) (0107) stepped over it. It has since landed with its own record, so it is not a gap — the third consecutive round in which stepping over a live claim cost nothing when the claim landed.

**No other number is a gap.** This section has repeatedly carried notes recording a number as a
"live claim" held by an open branch — 0081, 0089, 0090, 0092, 0094, 0097, and 0099 through 0103 all
appeared here that way, several of them contested by two branches at once. Every one has since
landed from its own branch, and this sentence is the correction those notes anticipated. The
convention that produced them is rule 1 of the
[ledger ID convention](../ledgers/README.md#allocating-ids): a branch that cannot see another
branch's pre-assigned number steps **over** it rather than racing it, and records that it did.
Keeping the next-unused number on one line above is the textual safety net — two branches that both
allocate it conflict in Git rather than merging cleanly into a duplicate.

That safety net is not hypothetical, and this file is the evidence. Between 2026-08-08 and
2026-08-13 the notes above accumulated as five overlapping rewrites of one paragraph, each landing
without a textual conflict because they did not overlap line-for-line: one truncated mid-sentence,
one contradicted the next line about ADR-0081, one named ADR-0089 as a gap while the record existed
and was indexed two lines below, and three separate summary sentences stood under the index at
once, disagreeing about the record count. The text above is the union of their facts with every
contradiction resolved against the files actually present in this directory.
**`scripts/check_adr_numbers.py` is the count to trust, not a sentence here.**

**How 0066 through 0068 came to be three records:** three concurrent branches each created an
`ADR-0066` — the atomic route bundle preview, ordered-layer routing, and route-aware placement
ranking. Their filenames differed, so Git merged all three without a conflict and nothing detected
the collision. Two were renumbered by hand after the fact, to 0067 and 0068. This is exactly what
`scripts/check_adr_numbers.py` now refuses.

## Status vocabulary

- **Accepted** — the decision is in force. This is the status of every ADR below.
- **Superseded** — replaced by a later ADR, which is named in this index and in the ADR itself. No
  ADR currently carries this status; where a later ADR widens an earlier one's scope, the earlier
  ADR's conditions are recorded as satisfied rather than the ADR being retired.
- **Proposed** — under discussion, not yet in force.

An ADR whose status carries a qualifier is noted in the table. A qualifier narrows the decision; it
never silently widens it.

## Index

| ADR | Title | Status |
|---|---|---|
| [0001](0001-candidate-first.md) | Candidate-first mutation model | Accepted |
| [0002](0002-mcp-adapter.md) | MCP is an external adapter | Accepted |
| [0003](0003-python-reference-core.md) | Python reference core with Rust-ready contracts | Accepted |
| [0004](0004-authoritative-kicad-drc.md) | Authoritative KiCad CLI DRC gate | Accepted |
| [0005](0005-canonical-board-ir.md) | Canonical integer Board IR v0.1 | Accepted |
| [0006](0006-bounded-deterministic-astar.md) | Bounded deterministic A* as the first routing reference | Accepted |
| [0007](0007-disposable-kicad-candidate-snapshot.md) | Disposable KiCad snapshots for route-candidate validation | Accepted |
| [0008](0008-candidate-bound-kicad-drc.md) | Candidate-bound authoritative KiCad DRC evidence | Accepted |
| [0009](0009-non-mutating-route-preview.md) | Bounded non-mutating route preview as the first public routing surface | Accepted |
| [0010](0010-board-ir-inspection-service.md) | Read-only Board IR inspection and a shared request boundary | Accepted |
| [0011](0011-existing-copper-obstacles.md) | Model existing copper as exact rectangular obstacles | Accepted |
| [0012](0012-via-obstacles.md) | Through vias as selected-layer obstacles | Accepted |
| [0013](0013-polygon-zone-obstacles.md) | Conservative polygon zone-boundary obstacles | Accepted |
| [0014](0014-canonical-circuit-intent.md) | Canonical circuit intent and deterministic schematic rendering | Accepted |
| [0015](0015-bounded-circuit-schematic-delivery.md) | Bounded Circuit Intent schematic delivery | Accepted |
| [0016](0016-same-net-attachment.md) | Attach to existing same-net copper and complete partial routes | Accepted |
| [0017](0017-diagonal-segment-envelopes.md) | Conservative integer envelopes for diagonal foreign copper | Accepted |
| [0018](0018-diagonal-attachment-cores.md) | Chained integer squares as the core of diagonal attachment copper | Accepted |
| [0019](0019-multi-pin-component-merging.md) | Route multi-pin nets by deterministic component merging | Accepted |
| [0020](0020-via-aware-connectivity.md) | Treat same-net through vias as connectivity joints | Accepted |
| [0021](0021-zone-fill-authority.md) | Trust poured copper only against a fresh KiCad refill | Accepted |
| [0022](0022-circuit-scene-observation.md) | Observe a board as a semantic scene, with its text held at arm's length | Accepted |
| [0023](0023-deterministic-board-render.md) | Render a board deterministically, and only as an advisory aid | Accepted |
| [0024](0024-placement-intent-and-legalization.md) | Typed placement intent, validated by a deterministic legalizer | Accepted |
| [0025](0025-file-level-candidate-apply.md) | Apply a route candidate by splicing bytes, not by rewriting a board | Accepted (mutating path added 2026-08-04) |
| [0026](0026-first-class-footprints-in-board-ir.md) | Make footprints revision-bound Board IR objects before moving them | Accepted |
| — | *0027 is deliberately unused; see **Known gaps** above.* | — |
| [0028](0028-revision-bound-scene-route-references.md) | Make Circuit Scene net references directly actionable for routing | Accepted |
| [0029](0029-read-only-kicad-ipc-observer.md) | Add a redacted, read-only KiCad IPC observer | Accepted |
| [0030](0030-live-ipc-circuit-scene-binding.md) | Bind a bounded KiCad IPC snapshot to Circuit Scene | Accepted |
| [0031](0031-live-ipc-route-proposal.md) | Keep live KiCad route proposals read-only and revision-bound | Accepted |
| [0032](0032-live-placement-proposal.md) | Keep live placement proposals read-only and revision-bound | Accepted |
| [0033](0033-live-editor-context.md) | Keep live editor context read-only and revision-bound | Accepted |
| [0034](0034-source-preserving-placement-candidates.md) | Keep placement candidate rendering source-preserving and subset-bound | Accepted |
| [0035](0035-internal-layered-search-oracle.md) | Keep the layered A* search seam internal until board fidelity exists | Accepted |
| [0036](0036-board-ir-layered-proposal-adapter.md) | Bind the layered search oracle to a narrow Board IR proposal contract | Accepted |
| [0037](0037-layered-kicad-serialization.md) | Render layered route proposals into disposable KiCad bytes | Accepted |
| [0038](0038-layered-candidate-drc-evidence.md) | Candidate-bound KiCad DRC for layered proposals | Accepted |
| [0039](0039-fill-aware-routing-obstacles.md) | Freshness-bound fill islands as routing obstacles | Accepted |
| [0040](0040-public-fill-routing-provenance.md) | Advertise freshness-bound fill provenance on routed previews | Accepted |
| [0041](0041-routing-safety-remediation.md) | Close layered and freshness-bound routing safety gaps | Accepted |
| [0042](0042-public-layered-route-preview.md) | Public, candidate-only layered route preview | Accepted |
| [0043](0043-durable-routing-job-ledger.md) | Durable routing-job ledger before protocol tasks | Accepted for the internal lifecycle subset |
| [0044](0044-live-layered-route-proposal.md) | Live layered route proposals are session- and snapshot-bound | Accepted, with a 2026-08-05 amendment superseding only the HMAC derivation |
| [0045](0045-layered-candidate-topology-verifier.md) | Verify layered candidate topology before serialization | Accepted |
| [0046](0046-routing-worker-leases-and-tasks-deferral.md) | Execute durable routing jobs with bounded leases; defer MCP Tasks | Accepted |
| [0047](0047-redacted-candidate-manifest-persistence.md) | Persist redacted candidate manifests, not route geometry | Accepted |
| [0048](0048-durable-routing-request-result-export.md) | Durable layered routing request, result, and export boundary | Accepted |
| [0049](0049-batched-one-steiner-ordering.md) | Add a bounded one-Steiner topology ordering policy | Accepted |
| [0050](0050-public-layered-route-drc-evidence.md) | Expose opt-in DRC evidence for file-backed layered proposals | Accepted |
| [0051](0051-conservative-spatial-index.md) | Narrow exact obstacle queries with a conservative spatial index | Accepted |
| [0052](0052-in-toto-candidate-drc-statement.md) | Emit candidate DRC as a redacted in-toto Statement payload | Accepted |
| [0053](0053-private-placement-candidate-drc.md) | Bind the supported placement subset to private KiCad DRC | Accepted |
| [0054](0054-review-boundary-hardening.md) | Close review-bot boundary gaps in routing and live observation | Accepted |
| [0055](0055-bounded-negotiated-congestion.md) | Add a bounded negotiated-congestion coordinator | Accepted (request-shape clause superseded in part by 0126) |
| [0056](0056-kicad-schematic-parity.md) | Verify bounded Circuit Intent schematic parity | Accepted |
| [0057](0057-front-back-footprint-observation.md) | Observe bounded front/back footprint poses | Accepted |
| [0058](0058-rectangular-courtyard-legality.md) | Check same-side rectangular courtyard legality | Accepted |
| [0059](0059-separately-authorized-placement-apply.md) | Separately authorize bounded placement application | Accepted |
| [0060](0060-public-placement-drc-evidence.md) | Expose opt-in placement DRC evidence through the file-backed preview | Accepted |
| [0061](0061-post-placement-observation.md) | Observe post-placement state through one captured scene and DRC context | Accepted |
| [0062](0062-stationary-padless-courtyard-envelope.md) | Keep padless courtyards in placement collision evidence | Accepted |
| [0063](0063-ipc-parser-deadline.md) | Carry live IPC deadlines into bounded board parsing | Accepted |
| [0064](0064-policy-bound-initial-negotiated-order.md) | Bind a closed routing-policy decision to the initial negotiated order | Accepted |
| [0065](0065-orthogonal-courtyard-chains.md) | Observe and legalize bounded orthogonal courtyard chains | Accepted |
| [0066](0066-atomic-route-bundle-preview.md) | Publish a composed route bundle only as one all-or-nothing read-only plan | Accepted |
| [0067](0067-route-aware-placement-ranking.md) | Keep route-aware placement ranking private, bounded, and opt-in | Accepted |
| [0068](0068-bounded-ordered-layer-routing.md) | Keep ordered-layer routing bounded and non-serializing | Accepted |
| [0069](0069-operator-gated-live-ipc-observation.md) | Gate live KiCad IPC on an operator opt-in and establish document type at the observer | Accepted |
| [0070](0070-layered-fill-aware-obstacles.md) | Shrink a layered zone envelope only against proved fill | Accepted |
| [0071](0071-authoritative-schematic-erc.md) | Authoritative KiCad schematic ERC and generated-schematic round trip | Accepted |
| [0072](0072-conservative-arc-track-envelopes.md) | Model foreign arc tracks as conservative integer envelopes rather than refusing the board | Accepted |
| [0073](0073-declared-negotiation-policy-slots.md) | Declare negotiation strategy as three separately digest-bound policy slots | Accepted |
| [0074](0074-live-ipc-one-undo-commit-apply.md) | Gate live editor mutation on its own consent, and ship its preconditions before it | Accepted |
| [0075](0075-courtyard-oracle-parity.md) | Model KiCad's courtyard cache, and make courtyard legality three-valued | Accepted |
| [0076](0076-segment-assembled-edge-cuts-outline.md) | Assemble the board outline from Edge.Cuts segments, and never repair it | Accepted (identity clause superseded by 0087) |
| [0077](0077-roundrect-corner-radius-rounding.md) | Roundrect corner radii round up, by geometry role | Accepted |
| [0078](0078-netless-copper-as-obstacle.md) | Net-0 copper is an obstacle with no connectivity contribution | Accepted |
| [0079](0079-discriminated-configurable-parse-budgets.md) | Make the structural parse budgets operator-settable, and name the one that refused | Accepted |
| [0080](0080-chamfered-and-circular-courtyards.md) | Bracket chamfered and circular courtyards instead of widening them | Accepted |
| [0081](0081-incremental-retention-and-bounded-ripup-window.md) | Reconstruct the congestion ledger incrementally and bound rip-up by a spatial window | Accepted |
| — | *0082 and 0083 are deliberately unused; see **Known gaps** above.* | — |
| [0084](0084-authoritative-source-to-board-parity.md) | Authoritative source-to-board parity via a board-eligible intent projection | Accepted |
| — | *0085 and 0086 are deliberately unused; see **Known gaps** above.* | — |
| [0087](0087-composite-native-identity-for-assembled-outlines.md) | Name an assembled Edge.Cuts outline by the sorted set of its members' own uuids | Accepted |
| [0088](0088-complete-or-withheld-scene-kinds.md) | A truncated scene withholds whole kinds instead of emptying them | Accepted |
| [0089](0089-region-scoped-obstacle-model.md) | Scope the obstacle model to a routing region, and split the budget that was counting three things | Accepted |
| [0090](0090-root-level-board-groups.md) | A root board group is organisation, accepted and counted rather than modelled | Accepted |
| [0091](0091-attaching-pad-zone-connect-overrides.md) | Accept the pad zone-connection overrides that attach, refuse the one that detaches | Accepted |
| [0092](0092-net-tie-copper-as-netless-obstacle.md) | Net-tie copper is a netless obstacle, and the tie is never a connectivity claim | Accepted |
| [0093](0093-actionable-off-grid-refusals.md) | An off-grid refusal carries the pad, the pitch and the exact miss | Accepted |
| [0094](0094-root-board-properties-as-metadata.md) | A root board property is a text variable, accepted and counted rather than modelled | Accepted |
| [0095](0095-copper-text-has-no-derivable-envelope.md) | Copper text has no envelope derivable from the board, and refuses under its own name | Accepted |
| [0096](0096-edge-connector-pads-convert-as-smd.md) | An edge-connector pad converts as an SMD pad, and the discarded token is counted | Accepted |
| [0097](0097-courtyard-layer-decides-the-side.md) | A courtyard keeps out on the layer it is drawn on, not on its footprint's side | Accepted |
| [0098](0098-reproducible-mutation-evidence.md) | A mutation claim is evidence only if the repository can re-run it | Accepted |
| [0099](0099-pad-fabrication-properties-and-named-pad-refusals.md) | A pad refusal names the field it refused, and seven of eight fabrication properties convert | Accepted |
| [0100](0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md) | A custom pad has a derivable envelope and nowhere in Board IR to put it | Accepted |
| [0101](0101-fill-currency-is-not-in-the-document.md) | Fill currency is not in the board file, so keep the model and gate the shrink | Accepted |
| [0102](0102-an-evaluation-must-observe-a-permit.md) | A refusal evaluation must observe a permit, and prove it kept observing one | Accepted |
| [0103](0103-a-candidate-records-the-model-that-produced-it.md) | A candidate records the obstacle model that produced it, and a replay refuses every other one | Accepted |
| [0104](0104-fill-vertex-budget-behind-a-parse.md) | The fill-vertex budget sits behind a parse, and is calibrated as what it is | Accepted |
| [0105](0105-a-schema-version-moves-with-its-accepted-set.md) | A schema version moves with its accepted set, and `0.2.0` is frozen where it stands | Accepted |
| [0106](0106-layered-fill-authority-is-public-and-bound.md) | A layered candidate records its obstacle model before the layered seam may reach one | Accepted |
| [0107](0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md) | An aggregator's repository licence does not govern the data it aggregated | Accepted |
| [0108](0108-typed-refusal-at-the-single-layer-fill-seam.md) | The single-layer fill seam refuses malformed evidence under its own numbers | Accepted |
| [0109](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md) | A published DRC count carries the comparability it was taken with | Accepted |
| [0110](0110-placement-boundary-verdicts-bracket-kicad-parity.md) | Placement boundary verdicts bracket KiCad parity | Accepted |
| [0111](0111-custom-pad-anchor-and-envelope.md) | Carry custom-pad copper separately from its attachment anchor | Accepted |
| [0112](0112-external-route-candidates-enter-through-a-disposer.md) | External route candidates enter through a bounded disposer | Accepted |
| [0113](0113-external-route-patches-preserve-multi-pin-topology.md) | External route patches preserve multi-pin topology | Accepted |
| [0114](0114-external-candidates-continue-to-private-kicad-drc.md) | Accepted external candidates continue to private KiCad DRC | Accepted |
| [0115](0115-external-route-verification-is-a-versioned-read-only-mcp-boundary.md) | External route verification is a versioned read-only MCP boundary | Accepted |
| [0116](0116-layered-fill-islands-have-a-measured-source-boundary.md) | Layered fill islands have a measured source boundary | Accepted |
| [0117](0117-local-exact-repair-is-an-opt-in-verified-transaction.md) | Local exact repair is an opt-in verified transaction | Accepted (two-pad repair-target clause widened by 0127) |
| [0118](0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md) | Keep authoritative signoff closed until a bounded executor exists | Accepted |
| — | *0119 is a live parallel-branch claim and is not free; see **Adding an ADR** above.* | — |
| [0119](0119-a-signoff-claim-rests-on-repeated-agreement-from-a-registered-backend.md) | A sign-off claim rests on repeated agreement from a registered backend | Accepted |
| [0120](0120-withheld-apply-authority-has-a-closed-reason.md) | Withheld apply authority has one closed, non-echoing reason | Accepted |
| [0121](0121-a-refusal-is-an-answer-and-a-crash-is-not.md) | A refusal is an answer and a crash is not | Accepted |
| [0122](0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md) | The stackup is read per field, because three of its fields are not about Z | Accepted |
| [0123](0123-a-container-refusal-that-names-no-field-is-the-defect.md) | A container refusal that names no field is the defect, not the refusal | Accepted |
| [0124](0124-an-outline-arc-is-inscribed-and-a-cut-is-refused.md) | An outline arc is inscribed, and an arc that cuts into the board is refused | Accepted |
| [0125](0125-stray-footprint-copper-is-bounded-because-no-fill-rule-is-written-down.md) | Stray footprint copper is bounded by a box because no fill rule is written down | Accepted |
| [0126](0126-negotiated-routing-admits-bounded-multi-pin-nets-on-request-local-lattices.md) | Negotiated routing admits bounded multi-pin nets on request-local lattices | Accepted (multi-pin repair deferral satisfied by 0127) |
| [0127](0127-multi-pin-local-repair-replaces-one-proven-responsible-branch.md) | Multi-pin local repair replaces one proven responsible branch | Accepted |
| [0129](0129-a-live-apply-proves-a-matched-digest-not-exclusive-access.md) | A live apply proves a matched digest, not exclusive access | Proposed |

One hundred and twenty-seven numbers allocated, one hundred and twenty-two records, no duplicates — and
`scripts/check_adr_numbers.py` proves that last clause on every run rather than asserting it. Read
its output, not this sentence: three earlier revisions of it stood here at once, disagreeing about
the count, and each was stale by a landing or two before it was ever read. 0027, 0082, 0083, 0085
and 0086 are unused; see **Known gaps** above. 0107 was allocated over the live claims 0105 and
0106 rather than racing them, the mechanism the [ledger README](../ledgers/README.md) describes for
`D-`/`R-`/`B-` numbers. Both have since landed — 0106 with issue #164's record and 0105 with issue
#172's — so neither is a gap, and all of 0100 through 0119 are real records in the index above.

## Reading order

The ADRs are chronological, not thematic. To follow one arc, read it in this order:

- **Board IR and geometry** — 0005, 0011, 0012, 0013, 0017, 0018, 0026, 0051, 0070, 0076, 0077,
  0078, 0079, 0080, 0087, 0090, 0091, 0092, 0094, 0095, 0096, 0097, 0099, 0100, 0124.
- **Routing** — 0006, 0009, 0016, 0019, 0020, 0021, 0035, 0036, 0037, 0039, 0042, 0049, 0055, 0064,
  0066, 0068, 0070, 0072, 0073, 0075, 0081, 0089, 0093, 0101, 0103, 0104, 0106, 0108, 0117,
  0126, 0127.
- **Candidate validation and DRC** — 0004, 0007, 0008, 0038, 0050, 0052, 0053, 0060, 0075, 0109.
- **Circuit Intent and schematic verification** — 0014, 0015, 0056, 0071, 0084.
- **Placement** — 0024, 0034, 0057, 0058, 0059, 0061, 0062, 0065, 0067, 0075, 0097, 0110.
- **Circuit Scene and rendering** — 0010, 0022, 0023, 0028, 0088.
- **Live KiCad IPC** — 0029, 0030, 0031, 0032, 0033, 0044, 0063, 0069, 0074, 0129.
- **Durable jobs and persistence** — 0043, 0046, 0047, 0048.
- **Mutation and authorization** — 0001, 0025, 0059, 0074.
- **Evidence, evaluation, and review boundaries** — 0041, 0052, 0054, 0098, 0102, 0105, 0107, 0109,
  0118, 0119, 0121.

- [ADR-0001: Candidate-first mutation model](0001-candidate-first.md)
- [ADR-0002: MCP is an external adapter](0002-mcp-adapter.md)
- [ADR-0003: Python reference core with Rust-ready contracts](0003-python-reference-core.md)
- [ADR-0004: Authoritative KiCad CLI DRC gate](0004-authoritative-kicad-drc.md)
- [ADR-0005: Canonical integer Board IR v0.1](0005-canonical-board-ir.md)
- [ADR-0006: Bounded deterministic A* reference](0006-bounded-deterministic-astar.md)
- [ADR-0007: Disposable KiCad candidate snapshots](0007-disposable-kicad-candidate-snapshot.md)
- [ADR-0008: Candidate-bound authoritative KiCad DRC evidence](0008-candidate-bound-kicad-drc.md)
- [ADR-0009: Bounded non-mutating route preview](0009-non-mutating-route-preview.md)
- [ADR-0010: Read-only Board IR inspection service](0010-board-ir-inspection-service.md)
- [ADR-0011: Existing copper as exact rectangular obstacles](0011-existing-copper-obstacles.md)
- [ADR-0012: Through vias as selected-layer obstacles](0012-via-obstacles.md)
- [ADR-0013: Conservative polygon zone-boundary obstacles](0013-polygon-zone-obstacles.md)
- [ADR-0014: Canonical circuit intent and deterministic schematic rendering](0014-canonical-circuit-intent.md)
- [ADR-0015: Bounded Circuit Intent schematic delivery](0015-bounded-circuit-schematic-delivery.md)
- [ADR-0016: Same-net attachment and partial-route completion](0016-same-net-attachment.md)
- [ADR-0017: Conservative integer envelopes for diagonal foreign copper](0017-diagonal-segment-envelopes.md)
- [ADR-0018: Chained integer squares as the core of diagonal attachment copper](0018-diagonal-attachment-cores.md)
- [ADR-0019: Route multi-pin nets by deterministic component merging](0019-multi-pin-component-merging.md)
- [ADR-0020: Treat same-net through vias as connectivity joints](0020-via-aware-connectivity.md)
- [ADR-0021: Trust poured copper only against a fresh KiCad refill](0021-zone-fill-authority.md)
- [ADR-0022: Observe a board as a semantic scene, with its text held at arm's length](0022-circuit-scene-observation.md)
- [ADR-0023: Render a board deterministically, and only as an advisory aid](0023-deterministic-board-render.md)
- [ADR-0024: Typed placement intent, validated by a deterministic legalizer](0024-placement-intent-and-legalization.md)
- [ADR-0025: Apply a route candidate by splicing bytes, not by rewriting a board](0025-file-level-candidate-apply.md)
- [ADR-0026: Make footprints revision-bound Board IR objects before moving them](0026-first-class-footprints-in-board-ir.md)
- [ADR-0028: Make Circuit Scene net references directly actionable for routing](0028-revision-bound-scene-route-references.md)
- [ADR-0029: Add a redacted, read-only KiCad IPC observer](0029-read-only-kicad-ipc-observer.md)
- [ADR-0030: Bind a bounded KiCad IPC snapshot to Circuit Scene](0030-live-ipc-circuit-scene-binding.md)
- [ADR-0031: Keep live KiCad route proposals read-only and revision-bound](0031-live-ipc-route-proposal.md)
- [ADR-0032: Keep live KiCad placement proposals read-only and revision-bound](0032-live-placement-proposal.md)
- [ADR-0033: Keep live editor context read-only and revision-bound](0033-live-editor-context.md)
- [ADR-0034: Keep placement candidate rendering source-preserving and subset-bound](0034-source-preserving-placement-candidates.md)
- [ADR-0035: Keep the layered A* search seam internal until board fidelity exists](0035-internal-layered-search-oracle.md)
- [ADR-0036: Bind the layered search oracle to a narrow Board IR proposal contract](0036-board-ir-layered-proposal-adapter.md)
- [ADR-0037: Render layered route proposals into disposable KiCad bytes](0037-layered-kicad-serialization.md)
- [ADR-0038: Candidate-bound KiCad DRC for layered proposals](0038-layered-candidate-drc-evidence.md)
- [ADR-0039: Freshness-bound fill islands as routing obstacles](0039-fill-aware-routing-obstacles.md)
- [ADR-0040: Advertise freshness-bound fill provenance on routed previews](0040-public-fill-routing-provenance.md)
- [ADR-0041: Close layered and freshness-bound routing safety gaps](0041-routing-safety-remediation.md)
- [ADR-0042: Public, candidate-only layered route preview](0042-public-layered-route-preview.md)
- [ADR-0043: Durable routing-job ledger before protocol tasks](0043-durable-routing-job-ledger.md)
- [ADR-0044: Live layered route proposals are session- and snapshot-bound](0044-live-layered-route-proposal.md)
- [ADR-0045: Verify layered candidate topology before serialization](0045-layered-candidate-topology-verifier.md)
- [ADR-0046: Execute durable routing jobs with bounded leases; defer MCP Tasks](0046-routing-worker-leases-and-tasks-deferral.md)
- [ADR-0047: Persist redacted candidate manifests, not route geometry](0047-redacted-candidate-manifest-persistence.md)
- [ADR-0048: Durable layered routing request, result, and export boundary](0048-durable-routing-request-result-export.md)
- [ADR-0049: Add a bounded one-Steiner topology ordering policy](0049-batched-one-steiner-ordering.md)
- [ADR-0050: Expose opt-in DRC evidence for file-backed layered proposals](0050-public-layered-route-drc-evidence.md)
- [ADR-0051: Narrow exact obstacle queries with a conservative spatial index](0051-conservative-spatial-index.md)
- [ADR-0052: Emit candidate DRC as a redacted in-toto Statement payload](0052-in-toto-candidate-drc-statement.md)
- [ADR-0053: Bind the supported placement subset to private KiCad DRC](0053-private-placement-candidate-drc.md)
- [ADR-0054: Close review-bot boundary gaps in routing and live observation](0054-review-boundary-hardening.md)
- [ADR-0055: Add a bounded negotiated-congestion coordinator](0055-bounded-negotiated-congestion.md)
- [ADR-0056: Verify bounded Circuit Intent schematic parity](0056-kicad-schematic-parity.md)
- [ADR-0057: Observe bounded front/back footprint poses](0057-front-back-footprint-observation.md)
- [ADR-0058: Check same-side rectangular courtyard legality](0058-rectangular-courtyard-legality.md)
- [ADR-0059: Separately authorize bounded placement application](0059-separately-authorized-placement-apply.md)
- [ADR-0060: Expose opt-in placement DRC evidence through the file-backed preview](0060-public-placement-drc-evidence.md)
- [ADR-0061: Observe post-placement state through one captured scene and DRC context](0061-post-placement-observation.md)
- [ADR-0062: Keep padless courtyards in placement collision evidence](0062-stationary-padless-courtyard-envelope.md)
- [ADR-0063: Carry live IPC deadlines into bounded board parsing](0063-ipc-parser-deadline.md)
- [ADR-0064: Bind a closed routing-policy decision to the initial negotiated order](0064-policy-bound-initial-negotiated-order.md)
- [ADR-0065: Observe and legalize bounded orthogonal courtyard chains](0065-orthogonal-courtyard-chains.md)
- [ADR-0066: Publish a composed route bundle only as one all-or-nothing read-only plan](0066-atomic-route-bundle-preview.md)
- [ADR-0067: Keep route-aware placement ranking private, bounded, and opt-in](0067-route-aware-placement-ranking.md)
- [ADR-0068: Keep ordered-layer routing bounded and non-serializing](0068-bounded-ordered-layer-routing.md)
- [ADR-0069: Gate live KiCad IPC on an operator opt-in and establish document type at the observer](0069-operator-gated-live-ipc-observation.md)
- [ADR-0070: Shrink a layered zone envelope only against proved fill](0070-layered-fill-aware-obstacles.md)
- [ADR-0071: Authoritative KiCad schematic ERC and generated-schematic round trip](0071-authoritative-schematic-erc.md)
- [ADR-0072: Conservative integer envelopes for foreign arc tracks](0072-conservative-arc-track-envelopes.md)
- [ADR-0073: Declare negotiation strategy as three separately digest-bound policy slots](0073-declared-negotiation-policy-slots.md)
- [ADR-0074: Gate live editor mutation on its own consent, and ship its preconditions before it](0074-live-ipc-one-undo-commit-apply.md)
- [ADR-0075: Model KiCad's courtyard cache, and make courtyard legality three-valued](0075-courtyard-oracle-parity.md)
- [ADR-0076: Assemble the board outline from Edge.Cuts segments, and never repair it](0076-segment-assembled-edge-cuts-outline.md)
- [ADR-0077: Roundrect corner radii round up, by geometry role](0077-roundrect-corner-radius-rounding.md)
- [ADR-0078: Net-0 copper is an obstacle with no connectivity contribution](0078-netless-copper-as-obstacle.md)
- [ADR-0079: Make the structural parse budgets operator-settable, and name the one that refused](0079-discriminated-configurable-parse-budgets.md)
- [ADR-0080: Bracket chamfered and circular courtyards instead of widening them](0080-chamfered-and-circular-courtyards.md)
- [ADR-0081: Reconstruct the congestion ledger incrementally and bound rip-up by a spatial window](0081-incremental-retention-and-bounded-ripup-window.md)
- [ADR-0084: Authoritative source-to-board parity via a board-eligible intent projection](0084-authoritative-source-to-board-parity.md)
- [ADR-0087: Name an assembled Edge.Cuts outline by the sorted set of its members' own uuids](0087-composite-native-identity-for-assembled-outlines.md)
- [ADR-0088: A truncated scene withholds whole kinds instead of emptying them](0088-complete-or-withheld-scene-kinds.md)
- [ADR-0089: Scope the obstacle model to a routing region, and split the budget that was counting three things](0089-region-scoped-obstacle-model.md)
- [ADR-0090: A root board group is organisation, accepted and counted rather than modelled](0090-root-level-board-groups.md)
- [ADR-0091: Accept the pad zone-connection overrides that attach, refuse the one that detaches](0091-attaching-pad-zone-connect-overrides.md)
- [ADR-0092: Net-tie copper is a netless obstacle, and the tie is never a connectivity claim](0092-net-tie-copper-as-netless-obstacle.md)
- [ADR-0093: An off-grid refusal carries the pad, the pitch and the exact miss](0093-actionable-off-grid-refusals.md)
- [ADR-0094: A root board property is a text variable, accepted and counted rather than modelled](0094-root-board-properties-as-metadata.md)
- [ADR-0095: Copper text has no envelope derivable from the board, and refuses under its own name](0095-copper-text-has-no-derivable-envelope.md)
- [ADR-0096: An edge-connector pad converts as an SMD pad, and the discarded token is counted](0096-edge-connector-pads-convert-as-smd.md)
- [ADR-0097: A courtyard keeps out on the layer it is drawn on, not on its footprint's side](0097-courtyard-layer-decides-the-side.md)
- [ADR-0098: A mutation claim is evidence only if the repository can re-run it](0098-reproducible-mutation-evidence.md)
- [ADR-0099: A pad refusal names the field it refused, and seven of eight fabrication properties convert](0099-pad-fabrication-properties-and-named-pad-refusals.md)
- [ADR-0100: A custom pad has a derivable envelope and nowhere in Board IR to put it](0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md)
- [ADR-0101: Fill currency is not in the board file, so keep the model and gate the shrink](0101-fill-currency-is-not-in-the-document.md)
- [ADR-0102: A refusal evaluation must observe a permit, and prove it kept observing one](0102-an-evaluation-must-observe-a-permit.md)
- [ADR-0103: A candidate records the obstacle model that produced it, and a replay refuses every other one](0103-a-candidate-records-the-model-that-produced-it.md)
- [ADR-0104: The fill-vertex budget sits behind a parse, and is calibrated as what it is](0104-fill-vertex-budget-behind-a-parse.md)
- [ADR-0106: A layered candidate records its obstacle model before the layered seam may reach one](0106-layered-fill-authority-is-public-and-bound.md)
- [ADR-0105: A schema version moves with its accepted set, and `0.2.0` is frozen where it stands](0105-a-schema-version-moves-with-its-accepted-set.md)
- [ADR-0107: An aggregator's repository licence does not govern the data it aggregated](0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md)
- [ADR-0108: The single-layer fill seam refuses malformed evidence under its own numbers](0108-typed-refusal-at-the-single-layer-fill-seam.md)
- [ADR-0109: A published DRC count carries the comparability it was taken with](0109-a-drc-count-carries-the-comparability-it-was-taken-with.md)
- [ADR-0110: Placement boundary verdicts bracket KiCad parity](0110-placement-boundary-verdicts-bracket-kicad-parity.md)
- [ADR-0111: Carry custom-pad copper separately from its attachment anchor](0111-custom-pad-anchor-and-envelope.md)
- [ADR-0112: External route candidates enter through a bounded disposer](0112-external-route-candidates-enter-through-a-disposer.md)
- [ADR-0113: External route patches preserve multi-pin topology](0113-external-route-patches-preserve-multi-pin-topology.md)
- [ADR-0114: Accepted external candidates continue to private KiCad DRC](0114-external-candidates-continue-to-private-kicad-drc.md)
- [ADR-0115: External route verification is a versioned read-only MCP boundary](0115-external-route-verification-is-a-versioned-read-only-mcp-boundary.md)
- [ADR-0116: Layered fill islands have a measured source boundary](0116-layered-fill-islands-have-a-measured-source-boundary.md)
- [ADR-0117: Local exact repair is an opt-in verified transaction](0117-local-exact-repair-is-an-opt-in-verified-transaction.md)
- [ADR-0118: Keep authoritative signoff closed until a bounded executor exists](0118-authoritative-signoff-stays-closed-until-a-bounded-executor-exists.md)
- [ADR-0119: A sign-off claim rests on repeated agreement from a registered backend](0119-a-signoff-claim-rests-on-repeated-agreement-from-a-registered-backend.md)
- [ADR-0120: Withheld apply authority has one closed, non-echoing reason](0120-withheld-apply-authority-has-a-closed-reason.md)
- [ADR-0121: A refusal is an answer and a crash is not](0121-a-refusal-is-an-answer-and-a-crash-is-not.md)
- [ADR-0122: The stackup is read per field, because three of its fields are not about Z](0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md)
- [ADR-0123: A container refusal that names no field is the defect, not the refusal](0123-a-container-refusal-that-names-no-field-is-the-defect.md)
- [ADR-0124: An outline arc is inscribed, and an arc that cuts into the board is refused](0124-an-outline-arc-is-inscribed-and-a-cut-is-refused.md)
- [ADR-0125: Stray footprint copper is bounded by a box because no fill rule is written down](0125-stray-footprint-copper-is-bounded-because-no-fill-rule-is-written-down.md)
- [ADR-0126: Negotiated routing admits bounded multi-pin nets on request-local lattices](0126-negotiated-routing-admits-bounded-multi-pin-nets-on-request-local-lattices.md)
- [ADR-0127: Multi-pin local repair replaces one proven responsible branch](0127-multi-pin-local-repair-replaces-one-proven-responsible-branch.md)
- [ADR-0129: A live apply proves a matched digest, not exclusive access](0129-a-live-apply-proves-a-matched-digest-not-exclusive-access.md)
