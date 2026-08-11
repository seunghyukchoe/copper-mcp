# Migrating a deployment to CopperMCP 0.7.0

CopperMCP 0.7.0 changes several behaviors a working 0.6.0 deployment can depend on without having
declared it: two families of diagnostic code that callers match on, the spelling every integer
environment variable accepts, the shape a Circuit Scene takes when it truncates, the locator every
root Board IR refusal carries, the message seven pad refusals emit, the derivations the published
DRC summary schema enforces, and — the one that invalidates stored data — `ROUTER_VERSION`, which
moves every route candidate identity. It also raises four parser defaults and one router default,
each a deliberate change to a denial-of-service posture and worth a decision rather than an upgrade.

Read §5 first if you have stored any route candidate, bundle, or exported candidate. Everything
else is additive, and [What does not require migration](#what-does-not-require-migration) names
this release's other landed changes and says why each one is additive, rather than leaving you to
infer it from silence.

## 1. `budget.exceeded` becomes ten discriminated codes

Every structural parse budget used to refuse under the single Board IR conversion code
`budget.exceeded`. Which ceiling had run out was not recoverable from the response, because
`inspect_board_ir` publishes `conversion_diagnostic_counts` — a count over diagnostic *codes* — and
the messages that did distinguish them are dropped before a caller sees them. An operator was told
that a limit was hit and never which one, at exactly the moment six of those limits became settable
([ADR-0079](../adr/0079-discriminated-configurable-parse-budgets.md), #112).

0.7.0 refuses under `budget.exceeded.<budget>`, where `<budget>` is the `ParseLimits` field name
without its `max_` prefix:

| Code | Raised by | Set with |
|---|---|---|
| `budget.exceeded.input_bytes` | `max_input_bytes` | `COPPER_MCP_MAX_BOARD_BYTES` (tightening only) |
| `budget.exceeded.tokens` | `max_tokens` | `COPPER_MCP_MAX_PARSE_TOKENS` |
| `budget.exceeded.nodes` | `max_nodes` | `COPPER_MCP_MAX_PARSE_NODES` |
| `budget.exceeded.children_per_list` | `max_children_per_list` | `COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST` |
| `budget.exceeded.objects` | `max_objects` | `COPPER_MCP_MAX_PARSE_OBJECTS` |
| `budget.exceeded.total_vertices` | `max_total_vertices` | `COPPER_MCP_MAX_PARSE_TOTAL_VERTICES` |
| `budget.exceeded.intersection_tests` | `max_intersection_tests` | `COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS` |
| `budget.exceeded.depth` | `max_depth` | not settable |
| `budget.exceeded.atom_chars` | `max_atom_chars` | not settable |
| `budget.exceeded.vertices_per_ring` | `max_vertices_per_ring` | not settable |

**Any caller matching the exact string `budget.exceeded` stops matching.**

To migrate:

1. Find every caller that compares a Board IR conversion diagnostic code to `budget.exceeded`.
   Replace the equality with a prefix test against `budget.exceeded.` if the caller only needs to
   know that *some* budget ran out — that stays correct when an eleventh budget is added. The
   prefix is exported as `copper_mcp.board_ir.BUDGET_EXCEEDED_PREFIX`.
2. If the caller wants to react differently per budget — for instance, to tell a user which
   variable to raise — match the specific codes above instead.
3. One refusal moved out of this family entirely. A footprint carrying more than 64 courtyard rings
   is now `schema.limit`, matching what the Board IR decoder already returned for the identical
   rule. That is a fixed schema ceiling with no knob, so it does not belong to a family whose
   members all name a setting. A caller that treated it as a budget refusal should treat it as the
   schema limit it always was.
4. The Circuit Intent (`.kicad_sch`) surface is deliberately unchanged and still raises the bare
   `budget.exceeded`. Those budgets have no operator knobs, and a discriminated code exists exactly
   where a knob exists.

## 2. Integer environment variables require an unambiguous spelling

`_bounded_int` used `int()` directly, which is looser than it looks: it accepted `"1_000"` as 1000,
the Arabic-Indic `"٤"` as 4, and stripped surrounding whitespace. A deployment's environment could
therefore *read* as one ceiling and *enforce* another.

0.7.0 requires an optional `-` followed by ASCII digits, and raises `ConfigurationError` at startup
otherwise. This applies to **every** integer setting, not only the new ones:
`COPPER_MCP_PORT`, `COPPER_MCP_MAX_BOARD_BYTES`, `COPPER_MCP_KICAD_TIMEOUT_SECONDS`, the DRC and
route-preview budgets, the fill, scene, render, and placement budgets, and the six new parse
budgets.

To migrate: check the server process environment for any integer value written with digit
separators, padding whitespace, or non-ASCII digits, and rewrite it as plain digits. A deployment
using ordinary values is unaffected. A deployment using one of these spellings fails loudly at
startup instead of silently enforcing a different number, which is the point.

## 3. Four parser defaults are raised — decide, do not just accept

The structural budgets were mis-scaled against the byte ceiling they were supposed to accompany: one
mebibyte of ordinary KiCad source carries about 130,000–170,000 S-expression nodes, so the 16 MiB
byte ceiling implied up to 2.7 million nodes against a node budget of 500,000. The node budget
refused every board above roughly 3.2 MiB, and `COPPER_MCP_MAX_BOARD_BYTES` could never bind. The
measurement is in
[the calibration note](../research/parse-budget-calibration-v1.md).

| Budget | 0.6.0 | 0.7.0 |
|---|---:|---:|
| `max_tokens` | 1,000,000 | 4,000,000 |
| `max_nodes` | 500,000 | 3,000,000 |
| `max_children_per_list` | 100,000 | 500,000 |
| `max_total_vertices` | 1,000,000 | 2,000,000 |

These budgets are a defence against a hostile `.kicad_pcb`, so the price is stated rather than
implied. Against deliberately adversarial input filling the whole 16 MiB byte ceiling, the transient
parse arena's worst case rises from **61 MiB / 0.70 s to 244 MiB / 2.91 s**. Every shape still
refuses, in bounded time, with a typed code.

To migrate:

- **If a larger transient allocation is acceptable**, do nothing. Boards up to roughly 16 MiB now
  reach the converter instead of being refused by a knob that did not exist.
- **If it is not**, set `COPPER_MCP_MAX_PARSE_TOKENS=1000000`. Peak parse-arena residency measured
  linear in the token budget at about 61 bytes per admitted token, so that value restores the 0.6.0
  memory posture exactly, while keeping the discriminated refusal.
- **Do not** try to mitigate this by lowering `COPPER_MCP_MAX_BOARD_BYTES` alone. It does not work
  for the worst-case shape: 4 MiB of deeply nested input still costs 244 MiB, because it exhausts
  the token budget rather than the byte budget. The token budget is the memory control.

## 4. Circuit Scene 0.3.0: a truncated kind is no longer an empty array

This one only shows up on a response that was already truncated. If every scene your client asks
for comes back with `truncation.objects_omitted == 0`, nothing in the response changes and there is
nothing to do beyond accepting the new `scene_version` string.

The scene used to spend one object budget over the objects of every kind in a fixed emission order.
Segments outnumber every other kind on a real board by two orders of magnitude and were emitted
fifth, so on a dense board they consumed the whole budget and every kind behind them — vias, zones,
and the net class under `rules` — came back `[]`. Measured on eleven real boards, eight returned
`vias: []` for boards holding up to 1,003 vias (#127). `truncation.ceiling_hit` and
`truncation.objects_omitted` did say truncation had happened; they did not say *what* was
truncated, and `vias: []` from a truncated scene was byte-identical to `vias: []` from a board with
no vias.

0.3.0 spends the ceilings over **whole kinds**. A kind is admitted only if all of it fits, so every
array in the response is complete for the requested region and layers. A kind that does not fit is
replaced — in its own slot under `static` or `mutable` — by an object:

```json
"mutable": {
  "segments": {
    "observation": "withheld_by_ceiling",
    "ceiling_hit": "max_scene_objects",
    "objects_omitted": 31389
  },
  "arcs": [],
  "vias": [ "... all 1003 of them ..." ],
  "zones": [ "... all 5 ..." ]
}
```

`observation` has exactly one permitted value. There is no spelling of this object that means
"observed and empty", which is the point: after this change `vias: []` says one thing only — the
region holds no vias. [ADR-0088](../adr/0088-complete-or-withheld-scene-kinds.md) carries the
argument, including why a per-kind *count* beside the array was rejected.

**What breaks:** a client with a closed schema that types `static.pads` and the other eight kinds
as arrays stops validating a truncated response. Every kind is now `array | withheld-object`.

Two second-order effects worth knowing before you meet them:

- **A truncated scene returns fewer objects than it used to.** A withheld kind frees its slots and
  nothing else fills them. On the densest board measured, `objects_returned` went from 2,000 to
  1,792 — and the 1,792 now include every footprint, pad, via, zone and the net class, where the
  2,000 included 1,217 of 31,181 segments and none of the rest.
- **Which kinds are withheld depends on the board.** Kinds are offered the budget smallest first,
  so adding objects can change which kind stops fitting. Output stays deterministic for a given
  board revision and request; it was never stable across revisions.

To migrate:

1. **Branch on the type wherever your client reads a scene kind as a list** —
   `scene["mutable"]["vias"]`, `scene.static.pads`, and so on:

   ```python
   vias = scene["mutable"]["vias"]
   if not isinstance(vias, list):
       raise NeedsSmallerRegion(vias["objects_omitted"])  # how many you are not seeing
   ```

   You do not need a fallback for "empty because truncated" any more; that state no longer exists.
   An empty list is an empty region.
2. **Widen any generated or hand-written schema** for the nine kinds to a union of the array and
   the withheld object. `CircuitSceneToolResponse` in `copper_mcp.mcp_contracts` is the
   authoritative shape and `SceneWithheldKindContract` is the new member.
3. **On a withheld kind, re-request a bounded region rather than raising the ceiling.** A
   whole-board request still returns the outline — one object, admitted first — which is what you
   need in order to choose a window. Raising `COPPER_MCP_MAX_SCENE_OBJECTS` is a legitimate
   deployment decision, but it does not remove the failure mode: at any ceiling below a board's
   object count some kind stops fitting, and the largest board measured would need 33,181.
4. **Accept `scene_version: "0.3.0"`** wherever you pin it. There is no compatibility mode; a
   deployment serves one scene version.

`max_scene_objects` keeps its 2,000 default. This release deliberately does not raise it: the
defect was never the ceiling's height, and raising it only moves the first board that reproduces it.

## 5. `ROUTER_VERSION` is `astar-grid/0.7.0`, so stored route candidates stop verifying

This is the only change in 0.7.0 that invalidates data you already hold.

The single-layer router's obstacle model is now scoped to a routing region and its one budget has
become three ([ADR-0089](../adr/0089-region-scoped-obstacle-model.md), #128; the mechanics are in
§6). A candidate records the settings it was computed under and the work its search performed, and
both moved, so `ROUTER_VERSION` advances from `astar-grid/0.6.0` to `astar-grid/0.7.0` and every
address derived from it moves with it.

**No path geometry changed anywhere.** The two-pad golden fixture, the NE5532 fixture, and all
twenty SimpleRouteJson corpus boards replay with identical vertices, wire length, and bend counts.
The addresses move because the recorded inputs moved, not because the router routes differently.
The version bump is what says so, rather than leaving it to be discovered by a caller whose stored
ID silently stopped reproducing.

What moves:

- `candidate_id` for every single-layer route candidate.
- `bundle_id` for every bundle containing one.
- Any exported candidate artifact whose digest was taken over those fields.

What does not move: Board IR snapshot and constraint digests, placement candidate IDs, Circuit
Scene revisions, policy digests, net reference IDs, and `LAYERED_ROUTER_VERSION`. The layered
router keeps its own budgets and is untouched by this change.

To migrate:

1. **Re-run the preview for any stored `candidate_id`, `bundle_id`, or exported candidate.** The
   old value will not reproduce. Treat a mismatch across this version boundary as expected rather
   than as tampering, exactly as [the 0.6.0 note](copper-mcp-0.6.0.md) says for a rendered-artifact
   digest.
2. **Do not re-pin an old ID by pinning old settings.** Candidates recorded under `astar-grid/0.4.0`
   through `0.6.0` still select their historical search behaviour for replay, but their recorded
   settings predate `region_margin_nm`. On a board larger than the routing region a replay
   therefore does not reproduce byte-for-byte, and the replay path is for reading history, not for
   minting addresses that verify.

## 6. One routing budget becomes three codes, and an exhausted region refuses `no_path_in_region`

`AStarSettings.max_obstacles` was charging three unrelated populations under one code. Measured
read-only against a live audio-project tree, 0 of 385 net previews routed at default settings and
93 refused `obstacle_budget_exceeded`; 61 of those 93 were the routed net's **own** copper — the
model that decides whether the net is already connected — charged to a budget named for the copper
it must avoid ([ADR-0089](../adr/0089-region-scoped-obstacle-model.md),
[the calibration note](../research/route-obstacle-budget-calibration-v1.md)).

| Setting | 0.6.0 | 0.7.0 | Ceiling | Refuses under |
|---|---:|---:|---:|---|
| `max_obstacles` | 256 | 4,096 | 4,096 → 32,768 | `obstacle_budget_exceeded` |
| `max_net_objects` | — | 1,024 | 4,096 | `net_object_budget_exceeded` |
| `max_obstacle_checks` | — | 2,000,000 | 10,000,000 | `obstacle_check_budget_exceeded` |
| `region_margin_nm` | — | 10,000,000 (10 mm) | 1 nm – 1 m | `no_path_in_region` |

**Any caller matching `obstacle_budget_exceeded` to mean "some routing budget ran out" stops
seeing two of the three cases.** Before, a route whose own net carried 800 objects and a route
that evaluated three million geometric predicates both refused `obstacle_budget_exceeded`. After,
the first refuses `net_object_budget_exceeded` and the second `obstacle_check_budget_exceeded`.
There is no shared prefix here — unlike the `budget.exceeded.` family in §1 these are three flat
codes — so a caller that needs "any budget" must match the set.

**`no_path` no longer covers a search that exhausted inside a scoped region.** `no_path` is a proof
about the modelled board; when the region is a proper subset of the board the honest claim is about
the region, and the caller's recourse is different — a wider `region_margin_nm` rather than a
different net or a different layer. On the external corpus 9 of 11 `no_path` refusals became
`no_path_in_region`.

Each of these messages names the budget and its configured value, and deliberately **not** the
observed count, which would disclose board density to a caller that may not hold the board. Do not
write a caller that parses a count out of the message; there is none.

To migrate:

1. **Widen every match on `obstacle_budget_exceeded`** to the three codes above, and decide which
   recourse each one gets. Raising `max_obstacles` does nothing for a `net_object_budget_exceeded`.
2. **Widen every match on `no_path`** to include `no_path_in_region` if the caller treats it as
   "this net cannot be routed". If the caller instead reports unroutability to a user, keep them
   separate: `no_path_in_region` has a recourse and `no_path` does not.
3. **Decide the ceiling raise rather than accepting it.** `max_obstacles` moving from 256 to 4,096
   is a change to a denial-of-service control ([SEC-132](../ledgers/security-ledger.md)). On the
   largest real board the worst net takes 1.87 s and 45.5 MiB at the new default, against 1.95 s
   and the same 45.5 MiB with the model unscoped at the 32,768 ceiling: peak memory is dominated by
   the parsed snapshot, not the obstacle list. Adversarial input packed to 60,000 objects inside
   the region refuses in 1.5 s at the default and 1.7 s at the ceiling, and Board IR's own parse
   budgets refuse the snapshot entirely past roughly 90,000. If that is not acceptable, set
   `max_obstacles` back to 256 — the scoping is what buys the coverage, not the number.
4. **Know that a wide detour can now be refused where it was previously searched for.** A route
   whose only legal path leaves the region is refused. On the external corpus this cost nothing
   measurable, but the possibility is real and is why the margin is a setting rather than a
   constant ([R-133](../ledgers/risk-register.md)).

## 7. Every root Board IR refusal moves off `kicad_pcb.unsupported`

A root-level `(group …)` — what KiCad writes whenever a designer groups a selection — refused an
entire board under a message and a locator that named nothing
([ADR-0090](../adr/0090-root-level-board-groups.md), #129):

```text
code:    unsupported.construct
message: root expression contains an unsupported semantic construct
locator: kicad_pcb.unsupported
```

`kicad_pcb.unsupported` was a constant. It is now `kicad_pcb.child[N]`, where `N` is the index of
the offending root child computed from the parse and never read from the document — and this
applies to **every** root refusal, not only to groups:

| | 0.6.0 | 0.7.0 |
|---|---|---|
| Unlocked root `(group …)` | refused | converts; counted in `ConversionResult.unmodelled_group_count` |
| Locked root `(group …)` | refused, unnamed | refused: `a locked group locks its members and is unsupported`, `object_kind: group` |
| Root `(property …)`, `(dimension …)`, `(image …)` | refused, `kicad_pcb.unsupported` | refused by name, `kicad_pcb.child[N]` |
| Any other unmodelled root head | refused, `kicad_pcb.unsupported` | refused unnamed, `kicad_pcb.child[N]` |

**Any caller matching the exact locator string `kicad_pcb.unsupported` stops matching**, including
one that only used it to group refusals for display. The diagnostic *code* is unchanged
(`unsupported.construct`) and is the stable thing to match on.

A locked group is refused deliberately, and the outcome for such a board is unchanged — it did not
convert before and does not convert now — but the reason it gives is now the true one.
`BOARD_ITEM::IsLocked()` derives an item's lock from its parent group, so a locked group locks
every member transitively without any member's own s-expression saying so; lock is a hard
authorization gate in CopperMCP, and reading one past would have converted members at
`locked=False` and authorized a move KiCad forbids.

To migrate:

1. Replace any equality test against `kicad_pcb.unsupported` with a test on the `unsupported.construct`
   code, and treat the locator as text for an operator rather than as a key.
2. If you read `ConversionResult`, note the new `unmodelled_group_count` field. It is a **count and
   not a diagnostic**, because every caller of `parse_kicad_bytes` treats a non-empty `diagnostics`
   tuple as a refusal and a warning would refuse the board this change exists to admit. A non-zero
   value means the board carried grouping that Board IR does not model: a caller may move one member
   of a group and break an intent nothing told it about ([R-134](../ledgers/risk-register.md)).
3. Expect **no content address to move**. A board with no group converts identically, and a board
   with one previously produced no snapshot at all, so there is nothing whose digest could change.

## 8. Seven pad refusals that could never fire now fire, with different text

A pad's `(zone_connect N)` was refused outright, grouped with seven other pad fields. It is now
accepted for the values that *attach* the pad to its pour and refused for the one that detaches it
([ADR-0091](../adr/0091-attaching-pad-zone-connect-overrides.md), #124). `zone_connect` derives no
copper: it is an input to KiCad's own zone filler, the finished fill is intersected with the zone's
own extents for every value, and the filler is the only thing that turns the value into copper.

| Pad token | 0.6.0 | 0.7.0 |
|---|---|---|
| `(zone_connect 1\|2\|3)` | refused | **accepted**, modelled as nothing |
| `(zone_connect 0)` | refused | refused: `pad zone_connect 0 detaches the pad from its pour and is unsupported` |
| `(zone_connect 4)`, `-1`, `01`, `yes`, `"2"` | refused | refused: `pad zone connection mode is unsupported` |

`0` refuses even where the loss would be provably harmless, because a value-and-context-dependent
rule is not worth the surface and over-refusal is the conservative direction. The value domain is
checked as an exact token rather than parsed as an integer, because KiCad's own parser casts it
with an unchecked `(ZONE_CONNECTION) parseInt(...)`.

**The part that breaks a caller is the side effect.** Seven named pad refusals were *unreachable*:
the closed pad allowlist ran first and rejected the same heads under one generic message. The named
checks now run **before** the allowlist, so each of these seven fields refuses under its own
sentence:

| Pad field | 0.6.0 message | 0.7.0 message |
|---|---|---|
| `clearance`, `offset`, `options`, `primitives`, `thermal_bridge_angle`, `thermal_bridge_width`, `thermal_gap` | `expression contains an unsupported semantic field` | `pad field '<name>' is unsupported` |

**Any caller matching the exact string `expression contains an unsupported semantic field` in order
to detect one of these seven stops matching.** The diagnostic code is `unsupported.construct` before
and after, `object_kind` is `pad` before and after, and the set of boards accepted and refused is
otherwise unchanged — only the sentence moved. The generic message still exists for every *other*
unknown pad head, so a caller matching it is now matching a strictly smaller set.

To migrate: match `unsupported.construct` plus `object_kind: pad` and read the message for a human,
or match the seven new sentences if the caller needs to name the field. Do not treat the generic
message's disappearance for these seven as a behavior change in what converts; it is not.

Board IR gains no pad-level zone-connection field. The converted content of a board carrying `1`,
`2` or `3` is identical to the same board without it in every field except `source.revision`, so no
pinned identity moves and the Board IR schema version does not bump. That equality is a no-op
measurement and not a safety argument — it would hold for any accepted value, `0` included.
Acceptance is sound only while pad-to-pour connectivity is derived exclusively from verified fill
geometry, which is true today and enforced by no type
([R-135](../ledgers/risk-register.md)).

## 9. The published DRC summary schema now pins two derived booleans

`schemas/drc-summary.schema.json` is the artifact a third party downloads, and it disagreed with
`DrcSummary.to_dict()` in both directions ([D-180](../ledgers/decision-ledger.md),
[R-137](../ledgers/risk-register.md), #11). Both are fixed, and the two fixes move validation in
opposite directions:

- **A payload that used to fail now validates.** The schema set `"additionalProperties": false` and
  never declared `clean`, while `to_dict()` has always emitted it — so every DRC payload CopperMCP
  published failed CopperMCP's own published schema. `clean` is now declared, and declared
  **required**, matching the closed `RouteDrcSummaryContract` at the same pinned `schema_version`
  of `1.0`.
- **A payload that used to validate may now be rejected.** Typed only as `boolean`, a payload
  carrying `warning_count: 1` beside `clean: true` validated, while `DrcSummary` and the MCP
  contract both refuse it — a false claim about a board, reachable through the published contract.
  `passed` had the identical hole. Both are now pinned in **both directions** with `if`/`then`/`else`
  over `const`: `passed` is true exactly when `error_count` and `unconnected_count` are `0`, and
  `clean` is true exactly when all five counts are `0` **and** `violation_type_counts` is empty.

To migrate:

1. **Re-validate any stored DRC summary payload you did not emit from CopperMCP.** A payload from a
   hand-built fixture, a rewriting transport, or a third-party producer that carries a `clean` or
   `passed` value inconsistent with its own counts now fails, where before it passed. A payload
   CopperMCP emitted has always been consistent, so it validates.
2. **Drop any local workaround that stripped `clean` before validating.** It now makes the payload
   fail the required-fields check.
3. **Know one condition that is easy to get wrong when re-deriving these yourself.** A
   present-but-zero entry in `violation_type_counts` is `passed` and **not** `clean`. A consumer that
   re-derives `clean` from the five counts alone will disagree with the schema and with the model.
4. **Do not read this as full agreement between the schema and the model.** JSON Schema has no
   arithmetic over sibling values, so the model's remaining requirement — that the violation-type
   counts sum to `error + warning + exclusion + unconnected` — is not expressible and is not
   enforced by the schema file. It is stated in the schema's own `$comment` rather than left
   implied.

No content address moves: schema files are hashed into no digest, and `to_dict()` is unchanged byte
for byte.

## What does not require migration

- **Golden identity pins, with one exception that has its own section.** `tests/test_golden_identities.py`
  records exactly one set of moved pins in this release — the route candidate identities of §5,
  with that reason attached — and every other pin is unchanged and passing. Parse budgets bound
  what is *admitted*, never what is written, so no Board IR snapshot or constraint digest, placement
  candidate ID, scene revision, or export digest moves for the changes in §1–§3. The scene change in
  §4 does not move one either: `board_revision` is a hash of the board bytes and `snapshot_digest`
  is the Board IR snapshot's, and neither depends on how the response is shaped. Neither do §7, §8
  or §9: a board with no group converts identically and a board with one produced no snapshot at
  all, an accepted `zone_connect` is modelled as nothing, and schema files are hashed into no digest.
- **The bounded-region scene path.** Eleven of eleven real boards returned a 5 mm window with
  `objects_omitted: 0` and every kind present as a complete array, before and after §4.
- **Net-tie footprints, which now convert.** A footprint carrying `net_tie_pad_groups` refused
  the whole board under 0.6.0, so no 0.6.0 deployment can hold a snapshot, candidate, or digest
  derived from one and nothing it holds stops verifying
  ([ADR-0092](../adr/0092-net-tie-copper-as-netless-obstacle.md), #116). Two consequences are worth
  knowing before you meet them, neither of which is a migration step. Each filled tie polygon
  becomes a netless obstacle `Segment` with a revision-derived identity, and both source-preserving
  patch adapters already refuse any snapshot carrying a `:derived:` identity — so a net-tie board is
  observable and previewable and **never patchable**, which is the first board class where
  "converts" does not imply "can be written back". And the tied nets deliberately report
  *unconnected* through the tie, because a joined-nets edge cannot be test-bound from the file
  alone; a caller asking whether they are connected is told less than the board knows, and a router
  may propose joining them with new copper elsewhere that authoritative KiCad DRC would then flag
  ([R-136](../ledgers/risk-register.md)). Boards without net ties are byte-identical.
- **`off_grid` route diagnostics, which gain typed evidence without gaining a required key.** An
  `off_grid` refusal now carries an `OffGridEvidence` object — the off-lattice pad, its lattice
  anchor, `grid_step_nm` in use, the signed per-axis nanometres from the nearest lattice line to
  the pad centre, and `largest_representable_step_nm`
  ([ADR-0093](../adr/0093-actionable-off-grid-refusals.md), #136). The published field is
  `off_grid: OffGridEvidenceContract | None = None` and is **deliberately not required**, which is
  what makes this additive: a stored `no_path` or `stale_revision` diagnostic recorded before this
  release has no such key, and requiredness would have invalidated every one of them. The
  anti-strip property people reach for requiredness to get is provided instead by a
  presence↔code biconditional — a payload carrying `code: "off_grid"` without evidence is refused,
  because an absent key resolves to the default and the biconditional then fires — and that
  biconditional is checked in the published contract as well as at runtime, so validating against
  the schema alone accepts nothing the service would refuse to build. The general rule, if you are
  making the same choice: a default is the right shape whenever the invariant is *relational*
  rather than *presence-based*. Routing semantics are untouched **by this change** — the lattice,
  the search, `ROUTER_VERSION` (already at `astar-grid/0.7.0` from §5), and all 385 real-board
  verdicts are byte-identical before and after it. One thing did change and is not a contract: the
  `off_grid` **message** now interpolates the numbers for a human reader. It is bounded at 256
  characters, it has never been a stable contract, and a caller matching its old text should read
  the typed object instead.
- **A board that converted under 0.6.0.** Raising a ceiling cannot turn an accepted board into a
  refused one, and neither can §7 or §8: an unlocked group and an attaching `zone_connect` widen
  what is accepted, and a locked group and `zone_connect 0` refused before this release too. Boards
  refused for a non-budget reason are refused under the **same diagnostic codes** as before. What
  did move for some of them is the locator (§7) and the message (§7, §8), which is why those are
  sections rather than lines here.
- **Every Board IR diagnostic code.** No code was added, removed, or repurposed on the board-reading
  side; only the `budget.exceeded` family changed shape (§1). The **route** failure codes are a
  different vocabulary and did change: two codes split out of `obstacle_budget_exceeded` and
  `no_path_in_region` is new. That is §6.
- **Tightening a budget below its shipped default** is supported and takes effect at every
  board-reading surface at once, because all of them now derive their limits from one seam.

No board, snapshot, candidate, or persisted artifact is rewritten by this upgrade. Nothing is
migrated in place and nothing is destroyed; the one thing you must re-derive rather than trust is a
stored route candidate identity, for the reason §5 gives.
