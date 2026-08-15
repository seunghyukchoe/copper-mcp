# MCP API Contract

## Design rules

- MCP is a thin adapter over pure application services.
- Tools use structured inputs and outputs; large artifacts use resource URIs.
- Read-only capabilities ship before mutation.
- Ephemeral artifact creation is distinct from durable filesystem or board mutation.
- Long-running work is backed by a durable internal job model, regardless of MCP client support.
- Network and authorization concerns never enter the geometry layer.

## Implemented tools

| Tool | Side effect | Description |
|---|---|---|
| `server_info` | None | Version, maturity, and honest capability inventory. |
| `inspect_board` | None | Bounded read-only inspection inside the configured workspace. |
| `run_board_drc` | Temporary report only | Fixed-argument KiCad DRC with a bounded, redacted summary. |
| `inspect_board_ir` | None | Read-only Board IR conversion check and structural description. |
| `inspect_live_board` | None | Optional `kicad-python` IPC observation of the first open PCB; returns numeric versions, a SHA-256 digest, byte count, bounded object counts, and a nullable opaque session CAS. |
| `observe_board_scene` | None, or a process-local render artifact when `include_render` is set | Bounded, region-scoped semantic scene of one board, with board text quarantined. |
| `observe_post_placement` | None | Read-only exact-revision observation: one file/context capture supplies both bounded semantic scene and aggregate redacted KiCad DRC. No token, candidate, render, or mutation input is accepted. |
| `observe_live_board_scene` | None | Bounded Circuit Scene `0.4.0` from the active KiCad IPC document; uses `board: "live"` and optional stale-digest compare values. |
| `preview_live_route` | None | Revision-bound, ref-anchored route proposal over one active KiCad IPC snapshot; never writes, runs DRC/fill, or grants apply authority. |
| `preview_layered_route` | None | Revision-bound, pad-ref-anchored two-signal-layer proposal with explicit full-stack vias and opt-in candidate-bound aggregate DRC evidence; still no refill, serialization, export, or apply authority. |
| `preview_live_layered_route` | None | Session-, source-, and Board IR-revision-bound via-capable proposal over one active KiCad IPC snapshot; candidate-only, with no DRC, refill, serialization, export, or apply authority. |
| `start_routing` | Local SQLite job state and bounded worker activity | Persist and queue one file-backed two-signal-layer proposal; returns a redacted lifecycle record and never applies copper. |
| `get_routing_job` | None | Read one authorization-bound routing lifecycle record and its normalized request after restart. |
| `cancel_routing_job` | Local SQLite lifecycle state | Request cooperative cancellation for one authorization-bound queued or running proposal. |
| `export_routing_candidate` | None | Explicitly disclose one immutable candidate geometry only after job and caller-context authorization succeed. |
| `preview_live_placement` | None | Revision-bound, ref-anchored placement proposal over one active KiCad IPC snapshot; never writes, runs DRC, or grants apply authority. |
| `inspect_live_editor_context` | None | Revision-bound active layer and bounded native selection references from the KiCad IPC editor; never reads raw selection text or mutates the editor. |
| `apply_candidate` | **Replaces the board file**; disabled by default | Separately authorized route-patch mutation. Requires an operator flag and a route-scoped single-use token. |
| `apply_placement_candidate` | **Replaces the board file**; disabled by default | Separately authorized bounded placement-pose mutation. Requires an operator flag and a placement-scoped single-use token. |
| `preview_placement` | None, or a short-lived placement capability when explicitly requested | Deterministic legality preview for a proposed footprint placement. `include_drc: true` may request aggregate DRC evidence for the file-backed serializer subset; `include_apply_token: true` may request a placement token. Neither flag writes the source board or grants live authority. |
| `preview_route` | None, or a temporary report when `include_drc` is set | Bounded, non-mutating two-pin route proposal on the documented Board IR subset. |
| `preview_route_bundle` | None | Bounded revision-bound composition of two through eight known net references; it never returns a partial plan, DRC artifact, derivative, token, or apply authority. |
| `render_circuit_schematic` | Process-local artifact only; stdio only | Validate structured Circuit Intent, require deterministic replay, and issue one opaque schematic resource capability. |
| `validate_candidate` | None | Validate and normalize candidate metadata. |
| `compare_candidates` | None | Correctness-first deterministic ranking. |

The static resource `pcb://server/manifest` exposes stable server metadata. On stdio only, a
successful `render_circuit_schematic` result also links one non-enumerable dynamic resource of the
form `pcb://artifacts/schematic/{opaque-token}/circuit.kicad_sch`.

`run_board_drc` returns the board SHA-256 revision, a DRC-context revision covering the board,
matching project/custom-rule files, and workspace-local KiCad library assets; KiCad/schema versions;
severity and connectivity counts; violation-type counts; and a hard-correctness pass flag. It
also reports how many DRC check classes KiCad marks ignored, while deliberately omitting their raw
descriptions along with net names, UUIDs, and coordinates. Exit codes `0` (clean) and `5`
(findings reported) are valid only when they agree with the strict report's finding collections;
other exit codes or process/report disagreement fail the tool. Warning-only and exclusion-only
findings can retain a hard-correctness pass flag while still requiring exit code `5`. The snapshot
and report have independent size ceilings, and report growth is limited before KiCad starts.

`inspect_board_ir` takes a workspace-relative `board` and integer `constraints`, and answers
whether the board converts to the supported Board IR before a caller commits to a preview. It
returns the board revision, snapshot and constraint digests, Board IR schema and units, copper
layer identities, per-collection object counts, and an `unmodelled_counts` map, or bounded
conversion diagnostic-code counts when the board is outside the subset. It never returns
coordinates, net names, pad or net identities, UUIDs, or source bytes.

`unmodelled_counts` is how a client learns what the conversion **accepted and did not model** —
roundrect rounding, unmodelled groups, edge-connector pads, unmodelled root board properties, and
unmodelled pad fabrication properties, keyed by the measured field each comes from. Four of the
five are the disclosure `R-134`, `R-139`, `R-141` and `R-144` each name as their mitigation, and
until 0.9.0 none of them reached a client at all. Every value is a non-negative integer and none
of them names anything: a count is not a set, and no surface refuses an operation on a board
carrying one. An unsupported board reports `{}` — a refused conversion measured nothing, and
zeros would claim it measured zero.

Every live tool below requires `COPPER_MCP_ALLOW_LIVE_IPC=1`, exact-membership and default off, the
same rule as `COPPER_MCP_ALLOW_APPLY`. With it unset the tools stay listed — a hidden tool is
indistinguishable from an unimplemented one — and refuse with a message naming the flag, before the
IPC endpoint is read from the environment and before any socket is opened. The refusal is a
capability state, not an error about the editor: it says nothing about whether KiCad is running.

`apply_live_candidate` additionally requires `COPPER_MCP_ALLOW_LIVE_APPLY=1`, under the same exact
membership rule. It is a third flag rather than the conjunction of the existing two because
ADR-0069 granted observation only, and it deliberately neither implies nor requires
`COPPER_MCP_ALLOW_APPLY`, which authorises replacing a file on disk. **The mutation is not
implemented:** the tool verifies consent, a live-scoped capability, and the session, board, and
snapshot compare-and-swap values, replays the candidate against the live board, and then refuses
with `capability_not_implemented`. See [ADR-0074](../adr/0074-live-ipc-one-undo-commit-apply.md).

`inspect_live_board` is a separate, no-argument read-only probe for an already-running KiCad PCB
Editor. It lazily loads the optional official `kicad-python` binding, accepts only KiCad's local
IPC socket, checks the binding/API version, and returns a redacted `kicad-ipc-live` record with a
board digest, bounded object counts, and required `session_revision`. With a plugin token this is
the opaque fixed-format PBKDF2 session CAS required by `preview_live_layered_route`; without one it
is explicitly `null`, which remains unrouteable. It never returns the live serialization, net
names, UUIDs, coordinates, tokens, or process salt. KiCad 9/10 requires a GUI session with the IPC
server enabled; a future
KiCad version is refused by default. This record is metadata-only and does not act as a route or
placement authority.

`observe_live_board_scene` takes the same constraints and region shape as `observe_board_scene`,
but requires the literal `board: "live"` and refuses `include_render`. It captures one bounded
IPC serialization, checks that its digest and byte count remain paired, and runs that exact source
through Board IR and Circuit Scene conversion. Optional `expect_board_revision` and
`expect_snapshot_digest` values provide compare-and-swap semantics for a caller re-observing the
same editor; either mismatch is a typed refusal. The output reuses the closed Scene `0.4.0`
contract with `board_path: "live"`, so it exposes exact geometry and typed references but no raw
serialization, socket path, or token. Revision-bound live proposal gates now exist for placement and
routing. These are read-only proposal surfaces: DRC, fill, editor mutation, and apply remain
separate file-backed or future transaction surfaces.

`observe_board_scene` takes one request object with a workspace-relative `board`, integer
`constraints`, a mandatory `region`, and optional `layers` and `include_annotations`. The region is
either a complete `min_x_nm`/`min_y_nm`/`max_x_nm`/`max_y_nm` box or one `around_ref_id` with a
`radius_nm`; supplying both forms, neither, a partial box, a box with a radius, a reference without
a radius, or reversed bounds is rejected before any file is read. There is no whole-board shorthand.
The resolved window is echoed back with a `source` of `explicit` or `around_ref`.

Objects are returned in two collections rather than flagged, each object additionally reporting
`locked` so pinned geometry is distinguishable from geometry a proposal may move (`null` for kinds
with no such concept, such as an outline contour or a net class). `static` holds `outline`,
`footprints`, `pads`, `keepouts` and `rules` — what a proposal must take as given — and `mutable`
holds `segments`, `arcs`, `vias` and `zones`. A footprint reports its Board IR origin, rotation,
side, owned pad references, courtyard rings and lock state. Each object carries the Board IR
`ref_id` it already has, its
`layer_ids`, exact integer `geometry`, and a `ref_stability` of `native` (a KiCad UUID, stable under
unrelated edits), `content_derived` (a geometry hash, which moves when its object changes), or
`request_scoped` (an id belonging to the request rather than the board). A scene-level
`ref_stability` summary reports `all_board_refs_native` plus the two counts, so a caller can decide
in one place whether the references it is about to store will survive.

`truncation` states completeness rather than implying it: `objects_returned`, `objects_omitted`,
`annotations_returned`, `annotations_omitted`, and a `ceiling_hit` that is non-null exactly when
something was dropped, naming `max_scene_objects`, `max_scene_vertices` or `max_scene_annotations`.
`ceiling_hit` names the first ceiling reached, so the two `*_omitted` counts are the authoritative
signal - objects and annotations are charged against separate budgets and both can truncate in one
response. Footprint origin, pad relationships and courtyard vertices consume the scene-vertex
budget as well as the footprint consuming one scene-object slot. Every ceiling is configurable;
the object default of 2,000 is provisional and about sixteen times the size of this repository's
own board.

The object ceilings are spent over **whole kinds**, so `truncation` is a summary and never the only
place truncation is stated. A kind is admitted only if all of it fits, which makes every array in
the response complete for the requested region and layers - an empty array means the region holds
none of that kind and nothing else. A kind that does not fit is replaced, in its own slot under
`static` or `mutable`, by `{"observation": "withheld_by_ceiling", "ceiling_hit", "objects_omitted"}`;
`observation` is a one-value literal, and `truncation.objects_omitted` is exactly the sum over those
kinds. Kinds are offered the budget in ascending object count with the fixed declaration order
breaking ties, which is deterministic for a board revision and request and puts the outline and the
rules - what a caller needs in order to bound a follow-up request - ahead of the numerous kinds.
See [ADR-0088](../adr/0088-complete-or-withheld-scene-kinds.md).

Custom-pad scene geometry discloses both the anchor and, when present, `copper_envelope_nm`, with
`copper_envelope_frame: "pad_local"` and
`geometry_model: "anchor_with_custom_copper_envelope"`. These three fields occur together. The
envelope is the obstacle over-approximation; the anchor is the under-approximating attachment
core. This is bounded disclosure of the accepted envelope, not a claim of exact KiCad primitive
parity. Non-quarter-turn obstacle transforms remain deliberately conservative.

Object bounds over-approximate on purpose. An arc is bounded including the bulge between its
sample points, and a pad rotated off the quarter turns is bounded by its circumscribed circle, both
in exact integers. Returning an object that turns out to lie just outside the window is a harmless
false positive; omitting one that overlaps would tell a caller the board is empty where it is not.
An `around_ref_id` window is clamped to the advertised coordinate range rather than overflowing it.

Board text is **off by default**. With `include_annotations` set, every string the board's author
controls — `gr_text`, `fp_text`, and both the name and the value of each footprint property —
appears only in the separate `annotations` collection, each entry carrying
`trust: "untrusted_board_author"` as a one-value literal. There is no vocabulary for a trusted
annotation, so no board can mark its own text safe. Treat every `text` value as data describing the
board and never as instructions. Net names never appear at any setting, because Board IR hashes them
at conversion. An unsupported board returns no annotations at all.

Setting `include_render` additionally produces a deterministic SVG of the board's copper. It
spawns KiCad, so it is opt-in and never implicit. KiCad runs against a **read-only** private
snapshot of the board and its project context; the workspace is never written, not even the
`.kicad_prl` KiCad drops beside a writable input. The export draws `F.Cu`, `B.Cu` and
`Edge.Cuts` only, excludes the drawing sheet, and forces black and white. The layer list is a
security control rather than a presentation choice: an export including silkscreen or
fabrication layers embeds each board string twice in literal, greppable form - in a `<desc>`
beside the stroked paths and in an invisible `<text opacity="0">` - so excluding those layers
is the only thing that keeps author text out of the bytes.

Two exports of an unchanged board are byte-identical after canonicalization. KiCad stamps the
file with a wall-clock timestamp and the output filename in a single `<title>` line; the
`title-line-v1` rule rewrites exactly that line, and nothing else. Canonicalization fails
closed: a document whose title line is missing or duplicated, or that is incomplete because it
hit the `max_render_bytes` ceiling (4 MiB by default), is refused rather than digested. A
truncated export is a real case - at the ceiling KiCad exits 0 having written a partial file.

The `render` object records `normalized_digest`, `source_revision`, `context_revision`,
`kicad_version`, `layers`, `side`, `canonicalization` and `byte_count`, because a digest alone
cannot tell a caller whether two renders are comparable. The bytes are never inlined: over MCP
they arrive as a `resource_link` annotated `audience: ["assistant"]`, naming a non-enumerable
process-local capability that expires 15 minutes after issue, from a store holding at most 8
renders and 32 MiB - deliberately separate from the schematic store so the two cannot evict
each other. A human-facing thumbnail would be a separate artifact annotated `audience:
["user"]` and is not implemented. The render is **whole-board even when the scene is a
window**: region scoping applies to semantics, not to the picture. It is advisory - any
disagreement with the scene should be resolved in favour of the scene - and no render is
produced for a board outside Board IR.

Unlike `render_circuit_schematic`, this tool is exposed over both transports: it returns one
self-contained response and retains no server-side state, so it follows the `preview_route`
precedent. It discloses workspace board coordinates by design, and workspace confinement is what
bounds that disclosure. `include_render` is the one asymmetry: render bytes are delivered
through the process-local capability store, which a stateless HTTP deployment cannot resolve,
so that flag alone is refused off stdio while the semantic scene stays available everywhere. Its handler returns a closed contract, so it advertises a real `outputSchema`
and returns populated `structuredContent`.

`preview_live_placement` takes the same ref-anchored rules and proposals as the file-backed
placement preview, but requires the literal `board: "live"`, scene footprint/pad references, and
both `expect_board_revision` and `expect_snapshot_digest` values copied from the live scene. It
captures one byte-confirmed KiCad IPC serialization and reuses the exact Board IR 0.2 snapshot,
placement view, and deterministic legalizer. A board mismatch is refused before conversion; a
snapshot mismatch is refused before placement-view/legalizer work. The output uses
`board_path: "live"` only as a label and carries no source bytes, socket/token, DRC, fill,
apply-token, or editor-write authority. The fake-client B-014 oracle covers candidate equality
with the file-backed path, deterministic replay, stale preconditions, and zero mutating calls;
it does not claim success against a running KiCad GUI session.

`inspect_live_editor_context` requires `board: "live"` plus the raw board-serialization SHA-256
precondition. It reads only the official Board `get_active_layer()`, `get_layer_name()`, and
`get_selection()` APIs, confirms the board and editor context twice, and returns a context digest
for follow-up compare-and-swap calls. Only allow-listed wrapper types with a validated native KIID
become typed refs (`pad:kicad:<uuid>`, `segment:kicad:<uuid>`, and similar); unknown, empty,
malformed, or over-budget selections fail closed. The service never calls
`get_selection_as_string()`, returns no board text, coordinates, net names, or tokens, and exposes
no write, DRC, placement, or routing authority. Its response `snapshot_digest` intentionally
aliases the confirmed serialization digest; it does not accept a scene `snapshot_digest`, which is
constraint-profile dependent and cannot be compared without receiving that same profile. Chain
the scene's `board_revision` into this read, then use `context_digest` for subsequent CAS.

`preview_placement` takes one request object with a workspace-relative `board`, integer
`constraints`, and `subjects` - the footprint references a proposal may move - plus optional
`rules`, `proposals`, `placement_grid_nm`, `include_drc`, and the explicit `include_apply_token`
capability request. Rules come in seven kinds (proximity, alignment,
symmetry, edge, region, orientation, side) and name objects only by references a scene already
returned. Proposals are anchored the same way, as an offset from another object's edge or
centre; there is no field anywhere in the language that accepts an absolute coordinate, so every
position in a response was derived by the server and snapped to the placement grid.

A `previewed` response carries an immutable candidate bound to **both** digests - `base_revision`
for the source board and `view_revision` for the placement projection. The placement view is
derived from the same Board IR 0.2 snapshot after the service verifies that the source bytes match
the snapshot's source revision; it is no longer recovered by a second parser. The response also
carries evidence holding per-rule residuals and the legality record. `pad_overlap` is
**three-valued**: `proven_clear` when pad
bounds are disjoint, `violated` when pad cores overlap, and `inconclusive` in between.
`inconclusive` is not a failure and a candidate is still produced; it means neither clearance nor
collision could be proven. `outline_containment` and `keepout_respect` use the same direction
bracket: over-approximating bounds prove `proven_inside`/`proven_clear`, under-approximating cores
prove an actual edge crossing or keepout intrusion, and the gap is `inconclusive`. This includes
copper wholly remote from an edge: KiCad 10.0.5's `copper_edge_clearance` provider tests collision
with nearby `Edge.Cuts`, not global point-in-board containment. A consumer needing an all-proven
placement must reject an inconclusive field or request the candidate-bound KiCad DRC path
([ADR-0110](../adr/0110-placement-boundary-verdicts-bracket-kicad-parity.md)).
`courtyard_overlap` is **three-valued** for the same reason, over Board IR 0.2's simple orthogonal
courtyard subset: `proven_clear` when the regions share no area, `violated` at exact parity with
KiCad's contracted courtyard cache, and `inconclusive` in the penetration band below its 10,000 nm
collision threshold, where raw geometry and KiCad disagree. A footprint's rings form one even-odd
region, so a ring nested inside another is a hole. Shapes are compared **per courtyard layer, not
per footprint side**: `F.CrtYd` and `B.CrtYd` remain independent, but a footprint may draw on the
layer opposite its own side and that keep-out is compared on the layer it was drawn on
([ADR-0097](../adr/0097-courtyard-layer-decides-the-side.md)). Edge contact is not overlap. A Board
IR conversion rejects unsupported courtyard
topology before a placement view exists, so the result cannot silently claim fidelity outside that
subset.
Padless/graphics-only footprints remain unavailable as subjects and anchors, but their supported
courtyards remain stationary collision envelopes and are included in this per-layer check; they are
not emitted in the candidate manifest.

A `refused` response carries a typed code: `unresolved_ref`, `infeasible_constraints`,
`budget_exhausted`, `unsupported_geometry`, `illegal_placement`, `stale_revision` or
`invalid_request`. `infeasible_constraints` and `budget_exhausted` are kept strictly apart - the
first is a proof that no placement satisfies the rules as written, the second an admission that
the work ran out - and only syntactic contradictions are claimed as infeasible. An
`illegal_placement` refusal includes the legality record that condemned it, so a caller never has
to guess which independent check failed. `satisfied_within_tolerance` appears only
when the caller supplied a `tolerance_nm`; an unstated tolerance means exact.

A proposal that would move a footprint whose Board IR `locked` field is true is refused as
`unsupported_geometry` before a candidate is issued. Unlocking or applying that move is never
implicit.

**The public preview never applies a placement.** With `include_drc: true`, the file-backed tool
may additionally run the same candidate through the private, disposable KiCad DRC gate. The
response exposes only candidate/source/patched-board/context digests and the aggregate
`DrcSummary`; it carries no raw report, board bytes, net names, UUIDs, or fabrication conclusion.
`passed` means KiCad reported no active errors or unconnected items, while `clean` is stricter and
also requires zero warnings, exclusions, ignored checks, and violation types. A warning-only
report can therefore be `passed: true, clean: false`. The source board is never written, the
candidate is re-bound to the captured source and context, and any timeout, unsupported syntax,
context race, malformed report, or binding mismatch fails closed. Live placement and apply paths
force `include_drc: false`. What the public tool claims without the flag is exactly what the
deterministic legalizer proved. A file-backed preview may explicitly request a short-lived
placement-scoped apply token; the token is issued only when the operator has enabled apply and the
same pure source-preserving replay accepts the candidate. Moving a footprint moves its pads, so a
placement candidate invalidates any route candidate bound to the same base revision, and observing
a scene after a hypothetical placement is not supported. Live placement never grants apply
authority.

`apply_placement_candidate` is the separately authorized file-level mutation surface for that
narrow replay subset. It takes `board`, the `candidate` manifest from a preview,
`apply_token`, `expect_board_revision`, and `constraints`. The token is operation-domain bound to
placement, so a route token cannot cross the boundary. The service applies the same lockfile,
double-CAS, pre-apply backup, atomic replacement, and truthful post-publication result contract
as route apply. It refuses side flips, locked footprints, unsupported properties/text/fabrication
graphics/library identity/3D-model pose, derived identities, and no-op candidates before a
replacement. Its response reports `footprints_moved` and `bytes_changed`; KiCad-open and DRC
stages remain `not_run` until independently verified.

`preview_route` takes one request object with a workspace-relative `board`, a copper `layer` name,
integer `constraints` for the applied net class, and **exactly one** net selector. `net` is the
compatibility selector for a caller that already knows the KiCad net name. `net_ref_id` is the
normal AI path: it is copied from Circuit Scene and requires that response's `board_revision` and
`snapshot_digest` as `expect_board_revision` and `expect_snapshot_digest`. The reference is used
directly rather than hashed as another name. A board-revision mismatch returns `stale_revision`
before Board IR conversion and carries a null `snapshot_digest`; a snapshot mismatch is checked
immediately after conversion. Both precede route search, zone-fill authority, DRC, and apply-token
issuance. Raw names remain supported but are not required for a scene-to-route workflow.

Optional `seed`, `settings`, `include_drc`, `include_fill_authority`, and `include_apply_token`
fields control only the documented bounded work. Unknown fields, selector ambiguity, missing
reference preconditions, non-integer or out-of-range budgets, booleans supplied as integers, control
characters, and unsupported layer names are rejected through the non-echoing request boundary.
The MCP tool advertises a closed two-variant input schema and a closed five-variant output union;
each status fixes which of candidate, connection, diagnostic, conversion counts, DRC, fill, and
token evidence may be non-null. Successful results are published as protocol `structuredContent`.
Every response carries a
`status` of `routed`, `already_connected`, `not_routed`, or `unsupported_board`, the board revision,
the Board IR snapshot digest when conversion succeeded, and the validated request. A routed
response includes the candidate ID, endpoint pad IDs, integer
geometry, exact cost decomposition, deterministic search metrics, and the resource ceilings that
produced it. Geometry is carried as `patch.paths`, a list of polylines: a two-pin proposal has one,
and a multi-pin proposal has one per merged component, together forming a tree over the net. The
response also reports `pad_count` and the `ordering_policy` that fixed the merge order. The current
values are `single-path` for two-pin routes, `batched-1-steiner-v1` for low-degree multi-pin
proposals (at most nine evolving components), and `component-mst-v1` for larger trees. An unrouted response carries one typed, non-echoing diagnostic; an unsupported board
carries bounded conversion diagnostic-code counts instead of raw adapter text.

The diagnostic carries one optional evidence object, `off_grid`, and carries it on exactly the
`off_grid` code — the contract refuses a payload that attaches it to another code and refuses an
`off_grid` diagnostic that lacks it, whether the key is `null` or absent, so a caller may branch on
either. The contract also refuses a self-contradicting measurement: an anchor equal to the pad it
anchors, a miss wider than half the step it names, a miss of zero on both axes, or a divisor the
requested step divides. Every one of those is checked by the published schema as well as by the
runtime, so a consumer validating against the schema alone accepts nothing the service would refuse
to construct. It names the off-lattice pad,
the pad the lattice is anchored at, the `grid_step_nm` in use, the signed per-axis nanometres from
the nearest lattice line to the pad centre, and `largest_representable_step_nm`, the greatest common
divisor of the two pad-centre deltas. The last is a statement about representability and not a
prediction that routing succeeds at that step, and it is `null` when the divisor exceeds the
JSON-safe integer range — reachable only from pads near opposite legal coordinate extremes, where
it is withheld rather than clamped because a clamped divisor would be a false claim. The other
fields stay exact there, so the refusal is still actionable. Every other diagnostic reports `off_grid: null` —
never an empty object, never a zero — because a refusal that measured no lattice has nothing to say
about one ([ADR-0093](../adr/0093-actionable-off-grid-refusals.md)).

`already_connected` is a terminal success, not a failure: the two pads already share one copper
component on the selected layer, so there is nothing to propose. Its `connection` object carries the
Board IR base revision it is bound to, both endpoint pad IDs, and integer counts of the attachment
segments, component objects, pads and vias involved. A non-zero `vias` count means the connection
was established across copper layers through those vias, so the evidence is multilayer even though
the request names a single layer. It returns no geometry and no diagnostic, and the outcome
deliberately has no `RouteFailureCode`. Clients that switch exhaustively over the previous three
statuses need a fourth branch.

Setting `include_fill_authority` allows freshness-verified poured copper to participate in routing.
KiCad refills a private disposable copy and the recomputed pour must reproduce the board's cache
exactly; matching returns a `fill_authority` record carrying both digests, the KiCad version, island
and vertex counts, and a `routing_effect` literal. On a routed result, `foreign_zone_obstacles`
means exact islands replaced the conservative foreign-zone envelope, `connectivity_evidence` means
same-net islands were available to prove contact, `both` means both roles were present, and
`verified_context` means the selected-layer cache was verified but contained no island relevant to
the route. On an `already_connected` result the effect is `connectivity_evidence`. A mismatch is
refused with the typed `stale_fill` diagnostic rather than answering from either version. The
workspace board is never refilled. The flag is opt-in because it spawns KiCad, and it changes
nothing for a board without zones on the requested layer.

A candidate the exact pour shaped records that fact in `candidate.fill_binding`, the content
address of the fill the router was handed; it is `null` when the conservative zone envelope was
the obstacle model, which is the ordinary case. Every verifier of a routed candidate replays it,
and a replay under a different obstacle model is not a replay, so the binding is what a replay
has to re-establish (ADR-0103). Two consequences are visible at this surface:

- `include_fill_authority` with `include_drc` is supported: the preview still holds the evidence
  and hands it to the serialization boundary the DRC run goes through.
- `include_fill_authority` with `include_apply_token` returns the candidate and **no token**.
  Apply runs in a later process and holds no fill evidence, so it could only replay against the
  envelope. A token is withheld here for the same reason it is withheld for a board whose derived
  geometry identities the append-only apply engine would reject: a capability whose exercise is
  guaranteed to refuse must not be issued. Applying a fill-routed candidate is not yet available.

Setting `include_drc` on file-backed `preview_layered_route` binds the proposal to candidate-bound
authoritative KiCad DRC evidence. The response returns the same aggregate, redacted summary as
`run_board_drc` plus candidate, source, patched-board, and patched-context revisions. The call
fails rather than returning a candidate whose requested evidence is missing or does not bind. The
flag is explicitly disabled for `preview_live_layered_route` and durable routing jobs. On a
non-routed status `drc_evidence` is `null`; no DRC is run without a candidate. Preview writes no
file, creates no job, and never returns source board bytes; it does return the geometry it generated,
so a host that must not disclose generated copper to a model should not enable this tool.

The aggregate `passed` field is the compatibility hard gate: it means no active errors or
unconnected items. The stricter `clean` field is true only when there are no errors, warnings,
exclusions, ignored checks, unconnected items, or violation types. A warning-only result can
therefore be `passed=true` and `clean=false`; neither field is a whole-board, fabrication, or
FreeRouting-quality claim.

When present, `drc_evidence.statement` is a deterministic unsigned in-toto Statement payload. Its
subject is the candidate digest; Link v0.3 materials are fixed names for the source, Board IR base,
patched board, and patched DRC context digests; and the byproducts contain only the existing
aggregate DRC summary plus the `disposable-candidate` scope. No path, net name, UUID, coordinate,
raw finding, board bytes, signature, or DSSE envelope is included. Canonical JSON bytes are exposed
only through the internal evidence object; signing and verification remain separate future gates.

`preview_layered_route` is the separate via-capable proposal surface. Its request names a
workspace-relative `.kicad_pcb`, two `pad:` references, explicit net-class dimensions, and both
the source-byte and Board IR snapshot digests copied from an observation. It does not accept a raw
net selector. The service converts the exact source first, checks both compare-and-swap values,
infers the common net from the pads, and then invokes the bounded Board IR adapter. The supported
matrix is exactly two signal layers, a rectangular hole-free outline, conservative foreign
geometry/keepout envelopes, and full-stack through-vias. A routed result carries a separate
layered candidate with per-layer integer paths, via centers/dimensions, deterministic cost and
search metrics, and a canonical candidate digest. Refusals carry only bounded status/code/count
data; a conversion failure is `unsupported_board`, while a valid board outside the layered subset
is `not_routed` with its snapshot digest.

This tool is read-only and idempotent. Without `include_drc` it never calls `begin_commit`,
`refill_zones`, or the KiCad DRC command. With the explicit flag it invokes only the bounded private
candidate-DRC replay and returns aggregate evidence; it still returns no serialized patch, apply
token, durable job, or persistent candidate. Its candidate is therefore an actionable proposal
with an optional authority signal for a later reviewed serializer/apply flow, not a claim of whole-
board or fabrication acceptance. B-024 covers deterministic calls and schema/CAS behavior; B-032
covers the new evidence binding, and the blocked-pad KiCad fixture covers the narrow real-tool path.

`preview_live_layered_route` applies the same candidate contract to the active editor. Its request
uses `board: "live"`, two `pad:` references, the net-class/layer/search bounds, and source,
Board IR, and redacted KiCad-session compare-and-swap digests. The service captures one bounded
`Board.get_as_string()` serialization, confirms it byte-for-byte, closes the official IPC client,
converts those exact bytes through the Board IR adapter, and infers the net only from the two pads.
The session revision is `pbkdf2-hmac-sha256:<64 lowercase hex>`: fixed-work PBKDF2-HMAC-SHA256
over `KICAD_API_TOKEN`, with a domain-separated fresh process-local 256-bit salt, rather than a
public token hash. `inspect_live_board` publishes this opaque value (or `null` without a plugin
token) so its structured output composes with the scene's public digests and pad refs. It is
constant-time compared after fixed-format validation and intentionally fails after a fresh process
or restart; neither token nor salt is returned. The remaining route
deadline is passed to IPC and search, checked between synchronous IPC calls, and checked during
bounded serialized-item counting.
A stale session/source/snapshot refuses before candidate work,
and the live candidate is compared against the file-backed oracle in B-026. The official wrapper
is synchronous, so a third-party IPC call cannot be forcibly pre-empted by this Python process;
this is a cooperative deadline, not a hard real-time guarantee. The supported
two-signal-layer geometry remains proposal-only: endpoint-via legality, KiCad DRC, refill,
serialization/export, apply, real GUI success, and electrical/fabrication claims are not implied.

`render_circuit_schematic` takes one structured Circuit Intent `content` object. The shared service
performs bounded semantic validation and normalization, computes the intent digest, renders the
accepted snapshot twice, and fails unless both immutable artifacts are byte-identical. Callers do
not submit KiCad S-expressions or calculate the content digest. Ordinary tool output is a redacted
`copper.circuit-schematic-build` record containing schema and format versions, intent and artifact
digests, byte and topology counts, and a resource URI. It omits titles, component references and
values, net and port names, UUIDs, source JSON, and schematic bytes.
Its versioned JSON Schema is
[`schemas/circuit-schematic-build/0.1.0.schema.json`](../../schemas/circuit-schematic-build/0.1.0.schema.json).

The verification matrix reports `intent_topology`, `artifact_digest`, `provenance_binding`, and
`deterministic_replay` as `passed`. It reports `kicad_cli_parse`, `erc`,
`schematic_board_parity`, and `electrical_validation` as `not_run`, with `board_ready` set to
`false`. A repository fixture's separate KiCad integration evidence does not upgrade the result of
an individual build.

The capability token has at least 256 bits of randomness; the artifact digest identifies content
but is not authorization. Access expires absolutely 15 minutes after insertion and a read does not
renew it. The thread-safe process-local store permits at most 16 entries, 16 MiB in total, and
1 MB (1,000,000 bytes) per artifact, evicts deterministically by least-recently-used order, rechecks the digest on every
read, and gives the same unavailable result for malformed, expired, evicted, and unknown tokens.
Expired entries are reclaimed lazily on a later store read/insertion or process exit. This access
TTL is not a secure-erasure guarantee for stale allocator memory, crash/swap artifacts, or byte
copies already returned to a host. The store has no listing API, persistence, filesystem write, or
content/token logging. Reading the resource reveals the accepted logical topology, so the MCP host
decides whether the bytes may enter model context.

The tool changes ephemeral server state and is therefore non-read-only and non-idempotent, although
it is non-destructive and performs no network access. Both the tool and dynamic resource are
disabled for streamable HTTP until authenticated principals, session isolation, authorization, and
per-principal quotas exist. Rendering over MCP never writes into the configured workspace.

The routing-job surface now composes four bounded SQLite tables behind a protocol-independent
repository: lifecycle metadata, a normalized request envelope, a redacted candidate manifest, and
an explicit geometry export. `start_routing` accepts only the current file-backed two-signal-layer
request, stores it before dispatching a single local worker, and returns a deterministic job ID as
an idempotency key rather than as authorization. `get_routing_job`, `cancel_routing_job`, and
`export_routing_candidate` require the same caller-context digest; unknown, expired, and wrong-
context records share an unavailable error. Requests are deep-frozen, bounded, and reject board
bytes, prompts, credentials, DRC findings, and token-like fields. Candidate bytes are content-
addressed, TTL/capacity bounded, and separately disclosed only by the export tool. The worker
rechecks the source and Board IR CAS values before routing and publishes the manifest/export before
the lifecycle CAS completion; an orphaned export is harmless and expires with the repository.

This is an ordinary MCP job API, not a claim of MCP Tasks compatibility. The current Tasks extension
(`io.modelcontextprotocol/tasks`) requires per-request capability negotiation, durable creation
before returning a task handle, and a polymorphic result (`tasks/get`, `tasks/update`, and
`tasks/cancel`). CopperMCP will add that adapter only after a pinned client matrix and task-handle
authorization contract. Route-candidate and bounded placement apply are implemented and documented
above; general placement fidelity and post-action observation remain open.

`apply_candidate` changes a board only for **route patches**. The separate
`apply_placement_candidate` tool is the corresponding bounded placement-pose mutation and has its
own operation-scoped token. Both take a preview candidate, an `apply_token`, an expected board
revision, and the same constraint profile; neither accepts model-generated copper or bypasses the
deterministic replay gate.

Three independent things must all hold. The operator must have set `COPPER_MCP_ALLOW_APPLY=1`
(exactly `"0"` or `"1"`; the tool stays listed when it is off and refuses with `apply_disabled`,
so the capability is discoverable rather than mysteriously absent). The caller must present an
`apply_token` that `preview_route` issued - via `include_apply_token` - for exactly this
candidate, board revision and path; it is verified against a key held only in this process, so
tokens do not survive a server restart, and it is checked **before** the board is read or parsed
so an unauthorized caller cannot make the tool do expensive work. A token is issued only when
apply is enabled and only for a board the append-only engine can actually apply to. And the board
must not be open in KiCad: a `~name.lck` sibling is a hard refusal naming the file, never removed,
because pcbnew has no external-change watcher and would silently overwrite the applied board on
its next save; it is re-checked under the lock immediately before the write.

The board digest and the Board IR snapshot digest are compared before the splice, and the file
digest is re-checked **under an exclusive lock held across the swap and the rename** immediately
before publication - so two applies from the same base serialise and the loser refuses rather
than clobbering the winner. A mismatch returns `stale_candidate` and is **never auto-refreshed**.
Before anything is written, a timestamped pre-apply copy is created in a `.copper-mcp-backups/`
subdirectory (not beside the board, where it would itself be an apply target), kept to a bounded
count, and its path returned in `backup_path` - **that copy is the undo**, restored by copying it
back. It is not a KiCad undo step. KiCad's own `-bak` files are never touched.

Publication is an atomic replace that preserves the board's permission bits. A failure *before*
the rename leaves the board untouched and is a clean refusal; a failure *after* it means the
board is already changed, and that is reported truthfully as **`applied_but_unverified`** with
the best-effort observed final digest rather than as "nothing changed". The final digest may be
the original when guarded rollback succeeds, a concurrent writer, or `null` when the board is
missing/unreadable. In that case a *guarded* rollback runs - it restores the pre-apply bytes only if the file still holds
exactly what this apply wrote, so a concurrent writer's newer bytes are never clobbered. The
service also takes one last best-effort digest observation before a successful apply response so
a visible rewrite after verification is not reported as `applied`; a longer editor transaction
would still be needed to close the last nanosecond race. The `verification` matrix reports byte
preservation, a fail-closed reparse and Board IR equality as `passed`, and reports
`kicad_opened_board` and `drc_after_apply` as `not_run` - an applied board carries no DRC
evidence. Failure codes are `invalid_request`, `apply_disabled`, `invalid_token`,
`token_expired`, `token_already_used`, `stale_candidate`, `backup_failed`, `kicad_open`,
`unsupported_board`, `unsafe_filesystem`, `splice_assertion_failed` and
`apply_verification_failed`. The tool's annotations say `destructiveHint: true` and
`readOnlyHint: false` truthfully, but they are advisory client hints and enforce nothing.

There is no merge, lock override, IPC apply, or batch apply. The read-only
`observe_post_placement` tool provides a bounded file-backed scene plus aggregate DRC observation
for an explicitly expected board revision; it does not establish mutation provenance or provide an
IPC/live-editor apply path.

## Planned tools

`analyze_routability`, `start_routing`, `get_routing_job`, `cancel_routing_job`,
`validate_route_candidate`, `explain_routing_failure`, and `export_candidate`.

Routing jobs will always have ordinary start/get/cancel tools. MCP Tasks may map onto the same job
records when both peers advertise support; Tasks will not become the only compatibility path.

## AI boundary

The host agent may interpret design intent and call tools. Optional learned policies may rank nets,
suggest corridors, choose repair neighborhoods, or score candidates. Neither may provide geometry
that bypasses the deterministic router and validators. Model output is untrusted input.

The planned Circuit Scene IR will add bounded semantic and visual observation plus typed placement
intent. Models may request immutable placement previews/candidates; deterministic services own
snapping, connectivity, clearance, provenance, and validation, and any eventual apply remains a
separate explicit capability. Direct model-authored KiCad mutation is never an MCP shortcut. This is
a high-fidelity north star; live-scene action CAS, broader placement fidelity, and autonomous
policy remain unimplemented. The narrow file-backed `apply_placement_candidate` capability is
implemented as a separately authorized, default-off operation with closed request fields and
post-publication verification.

## Compatibility

Public tools and schemas are versioned independently of implementation backends. Before `1.0.0`, a
minor release may change experimental contracts, but the changelog and migration notes must explain
the impact. Stable tools should be additive whenever possible.
