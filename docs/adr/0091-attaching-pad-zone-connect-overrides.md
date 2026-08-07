# ADR-0091: Accept the pad zone-connection overrides that attach, refuse the one that detaches

- Status: Accepted
- Date: 2026-08-08
- Owners: `@seunghyukchoe`
- Related: [Issue #124](https://github.com/seunghyukchoe/copper-mcp/issues/124), ADR-0013,
  ADR-0016, ADR-0020, ADR-0021, ADR-0039, ADR-0070,
  [KiCad pad zone_connect research](../research/kicad-pad-zone-connect-v1.md)

## Context

A KiCad pad may carry `(zone_connect N)`, a per-pad override of how a same-net copper pour
attaches to it. The Board IR adapter refused it, grouped with `clearance`, `offset`, `options`,
`primitives`, `thermal_bridge_angle`, `thermal_bridge_width` and `thermal_gap`. One real board,
`phono-preamp/tier1-rev-a`, carries it on five pads — all `zone_connect 2` — and it is the last
cause blocking that board. No other board in the twelve-board survey carries the field at all.

The grouping is defensible for the other seven and wrong for this one, and the difference is
what the field *is*. `clearance` overrides the clearance the router must honour; `offset` and
`primitives` change the pad's own shape. Each makes Board IR describe copper it cannot derive.
`zone_connect` derives nothing: it is an input to KiCad's own zone filler, and the filler is the
only thing that turns it into copper.

An earlier attempt simply added `zone_connect` to the pad allowlist and was reverted, correctly:
a bare allowlist entry accepts all four values, and one of them is not safe. This record is the
argument that separates them.

### What the field provably does

Established from KiCad's format definition and from KiCad 10's own filler and DRC engine; every
citation is in the [research note](../research/kicad-pad-zone-connect-v1.md).

The value is KiCad's `ZONE_CONNECTION` enum: `0` none, `1` thermal relief, `2` solid fill,
`3` through-hole thermal. `-1` (`INHERITED`) is never written — an absent token *is* inheritance
— and resolution runs **pad override first**, then custom DRC rules, then the footprint override,
then the zone's `connect_pads`. The pad's own override returns from `DRC_ENGINE::EvalRules` before
any rule iteration, so a custom rule cannot override it. `3` resolves to `1` on a plated
through-hole pad and to `2` on any other.

In `ZONE_FILLER::knockoutThermalReliefs` the resolved value selects one of three treatments:
`THERMAL` knocks out a thermal-gap annulus and adds spokes back, `NONE` knocks the pad out with
clearance, `FULL` knocks nothing out. The finished fill is then intersected with the zone's own
extents, so **poured copper is a subset of the zone boundary for every value**. The field never
moves the pad, changes its shape, changes a clearance the router honours, or changes the zone
outline.

The filler is not the value's only *reader* — `drc_test_provider_zone_connections.cpp` reads it to
flag a starved thermal, and `board_inspection_tool.cpp` reads it to explain a connection in the UI.
Neither produces geometry. The claim this decision rests on is the narrower one that was actually
established: **the filler is the only thing that turns the value into copper.**

### Why the obstacle direction cannot break

Obstacles over-approximate. A zone converts as its boundary polygon (ADR-0013), or — only
against proved fill — as the exact islands (ADR-0039, ADR-0070). The boundary contains the fill
for every value, by the intersection above; and the exact-fill path uses KiCad's own recomputed
polygons, which already have `zone_connect` applied, behind a containment gate that proves the
replacement can only shrink. No value of `zone_connect` can put copper anywhere the current
obstacle model does not already cover.

### Why the connectivity direction breaks for exactly one value

Connectivity under-approximates. CopperMCP claims a pad is joined to a pour only through exact
integer contact between the pad's under-approximating core and a *verified* fill island
(ADR-0021), and a same-net zone on any layer without verified fill vetoes the claim outright.
So no live claim reads `zone_connect`; KiCad's polygon has already applied it.

But Board IR publishes a pad-to-pour attachment statement of its own: `Zone.pad_connection`,
parsed from the zone's `connect_pads` and carried into every snapshot and every snapshot digest.
(Circuit Scene does *not* carry it: `_zone_object` publishes boundary, net and clearances only.)
A pad's `zone_connect` overrides that statement for one pad, and the four values do not override
it in the same direction:

- `1`, `2` and `3` all **attach**, and discarding one never turns `Zone.pad_connection` into a
  claim of attachment where there is none. It can still make the published *mode* wrong in
  either direction — a zone declaring `solid` over a pad overridden to `1` overstates the copper,
  and a zone declaring `no` over a pad overridden to `2` understates it — but both readings still
  answer "attached", so no connection the board lacks is ever claimed. Nothing in this repository
  reads the field, and the same imprecision already exists on `main` with no pad token present at
  all, since a zone declaring `thermal` says nothing about whether its spokes survived.
- `0` **detaches**. Discarding it can leave Board IR publishing `thermal`, `solid` or
  `thru_hole_only` attachment over a pad its designer deliberately isolated. That is a claimed
  connection the board does not have — the one direction this project forbids — and no other
  Board IR field records it.

## Decision

**A pad `zone_connect` of `1`, `2` or `3` is accepted and modelled as nothing. Every other
value, `0` included, refuses by name.**

- Acceptance is an argued no-op, not support. Board IR gains no pad-level zone-connection field,
  and the converted content of a board carrying `1`, `2` or `3` is identical to the same board
  without it in every field except `source.revision`, which is the digest of the file bytes. A
  test asserts that whole-content equality rather than a pad-by-pad one.

  **That equality is not evidence of soundness.** The converter reads the token and propagates
  nothing, so the equality holds by construction for *any* value the converter accepts — it would
  hold identically if `0` were admitted. It establishes exactly three things: that acceptance
  changes no modelled geometry, that the Board IR schema is untouched, and that no pinned identity
  moves. Soundness rests entirely on the KiCad-domain argument above plus ADR-0021's rule that
  pad-to-pour attachment comes only from verified fill. The surviving mutant that admitted `0` to
  the accepted set proved the point: the equality test could not see it, and only the constant
  partition test could.
- `0` refuses **even when the zone itself says `no`** and the loss would be provably harmless.
  A value-and-context-dependent rule is not worth the surface, and over-refusal is the
  conservative direction.
- The value domain is checked as an exact token, not parsed as an integer. KiCad's own parser
  casts it with an unchecked `(ZONE_CONNECTION) parseInt(...)`, so `4`, `-1`, `01`, `yes` and a
  quoted `"2"` all refuse.
- `clearance`, `offset`, `options`, `primitives`, `thermal_bridge_angle`,
  `thermal_bridge_width` and `thermal_gap` keep refusing, unchanged.

### A refusal that could not say what it refused

Those seven named refusals were **unreachable**. The pad allowlist ran first and rejected the
same heads with "expression contains an unsupported semantic field", which names no field —
issue #124 quotes a message the adapter could not emit. The named check now runs *before* the
allowlist, so each says which field it refused, without opening the allowlist by one head. Seven
diagnostics are repaired as a side effect of reaching one of them.

## Consequences

- `phono-preamp/tier1-rev-a` loses its last `zone_connect` cause. Whether it converts depends on
  the rest of that board and is not claimed here.
- No pinned identity moves. The committed fixtures are unchanged, `Pad` is unchanged, the
  canonical encoding is unchanged, and the Board IR schema version does not bump.
- The acceptance is load-bearing on one property that is true today and is not enforced by a
  type: **pad-to-pour connectivity is derived only from verified fill geometry.** A future
  surface that infers it from a same-net pad sitting inside a zone outline would make accepting
  `1`, `2` and `3` unsound, because it would be reading an attachment mode Board IR discarded.
  R-135 carries that, and the docstring on `_require_attaching_pad_zone_connection` names it at
  the point of change.
- A board that isolates a pad from its pour still refuses, and now says so in one sentence
  instead of naming no field.

## Alternatives considered

- **Add `zone_connect` to the pad allowlist and accept all four values.** Rejected — this is the
  reverted attempt. It accepts `0`, which inverts the connectivity direction of error against
  `Zone.pad_connection`.
- **Model the override in Board IR as a per-pad `PadZoneConnection`, mirroring
  `Zone.pad_connection`.** This is the coherent long-run shape and issue #124 proposes it. It is
  rejected *for now* on cost, not on principle: a new field in `Pad` and in `canonical._pad()`
  moves `BOARD_IR_V02_SNAPSHOT_DIGEST` and its byte count, forces the committed
  `board-ir-v0.2/schema-valid.json` to be regenerated, and cascades into the route candidate,
  placement candidate, bundle, scene, render, job, manifest, export and attestation identities —
  a Board IR schema-version bump with a migration note, for a field nothing reads. Doing it
  behind this decision costs one refusal that is already correct; doing it now would spend a
  schema version on a field with no consumer. When a surface needs it, that is the change to
  make, and `0` becomes acceptable in the same change.
- **Keep refusing all four values.** Rejected as the honest-sounding answer that is not the true
  one. The refusal for `1`, `2` and `3` is not supported by either direction-of-error rule, and
  a refusal without an argument is how a tool acquires superstitions.
- **Accept `0` when the zone's own `connect_pads` is already `no`.** Rejected: provably harmless
  and not worth a rule whose correctness depends on which zone a pad happens to sit in, across
  layers and priorities.
- **Accept `0` and record it as an attachment veto without a full IR field** (for example, a
  refusal only when a same-net zone overlaps the pad). Rejected: it is a geometric claim about
  zone membership, which is exactly the inference this ADR is protecting against making
  implicitly.

## References

- [ADR-0013](0013-polygon-zone-obstacles.md), [ADR-0021](0021-zone-fill-authority.md),
  [ADR-0070](0070-layered-fill-aware-obstacles.md)
- [KiCad pad zone_connect research](../research/kicad-pad-zone-connect-v1.md)
- [Decision ledger D-178](../ledgers/decision-ledger.md),
  [Risk register R-135](../ledgers/risk-register.md)
