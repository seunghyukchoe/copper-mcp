# Benchmark Ledger

No routing benchmark claims are recorded because the repository does not yet ship a router.

> **Amendment — 2026-08-03:** ADR-0006 supersedes the final clause above: the repository now ships a
> narrow candidate-only two-pin A* reference. No routing benchmark claim is recorded yet; the first
> result must satisfy the evidence table below and must not be generalized beyond its synthetic
> fixture and supported geometry.

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
