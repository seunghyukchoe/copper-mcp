# Multi-pin routing references

Literature and licensing survey for the multi-pin Steiner routing contract, gathered 2026-08-03
(initial pass plus a deep 2022-2026 sweep). Concepts referenced here inform CopperMCP's own
implementations; no external code is copied.

> **Source verification, 2026-08-03.** An independent audit confirmed the pre-2026 literature cited
> here (Hwang 1976, Takahashi-Matsuyama 1980, FLUTE, GeoSteiner, PathFinder, TritonRoute, Magic,
> Shewchuk, CGAL, *Build Systems a la Carte*) and the licensing claims. Identifiers in the
> `2512.*`, `2602.*`, `2605.*` and `2607.*` arXiv ranges post-date the audit tooling's knowledge
> cutoff and **could not be confirmed**; they are recorded as reported, not as verified sources,
> and nothing in this repository depends on them. Specifically unconfirmed: `2607.05915`
> (PCBWorld), `2607.22761` (DRC-Aid), `2607.21850` (SCALE), `2605.15669` (Rule2DRC),
> `2512.03594` (offline RL over cost weights), `2602.00510` (PCBSchemaGen).

## Licensing boundary

- **freerouting** (Java push-and-shove autorouter) is **GPL-3.0**: concepts only, never code, in
  this Apache-2.0 repository. Its underlying techniques — Lee-style maze routing, SPECCTRA-era
  push-and-shove, rip-up/reroute — come from decades-old public literature and may be reimplemented
  independently from the papers.
- **GeoSteiner** (exact planar Steiner) and **FLUTE** (lookup-table RSMT) carry
  non-commercial/academic encumbrances — FLUTE's lookup tables are the encumbered part even where
  wrapper code is redistributed under permissive terms — and **REST** (DAC 2021 RL RSMT) uses
  CUHK's non-OSI CU-SD license. None may become dependencies of this project without independent
  legal review. The first routing slice deliberately uses a clean-room MST-over-components order
  instead, and the candidate's recorded `ordering_policy` keeps richer topology sources external.
- **TritonRoute / OpenROAD `drt`** and **InstantGR** (ICCAD 2024 GPU global routing) are
  **BSD-3-Clause**: legitimate architectural references. **OrthoRoute** (hobbyist GPU PathFinder
  for KiCad) is MIT.

## Obstacle-avoiding RSMT (OARSMT)

- Exact methods have been quiet since Huang & Young (ICCAD 2010); the standard heuristics remain
  the spanning-graph family (Lin et al., TCAD 2008) and multi-layer successors. No
  permissively-licensed reference code exists; implementations are clean-room by necessity.
- Guo, Kong & Feng (arXiv:2503.07268, ACM TODAES 2025) describe a rule-based OARSMT generator
  feeding OARSMT-guided obstacle-aware sparse maze routing — the same topology-then-bounded-maze
  split as CopperMCP's sequential component merging, validating the architecture as current
  practice.
- Learned OARSMT (MazeNet arXiv:2410.18832, preprint; DRL ML-OARSMT, Springer 2025) offers no
  certificates and belongs, at most, behind the ordering/topology policy seam.

## RSMT construction beyond FLUTE

- **NN-Steiner** (AAAI 2024, arXiv:2312.10589): neural components inside Arora's PTAS skeleton —
  the most rigorous ML-Steiner work. **GAT-Steiner** (ICCAD 2024, arXiv:2407.01440): GNN Steiner
  point prediction with honest miss-rate reporting. **GPU-FLUTE** (ICCAD 2022): 10× parallel FLUTE.
- The augmented-BVH decomposition idea (arXiv:2503.02319, preprint) — recursive partition around a
  small exact solver — is license-free and the most transferable scaling technique for high-pin
  merge ordering.

## Detailed routing

- TritonRoute (TCAD 2021, BSD-3): pin access → track assignment → initial route →
  search-and-repair with escalating cost weights → integrated DRC engine. The ISPD detailed-routing
  contests ended in 2019; ISPD 2024/2025 moved to (GPU/performance-driven) global routing.
- **Offline RL over cost weights** (Khan & Rovinski, arXiv:2512.03594, DATE 2026): a conservative
  Q-learning policy picks per-iteration cost weights; the underlying search stays deterministic.
  1.56× average convergence speedup on ISPD19 with DRV parity. This is the highest-value contract
  lesson for CopperMCP: iteration/cost schedules should be declared, serialized, hashable request
  parameters so a future policy can select them without touching the deterministic core.

## PCB-specific routing

- Credible peer-reviewed work is sparse: any-direction length-matching with region assignment then
  in-region meander (DAC 2024, arXiv:2407.19195; TODAES 2026 follow-on), unified constraint/diff-pair
  routing (ASP-DAC 2021). Push-and-shove has never been formalized academically (KiCad's router is
  documented only in a FOSDEM 2015 talk).
- **PCBWorld** (arXiv:2607.05915, CC0): a Gym environment over KiCad with DRC-scored evaluation on
  10k synthetic boards plus 679 real open-source boards (PCBench). This is the external benchmark
  corpus CopperMCP should eventually measure against.
- Commercial AI routers (DeepPCB, Quilter) publish no peer-reviewed evidence; treat their numbers
  as marketing.

## Multi-net ordering and negotiation

- PathFinder (FPGA 1995) remains the substrate. The consistent 2024-2026 pattern: learning is
  applied to ordering (Sci. Rep. 2024), batching (GANGR, DATE 2026), rip-up selection (RL-Ripper,
  TODAES 2024), and cost weights — never to the path search itself. Design consequence for the
  future negotiation phase: net order, history-cost update rule, and rip-up selection should be
  three separate declared policy slots, each defaulting to a deterministic heuristic.

## Verification-coupled routing

- The field converged on DRC-as-oracle-in-a-loop in 2026: DRC-Aid (arXiv:2607.22761) gates a
  bounded edit-menu search with commercial DRC/LVS at every step; SCALE (arXiv:2607.21850) and
  Rule2DRC (arXiv:2605.15669) are adjacent. PCBWorld scores agents with KiCad DRC.
- Nobody retains the DRC run as a durable, hash-bound evidence artifact tied to the specific
  routing decision that produced it. That remains CopperMCP's unoccupied differentiator and is
  worth stating explicitly in public docs. Two ideas worth adopting from DRC-Aid regardless: the
  bounded rule-derived edit menu (fail-closed repair), and a memory bank preventing cyclic
  re-exploration in negotiation loops.

## Sources

- https://arxiv.org/abs/2503.07268 · https://arxiv.org/abs/2410.18832
- https://arxiv.org/abs/2312.10589 · https://arxiv.org/abs/2407.01440 · https://arxiv.org/abs/2503.02319
- https://github.com/cuhk-eda/REST · http://www.geosteiner.com/ · https://github.com/The-OpenROAD-Project-Attic/flute3
- https://vlsicad.ucsd.edu/Publications/Journals/j133.pdf · https://github.com/The-OpenROAD-Project/OpenROAD/blob/master/src/drt/README.md
- https://arxiv.org/abs/2512.03594 · https://arxiv.org/abs/2407.19195 · https://arxiv.org/html/2607.05915v1
- https://github.com/cuhk-eda/InstantGR · https://arxiv.org/abs/2511.17665 · https://github.com/bbenchoff/OrthoRoute
- https://arxiv.org/abs/2607.22761 · https://arxiv.org/abs/2605.15669 · https://arxiv.org/html/2607.21850v1
