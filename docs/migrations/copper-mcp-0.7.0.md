# Migrating a deployment to CopperMCP 0.7.0

CopperMCP 0.7.0 changes three behaviors a working 0.6.0 deployment can depend on without having
declared it: a Board IR conversion diagnostic code that callers match on, the spelling every
integer environment variable accepts, and the shape a Circuit Scene takes when it truncates. It
also raises four parser defaults, which is a deliberate change to a denial-of-service posture and
is worth a decision rather than an upgrade. Everything else is additive.

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

## What does not require migration

- **Golden identity pins.** `tests/test_golden_identities.py` is unchanged and passing. Parse
  budgets bound what is *admitted*, never what is written, so no candidate ID, bundle ID, placement
  candidate ID, Board IR snapshot or constraint digest, scene revision, or export digest moves. The
  scene change in §4 does not move one either: `board_revision` is a hash of the board bytes and
  `snapshot_digest` is the Board IR snapshot's, and neither depends on how the response is shaped.
- **The bounded-region scene path.** Eleven of eleven real boards returned a 5 mm window with
  `objects_omitted: 0` and every kind present as a complete array, before and after §4.
- **A board that converted under 0.6.0.** Raising a ceiling cannot turn an accepted board into a
  refused one. Boards that were refused for a non-budget reason — an unsupported version,
  construct, or topology — are refused identically and with the same codes.
- **Every other diagnostic code.** Only the `budget.exceeded` family changed shape.
- **Tightening a budget below its shipped default** is supported and takes effect at every
  board-reading surface at once, because all of them now derive their limits from one seam.

No board, snapshot, candidate, or persisted artifact is rewritten by this upgrade.
