# Architecture Decision Records

ADRs record durable decisions and their tradeoffs. They are immutable after acceptance except for
status and links to superseding records.

## Adding an ADR

1. Copy [`template.md`](template.md) and assign the next unused number — currently **0100**.
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

**Known gaps:** there is no ADR-0027, ADR-0082, ADR-0083, ADR-0085, or ADR-0086.
ADR-0094 was a **live claim** held by an open branch (issue #140) when ADR-0095 and ADR-0096 were
allocated. Both stepped over it rather than racing it, exactly as the ledger convention's rule 1
prescribes, and it has since landed from its own branch (PR #150) as the root-board-properties
decision — this sentence is the correction the earlier note anticipated, and 0094 is not a gap.
ADR-0097 went the same way: it was a live claim held by open PR #154 when ADR-0098 was allocated,
so ADR-0098 stepped over it, and it has since landed as the courtyard-layer decision. Neither is a
gap; both notes are corrections that came due exactly as written.
**ADR-0099 is a live claim contested by two open branches at the time of writing, and this note is
the acknowledgement the convention asks for.** This branch (issue #152, `D-189`/`R-144`) and PR #158
(issue #153, `D-190`/`R-145`) both write `docs/adr/0099-*.md`. Neither stepped over the other
because neither could see the other when its number was assigned — the pre-assignment happened
off-branch. The tie-break is the ledger numbers, which do not collide and do order: `D-189` is lower
than `D-190`, so this branch lands first and keeps 0099, and #158 renumbers to 0100. Whichever lands
second resolves the textual conflict on the next-unused line above, which is exactly the safety net
rule 1 describes. If this branch is abandoned instead, 0099 is spent and #158 should still take
0100 rather than filling it.
ADR-0089 was listed here until this line was corrected: it landed from its own branch as the
region-scoped obstacle model and is not a gap. ADR-0092 was listed too, and has since landed from
its own branch as the net-tie netless-obstacle model; this sentence is that correction, exactly as
the note anticipated.
ADR-0027 was allocated on a branch whose ADR never landed. The 0081–0083 block was allocated by
concurrent branches that were still open when ADR-0084 landed: that branch took a number above all
of them rather than the next one, which is the rule the collision that produced 0066–0068 exists to
enforce. ADR-0081 has since landed from its own branch and is no longer a gap; 0082 and 0083 remain
spent. 0085 and 0086 went the same way — two branches were open alongside ADR-0088 when it landed, so
it took a number above both rather than the next one. 0090 was claimed the same way by a branch
still open when ADR-0091 (issue #124) landed, so that record took the number above it — but
ADR-0090 has since landed from its own branch and is no longer a gap, and ADR-0092 then took the
next number cleanly above both. Every unused number is deliberately left unused rather than
recycled, so that an external reference resolves to nothing rather than to an unrelated decision.
If a later branch lands its own record in one of these gaps, this note is what it corrects, exactly
as it just did for 0090.

Two corrections are folded into the list above rather than left standing, and this paragraph is
the **only** account of them — a second overlapping one was written while resolving this branch's
merge and is removed rather than left to become a fourth copy. ADR-0089 was named as a gap while
the record existed and was indexed two lines below. And three overlapping revisions of this
paragraph stood here until ADR-0091 landed, one truncated mid-sentence and one contradicting the
next line about ADR-0081; the surviving text above is the union of their facts, with every
contradiction resolved against the files actually present in this directory. Both were merge
residue from the concurrent-branch period the paragraph describes.

**ADR-0082 is now permanently spent rather than merely claimed.** It was allocated by the branch
that first wrote the net-tie copper decision. That branch sat ungated while ADR-0084 and later
ADR-0087 each took a number above it, and when the work finally landed it landed as
[ADR-0092](0092-net-tie-copper-as-netless-obstacle.md) -- a new number, not the one it had reserved.
Nothing will ever occupy 0082: recycling it would silently repoint every external citation of the
abandoned draft at a record it never described, which is the exact failure the never-reuse rule
exists to prevent. ADR-0083 remains claimed by a branch that has not landed and is spent on the same
terms whether or not it ever does.

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
| [0055](0055-bounded-negotiated-congestion.md) | Add a bounded negotiated-congestion coordinator | Accepted |
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

Ninety-seven numbers, ninety-two records, no duplicates — and `scripts/check_adr_numbers.py`
now proves that last clause on every run rather than asserting it. 0027, 0082, 0083, 0085 and
0086 are unused; see **Known gaps** above. The three stray summary sentences that stood here until
| [0098](0098-reproducible-mutation-evidence.md) | A mutation claim is evidence only if the repository can re-run it | Accepted |
| [0099](0099-pad-fabrication-properties-and-named-pad-refusals.md) | A pad refusal names the field it refused, and seven of eight fabrication properties convert | Accepted |

Ninety-nine numbers, ninety-four records, no duplicates — and `scripts/check_adr_numbers.py`
now proves that last clause on every run rather than asserting it (the previous revision of this
sentence said "ninety-six numbers, ninety records", stale by two landings at the time it was
read; the checker's own output is the count to trust). 0027, 0082, 0083, 0085 and
0086 are unused; 0097 was a live claim on an open branch when the sentence above it was written and
has since landed, so it is not a gap either. See
**Known gaps** above. The three stray summary sentences that stood here until
ADR-0088 landed were merge residue from the concurrent branches described above: Git accepted
three different rewrites of one paragraph because they did not overlap textually.

## Reading order

The ADRs are chronological, not thematic. To follow one arc, read it in this order:

- **Board IR and geometry** — 0005, 0011, 0012, 0013, 0017, 0018, 0026, 0051, 0070, 0076, 0087,
  0091, 0092, 0095, 0096, 0097, 0099.
- **Routing** — 0006, 0009, 0016, 0019, 0020, 0021, 0035, 0036, 0037, 0039, 0042, 0049, 0055, 0064,
  0066, 0070, 0073, 0075, 0089, 0093.
- **Candidate validation and DRC** — 0004, 0007, 0008, 0038, 0050, 0052, 0053, 0060, 0075.
- **Circuit Intent and schematic verification** — 0014, 0015, 0056, 0070.
- **Placement** — 0024, 0034, 0057, 0058, 0059, 0061, 0062, 0065, 0075, 0097.
- **Circuit Scene and rendering** — 0010, 0022, 0023, 0028, 0056.
- **Live KiCad IPC** — 0029, 0030, 0031, 0032, 0033, 0044, 0063, 0069, 0074.
- **Durable jobs and persistence** — 0043, 0046, 0047, 0048.
- **Mutation and authorization** — 0001, 0025, 0059, 0074.

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
