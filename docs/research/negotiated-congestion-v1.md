# Negotiated congestion: what the literature actually specifies

Literature survey for the three declared negotiation policy slots, gathered 2026-08-06. It extends
[Multi-pin routing references](multi-pin-routing-references.md), whose "Multi-net ordering and
negotiation" section states the design consequence this note grounds: *net order, history-cost
update rule, and rip-up selection should be three separate declared policy slots, each defaulting
to a deterministic heuristic*. Concepts referenced here inform CopperMCP's own implementation; no
external code is copied.

> **Source verification, 2026-08-06.** Every formula, default, and quotation below was read from a
> primary source that resolved on the open web: the PathFinder technical report, the VTR source
> tree and its documentation source, and open PDFs of the router papers named in
> [Sources](#sources). Where a claim could **not** be verified it is listed under
> [Explicit non-claims](#explicit-non-claims) instead of being stated. Two hosts served the
> primary text over an expired or mismatched TLS certificate (`cecs.uci.edu`, and the `sharif.edu`
> mirror of the PathFinder report); the `.ir` PathFinder host is the one used here.

## The correction that mattered most

The rule usually written as "PathFinder's history update" —
`h_n^(i+1) = h_n^(i) + max(0, occupancy_n − capacity_n)` — **is not in McMurchie and Ebeling's
paper.** The paper gives only the cost function

> c_n = ( b_n + h_n ) × p_n

and specifies both non-base terms qualitatively: h_n is "increased slightly" each iteration a node
is shared, its effect being to "permanently increase the cost of using congested nodes"; p_n is
"initialized to one" for the first iteration and thereafter "gradually increased, depending on how
many signals share n". **No closed form is published for either term, and no growth constant is
given.** The additive-overuse form is VPR's `acc_cost`, and it is the correct attribution.

This is why CopperMCP's first cost-update literal is named `accumulated-overuse-v1` rather than
anything containing "pathfinder". A slot name is a claim about provenance, and the wrong one would
be a claim this project cannot support.

Two further structural differences are worth stating plainly, because CopperMCP matches neither
exactly:

- PathFinder adds history to the base cost and **multiplies** by the present factor.
- VPR multiplies all three: `cost = base_cost × acc_cost × pres_cost`, with
  `pres_cost = 1 + pres_fac × (overuse + 1)` when `overuse ≥ 0` and `1.0` otherwise.
- CopperMCP's ledger adds both terms as an integer nanometre penalty
  (`present × count + history × count`), because ADR-0006 requires exact integer costs and a
  multiplicative float factor would forfeit that. This is a deliberate divergence, not an
  approximation of either.

## Slot 1 — net order

**What is established.** Finding an optimal net order is NP-hard, and the field's own summary is
that no single ordering wins everywhere. FastGR states it directly, citing Abel: "Some earlier
studies concluded that no single net-ordering strategy could perform better than others in all
routing problems." The heuristics that recur are the ascending or descending order of wirelength,
of bounding-box area, and of the number of pins inside a net's bounding box — with the rationale
that "routing the shorter nets first always leads to better routability since this kind of net
often has less routing flexibility."

The direction is genuinely contested. NTHU-Route 1.0 ripped up "a congested two-pin net with
smallest bounding box" first; NTHU-Route 2.0 **reversed** it, reporting the same overflow with
shorter wirelength because a large net "has more routing choices" and re-routing it can leave the
small nets untouched. BoxRouter 1.0's PreRouting orders wires by length ascending. Dr. CU sorts
"in decreasing size of routing region", then reverses the batch order. TritonRoute orders nets by
"distance to the nearest marker".

**How much ordering actually buys.** Qu et al. randomised Dr. CU's net order 300 times on one
ISPD18 benchmark and measured a relative standard deviation of **1.95% in DRC violations, 0.04% in
vias, and 0.008% in wirelength**. Ordering moves rule violations materially and wirelength barely.
Their RL ordering policy — which changes only the ordering and leaves the router's path search
untouched — reports "14% fewer DRC violations and 0.7% less total costs" against Dr. CU 2.0.

**Why negotiation is supposed to make ordering matter less.** PathFinder's own argument:
"This scheme of negotiation for routing resources depends on a relatively gradual increase in the
cost of sharing nodes. If the increase is too abrupt, signals may be forced to take high cost
routes that lead to other congestion. Just as in the standard rip-up and retry scheme, the ordering
would become important."

**Declared literals and why.**

| Literal | Grounding |
|---|---|
| `conflict-descending-v1` *(default)* | ADR-0055's measured behavior: stable `(net_id, seed)` first, then the coordinator's own exact conflict scores descending. It is the default because it is the only one this repository has measured, not because the literature prefers it. |
| `stable-identifier-v1` | The fixed order PathFinder uses — "Nets are ripped up and rerouted in the same order every iteration" — made available as an explicit choice rather than an accident of the sort key. |
| `demand-descending-v1` | NTHU-Route 2.0's reversal, and Dr. CU's "decreasing size of routing region". |
| `demand-ascending-v1` | BoxRouter 1.0's ascending-length PreRouting, and NTHU-Route 1.0's smallest-bounding-box order. |

Demand is the exact Manhattan pad separation in whole lattice cells — the two-pin analogue of
half-perimeter wirelength, and already the feature ADR-0064's closed policy input carries.

## Slot 2 — cost update

| Router | History rule | Decay |
|---|---|---|
| PathFinder 1995 | qualitative only; bounded by `h_n ≤ d_n` for its delay theorem | no |
| VPR | `acc_cost += max(0, occ − cap) × acc_fac`, once per completed iteration, `acc_fac` default **1**, initial `acc_cost` 1.0 | no |
| NTHU-Route 1.0 | `h^{i+1} = h^i + 1` on overflowed edges, `h^1 = 1` | no |
| NTHU-Route 2.0 | same accumulation, consumed through a per-iteration amplifier bounded by `k2/(k2−1)`; base cost decays on a Gompertz curve | base cost only |
| BoxRouter 2.0 | `h_i(e) = h_{i−1}(e) + i` — super-linear | no; dynamic rescaling instead |
| FastRoute 4.x | no history at all: virtual capacity `vc_e ← vc_e − o_e`, where `o_e` may be negative | yes, self-correcting |
| Dr. CU | history charged only on violated intervals, discounted against current violations, with a fading factor | yes |
| TritonRoute | marker cost decayed by iteration index inside a worker; **no history between outer iterations** | yes |

**Present-factor schedule.** VPR's documented defaults are the only fully specified schedule found:
`first_iter_pres_fac` **0.0**, `initial_pres_fac` **0.5**, `pres_fac_mult` **1.3**, `max_pres_fac`
**1000.0**. History is disabled on the first iteration (`acc_fac = 0` there). Growth is geometric
and hard-capped.

**Why the cap is load-bearing, with evidence.** BoxRouter 2.0 publishes the failure mode directly:
"after many iterations which frequently happens for highly congested designs, h_i(e) starts to
dominate p(e). This implies that a presently congested edge becomes cheaper to pass through than a
previously congested edge. This may lead to routing instability in a sense that the solution
quality may get worse with more iterations." Their figure shows overflow *increasing* past a point
without rescaling. PathFinder's Theorem 1 makes the complementary statement: if `h_n ≤ d_n` for all
nodes, every routed path's delay is bounded by `D_max` — and the authors concede that "for very
congested circuits, h_n will exceed d_n", i.e. the bound is given up in practice. Bounded history
buys a guarantee; unbounded history forfeits one and can actively degrade.

CopperMCP already caps history at `_MAX_HISTORY` and the penalty at `_MAX_PENALTY_NM`. This note is
the justification for keeping those caps rather than treating them as arbitrary safety limits.

**Declared literals and why.**

| Literal | Grounding |
|---|---|
| `accumulated-overuse-v1` *(default)* | VPR's `acc_cost` rule with `acc_fac` pinned to 1, which is exactly what ADR-0055 already does. |
| `scaled-accumulation-v1` | The same rule with `acc_fac` exposed as the bounded integer `accumulation_weight` — VPR's `--acc_fac`, made declarable. |
| `saturating-decay-v1` | The fading-factor family: Dr. CU's "fading factor so that history violations more iterations ago will have less impact", TritonRoute's per-iteration decay, and FastRoute's self-correcting virtual capacity. Expressed as an exact integer ratio so no float enters a cost. |

The present-growth ratio lives in this same slot rather than a fourth one, because PathFinder's
iteration update moves both terms and two digests must not be able to describe one behavior. A
ratio of 13/10 reproduces the shape of VPR's `pres_fac_mult`.

## Slot 3 — rip-up selection

**Classic PathFinder rips up everything, every iteration**, and says why: "Only one net is ripped up
at a time, but every net is ripped up and rerouted on every iteration, even if the net does not pass
through a congested area. In this way nets passing through uncongested areas can be diverted to make
room for other nets currently in congested regions." The reason is *second-order congestion* — the
paper's Figure 2 case, where the net that must move is not itself using a congested node: "This
second-order congestion problem cannot be solved using p_n alone. The term h_n is required", and
"Because signal 1 does not use a congested node, determining that it needs to be rerouted will be
difficult in general."

**But the same paper proposes the conflicted-only variant and reports no quality loss.** Section 3.5:
"Another enhancement is to route only the signals involved in congested nodes… To date we have not
seen any cases where routing only congested nodes resulted in a lower-quality route. In our
experience the number of iterations increases, but the total running time decreases." The common
framing that full rip-up trades speed for quality is therefore **not** supported by the original
authors.

Practice followed the enhancement. VPR's `should_route_net()` re-routes a net only when it has no
routing yet, when any node on its tree is over capacity, when a hold constraint forces it, or when
sinks remain — and additionally reuses the uncongested part of a high-fanout net's tree
(`--min_incremental_reroute_fanout`, default 16). CUGR rips up "those nets with violations". Dr. CU
rips up "only nets with violations… to save runtime", expanding their routing regions slightly.
TritonRoute rips up only nets attached to a current DRC marker. NTHU-Route selects congested
two-pin nets with both pins inside an expanded congested region, and re-routes a marked net "only
when it remains congested after the nets prior to it have been processed". BoxRouter 1.0 declines
rip-up entirely,
arguing that it "deprives" a wire of capacity "without guaranteeing any improvement", in favour of
voluntary release that happens "only when the solution improves".

**Declared literals and why.**

| Literal | Grounding |
|---|---|
| `all-nets-v1` *(default)* | Classic PathFinder, and what ADR-0055 does today. Default because it is the measured behavior, and because it is the only rule that addresses second-order congestion without needing to detect it. |
| `conflicted-only-v1` | PathFinder §3.5, VPR, CUGR, Dr. CU, TritonRoute. |
| `top-conflict-only-v1` | **CopperMCP-original.** No published router selects a bounded top-k by conflict score. The nearest analogues — GRIP's subregion ordering by decreasing total edge overflow, and NTHU-Route 2.0's refinement ordering by decreasing overflowed-edge count — are *orderings*, not selection cutoffs. It exists here as a work-bounding knob and claims no pedigree. |

## Learning that touches ordering and weights but never the search

This is the pattern the project's boundary depends on, and it is real published practice rather
than a convenient framing.

- **Qu et al., DATE 2021** — asynchronous RL for detailed-routing net order. "We define the
  environment as the router, the agent as a net order planner… The net ordering result is the
  action." The action is a vector of per-net ordering scores; the state is seven per-net features;
  the underlying router (Dr. CU 2.0) and its path search are untouched. Reported: 14% fewer DRC
  violations, 0.7% lower total cost.
- **Offline RL over cost weights** (Khan and Rovinski, DATE 2026), already recorded in the
  multi-pin note: a conservative Q-learning policy picks per-iteration cost weights while "the
  underlying search stays deterministic".
- **Non-learned prediction feeding the loop**: VTR seeds `acc_cost` above 1.0 for wires in channels
  whose post-placement utilization estimate exceeds a threshold
  (`--initial_acc_cost_chan_congestion_threshold`), so that "the router avoid[s] channels that are
  likely to be congested". A congestion *predictor* biasing the negotiation state, with the search
  unchanged.

CopperMCP's structural version of this boundary is stronger than a convention: a slot value is a
member of a closed enumeration, so a future selector — learned or otherwise — can pick among rules
and weights but cannot author a rule body at all.

## What this means for CopperMCP

1. **Name the rules after what they are.** The default cost update is VPR's, not PathFinder's.
2. **Keep the history and penalty caps, and say why.** BoxRouter 2.0's instability result and
   PathFinder's Theorem 1 are the evidence.
3. **Default to `all-nets-v1` on measurement, not on literature.** The literature mildly favours
   conflicted-only; ADR-0055's measured behavior is all-nets, and changing a default needs its own
   evidence.
4. **Expect ordering to move rule violations more than wirelength.** Qu et al.'s 300-order sweep is
   the calibration. A large wirelength swing from ordering alone on a small synthetic fixture is
   evidence about the fixture, not about routing.
5. **A partial rip-up rule needs the acceptance gate to say what it refused.** Every conflicted-only
   router above knows which nets carry a violation. CopperMCP's clearance gate did not report that,
   which is why this slice makes it attribute the offending pair.

## Explicit non-claims

These were sought and **not** verified. Nothing in this repository depends on them, and they must
not be cited as established.

- `h_n^(i+1) = h_n^(i) + max(0, occ − cap)` **as PathFinder's rule**, or any closed form for
  PathFinder's `p_n`. The paper publishes neither.
- "A Smoothing Iterative Routing Algorithm for FPGAs" as a McMurchie/Ebeling title — no evidence it
  exists.
- Any blanket statement that VPR routing is deterministic given a fixed seed. VPR's `--seed` is
  documented as the **placer** seed. The published determinism claim covers VTR's `parallel` and
  `parallel_decomp` routers ("Both routers are deterministic and serially equivalent"), and is
  conditioned on heuristic admissibility for the parallel connection router.
- Which VTR release removed `breadth_first` from `--router_algorithm`. It is absent from current
  documentation; the removing version was not established.
- FastRoute's rip-up granularity. Its journal article says it "only rips up net in congestion
  region"; SPRoute, built on its engine, describes "one pass to rip up and reroute all the nets".
  Unresolved.
- Dr. CU's numeric history discount and fading-factor values — described qualitatively only.
- "Top-k by conflict score" as an established rip-up rule. No primary source found.
- Any head-to-head ablation of linear versus exponential versus capped versus decaying history
  under otherwise identical conditions. This appears not to exist in the accessible literature; it
  is a genuine gap, and any such comparison CopperMCP wants must be measured here rather than cited.
- Archer, NCTU-GR 1.0/2.0, FGR, GAMER, CUGR2/EDGE, GGR, and the ISPD 2024/2025 contest winners:
  paywalled or unavailable, and not summarized above.
- "Revisiting PathFinder Routing Algorithm" (FPGA 2022) is highly relevant by title and was not
  readable; it is the first thing to check when institutional access exists.

## Sources

- PathFinder, McMurchie and Ebeling, FPGA 1995 — technical-report full text:
  https://ee.sharif.ir/~asic/References/Physical%20Design%20Papers/pathfinder-TR3.pdf ·
  canonical venue (paywalled): https://dl.acm.org/doi/10.1145/201310.201328
- VTR cost expression: https://raw.githubusercontent.com/verilog-to-routing/vtr-verilog-to-routing/master/vpr/src/route/route_common.h
- VTR `acc_cost` update and initialization: https://raw.githubusercontent.com/verilog-to-routing/vtr-verilog-to-routing/master/vpr/src/route/route_common.cpp
- VTR `should_route_net`: https://raw.githubusercontent.com/verilog-to-routing/vtr-verilog-to-routing/master/vpr/src/route/route_net.cpp
- VTR `pres_fac` schedule: https://raw.githubusercontent.com/verilog-to-routing/vtr-verilog-to-routing/master/vpr/src/route/route.cpp
- VTR option defaults: https://raw.githubusercontent.com/verilog-to-routing/vtr-verilog-to-routing/master/doc/src/vpr/command_line_usage.rst ·
  rendered: https://docs.verilogtorouting.org/en/latest/vpr/command_line_usage/
- Parallel VPR router determinism (Koşar, MASc 2023): https://utoronto.scholaris.ca/server/api/core/bitstreams/f7e3bbcf-1ca7-4f4c-b0bc-902a47a3aa46/content
- NTHU-Route 2.0: https://www.cecs.uci.edu/~papers/iccad08/PDFs/Papers/05A.1.pdf
- FastRoute (VLSI Design 2012, open access): https://downloads.hindawi.com/archive/2012/608362.pdf
- BoxRouter 1.0: https://www.cerc.utexas.edu/utda/publications/tcad2007Boxrouter.pdf ·
  BoxRouter 2.0: https://www.cerc.utexas.edu/utda/publications/a32-cho.pdf
- CUGR: https://cwpui.com/doc/c10.pdf · Dr. CU: https://chengengjie.github.io/papers/J3-TCAD20-DrCU.pdf
- TritonRoute: https://vlsicad.ucsd.edu/Publications/Journals/j133.pdf · SPRoute: https://csl.yale.edu/~rajit/ps/sproute.pdf
- FastGR: https://yibolin.com/publications/papers/ROUTE_DATE2022_Liu.pdf ·
  HeLEM-GR: https://yibolin.com/publications/papers/ROUTE_ICCAD2024_Zhao.pdf
- GRIP: https://jlinderoth.github.io/papers/Wu-Davoodi-Linderoth-10-PP.pdf ·
  Bancajas et al., ICCAD 2011: http://dbancajas.github.io/files/iccad2011.pdf
- Qu et al., DATE 2021, RL net ordering: https://yibolin.com/publications/papers/ROUTE_DATE2021_Qu.pdf
