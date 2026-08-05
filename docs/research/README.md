# PCB autorouting research

**Snapshot date:** 2026-08-05

This package surveys current PCB autorouters and turns the evidence into an
implementation direction for CopperMCP. It is a research snapshot, not a claim
that every product feature, price, or repository release will remain current.

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
- [Unshipped KiCad ERC containment experiment](./kicad-schematic-erc-containment.md) records why
  a real local KiCad ERC probe did not become an MCP capability: legacy sandbox and aggregate-quota
  containment did not meet the required security boundary.
- [Held-out audio project-family evaluation](./heldout-audio-project-family-evaluation.md) defines
  the first hash-separated, independently authored family split and its one-family evidence limits.
- [FreeRouting comparison boundary](./freerouting-comparison.md) documents the GPL-isolated,
  receipt-bound comparison harness and why it cannot yet declare a result.
- [Multi-pin routing references](./multi-pin-routing-references.md) grounds deterministic component
  merging and the limits of the current topology policy.
- [Negotiated physical-clearance acceptance](./negotiated-physical-clearance.md) defines the
  bounded exact-integer, same-layer candidate-pair gate that supplements lattice congestion
  accounting without claiming board-wide or KiCad physical verification.
- [Bounded local exact repair](./bounded-local-exact-repair.md) defines the standalone verified
  lattice operator and the gates still required before negotiated-routing integration.
- [Performance profile v1](./performance-profile-v1.md) records the clean-worktree routing,
  placement, and Circuit Scene measurement prerequisite for any future acceleration work.
- [Bounded placement-heuristic baseline](./placement-heuristic-baseline.md) records the
  legalizer-gated local/beam search, its connectivity proxy, reproducible fixture evidence, and
  the limits that keep it advisory.
- [AI routing-policy boundary](./ai-routing-policy-boundary.md) defines a closed, redacted policy
  contract for ordering and coordinator-supplied options without direct copper authority.
- [Isolated reference policy-worker protocol](./isolated-policy-worker-protocol.md) records the
  fixed order-only subprocess contract, initial-order admission, and explicit OS-sandbox non-claim.
- [MCP Tasks compatibility](./mcp-tasks-compatibility.md) records the observed runtime facts and
  the owner-bound, durable-storage gates that keep experimental wire Tasks disabled.
- [MCP excessive-agency evaluation](./mcp-excessive-agency-evaluation.md) defines the seven-case
  offline capability, disclosure, revision, and quota regression boundary.
- [Circuit Scene IR references](./circuit-scene-ir-references.md) grounds the typed semantic/visual
  observation contract and its disclosure limits.
- [FreeRouting real-run evidence](./freerouting-real-run-v2.md) records the first public,
  KiCad-DRC-gated DSN/SES run while retaining the harness's no-parity/no-closure boundary.
- [Circuit Scene to route action closure](./scene-action-closure-references.md) reproduces the
  opaque-reference integration failure, defines the revision-bound MCP contract, and records its
  exact hidden-name equivalence oracle and limitations.
- [KiCad IPC observer references](./kicad-ipc-references.md) records the official socket,
  plugin, version, and `kicad-python` boundaries behind the redacted live-board observer.
- [Live KiCad IPC fidelity oracle](./kicad-live-ipc-oracle.md) records the read-only
  source-to-Board-IR-to-Scene digest probe, capability outcomes, deadline boundary, and
  workstation evidence without claiming a live-editor result.
- [KiCad session-revision HMAC boundary](./kicad-session-revision-hmac.md) records why the
  live-session CAS uses a process-local keyed fingerprint instead of an offline-testable token
  hash, including the deliberate restart refusal.
- [Safe apply references](./safe-apply-references.md) grounds compare-and-swap, atomic publication,
  editor-lock, recovery, and explicit-authorization decisions for file-backed mutation.
- [Apply-token retention](./apply-token-retention.md) records nonce-expiry and bounded-retention
  rules without treating TTL as secure erasure.
- [OpenSSF criticality and supply-chain](./openssf-criticality-and-supply-chain.md) separates the
  Criticality activity proxy from Scorecard controls and maps the dated baseline to sustainable
  contributor, release, review, and adoption work.

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
