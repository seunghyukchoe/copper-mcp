# PCB autorouting research

**Snapshot date:** 2026-08-04

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
- [Audio circuit benchmark intake](./audio-circuit-benchmarks.md) records why public DIY catalogs
  are reference-only and defines a licence-aware, original-fixture capability ladder for MCP-shared
  board inspection/routing tests plus MCP-independent Circuit Intent and schematic-rendering checks.
- [Circuit Scene to route action closure](./scene-action-closure-references.md) reproduces the
  opaque-reference integration failure, defines the revision-bound MCP contract, and records its
  exact hidden-name equivalence oracle and limitations.

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
