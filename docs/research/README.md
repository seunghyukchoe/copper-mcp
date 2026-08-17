# PCB autorouting research

**Snapshot date:** 2026-08-05

This package surveys current PCB autorouters and turns the evidence into an
implementation direction for CopperMCP. It is a research snapshot, not a claim
that every product feature, price, or repository release will remain current.

## How to read this directory

Each document is a **dated snapshot of external evidence**, not a maintained summary. It records
what was true, and what was licensable, when it was gathered — and it is not revised as the code
moves. Every entry below therefore states what the survey covers and carries its own
`Research date` or `Reviewed` line near the top; check that line before relying on a claim.

These documents exist because a major engineering slice starts from a current-literature pass with
licences and per-item implications, cited from that slice's ADR. That is how the licensing landmines
stay out of the tree: freerouting is GPL-3.0, GeoSteiner and FLUTE carry non-commercial
encumbrances, and REST uses a non-OSI licence. None may become dependencies.

When adding a document, add it to the list below in the same sentence form — *what it covers*, and
*what it refuses to claim*.

## Documents

- [Autorouter landscape](./autorouter-landscape.md) compares open-source and
  proprietary systems, interaction models, licenses, integration boundaries,
  and their usefulness as baselines.
- [Modern algorithms and hardware](./modern-algorithms-and-hardware.md) separates
  mature PCB techniques from promising transfers and speculative research, then
  recommends a staged architecture and evaluation plan.
- [Independent stack audit](./audit-2026-08-03.md) reviews the implemented deterministic,
  KiCad, MCP, benchmark, and governance boundaries against their recorded evidence.
- [Audio circuit benchmark intake](./audio-circuit-benchmarks.md) records why public DIY catalogs
  are reference-only and defines a licence-aware, original-fixture capability ladder for MCP-shared
  board inspection/routing tests plus MCP-independent Circuit Intent and schematic-rendering checks.
- [Live IPC apply references](./ipc-apply-v1.md) records what KiCad's `begin_commit`/`push_commit`
  transaction does and does not guarantee for a mutation pushed into a running editor, including
  two load-bearing negatives — no revision, dirty flag, or conditional write anywhere in the
  protocol, and no documented atomicity for a push — and maps each failure mode to a typed
  refusal. It refuses to claim anything about SWIG-binding mutation, the schematic editor, or
  KiCad 11.
- [Unshipped KiCad ERC containment experiment](./kicad-schematic-erc-containment.md) records why
  a real local KiCad ERC probe did not become an MCP capability: legacy sandbox and aggregate-quota
  containment did not meet the required security boundary.
- [Authoritative KiCad schematic ERC](./kicad-schematic-erc-authority-v1.md) records the
  fixed-argument `kicad-cli sch erc` contract, why exit code 5 counts violations rather than
  signalling an error, and why board parity has nowhere to live in an ERC report. It claims no
  electrical correctness and no containment.
- [Held-out audio project-family evaluation](./heldout-audio-project-family-evaluation.md) defines
  the first hash-separated, independently authored family split and its one-family evidence limits.
- [FreeRouting comparison boundary](./freerouting-comparison.md) documents the GPL-isolated,
  receipt-bound comparison harness and why it cannot yet declare a result.
- [Multi-pin routing references](./multi-pin-routing-references.md) grounds deterministic component
  merging and the limits of the current topology policy.
- [Negotiated congestion v1](./negotiated-congestion-v1.md) surveys what PathFinder, VPR, and the
  modern negotiation-based routers actually specify for net order, the per-iteration cost update,
  and rip-up selection; it corrects the common misattribution of VPR's history rule to PathFinder,
  records the published evidence that unbounded history can degrade a solution, and lists what
  could not be verified.
- [Negotiated physical-clearance acceptance](./negotiated-physical-clearance.md) defines the
  bounded exact-integer, same-layer candidate-pair gate that supplements lattice congestion
  accounting without claiming board-wide or KiCad physical verification.
- [Route-bundle preview](./route-bundle-v1.md) records the read-only, twice-composed plan contract
  for two to eight two-pin nets on one lattice, published only when both compositions agree. It
  claims no multilayer capacity, no vias or zones, and no authority to apply the plan.
- [tscircuit output-validation integration contract](./tscircuit-output-validation-contract-v1.md)
  separates whole-output DRC from per-net CopperMCP disposal, freezes the first fail-closed SRJ
  conversion subset, and refuses to infer cross-candidate, via, KiCad, or mutation claims from a
  one-candidate acceptance.
- [Bounded local exact repair](./bounded-local-exact-repair.md) defines the standalone verified
  lattice operator and the gates still required before negotiated-routing integration.
- [Exact local-repair negotiated-integration gate](./exact-local-repair-negotiated-integration-gate.md)
  records the internal exact per-edge Board-IR candidate-path acceptance prerequisite, its capped
  same-net-aware subset, and why the predeclared >=10% gain gate still declines integration.
- [Incremental spatial indexing](./incremental-spatial-index-v1.md) surveys what TritonRoute and
  VPR index bounded rip-up and reroute with, and argues for a deterministic uniform grid mutated
  only between negotiation passes. It claims no R-tree comparison and no performance result.
- [Performance profile v1](./performance-profile-v1.md) records the clean-worktree routing,
  placement, and Circuit Scene measurement prerequisite for any future acceleration work.
- [Performance parse profile v2](./performance-parse-profile-v2.md) extends that prerequisite with
  a committed parse-heavy complete-board read and nested stage attribution before #87 selects any
  acceleration experiment.
- [Ordered-layer routing v1](./ordered-layer-routing-v1.md) records the 2..8 signal-layer,
  full-stack-via proposal boundary and its serialization/DRC promotion gates.
- [Layered fill-aware obstacles v1](./layered-fill-aware-obstacles-v1.md) records how production
  routers treat poured copper, and the revision, zone-backing, and containment proofs that let the
  ordered-layer adapter shrink a zone envelope to verified fill bounding boxes; it claims nothing
  about exact polygon layered collision or a public layered fill-authority contract.
- [Bounded placement-heuristic baseline](./placement-heuristic-baseline.md) records the
  legalizer-gated local/beam search, its connectivity proxy, reproducible fixture evidence, and
  the limits that keep it advisory.
- [Route-aware placement policy](./route-aware-placement-policy.md) records the bounded opt-in
  A* evidence ranker, operation-wide probe accounting, and its fixture-specific acceptance gate.
- [Padless proposal-anchor validation order](./padless-anchor-validation-order.md) records why an
  explicit anchor naming a known padless footprint refuses `unsupported_geometry` before an
  unrelated syntactic contradiction, without extending anchor geometry or padless placeability.
- [AI routing-policy boundary](./ai-routing-policy-boundary.md) defines a closed, redacted policy
  contract for ordering and coordinator-supplied options without direct copper authority.
- [Isolated reference policy-worker protocol](./isolated-policy-worker-protocol.md) records the
  fixed order-only subprocess contract, initial-order admission, and explicit OS-sandbox non-claim.
- [MCP Tasks compatibility](./mcp-tasks-compatibility.md) records the observed runtime facts and
  the owner-bound, durable-storage gates that keep experimental wire Tasks disabled.
- [MCP excessive-agency evaluation](./mcp-excessive-agency-evaluation.md) defines the seven-case
  offline capability, disclosure, revision, and quota regression boundary.
- [Excessive-agency evaluation v1](./excessive-agency-eval-v1.md) builds the systematic 29-scenario
  adversarial suite on top of that boundary and replays it against four project families, three of
  them held out. It reports 77 passes, 0 failures, and 39 scenarios that could not run — including
  the finding that the only externally authored family reaches no agency boundary at all — and it
  refuses to claim that any of this measures a model's behaviour.
- [Circuit Scene IR references](./circuit-scene-ir-references.md) grounds the typed semantic/visual
  observation contract and its disclosure limits.
- [FreeRouting real-run evidence](./freerouting-real-run-v2.md) records the first public,
  KiCad-DRC-gated DSN/SES run while retaining the harness's no-parity/no-closure boundary.
- [Harness-owned KiCad Specctra transaction](./freerouting-harness-owned-kicad-transaction.md)
  records the documented DSN/SES export-import chain the comparison harness can own through
  KiCad-bundled Python, without CopperMCP parsing or applying a session itself.
- [KiCad orthogonal courtyard topology](./kicad-orthogonal-courtyard-topology.md) grounds ADR-0065's
  exact decimal-to-nanometre courtyard chain reconstruction in the official S-expression
  specification, copying no external code.
- [Courtyard oracle parity](./courtyard-oracle-parity-v1.md) grounds ADR-0075 in KiCad 10.0.5's own
  source and in measurements against the real `kicad-cli`: the courtyard cache is contracted by
  5,000 nm so a collision needs 10,000 nm of penetration, and a footprint's rings are one even-odd
  region in which a nested ring is a hole — a topology 31 shipping KiCad library footprints actually
  use. It records the sub-threshold band as an explicit non-claim, and claims nothing about arcs,
  custom courtyard clearance, the tiny-shape band, or intersecting same-footprint rings.
- [Chamfered and circular courtyards](./courtyard-curved-shapes-v1.md) measures what the #116
  refused boards actually carry — exact 45-degree chamfers and exact-radius circles, no rotated
  rectangles, no arcs — pins KiCad 10.0.5's inward circle polygonisation band against the real
  `kicad-cli`, and restates ADR-0072's outward-envelope direction for a keep-out on an
  evidence-publishing surface: outer bounds may only prove clearance, inner bounds may only prove
  violation, and everything between is a declared concession.
- [KiCad courtyard layer versus footprint side](./kicad-courtyard-layer-vs-footprint-side-v1.md)
  settles which side a courtyard drawn on the "wrong" layer constrains, grounding ADR-0097. It reads
  KiCad 10.0.5's cache build and courtyard-DRC provider in both directions — sites keyed on a
  shape's own layer and sites keyed on `IsFlipped()` — finds the overlap rule in the first group and
  the DRC rule language in the second, and measures seven arrangements against the real `kicad-cli`
  in which the verdict tracks the courtyard layer and never the footprint's side. It records that
  the offending corpus footprint is one unmodified stock KiCad library part, so neither a board
  defect nor an adapter bug. It claims nothing about arcs or curved chains on either layer, about
  the `pth_inside_courtyard` rule KiCad also reports on those fixtures, about non-zero configured
  courtyard clearance, or about any board being DRC-clean.
- [KiCad pad fabrication property](./kicad-pad-fabrication-property-v1.md) establishes what a pad's
  `(property <token>)` does, grounding ADR-0099. It pins the eight tokens KiCad's writer can emit
  and the single bare-token shape it emits them in, records that KiCad's *reader* is looser in three
  ways the writer never produces — an empty property, a silently skipped unknown token, and a
  multi-token form in which the **last** token wins — and sweeps every consumer in both directions,
  enumerator literals and the bare accessor alike. Its central finding is that exactly one value,
  `pad_prop_castellated`, reaches routable space: KiCad's own router adds a castellated pad's hole
  to the world as an edge exclusion, and `Edge.Cuts` does not carry it. It also records the
  rule-language route — the value is nameable as `A.Fabrication_Property` in a `.kicad_dru`, where
  KiCad's shipped examples use it to set a `zone_connection`. It claims nothing about what the
  exclusion region actually is, nothing about round trips through non-KiCad tools, and nothing about
  which KiCad release added `pad_prop_pressfit`.
- [Source-to-board parity with `kicad-cli` 10.0.5](./source-to-board-parity-v1.md) corrects the
  recorded assumption that a board-side parity verdict needs a project context — it does not, for
  the CLI — and then documents the four distinct ways the resulting check yields a *silent* false
  pass, three of them KiCad's and one of them ours. Its central finding is that CopperMCP's own
  delivered schematic is board-excluded by construction and produces identical output for a correct
  board and a deliberately wrong one, which is what forces a board-eligible projection and the
  arithmetic liveness invariant that gates every verdict. It carries a dated reproduction of every
  measurement and one correction to its own §4.
- [KiCad pad `zone_connect`](./kicad-pad-zone-connect-v1.md) establishes from KiCad 10's zone filler
  and DRC engine that the filler is the only thing that turns the field into copper, that the finished
  fill is clipped to the zone's own extents so poured copper is a subset of the zone boundary for
  every value, and that of the four values only `0` removes an attachment — which is what separates
  the three CopperMCP can discard from the one it may not. It grounds ADR-0091 and claims nothing
  about how the override should eventually be modelled, nothing about thermal spoke geometry, and
  nothing about the population of boards that carry it.
- [KiCad custom pad envelope](./kicad-custom-pad-envelope-v1.md) answers whether an envelope for a
  custom pad is derivable from the document — it is, since the primitives union with the anchor and
  each primitive head admits an exact integer containing box — and then refuses anyway, because a
  Board IR `Pad` is read over-approximating for its obstacle and under-approximating for its
  attachment core from the same three fields, and no single rectangle can be both. It offers no
  bounded primitive subset and no with/without differential, either of which would prove nothing here.
- [Pad geometry reader survey](./pad-geometry-reader-survey-v1.md) enumerates every reader of a
  Board IR `Pad`'s `shape`, `size_x_nm` and `size_y_nm` — by field access and by accessor call,
  including consumers three hops away through stored dataclass fields — and records the direction of
  error each one needs. It finds 23 sites where ADR-0100 assumed 3, and its decisive finding is
  three readers in `placement/legalizer.py` whose direction requirement is **already unsatisfied**:
  two publish `violated` from an over-approximating box, and one feeds `rect_inside_ring` and
  `rect_touches_ring` — opposite directions — from a single accessor. It corrects one sentence of
  the custom pad envelope note's §5 and it measures what a distinct envelope would buy on the
  corpus: **zero additional saves**, because `thermal_bridge_angle` refuses one construct behind the
  custom pad on every target board. It claims no false-violation rate on any real board, and no
  conversion win.
- [KiCad `connect` pads](./kicad-connect-pad-attribute-v1.md) establishes what
  `PAD_ATTRIB::CONN` is from two sweeps of KiCad's source outside the foreign-format import
  plug-ins: every occurrence of `PAD_ATTRIB::CONN` (35 across 17 files) and every occurrence of
  `PAD_ATTRIB::SMD`. Its central finding is universal and is what the modelling rests on — the
  connectivity engine, the push-and-shove router, layer trimming and hole suppression all put
  `CONN` and `SMD` in one shared case body, so no site anywhere gives the two different copper,
  layer span, hole or connectivity. It also carries a **lower bound** of ten divergences, none of
  them geometric, and the methodological correction that produced it: a sweep for one enum value
  is structurally blind to branches testing its siblings, which is how the first version missed
  the pick-and-place exclusion and then claimed exhaustiveness anyway. It disposes of the plating
  hypothesis — plating is not a pad attribute in KiCad at all. It grounds ADR-0096 and D-186, and
  claims nothing about castellated pads, per-layer padstacks, behaviour reached through
  user-authored DRC rules, or the population of boards that carry an edge connector.
- [KiCad aperture pads and net ties](./kicad-aperture-pads-and-net-ties-v1.md) grounds three
  singleton refusals: a copper `fp_poly` that is a declared short, a paste-only pad that is a
  stencil aperture, and `placed` as editor bookkeeping. It claims no net-tie model.
- [KiCad net-tie modelling](./kicad-net-tie-modelling-v1.md) defines the netless-obstacle model for
  a declared `net_tie_pad_groups` short: the copper is something to route around and never a
  connection. It claims nothing about multi-group ties or non-rectangular tie copper.
- [KiCad net 0 copper](./kicad-net0-copper-v1.md) establishes that net 0 is KiCad's value for
  unconnected items on real copper — 7 of 12 surveyed boards carry it, 115 vias and 2,687 segments —
  and that its saved spellings must resolve identically; a negative ordinal stays refused.
- [KiCad arc tracks as routing obstacles](./kicad-arc-track-obstacles-v1.md) grounds ADR-0070's
  conservative arc envelope in the official S-expression arc grammar and the inscribed-angle
  theorem, and states plainly that the envelope is loose for a near-semicircular arc and claims
  nothing about major arcs, arcs as attachment copper, or the layered proposal adapter.
- [KiCad copper text envelope](./kicad-copper-text-envelope-v1.md) asks whether a region provably
  containing every plotted glyph is derivable from the board document, and answers no for four
  measured reasons, so copper text stays refused at a cost of one board.
- [Edge.Cuts outline assembly](./edge-cuts-outline-assembly-v1.md) records what KiCad requires of a
  segment-drawn outline, and why the adapter chains endpoints at zero epsilon: an outline is routing
  room and may only be under-approximated. It claims nothing about arcs, curves, or holes.
- [KiCad UUID uniqueness](./kicad-uuid-uniqueness-v1.md) establishes that the format says a uuid
  *should* be unique rather than must — nine of twelve surveyed boards reuse one across footprint
  instances — so a reused value names nothing and degrades to a derived name that keeps mutation
  refused.
- [Assembled-outline identity](./assembled-outline-identity-v1.md) names an `Edge.Cuts` contour by
  the sorted uuids of its member segments, and records why the revision-derived name it replaces is
  structurally unappliable. It claims no uuid uniqueness and no curve-bearing outline.
- [NE5532-class audio routing fixture](./ne5532-audio-routing-fixture.md) records the public
  datasheet pin roles and bypass guidance behind the CopperMCP-original Apache-2.0 fixture, which
  reproduces abstract roles rather than any third-party schematic, artwork, or values.
- [Circuit Scene to route action closure](./scene-action-closure-references.md) reproduces the
  opaque-reference integration failure, defines the revision-bound MCP contract, and records its
  exact hidden-name equivalence oracle and limitations.
- [KiCad IPC observer references](./kicad-ipc-references.md) records the official socket,
  plugin, version, and `kicad-python` boundaries behind the redacted live-board observer.
- [Live KiCad IPC fidelity oracle](./kicad-live-ipc-oracle.md) records the read-only
  source-to-Board-IR-to-Scene digest probe, capability outcomes, deadline boundary, and
  workstation evidence without claiming a live-editor result.
- [KiCad session-revision HMAC boundary](./kicad-session-revision-hmac.md) records why the
  prior HMAC live-session CAS replaced an offline-testable token hash, including the deliberate
  restart refusal. It is retained as historical evidence.
- [KiCad session-revision PBKDF2 boundary](./kicad-session-revision-pbkdf2.md) records the current
  fixed-work, process-local-salt derivation, CodeQL remediation rationale, latency measurement,
  and deliberate restart refusal.
- [Safe apply references](./safe-apply-references.md) grounds compare-and-swap, atomic publication,
  editor-lock, recovery, and explicit-authorization decisions for file-backed mutation.
- [Apply-token retention](./apply-token-retention.md) records nonce-expiry and bounded-retention
  rules without treating TTL as secure erasure.
- [OpenSSF criticality and supply-chain](./openssf-criticality-and-supply-chain.md) separates the
  Criticality activity proxy from Scorecard controls and maps the dated baseline to sustainable
  contributor, release, review, and adoption work.
- [Open-baseline routing benchmarks](./open-baseline-benchmarks-v1.md) records the SimpleRouteJson
  interchange as actually specified, the millimetre-to-nanometre rule and its rounding directions,
  a per-corpus licensing determination with URLs (tscircuit-benchmark MIT, tscircuit/autorouting
  unlicensed, PCBench MIT, PCBWorld split and unreleased, FreeRouting GPL-3.0), and the harness
  lessons from an abandoned upstream benchmark. It claims no baseline comparison and no
  generalisation beyond LLM-generated 2-layer boards.
- [KiCad copper layer numbering](./kicad-copper-layer-numbering-v1.md) establishes, from KiCad's
  own enumeration and board writer rather than from one sample board, that copper is numbered
  `F.Cu=0`, `B.Cu=2`, `InN.Cu=2+2N` and declared front-to-back so the IDs do not ascend, that this
  numbering replaced a different consecutive one at KiCad 9, and that the two cannot both be
  accepted at once. It claims nothing about non-copper ordinals, the pre-4.0 legacy format, or
  KiCad 11.
- [KiCad board groups](./kicad-board-groups-v1.md) establishes that a root `(group …)` is editor
  organisation carrying no geometry or connectivity, and that the differential proving it bounds
  what the adapter adds rather than what it fails to read. It models no grouping and refuses a
  locked group.
- [KiCad root board properties](./kicad-root-board-properties-v1.md) establishes that a root
  `(property …)` is one entry of a text-variable map that is **not** inert: six enumerated termini
  reach real behaviour, including copper text and custom DRC rules. An enumeration is not a
  completeness proof, and it says so.
- [KiCad PCM distribution](./kicad-pcm-distribution-v1.md) records the addon package format from
  the published JSON Schema and the addons-metadata CI rather than the prose guide, listing the six
  fields on which the two disagree; the archive whitelist, icon bounds, size tolerances, and
  version-immutability rules a submission is judged against; the split between the in-archive and
  submitted `metadata.json`; and the two behaviours in KiCad's own plugin manager that decide
  whether a Python IPC plugin is reachable at all — a `requirements.txt` it cannot use to install
  CopperMCP, and a `--system-site-packages` venv that is why it does not need to. It claims no
  submission, acceptance, or publication, and no observed behaviour on any platform.
- [Roundrect radius precision](./roundrect-radius-precision-v1.md) establishes from KiCad's own
  padstack source, not from the format prose, that a roundrect corner radius is never stored — a
  ten-significant-digit ratio of the pad's shorter side is, and the radius is recomputed and
  `KiROUND`ed on every read — so an ordinary pad lands on a fractional nanometre; it measures 592
  such pads among 4,537 across 23 real boards with a worst residue of 0.80 nm, and argues the
  rounding direction by geometry role rather than by "a pad is copper", which points the wrong way
  because a larger radius means a smaller pad. It claims nothing about chamfered pads, per-layer
  padstacks, board format versions other than `20260206`, or that the 23-board tree is
  representative of KiCad boards generally.
- [Parse-budget calibration](./parse-budget-calibration-v1.md) measures what one mebibyte of real
  KiCad board costs in tokens, nodes, and vertices across 38 boards, re-derives the `ParseLimits`
  defaults from those densities, and prices each against adversarial input. Admitting a board is not
  converting it.
- [Fill-vertex budget calibration](./fill-vertex-budget-calibration-v1.md) re-derives
  `max_fill_vertices` from 50,000 to 500,000 against the pour densities real boards carry, a ceiling
  that was refusing seven corpus boards before freshness was ever considered, and prices what the
  raise buys an attacker. It claims no route benefit and no generalisation beyond the boards
  measured.
- [Route obstacle-budget calibration](./route-obstacle-budget-calibration-v1.md) splits the one
  `max_obstacles` budget that was charging three different populations — 61 of 93 refusals were the
  routed net's *own* copper — and re-derives it under a region-scoped obstacle model. Nothing in it
  was DRC-checked or applied.
- [Off-grid lattice refusals](./off-grid-lattice-refusal-v1.md) measures every `off_grid` refusal on
  a live board tree and re-previews each at the finest lattice step its geometry permits: zero of
  eighteen route, which refutes the hypothesis that the lattice is the binding constraint. It claims
  no sample of KiCad boards.
- [Tier-2 real-board capability survey](./tier2-real-board-capability-v1.md) measures what five
  read-only surfaces return on 12 real boards at default settings, now that 10 of them convert:
  authoritative KiCad DRC works on all 12 including the two Board IR refuses; a region-scoped
  Circuit Scene works on 10 of 10 while 8 of 10 whole-board requests silently return empty `vias`,
  `zones` and `rules` lists; placement preview accepts 991 of 991 footprints and 10 of 10 boards
  refuse the source-preserving render; route preview routed 0 of 345 nets. It claims nothing
  electrical, thermal or manufacturable, does not claim an apply would have succeeded had the gate
  passed, is not a whole-board routing completion result, and does not treat one designer's
  project family as a sample of KiCad boards generally. **Its conversion figure is superseded and
  its note is not edited**: that note and its ledger row are point-in-time records of 2026-08-07,
  and the corpus has both been deduplicated and grown since. For the current figure read the
  `B-099` survey close-out replay in the
  [benchmark ledger](../ledgers/benchmark-ledger.md) — 12 of 18 saves, 17 of them distinct — and
  [D-191](../ledgers/decision-ledger.md) for why no count from this live tree stays true for long.

## Terms used in this review

- **Open source** means the upstream project declares an OSI-style license that
  permits source inspection, modification, and redistribution under its terms.
- **Source-available** means source can be inspected but the license is absent or
  non-open-source. It is not interchangeable with open source. No shortlisted
  general-purpose router is classified as source-available in the main table;
  an inspectable research artifact without a clear license is evidence, not
  reusable implementation material.
- **Proprietary** means use and integration are controlled by commercial terms.
- **Interactive or guided routing** keeps a designer in the loop: the user picks
  connections, corridors, layers, or regions and the tool completes a bounded
  route.
- **Whole-board autorouting** means a batch attempt over many or all unrouted
  nets on an already placed board. It does not imply automatic placement or
  electrical-signoff autonomy.

## Evidence labels

Product tables identify the origin of consequential claims:

- **Official project evidence** covers an open-source repository, manual,
  release, or paper from its maintainers.
- **Vendor claim** covers commercial feature, performance, and AI claims that
  were not independently reproduced in this review.
- **Independent PCB evidence** is measured on PCB layouts outside the product
  vendor's own material.
- **IC-transfer evidence** comes from integrated-circuit placement or routing.
  It can motivate a kernel or scheduling design, but does not establish PCB
  clearance, layer, via, signal-integrity, manufacturability, or KiCad behavior.

## Bottom line

1. Use KiCad as the editing host and authoritative DRC gate, not as evidence that
   a general whole-board router already exists in-process.
2. Treat Freerouting as the strongest open whole-board comparison baseline. Keep
   GPL implementations outside the Apache-2.0 core and exchange files or data
   through an explicit process boundary.
3. Build the first production path around deterministic CPU A* or maze search,
   multilayer negotiated congestion, incremental geometry checks, and bounded
   rip-up and reroute.
4. Add conflict-aware multicore scheduling and profile-guided Rust kernels before
   making a GPU part of the required runtime.
5. Use exact SAT, MIP, or CP only on small failed windows. Use ML for typed policy
   choices such as ordering, corridors, repair windows, and cost weights—not for
   unchecked copper geometry.
6. Keep internal exact connectivity and geometry checks, authoritative KiCad
   DRC, and selected SI/PI/thermal/DFM checks as release gates. Every route remains
   an immutable candidate until revision recheck and explicit apply.

## Limitations

- Commercial feature boundaries and prices can vary by edition and contract.
- Most public GPU and learned-routing results are IC results; the transfer to
  irregular PCB geometry is explicitly treated as unproven.
- The July 2026 PCBWorld result is a recent preprint and should be replicated
  before it drives a product decision.
- A public paper or downloadable dataset does not imply redistribution rights.
  Dataset and artifact licenses must be recorded per benchmark run.
