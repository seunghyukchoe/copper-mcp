# Autorouter landscape

**Snapshot date:** 2026-08-03

> **Source verification, 2026-08-03.** An independent audit confirmed the pre-2026 literature cited
> here (Hwang 1976, Takahashi-Matsuyama 1980, FLUTE, GeoSteiner, PathFinder, TritonRoute, Magic,
> Shewchuk, CGAL, *Build Systems a la Carte*) and the licensing claims. Identifiers in the
> `2512.*`, `2602.*`, `2605.*` and `2607.*` arXiv ranges post-date the audit tooling's knowledge
> cutoff and **could not be confirmed**; they are recorded as reported, not as verified sources,
> and nothing in this repository depends on them. Specifically unconfirmed: `2607.05915`
> (PCBWorld), `2607.22761` (DRC-Aid), `2607.21850` (SCALE), `2605.15669` (Rule2DRC),
> `2512.03594` (offline RL over cost weights), `2602.00510` (PCBSchemaGen).

The practical market splits into three different products: interactive helpers,
batch routers for an already placed board, and services that also attempt
placement. Comparing them as if they solved the same problem overstates the
capability of guided tools and understates the signoff burden of autonomous ones.

## Open-source systems

| System | License and status | Routing model | Integration and limits | Evidence and CopperMCP fit |
| --- | --- | --- | --- | --- |
| [KiCad 10](https://www.kicad.org/blog/2026/07/KiCad-10.0.5-Release/) | Predominantly GPL-3.0-or-later; 10.0.5 released 2026-07-22 | PNS is an interactive, DRC-aware push/shove and walkaround router. “Attempt Finish” completes selected connections sequentially on the current layer. | Native board editing and DRC. The current automatic completion does not add vias or change layers; whole-board multilayer routing still relies on an external router workflow. | **Official project evidence.** Use as host, file authority, and final DRC gate. Do not describe the selected-track helper as a whole-board autorouter. See the [PCB Editor manual](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#automatically-completing-tracks) and [IPC API](https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/index.html). |
| [Freerouting](https://github.com/freerouting/freerouting) | GPL-3.0; [2.2.4](https://github.com/freerouting/freerouting/releases/tag/v2.2.4) released 2026-05-13 | Whole-board maze/wavefront routing with conflict-driven rip-up and reroute, then trace and via optimization. | DSN/SES, GUI, CLI, self-hosted HTTP API, and Docker; documented integrations include KiCad, EAGLE, pcb-rnd, and tscircuit. DSN/SES import and release regressions require fixture coverage. | **Official project evidence; limited independent PCB evidence.** Strongest general open baseline. Run out of process and compare results; do not copy or link GPL code into the Apache core. See its [architecture](https://github.com/freerouting/freerouting/blob/master/docs/architecture.md) and [integrations](https://github.com/freerouting/freerouting/blob/master/docs/integrations.md). |
| [route-rnd](http://www.repo.hu/projects/route-rnd/) with pcb-rnd | GPL-2.0-or-later; maintained but experimental | Command-line modular routing through tEDAx. HACE expands gridless rectangles, horver uses aligned two-layer grids, and topo uses triangulation and rubber-band paths. | Useful as a modular algorithm laboratory and external baseline. Its own [state page](http://www.repo.hu/projects/route-rnd/state.html) documents grid, pad-clearance, convexity, detour, and unrouted-case limitations. | **Official project evidence.** Study interfaces and failure modes; keep GPL code behind a process/interchange boundary. Not the default production baseline. |
| [KiCadRoutingTools](https://github.com/drandyhaas/KiCadRoutingTools) | MIT; [0.19.3](https://github.com/drandyhaas/KiCadRoutingTools/releases/tag/v0.19.3) released 2026-08-01 | Direct KiCad plugin/CLI with Rust A*, octilinear multilayer vias, negotiated rip-up, net ordering, differential pairs, fanout, planes, and length/time matching. | Direct `.kicad_pcb` integration is attractive. Published limits include no push/shove or blind/buried vias, coarse global assignment, and incomplete regional-rule support. | **Official project evidence.** Best permissive implementation reference and test collaborator, subject to independent correctness and performance measurement. Preserve MIT notices for any reused code. |
| [OrthoRoute](https://github.com/bbenchoff/OrthoRoute) | MIT; [1.0.0](https://github.com/bbenchoff/OrthoRoute/releases/tag/v1.0.0) released 2026-07-30 | PathFinder-style routing on a 3-D Manhattan lattice. CUDA/CuPy and Apple Metal accelerate shortest-path search within one route; nets still route sequentially. | Intended for regular backplanes and BGA escapes. Orthogonal paired layers, incomplete DRC/differential-pair behavior, and reported 40–80 GB VRAM needs at fine grids limit general use. | **Official project evidence.** Valuable GPU experiment and benchmark, not proof that a full irregular PCB detailed router should be GPU-first. |
| [tscircuit autorouter](https://github.com/tscircuit/tscircuit-autorouter) | MIT; active | Capacity-mesh or hypergraph global routing, congestion-aware A*, local solvers, seeded benchmarks, and browser visualization. | SimpleRouteJson and TypeScript/Node favor experimentation; the data model is tscircuit-centric and the router is younger than Freerouting. | **Official project evidence.** Useful for visualization, synthetic tests, and interface ideas. Its [hypergraph description](https://blog.autorouting.com/p/hypergraph-autorouting) is a design reference, not an independent quality benchmark. |

There is no main-table **source-available** entry. If an artifact has visible code
but no explicit compatible license, CopperMCP may cite or evaluate it under its
terms but must not treat it as reusable open-source code.

### Independent PCB evidence

[PCBWorld](https://arxiv.org/abs/2607.05915) is a July 2026 preprint built from
679 real KiCad boards. Its reported results make a useful caution: a learned
method was competitive on small boards, while Freerouting performed better on
larger ones (reported clean-pass rates 0.78 versus 0.45 in that comparison).
This is independent PCB evidence, but it is new, not yet a settled benchmark, and
does not validate every Freerouting release or rule type.

## Proprietary and commercially available systems

The following capabilities and boundaries are **vendor claims** unless noted.
This review found no public, reproducible, apples-to-apples PCB benchmark spanning
the major commercial products.

| System | Mode | Reported scope and constraints | Integration relevance |
| --- | --- | --- | --- |
| [Cadence Allegro X / OrCAD X](https://www.cadence.com/en_US/home/training/all-courses/86019.html) | Whole-board batch plus selected-net, selected-area, fanout, cleanup, and interactive routing | Allegro PCB Router is a conventional production autorouter. [Allegro AI Studio](https://www.cadence.com/en_US/home/tools/pcb-design-and-analysis/allegro-ai-studio.html) advertises automated placement, full-constraint routing, multiphysics, and GPU acceleration; reproducible evidence was not found. | Important upper-end capability reference. Proprietary formats, editions, and quote-based AI features make it a comparison target, not an embeddable engine. The [OrCAD X tier matrix](https://www.cadence.com/en_US/home/resources/datasheets/orcad-x-platform-tiers.html) should be checked for a specific purchase. |
| [Siemens Xpedition / PADS Professional](https://static.sw.cdn.siemens.com/siemens-disw-assets/public/40wAqZx1wqTIcUHhzseF7P/en-US/Siemens-SW-Routing-automation-PADS-Professional-EB-83088-D2.pdf) | Xpedition offers customizable multipass autorouting; Sketch Router is guided, corridor-based routing from one to hundreds of connections | Strategy-driven routing, fanout, differential pairs, tuning, and interactive shove are positioned as an integrated flow. Guided Sketch routing should not be counted as autonomous whole-board placement and routing. | Compare constraint coverage, multipass cleanup, and designer control. Product tier and automation availability require vendor confirmation. |
| [Altium Designer](https://www.altium.com/documentation/altium-designer/pcb/routing/situs-topological-autorouter) | Situs provides whole-board or selected nets/classes/rooms/components/areas; ActiveRoute is guided | Situs is a topological batch router that observes board rules and blind/buried vias. Altium recommends routing and locking critical or differential connections first. [ActiveRoute](https://www.altium.com/documentation/altium-designer/pcb/routing/activeroute) does not itself choose a complete via or power strategy. | Useful reference for topology-first search plus manual critical-net ownership. Proprietary; exchange fidelity must be verified if used as a baseline. |
| [Zuken CR-8000](https://www.zuken.com/en/product/cr-8000/multi-instance-interactive-and-automatic-routing/) | Interactive plus simultaneous, strategy-driven regional routing | Dragon EX combines interactive and automatic routing. The 2025 [AI-assisted PCB release](https://www.zuken.com/en/product/cr-8000/whats-new-in-cr-8000-release-2025/) covers block suggestions, breakout, learned “Brain Files,” short single-layer routes, and decoupling support—not a demonstrated autonomous full-board replacement. | Compare human-guided strategies and reuse of proven decisions. Treat AI claims as vendor evidence. |
| [Autodesk Fusion Electronics](https://www.autodesk.com/products/fusion-360/blog/18-things-need-to-know-fusion-360-electronics/) | Whole-board or selected-scope autorouting plus QuickRoute guidance | Multiple autorouter variants are available, but the multilayer router [cannot create microvias](https://help.autodesk.com/view/fusion360/ENU/?guid=ECD-ROUTE-MULTILAYER-BOARD-CPT). Standalone EAGLE availability ended on 2026-06-07 according to Autodesk's [transition notice](https://www.autodesk.com/support/technical/article/caas/sfdcarticles/sfdcarticles/Autodesk-EAGLE-Announcement-Next-steps-and-FAQ.html). | Accessible comparison for conventional boards; unsuitable as evidence for advanced-via routing. |
| [TopoR](https://www.eremex.com/products/topor/) | Whole-board topological autorouting with interactive alternatives | Arbitrary-angle routing, BGA and differential-pair support, alternative variants, and DSN/SES are advertised. | Relevant commercial comparison for topology and angle freedom. Closed implementation and vendor-only quality claims. |
| [Quilter](https://docs.quilter.ai/using-quilter/introduction) | Automated placement and routing service producing multiple candidates | Supports imports from major EDA tools and advertises DRC and physics checks. Its [published limits](https://docs.quilter.ai/about-quilter/current-limitations) recommend roughly fewer than 5,000 pins, under 20% density, below 6 GHz, at most 48 V, and at most 10 A; Quilter also says it does not infer all schematic intent or replace EMC/human review. | Closest visible autonomous-service comparison. Cloud/data terms, per-project [pricing](https://www.quilter.ai/pricing), reproducibility, and human signoff make it unsuitable as an unchecked oracle. |
| [DeepPCB](https://deeppcb.ai/pricing/) | Cloud learned placement and routing | Published standard limits include 8 layers, 1,200 airwires, 1,000 components, and 2,200 pins. Performance and quality comparisons are vendor-provided. | Useful to monitor, but the closed model and pay-per-compute service prevent implementation reuse and reproducible local baselining. |
| [Flux Auto-Layout](https://docs.flux.ai/tutorials/auto-layout) | AI-assisted routing after the user finalizes placement | Positioned for simple-to-medium browser projects; automatic component placement is not the advertised scope. | Interaction reference, not evidence for whole-board placement and routing. No reproducible benchmark found. |
| [JITX](https://docs.jitx.com/en/latest/essentials/physical_design/autorouter.html) | Code-driven plus interactive, selected-object routing, one layer at a time | Deterministic geometry and constraints can be generated in code; routing remains a designer-directed physical-design operation. | Strong reference for typed, programmatic intent. It is not a general autonomous whole-board router. |

Other available proprietary routers include [Proteus shape-based
routing](https://www.labcenter.com/router/), [DipTrace](https://diptrace.com/diptrace-software/),
[EasyEDA](https://docs.easyeda.com/en/PCB/Route/index.html), and the standalone
[ELECTRA router](https://konekt.com/products/). They broaden price and workflow
coverage, but this review found no independent result that would displace
Freerouting as the primary reproducible open baseline.

## Integration decision table

| Need | Recommended comparison or component | Why | Boundary |
| --- | --- | --- | --- |
| Authoritative editor, file mutation, and DRC | KiCad | It is the project's native board authority and exposes an IPC path. | CopperMCP proposes immutable candidates; KiCad validates and explicitly applies them after revision recheck. |
| Reproducible whole-board baseline | Freerouting | Mature open batch flow with CLI/API and DSN/SES exchange. | Separate process; preserve provenance and imported-rule warnings; no GPL code in the Apache core. |
| Permissive direct-KiCad reference | KiCadRoutingTools | MIT, multilayer A*, and negotiated rip-up are close to the planned architecture. | Independently verify rules and quality; reuse only with notices and contract tests. |
| Alternative algorithm laboratory | route-rnd and tscircuit | Exposes modular algorithms, failure modes, synthetic tests, and visualizations. | External comparison for GPL route-rnd; tscircuit's model needs an adapter rather than becoming Board IR. |
| GPU feasibility experiment | OrthoRoute | Concrete CUDA/Metal implementation exposes throughput and memory tradeoffs. | Benchmark only on a bounded kernel and representative irregular boards; retain deterministic CPU fallback. |
| Commercial ceiling | Cadence, Siemens, Altium, Zuken, TopoR | Shows production expectations for constraints, interaction, cleanup, and enterprise flows. | Vendor claims are not acceptance evidence; do not automate or upload private boards without an explicit license and data agreement. |
| Autonomous-service comparison | Quilter, DeepPCB | Tests multi-candidate placement/routing and service UX expectations. | Closed engines, board-data exposure, pricing, and non-reproducibility exclude them from the trusted local core. |

## License and interchange policy

- CopperMCP is Apache-2.0. As a conservative engineering boundary—not legal
  advice—do not vendor, link, translate, or copy GPL KiCad, Freerouting, or
  route-rnd implementation code into the core. Execute comparison tools as
  standalone programs through documented IPC or file formats, and retain their
  licenses when distributing them separately.
- MIT projects may be reused under their license with attribution and notices,
  but permissive licensing does not establish correctness. Prefer stable Board
  IR contracts and focused adapters over importing an engine wholesale.
- DSN/SES and other interchange formats can omit or approximate newer KiCad
  constraints. Record exporter/importer versions, rule losses, unit conversions,
  and the source board revision in candidate provenance; rerun exact internal
  checks and authoritative KiCad DRC after import.
- Proprietary tools and services are evaluation references unless a contract
  explicitly grants automation, data handling, redistribution, and result-use
  rights. Never upload private board data by default.
