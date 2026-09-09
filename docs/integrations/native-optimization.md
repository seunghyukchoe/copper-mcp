# Native supervised optimization

This is the implemented native workflow of the [v0.13 plan](../plans/v0.13-supervised-optimization.md),
not the completed general hybrid-router release. The package version remains 0.12.0 pending release.

## Start and observe

Run the installed server over stdio with `COPPER_MCP_WORKSPACE` pointing at the board/project
directory. The implementation should come from the integration environment, not from a shared
editable environment pointing at an older checkout. Python 3.12 is canonical; 3.11 and 3.13 remain
supported. All five tools take one closed `request` argument.

Before starting, observe the board through the existing inspection/scene tools using the same
clearance, width and via constraints. Copy the returned board revision, snapshot digest, target
net references, and any explicitly movable footprint references into the launch request.

`start_optimization` accepts:

| Field | Meaning |
|---|---|
| `schema_version` | Set `optimization/v2` for measured comparison and project input. Omit it for unchanged v1 behavior. |
| `input_mode` | V2 defaults to `observed-snapshot`. Explicit `native-full-board` uses the pinned native importer and derives the working snapshot and full eligible net scope. |
| `board` | Workspace board path ending in `.kicad_pcb`. |
| `expect_board_revision`, `expect_snapshot_digest` | The original file digest is always mandatory. The observed snapshot is mandatory in observed mode; native-full-board derives it and verifies it if one is supplied. |
| `constraints` | The existing integer `clearance_nm`, `track_width_nm`, `via_diameter_nm`, `via_drill_nm` object. |
| `target_net_refs` | Explicit distinct references in observed mode. Omit in native-full-board mode: every net with at least two pads is included, without filtering later failures or already-connected targets. |
| `movable_footprint_refs` | Optional explicit scope; the empty default means a verified identity placement. Locked footprints refuse. |
| `placement_intent_path` | Optional bounded JSON sidecar with only `rules` and `proposals`, using the existing placement language. It cannot override the board, scope, side or capabilities. |
| `electrical_intent_path` | Optional bounded, self-digesting Circuit Intent JSON snapshot. Supplying it makes ERC mandatory. It cannot be combined with v2 `project`. |
| `project` | V2 only: declared schematic `root_path`, digest-bound `files` (`path`, `digest`) and `libraries` (`name`, `path`, `digest`). Captured project ERC and separate candidate-bound PCB parity are mandatory. Unsupported projects refuse. |
| `required_domains` | V2 only: additional required judge domains. The caller cannot remove mandatory DRC/DFM or project-required ERC. Missing authorities block review. |
| `placement_grid_nm`, `routing_settings`, `seed` | Bounded existing native search parameters. Manhattan distance may guide the search, never final evidence. |
| `limits` | Cumulative runtime, candidate, placement, route-attempt, repair, expansion, obstacle-check and output ceilings. Server ceilings may tighten them. |
| `allowed_backends` | The implemented path is `internal-layered-v1`. External selections currently refuse until the production format bridge is integrated. The [local runtime setup](local-router-runtime.md) is separate. |

The response includes a job record. `get_optimization_job` takes its `job_id` and returns current
state and bounded judge reports. `cancel_optimization_job` additionally requires
`expected_record_revision`; it fences publication and invalidates issued resource access.
The operator identity is server-owned, not a caller-selected digest. Network transport execution
is refused until authenticated ownership is integrated.

## Native full-board input

Set `schema_version: optimization/v2` and `input_mode: native-full-board`, provide the original
`expect_board_revision` and ordinary constraints, and omit `target_net_refs`. Movable scope remains
explicit; this mode does not authorize moving every component. Native KiCad 10.0.5 upgrades two
confined copies. Existing IDs retain their semantic owners. Only native-generated, unique
Datasheet/Description field IDs receive deterministic replacements, and the complete normalized
outputs must agree. The original board and project are never upgraded in place.

The exported `native_import` binding distinguishes the original file from the imported working
board, and binds the executable, version, command and normalization/disposal policies. The child
worker and review path reproduce that same import from fresh originals. A generated same-stem
`.kicad_prl` is charged and discarded only inside the private copy; captured files, aliases,
links, oversized output and unknown side effects cannot be silently discarded. Local preferences
never become electrical or rule authority.

This is not automatic electrical completion: declared project checks still require the supplied,
bound symbol libraries and schematic/PCB parity. Required missing or inconclusive evidence blocks
review. Footprint-local board outlines and other unsupported geometry still refuse explicitly.

## Execution and evidence

An owned guardian keeps the process-group identity live while the worker executes. Its private
status pipe reports the worker's real exit code and is closed in the worker before exec. On
completion, the parent terminates the group before reaping the guardian, even if a descendant keeps
stdout open. The original deadline still gates delivery; a fixed cleanup watchdog prevents the
guardian from parking forever after parent loss. Unverified cleanup never counts as success.
Import and review freshness scans use only their remaining work allowance.

Native jobs use a fresh isolated Python process with source inventory checks and an empty private
bytecode-cache prefix. The parent bounds I/O, cancellation and the process group. SQLite retains
only typed redacted lifecycle/package metadata; private captures/results have separate byte and
retention bounds. A cancelled or fenced worker cannot publish a selected package.
Every package directory entry is inspected so symlinked modules cannot evade the source inventory.
The parent validates the complete closed response and the returned candidate bytes before review:
database metadata alone never enables confirmation or geometry disclosure. Both operations recheck
delivery readiness after the human prompt returns.

Each selected placement passes the existing legalizer and source-preserving serializer. Routing
sees the placed snapshot and composes replayed native derivatives. Already-connected targets are
counted without inventing copper. The ordered-layer serializer supports two through eight signal
layers with full-stack through-vias; other core geometry restrictions remain in force.

KiCad DRC runs twice on the complete composition and frozen rule/library context. The bounded DFM
profile reuses that DRC evidence; it does not establish general manufacturability. Captured Circuit
Intent ERC is a separate electrical-input check and does not prove schematic/PCB parity. Missing
physics authorities remain inconclusive. Suppressed checks, disagreement or missing required
authority prevent package selection.

Final ranking uses successful hard gates, target completion, coarse straight-track/via occupancy,
clearance headroom, vias, copper length, intent residual/displacement and a deterministic identity
tie-break. V1 retains its zero placeholder; it is not a measurement. V2 binds a signed clearance
measurement to the complete final snapshot, constraints, method and charged work. If any compared
candidate lacks the measurement, clearance is omitted from the entire comparison and disclosed as
unavailable. Neither value establishes native-rule or physics authority.

V2 retains unchanged placement before heuristic screening and freezes the candidate population
before routing. Each slot gets equal, nontransferable routing/evaluation budgets and seed schedules;
failed slots remain visible. A moved candidate is not an improvement over an unmeasured baseline.
Private v2 placement supports ordinary front/back orthogonal poses without side flips; unchanged
expressions remain intact. The older file-apply serializer's supported subset is not widened.

V2 enables five native-default suppressed checks as warnings in a bound private DRC profile,
without changing the source project or weakening stronger explicit severities. Any remaining
suppression still blocks review. See [the versioned workflow contract](../adr/0161-measured-optimization-is-an-end-to-end-versioned-workflow.md).

## Reproduce the native placement-and-routing control

From this checkout, configure `COPPER_MCP_TEST_PROJECT_ERC_CLI` to the reviewed KiCad executable
and use the canonical Python environment with the test dependencies installed:

```sh
PYTHONPATH=src python -m pytest --no-cov -n 0 \
  'tests/test_optimization_workflow_native.py::test_mcp_routes_complete_multilayer_tree_and_exports_without_apply[v2-placement-and-routing]'
```

The test sends an SDK MCP request through the real isolated worker, compares unchanged and moved
placement on an owned four-layer fixture, and requires a moved result with shorter copper, a
complete three-pad target connection, vias and zero hard KiCad DRC errors. It exports metadata,
keeps optional physics inconclusive, and verifies the source board's bytes, inode and timestamp
are unchanged. It neither applies nor saves, and creates no project file beside the v2 source.
This controlled test does not prove held-out quality, ordinary-project coverage or real-human
consent; the synthetic-CLI tests are not substitutes for this native execution.

The corresponding real-import control uses the same consumer and leaves snapshot/target selection
to production intake:

```sh
PYTHONPATH=src python -m pytest --no-cov -n 0 \
  'tests/test_optimization_workflow_native.py::test_mcp_routes_complete_multilayer_tree_and_exports_without_apply[v2-native-full-board-placement-and-routing]'
```

That control also checks the exported import binding. It uses the owned four-layer fixture;
production intake of selected older-format projects is separate evidence, not a claim that those
projects have been optimized or electrically judged.

The zoned controls exercise placement comparison, routing, fresh fill and export together:

```sh
PYTHONPATH=src python -m pytest --no-cov -n 0 \
  'tests/test_optimization_zoned_workflow.py::test_mcp_compares_and_checks_complete_fresh_zoned_candidates[cross-layer-tree-placement-and-fill-native]' \
  'tests/test_optimization_zoned_workflow.py::test_mcp_compares_and_checks_complete_fresh_zoned_candidates[placement-routing-and-fill-native]'
```

With the same explicitly configured native executable, both owned cases passed their original
120-second job limits. The four-layer case connects all three tree pads across layers beside a
ground pour, selects a shorter moved layout than identity, refreshes fill after routing and checks
the complete candidate. Both cases retain zero hard native DRC errors and unchanged source files.
This is a controlled product-path demonstration, not a held-out or physics result.

### Existing copper and placement repair

When `limits.max_repair_rounds` supplies at least one round to each candidate's equal allocation,
v2 may reset existing copper on declared target nets that are disconnected or attached to an
actually moved footprint. Locked copper, group members and unrelated nets are not removed.
Groups are protected at both board and footprint scope. An unchanged target is reset only when
disconnection is proved; unsupported or inconclusive connectivity is not permission to replace
its copper. Positive connection and positive disconnection use separately directed geometry bounds.
The unchanged-placement comparison has the same allowance. One batch counts as one repair round;
failed work remains charged. This is whole-affected-net repair, not a negotiated local optimizer.

The exported `repair` record and candidate `repair_digest` disclose and bind the private removal.
Fresh fill, complete routing and final KiCad checks still gate selection; a partial repair is never
exported as a candidate. The original file is neither edited nor saved. With the configured native
backend, the following owned controls passed under unchanged 120-second job limits:

```sh
PYTHONPATH=src python -m pytest --no-cov -n 0 \
  'tests/test_optimization_zoned_workflow.py::test_mcp_compares_and_checks_complete_fresh_zoned_candidates[existing-copper-placement-and-repair-native]' \
  'tests/test_optimization_zoned_workflow.py::test_mcp_compares_and_checks_complete_fresh_zoned_candidates[partial-copper-routing-repair-native]'
```

## Export and confirm

`export_optimization_package` requires `job_id`, `expected_record_revision` and
`expected_package_digest`. Metadata export carries no apply authority. Optional
`include_geometry: true` requests disclosure of the **complete candidate board including original
design content**; it requires a separate trusted-host consent. The returned resource lasts at most
five minutes, is byte/count bounded, and is revoked when the job is cancelled.

For an unsuccessful terminal v2 job, status instead provides `blocked_evaluation_digest`.
Pass that digest as `expected_package_digest`, together with the current record revision, to
export its `blocked-evaluation` report. The response is explicitly `status: blocked` and contains
only the retained terminal record and its fixed blocker codes. It is not a selected candidate or
a JudgeReport; missing detailed evidence is not reconstructed. This metadata remains available
after private candidate expiry or server restart. Geometry disclosure and review approval are
refused without prompting, and v1 jobs do not gain this export path.

`approve_optimization_job` requires the job, record revision, package digest and judge digest.
It requests human confirmation through the host, then atomically persists consent and final
completion. `completed` means the approved package workflow finished. It does not mean that the
board was applied, fabricated, or electrically signed off.

Confirmation/disclosure are default-off. Enable `COPPER_MCP_OPTIMIZATION_HOST_CONFIRMATION=1`
only after verifying that the connected host displays requests to a human and does not auto-answer
them. MCP elicitation support alone does not establish that property. Model-supplied approval
booleans or capabilities are not accepted as consent.

The existing `apply_candidate` and `apply_placement_candidate` operations remain separate,
default-off and independently token-authorized. Applying placement changes the source revision;
old route tokens cannot be reused. Re-observe and re-verify before obtaining fresh route authority.

## Remaining release gates

V1 zoned compositions remain refused. V2 candidate fill is connected to routing and final
connectivity/package checks, including native mixed-board and cross-layer tree controls. Foreign
pours use the shared conservative envelope/fresh-fill obstacle model. Selected-net zone attachment
remains unsupported in the tree path. Existing target copper can be reset by the bounded private
batch above; negotiated repair and wider source semantics remain open. These controls are not
general zoned-board acceptance. The pinned development audio/supply board-stage diagnostics reached
required checks but remained blocked by explicit project DRC suppressions; no suppression was
waived and neither diagnostic claimed project ERC, physics or placement improvement.
Production FreeRouting/SRJ
conversion and disposal, older-format project intake, broader project ERC/parity coverage, bounded repair
coordination, Orca advisory scheduling and quality measurement, before/after rendering, the
held-out corpus, real host UI validation and hosted calibration are unfinished. No 90% routing,
3x speedup, unqualified ordinary-board coverage or v0.13 release acceptance follows from these
tools or their synthetic integration tests.
