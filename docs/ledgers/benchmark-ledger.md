# Benchmark Ledger

No routing benchmark claims are recorded because the repository does not yet ship a router.

> **Amendment — 2026-08-03:** ADR-0006 supersedes the final clause above: the repository now ships a
> narrow candidate-only two-pin A* reference. No routing benchmark claim is recorded yet; the first
> result must satisfy the evidence table below and must not be generalized beyond its synthetic
> fixture and supported geometry.

> **Second amendment — 2026-08-03:** B-003 supersedes the prior “no routing benchmark” clause with
> one narrow synthetic optimality/reproducibility baseline. It is not a KiCad, whole-board,
> cross-router, or production-performance result.

> **Third amendment — 2026-08-03:** B-004 is the current-contract corrective result. B-003 remains
> immutable historical evidence but omitted explicit unrouted, via, aggregate-length, and cleanup
> fields required by this ledger.

Every future entry must include:

| Field | Requirement |
|---|---|
| Run ID | Stable content-addressed identifier |
| Date and commit | UTC date, exact Git commit, dirty-tree status |
| Environment | CPU/GPU, memory, OS, dependency and KiCad versions |
| Dataset | Name, licence, provenance, split, and exclusion rules |
| Configuration | Router, policy/model digest, seed, time/memory budget |
| Metrics | Completion, hard DRC, unrouted, vias, length, runtime, memory, cleanup |
| Artifacts | Machine-readable results and logs without proprietary content |
| Interpretation | Limitations, failures, and comparison caveats |

Never delete failed runs. Supersede an invalid result with a linked corrective entry.

## Non-routing baselines

These measurements exercise infrastructure that future routers will consume. They are explicitly
not routing-quality or throughput claims.

### B-001 — CopperTone KiCad-to-Board-IR conversion

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:44f6f4b385085ee874cdfb55036d3c2fe73246717d36f856a98cc427ec2e8387` |
| Date and commit | 2026-08-02 20:16:50 UTC; `25e9d34d4dbba6e19097edc9d0686437eb49a1d8`; clean tree |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Committed CopperTone board, CERN-OHL-S-2.0, 166,070 bytes, SHA-256 `3bcd01ec4942fccabfaf1c21bdae050a31a7bf99af7ab1bcb0dbb3d0aabcfb94`; one integration fixture, no train/test split |
| Configuration | `board-ir-kicad-conversion-v1`; default parser budgets; one recorded default net class; two warmups; seven measured iterations; CPU-only; no router, policy, model, or seed |
| Metrics | Median 345,090,292 ns (min 338,680,209; max 366,999,959) with `tracemalloc` enabled; median incremental peak 4,914,881 bytes (max 5,176,681); 40,810-byte canonical snapshot |
| Artifact | [`2026-08-02-coppertone-25e9d34.json`](../../benchmarks/results/board-ir/2026-08-02-coppertone-25e9d34.json) |
| Interpretation | Parses and validates 14 nets, 55 pads, 9 vias, 53 segments, 2 zones, and 2 keepouts deterministically on one host. Instrumentation affects timing. No routing, DRC, SI/PI, audio, DFM, cross-host, or fabricated-board conclusion may be drawn. |

### B-002 — Hardened CopperTone KiCad-to-Board-IR conversion

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:18d0a8d6fe6e1951b8d7b32f20d79e10dfc74f169cd435e96063b5a8b7079c0d` |
| Date and commit | 2026-08-02 21:42:27 UTC; `db0f1681ae5aecdf523ec6884c8f359661ab5d1a`; clean tree |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Committed CopperTone board, CERN-OHL-S-2.0, 166,070 bytes, SHA-256 `3bcd01ec4942fccabfaf1c21bdae050a31a7bf99af7ab1bcb0dbb3d0aabcfb94`; one integration fixture, no train/test split |
| Configuration | `board-ir-kicad-conversion-v1`; default parser budgets; one recorded default net class; two warmups; seven measured iterations; CPU-only; no router, policy, model, or seed |
| Metrics | Median 367,226,125 ns (min 363,958,959; max 374,307,792) with `tracemalloc` enabled; median incremental peak 2,026,494 bytes (max 2,124,825); 40,942-byte canonical snapshot |
| Artifact | [`2026-08-02-coppertone-db0f168.json`](../../benchmarks/results/board-ir/2026-08-02-coppertone-db0f168.json) |
| Interpretation | Current-contract replacement for B-001 after exact zone semantics, quote-aware KiCad parsing, fail-closed semantic preflight, and streaming allocation hardening changed canonical meaning and the snapshot digest to `sha256:edd6d3c778f7a153c3bd12588d162b73ae50e5d81de56eaef138da9570ec4fd0`. B-001 remains historical evidence but is superseded for current-contract comparisons. Instrumentation and one-host run noise prevent a general performance claim. No routing, DRC, SI/PI, audio, DFM, cross-host, or fabricated-board conclusion may be drawn. |

## Routing correctness baselines

### B-003 — Synthetic two-pin A*/Dijkstra optimal-cost comparison

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:8b7dadf774868dd0694c148a87961b458525e68d9ff63b5a4f3fa5e4b075639c` |
| Date and commit | 2026-08-02 22:37:13 UTC; `15f3931e8fb59b390721c3cc4c47445911580771`; clean tree |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Four deterministic Apache-2.0 synthetic Board IR fixtures generated by `scripts/benchmark_routing.py` (generator SHA-256 `972497b3048dc52f507cb8b515aace2016aae872570407ef81101a1c9eb84e97`): straight, rectangular detour, exact-clearance channel, and retained expected no-path; no external boards, exclusions, or train/test split |
| Configuration | `orthogonal-a-star-v1` against benchmark-only `orthogonal-dijkstra-oracle-v1`; seed 7; 1,000 nm grid; 500 nm bend and 50 nm proximity penalties; 1,000-node, 5,000-expansion, 128-obstacle, and 100,000-obstacle-check ceilings; two warmups and seven measured iterations per fixture/backend; CPU-only |
| Metrics | A*/Dijkstra completion matched 4/4; exact total cost, bend count, and proximity steps matched 3/3 completed fixtures; the blocked fixture remained `no_path`; completed A* candidates reported zero internal violations. Median A*/Dijkstra times with `tracemalloc` were 2.321/9.125 ms straight, 6.961/15.772 ms detour, 3.845/9.417 ms exact-clearance, and 11.445/10.434 ms blocked. Authoritative DRC was not run because the fixtures are not KiCad boards. |
| Artifact | [`2026-08-03-synthetic-15f3931.json`](../../benchmarks/results/routing/2026-08-03-synthetic-15f3931.json) |
| Interpretation | This detects heuristic/search-order mistakes against a zero-heuristic oracle over the same bounded preparation and edge-cost evaluators. Shared evaluators can share bugs; four tiny generated cases do not establish general correctness, throughput, memory scaling, KiCad legality, whole-board completion, SI/PI, EMC, DFM, fabricated-board success, or superiority over another router. Timings include instrumentation and one-host noise. |

### B-004 — Corrected synthetic two-pin A*/Dijkstra comparison

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:2515626f493e201108d519160ccd3a9d90c5f93fd3d9016c180ed2edf1953636` |
| Date and commit | 2026-08-02 22:43:18 UTC; `5adcc07554d83bc43a0eadc37d1816fcf095ab4a`; clean tree |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Same four deterministic Apache-2.0 synthetic Board IR fixture families as B-003, regenerated by `scripts/benchmark_routing.py` at SHA-256 `c4ae45282c6363a2da396d21c24f1ebabfd18dab8c9955cb52c9fce595a1e4f6`; expected no-path retained; no external boards, exclusions, or train/test split |
| Configuration | `orthogonal-a-star-v1` against benchmark-only `orthogonal-dijkstra-oracle-v1`; seed 7; 1,000 nm grid; 500 nm bend and 50 nm proximity penalties; 1,000-node, 5,000-expansion, 128-obstacle, and 100,000-obstacle-check ceilings; two warmups and seven measured iterations per fixture/backend; CPU-only |
| Metrics | Completion matched 4/4; exact total cost, bend count, and proximity steps matched 3/3 completed fixtures; aggregate completed-candidate length 28,000 nm; zero completed-candidate unrouted connections, vias, and internal violations; one expected `no_path`. Authoritative DRC was not run. Cleanup was not applicable because no candidate was applied. Median A*/Dijkstra times were 2.320/9.350 ms straight, 7.059/16.402 ms detour, 2.926/8.441 ms exact-clearance, and 9.981/9.380 ms blocked; corresponding median incremental peaks were 9,909/21,480, 22,246/21,144, 10,250/11,616, and 16,840/11,864 bytes. |
| Artifact | [`2026-08-03-synthetic-5adcc07.json`](../../benchmarks/results/routing/2026-08-03-synthetic-5adcc07.json) |
| Interpretation | Corrective current-contract replacement for B-003 after the result invariant was tightened and all mandatory metric statuses were made explicit. It checks A* against a zero-heuristic oracle over shared preparation and edge-cost evaluators; shared evaluator bugs remain possible. The four tiny, instrumented, one-host fixtures provide no KiCad, scaling, cross-router, whole-board, SI/PI, EMC, DFM, or fabricated-board evidence. |
