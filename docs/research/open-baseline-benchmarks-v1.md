# Open-baseline routing benchmarks and the SimpleRouteJson import seam

Research basis for issues [#65](https://github.com/seunghyukchoe/copper-mcp/issues/65) and
[#96](https://github.com/seunghyukchoe/copper-mcp/issues/96), reviewed 2026-08-06. It records the
interchange format we import, the licensing determination for every corpus considered, the harness
design lessons the prior art supplies, and — most importantly — what a number measured on any of
these corpora is *not* allowed to claim.

## 1. Why an import seam rather than a tool

CopperMCP's routing core already has an entry path: KiCad bytes to Board IR to a `RouteRequest`.
An external benchmark corpus needs the same treatment, not a parallel one. The adapter added here
(`src/copper_mcp/benchmarks/simple_route_json.py`) produces a `BoardIRSnapshot` and ordinary
`RouteRequest` values, so every imported problem passes through the same canonical verification,
the same clearance model, and the same typed refusal taxonomy as a KiCad board. It has no MCP
exposure, no apply authority, and no file mutation, and it lives in a `benchmarks` subpackage
whose module docstring says so. This is a `D-` decision, not an ADR: no public contract moves.

## 2. SimpleRouteJson, as actually specified

The authoritative type is in the tscircuit autorouting-dataset repository, not in its README:

- <https://github.com/tscircuit/autorouting/blob/main/module/lib/solver-utils/SimpleRouteJson.ts>
  — `SimpleRouteJson { layerCount, minTraceWidth, obstacles, connections, bounds }`, with
  `bounds: { minX, maxX, minY, maxY }` and
  `SimpleRouteConnection { name, pointsToConnect: Array<PointWithLayer> }`.
- <https://github.com/tscircuit/autorouting/blob/main/module/lib/types.ts> — the obstacle type is
  `{ type: "rect", layers: string[], center, width, height, connectedTo: string[] }`, carrying the
  in-source comments `// TODO include ovals` and `// NOTE: most datasets do not contain ovals`.
- <https://github.com/tscircuit/autorouting/blob/main/AUTOROUTING_API.md> — states only that a
  solver must accept the format, and links to the type above. It documents no fields, no units,
  and no layer names. Do not cite it as the field specification.

Three consequences shaped the adapter:

1. **The published spec admits `rect` only, but real corpora contain `oval`.** All 36 boards in
   dwiel/tscircuit-benchmark use both: 1,116 `rect` and 229 `oval` obstacles. An importer written
   to the spec alone would refuse a third of every board's geometry. The adapter therefore
   represents both and refuses any third type.
2. **Units are millimetres by convention, never by specification.** Every coordinate field is a
   bare `number`. The millimetre reading rests on the repository's own `distant-single-trace`
   description ("Long (200mm+) single trace") and on tscircuit circuit-json's mm-suffixed values.
   Because the convention is undocumented, the adapter states its own rule explicitly and records
   it in every artifact rather than leaving it implicit.
3. **Layer names are bare strings.** `"top"`, `"bottom"`, and `"inner1"` appear only as example
   values. The corpus additionally names `inner1`/`inner2` on boards whose `layerCount` is 2 — an
   artefact of plated-hole conversion. An obstacle naming a layer outside the declared stack is
   widened to the whole stack rather than dropped.

### The millimetre-to-nanometre rule

Stated once here and enforced in one function:

> SimpleRouteJson millimetre values are read as their **literal JSON tokens**, never as floats, and
> converted through `decimal.Decimal` at exactly 1,000,000 nanometres per millimetre. A token with
> at most six fractional digits and no exponent converts identically to
> `copper_mcp.board_ir.mm_to_nm`, the rule the KiCad adapter uses, and a test asserts that equality
> directly. `NaN`, `Infinity`, `-Infinity`, a token longer than the declared budget, and a value
> outside the declared board extent are each a typed refusal rather than a clamp.

The corpus forces the interesting half of this. 527 of its numeric tokens carry sub-nanometre
digits — `2.9000000000000004`, `-1.5299999999999998` — which are IEEE-754 residue from the
JavaScript pipeline that wrote them. Refusing them outright would have refused 33 of 36 boards;
rounding them to the nearest nanometre would have silently moved copper in an unstated direction.
The adapter instead resolves the residue **by geometry role, in exact decimal arithmetic**:

| Geometry | Rounding | Why |
|---|---|---|
| Obstacle and pad rectangles | low edge floored, high edge ceiled | the mapped rectangle contains the source shape |
| Board outline | low edge ceiled, high edge floored | outline is routing *room*; growing it would hand the router area the document never granted |
| `minTraceWidth` | ceiled | a wider trace is the harder problem |

The largest movement any edge can make is one nanometre, and the harness records the observed
maximum (`max_outward_rounding_nm`) per board rather than asserting it. Three of the twenty
committed boards need no rounding at all.

## 3. Licensing determination

Checked 2026-08-06 before any external byte was committed.

| Corpus | Licence | Evidence | Determination |
|---|---|---|---|
| [dwiel/tscircuit-benchmark](https://github.com/dwiel/tscircuit-benchmark) | **MIT**, © 2026 Zach Dwiel | [`LICENSE`](https://github.com/dwiel/tscircuit-benchmark/blob/master/LICENSE); GitHub API `spdx_id: MIT`; README `## License` section reads `MIT` | **Redistribution permitted with attribution.** 20 of 36 boards committed under `benchmarks/corpora/tscircuit-benchmark/` with the upstream `LICENSE` and an `ATTRIBUTION.md`; SHA-256 recorded for all 36. |
| [tscircuit/autorouting](https://github.com/tscircuit/autorouting) | **None** | No `LICENSE`/`COPYING` file in the repository root; no `license` key in [`package.json`](https://raw.githubusercontent.com/tscircuit/autorouting/main/package.json); no licence text in [`README.md`](https://raw.githubusercontent.com/tscircuit/autorouting/main/README.md) or [`BENCHMARKS.md`](https://raw.githubusercontent.com/tscircuit/autorouting/main/BENCHMARKS.md); GitHub API `license: null`. Repository archived 2025-08-15. | **All rights reserved. Nothing redistributed.** Its format specification is cited; no file is copied. Its tiered problems are consequently out of reach for an in-repo corpus. |
| [PCBench](https://github.com/PCBench/PCBench) | **Repository: MIT**, © 2023 PCBench. **Board data: heterogeneous, and 22 % unlicensed** | GitHub API `spdx_id: MIT`; [`LICENSE`](https://github.com/PCBench/PCBench/blob/main/LICENSE) read in full at commit `dec3be7`, `sha256:27f95dfaac3ac61888e14732fb83736da6880127c4a5d3093446e26e33ee1cb8`. **But** `github_meta/*.json` are stored GitHub search results over 1,018 repositories, and each `PCBs/*/metadata.json` records that board's own `source` and upstream licence | ~~**Redistributable, but not imported by this adapter.**~~ **Corrected 2026-08-14: not redistributable.** The MIT grant covers PCBench's own code, not boards it scraped and could not relicense — of its advertised 164, **36 have no licence at all**, 57 are copyleft or CERN-OHL, 53 permissive. Ruled out by the same reasoning already applied to PCBWorld's D3, one row down. Separately, [B-110](../ledgers/benchmark-ledger.md) measured **0 of 164 converting**. See [ADR-0107](../adr/0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md), [D-199](../ledgers/decision-ledger.md). |
| PCBWorld ([arXiv:2607.05915](https://arxiv.org/abs/2607.05915)) | **Split.** Synthetic sets D1/D2, their generators, and the evaluation code are CC-BY 4.0; the 679 real boards in D3 "retain the license of their source repository" and are heterogeneous | Appendix P and O.7 of <https://arxiv.org/html/2607.05915v2> | **Not obtainable today.** The paper states the datasets "will be released on a public repository upon publication"; no GitHub, HuggingFace, or Zenodo host exists yet. Recorded as announced, not released. The CC0 on the arXiv submission covers the paper, not the data. |
| [freerouting/freerouting](https://github.com/freerouting/freerouting) | **GPL-3.0**, current release v2.2.4 (2026-05-13) | GitHub API `spdx_id: GPL-3.0`; [`LICENSE`](https://github.com/freerouting/freerouting/blob/master/LICENSE) | **Invocable as an out-of-process baseline only.** Linking or vendoring its code into this Apache-2.0 repository would be licence-incompatible. It is not installed in the recording environment, so the benchmark records it as `not_run`. |

The issue text for #65 described PCBWorld/PCBench as CC0. That is corrected here: PCBench is MIT
and PCBWorld is CC-BY-4.0-plus-heterogeneous. Neither is CC0.

> **Amendment — 2026-08-14.** The PCBench row above carried a second error, and this note is where
> it was made. Reading the `LICENSE` was not the same as determining the licence of the *data*:
> PCBench aggregates 1,018 other repositories, and the row treated the aggregator's MIT grant as if
> it reached the boards. It does not. Corrected in the row, and generalised into an intake rule as
> [ADR-0107](../adr/0107-an-aggregators-licence-does-not-govern-what-it-aggregated.md) so the same
> mistake is not available to the next corpus. The instructive part is that this note **already had
> the right analysis one row down** — PCBWorld's D3 boards were ruled out for exactly this reason,
> because the paper says so in prose. PCBench says it in per-item metadata, and a determination made
> from the repository root could not see it.

## 4. What the prior art gets right, and what it gets wrong

### tscircuit/autorouting — the harness to learn from and not to repeat

Worth keeping:

- **A tiered difficulty ladder.** Its published tiers are `single-trace`, `traces`,
  `distant-single-trace` (all implemented), plus `single-trace-group`, `layers-traces`,
  `traces-groups`, `layers-traces-groups`, `width-constraints-*`, `hyperdense-*`, and
  `incremental-*`. A ladder makes an aggregate number decomposable.
- **A stated sample floor**: "For public evaluations the sample count should be set to at least
  1,000."
- **SVG snapshot regression**, which its README calls "INCREDIBLY USEFUL", implemented through
  `bun-match-svg`.

Worth not repeating, and each of these is a first-hand citation rather than a survey claim:

1. **The ladder was mostly aspirational.** 8 of its 11 tiers were permanently `TBA`, and
   `layers-traces` appears twice with contradictory descriptions and difficulties. The repository
   was archived on 2025-08-15 with the ladder still unfinished.
2. **The published numbers do not follow the published protocol.** `BENCHMARKS.md` reports 100
   samples per problem type, not the ≥1,000 its own README prescribes.
3. **Failures were silently truncated by default.** "If no `problemType` is provided and the solver
   fails on the first 10 samples, it will not run the remaining samples of the problem type" —
   unless `noSkipping` is set. A weak solver's published score is therefore computed over a
   shortened, non-comparable denominator. **This is exactly the failure mode our harness inverts:
   every attempted net is accounted for by exactly one outcome, and a test asserts that the
   outcome breakdown sums to the attempted count.**
4. **A dataset with no licence cannot be depended on**, however good it is.

### The dwiel corpus — usable, but constrained in ways that matter more than its licence

- **The boards are LLM-generated.** Upstream's README: "36 real-world PCB designs … converted from
  circuit JSON exported by [pcbgen](https://github.com/dwiel/ai-pcb-experiment) … Generated from
  plain-English specs by an LLM, then routed with Freerouting." The phrase "real-world" in that
  sentence is upstream's; it is not accurate, and this repository does not repeat it.
- **FreeRouting was in the construction loop**, and upstream's own table reports FreeRouting
  "clean" on 35 of 36 boards. Using this corpus to score CopperMCP *against FreeRouting* would be
  scoring against a set FreeRouting helped define — a distribution-shift problem in the sense of
  Kapoor & Narayanan's leakage taxonomy. If a FreeRouting comparison is ever run, it must not be
  run on this corpus alone.
- **Narrow coverage**: every board is 2-layer with 3–35 components; no multi-layer, BGA,
  differential-pair, or width-constrained case exists in it at all.
- **One thing upstream does well**: `externallyConnectedPointIds` are deliberately stripped "so
  benchmark nets are not pre-short-circuited by off-board equivalence metadata", with a CI
  guardrail. Our adapter depends on that: obstacle-to-net ownership is derived from `connectedTo`
  against the points the connections actually name.

### General benchmark-design sources

- Kapoor & Narayanan, "Leakage and the reproducibility crisis in machine-learning-based science",
  *Patterns* 4(9):100804, 2023 — <https://arxiv.org/abs/2207.07048> — the eight-type leakage
  taxonomy, and the reason a corpus built with a router in the loop is not a neutral yardstick for
  that router.
- Reuel et al., "BetterBench: Assessing AI Benchmarks, Uncovering Issues, and Establishing Best
  Practices", NeurIPS 2024 Datasets & Benchmarks — <https://arxiv.org/abs/2411.12990> — a
  lifecycle checklist; its headline finding is that most benchmarks are neither statistically
  reported nor easily replicable, which is what the self-digesting artifact and the byte-identical
  replay test exist to prevent here.
- Dehghani et al., "The Benchmark Lottery" — <https://arxiv.org/abs/2107.07002> — rankings flip
  with task selection, so a single headline number over a mixed corpus hides more than it says.
- Chai et al., "CircuitNet: An Open-Source Dataset for Machine Learning Applications in EDA" —
  <https://arxiv.org/abs/2208.01040>, <https://github.com/circuitnet/CircuitNet> (BSD-3-Clause) —
  the positive counter-example: a licensed, versioned, maintained EDA dataset.
- Gebru et al., "Datasheets for Datasets", *CACM* 64(12):86–92 — <https://doi.org/10.1145/3458723>
  — the structure `benchmarks/corpora/*/ATTRIBUTION.md` follows.

## 5. Harness design, and what it deliberately does not do

`scripts/benchmark_simple_route_json_corpus.py` routes every imported net of every committed board
and records, per configuration: boards offered and imported, import refusals by typed code, nets
attempted and routed, the outcome breakdown by `RouteFailureCode`, total routed wire length, a
provable lower bound, via count, bend count, and mean wall time.

Three design choices are worth stating:

**The lower bound is provable, not estimated.** For each routed net,
`max(0, max(pad.min_x) − min(pad.max_x)) + max(0, max(pad.min_y) − min(pad.max_y))`. Any
rectilinear tree touching every pad must span at least that far in each axis. It is loose — it
ignores every obstacle, bend, and detour — which is precisely why a ratio against it is safe to
publish. The exact pad-centre Manhattan distance is recorded separately for two-pin nets, where the
router genuinely joins centres.

**Two grid policies are recorded, not one.** The reference A* router searches a uniform lattice
anchored at a pad centre and additionally requires a two-pin request's pad-centre delta to divide
by the grid step. External boards respect neither constraint. The harness therefore runs the corpus
under a fixed 250 µm step and under a per-net step chosen as the largest ladder entry dividing that
net's pad-centre delta — decided from geometry *before* routing, never by retrying until something
works. Reporting both is the point: the second policy converts `off_grid` refusals into
`grid_budget_exceeded` ones and routes no additional net, which localises the constraint to the
lattice-node budget rather than to grid alignment. That finding would have been invisible under
either configuration alone.

**A baseline that is not installed is `not_run`.** FreeRouting is GPL-3.0 and is not present in the
recording environment; there is no SimpleRouteJson-to-DSN bridge in this repository yet. The
artifact records `status: not_run` with a reason, and the ledger entry says the comparison in #65
remains unmeasured. No number is estimated, inferred, or carried over.

> **Amendment — 2026-08-14.** §3's determinations are now carried into a committed comparison table
> rather than living only here. [B-113](../ledgers/benchmark-ledger.md) declares one row per router
> baseline this section names — FreeRouting on an environment fact, `tscircuit/autorouting` and
> PCBWorld on licence facts — with the unmet preconditions named and the roster pinned by a test, so
> a baseline leaving the table is a test failure rather than a shorter table. The corpora rows are
> carried too, so no §3 row is silently absent. Two things this section said that are worth restating
> because the table now depends on them: the dwiel corpus **is** frozen and redistributable in the
> sense M2's closing condition 3 requires, which is what makes the routing measurement closable
> today; and a FreeRouting row measured on it would still not be neutral, which the table records as
> a caveat on that row rather than discovering later.
>
> Separately, and outside this note's routing scope: [B-114](../ledgers/benchmark-ledger.md) took
> §3's own reasoning to the KiCad intake path and found the constraint is not where this note's
> licence work would suggest. Ten single-author open-hardware boards, verified per item under
> ADR-0107, eight importable on licence alone, all ten clearing the board format version after a
> KiCad 10.0.5 re-save — and none converting. The binding constraint there is geometry coverage.

## 6. Not claimed

Recorded here so the ledger entry can point at one list:

- No comparison against FreeRouting, Electra, or any other router. Every baseline is `not_run`.
- No KiCad DRC, electrical, signal-integrity, thermal, or fabrication claim for any imported board.
- No apply, export, mutation, or live-editor behaviour.
- No whole-board completion result: each net is routed independently against the unrouted
  snapshot, so the candidates are not mutually compatible.
- No generalisation beyond LLM-generated 2-layer tscircuit boards.
- No claim that the committed 20-board prefix represents the full 36-board corpus, or that either
  represents production PCB design.
- No routing-optimality claim: the recorded ratio is against a loose provable lower bound.
