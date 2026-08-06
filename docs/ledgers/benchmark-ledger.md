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

### B-086 — Layered fill-aware zone obstacles

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:aa63918b6d855b7c61d4abc6c4d2a13b0ff03814246e568bd9fd9e0db529acf5` |
| Date and commit | 2026-08-05 22:02:15 UTC; measured on `feat/fill-aware-routing-obstacles` with the ADR-0070 adapter change applied on top of `9746bdbc04f7998dbaec849436c13e05d0edc14a`, before the implementing commit existed |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently generated synthetic Board IR fixture `synthetic-layered-fill-corridor-v1`: two AUDIO pads, one rectangular POWER zone on the front signal layer, a full-height back-layer track keepout that forecloses a via escape, and a verified upper fill island leaving a lower corridor; no external or proprietary design data |
| Configuration | `layered-fill-obstacles-v1`; ten deterministic replays each for conservative and freshness-verified modes; 1,000 nm grid; bounded two-layer ordered-stack adapter; rectangular island envelopes; source-revision, matching-zone, and outline-containment gates; CPU-only |
| Metrics | Deterministic conservative 10/10; deterministic fill-aware 10/10; conservative wire length 14,000 nm with 0 vias; fill-aware wire length 8,000 nm with 0 vias; reduction 6,000 nm; nested-island lengths 8,000 → 10,000 → 12,000 → 14,000 nm with `growth_monotonic=true`; stale evidence refused `stale_revision`; orphaned island refused `unsupported_geometry`; island escaping its outline refused `unsupported_geometry`; KiCad invocation `false`; DRC `false` |
| Artifact | [`2026-08-06-layered-fill-obstacles.json`](../../benchmarks/results/routing/2026-08-06-layered-fill-obstacles.json) |
| Interpretation | This measures a route-quality improvement in the internal bounded ordered-layer proposal seam, and records the metamorphic monotonicity property and all three fail-closed gates as real invocations rather than metadata claims. The outline-sized island reproducing the conservative length is the bound on how much the replacement can ever open. It does not establish zone-refill behavior beyond ADR-0021, exact polygon layered collision, same-net poured attachment, a public layered fill-authority contract, whole-board completion, electrical correctness, DFM, fabrication readiness, or FreeRouting parity. |

### B-087 — Declared negotiation policy-slot sweep

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:43d3409d2f9ee0330c55649386804cb3c90aa7c3704834a2fee8e0e0cab1c205` |
| Date and commit | 2026-08-06 03:00 UTC; measured on `feat/negotiated-congestion` at implementation commit `b5227c977c6d447f4d90d3b728b793f5c07616e2`, which is the artifact's `evidence_harness_commit` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Three CopperMCP-original synthetic Board IR fixtures, no external or proprietary design data: `crossing-neutral-control` reproduces the two-net ADR-0055/B-036 crossing topology; `congested-channel-negotiating` and `congested-channel-first-pass` are the same six nets — one 10-cell horizontal net crossed by three verticals plus two nets far enough apart never to conflict — under a penalty that respectively does and does not force multi-iteration negotiation |
| Configuration | `copper-mcp/benchmark/negotiated-plan-slots/v1`; eleven declared plans (no-plan baseline, the legacy-equivalent plan, four net-order rules, three cost-update rules, two rip-up rules, one two-slot composition) × three fixtures; ten deterministic replays each; 1,000,000 nm grid; 8-iteration ceiling; single signal layer; CPU-only; script `sha256:14b31f8585cda65ddf45c8821966fa310ef92a5348927ddf1001175c34459e90` |
| Determinism | 330 replays, zero divergence. Every plan's ten replays produced byte-identical candidate digests, geometry digest, iteration counts, router-call counts, and plan/slot digests. |
| Metrics — neutral control | All eleven plans identical: completed, 1 iteration, 2 router calls, 26,000,000 nm, 0 overflow units, 0 vias. The slots are inert where the first pass already resolves the board. |
| Metrics — congested, negotiating | Baseline (no plan): completed, 5 iterations, 30 router calls, 56,000,000 nm. `net-order/demand-ascending`: completed, **1** iteration, **6** router calls, **54,000,000 nm** — 80% fewer router calls and 3.6% less copper. `cost-update/present-growth-13-10`: completed, 3 iterations, 18 router calls, 60,000,000 nm — 40% fewer calls but 7% more copper. `cost-update/scaled-accumulation-4`: completed, 5 iterations, 30 calls, 70,000,000 nm — 25% more copper for no saving. **Negative results:** `rip-up/conflicted-only`, `rip-up/top-conflict-2`, `cost-update/saturating-decay-half`, `net-order/stable-identifier`, `net-order/demand-descending`, and `composed/conflicted-only+present-growth` each reached the 8-iteration ceiling with `no_path` where the default converged in five. |
| Metrics — congested, first pass | All plans completed in 1 iteration with 6 router calls at 76,000,000 nm, except `net-order/demand-ascending` at 54,000,000 nm. |
| Rip-up accounting | The baseline reports `0` rip-ups across five iterations while `legacy-equivalent` reports `16` for the same routing. This is not a behavior difference: the legacy counter adds `len(best_candidates)`, which stays empty while every intermediate pass is refused by the clearance gate. The plan-mode counter counts the nets actually selected for re-routing. The legacy number is preserved because changing it would move published bytes. |
| Artifact | [`2026-08-06-negotiated-plan-slots.json`](../../benchmarks/results/routing/2026-08-06-negotiated-plan-slots.json); validated against its own self-digest by `tests/test_benchmark_negotiated_plan_slots.py`, which also re-runs the harness and requires the fresh cases to equal the committed ones |
| Mutation sensitivity | Five load-bearing guards mutated one at a time. Caught: the rip-up rule always retrying a net with nothing retained; the plan digest composing from its own three slot digests; candidate identity binding the plan composite rather than the envelope; the no-inert-parameter rule. **Survived on first run:** an unattributed clearance refusal retaining nothing, because no test produced an unattributed refusal. A test was added and the mutant is now caught. |
| Claim | **None.** The artifact classifies itself `exploratory sweep / no quality claim`. The sweep was not predeclared, the fixtures are small and synthetic, and there is no held-out corpus. Qu et al. measured 1.95% relative deviation in rule violations but 0.008% in wirelength across 300 random net orders on a real benchmark, so the 29% wirelength swing seen here from ordering alone is evidence about this fixture, not about routing. |
| Interpretation | This measures that the three declared slots are separable, deterministic, digest-bound, and behaviorally real — including where they are worse than the default. It does not establish that any slot combination should become the default, and it makes no KiCad DRC, electrical, multilayer, via, fabrication, apply, whole-board, scaling, or FreeRouting-parity claim. Via counts are structurally zero because the negotiated coordinator is single-layer by contract. |

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

### B-026 — Live layered route-preview contract

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:722d55fc1df22a711219dfa7c12cc07103575fe0dda192809f9e76e102489a40` |
| Date and commit | 2026-08-04 18:10:26 UTC; `ceba0c907d127f2aadec69145eb55a0da876fa82`; measured after the live implementation commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad IPC server was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; two selected-net pads, a front-layer blocker, and two signal layers; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/live-layered-route-preview/v1`; ten fake official-client replays; source, Board IR, and hashed session CAS; remaining route deadline passed to IPC; CPU-only |
| Metrics | Schema-valid replays 10/10; deterministic candidate ID `sha256:bbd149eb890ffd527def0c65f2bcb4269aca8423938cfaaa24743f3fe959a587`; candidate equals file-backed oracle `true`; two full-stack vias; stale-board refusal `true`; capture-race refusal `true`; IPC clients closed `true`; source unchanged `true`; KiCad/DRC/serialization/apply/real-GUI `false` |
| Artifact | [`2026-08-05-live-layered-route-preview.json`](../../benchmarks/results/routing/2026-08-05-live-layered-route-preview.json) |
| Interpretation | This proves deterministic observe-to-via-capable-proposal closure over a fake official IPC client, including session/source/snapshot CAS, deadline and lifecycle safety. It does not establish a running KiCad GUI session, endpoint-via legality, DRC, serializer/export, persistence, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-027 — Layered candidate topology verifier

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:99f494b856c93b641bbb54142039fda146e36fd2aa54e992caf9b2f947f59b8b` |
| Date and commit | 2026-08-04 18:33:18 UTC; `939c9eb82d70299e0a48128d5980354716a487e3`; measured after the topology-verifier commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; two selected-net pads, a front-layer blocker, and two signal layers; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/layered-candidate-verifier/v1`; ten bounded structural verification replays; candidate digest, Board IR revision, endpoint, topology, duplicate/crossing, and conservative endpoint-via checks; CPU-only |
| Metrics | Verified replays 10/10; deterministic candidate IDs `true`; 3 paths and 2 vias; disconnected geometry refused `true`; endpoint-via refused `true`; stale revision refused `true`; `physical_validation=not_modelled`; KiCad/DRC/serialization/apply `false` |
| Artifact | [`2026-08-05-layered-candidate-verifier.json`](../../benchmarks/results/routing/2026-08-05-layered-candidate-verifier.json) |
| Interpretation | This demonstrates a bounded structural gate before disposable layered serialization, including CAS/topology refusal and explicit physical-validation non-claim. It does not establish KiCad DRC, padstack/fabrication legality, whole-board completion, electrical behavior, live GUI compatibility, or FreeRouting parity. |

### B-028 — Protocol-independent routing worker leases

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:4fa09cb8548ecde12ea7fd3958a372df4c5b5213469e55fb0af5b23170f7fd34` |
| Date and commit | 2026-08-04 18:49:20 UTC; `4ab2c1a1a6db4ce0ced6be7aa07bb3f71b59650f`; measured after the worker/lease commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/blocked-pad.kicad_pcb`; its Board IR candidate was used only as an in-memory executor result; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/routing-job-worker/v1`; separate bounded SQLite stores for success, race, cancellation, recovery, and invalid-output probes; injected clocks; CPU-only |
| Metrics | Successful completion `true`; deterministic candidate ID `true`; claim race one winner `true`; cancellation terminal `true`; expired lease recovered `true`; invalid candidate terminal `true`; redacted storage `true`; candidate persistence `false`; MCP Tasks `false` |
| Artifact | [`2026-08-05-routing-job-worker.json`](../../benchmarks/results/routing/2026-08-05-routing-job-worker.json) |
| Interpretation | This demonstrates local CAS-backed single-worker execution, cooperative cancellation, stale-lease terminalization, and safe invalid-output handling while preserving the redacted-store boundary. It does not establish request/result persistence, durable candidate export, authorization, MCP Tasks compatibility, KiCad DRC, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-029 — Redacted candidate-manifest persistence

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:80e37afd84c7c8ded159dba51c13a11fc75e3ebb51bcd5a707f9e1a928f49359` |
| Date and commit | 2026-08-04 19:01:26 UTC; `2c1a7fd14e1cf5f50ffc25ab40ab5219947d1ad9`; measured after the manifest-store commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Synthetic redacted-manifest fixture `synthetic-redacted-manifest-v1`; no board bytes, net names, geometry, prompts, credentials, or DRC findings |
| Configuration | `copper-mcp/benchmark/routing-candidate-manifest-store/v1`; temporary SQLite stores; bounded TTL; deterministic manifest digest; tamper and uniform lookup probes; CPU-only |
| Metrics | Restart preservation `true`; idempotent put `true`; expiry refusal `true`; unknown/expired error uniform `true`; tamper refusal `true`; redacted payload `true`; geometry export `false`; MCP Tasks `false` |
| Artifact | [`2026-08-05-routing-candidate-manifest-store.json`](../../benchmarks/results/routing/2026-08-05-routing-candidate-manifest-store.json) |
| Interpretation | This demonstrates restart-safe redacted candidate-summary persistence and fail-closed integrity/retention boundaries. It does not establish route-geometry rehydration/export, ordinary MCP job tools, authorization, MCP Tasks compatibility, KiCad DRC, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-030 — Durable layered routing request/result and explicit geometry export

| Field | Recorded evidence |
|---|---|
| Run ID | Recorded in the self-addressed artifact below |
| Date and commit | 2026-08-05; source commit recorded in the artifact; benchmark run after the implementation commit |
| Environment | Apple arm64 CPU; Python 3.12; KiCad was not invoked; temporary local SQLite repository |
| Dataset | Independently authored `tests/fixtures/route-candidate/two-pad.kicad_pcb`; one two-pad audio-style net; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/routing-job-request-result-export/v1`; file-backed `start_routing_job` service, single local worker, five lookup/export timing samples, 60-second TTL, no MCP Tasks |
| Metrics | The artifact records queued-before-worker, deterministic job-ID idempotency, completed candidate, explicit geometry export, wrong-context refusal, restart recovery of the terminal job and normalized request, deep/redacted persistence, live-request refusal, median lookup/export latency, board mutation `false`, and MCP Tasks `false`. |
| Artifact | [`2026-08-05-routing-job-request-result-export.json`](../../benchmarks/results/routing/2026-08-05-routing-job-request-result-export.json) |
| Interpretation | This is contract and restart evidence for the narrow file-backed two-signal queue. It demonstrates a measurable durable handoff and a separately authorized geometry disclosure, not general routing quality, throughput, KiCad DRC, live GUI compatibility, MCP Tasks interoperability, electrical behavior, fabrication readiness, or FreeRouting parity. |

### B-031 — Bounded one-Steiner topology ordering

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:1c4eb4567825483a82ef14b1fc6495f56bd706d4e466e22917fc3c7c3faf8b6f` |
| Date and commit | 2026-08-05; source commit `6d9e632` recorded before this implementation commit; dirty tree during local benchmark generation |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/tree-star.kicad_pcb`; four-pad multi-pin synthetic fixture; no external or proprietary design data |
| Configuration | `batched-1-steiner-v1`; ten deterministic public `preview_route` replays against the same bounded A* and budgets; baseline replay swaps only the topology-ordering seam to `component-mst-v1` |
| Metrics | Baseline wire length 48,000,000 nm; one-Steiner ordering 42,000,000 nm; reduction 6,000,000 nm (12.5%); deterministic heuristic candidate `true`; deterministic baseline candidate `true`; source bytes/inode/mtime unchanged `true`; KiCad invocation `false`; optimality claim `false`; FreeRouting parity claim `false` |
| Artifact | [`2026-08-05-steiner-ordering.json`](../../benchmarks/results/routing/2026-08-05-steiner-ordering.json) |
| Interpretation | This is a narrow topology-ordering improvement on one synthetic four-pad fixture, measured through the real route-preview service and the same obstacle-aware A* budgets. It is not a FLUTE implementation, a Steiner-optimality certificate, a whole-board result, KiCad DRC evidence, electrical/fabrication evidence, or a FreeRouting comparison. |

### B-032 — Public layered candidate DRC evidence contract

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c13d66d2ece04f0a8d4605a2b7ef7a8cab56be0fce0a656f4cf2833c8c14de6d` |
| Date and commit | 2026-08-05; source commit `19f8a81cb5968dcea2fab63a0799afe213488384` recorded before this slice; benchmark run used a dirty tree with the current implementation |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; fake authority only; KiCad was not invoked |
| Dataset | Independently authored `tests/fixtures/route-candidate/two-pad.kicad_pcb`; one two-pad fixture; no external or proprietary board data |
| Configuration | `copper-mcp/benchmark/layered-drc-preview/v1`; public `preview_layered_route` service; one omitted-flag replay and one explicit `include_drc=true` replay; aggregate redacted fake evidence; no geometry or raw report disclosure |
| Metrics | Omitted DRC calls `0`; requested DRC calls `1`; candidate/evidence binding `true`; source unchanged `true`; workspace mutations `0`; KiCad invoked `false`; whole-board DRC claim `false`; FreeRouting parity claim `false` |
| Artifact | [`2026-08-05-layered-drc-preview.json`](../../benchmarks/results/routing/2026-08-05-layered-drc-preview.json) |
| Interpretation | This measures the public opt-in/schema and provenance boundary, not KiCad DRC quality or timing. The real blocked-pad smoke uses KiCad 10.0.5 and records zero errors, warnings, and unconnected items with the same candidate/source/context binding; neither result establishes whole-board, fabrication, electrical, refill, general multilayer, or FreeRouting authority. |

### B-033 — Conservative obstacle spatial-index differential

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:0375d0e76d5aedbd0dd7dbed082d07f07fc8793ea38cb87ee92faade91993631` |
| Date and commit | 2026-08-05; source commit `62f10efcd82a8a6e0974e15d69e56573d4115c6e` recorded after the spatial-index implementation commit |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Independently authored deterministic uniform-grid fixture: 512 conservative rectangle entries and 256 closed AABB queries; no board bytes, private designs, or external corpus |
| Configuration | `copper-mcp/benchmark/routing-conservative-spatial-index-v1`; ten repetitions; exact closed-bound legacy relation scan versus immutable uniform-grid query; 256 deterministic exact-query replays |
| Metrics | Legacy relation checks `131,072`; indexed relation checks `31`; reduction `99.9763%`; exact query matches `256/256`; indexed buckets `136`; median microbenchmark speedup `346.471x` on this host. Differential A*/Dijkstra route fixture: same geometry/cost/expanded states; A* exact checks `19,982 → 308`; low ceilings remain fail-closed while indexed work may complete under a ceiling the legacy scan would exhaust. |
| Artifact | [`2026-08-05-spatial-index.json`](../../benchmarks/results/routing/2026-08-05-spatial-index.json) |
| Interpretation | This is a conservative candidate-filter and resource-use result, not a whole-board scaling claim. It does not establish congestion/rip-up, FreeRouting parity, KiCad DRC, electrical behavior, fabrication readiness, or cross-host wall-clock performance. |

### B-034 — Deterministic unsigned in-toto candidate DRC Statement

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c70de01be5a259936c5c784c3e5b17141bdff8ab244c4006f09f85519a5ec933` |
| Date and commit | 2026-08-05; source commit `7facad8` recorded before this attestation slice; local benchmark artifact generated on a dirty tree |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Fixed content-addressed synthetic revisions and aggregate `DrcSummary`; no board bytes, geometry, net names, UUIDs, or external/private corpus |
| Configuration | `copper-mcp/benchmark/drc-statement/v1`; 128 deterministic builds; in-toto Statement v1 with Link v0.3 predicate; closed Pydantic contract validation; canonical compact UTF-8 serialization |
| Metrics | Schema-valid `128/128`; deterministic bytes `true`; subject candidate binding `true`; material revision binding `true`; redacted payload `true`; Statement size `1,412` bytes; median build+serialize `19,875 ns` on this host; signature count `0`; DSSE envelope `false`; KiCad invoked `false` |
| Artifact | [`2026-08-05-drc-statement.json`](../../benchmarks/results/routing/2026-08-05-drc-statement.json) |
| Interpretation | This measures payload shape, binding, redaction, and local serialization only. It does not establish DSSE authentication, verifier coverage, provenance, whole-board DRC quality, electrical/fabrication readiness, remote transport, persistence, or FreeRouting parity. |

### B-035 — Private placement-candidate KiCad DRC replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:3e1b4fa7abca5c934b5a86201bc121cd427ee6cbdc8b5fa8590890f93776410b` |
| Date and commit | 2026-08-05; source commit `7facad8` recorded before the placement DRC implementation; benchmark run after implementation on the local worktree |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad CLI 10.x |
| Dataset | Independently authored `tests/fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb`; two pad-owning front-side orthogonal footprints with unfilled rectangular courtyards |
| Configuration | `copper-mcp/benchmark/placement-candidate-drc/v1`; three private disposable KiCad DRC replays; fixed JSON report command with no refill/save; source/context CAS and aggregate-only evidence |
| Metrics | Clean DRC `3/3`; candidate binding `true`; context binding `true`; source bytes/inode/mtime preserved `true`; workspace mutations `0`; median private DRC `846,023,417 ns` on this host |
| Artifact | [`2026-08-05-placement-drc.json`](../../benchmarks/results/routing/2026-08-05-placement-drc.json) |
| Interpretation | This establishes the narrow private placement evidence gate and source-preserving replay on one fixture. It does not establish back-side/non-rectangular coverage, public/live placement DRC, apply/undo, electrical/fabrication readiness, or FreeRouting parity. |

#### B-034 replay — current schema-bound payload evidence

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:080405e99f54c88a643abd4d20219a8c415222270604aed66c1060a509e1543e` |
| Date and commit | 2026-08-05; source commit `6b5c75e454db1a437bc947096bc61e0d4b7b9398`; tracked tree clean except the pre-existing user handoff file |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Same fixed content-addressed synthetic revisions and aggregate `DrcSummary` as B-034; no board bytes, geometry, net names, UUIDs, or external/private corpus |
| Configuration | Same `copper-mcp/benchmark/drc-statement/v1`; 128 deterministic builds after the required nested digest contract fix; closed Pydantic validation; canonical compact UTF-8 serialization |
| Metrics | Schema-valid `128/128`; deterministic bytes `true`; subject candidate binding `true`; material revision binding `true`; redacted payload `true`; Statement size `1,412` bytes; median build+serialize `19,666.5 ns`; signature count `0`; DSSE envelope `false`; KiCad invoked `false` |
| Artifact | [`2026-08-05-drc-statement.json`](../../benchmarks/results/routing/2026-08-05-drc-statement.json) |
| Interpretation | Current-contract replay superseding B-034's dirty-tree/pre-implementation provenance note. The nested digest object now requires exactly one `sha256` field. This remains payload-shape evidence only; it does not establish DSSE authentication, verifier coverage, provenance, whole-board DRC quality, electrical/fabrication readiness, remote transport, persistence, or FreeRouting parity. |

#### B-035 replay — current implementation provenance

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:7e72b2586d1d2208ce0972bd7b8077ac9c30901beec814ec2c633b62c115fa1a` |
| Date and commit | 2026-08-05; source commit `6b5c75e454db1a437bc947096bc61e0d4b7b9398`; tracked tree clean except the pre-existing user handoff file |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad CLI 10.x |
| Dataset | Same independently authored `tests/fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb`; two pad-owning front-side orthogonal footprints with unfilled rectangular courtyards |
| Configuration | Same `copper-mcp/benchmark/placement-candidate-drc/v1`; three private disposable KiCad DRC replays from the implementation commit; fixed JSON report command with no refill/save; source/context CAS and aggregate-only evidence |
| Metrics | Clean DRC `3/3`; candidate binding `true`; context binding `true`; source bytes/inode/mtime preserved `true`; workspace mutations `0`; median private DRC `3,088,695,417 ns` on this host |
| Artifact | [`2026-08-05-placement-drc.json`](../../benchmarks/results/routing/2026-08-05-placement-drc.json) |
| Interpretation | Current-contract replay superseding B-035's pre-implementation source commit. It establishes the narrow private placement evidence gate and source-preserving replay on one fixture. It does not establish back-side/non-rectangular coverage, public/live placement DRC, apply/undo, electrical/fabrication readiness, or FreeRouting parity. |

### Review-remediation replays — 2026-08-05

The following append-only rows supersede only the provenance or metric-coverage limitations called
out in the historical B-022, B-026, B-027, B-031, B-032, and B-033 rows. Earlier rows remain
historical evidence; these runs were generated from the clean implementation commit
`d69f3b0b9e563ef5d4d6c1e99a6ef47508fdf51b`.

#### B-022 — complete workspace-preservation replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:267bd95964d7248992fa6fb0afb3ac1071db96e5d4d9695654b8106c6050ffb4` |
| Artifact | [`2026-08-05-fill-preview-provenance-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-fill-preview-provenance-review-remediation.json) |
| Evidence | Ten deterministic routed previews; ten foreign-zone provenance outcomes; schema-valid `10/10`; complete temporary-workspace entry snapshot unchanged; board bytes unchanged; KiCad/DRC not invoked. |
| Limits | Synthetic fill-authority fixture only; no KiCad refill, electrical, fabrication, or FreeRouting claim. |

#### B-026 — live layered CAS and client-closure replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:3bc9d8ac9b50f0df2d23b6711aa60c81bea3359358a380c7807349e8410fbded` |
| Artifact | [`2026-08-05-live-layered-route-preview-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-live-layered-route-preview-review-remediation.json) |
| Evidence | Ten schema-valid deterministic replays; stale board, stale Board IR snapshot, and stale KiCad-session CAS refusals; capture-race refusal; `ipc_clients_closed=true` for success and refusal paths; file-oracle candidate equality; source unchanged. |
| Limits | Fake official-client harness, not a real GUI session; the cooperative IPC deadline cannot pre-empt one blocking official call. |

#### B-027 — layered topology verifier replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:bce0deb65e8e9f3e02666f0da99c041c38f8b2689d5561f9b5746a2470c6a799` |
| Artifact | [`2026-08-05-layered-candidate-verifier-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-layered-candidate-verifier-review-remediation.json) |
| Evidence | Ten verified replays with deterministic candidate IDs; disconnected, duplicate, crossing, stale-revision, and endpoint-via refusal metrics all true; three paths and two vias; physical validation explicitly `not_modelled`. |
| Limits | Structural verifier fixture only; no exact padstack, refill, DRC, fabrication, or FreeRouting claim. |

#### B-031 — deterministic multi-pin ordering replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:0ee063da419e2986334a5029f8eea173972eecbf0e394a9c1eef5e2f181c9053` |
| Artifact | [`2026-08-05-steiner-ordering-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-steiner-ordering-review-remediation.json) |
| Evidence | Ten independent Steiner and ten baseline replays; both candidate streams deterministic; wire length `48,000,000 → 42,000,000 nm` (`12.5%`) on the fixed four-pad fixture; source unchanged. |
| Limits | One bounded one-Steiner heuristic fixture; no optimality, congestion, DRC, electrical, fabrication, or FreeRouting claim. |

#### B-032 — exact layered DRC provenance replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:17f4126edc3b8cd18d5d6bb79e3a5d4bb78736350889d801dbb14f197864f664` |
| Artifact | [`2026-08-05-layered-drc-preview-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-layered-drc-preview-review-remediation.json) |
| Evidence | Candidate/evidence binding is derived from candidate, source, base, patched-board, and DRC-context revisions; omitted DRC calls `0`; requested calls `1`; source unchanged; workspace mutations `0`; KiCad not invoked; no whole-board claim. |
| Limits | Fake authority and narrow two-pad contract evidence only; no KiCad DRC quality, fabrication, electrical, or FreeRouting claim. |

#### B-033 — exact spatial-index identity replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:40aeea7c3befa294be0252108c2523bb42598c9c4dc319db5acb6f4d28796259` |
| Artifact | [`2026-08-05-spatial-index-review-remediation.json`](../../benchmarks/results/routing/2026-08-05-spatial-index-review-remediation.json) |
| Evidence | Exact ordered query identities match `256/256`, not merely hit counts; legacy relation checks `131,072` versus indexed `31` (`99.9763%` reduction); median fixture query speedup `14.134x`; source ordinals are the comparison authority. |
| Correction | The historical B-033 row's route-differential metrics are not reproduced by this artifact and are withdrawn as unsupported by the recorded script. This row supports only exact conservative query equivalence and fixture-bounded relation reduction. |
| Limits | Synthetic uniform-grid fixture only; no whole-board scaling, congestion, DRC, fabrication, electrical, or FreeRouting claim. |

#### B-036 — deterministic negotiated-congestion KiCad-fixture replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:ee3320444a09e1a1e80023220eec96b75897d8518847b98390434119e6d9d6da` |
| Date and commit | 2026-08-05; source commit `a68968d96a55b6f191c19bf668a356edfe13c8e1`; tracked tree clean except the pre-existing untracked `docs/HANDOFF-CODEX.md` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad GUI/CLI not invoked; Board IR adapter exercised |
| Dataset | [`negotiated-crossing-v1.kicad_pcb`](../../audio/fixtures/negotiated-crossing-v1.kicad_pcb), fixture SHA-256 `dbbfc5179cca7f644b90303ff3bc695f191ba94f7e5bbc8b4b1437d810ec83c7`; two orthogonal two-pin nets on a common 1 mm lattice |
| Configuration | `negotiated-congestion-kicad-crossing-v1`; present penalty `20,000,000 nm`; history penalty `5,000,000 nm`; eight-iteration ceiling; total expansion/obstacle-check ceilings `2,000,000` / `10,000,000`; three deterministic replays |
| Metrics | Sequential baseline overflow `1` lattice unit and wire length `16,000,000 nm`; negotiated status `completed`, overflow `0`, wire length `26,000,000 nm`, iterations `1`, rip-ups `0`; candidate IDs and serialized outcomes identical across `3/3` replays |
| Artifact | [`2026-08-05-negotiated-congestion.json`](../../benchmarks/results/routing/2026-08-05-negotiated-congestion.json) |
| Interpretation | This is a measurable first negotiated-congestion slice: it removes the shared lattice resource on the committed KiCad fixture at a known wire-length tradeoff. The metric is structural occupancy only; it does not establish exact pairwise clearance, multilayer capacity, KiCad DRC, electrical/fabrication correctness, general-board scaling, or FreeRouting parity. |

#### B-037 — corrected spatial-index predicate-work replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:99b35ca74757968063a496cd2bd88d9be03639b84bad536eebb7bd7d8277f111` |
| Date and commit | 2026-08-05; source commit `9b829d5ba8fbfef3b868b3f334be7a9bb5c17653` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked |
| Dataset | Deterministic uniform-grid fixture with 512 conservative rectangle entries and 256 closed AABB queries; no board bytes, private designs, or external corpus |
| Configuration | `copper-mcp/benchmark/routing-conservative-spatial-index-v1`; ten repetitions; indexed candidate count is measured before the exact `_bounds_intersect` predicate; ordered source ordinals remain the correctness authority |
| Metrics | Linear candidate checks `131,072`; indexed candidates examined `636`; predicate-work reduction `99.5148%`; exact query matches `256/256`; indexed buckets `136`; median fixture speedup `13.037x` on this host |
| Artifact | [`2026-08-05-spatial-index.json`](../../benchmarks/results/routing/2026-08-05-spatial-index.json) |
| Interpretation | This supersedes the historical `31` indexed-hit metric as current performance evidence. It measures candidate filtering work on one synthetic fixture only; it does not establish whole-board scaling, congestion/rip-up, DRC, electrical/fabrication readiness, or FreeRouting parity. |

#### B-038 — layered public DRC evidence hardening

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c89e42b4a5a11861bb43f784ed18ca66251d994054a66319901ee989863da5ab` |
| Date and commit | 2026-08-05; source commit `0c5b2a97c59a52e43f0ed48ca384267b6cc1f6d7` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad not invoked by the benchmark |
| Dataset | `tests/fixtures/route-candidate/two-pad.kicad_pcb`, fixture digest `sha256:5f88ebcf52cf8f1548990bdbdc1c52ac7a30f39c013366f79b161ec15e1caae2` |
| Configuration | `copper-mcp/benchmark/layered-drc-preview/v1`; omitted-flag replay, clean fake authority, warning-only fake authority, and malformed authority through public `preview_layered_route` |
| Metrics | Omitted DRC calls `0`; requested calls `1`; candidate/source CAS binding `true`; clean signal `true`; warning-only `clean=false` while hard-gate `passed=true`; malformed authority refused `true`; source unchanged `true`; workspace mutations `0` |
| Artifact | [`2026-08-05-layered-drc-hardening.json`](../../benchmarks/results/routing/2026-08-05-layered-drc-hardening.json) |
| Interpretation | This closes a presentation-boundary ambiguity: warning/exclusion findings remain machine-visible but cannot be represented as clean, and malformed or unbound authority cannot cross the MCP response boundary. It is not KiCad quality, whole-board DRC, fabrication, electrical, or FreeRouting evidence. |

#### B-039 — bounded KiCad schematic component/connectivity parity

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:15acd84f6920e3464e85338ca6eec9f4eded230e5a2bf57e377fb3d248c39d98` |
| Date and commit | 2026-08-05; source commit `e7fefc9` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad 10.0.5 export used by the integration fixture |
| Dataset | [`rc-low-pass-v1.kicadxml`](../../tests/fixtures/kicad-schematic/rc-low-pass-v1.kicadxml), replayed against the canonical CopperMCP schematic fixture |
| Configuration | `copper-mcp/benchmark/kicad-schematic-parity/v1`; exact source replay, format-E XML component/pin/net-node comparison, bounded malformed/DTD/unknown-structure cases |
| Metrics | Component parity `passed` for `2` components; connectivity parity `passed` for `3` nets and `4` connections; four hostile-input budget/structure refusals; deterministic source replay `passed` |
| Artifact | [`2026-08-05-kicad-schematic-parity.json`](../../benchmarks/results/schematic/2026-08-05-kicad-schematic-parity.json) |
| Interpretation | This is a reusable source/connectivity oracle for the bounded passive subset. It does not establish authoritative ERC, schematic-to-PCB parity, electrical correctness, broader symbol/library coverage, or fabrication readiness. |

#### B-040 — bounded front/back footprint observation and KiCad DRC oracle

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:671a5f6e40d372e8071cffa1f541564a09c2425fede44977bacbfdc1120db87a` |
| Date and commit | 2026-08-05; source commit `e7fefc9` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad 10.0.5 headless CLI DRC |
| Dataset | [`footprint-front-back-pose.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/footprint-front-back-pose.kicad_pcb), one asymmetric `F.Cu` and one asymmetric `B.Cu` footprint with matching rectangular courtyards |
| Configuration | `copper-mcp/benchmark/kicad-front-back-footprint-observation/v1`; Board IR parse, native identity/side/pad/courtyard assertions, and `kicad-cli pcb drc --format json --severity-all --exit-code-violations` |
| Metrics | Front footprints observed `1`; back footprints observed `1`; pad/courtyard pose match `true`; second mirror applied `false`; KiCad violations `0`; unconnected items `0` |
| Artifact | [`2026-08-05-kicad-front-back-footprint-observation.json`](../../benchmarks/results/placement/2026-08-05-kicad-front-back-footprint-observation.json) |
| Interpretation | This expands read-only observation to the narrow front/back rectangular-courtyard subset and pins a valid source/CLI DRC oracle. It is not a GUI flip-save round-trip, general courtyard topology, side-aware placement legality, apply, or FreeRouting result. |

#### B-041 — side-aware rectangular-courtyard placement legality

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:3b88877d622b04abc850137ad6804308f151652e699cc67cff1d2a666394fe54` |
| Date and commit | 2026-08-05; source commit `46d0ef5255093133619da961f61d093d4d0c5ba4` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad not invoked |
| Dataset | Front/back asymmetric Board IR fixture plus the no-courtyard placement fixture; synthetic source variants are created in memory and never written |
| Configuration | `copper-mcp/benchmark/placement-courtyard-legality/v1`; exact integer pose transformation and same-side rectangle overlap through `evaluate_placement` |
| Metrics | Same-side overlap: `refused`, `courtyard_overlap=violated`, `pad_overlap=proven_clear`; cross-side overlap: `previewed`, `proven_clear`; absent courtyard: `previewed`, `proven_clear`; workspace mutations `0` |
| Artifact | [`2026-08-05-courtyard-legality.json`](../../benchmarks/results/placement/2026-08-05-courtyard-legality.json) |
| Interpretation | This closes the former placement `not_modelled` gap for the exact Board IR v0.2 rectangular subset. It does not establish nonzero custom courtyard clearance, general polygon/line-chain topology, KiCad DRC, post-placement connectivity, apply, or FreeRouting parity. |

#### B-042 — separately authorized bounded placement apply

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:096c9bcb467871e1c24611aa0240aa3294e4cb02b9d81ab0d4725a2ede33e301` |
| Date and commit | 2026-08-05; source commit `d0fbc09a86565d30c834e7b33c9245212e485a9c` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad and live IPC not invoked |
| Dataset | [`placement-legal.kicad_pcb`](../../tests/fixtures/placement-v0.1/placement-legal.kicad_pcb), one committed front-side orthogonal footprint fixture |
| Configuration | `copper-mcp/benchmark/placement-apply/v1`; explicit placement-scoped token; operator apply enabled only in an isolated temporary workspace; source/snapshot/candidate CAS; lock/backup/atomic service; route-token cross-domain replay |
| Metrics | Apply status `applied`; `footprints_moved=1`; `bytes_changed=530`; exact pre-apply backup `true`; result reparsed clean `true`; result revision matched `true`; route-token cross-domain refusal `true`; workspace files `1 → 2`; workspace mutations `2` |
| Artifact | [`2026-08-05-placement-apply.json`](../../benchmarks/results/placement/2026-08-05-placement-apply.json) |
| Interpretation | This closes the file-backed mutation gate for the documented front-side, orthogonal, native-identity, rectangular-courtyard subset. It does not establish general KiCad footprint fidelity, side flips, post-placement DRC/connectivity, live IPC mutation, undo integration, fabrication/electrical safety, or FreeRouting parity. |

#### B-043 — deterministic synthetic audio-routing microcase

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:d57b448c4fb6982686230bf9c9bda98751fb5de451147dd9755d21c4f2e8fd98` |
| Date and commit | 2026-08-05; source commit `d0fbc09a86565d30c834e7b33c9245212e485a9c` |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad and live IPC not invoked |
| Dataset | [`rc-low-pass-routing-v1.kicad_pcb`](../../benchmarks/audio/fixtures/rc-low-pass-routing-v1.kicad_pcb), CopperMCP-original synthetic two-pad RC microcase with Apache-2.0 catalog metadata |
| Configuration | `copper-mcp/benchmark/audio-routing-gap/v1`; ten deterministic route previews, candidate identity replay, disposable source-preserving serialization, and Board IR reparse |
| Metrics | Candidate deterministic `true`; derivative bytes deterministic `true`; original segments `0`; rendered segments `1`; new copper segments `1`; new copper length `8,000,000 nm`; source unchanged `true`; candidate applied `false`; authoritative DRC `false` |
| Artifact | [`2026-08-04-audio-routing-gap-378d4c6.json`](../../benchmarks/results/routing/2026-08-04-audio-routing-gap-378d4c6.json) |
| Interpretation | This is a deterministic candidate-to-disposable-copper regression for a synthetic low-voltage audio-shaped RC net. It does not establish external-board coverage, circuit correctness, KiCad DRC, apply, manufacturability, fabrication readiness, hardware behavior, or FreeRouting parity. |

#### B-044 — public file-backed placement preview with opt-in KiCad DRC

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:96073306d5b6ca4c65730e90d7da91e4e28073f57527d065a878d3453ec76169` |
| Date and commit | 2026-08-05; source commit `2d0172cf127522b73d5afa3c93bdcb49754038e5` |
| Environment | Apple arm64 CPU; Python 3.12.13; KiCad 10.x headless CLI DRC |
| Dataset | [`placement-legal.kicad_pcb`](../../tests/fixtures/placement-v0.1/placement-legal.kicad_pcb), the committed front-side orthogonal rectangular-courtyard placement fixture |
| Configuration | `copper-mcp/benchmark/public-placement-preview-drc/v1`; three public `preview_placement` calls with `include_drc=true`, private disposable KiCad JSON DRC, candidate/source/patched-board/context binding, and source preservation checks |
| Metrics | `passed_drc_runs=3`; `clean_drc_runs=0`; deterministic aggregate counts `{error_count:0, warning_count:0, exclusion_count:0, ignored_check_count:5, unconnected_count:0}`; deterministic evidence digests `1`; candidate/context binding `true`; source bytes/inode/mtime preserved `true`; workspace mutations `0`; median preview+DRC `423,686,875 ns` |
| Artifact | [`2026-08-05-public-placement-drc.json`](../../benchmarks/results/placement/2026-08-05-public-placement-drc.json) |
| Interpretation | This measures the new public disclosure boundary and private replay, not a clean-board claim: hard-gate `passed` is distinct from strict `clean`, here because KiCad reports five ignored check classes. It does not establish general footprint fidelity, live editor CAS, apply, ERC/electrical correctness, fabrication readiness, hardware behavior, or FreeRouting parity. |

#### B-045 — revision-bound post-placement scene and DRC observation

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c175adf67ca1406cc7fd272fb21bc866936b364f3b8832c2f104a331db8b0cba` |
| Date and commit | 2026-08-05; source commit `eb44ae9253166eabf0cf190beada1b990c32766e` |
| Configuration | One legal bounded placement apply in a temporary workspace, followed by three required-revision observations deriving bounded Circuit Scene and private aggregate KiCad DRC from one captured context. |
| Metrics | Three observations bind to one returned post-apply board revision and one scene/DRC context; post-apply board bytes preserved by the observer; post-observer workspace mutations `0`; hard DRC pass `true`, clean `false` due to five ignored checks; median latency `378,795,500 ns`. |
| Artifact | [`2026-08-05-post-placement-observation.json`](../../benchmarks/results/placement/2026-08-05-post-placement-observation.json) |
| Interpretation | This demonstrates a legal file-backed apply followed by three read-only observations of the exact revision returned by apply. It does not provide cryptographic mutation provenance beyond that revision CAS, live editor CAS, ERC/electrical/fabrication readiness, or FreeRouting parity. |

#### B-052 — post-placement observation boundary replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:d1c586ac5413cde8a40292f15e45e75944e71acef6422d8d7b5ea88229a3cc4e` |
| Date and commit | 2026-08-05; source commit `5fd8fab8f41f20edffecb89a181947d13d59a256` |
| Configuration | Three required-revision post-placement observations over the bounded placement fixture, with workspace state digested before and after the observer; malformed/stale pre-work and padless rule-order regressions are covered by focused tests. |
| Metrics | Three observations; one binding signature; hard DRC pass `true`; clean `false` due five ignored checks; median latency `432,344,250 ns`; workspace state before/after both `sha256:1a453c07a4018679a41652a141c476362c5a319881d6d2a06dc65f6156ccfdc8` with two entries; `workspace_mutations=false` derived from those snapshots. |
| Artifact | [`2026-08-05-post-placement-observation-replay-b052.json`](../../benchmarks/results/placement/2026-08-05-post-placement-observation-replay-b052.json) |
| Interpretation | This is an append-only correction to the B-045 measurement boundary. It does not add mutation provenance, live/editor CAS, ERC/electrical/fabrication signoff, or FreeRouting parity. |

### Append-only artifact-preservation amendments

These entries preserve superseded content-addressed benchmark artifacts when a historical result
path was later reused by a replay. No earlier ledger row is rewritten; the recovered files below
are the audit copies for the original run IDs.

#### B-053 — preserve B-045 post-placement artifact copy

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c175adf67ca1406cc7fd272fb21bc866936b364f3b8832c2f104a331db8b0cba` |
| Historical source | `eb44ae9253166eabf0cf190beada1b990c32766e` |
| Artifact | [`2026-08-05-post-placement-observation-historical-b045.json`](../../benchmarks/results/placement/2026-08-05-post-placement-observation-historical-b045.json) |
| Reason | The original B-045 bytes are preserved as an explicit audit copy while B-052 records the stronger workspace-state measurement. The original B-045 row and artifact path remain unchanged. |

#### B-046 — preserve superseded B-033 spatial-index artifact

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:0375d0e76d5aedbd0dd7dbed082d07f07fc8793ea38cb87ee92faade91993631` |
| Historical source | `62f10efcd82a8a6e0974e15d69e56573d4115c6e` |
| Artifact | [`2026-08-05-spatial-index-historical-b033.json`](../../benchmarks/results/routing/2026-08-05-spatial-index-historical-b033.json) |
| Reason | The original B-033 path was reused by the corrected B-037 replay. This recovered copy preserves the exact old bytes and run ID; B-037 remains the current predicate-work evidence. |

#### B-047 — preserve superseded B-038 layered DRC artifact

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:398b17f57e8525808e6ce3c3247327c76acd0155eb0d6f9679085cc3c3544c65` |
| Historical source | `896c88d9405cfed469d60d45e1b80fd9bdfad2ad` |
| Artifact | [`2026-08-05-layered-drc-hardening-historical-b038.json`](../../benchmarks/results/routing/2026-08-05-layered-drc-hardening-historical-b038.json) |
| Reason | The original B-038 path was reused by the replay that corrected its source provenance. This recovered copy preserves the superseded payload without changing the historical row. |

#### B-048 — preserve superseded B-043 audio-routing artifact

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:e4b1409098de79f6ebf496ac7c53ba3be7763c84e1fb8bfd2f01eda26dad88d4` |
| Historical source | `ceb4e9f31cd348d976c46fdd76c08a19d6648fda` |
| Artifact | [`2026-08-04-audio-routing-gap-historical-b043.json`](../../benchmarks/results/routing/2026-08-04-audio-routing-gap-historical-b043.json) |
| Reason | The original B-043 path was reused by a later clean-tree replay. This recovered copy preserves the earlier candidate-only audio microcase evidence and its exact run ID. |

#### B-049 — preserve superseded B-044 public placement DRC artifact

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:bed5fb0c00fdbea3083d4cfcf305d0745da9de5ecdc7130dc43ccc7e30041745` |
| Historical source | `6959bc22b45ce6a2a4550ef3ce375cc9f332fb8e` |
| Artifact | [`2026-08-05-public-placement-drc-historical-b044.json`](../../benchmarks/results/placement/2026-08-05-public-placement-drc-historical-b044.json) |
| Reason | The original B-044 path was reused by a later aggregate-count transparency replay. This recovered copy preserves the superseded public-preview evidence and exact run ID. |

#### B-050 — stationary padless-courtyard collision replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:7681e24b5362769ae432c0eaa5d059d331c53e443f58d942b29e62e16c3e08f0` |
| Date and commit | 2026-08-05; source commit `94700092f51f4825deb794ba9256be58f05cc1d5` |
| Environment | Apple arm64 CPU; Python 3.12.13; KiCad not invoked |
| Dataset | [`padless-footprint.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/padless-footprint.kicad_pcb), one pad-owning footprint and one graphics-only rectangular-courtyard footprint |
| Configuration | `copper-mcp/benchmark/padless-courtyard/v1`; one baseline preview and one deterministic proposed move into the stationary padless courtyard |
| Metrics | Baseline `previewed`; collision `refused`; `stationary_padless_courtyards=1`; `collision_courtyard_overlap=violated`; source SHA-256 `sha256:cd235d2a11a12ef68e9f8039f05f790494f7e4969e190c495396f13fcbd77102`; workspace mutations `0` |
| Artifact | [`2026-08-05-padless-courtyard.json`](../../benchmarks/results/placement/2026-08-05-padless-courtyard.json) |
| Interpretation | This measures the bounded fix prompted by KiCad's courtyard-placement semantics and the review-bot reproduction. It covers only rectangular Board IR v0.2 courtyards; custom clearance, arbitrary topology, DRC, apply, fabrication, and FreeRouting parity remain unclaimed. |

#### B-051 — cooperative IPC parser deadline replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:fe76a3536d5613dd4825f6e8ffbbbf8e3d97eb341ca4c6fc9d6de333297782ca` |
| Date and commit | 2026-08-05; source commit `c00ea8c59cb69560772d14ae6cfc53b794da1a2e` |
| Configuration | `copper-mcp/benchmark/ipc-deadline-parse/v1`; one 900,011-byte malformed board payload with an already-expired typed callback and fixed parser ceilings |
| Metrics | Deadline refusal `true`; callback invoked once before parser materialization; parser completion `false`; elapsed `24,875 ns` on this host; payload digest recorded in the artifact |
| Artifact | [`2026-08-05-ipc-deadline-parse.json`](../../benchmarks/results/routing/2026-08-05-ipc-deadline-parse.json) |
| Interpretation | This measures cooperative early refusal and typed error preservation. It does not claim hard process pre-emption, interruption during one atomic UTF-8 decode, or interruption of a blocking official KiCad IPC call. |

#### B-054 — spatial-index candidate-work provenance correction

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:ff639282bc61125319a151041e0b07497b87fdf44bb9f9dfc746f18130643163` |
| Date and commit | 2026-08-05; source commit `748cc586963a33392b73842a97fff888dc6af6d0`; clean tree before artifact generation |
| Configuration | `routing-conservative-spatial-index-v1`; 512 entries, 256 exact closed-AABB queries, and seven timing repetitions; source ordinals remain the equality authority |
| Metrics | Exact ordered identities `256/256`; legacy relation checks `131,072`; indexed candidate checks `636`; candidate-work reduction `99.5148%`; median fixture speedup `19.125x` on this machine |
| Artifact | [`2026-08-05-spatial-index-provenance-correction.json`](../../benchmarks/results/routing/2026-08-05-spatial-index-provenance-correction.json) |
| Correction | The B-033 review-remediation artifact was generated before the script measured examined candidates, so its `31` value was a returned-hit count, not relation work. Its exact identity result remains historical evidence; this row is the replacement for candidate-work metrics. |
| Interpretation | Synthetic uniform-grid fixture only; no whole-board scaling, congestion, KiCad DRC, electrical/fabrication readiness, or FreeRouting claim. Wall-clock timing is machine-bound. |

#### B-055 — review-remediation source-commit clarification

| Field | Recorded evidence |
|---|---|
| Correction | The shared review-remediation preface's `d69f3b0b9e563ef5d4d6c1e99a6ef47508fdf51b` wording applies to B-022, B-026, B-027, and B-032. The embedded source commits in the B-031 and original B-033 remediation artifacts are `43df15d62336a95d44f960181a9b0fe1e2279c2e`; artifact-level `source_commit` is authoritative. |
| Scope | This corrects provenance description only. It changes neither historical artifact bytes nor the B-031 topology metrics; B-054 replaces the B-033 candidate-work metric claim. |

#### B-056 — post-placement workspace metadata replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:bca66bf635e4f669b959f371dd098168ca8fe4633696f07f32c779f60d1af8b3` |
| Date and commit | 2026-08-05; source commit `b49813027c8b024863847f53630dac7718cb0a48`; clean tree before artifact generation |
| Configuration | Three required-revision post-placement observations over the bounded placement fixture; before/after workspace snapshots cover every visible file and symlink with `kind`, `path`, `mode`, `inode`, `mtime_ns`, `sha256`, `size`, and `target` fields where applicable. |
| Metrics | Three observations; one scene/DRC binding signature; hard DRC pass `true`; clean `false` due five ignored checks; median latency `388,642,375 ns`; board bytes preserved `true`; workspace state before/after both `sha256:aa1aad576600947b500c18fdd06737288d20d43656594ae645b0a86aa79e632a` with two entries; `workspace_mutations=false` derived from metadata-sensitive snapshots. |
| Artifact | [`2026-08-05-post-placement-observation-replay-b056.json`](../../benchmarks/results/placement/2026-08-05-post-placement-observation-replay-b056.json) |
| Interpretation | This is an append-only evidence correction to B-045/B-052: it detects content, mode, symlink-target, inode, and nanosecond-mtime changes in the disposable workspace. It does not provide filesystem transaction provenance, live/editor CAS, ERC/electrical/fabrication signoff, or FreeRouting parity. |

#### B-057 — B-056 tracked-tree provenance qualifier

| Field | Recorded evidence |
|---|---|
| Correction | In B-056, “clean tree before artifact generation” means the tracked repository tree matched the recorded `source_commit`; the user-owned untracked [`docs/HANDOFF-CODEX.md`](HANDOFF-CODEX.md) was intentionally excluded from staging and does not belong to the benchmark workspace. |
| Scope | No B-056 artifact bytes, run ID, metrics, or source commit change. This append-only note narrows the provenance wording without exposing or modifying the handoff contents. |

#### B-058 — negotiated physical-clearance acceptance replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:6b55bb5d8fc59c26bec6c657a5af68249703081cded6224a4103c1cb49183397` |
| Date and commits | 2026-08-05; implementation `e6634a72e7e1ec1204b38c5907a93b4a2e15e4ba`; evidence harness `5a2c1073de2a97cbb69e3a54d5077db0d4a24ba8` |
| Configuration | Three deterministic CPU-only replays of two one-layer, orthogonal, self-identified synthetic candidates with exact assigned 600,000 nm widths; no external EDA tool invoked. |
| Metrics | Lattice overflow `0`; legacy lattice-only acceptance `true`; available centreline clearance `300,000 nm`; governing pairwise net-class clearance `500,000 nm`; exact gate rejected the set as `clearance_violation` after one pair check; replays deterministic `true`. |
| Artifact | [`2026-08-05-negotiated-physical-clearance.json`](../../benchmarks/results/routing/2026-08-05-negotiated-physical-clearance.json) |
| Interpretation | This isolates a post-lattice candidate-pair acceptance delta. It does not measure KiCad DRC, existing-board copper, pads, vias, zones, custom rules, multilayer geometry, fabrication clearance, or FreeRouting parity. Generic custom-router outputs are independently reference-replayed under shared half-budget accounting in production tests; the current generic linear verifier is the safety gate, not a claimed acceleration. |

#### B-059 — bounded placement-solver proxy replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:e41d1061f17bd43cfa402e9c88d84d0015d87c0351725cc0b660968882cae9e5` |
| Date and commit | 2026-08-05; source commit `b6d20e8a442614d7cb1f34d4eb4ee3c33a8fa94d` |
| Dataset | [`footprint-rotation.kicad_pcb`](../../tests/fixtures/board-ir-v0.1/footprint-rotation.kicad_pcb), a committed Board IR v0.1 fixture |
| Configuration | Three deterministic replays; local/beam settings: 128 evaluations, five rounds, beam width four, 1,000,000 nm step. Retained candidates are existing legalizer-issued candidates. |
| Metrics | All retained candidates legal `true`; deterministic replays `true`; same-net Manhattan proxy `12,000,000 → 10,000,000 nm`; improvement `2,000,000 nm`. |
| Artifact | [`2026-08-05-placement-solver-baseline-v1.json`](../../benchmarks/results/placement/2026-08-05-placement-solver-baseline-v1.json) |
| Interpretation | The proxy is not routed length, an optimality guarantee, DRC, electrical/timing/SI/thermal/fabrication evidence, KiCad mutation, routing feasibility, congestion, or manufacturing-clearance evidence. |

#### B-060 — advisory AI routing-policy boundary replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:b7ffb5991153a3612764296761a9813c669e536f282f49077c368678ac107ab7` |
| Date and commits | 2026-08-05; implementation `f1af095bfd6712969b0f9fb29933cd48aedef0dc`; evidence harness `5a2c1073de2a97cbb69e3a54d5077db0d4a24ba8` |
| Configuration | Ten deterministic evaluations of the bounded reference policy over the committed closed input fixture; canonical decision/trace replay plus hostile decision-digest refusal. |
| Metrics | Closed decision accepted `true`; hostile decision refused `true`; decision/trace deterministic `true`; routing invoked `false`; copper emitted `false`; route-quality and physical-validity claims `false`. |
| Artifact | [`2026-08-05-ai-policy-trace-privacy.json`](../../benchmarks/results/routing/2026-08-05-ai-policy-trace-privacy.json) |
| Interpretation | This proves a bounded advisory contract and redacted-trace replay only. It is not route-integrated and does not measure learned-policy quality, PCB routing, KiCad DRC, fabrication, FreeRouting parity, or secrecy against a complete low-entropy-record dictionary test. |

#### B-061 — FreeRouting comparison harness preflight (no comparison run)

| Field | Recorded evidence |
|---|---|
| Date | 2026-08-05 |
| State | The local preflight found `/usr/bin/java` but no installed Java runtime, no `kicad-cli`, no released FreeRouting JAR, no common licensed DSN/KiCad fixture, and no result-board pair. No download, GPL source copy, fixture addition, routing invocation, or comparison report was produced. |
| Harness | [`benchmark_freerouting_comparison.py`](../../scripts/benchmark_freerouting_comparison.py) and [comparison boundary](../research/freerouting-comparison.md) require bounded external inputs, a minimal environment, KiCad DRC for both boards, and provenance receipts. |
| Closure | There is intentionally no run ID or score: self-attested SES-import and CopperMCP-runner receipts always yield `unavailable_or_incomplete` and can never close a comparison. A future harness-owned constrained KiCad import and candidate-runner transaction is required before a result can be evaluated. |

#### B-062 — B-058 terminology and safety-gate correction

| Field | Recorded evidence |
|---|---|
| Correction | B-058's `300,000 nm` is the available copper-edge clearance after two 600,000 nm track widths, not centreline clearance. The earlier wording is preserved above as append-only history. |
| Safety gate | B-058/D-103's “generic linear verifier” wording was inaccurate: no generic linear independent candidate verifier is implemented. Exact deterministic A* replay is the current safety gate for custom negotiated-router output under the shared half-budget allocation. A generic linear independent verifier remains future acceleration work. |
| Scope | No B-058 artifact bytes, run ID (`sha256:6b55bb5d8fc59c26bec6c657a5af68249703081cded6224a4103c1cb49183397`), implementation/evidence commits, or physical-gate result changes. This correction adds no KiCad DRC, board-wide clearance, or real FreeRouting comparison claim. |

#### B-063 — negotiated policy initial-order replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:c63409d9429c345c70817ae5516d8b73d3055f01074a1bbff3e4e9765c22c7fa` |
| Date and commits | 2026-08-05; implementation `cde2f9adc3a6436dbe99a20a12946cc70616f232`; typed harness base `62570d5bcbe4d812028f77380cef8230241a1785`; evidence harness `2d249640bc2c6abfd0ad5a89d21af7b89366ef4a`; artifact materialization `e0345a7bf2d65a8f3a8e75c4ba4f4ba64e146dca`. |
| Configuration | `copper-mcp/benchmark/routing-policy-order/v1`; ten deterministic replays per synthetic fixture compare no-profile routing with the exact internal `deterministic-reference-v1` profile. The profile can affect only the first negotiated net order; retry ordering remains coordinator-owned. |
| Metrics | All baseline/profile runs completed with zero overflow. Primary asymmetric fixture: baseline `185` expansions, `2,585` obstacle checks, `22 mm`, `1` iteration; profile `392`, `5,307`, `24 mm`, `2`. Independent control: baseline `121`, `1,625`, `18 mm`, `1`; profile `315`, `4,174`, `20 mm`, `2`. Neutral crossing control was equal at `197` expansions, `2,576` checks, `26 mm`, and `1` iteration. |
| Artifact | [`2026-08-05-routing-policy-order.json`](../../benchmarks/results/routing/2026-08-05-routing-policy-order.json); the artifact records its script SHA-256, profile, policy ID, three fixture digests, and ten deterministic replays. |
| Classification | `order-effect/no quality claim` |
| Interpretation | This shows only that the bounded initial-order profile has a deterministic scheduling effect in synthetic fixtures; it makes no quality or routing-improvement claim. KiCad DRC was not run, apply was not invoked, and no learned/model output or model-generated copper was used. It provides no manufacturing, fabrication, board-mutation, or FreeRouting-parity evidence. |

#### B-064 — live IPC fidelity-oracle fake-client and capability replay

| Field | Recorded evidence |
|---|---|
| Date and source | 2026-08-05; implementation/remediation commit `bed248b98e5a315f0a2daac2fbce5f646dfbd564`. |
| Configuration | One deterministic fake KiCad 10.0.5 client returns the committed supported Circuit Scene fixture; the oracle receives plugin-shaped non-secret test credentials, a 2,000 ms cooperative deadline, and no mutable method implementation. Separate replays remove credentials, inject hostile workspace configuration, malformed endpoint/token configuration, generic configuration refusal, and post-capture deadline expiry. |
| Metrics | Four digest equalities true: captured source/observation, Board IR source/observation, Scene source/observation, and Scene/Board IR snapshot. Mutating calls `0`; client closure `true`; absent credentials return `kicad_plugin_environment_absent`; hostile workspace script exit `0` with no traceback/path; post-capture expiry starts neither Board IR nor Scene conversion. |
| Artifact | Focused regression suite [`test_kicad_live_ipc_oracle.py`](../../tests/test_kicad_live_ipc_oracle.py) and read-only CLI [`probe_kicad_live_ipc.py`](../../scripts/probe_kicad_live_ipc.py). No private board, token, socket, or editor output is committed. |
| Interpretation | This is fake-client contract evidence and a local capability replay, not a real KiCad GUI/API-server observation. It makes no live-editor fidelity, DRC, routing, placement, mutation, electrical, fabrication, or FreeRouting claim. |

#### B-065 — MCP excessive-agency boundary replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:0c0b27c9a6c86de4422e00d9093ba36b25dd7442fd56b7d4fb62da9948da0c58` |
| Date and code provenance | 2026-08-05; hardened evaluation harness `7f7a3d71c2b960f8917deeef0dc0d43fb937d227`; the artifact bytes are committed with this ledger entry. |
| Configuration | `copper-mcp/security-evaluation/mcp-agency/v1`; seven deterministic offline MCP cases. The public route and placement apply handlers run with apply enabled and receive syntactically valid unauthorized tokens. Temporary-workspace snapshots compare path, type/symlink status, mode, size, digest, mtime, and inode; the report contains only stable unchanged assertions. |
| Metrics | Attempted/blocked `7/7`; refused `4`; contained `3`; leaked `0`; `apply_candidate` and `apply_placement_candidate` each returned structured `invalid_token` before source access; workspace content/mode/metadata unchanged. |
| Artifact | [`2026-08-05-mcp-agency-evaluation.json`](../../benchmarks/results/security/2026-08-05-mcp-agency-evaluation.json) |
| Interpretation | This is a local MCP input, output/report-disclosure, revision, quota, and capability-boundary regression result. It invokes no model, network, KiCad, or board mutation. Application logging is not evaluated because the current source has no application logger sink; host logs, provider telemetry, remote authorization, unknown attacks, electrical/DRC/fabrication safety, and live-editor behavior remain outside scope. |

#### B-066 — held-out audio project-family evidence replay

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:44d3c793c0e5b76f856640ccc576692086b31c1688471362d210cef91a08587c` (nested evidence run `sha256:34d83180f8fd5b62c17c0b1bc7d8306301d5949a7851e6f31b148177da261884`). |
| Date and commits | 2026-08-05; clean evidence source `96110ee9c3dbf9769070abaaffefb85a4018a4cc`; artifact refresh `6e6ea69053bf9652c14dc7dc3839e6763736763e`; detached isolated-replay regression `d7c1625d75b83528377d29bf8da6801906781bfb`. |
| Environment | The content-addressed report deliberately records no host, OS, Python, memory, or timing observation. It records `network_access=false`, `kicad_invoked=false`, `candidate_applied=false`, and exact deterministic service outputs; therefore it is not a performance or cross-host result. |
| Dataset | Independently authored Apache-2.0 `ac-coupled-signal-chain-v1.kicad_pcb`, SHA-256 `2afcfabf4b6d910a21d507978686da1f24d5801521fbf6923724d4abd276b64f`; licence SHA-256 `7ad27bd3d842c3cb15e67e107f51d6d5496470bc7b82f4fb91294721a75b6351`. The hash-bound split declares existing `passive-rc-low-pass` as training, no tuning family, and the new `ac-coupled-signal-chain` family as held out; the evaluator records that no training/tuning fixture was read. Elliott Sound Products and diyAudioProjects remain reference-only and contributed no copied design content. |
| Configuration | Three repetitions of the unmodified inspection, bounded placement, and per-net route-preview services; script, fixture, licence, provenance, and split bytes are content-addressed. No model, learning, policy comparison, network, KiCad, or apply operation. |
| Metrics | Exact replay signature stable across 3/3 repetitions. Inspection: 6 footprints, 12 pads, 6 nets. Placement: `work_exhausted`, 96 evaluations, 8 legal candidates, same-net Manhattan proxy `202,000,000 → 198,000,000 nm`, zero proxy-rule violations. Routing preview: 6/6 attempted nets produced candidates, completion fraction `1.0`, 18,996 expansions, 2,743 obstacle checks, `205,000,000 nm` total candidate wire length, zero hard internal violations. Source unchanged `true`. |
| Artifact | [`2026-08-05-audio-project-family-v1.json`](../../benchmarks/results/heldout/2026-08-05-audio-project-family-v1.json), [split/protocol note](../research/heldout-audio-project-family-evaluation.md). |
| Interpretation | This closes only the first project-family split and exact-replay evidence step. One original family is not a corpus, 6/6 candidate-preview completion is not whole-board routing or quality, and the placement proxy is not placement quality. No policy training/improvement, KiCad DRC/ERC, electrical/SI/thermal, external-project coverage, mutation/live-editor, fabrication, or audio-hardware claim is made. |

#### B-067 — standalone exact local-repair acceptance fixture

| Field | Recorded evidence |
|---|---|
| Run/result ID | Route digest `sha256:3a8a880a7f02057c3093de4f417e428051180dcaaebcf665ea78682767ec6a9b`; input digest `sha256:1e3e44890fbef2bec22e3173545b8cc2d43d231e72ef349fb10e34599fd1ffae`. No standalone benchmark artifact was generated. |
| Date and commit | 2026-08-05; final current-request implementation `61f052998497d0e8e49f2dab323d2cae353c3491`; verification replay at integration source `148479870135f7f4d155593be03e11a55de230ec`; tracked tree clean with one unrelated untracked user document present before documentation work. |
| Environment | Apple arm64 CPU; no accelerator; 38,654,705,664 physical bytes; macOS 26.5.2; Python 3.12.13; no network or KiCad invocation. No timing or memory sample was recorded. |
| Dataset | Repository-original Apache-2.0 `window-detour-v1` regression: integer cells `[0,4] × [0,4]`, `(0,2)` to `(4,2)`, with `(2,1)`, `(2,2)`, and `(2,3)` occupied. This is an abstract lattice fixture, not a PCB board or train/test corpus. |
| Configuration | Ten equality replays; lexicographic unit-step/bend/cell-sequence Dijkstra; at most 64 expanded states. Separate one-expansion, cancellation, callback-exception, malformed-current-request, and forged-result checks. |
| Metrics | 10/10 identical completed routes; canonical upper detour; 8 unit steps, 2 bends, exactly 50 expanded states; no blocked or out-of-window cell; result verifier `true`. A one-expansion budget returns atomic `budget_exhausted` with one expansion and no route; cancellation/callback exception returns atomic `cancelled` with no route. The verifier rejects diagonal, repeated, out-of-window, blocked, wrong-endpoint, wrong-bend, type-confused, and digest-forged results. |
| Artifact | Committed regression [`test_routing_repair.py`](../../tests/test_routing_repair.py) and [bounded local-repair protocol](../research/bounded-local-exact-repair.md); no machine-readable result file or performance log. |
| Interpretation | This records deterministic standalone local geometry and fail-closed result verification only. The operator is not connected to negotiated routing, Board IR, a route candidate, MCP, KiCad, physical clearance, DRC, or apply. It provides no authenticated window provenance, PCB routing-quality improvement, model-quality, manufacturing, or hardware claim. |

#### B-068 — clean-worktree performance profile v1

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:8a60c49915613a56635cda7ed6aec0b1bbf15598697cdba403a1474b00fa6c5b`; deterministic identity `sha256:c8ef2dcb7eaebe846488fdb29adf4ecba0ee1a4b44f125012db1460488dfdb12`. |
| Date and commits | 2026-08-05; clean source provenance `412daf6c8d9f070f7d94eb81aae13ceb6331f2d1`; artifact materialization `ba20400ebeebdcfcbd179a9d927649bbdd7d733e`; isolated-provenance regression `25cc40f2925621dc41f58b1c034d9b96f3d059d3`. |
| Environment | Apple arm64; Python 3.14.2. OS, memory, dependency versions, CPU model/load/frequency, and thermal state were not recorded, so absolute timings cannot be compared across machines. KiCad CLI was not invoked. |
| Dataset | Three committed local fixtures, bound by SHA-256 in the identity: two-pad file-backed route preview, footprint-rotation placement, and file-backed Circuit Scene observation. No train/test split; these are contract fixtures, not a routing-quality corpus. |
| Configuration | Fixed seed 23; 2 warmups and 5 unprofiled `perf_counter_ns` samples per scenario; identical output digest required for every sample; one separate `cProfile` pass with at most 8 cumulative rows and stable redacted function labels. Timing, profile costs, operational span, machine, and Python version are environmental observations outside deterministic identity. |
| Metrics | Median/min/max: placement `133,169,459 / 131,540,208 / 137,295,375 ns`; routing `3,913,917 / 3,222,750 / 4,547,125 ns`; scene `1,969,958 / 1,727,917 / 2,441,958 ns`. The separately instrumented placement pass reports cumulative `solve_placement` `324,204,792 ns`, `evaluate_placement` `265,092,867 ns`, `rect_inside_ring` `155,690,123 ns`, and `_segments_intersect` `102,952,965 ns`; nested cumulative rows must not be summed. |
| Artifact | [`2026-08-05-performance-profile-v1.json`](../../benchmarks/results/performance/2026-08-05-performance-profile-v1.json) and [measurement protocol](../research/performance-profile-v1.md). |
| Interpretation | This identifies placement containment/intersection geometry as the strongest first experiment on one recorded environment; it does not prove a bottleneck across boards or that Rust, SIMD, or GPU execution will help. No acceleration, speedup, cross-machine precision, KiCad DRC, routing/placement quality, mutation, fabrication, or hardware-performance claim is made. Any future acceleration must preserve the recorded output digests and compare against the same manifest separately. |

#### B-069 — real FreeRouting v2.2.2 two-pad smoke observation

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:b72a4ab2dfe1d44d1d2641af9e62f20daec63ff78ce2b0843196daeb2e0d3ddf` |
| Date and commits | 2026-08-05; final independently reviewed evidence commit `0009b65e3474c4c71e126fe11fa827f01bbcb673`; integration commit `b5313e08ec93b1cd88a8d05b3e0e63aaedee1900`. The committed artifact and research-note blobs are byte-identical across those revisions. |
| Official tool input | FreeRouting v2.2.2 GPL-3.0-only JAR from [`https://github.com/freerouting/freerouting/releases/download/v2.2.2/freerouting-2.2.2.jar`](https://github.com/freerouting/freerouting/releases/download/v2.2.2/freerouting-2.2.2.jar), SHA-256 `f7a716c8f2586eb79d7e6c54c497a6752c3b2401730fdb75c37245d461baa228`. The released JAR remains outside the Apache-2.0 core and is neither copied nor linked into it. |
| Environment | macOS 26.5.2 arm64; Python 3.14.2; OpenJDK 26.0.2; KiCad CLI/UI 10.0.5. Seed `0`; 120-second process limit; `-Djava.awt.headless=true`; minimal allowlisted child environment with provider tokens stripped. Authorized child processes were lifecycle/output bounded, not filesystem/network/device sandboxed. |
| Dataset and source baseline | CopperMCP-original Apache-2.0 two-SMD-pad fixture with one intentionally unrouted `AUDIO` net; source SHA-256 `f2bb74d00f9195237ecebbf15d55c1e0175c38cc0ca65b3281edf64a3ee45c9c`; DSN SHA-256 `1596d6987d8cb5605c9acffd40bff02cd62845d5be31fb4b228c6379472b3210`. The recorded KiCad GUI source DRC counts are zero hard violations, one unconnected item, and zero footprint errors. Source bytes remained unchanged. |
| Execution | The official JAR emitted a valid SES, SHA-256 `eb016ba1a7a4e680c472787dcf49b5f115c969d33603951b9b9fb3e94ba8fc4a`. Its GUI-imported disposable board and CopperMCP's workspace preview plus standalone pure `apply_route_candidate` kernel output were saved and checked with strict parsed KiCad GUI DRC reports. The CopperMCP runner did not invoke MCP transport, operator authorization, apply tokens, CAS, backups, or atomic publication. |
| Observed output metrics | CopperMCP and FreeRouting each recorded zero KiCad hard violations, zero unconnected items, zero vias, and `20,000,000 nm` routed length. CopperMCP process elapsed `221,698,250 ns`; FreeRouting process elapsed `4,248,779,750 ns`. Those timings cover different pipelines on one trivial fixture and are not a performance comparison. |
| Binding and closure | `dsn_source_export_binding.status` and `source_drc_binding.status` are `self_attested_unverified`: separate hashes and a basename-only GUI report header do not causally prove the source/export or source/report relationships. SES-import and pure-kernel runner workflow receipts are also self-attested. The artifact therefore records `comparison_closed=false`, `status=unavailable_or_incomplete`, and `incomplete_reason=self_attested_unverified`. |
| Artifact | [`2026-08-05-freerouting-common-two-pad.json`](../../benchmarks/results/routing/2026-08-05-freerouting-common-two-pad.json) and [real-run protocol/review note](../research/freerouting-real-run-v2.md). |
| Interpretation | This is evidence that the public release process, DSN/SES bridge, disposable KiCad result checks, and CopperMCP pure kernel were exercised on one original two-pad smoke fixture. It does not establish causal workflow provenance, feature parity, comparative quality or performance, whole-board routing, MCP/apply-service safety, executable sandboxing, fabrication readiness, or comparison closure. A harness-owned constrained SES-import transaction and candidate-runner transaction remain required before closure. |

#### B-070 — harness-owned transaction containment regression

| Field | Recorded evidence |
|---|---|
| Scope | Focused test-only harness boundary regression; no KiCad, Java, FreeRouting JAR, board, network, or hardware execution occurred. |
| Configuration | Provider capability is represented by an owner-private temporary root with the required aggregate-budget declaration. Fakes observe subprocess arguments without executing external tools. |
| Metrics | 36 focused tests passed: absent capability observed zero process launches; provider-present Java/KiCad/KiCad-Python probes and every live caller-result DRC argument/CWD were provider descendants; source/import preservation and caller-result tamper checks refused before DRC. |
| Interpretation | This is code-path evidence for fail-closed containment semantics only. It does not prove an OS quota, sandbox, KiCad/FreeRouting interoperability, routing quality, performance, parity, manufacturing readiness, or comparison closure. |

#### B-071 — preserved historical exact local-repair admission replay

| Field | Recorded evidence |
|---|---|
| Correction | B-071 is retained audit history, but its related KiCad-derived fixture used 512 grid nodes, 20,000 expansions, 128 obstacles, and 200,000 obstacle checks. It is not equivalent to the predeclared `_crossing_snapshot` and `_requests` experiment and is not relied upon for that attribution. |
| Configuration | Historical `exact-local-repair-gate-v1` artifact; ten negotiated-routing replays under the recorded related-fixture envelope. |
| Metrics | Ten replays completed with zero overflow, one iteration, zero ripups, and no rejected allocation; local-repair and validator meters remained zero because no repair admission was attempted. |
| Artifact | Preserved [`2026-08-05-exact-local-repair-gate.json`](../../benchmarks/results/routing/2026-08-05-exact-local-repair-gate.json). |
| Classification | Declined; historical negative observation only. |
| Interpretation | This does not establish the predeclared experiment, coordinator integration, routing improvement, DRC, FreeRouting parity, or board-mutation authority. B-072 supersedes its experiment attribution without deleting or modifying this record. |

#### B-072 — corrected exact local-repair admission replay

| Field | Recorded evidence |
|---|---|
| Correction | Supersedes only B-071's predeclared-experiment attribution. B-071 remains immutable history. |
| Configuration | Ten negotiated-routing replays using an independently reconstructed source-`965d8fc97ddeb720251cb7863c7b62310637f301` fixture with snapshot digest `sha256:9ad048f6f439a7e71be4c1f115d8a205f00c92f0853e0c140725906c1acdb245`, 256 grid nodes, 5,000 expansions, 64 obstacles, and 100,000 obstacle checks. |
| Metrics | Ten replays completed with zero overflow, one iteration, zero ripups, and no rejected allocation. The repair and validator meters are zero because no rejected allocation was present; this is a declined gate, not a repair result. |
| Artifact | [`2026-08-05-exact-local-repair-gate-correction-v2.json`](../../benchmarks/results/routing/2026-08-05-exact-local-repair-gate-correction-v2.json), [`benchmark_exact_local_repair_integration_gate.py`](../../scripts/benchmark_exact_local_repair_integration_gate.py), and [`exact_local_repair_gate_fixture.py`](../../scripts/exact_local_repair_gate_fixture.py). |
| Classification | Declined; no integration or improvement claim. |
| Interpretation | The helper is regression-checked against the original semantic builder and imports no test code. This is benchmark-attribution evidence only; it adds no profile, transaction, public behavior, routing integration, DRC, FreeRouting parity, apply, or board-mutation claim. |

#### B-073 — exact orthogonal courtyard topology replay

| Field | Recorded evidence |
|---|---|
| Run ID | Recorded in [`2026-08-05-orthogonal-courtyard-topology.json`](../../benchmarks/results/placement/2026-08-05-orthogonal-courtyard-topology.json). |
| Dataset | KiCad 10.0.5-resaved `tests/fixtures/board-ir-v0.2/courtyard-orthogonal-chains.kicad_pcb`, containing one concave eight-vertex unfilled `fp_poly` courtyard and one unordered four-edge unfilled `fp_line` closed chain on `F.CrtYd`; no proprietary design data. |
| Protocol | Parse the committed fixture through Board IR v0.2 and the placement view; evaluate unchanged placement and one fixed `-20,000,000 nm` offset of the line-chain footprint. Separately invoke fixed-argv KiCad 10.0.5 CLI DRC on the source fixture. |
| Metrics | Baseline adapter outcome `unsupported.construct` (historical rectangular-only contract); current snapshot success `true`; observed polygon vertices `8`; observed line-chain vertices `4`; unchanged same-side result `proven_clear`; overlapping proposal result `courtyard_overlap=violated`; KiCad DRC violations `0`; unconnected items `0`; source mutation `false`. |
| Interpretation | This is a measurable acceptance expansion for simple closed orthogonal courtyard observation and exact candidate legality. It is not general KiCad courtyard support, nonzero custom-clearance support, DRC of a proposed placement, placement apply, GUI/live IPC evidence, electrical/fabrication signoff, or FreeRouting parity. |

#### B-074 — original NE5532-class multi-pin route-preview and DRC derivative replay

| Field | Recorded evidence |
|---|---|
| Date and fixture | 2026-08-05; CopperMCP-original Apache-2.0 `ne5532-stereo-summing-routing-v1.kicad_pcb`, SHA-256 `749adc8b4d26b7f7ef878f9cf681521a8efdf446b9f0bf559243918e6e1957a9`; root licence SHA-256 `947af68b9ff8f542f5bba7a084e343573274741f4d7e4cbe1e4e9668a89331de`. TI NE5532x datasheet and KiCad PCB-format links are recorded in the fixture provenance; no third-party schematic, PCB, artwork, values, BOM, or board file is included. |
| Configuration | `scripts/benchmark_ne5532_audio_routing.py`, fixed seed `5532`, public `copper_mcp.tools.preview_route` service plus equality check against the shared route-preview result. Every request starts from one private copy with 14 footprints, 35 pads, 11 nets, and zero segments/vias/zones. Eight selected nets are previewed independently; candidates are never merged or applied. |
| Route metrics | 8/8 previews routed, with four two-pad and four multi-pad candidates; all recorded zero vias and zero internal violations. Wire lengths (mm) are `12`, `12`, `37.25`, `34.75`, `45.5`, `48`, `55.5`, and `50.75` for `L_IN`, `R_IN`, `L_SUM`, `R_SUM`, `L_OUT`, `R_OUT`, `VPOS`, and `VNEG`. The four multi-pad candidates use two or three paths. Repeated service outputs and candidate identities are pinned by regression. |
| KiCad authority | Explicit local KiCad CLI `10.0.5` executed JSON DRC on the source private copy and eight independently serialized single-net derivatives. Source: 14 violations, 24 unconnected items. Derivatives: 14 violations and 23/23/22/22/23/23/21/21 unconnected items in the same route order. Thus each derivative reduces its selected net's disconnect count without adding a violation; all remain non-clean. |
| Interpretation | This is public-service deterministic candidate-preview evidence and an authoritative KiCad DRC observation of independent disposable derivatives. It is not combined-net routing, a clean board DRC, electrical/ERC verification, component-value selection, fabrication/hardware evidence, performance comparison, or FreeRouting parity. |

#### B-075 — held-out audio evidence-source provenance correction

| Field | Recorded evidence |
|---|---|
| Correction | Supersedes only B-066's clean-evidence source reference. B-066 remains immutable history. PR #51 was squash-merged, so its previously recorded source `96110ee9c3dbf9769070abaaffefb85a4018a4cc` is not an ancestor of the merged-main lineage and cannot be guaranteed available to a shallow CI checkout. |
| Rebound source | `999d64d3bc930f3f5a3fcbed0725bf7f203e6337`, the merged-main squash commit, is locally reachable from the integration head and contains byte-identical bound script, fixture, licence, provenance, and split inputs. |
| Artifact | [`2026-08-05-audio-project-family-v1.json`](../../benchmarks/results/heldout/2026-08-05-audio-project-family-v1.json); regenerated evidence run `sha256:f8c5be968f5df360fb975c4959a20fe0663621dd2b9cd2e3ba0acedf37795c40`; artifact run `sha256:839013f27b62c2ae4eeab2212b24aab675876d955863c6caf1a9cb72448d68e2`. |
| Metric preservation | The regenerated three-replay signature remains `29b28fbab5baa7f8df04a958d21c90dbcd0ac53b1344f18d69156da5e7304bdd`: 6/6 routed nets, `205,000,000 nm` total wire length, 8 legal placement candidates after 96 evaluations, and zero internal routing violations. |
| Replay guard | The detached clean-worktree regression now proves, using only local Git ancestry, that the artifact source is an ancestor of the checkout before cloning or checkout. It does not fetch, skip, xfail, or relax the clean-source byte replay. |
| Interpretation | This is a provenance and CI-availability correction only. It adds no fixture, capability, quality, KiCad, network, model, electrical, fabrication, or hardware claim. |

#### B-078 — opt-in route-aware placement selection replay

| Field | Recorded evidence |
|---|---|
| Date and fixture | 2026-08-05; CopperMCP-original Apache-2.0 `benchmarks/audio/fixtures/ne5532-stereo-summing-routing-v1.kicad_pcb`, source SHA-256 `749adc8b4d26b7f7ef878f9cf681521a8efdf446b9f0bf559243918e6e1957a9`. |
| Configuration | `scripts/benchmark_route_aware_placement.py`; 128 legalizer evaluations, four rounds, beam width four, 5,000,000 nm placement step; one independent existing-A* probe per candidate at a 1,000,000 nm grid with 10,000 expansions, shared operation cap 128. Candidate identity and exact snapshot/view bindings are verified before the private projection. Default `same-net-manhattan-v1` and opt-in `route-aware-astar-v1` search the same bounded legal candidate space. |
| Predeclared criterion | Three deterministic replays; every retained candidate must remain legal, and route-aware selection must either improve the independent bounded-A* wire length by at least 10% or strictly reduce unrouted probes versus the default selection. |
| Metrics | All retained candidates were legal; both choices completed one probe with zero unrouted probes. The route-aware solve consumed 116 of its 128 operation-wide probe budget. Default selection: 42,000,000 nm. Route-aware selection: 32,000,000 nm. Improvement: 23.80952380952381%; all three replay signatures matched. Run ID: `sha256:7ee7700748c25c759c9104e02072bcd007b7eb4bd71836ae0c1da48c287f3219`. |
| Artifact | [`2026-08-05-route-aware-placement-v1.json`](../../benchmarks/results/placement/2026-08-05-route-aware-placement-v1.json), [`benchmark_route_aware_placement.py`](../../scripts/benchmark_route_aware_placement.py), and [`test_route_aware_placement.py`](../../tests/test_route_aware_placement.py). |
| Interpretation | Accepted for this opt-in ranking policy only. Independent probes are not a combined-net route, negotiated congestion/overflow result, physical-clearance proof, KiCad DRC, external-router comparison, electrical/fabrication result, optimal-placement result, board mutation, or apply authority. |

#### B-081 — route-aware placement reachable-source correction

| Field | Recorded evidence |
|---|---|
| Correction | Supersedes only B-078's artifact-source provenance. B-078 remains immutable history. Its previous source object was not reachable from this branch after the one-commit transplant. |
| Rebound source | `74647797582d0d8713c063afc146daf5d55e6163`, the DCO-signed projection-binding remediation commit, is an ancestor of this correction and contains the exact route-aware code and direct helper regressions used for the replay. |
| Artifact | Regenerated [`2026-08-05-route-aware-placement-v1.json`](../../benchmarks/results/placement/2026-08-05-route-aware-placement-v1.json), run ID `sha256:a304b1e428b37d91f1d29f62f97460aac9569d6f9a12160953fc01a55de71fb0`; the artifact records the reachable source commit above. |
| Metric preservation | Three deterministic replays remained legal and selected the same candidates: 42,000,000 nm baseline versus 32,000,000 nm route-aware wire length (23.80952380952381% improvement), zero unrouted probes, and 116 of 128 operation-wide probes used. |
| Replay guard | The artifact self-digest and local Git ancestry are checked before this correction is committed; replay uses the source tree at the recorded, reachable commit. |
| Interpretation | This is a provenance correction only. It does not alter placement/routing behavior, fixture inputs, candidate identity, legality, DRC, congestion, external-router, electrical/fabrication, mutation, or apply claims. |

#### B-082 — route-aware placement claim correction, 2026-08-06

| Field | Recorded evidence |
|---|---|
| Correction | Supersedes only the *interpretation* recorded in B-078 and repeated in B-081. B-078 and B-081 remain immutable history; their fixture, configuration, and numbers are unchanged and were independently reproduced here. What was wrong was the sentence "Default `same-net-manhattan-v1` and opt-in `route-aware-astar-v1` search the same bounded legal candidate space." They do not. |
| Cause | The score feeds `solver._state_key`, which orders the beam, so the surviving beam decides which successors are generated in the next round. The scoring policy changes which candidates are ever explored, not merely how a fixed set is ordered. Measured on the same fixture and settings: at `max_ranked=64` the two retained sets intersect in **1** candidate; at the committed `max_ranked=8` they intersect in **0** — entirely disjoint. B-078's 23.80952380952381% is therefore a **different-search-trajectory** result, not a re-ranking result. |
| Corrected claim | Two different bounded searches over one intent, fixture, and set of work ceilings, each ordering its own beam by its own score, **at one A\* probe per candidate**. The baseline search selected a candidate measuring 42,000,000 nm and the route-aware search one measuring 32,000,000 nm on that single probe: 23.80952380952381%. |
| Added measurement — true re-ranking | One fixed candidate set — the 16-candidate union of what both searches retained — scored under both policies with the argmin of each compared. It independently reproduces the same two candidate choices and the same 42,000,000 → 32,000,000 nm, 23.80952380952381%. The union is itself drawn from the two searches, so this is a shared-set comparison, not a search-independent one. |
| Added measurement — all probeable nets | The ranked search probes 1 net per candidate while the fixture has **11** probeable nets, so B-078's "zero unrouted probes" was a one-net statement. Probed against all 11, both chosen candidates complete 7 and leave **4 unrouted — all four `off_grid` refusals**, where the pad-centre delta is not divisible by the 1,000,000 nm probe grid. The ordering **reverses**: baseline choice 359,000,000 nm, route-aware choice 391,000,000 nm. The one-probe signal that steered the search does not survive the broader question. |
| Artifact | New [`2026-08-06-route-aware-placement-v2.json`](../../benchmarks/results/placement/2026-08-06-route-aware-placement-v2.json), run ID `sha256:fecb29c10145ee7cce807a5034ac7d2da176a3963aec6c074f0763ab45af74fd`, source commit `ebe842984ed924df71c28b21531340934ae93731` — the commit carrying the corrected benchmark and its regressions, and a strict ancestor of this entry, following B-081's reachable-source rule. The v1 artifact is not regenerated; the overstated evidence stays auditable beside its correction. The v2 `run_id` binds the estimator id and the full probe/A\* configuration digests, so a one-probe report and an eleven-probe report can no longer share an identity. |
| Criterion | Unchanged and still met on the search comparison: ≥10% improvement or strictly fewer unrouted probes, over three deterministic replays with every retained candidate legal. A failed criterion is now recorded with `criterion.passed` false and a non-zero exit rather than raised, which is what ADR-0067 promised. |
| Interpretation | This corrects a claim, not an architecture. A route-aware score steering the search is the intended behavior; describing that as a re-ranking result was not. Adds no fixture, capability, DRC, congestion, external-router, electrical, fabrication, mutation, or apply claim, and no whole-board routing claim in either direction. |

#### B-076 — recorded-link target corrections for B-036 and B-057

| Field | Recorded evidence |
|---|---|
| Correction | Two Markdown link *targets* recorded in this ledger do not resolve, and this entry records where each was meant to point. B-036's `Dataset` link target `../../audio/fixtures/negotiated-crossing-v1.kicad_pcb` omits the `benchmarks/` path segment; the fixture it names is at [`benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb`](../../benchmarks/audio/fixtures/negotiated-crossing-v1.kicad_pcb). B-057's `Correction` link target `HANDOFF-CODEX.md` resolves to `docs/ledgers/HANDOFF-CODEX.md`, a path that has never existed here; the document it names is the user-owned untracked `docs/HANDOFF-CODEX.md`, which is deliberately not in the repository and therefore has no correct in-repository target. |
| Scope | B-036 and B-057 are unchanged, byte for byte. Nothing in this ledger's history was rewritten: this is a new dated entry recorded on 2026-08-06 under issue #70 (repository professionalization), which is the only way a correction is recorded here. No run ID, digest, fixture name, metric, source commit, interpretation, or displayed text changes anywhere. |
| Checker exemption | `scripts/check_doc_links.py` carries these two targets as its only exemptions, each keyed to the exact document and target string and each naming this entry. The exemption list is closed: a target is exempt only while it appears there, an exemption that stops matching a real link fails the check, and no new broken link can be added without editing the checker. |
| Interpretation | This is a documentation-navigability record with no measurement, capability, KiCad, electrical, fabrication, or hardware claim. It exists so a reader who follows a stale target learns where it pointed, without the ledger's append-only history being edited to hide that it was stale. |

#### B-076 — ordered-layer internal proposal oracle

| Field | Recorded evidence |
|---|---|
| Dataset | Deterministic synthetic 5x5 lattice and Board IR fixtures: both two available layers contain a full crossing wall, while the three-layer fixture has a clear inner signal layer. No external or proprietary board data. |
| Protocol | Run the same bounded request under a two-layer stack and a three-layer stack; replay the latter twice; verify the emitted full-stack spans and reject invalid span, 9-layer, and zero-via-budget variants. |
| Metrics | Two-layer result: `no_path`; three-layer result: complete with two vias through the inner layer and deterministic replay equality. |
| Interpretation | This measures the internal candidate-only generalized-stack seam. It does not establish generalized KiCad serialization, Board-IR round-trip of rendered bytes, zone refill, authoritative DRC, MCP exposure, jobs, apply, fabrication, or whole-board routing. |

#### B-077 — ordered-layer boundary and via-cap compatibility regression

| Field | Recorded evidence |
|---|---|
| Dataset | Deterministic synthetic two-layer alternating-transition lattice, structurally restamped 65/66-via candidates, and converted public two-pad snapshots with one injected internal signal layer. No external or proprietary board data. |
| Protocol | Route a 65-transition two-layer lattice with omitted policy and with an explicit 65-via policy; attempt the 66-transition case with that explicit policy; run a 65-transition three-layer lattice with its clear third layer blocked; verify restamped 65/66-via candidates; call file preview, live preview, and durable-job preparation with a three-layer snapshot. |
| Metrics | Omitted two-layer route: complete with 65 vias. Explicit 65-via route: complete with 65 vias; 66-via route: `no_path`. Omitted three-layer route: `no_path` at its effective 64-via cap. Restamped 65-via candidate: verified; restamped 66-via candidate under explicit 65-via policy: `budget_exceeded`; omitted two-layer 66-via candidate: verified. File/live/durable entry points: all refuse the three-layer snapshot before router publication. |
| Interpretation | This is a compatibility and boundary regression, not generalized KiCad routing evidence. It confirms only that the internal 3..8-layer seam cannot leak through existing public/live/durable two-layer surfaces and that verifier and constructor policy agree. Serialization, DRC, refill, apply, fabrication, and whole-board routing remain unproven. |

#### B-078 — capped ordered-layer exact `(node, vias)` differential

| Field | Recorded evidence |
|---|---|
| Correction | Supersedes the unrecorded multilayer differential claim made alongside B-076. The committed `scripts/benchmark_layered_astar.py` had a two-layer, uncapped `_dijkstra_cost` and four two-layer uncapped cases, so it never reached the capped search or a multilayer expansion. B-076 and B-077 remain immutable history and are unaffected; only the differential claim is replaced by this recorded run. |
| Dataset | 20,000 seeded synthetic lattices (`seed 20260805`) of 2..5 ordered layers on 3x3..6x6 integer grids, with full-height per-layer walls, layer-scoped rectangular track keepouts, layer-scoped via keepouts, and omitted or explicit via caps of 0/1/2/3/6. Plus the unchanged four fixed 5x5 two-layer cases and a three-case via-policy boundary. No external or proprietary board data. |
| Protocol | Route each lattice with the bounded search; compare its cost against an exact `(x, y, layer, vias_used)` uniform-cost Dijkstra written without a heuristic, without Pareto pruning, and enumerating the augmented state space directly; replay each returned path through an independent legality checker for bounds, obstacles, orthogonal unit moves, coincident via transitions, the via keepout, the cap, and cost/via accounting; replay each lattice twice for determinism. Separately observe the routed/refused boundary either side of the declared generalized via cap on a 64- and 65-transition corridor, and a 65-transition two-layer corridor with omitted policy. |
| Metrics | 20,000/20,000 differential matches, 0 mismatches, 0 illegal paths, 40,000 deterministic replays with no divergence. Layer coverage 5,074 / 5,039 / 4,948 / 4,939 for 2 / 3 / 4 / 5 layers; 19,190 lattices under a finite effective cap; 13,032 routed; deepest observed chain 4 transitions. Outcome signature `sha256:9c2543ee8bcc9981e8ca6ed9f3e65cf6a226558e0738757f6eb9937c58a2d0cc`. Policy boundary: omitted 3-layer cap routes exactly 64 transitions and refuses the 65th with `no_path`; omitted 2-layer policy stays unbounded at 65 transitions. |
| Artifact | [`2026-08-06-layered-astar-capped-multilayer.json`](../../benchmarks/results/routing/2026-08-06-layered-astar-capped-multilayer.json); run `sha256:65291e67b0a20a219b129d0f13126bbc826ecab2629e9cc59a6f03ff8dbb5b02`; bound script `sha256:d5b8d70af633120f653d5d2544752676198854112db81ca6aa2f4eeabe7ab30b` at source `00502fa6d8cf4fc0ef322c93051823a08e673231`. |
| Mutation sensitivity | Observed on this suite: an off-by-one via cap, a coordinate-only search state, transitions restricted to adjacent layers, an ignored via keepout, and a changed declared default cap are each detected. Not detected: removing the via-awareness of the per-coordinate Pareto front, which is an additional prune above the sound `(node, vias)` dominance key. |
| Interpretation | This measures the internal candidate-only search kernel: cost exactness under a finite via budget, path legality, and deterministic replay over 2..5 layers. It is not evidence for six through eight layer stacks, Board IR mapping, trace width or clearance, via annulus/drill/net-class rules, KiCad serialization, authoritative DRC, MCP exposure, jobs, apply, fabrication, or whole-board routing. |

#### B-079 — bounded composed route-bundle KiCad replay

| Field | Recorded evidence |
|---|---|
| Artifact | [`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json) |
| Dataset | CopperMCP-original Apache-2.0 `negotiated-crossing-v1.kicad_pcb`, SHA-256 `dbbfc5179cca7f644b90303ff3bc695f191ba94f7e5bbc8b4b1437d810ec83c7`. |
| Metrics | Independent same-base candidates overflow one lattice unit; the two-net route bundle replayed identically, completed with zero overflow and three exact physical pair checks, at 26 mm total length. |
| KiCad authority | The project bounded DRC adapter resolved and SHA-256-bound KiCad 10.0.5 before launching its fixed-argument, private-environment execution. It capped report, stdout, and stderr bytes before strict UTF-8/duplicate-key/structure parsing and checked report/exit consistency. The private combined derivative completed with exit `0`, zero errors, and zero unconnected items; source bytes/inode/mtime remained unchanged. |
| Limits | One two-pin/one-layer/common-grid fixture only. No apply/export/persistence authority, multilayer/via/zone capacity, electrical or fabrication claim, or general-board scaling result. |

#### B-080 — route-bundle boundary and DRC-binding correction

| Field | Recorded evidence |
|---|---|
| Correction | The route-bundle request boundary now rejects a non-list or fewer-than-two/more-than-eight reference collection before iterating or validating an element; an explosive nine-item list regression proves the upper-bound refusal does no caller-controlled element work. |
| KiCad authority binding | The regenerated [`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json) retains `DrcSummary.base_revision` `sha256:efbab994177e0737b4ed1ae7343631b969e5ab7c51e888dfe90311c362209f4d` and `drc_context_revision` `sha256:1c8dee9d3b4248b891b38ff23e49cc5e1bf2f296db22e216d9f3f38591c0e2ee` for the private combined derivative. The benchmark recomputes the derivative bytes and refuses a mismatched summary before reporting completed evidence. |
| Interpretation | This corrects request-work and evidence-binding boundaries only. The artifact remains aggregate evidence for one disposable public-fixture derivative; it grants no apply/export/persistence authority and makes no general-board, electrical, or fabrication claim. |

#### B-082 — route-bundle released-version provenance correction

| Field | Recorded evidence |
|---|---|
| Correction | The earlier route-bundle artifact predated the released `0.5.0` source lineage. Release commit `a12ee823fef5c099aa80c16bf5a694806eb643e2` was merged before source commit `f1fcfafa67843c9c7b27a0caa5676aaeeccd91cf`; the latter adds a report field and exact regression requiring `copper_mcp_version: "0.5.0"`. |
| Artifact | Regenerated [`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json) from that exact source tree. The bounded local KiCad 10.0.5 DRC record reports exit `0`, zero errors, zero unconnected items, combined-derivative SHA-256 `2617ea1a91e6eabd8f7de6d124672ee05478761843308a2d365a30f1a5dedc78`, and DRC-context SHA-256 `61efa0a7cf5845a8f49fe13eee46fe20d00b50f208a7c37c9d2ec4e9ffacfab4`. |
| Replay guard | The evidence commit contains only this artifact, this append-only record, and the changelog correction. The source/test tree is byte-identical to source commit `f1fcfaf` and that commit is an ancestor of the evidence commit. |
| Interpretation | This is a released-version and reachable-provenance correction. It preserves B-079/B-080's routing and bounded-DRC claims, and adds no authority, quality, electrical, fabrication, or general-board claim. |

#### B-083 — route-bundle policy-bound identity regeneration

| Field | Recorded evidence |
|---|---|
| Correction | `bundle_id` now binds the coordinator's `policy_digest` (its iteration ceiling and penalty/budget envelope) alongside the ordered references, candidate IDs, settings, work evidence, and Board IR revision. Two bundles composed from identical references under different coordinator policy can no longer share one identity. Because the identity feeds the private combined serializer's element identifiers, the derivative bytes and every recorded revision change with it. |
| Artifact | Regenerated [`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json). The bundle identity moves from `sha256:5eda7cfc4646373d4dd8a843ca95eadf210e0808e09510260454a914ec75fdb9` to `sha256:6b54fa782ec45fe3ac919736799fc6dd51506f71b78acc7db15fe2a57f59bd75`. |
| KiCad authority | The same bounded adapter resolved and SHA-256-bound KiCad 10.0.5 (`sha256:852c180a8c923beb6173b54bd6cc0bd66714e52ebfdd451ef0e061224bc954f5`) and ran its fixed-argument, private-environment, `DEVNULL`-connected child. The regenerated private combined derivative `sha256:678c9fc7a67217530d5ca112d181c7efc45b50ed237f7ce0e81d00dd3a6d11bb` with DRC context `sha256:0cc3a5ce94e32cb4485a49e543ab254ef33183ae2d83ff44c681f77e07682528` reported exit `0`, zero errors, zero warnings, zero exclusions, and zero unconnected items. Source bytes, inode, and mtime were unchanged. |
| Metric preservation | Every measured quantity is unchanged: one overflow unit for independent same-base candidates, zero overflow for the bundle, two candidates, one core replay, three exact physical pair checks, and 26 mm total candidate length. |
| Interpretation | This is an identity-binding and provenance regeneration only. It adds no fixture, capability, routing-quality, electrical, fabrication, apply, or general-board claim, and B-079's and B-080's claims are preserved. |

#### B-085 — route-bundle 0.6.0 generator-version regeneration

| Field | Recorded evidence |
|---|---|
| Correction | CopperMCP writes `(generator_version "<package version>")` into every board it renders, so the private combined derivative's bytes — and therefore `base_revision` and `drc_context_revision` — are reproducible only by the exact package version that recorded them. Bumping the source version to `0.6.0` made `tests/test_route_bundle_benchmark.py` fail against its own committed artifact. This is a property of the release process, not a routing or DRC change, and it is now a named step in [the release process](../releasing.md). |
| Artifact | Regenerated [`2026-08-05-route-bundle-v1.json`](../../benchmarks/results/routing/2026-08-05-route-bundle-v1.json) under CopperMCP `0.6.0`; run `sha256:f050fcfc25a7f2ad904088ef3264303fb6970f88cd27c80dc0408c3f51ce5d75`, bound script `sha256:cadb5e760d23d6d742f1f812d67b35fd5e645cba5bb946877ce7bbd5e448b402` (unchanged). |
| KiCad authority | The same bounded adapter resolved and SHA-256-bound KiCad 10.0.5 (`sha256:852c180a8c923beb6173b54bd6cc0bd66714e52ebfdd451ef0e061224bc954f5`) and ran its fixed-argument, private-environment, `DEVNULL`-connected child. The regenerated private combined derivative `sha256:a8189214c224cbbe565b750ff91f141b35156517fc29c38011e4e45056e5772c` with DRC context `sha256:8881a9ef8f45019bd8e2df67892ae181c06ddd45969891f41679f3e1f891d56e` reported exit `0`, zero errors, zero warnings, zero exclusions, and zero unconnected items. Source bytes, inode, and mtime were unchanged. B-083's `sha256:678c9fc7…` / `sha256:0cc3a5ce…` pair remains the immutable `0.5.0` record. |
| Metric preservation | Every measured quantity is unchanged from B-083, including the bundle identity `sha256:6b54fa782ec45fe3ac919736799fc6dd51506f71b78acc7db15fe2a57f59bd75`: one overflow unit for independent same-base candidates, zero overflow for the bundle, two candidates, one core replay, three exact physical pair checks, and 26 mm total candidate length. Only the version-bound provenance fields and the recording timestamp moved. |
| Interpretation | This is a version-provenance regeneration only. It adds no fixture, capability, routing-quality, electrical, fabrication, apply, or general-board claim, and B-079's, B-080's, and B-083's claims are preserved. It is also not a claim that the earlier revisions were wrong: they are correct for the version that produced them. |

#### B-084 — recorded benchmark-ledger double allocations

| Field | Recorded evidence |
|---|---|
| Correction | Three numbers each name two unrelated entries in this ledger. `B-076` names the "recorded-link target corrections for B-036 and B-057" sub-entry (a documentation-navigability record from issue #70) and the "ordered-layer internal proposal oracle" measurement ([ADR-0068](../adr/0068-bounded-ordered-layer-routing.md)). `B-078` names the "opt-in route-aware placement selection replay" ([ADR-0067](../adr/0067-route-aware-placement-ranking.md)) and the "capped ordered-layer exact `(node, vias)` differential". `B-082` names the "route-aware placement claim correction" and the "route-bundle released-version provenance correction". In each case two branches allocated the number in parallel; because this ledger is organized by topic, the colliding sub-entries landed in different sections and Git merged both without a conflict. |
| Scope | No entry is changed, byte for byte, and nothing is renumbered. Renumbering would rewrite append-only history and break external citations that already exist: `scripts/check_doc_links.py` names "B-076" in both of its link exemptions (meaning the first B-076), D-137 and D-140 cite `B-076` in their records (the second), B-078 supersedes "the unrecorded multilayer differential claim made alongside B-076" (also the second), and D-140's correction cites "B-078/B-081" (meaning the route-aware B-078). Read an unqualified `B-076`, `B-078`, or `B-082` as ambiguous and cite the sub-entry title alongside it. |
| Detection | Recorded on 2026-08-06 under issues #82 and #83. `scripts/check_ledgers.py` now parses this ledger's `B-` headings and rejects a repeated number. These three collisions are carried in its closed `RECORDED_COLLISIONS` list, keyed to this entry, so each is reported on every run; the nine legitimate replay sub-entries are carried in a separate closed list and must still be `####` headings beneath the `###` entry they replay. A new duplicate fails the build. |
| Interpretation | This is a bookkeeping record with no measurement, run, artifact, capability, KiCad, electrical, fabrication, or hardware claim. It exists so that a reader who follows one of these citations learns that the number is ambiguous, without the ledger's history being edited to hide that it was. |

## External-corpus baselines

These run the existing routers over third-party boards imported through the benchmark-only
SimpleRouteJson seam. They are the first CopperMCP measurements taken on data this project did not
author, and their refusal breakdowns are the result rather than an error path.

### B-088 — SimpleRouteJson external-corpus routing and refusal breakdown

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:feb4924accf3a657780957623f87a995caa04ee8af823d4f4da526319ff3e12a` |
| Date and commit | 2026-08-06 UTC; source commit `40889bdef6b4370a15e1cbea8fe19ed8c8478ca7`, whose tree carries the whole change and to which this evidence commit adds only this artifact and this record; bound runner `sha256:8fb5d05fb60a75b66e4720b3aa3ba9e0b28dbd8c3377ac159a239adbc4795fed`; bound adapter `sha256:fb64b59f5727d792de30d82b1e9c0b7eab606d071569a29c8c8bc3ff8db5ec66`. Both digests are recorded inside the artifact's own `configuration`, so a reader never has to guess which revision produced a number. |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; KiCad was not invoked; FreeRouting was not installed |
| Dataset | 20 of the 36 SimpleRouteJson boards in [dwiel/tscircuit-benchmark](https://github.com/dwiel/tscircuit-benchmark) at commit `be36518b5bf51755dae92c230061ab3cf4e3e063`, MIT (© 2026 Zach Dwiel), redistributed with the upstream licence and an attribution file under [`benchmarks/corpora/tscircuit-benchmark/`](../../benchmarks/corpora/tscircuit-benchmark/). The subset is the first 20 filenames in upstream lexical order, a rule fixed before the run; SHA-256 is recorded for all 36 and every committed file is verified against it before routing. The boards are **LLM-generated from plain-English specs and were routed with FreeRouting as part of their construction**, so they are neither human-designed hardware nor a neutral yardstick for FreeRouting. |
| Configuration | `simple-route-json-import-v1` adapter; clearance 200,000 nm, via 600,000/300,000 nm, track width from each board's `minTraceWidth` (100,000 nm throughout); seed 0; per-request ceilings 250,000 grid nodes, 100,000 expansions, 2,048 obstacles, 2,000,000 obstacle checks. Two grid policies, both run over the whole corpus: `fixed` at a 250,000 nm lattice step, and `divisor-aligned`, which gives a two-pin net the largest ladder step dividing the greatest common divisor of its pad-centre delta — decided from geometry before routing, never by retrying. |
| Import | 20 of 20 boards imported; zero import refusals. 591 obstacles (487 `rect`, 104 `oval`), 449 pads, 142 keepouts, 126 nets, 117 of them with routing work. 210 obstacles were over-approximated and 114 were widened to the whole stack because they named an `inner1`/`inner2` layer a 2-layer board does not declare. The largest outward edge movement on any board was **1 nm**, and 3 of 20 boards needed none at all. |
| Metrics | **70 of 117 nets routed (59.83%) under both grid policies.** Refusals, `fixed`: 36 `off_grid`, 11 `no_path`. Refusals, `divisor-aligned`: 21 `grid_budget_exceeded`, 15 `off_grid`, 11 `no_path`. A further 9 nets carried a single pad and so stated no routing work; they are counted separately and excluded from the denominator. Routed wire length 1,676,500,000 nm against a provable pad-gap lower bound of 1,431,522,993 nm, a ratio of **1.1711**. 0 vias (single-layer routing only), 235 bends. Mean wall time 3.40 s (`fixed`) and 3.44 s (`divisor-aligned`) per whole-corpus pass; three passes per policy replayed byte-identically. |
| Negative result | **Every two-pin net in the corpus refused, under both policies.** All 70 routed nets are multi-pin, which is why `routed_two_pin_centre_manhattan_nm` is 0. The reference A* router's two-pin path additionally requires the pad-centre delta to divide by the lattice step; external pad coordinates do not oblige. Choosing a finer, divisor-respecting step converts those `off_grid` refusals into `grid_budget_exceeded` ones and routes **no additional net**, which localises the constraint to the lattice-node budget rather than to grid alignment. Reporting one policy alone would have hidden that. |
| Baseline comparison | **`not_run`.** FreeRouting is GPL-3.0, is not installed in the recording environment, and this repository has no SimpleRouteJson-to-DSN bridge. The artifact records `status: not_run` with a reason for every known baseline. No FreeRouting number is estimated, inferred, or carried over, so the cross-router comparison issue #65 asks for **remains unmeasured**. |
| Artifact | [`2026-08-06-simple-route-json-corpus-v1.json`](../../benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json), validated against its own self-digest by `scripts/check_ledgers.py`. Wall time is part of the report and therefore part of `run_id`, following every other artifact here, which makes the identity machine-bound; the machine-independent claim is `metrics`, and `tests/test_benchmark_simple_route_json_corpus.py` replays exactly that against a fresh run. |
| Mutation sensitivity | The over-approximation invariant is mutation-checked: `tests/test_simple_route_json_import.py` asserts that every mapped obstacle rectangle contains its exact decimal source rectangle, and a second test replaces the low-edge floor with a ceil and asserts the first test's property now fails. Without that pair, the containment assertion could pass merely because the fixtures were exact at nanometre resolution. |
| Interpretation | This measures the existing single-layer candidate-only router against imported external geometry. It is not a cross-router comparison, not a whole-board completion result (each net is routed independently against the unrouted snapshot, so the candidates are not mutually compatible), and not a KiCad DRC, electrical, thermal, signal-integrity, or fabrication claim. Nothing was applied, exported, or written. It does not generalise beyond LLM-generated 2-layer tscircuit boards, and the committed 20-board prefix is the easier half of an already-narrow corpus. The 1.1711 ratio is against a loose provable lower bound that ignores every obstacle and bend; it is not an optimality claim. |

### B-089 — KiCad 10.0.5 courtyard oracle parity and the cached-courtyard inset

| Field | Recorded evidence |
|---|---|
| Run ID | `sha256:a1c1f09b397ab69633e4ad7424d53760c684dbe4beef02a9ad3b2acb495d733a` |
| Date and commit | 2026-08-06 UTC; source commit `892bcac03541c921eac062b966cf78449391f946`, the branch point; the tree carrying the measured code is the pull request that lands this record. |
| Environment | Apple arm64 CPU; macOS 26.5.2; Python 3.12.13; **KiCad 10.0.5 invoked for real** (`kicad_invoked: true`), each run in isolated config/home/cache/temp directories. This replaces the `kicad_invoked: false` posture of B-041's `benchmark_courtyard_legality.py`, which measured the legalizer against itself. |
| Dataset | 15 cases: 11 CopperMCP-original synthetic boards at exact nanometre penetrations between two 10 mm front courtyard squares (−1 mm, −1, 0, 1, 5,000, 9,998, 9,999, 10,000, 10,001, 20,000, 1 mm), plus four committed boards — [`courtyard-donut.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/courtyard-donut.kicad_pcb), [`courtyard-inset-below.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/courtyard-inset-below.kicad_pcb), [`courtyard-inset-at.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/courtyard-inset-at.kicad_pcb), and the CopperTone buffer board. All fixtures are CopperMCP-original; none is copied from any footprint library. |
| Configuration | `kicad-cli pcb drc --format json --units mm --severity-all`, zero-clearance courtyard default, no custom design rules; the legalizer at `COURTYARD_CACHE_INSET_NM = 5,000` and `COURTYARD_COLLISION_THRESHOLD_NM = 10,000`. KiCad's verdict is read as the presence of a `courtyards_overlap` violation specifically, not as an error count, because the claim under test is about that one provider. |
| Metrics | **10/15 exact parity, 5/15 conceded `inconclusive`, 0 contradictions, 0 false-positive violations, 0 false-negative clears** (`non_contradiction_rate` 15/15). The five conceded cases are exactly the sub-threshold band (1, 5,000, 9,998, 9,999 nm, and the `inset_below` fixture at 9,999 nm), where KiCad reports clear and the raw geometry overlaps. The donut fixture and the CopperTone board are both `proven_clear` in agreement with KiCad; `inset_at` is `violated` in agreement with KiCad. |
| Negative result | **The exact-parity rate is 10/15, not 15/15, and that gap is the point.** It is reported as a separate `conceded_inconclusive` count rather than folded into an agreement percentage, because an `inconclusive` is a declared non-claim and counting it as agreement would manufacture a result. The script raises rather than emitting an artifact if any contradiction appears, so a passing artifact is itself evidence of zero contradictions. |
| Boundary result | KiCad's first collision is at **exactly 10,000 nm of nominal penetration, inclusive** — 9,999 nm is clear — matching `2 × maxError` from `FOOTPRINT::BuildCourtyardCaches`. Corner-only overlap was measured separately and shows the same threshold applying independently to each axis, with no degenerate band at 10,000 × 10,000 nm. |
| Artifact | [`2026-08-06-courtyard-oracle-parity.json`](../../benchmarks/results/placement/2026-08-06-courtyard-oracle-parity.json), validated against its own self-digest by `scripts/check_ledgers.py`. Every case's KiCad verdict, model verdict, and agreement class is recorded individually, so the aggregate can be re-derived from the artifact rather than trusted. |
| Mutation sensitivity | Seven mutations of the inset arithmetic and the ring handling were applied and every one was caught by `tests/test_placement.py -k Courtyard`: inset 5,000→4,999 (5 failures), 5,000→5,001 (5), 5,000→0 (8), threshold `2 × inset`→`1 × inset` (1), the witness bound `>=`→`>` (3), the two-axis witness `and`→`or` (7), and reverting the even-odd crossing pool to per-ring solids — the original issue #74 defect — (4). No mutation survived. |
| Interpretation | This measures one DRC provider on the orthogonal courtyard subset. It is not a full-board DRC, placement-apply, electrical, or fabrication claim, and nothing was applied or written. It does not cover nonzero or negative custom `courtyard_clearance`, arcs or non-orthogonal geometry, same-footprint rings that touch or properly intersect, or the tiny-shape band where a courtyard is thinner than the 10,000 nm threshold — all of which the artifact lists under `not_claimed`. The 11 synthetic cases are two rectangles each; they do not exercise concave chains. |
