# Native supervised optimization

This is the implemented native slice of the [v0.13 plan](../plans/v0.13-supervised-optimization.md),
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
| `board` | Workspace board path ending in `.kicad_pcb`. |
| `expect_board_revision`, `expect_snapshot_digest` | Both observed identities are mandatory. |
| `constraints` | The existing integer `clearance_nm`, `track_width_nm`, `via_diameter_nm`, `via_drill_nm` object. |
| `target_net_refs` | Explicit distinct net references. Targets are never silently truncated. |
| `movable_footprint_refs` | Optional explicit scope; the empty default means a verified identity placement. Locked footprints refuse. |
| `placement_intent_path` | Optional bounded JSON sidecar with only `rules` and `proposals`, using the existing placement language. It cannot override the board, scope, side or capabilities. |
| `electrical_intent_path` | Optional bounded, self-digesting Circuit Intent JSON snapshot. Supplying it makes ERC mandatory. Ordinary `.kicad_sch` intake is not implemented in this slice. |
| `placement_grid_nm`, `routing_settings`, `seed` | Bounded existing native search parameters. Manhattan distance may guide the search, never final evidence. |
| `limits` | Cumulative runtime, candidate, placement, route-attempt, repair, expansion, obstacle-check and output ceilings. Server ceilings may tighten them. |
| `allowed_backends` | The implemented path is `internal-layered-v1`. External selections currently refuse until the production format bridge is integrated. The [local runtime setup](local-router-runtime.md) is separate. |

The response includes a job record. `get_optimization_job` takes its `job_id` and returns current
state and bounded judge reports. `cancel_optimization_job` additionally requires
`expected_record_revision`; it fences publication and invalidates issued resource access.
The operator identity is server-owned, not a caller-selected digest. Network transport execution
is refused until authenticated ownership is integrated.

## Execution and evidence

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
tie-break. The current headroom value is a conservative zero lower bound. It is not a measured
placement-quality or clearance-optimality claim.

## Export and confirm

`export_optimization_package` requires `job_id`, `expected_record_revision` and
`expected_package_digest`. Metadata export carries no apply authority. Optional
`include_geometry: true` requests disclosure of the **complete candidate board including original
design content**; it requires a separate trusted-host consent. The returned resource lasts at most
five minutes, is byte/count bounded, and is revoked when the job is cancelled.

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

Zoned compositions refuse until fresh candidate fill is integrated. Cross-layer multi-pin trees,
production FreeRouting/SRJ conversion and disposal, normal-workspace schematic ERC, bounded repair
coordination, Orca advisory scheduling and quality measurement, before/after rendering, the
held-out corpus, real host UI validation and hosted calibration are unfinished. No 90% routing,
3x speedup, unqualified ordinary-board coverage or v0.13 release acceptance follows from these
tools or their synthetic integration tests.
