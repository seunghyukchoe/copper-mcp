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
- [Bounded local exact repair](./bounded-local-exact-repair.md) defines the standalone verified
  lattice operator and the gates still required before negotiated-routing integration.
- [Exact local-repair negotiated-integration gate](./exact-local-repair-negotiated-integration-gate.md)
  records the internal exact per-edge Board-IR candidate-path acceptance prerequisite, its capped
  same-net-aware subset, and why the predeclared >=10% gain gate still declines integration.
- [Performance profile v1](./performance-profile-v1.md) records the clean-worktree routing,
  placement, and Circuit Scene measurement prerequisite for any future acceleration work.
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
- [KiCad arc tracks as routing obstacles](./kicad-arc-track-obstacles-v1.md) grounds ADR-0070's
  conservative arc envelope in the official S-expression arc grammar and the inscribed-angle
  theorem, and states plainly that the envelope is loose for a near-semicircular arc and claims
  nothing about major arcs, arcs as attachment copper, or the layered proposal adapter.
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
