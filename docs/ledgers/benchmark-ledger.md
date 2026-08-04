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

### B-005 — Board IR 0.2 CopperTone footprint conversion

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:ca65721f1519104c2d9fc803b401936b5c401b14818f9f0dabc046809453ed48` |
| Date and commit | 2026-08-04 01:19:59 UTC; `cffb67d9dbea86e349e2816a4a487da7666f32c1`; clean tree |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Committed CopperTone board, CERN-OHL-S-2.0, 166,070 bytes, SHA-256 `3bcd01ec4942fccabfaf1c21bdae050a31a7bf99af7ab1bcb0dbb3d0aabcfb94`; one integration fixture, no train/test split |
| Configuration | `board-ir-kicad-conversion-v1`; default parser budgets; one recorded default net class; two warmups; seven measured iterations; CPU-only; no router, policy, model, or seed |
| Metrics | Median 280,239,000 ns (min 276,004,209; max 292,213,667) with `tracemalloc` enabled; median incremental peak 2,066,581 bytes (max 2,183,557); 48,188-byte canonical Board IR 0.2 snapshot |
| Artifact | [`2026-08-04-coppertone-cffb67d.json`](../../benchmarks/results/board-ir/2026-08-04-coppertone-cffb67d.json) |
| Interpretation | Current-contract replacement for B-002 after Board IR 0.2 made 26 footprint poses, pad ownership relationships, lock states, and supported courtyard geometry first-class snapshot content, changing the snapshot digest to `sha256:a3803f87e8c70f300c49f9c3a6f5167a4ad629ebc96a8878aed548591a37c79e`. B-001 and B-002 remain historical evidence. Instrumentation, one-host noise, and changed canonical work make this unsuitable as a general speedup claim. No routing, DRC, SI/PI, audio, DFM, cross-host, or fabricated-board conclusion may be drawn. |

### B-007 — Scene-to-route referential-closure MCP integration

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:405762c9141add014a3752fac6ce662312fbb82230b2302c997976f3828f1036` |
| Date and commit | 2026-08-04 11:59:05 UTC; `23092dbb0eb03e16ab2b3ea8db05ad289d7e21a5`; tracked tree clean; one unrelated untracked handoff file present |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; MCP 2.0.0; Pydantic 2.13.4; jsonschema 4.26.0; KiCad was not invoked |
| Dataset | Catalogue-validated `rc-low-pass-routing-v1`, Apache-2.0, board SHA-256 `8cfea1dbcedbfede05905cdeb36160aa41f1022245f4dfe5c1e723aa503a758b`; licence SHA-256 `947af68b9ff8f542f5bba7a084e343573274741f4d7e4cbe1e4e9668a89331de`; catalogue SHA-256 `a233d5b80241ca562a25b76c5cbf05b8ff0da6ca9971a713907949e0442a63f7`; no third-party content included; one fixture and no train/test split |
| Configuration | `scene-route-referential-closure-v1`; actual `mcp.call_tool` path; one warmup and ten measured repetitions; seed 23; 250,000 nm clearance and track width; 800,000/400,000 nm via diameter/drill; complete 100,000,000 nm square region; script SHA-256 `adebb3768c21ab5ac4898d7a48c6eee934564956c8910df72d2236c52f5670a0`; CPU-only |
| Metrics | Scene returned 3 net references with 14 objects and 0 omissions. Former name-only counterfactual: 0/3 actionable. Revision-bound reference selector: 3/3 actionable. Hidden-name oracle: 3/3 actionable. Candidate equality: 3/3. Stale board refusals: 3/3; stale snapshot refusals: 3/3. Deterministic scene replays: 10/10; route replays: 90/90. Final workspace tree identical; persistent workspace changes: 0. Output schema: 2 closed selector variants, 5 closed status variants, 20 closed record objects, and digest `sha256:38bf57bc4f306dfcbc9ea32a7593d3c24fc53f42ef82b9708ce073be21e70157`. |
| Artifact | [`2026-08-04-scene-action-23092db.json`](../../benchmarks/results/mcp/2026-08-04-scene-action-23092db.json) |
| Interpretation | This is an integration and contract baseline proving that a supported Circuit Scene reference can drive the same deterministic candidate without exposing a KiCad net name. It does not claim KiCad DRC, whole-board completion, multilayer routing, placement, live IPC, electrical performance, fabrication readiness, or performance generalization. Final-tree equality does not prove that no transient filesystem write occurred. |

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

### B-009 — KiCad IPC snapshot-to-Circuit-Scene binding

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:f2dc647c99eefcee985ea6a404d47c228f8548d7854d192707a9055a0cf88802` |
| Date and commit | 2026-08-04 12:35:36 UTC; `ac221f97ee41425c933ba7f51cf6ad5f73c278dd`; tracked worktree dirty because this slice was measured before its evidence commit; three untracked files |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Committed `scene-region.kicad_pcb` Circuit Scene fixture, SHA-256 `c69298f27512becfe4b765b99e75628426711103837ab04f4ea424cc48580a1c`; deterministic fake official-client serialization; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-live-scene-v1`; ten repetitions; fake `kicad-python` client; exact Board IR/Circuit Scene `0.2.0` conversion; source script SHA-256 `d2fbae17c838641a619ebb7f708d248160c0542650001b364c7634d37f7cce43`; CPU-only |
| Metrics | Deterministic replays 10/10; median end-to-end capture/convert latency 1,613,625 ns; board revision `sha256:c69298f27512becfe4b765b99e75628426711103837ab04f4ea424cc48580a1c`; snapshot digest `sha256:e21e0eb1211cda221a94359805955b6aa5c889173e7085f8728070d4e51e7e4a`; scene version `0.2.0`; 10 objects returned; raw source returned `false`; stale board and stale snapshot refusals 1/1 |
| Artifact | [`2026-08-04-live-scene-ac221f9.json`](../../benchmarks/results/mcp/2026-08-04-live-scene-ac221f9.json) |
| Interpretation | This proves deterministic closure from one fake IPC serialization to the existing semantic scene and proves both stale-digest refusal paths. It does not establish a live KiCad session, API-version compatibility with the GUI, live placement/routing authority, render delivery, DRC, ERC, electrical behavior, fabrication readiness, or real-board throughput. |

### B-008 — Redacted KiCad IPC observer contract replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:769c5b016581977a58616138ce719a4e103f3a2606bb067c9672afc63ddb793b` |
| Date and commit | 2026-08-04 12:18:23 UTC; `73e4844ab815fec27b8d77f94d4d525d4df391ce`; tracked tree clean, one pre-existing untracked user handoff file |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Deterministic in-memory fake official-client surface with a 39-byte UTF-8 board serialization, 3 nets, 2 footprints, 6 pads, 4 tracks, 1 via, 1 zone, 3 shapes, and 1 text object; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-observer-v1`; ten repetitions; local fake-client transport; 64 MiB board ceiling; source script SHA-256 `4345b7e5948ca9d846a93c90536a18173b161b42c2a118079c8ae0a942c74e3f`; CPU-only |
| Metrics | Deterministic replays 10/10; median observation latency 14,166.5 ns; board digest `sha256:f2fa39e641c1dfa6ca0f649fd49b46971cb205c42bfec0b33ff6354f3e7813cc`; future-version default refusals 1; TCP endpoint refusals 1; raw board/object content returned `false/false`; all object counts matched the fixture |
| Artifact | [`2026-08-04-kicad-ipc-73e4844.json`](../../benchmarks/results/mcp/2026-08-04-kicad-ipc-73e4844.json) |
| Interpretation | This is a deterministic contract and redaction baseline for the optional local observer. It does not establish a live KiCad session, API-version compatibility with the installed GUI, Circuit Scene binding, placement, routing, DRC, ERC, electrical behavior, fabrication readiness, or throughput on real boards. A real probe was intentionally not claimed because the workstation IPC server is disabled. |

### B-010 — Corrected redacted KiCad IPC observer replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:e64e887072edfc1e9960f22951dfed171a1c2af6b316a4a7a1fcbfc5b5ae6d0f` |
| Date and commit | 2026-08-04 12:46:58 UTC; `0749a640941784196e267a2659a93f90c95653bc`; tracked tree clean, two untracked files (the pre-existing user handoff and the output artifact at measurement time) |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Deterministic 39-byte in-memory fake official-client serialization containing one net plus identifiable private-marker objects returned only by fake collection getters; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-observer-v1`; ten repetitions; local fake-client transport; 64 MiB board ceiling; source script SHA-256 `9c9825c40579db87b949b0ad11fe68080fbd22b0da25f45dcf35a2173d29c97f`; CPU-only |
| Metrics | Deterministic replays 10/10; median observation latency 25,874.5 ns; board digest `sha256:f2fa39e641c1dfa6ca0f649fd49b46971cb205c42bfec0b33ff6354f3e7813cc`; serialized-source counts `nets=1`, all other categories `0`; false-version default refusals 1; future-version default refusals 1; TCP endpoint refusals 1; raw board/object content returned `false/false`; source-bound counts `true` |
| Artifact | [`2026-08-04-kicad-ipc-review-fix.json`](../../benchmarks/results/mcp/2026-08-04-kicad-ipc-review-fix.json) |
| Interpretation | Corrective replacement for the B-008 implementation evidence after review findings: false version results fail closed, counts are tied to one serialization and a byte-identical confirmation, and the redaction metric is computed from identifiable private-object markers in the actual JSON response. It remains a fake-client contract baseline; it does not establish a live KiCad session, API compatibility with the installed GUI, placement, routing, DRC, ERC, electrical behavior, fabrication readiness, or hard pre-allocation memory guarantees. |
