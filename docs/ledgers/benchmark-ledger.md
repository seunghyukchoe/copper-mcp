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

#### B-007 replay — current-contract evidence at `d6ee84b`

The original B-007 run remains immutable. This append-only replay records the same oracle after
the revision-precondition hardening landed in `d6ee84b`; it is not a replacement for the original
result or a general performance comparison.

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:0c47fdf0d2a35602ef6facba43648db09e637a0c897eb0dffbb8a380bae08543` |
| Date and commit | 2026-08-04 14:32:57 UTC; `d6ee84b582f47b1d05aa19b3cd997ac53a560062`; tracked tree clean; one unrelated untracked handoff file present |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; MCP 2.0.0; Pydantic 2.13.4; jsonschema 4.26.0; KiCad was not invoked |
| Dataset | Same catalogue-validated `rc-low-pass-routing-v1` fixture, Apache-2.0, board SHA-256 `8cfea1dbcedbfede05905cdeb36160aa41f1022245f4dfe5c1e723aa503a758b`; licence SHA-256 `947af68b9ff8f542f5bba7a084e343573274741f4d7e4cbe1e4e9668a89331de`; catalogue SHA-256 `a233d5b80241ca562a25b76c5cbf05b8ff0da6ca9971a713907949e0442a63f7`; no third-party content included; one fixture and no train/test split |
| Configuration | `scene-route-referential-closure-v1`; actual `mcp.call_tool` path; one warmup and ten measured repetitions; seed 23; 250,000 nm clearance and track width; 800,000/400,000 nm via diameter/drill; complete 100,000,000 nm square region; script SHA-256 `adebb3768c21ab5ac4898d7a48c6eee934564956c8910df72d2236c52f5670a0`; CPU-only |
| Metrics | Scene returned 3 net references with 14 objects and 0 omissions. Former name-only counterfactual: 0/3 actionable. Revision-bound reference selector: 3/3 actionable. Hidden-name oracle: 3/3 actionable. Candidate equality: 3/3. Stale board refusals: 3/3; stale snapshot refusals: 3/3. Deterministic scene replays: 10/10; route replays: 90/90. Final workspace tree identical; persistent workspace changes: 0. Median latencies (ns): scene 2,620,813; legacy 4,817,979; reference 20,601,437; oracle 15,210,187. Output schema remained 2 closed selector variants, 5 closed status variants, 20 closed record objects, and digest `sha256:38bf57bc4f306dfcbc9ea32a7593d3c24fc53f42ef82b9708ce073be21e70157`. |
| Artifact | [`2026-08-04-scene-action-d6ee84b.json`](../../benchmarks/results/mcp/2026-08-04-scene-action-d6ee84b.json) |
| Interpretation | This is a route-only current-contract replay after the board/snapshot revision-precondition hardening in `d6ee84b`; it confirms referential closure and deterministic route candidate equality on the reviewed fixture. It does not replay or validate file-backed placement CAS, which is covered by placement tests and B-014. Latency medians are instrumented, one-host observations and must not be read as a speedup claim. It still does not claim KiCad DRC, whole-board completion, multilayer routing, placement quality, live IPC, electrical performance, fabrication readiness, or performance generalization. |

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

### B-011 — Live Circuit Scene malformed-request preflight

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:e79370ca790206d7bdde7f69a0cd4a4dc9d535928b8db0c2e6b9ebc5e7e9e73c` |
| Date and commit | 2026-08-04 12:53:00 UTC; `2e4b5d30c6e9515ee6553bd81bbb1078eef7e6f6`; tracked tree clean, two untracked files (the pre-existing user handoff and the output artifact at measurement time) |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Committed `scene-region.kicad_pcb` Circuit Scene fixture, SHA-256 `c69298f27512becfe4b765b99e75628426711103837ab04f4ea424cc48580a1c`; deterministic fake official-client serialization; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-live-scene-v1`; ten valid replays plus one malformed-constraint request; source script SHA-256 `3ab61e242525fef9db8f6487280592f6b88d8c6dc68f8182cad5cd7d78057a4e`; CPU-only |
| Metrics | Deterministic valid replays 10/10; median end-to-end capture/convert latency 3,840,021 ns; scene version `0.2.0`; objects returned 10; stale board/snapshot refusals 1/1; malformed request refusal 1/1; IPC client calls for malformed request `0`; raw source returned `false` |
| Artifact | [`2026-08-04-live-scene-preflight.json`](../../benchmarks/results/mcp/2026-08-04-live-scene-preflight.json) |
| Interpretation | This isolates an application-boundary denial-of-service guard: a malformed live-scene request is rejected before any fake KiCad client is opened, while valid revision-bound scene behavior remains deterministic. It does not establish a live GUI session, placement/routing authority, DRC, ERC, electrical behavior, fabrication readiness, or real-session resource behavior. |

### B-012 — Corrected serialized IPC topology counts

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:338ba88873edc23bebd90652c86d57c1f7d1f16e0a8ffc917a29b3af28df7172` |
| Date and commit | 2026-08-04 13:22:16 UTC; `461322f55d2f79685d7681cd91d560e7de1758a4`; tracked tree clean, one pre-existing untracked user handoff file |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Deterministic 105-byte fake official-client serialization with one direct board net declaration, one nested pad net reference, one footprint/pad, and one `gr_circle`; private-marker collection getters remain available only as leakage sentinels; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-observer-v1`; ten repetitions; local fake-client transport; 64 MiB board ceiling; source script SHA-256 `18c9630485633284d0a58ab0d21217a3c7473566471d2c40f9d6e725b7f77fec`; CPU-only |
| Metrics | Deterministic replays 10/10; median observation latency 41,292 ns; board digest `sha256:8641a71e350494ce284c46b3a4b7e768d8ad6c5d78009f308a212ecdd54343a0`; serialized-source counts `nets=1`, `footprints=1`, `pads=1`, `shapes=1`, all other categories `0`; false/future version refusals 1/1; TCP endpoint refusals 1; raw board/object content returned `false/false` |
| Artifact | [`2026-08-04-kicad-ipc-counts.json`](../../benchmarks/results/mcp/2026-08-04-kicad-ipc-counts.json) |
| Interpretation | Corrective replacement for B-010's count implementation after review: only direct board-level `(net ...)` declarations count as nets, nested copper references do not inflate topology, and `gr_circle` is classified as a shape. It remains a fake-client contract baseline with no live-session, placement, routing, DRC, electrical, fabrication, or throughput claim. |

### B-013 — Revision-bound live route proposal

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:14749ab5c2762da3ed2fc99e7ab4968e7ae0c8ce8ee146f0d3fb8e90d100e6a4` |
| Date and commit | 2026-08-04 13:41:12 UTC; `1c190305d2a46f546d084a14a224e9dc73cc28f9`; tracked tree clean, one pre-existing untracked user handoff file |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Committed `two-pad.kicad_pcb` route fixture; the same bytes are used by the file-backed route oracle and fake IPC client; no external or proprietary board |
| Configuration | `kicad-ipc-live-route-v1`; ten replays; scene `net_ref_id`; both board/snapshot preconditions; fixture SHA-256 `5f88ebcf52cf8f1548990bdbdc1c52ac7a30f39c013366f79b161ec15e1caae2`; script SHA-256 `faeace4413360ba8163a529d5b4e9ee0fd2120784f676fde72946399ad7c16c8`; CPU-only |
| Metrics | Deterministic replays 10/10; median end-to-end capture/convert/route latency 3,606,646 ns; board revision `sha256:5f88ebcf52cf8f1548990bdbdc1c52ac7a30f39c013366f79b161ec15e1caae2`; snapshot/candidate base `sha256:e57e679dc80e2d413c59c186db4ff520a5dc526bb025fde32f3b9eaa8d1e469f`; candidate `sha256:befda305388c5e9d7e46f9ca859af1cf3876d2132cd20c427a292b573bfe9a81`; stale board/snapshot refusals 1/1; forbidden action refusal 1/1 with zero IPC calls; raw source, DRC evidence, fill authority, and apply token all `false` |
| Artifact | [`2026-08-04-live-route-proposal.json`](../../benchmarks/results/mcp/2026-08-04-live-route-proposal.json) |
| Interpretation | This proves the read-only observe-to-propose contract over one exact fake IPC snapshot. It does not establish live GUI-session success, live editor mutation, DRC, placement, electrical behavior, fabrication readiness, or throughput on real boards. |

### B-014 — Revision-bound live placement proposal

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:cad3f2e51065ebc8f2ad0df810d084694c09abaae1d613b2ef3e0c13edaf5535` |
| Date and commit | 2026-08-04 14:02:13 UTC; `707dfbe12119ef8550879393fbe1e2af8d26f190`; tracked tree clean, two untracked files (this artifact before commit and the pre-existing user handoff) |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; `kicad-python` not installed; KiCad IPC server disabled and not invoked |
| Dataset | Committed `placement-legal.kicad_pcb`; the same bytes feed the file-backed placement oracle and fake IPC client; one ref-anchored offset proposal; no external or proprietary board |
| Configuration | `kicad-ipc-live-placement-v1`; ten replays; fake official-client transport; fixture SHA-256 `4396686c92d63969b8c9282530d85b3a220ee38cfc23f16966eacb689e80add3`; script SHA-256 `43674c8bb35cfe30242bea5046585e5a302d14cc98839df040fb515950e42393`; CPU-only |
| Metrics | Deterministic replays 10/10; median end-to-end capture/convert/legalize latency 1,266,333 ns; status `previewed`; candidate `sha256:d8a4cf178732c626c806826792ffdd475e8d29a99c747420f8998f510c621d86`; candidate equality and canonical bytes equality with file oracle `true/true`; stale board/snapshot refusals 1/1; forbidden action refusal 1/1 with zero IPC calls; mutating IPC calls `0`; raw source, DRC, fill, and apply authority all `false` |
| Artifact | [`2026-08-04-live-placement-proposal.json`](../../benchmarks/results/mcp/2026-08-04-live-placement-proposal.json) |
| Interpretation | This proves the revision-bound read-only observe-to-place proposal over one exact fake IPC snapshot. It does not establish live GUI-session success, KiCad placement mutation, single-undo behavior, DRC, ERC, electrical validation, fabrication readiness, or throughput on real boards. |

### B-015 — Revision-bound live editor context

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:7360e85432d0d9930730a9dc366c894778fad66db25d83e6e09f76c520b03470` |
| Date and commit | 2026-08-04 14:19:01 UTC; `98b77ac5b453dd17f355125935125d4994e33dd6`; tracked tree dirty because this slice was measured before its evidence commit; new benchmark and artifact files present |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; `mcp` 2.0.0; `pydantic` 2.13.4; no KiCad process invoked; IPC server disabled |
| Dataset | Deterministic in-memory fake official-client board serialization, active `F.Cu` layer, and two native UUID-bearing selection wrappers; no external or proprietary board; no train/test split |
| Configuration | `kicad-ipc-live-editor-context-v1`; ten repetitions; fake `kicad-python` client; 256-item selection ceiling; source script SHA-256 `84f367b86b1bdb04e524302fa011b34709f9b1624ad1ad04dfadb5e00b43f249`; CPU-only |
| Metrics | Deterministic replays 10/10; unique response digests 1; median capture latency 31,437 ns; selection count 2; stale board/context refusals 1/1; changing active layer changes context digest 1/1; mutating IPC calls 0; raw editor content returned `false` |
| Artifact | [`2026-08-04-live-editor-context.json`](../../benchmarks/results/mcp/2026-08-04-live-editor-context.json) |
| Interpretation | This proves only the bounded read-only editor-context contract over a fake client: active layer and typed native selection references are deterministic and stale/context changes fail closed. It does not establish live GUI compatibility, placement/routing mutation, DRC, ERC, electrical behavior, fabrication readiness, or performance generalization. |

### B-016 — Source-preserving KiCad placement projection

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:a9a2227c5c9beb21f3b43f2bc61c5db315c33922220f648559f9813a54f56ec5` |
| Date and commit | 2026-08-04 14:47:34 UTC; `d76cc134ee6d8bde9c65553d78a57310d615f9d8`; tracked tree clean at run time except the pre-existing user handoff file and the output artifact created by the run |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; MCP 2.0.0; Pydantic 2.13.4; KiCad was not invoked |
| Dataset | Independently authored `footprint-pose-courtyard.kicad_pcb`, SHA-256 `8bb7b22bf38db4d0272d5010cd9d9357123f3bf5d37329e4e01851266032cf48`; four native-identity front-side orthogonal footprints, two pads each, and unfilled `F.CrtYd` rectangles; no external or proprietary board; no train/test split |
| Configuration | `kicad-source-preserving-placement-v1`; ten measured repetitions; `request-200um-v1` constraints; `front-orthogonal-unfilled-courtyard-v1` projection; script SHA-256 `eeaf08e818d0a68d1c09399171f79d0803db6f86bd49f95176ff12e1ad414ddb`; CPU-only |
| Metrics | Deterministic projection replays 10/10; Board IR round-trip replays 10/10; source unchanged `true`; median projection latency 5,502,417 ns; candidate `sha256:4d4839eb58c6679384c78a94cbc640268fee994d7018de44ea46a54e6f2a3538`; candidate base `sha256:73463aa02b61a571f5b4c3b21844a56dd5c3ca0b457958c73b096b0e7f2ed982`; output `sha256:1a0dd7389e80027393b0ffb53ebdaa60a40a4a4d3f1469cc613dcd879eba1153`; source/output bytes 2,914/2,938; no KiCad, DRC, live mutation, or undo calls |
| Artifact | [`2026-08-04-placement-projection-d76cc13.json`](../../benchmarks/results/mcp/2026-08-04-placement-projection-d76cc13.json) |
| Interpretation | This is a deterministic source-preservation and Board IR round-trip oracle for the narrow supported placement subset. It shows that a disposable placement derivative can be produced without changing the source fixture, but it does not claim placement DRC, courtyard legality, back-side or non-orthogonal fidelity, live KiCad mutation, undo, electrical behavior, fabrication readiness, or performance generalization. |

### B-017 — Internal layered A* oracle differential

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:7a0e238729d37384d2ea9d11aad09061101f6067b2d5cf783589a802cdeb974b` |
| Date and commit | 2026-08-04 15:06:56 UTC; `81ca8031583393b37cb88fc207bec82b42c545fc`; tracked tree clean except the pre-existing user handoff file and this output artifact before commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Four fixed 5x5 two-layer abstract lattice cases: via-required, via-cost choice, direct single-layer, and both layers blocked; no Board IR/KiCad board or external design data |
| Configuration | `layered-astar-oracle-v1`; 50 replays per case; independent zero-heuristic Dijkstra differential; script SHA-256 `888d1c4fc86d1bdbda4aed851607d644f3afd8c3a06543f0c01086d362d56729`; fixture set `four-fixed-5x5-v1`; CPU-only |
| Metrics | Deterministic replays 200/200; differential cost/no-path matches 4/4; via-required success `true` with two transitions; direct single-layer case used zero transitions; blocked case remained `no_path` |
| Artifact | [`2026-08-04-layered-astar-oracle.json`](../../benchmarks/results/routing/2026-08-04-layered-astar-oracle.json) |
| Interpretation | This is algorithmic evidence for the internal maze-level `(x, y, layer)` search seam only. It does not claim Board IR mapping, trace width/clearance, via annulus/drill/keepout/net-class legality, source-preserving KiCad serialization, DRC, congestion/rip-up, whole-board completion, or FreeRouting parity. |

### B-018 — Board IR-bound layered proposal adapter

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:6691b5c860ae3d48eb6e9c6710c5308247de6e43011486098e976f1a4f13f79a` |
| Date and commit | 2026-08-04 15:39:10 UTC; `b19b2d020ac017d34f0911176fc88733895f3d4d`; tracked tree clean except the pre-existing user handoff file and this output artifact before its evidence commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored synthetic Board IR v0.2 fixture set `synthetic-two-layer-board-ir-v1`; exactly two signal layers, rectangular hole-free outline, two F.Cu pads, front-layer wall, both-layer block, full via keepout, stale revision, and off-grid endpoint; no external content or proprietary board |
| Configuration | `layered-board-ir-adapter-v1`; ten replays per case; six cases; `board-layered-a-star-v1`; script SHA-256 `0cebb5563952a4d4ba8510e0232c6b29d3c12e6fd8e52ff88eff673664d062f8`; CPU-only; no KiCad subprocess |
| Metrics | Deterministic replays 60/60; same-layer candidate 1 path/0 vias; via-required candidate 2 paths/2 vias/8,000 nm wire with 90 obstacle checks; blocked and via-keepout cases `no_path`; stale and off-grid refusals before search; source snapshot unchanged `true`; candidate digest tamper rejected `true` |
| Artifact | [`2026-08-04-layered-board-adapter.json`](../../benchmarks/results/routing/2026-08-04-layered-board-adapter.json) |
| Interpretation | This is evidence for a deterministic, Board IR-bound proposal seam and its fail-closed boundaries only. It does not claim source-preserving KiCad serialization, Board IR round-trip after a write, KiCad DRC/refill, whole-board completion, electrical behavior, fabrication readiness, performance generalization, negotiated congestion, rip-up/reroute, or FreeRouting parity. |

### B-019 — Disposable layered KiCad serializer

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c24ecef1fc5d14f4f29aee5b35c13aeff6c21203235c2545132795324e26bc02` |
| Date and commit | 2026-08-04 15:57:46 UTC; `b94a92d032be23efa63fc5d8cff2bd145203e394`; serializer changes were measured before this evidence commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; two front-side AUDIO pads, a foreign front-side blocker, and two signal layers; no external or proprietary board |
| Configuration | `layered-kicad-serializer-v1`; ten request-replayed renders; source/profile/snapshot and candidate digest gates; deterministic UUID-v5 segment/via identities; CPU-only |
| Metrics | Deterministic outputs 10/10; Board IR round trip `true`; source unchanged `true`; two paths and two through-vias; serialized bytes 1,868; output `sha256:c732d5abaccb2d4521005ef15122d1038ff02f96a752df24ff640b69970f842c`; stale request refusal `true`; KiCad invocation `false`; DRC `false` |
| Artifact | [`2026-08-05-layered-kicad-serializer.json`](../../benchmarks/results/routing/2026-08-05-layered-kicad-serializer.json) |
| Interpretation | This proves request-replayed disposable segment/via serialization and Board IR round-trip equality for one narrow fixture. It does not establish KiCad DRC, live editor compatibility, mutation/apply safety, electrical behavior, fabrication readiness, whole-board completion, or FreeRouting parity. |

### B-020 — Layered candidate-bound KiCad DRC

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:4f71b18b7d6928680888fdc59b88658e0c9e24f126eb16d7e83d287b32d064cd` |
| Date and commit | 2026-08-04 16:16:14 UTC; `b13d37d47b140847cf4dca31d6ebebfaa29c97ce`; measured after the layered DRC gate commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad CLI 10.0.5 at the standard macOS application path |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; two front-side AUDIO pads, one foreign front-side blocker, and two signal layers; no external or proprietary board |
| Configuration | `layered-kicad-drc-v1`; ten fresh private-workspace runs; original `LayeredRouteRequest` replay; fixed private KiCad DRC command; aggregate redacted evidence only; CPU-only |
| Metrics | Deterministic evidence 10/10; KiCad DRC pass 10/10; errors 0; warnings 0; unconnected items 0; ignored checks 5; source bytes/inode/mtime unchanged 10/10; workspace entries unchanged 10/10; patched board `sha256:c732d5abaccb2d4521005ef15122d1038ff02f96a752df24ff640b69970f842c`; patched context `sha256:a929034fa3e4ba7cfe9fcd2b5b0efd9c57b3d98566e691c24a74455229c7cc1c` |
| Artifact | [`2026-08-05-layered-kicad-drc.json`](../../benchmarks/results/routing/2026-08-05-layered-kicad-drc.json) |
| Interpretation | This is authoritative KiCad evidence for one replayed two-layer candidate and its captured context. It does not establish multilayer completion, filled-zone routing, negotiated congestion, whole-board completion, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-021 — Fresh fill-aware routing corridor

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:cfd4c82d888e0ea0de38aac4eeab89409c6498b8d85266b7d17f4276177a3b5f` |
| Date and commit | 2026-08-04 16:28:05 UTC; `d1bdfe07053e458fa7b9ee26b3659d94b78e8eef`; measured after the fill-aware routing commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently generated synthetic Board IR fixture `synthetic-fill-corridor-v1`: two AUDIO pads, one rectangular POWER zone, and a verified upper fill island leaving a lower corridor; no external or proprietary design data |
| Configuration | `fill-aware-routing-v1`; ten replays each for conservative and freshness-verified modes; 1,000 nm grid; exact polygon obstacles; source-revision and matching-zone gates; CPU-only |
| Metrics | Deterministic conservative 10/10; deterministic fill-aware 10/10; conservative wire length 14,000 nm; fill-aware wire length 8,000 nm; reduction 6,000 nm; matching zone required `true`; KiCad invocation `false`; DRC `false` |
| Artifact | [`2026-08-05-fill-aware-routing.json`](../../benchmarks/results/routing/2026-08-05-fill-aware-routing.json) |
| Interpretation | This measures a meaningful route-quality improvement in the internal bounded routing core. It does not establish fresh-fill behavior on a real KiCad board, whole-board completion, electrical correctness, fabrication readiness, or FreeRouting parity; the public routed-candidate provenance contract remains open. |

### B-022 — Public routed fill-provenance contract

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:cb227469da7c33fcc601553eea5886699f5d498d655a00b26efb527b10c15c96` |
| Date and commit | 2026-08-04 16:39:37 UTC; `ee60b62f1bf54e0e7eee128dc92cfe1eb34791c3`; measured before the provenance contract commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `blocked-zone.kicad_pcb`; two AUDIO pads, one POWER zone, and a synthetic freshness-bound upper fill island; no proprietary board data |
| Configuration | `fill-preview-provenance-v1`; ten repeated public-service calls; closed `RoutePreviewToolResponse` validation; private temporary workspace; foreign-zone evidence injected only at the tested authority seam |
| Metrics | Routed outcomes 10/10; foreign-zone provenance outcomes 10/10; schema-valid outputs 10/10; deterministic candidate IDs `true`; workspace unchanged `true`; KiCad invocation `false`; DRC `false` |
| Artifact | [`2026-08-05-fill-preview-provenance.json`](../../benchmarks/results/routing/2026-08-05-fill-preview-provenance.json) |
| Interpretation | This proves that an MCP caller can distinguish a routed candidate shaped by freshness-bound foreign fill without receiving raw island geometry or mutation authority. It is contract evidence, not KiCad refill, DRC, whole-board, electrical, fabrication, performance, or FreeRouting evidence. |

### B-023 — Fill-aware routing safety remediation

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:90b840eed06667b3928d4264cbb4891f95d7993ad5745c8ae550c2ce860764da` |
| Date and commit | 2026-08-04 16:58:55 UTC; `12be421304b8b512552a2a8173924ab4a40fcc67`; measured after the remediation code commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently generated synthetic Board IR fixture `synthetic-fill-corridor-v1`, with a matching foreign zone/fill case and an orphaned-fill negative case; no external or proprietary design data |
| Configuration | `fill-aware-routing-v1` remediation replay; ten deterministic conservative and freshness-aware replays; explicit orphaned-fill diagnostic gate; CPU-only |
| Metrics | Conservative 10/10 deterministic; fill-aware 10/10 deterministic; wire length 14,000 nm → 8,000 nm (6,000 nm reduction); orphaned fill refused with `unsupported_geometry`; `matching_zone_required=true`; KiCad invocation `false`; DRC `false` |
| Artifact | [`2026-08-05-fill-aware-routing-remediation.json`](../../benchmarks/results/routing/2026-08-05-fill-aware-routing-remediation.json) |
| Interpretation | This substantiates both the route-quality replay and the matching-zone safety gate after review remediation. It remains synthetic core evidence and does not claim real-board refill, KiCad DRC, whole-board completion, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-024 — Public layered route-preview contract

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:5e6b2d56a38ead46c165a0fd42ea89635042b19c73aa4e6170ee1d9fd98dfa99` |
| Date and commit | 2026-08-04 17:17:48 UTC; `a739c1d93c65aa05a95e4c946d83ba1b265355c5`; measured after the public MCP slice commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; two same-net pads, a front-layer blocker, and two signal layers; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/layered-route-preview/v1`; ten calls through the public layered service and `LayeredRoutePreviewToolResponse`; both source/snapshot CAS values required; CPU-only |
| Metrics | Schema-valid replays 10/10; deterministic candidate ID `sha256:7ec5e572a93a197843928a65173f768b8999ed3d2768d692f517706b13d90a7d`; two full-stack vias; stale-board and stale-snapshot refusals `true`; source bytes/inode/mtime unchanged; KiCad/DRC/apply authority `false` |
| Artifact | [`2026-08-05-layered-route-preview.json`](../../benchmarks/results/routing/2026-08-05-layered-route-preview.json) |
| Interpretation | This is evidence that the read-only MCP contract can produce and validate a deterministic via-capable proposal while refusing stale references and preserving the source. It does not claim public KiCad DRC, serializer/export, durable jobs, multilayer completion, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-025 — Durable routing-job ledger

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c6a01655da9938cb085679784bf6fa0f934df0095609978ff71b92c1c9a1c001` |
| Date and commit | 2026-08-04 17:46:09 UTC; `519077cc158b773aaaed6b88319eaa941bf237cd`; measured after the pushed ledger implementation |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; only its source digest was used in normalized job specifications |
| Configuration | `copper-mcp/benchmark/routing-job-ledger/v1`; 100 bounded SQLite records; deterministic create/start/cancel/get transitions; reopen and expiry probes; CPU-only |
| Metrics | Records rehydrated `true`; idempotent create `true`; revision-CAS refusal `true`; expiry refusal `true`; redacted storage `true`; source unchanged `true`; create latency p50/p95 `499.292/743.042 µs`; transition latency p50/p95 `180.75/1,651.958 µs`; worker execution `false`; candidate geometry persistence `false`; MCP Tasks `false` |
| Artifact | [`2026-08-05-routing-job-ledger.json`](../../benchmarks/results/routing/2026-08-05-routing-job-ledger.json) |
| Interpretation | This measures a restart-safe, bounded lifecycle ledger for revision-bound job metadata. It does not claim background routing, candidate persistence/export, MCP Tasks compatibility, KiCad DRC, electrical behavior, fabrication readiness, or FreeRouting parity. |
