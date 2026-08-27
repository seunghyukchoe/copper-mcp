# ADR-0122: The stackup is read per field, because three of its fields are not about Z

- Status: Accepted
- Date: 2026-08-28
- Owners: `@seunghyukchoe`
- Related: [Issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188),
  [D-227](../ledgers/decision-ledger.md), [R-178](../ledgers/risk-register.md),
  [SEC-164](../ledgers/security-ledger.md),
  [B-130](../ledgers/benchmark-ledger.md) and B-131,
  [the setup-field research note](../research/kicad-setup-field-semantics-v1.md),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md) (invoked and found not to
  apply), [ADR-0099](0099-pad-fabrication-properties-and-named-pad-refusals.md) (the shape this follows)

## Context

[B-129](../ledgers/benchmark-ledger.md) measured `setup_semantics` as **6 of 10** public terminal
blockers — the largest single class — and could say nothing about what those boards actually
write, for a reason that is itself the finding: the adapter refuses the `setup` block through
`_reject_unknown_children`, whose diagnostic names the **block**, `kicad_pcb.setup`, and no field.
B-129 classified that terminal as `unmaskable` not because the construct was unmaskable but
because *the diagnostic did not locate anything a per-field mask could delete*. A refusal that
names a container is a refusal a measuring instrument cannot decompose.

[B-130](../ledgers/benchmark-ledger.md) decomposed it by reading the boards directly, and the
surface came back **closed**: `other` is 0 at all three levels, so nothing outside the predeclared
vocabulary appears. Four heads stand between those six boards and this gate.

| Head | Boards (of 6) | Occurrences |
|---|---|---|
| `stackup` | 6 | 6 |
| `grid_origin` | 3 | 3 |
| `pad_to_paste_clearance_ratio` | 2 | 2 |
| `aux_axis_origin` | 1 | 1 |
| `pad_to_paste_clearance` | 0 | 0 |
| anything else (`other`) | **0** | **0** |

Inside the six stackups: 86 layer entries, and three child heads only — `layer`, `copper_finish`,
`dielectric_constraints`. `edge_plating`, `castellated_pads` and `edge_connector` occur **0 times
on 0 boards**.

## Decision

**Accept `stackup`, `grid_origin`, `aux_axis_origin`, `pad_to_paste_clearance` and
`pad_to_paste_clearance_ratio` as typed non-claims disclosed through `unmodelled_counts`. Read the
stackup through a closed nested grammar rather than as a unit, and refuse `edge_plating`,
`edge_connector` and `castellated_pads` at their own field unless explicitly neutral. Do not move
the Board IR schema version.**

### The rule the per-field decision is made under

> A field that constrains copper geometry, copper extent, or electrical clearance cannot be read
> past. A field that does not may be accepted as a typed non-claim, and the acceptance must be
> disclosed by a count rather than by silence.

Under-approximating copper — reporting less than the board carries — is the direction that lets a
router place metal where metal already is. Over-refusing costs a board. The two are not
symmetric and the decision is made in that asymmetry, per field.

### Why the whole block cannot simply be accepted

`setup` is not one thing. It mixes editor state, fabrication-output preference, via fabrication
treatment that this adapter *already* refuses through `_validate_neutral_via_treatment`, and a
stackup whose own children divide the same way again. A block-level allowlist entry for `stackup`
would have admitted `edge_plating` and `edge_connector` with it, which is the whole reason the
grammar is nested rather than flat.

### `stackup`: Z is not XY, with one exception that is already closed

The stack describes the board perpendicular to everything Board IR models — order, thickness,
material, `epsilon_r`, `loss_tangent`, finish, colour. The copper layer *set* is not read from
here either: KiCad's stackup parser matches each `(layer "NAME")` against the already-enabled
layers, and `BuildDefaultStackupList` reads `GetEnabledLayers()`/`GetCopperLayerCount()`, so the
stack is derived **from** the `(layers …)` section this adapter already validates.

**One path makes a `thickness` load-bearing for copper, and this decision rests on that path
already being closed.** In KiCad 10 a pad or via carrying `front_post_machining` or
`back_post_machining` has its per-layer copper knocked out — or its countersink diameter computed
— by comparing the machining depth against `BOARD_STACKUP::GetLayerDistance()`, and that result
reaches `GetEffectiveShape()`, every DRC clearance test, `zone_filler.cpp` and connectivity.
Accepting such a pad *while* reading the stack past would report copper larger than the board's.
It cannot happen: both tokens are in `_UNSUPPORTED_PAD_FIELDS` and the via head allowlist refuses
them. **That is a premise, not a coincidence**, so it is stated here and pinned by a test on both
object kinds. Two facts a reviewer should not have to re-derive: the coupling is KiCad-10-only
(`grep -rn PostMachining` over the `9.0` tree returns nothing), and plain `backdrill` and
`tertiary_drill` use explicit layer ranges and are *not* stackup-dependent.

### The three edge attributes: deliberate over-refusal, with the counter-evidence recorded

`edge_plating`, `edge_connector` and `castellated_pads` refuse unless explicitly `no`.

**The counter-evidence is strong and is not hidden.** KiCad derives no geometry from any of the
three. A whole-tree grep for `m_EdgePlating` and `m_EdgeConnectorConstraints` returns only the
Gerber job-file writer, the stackup text report, the board-characteristics table, the GUI panel,
the stackup class and the parser — **zero hits in `pcbnew/drc/`, in any plotter, in
`zone_filler.cpp`, in the 3D viewer or in the STEP exporter** — and both the 9.0 and 10.0 manuals
say these settings "only impact the board attributes output as part of Gerber job files at this
time". On KiCad's own model, reading them past loses nothing.

**Consistency with an existing decision settles it anyway.** `_validate_neutral_via_treatment`
already refuses `capping` and `filling` unless `no`, and non-default `tenting`/`covering`/
`plugging`, under the docstring "Reject board-level or per-via fabrication treatment that Board IR
omits". Via capping is likewise conductive fabrication treatment KiCad derives no geometry from.
Plated board edges and edge-connector bevels are the same class of claim about the *physical*
board, and `bevelled` additionally asserts material removed from the outline. Accepting them here
while refusing capping there would be an inconsistency with no argument behind it.

**The price is measured, not assumed:** B-130 counts all three at 0 occurrences on 0 boards of the
cohort, and `castellated_pads` is additionally moot at the one board format version this adapter
accepts — KiCad 10 removed the member (commit `09e1fca7e4`), never emits the token, and derives
castellation from the pad `pad_prop_castellated` property, which this adapter already refuses.
The stackup flag is kept in the grammar to fail closed on a hand-edited or third-party document,
not because KiCad 10 can write one.

### The origins and the paste pair

`grid_origin` is the editor's grid anchor: `///< origin for grid offsets`, read by the draw frame
and grid helper, and no board object's stored position depends on it. `aux_axis_origin` is
`///< origin for plot exports`, read only by exporters. Neither moves anything. The honest caveat
is stated rather than buried: `aux_axis_origin` *is* the offset KiCad applies when writing drill,
place and Gerber output, so a consumer generating fabrication output from a snapshot alone would
emit it in the wrong frame — which is what the count discloses.

`pad_to_paste_clearance` and `pad_to_paste_clearance_ratio` adjust stencil apertures only. KiCad's
own proof is `FOOTPRINT::TransformPadsToPolySet()`: it adds the mask expansion under
`case F_Mask: case B_Mask:`, the paste margin under `case F_Paste: case B_Paste:`, and `default:`
— every copper layer — breaks with no adjustment. This is the argument the already-accepted
`pad_to_mask_clearance` and `solder_mask_min_width` stand on, applied to their paste twins. All
four do feed DRC, but only mask-domain and paste-domain tests; the non-claim covers soldermask and
solderpaste geometry **and** the DRC over them.

### `zone_defaults` is refused, and the absence is named

KiCad 10's `(zone_defaults … (hatch_position …))` carries the default hatch *phase* of a hatched
zone fill. That is copper geometry for anything that re-fills, so it is not accepted. It is listed
in `_REFUSED_SETUP_HEADS_ON_RECORD` so a reader sees a decision rather than an omission, and
B-130's `other` bucket — which measured 0 — is the instrument that would have reported a board
carrying one.

### ADR-0105 is invoked and does not apply, and the reasoning is stated rather than assumed

ADR-0105's rule is that *a published schema's accepted set may change only when its declared
version changes*, where the accepted set is "the property names of every object, its
`additionalProperties` setting, its `required` list, every `enum`, every `const`, and every union
`type`", over `schemas/**/*.json`.

**Nothing here touches that.** No field is added to `BoardIRSnapshot`; the five accepted heads are
validated and discarded, and the two new numbers are `ConversionResult` counters published through
`BoardIrSummary.unmodelled_counts`, which is an MCP summary and is not described by any file under
`schemas/`. `schemas/board-ir/0.4.0.schema.json` is byte-unchanged and
`BOARD_IR_SCHEMA_VERSION` stays `0.4.0`. `scripts/check_schema_sets.py` runs in `make lint` and is
the mechanical confirmation, with **no exemption added**.

This is exactly the `thermal_bridge_angle` precedent (D-205, B-115): a validated field accepted as
a typed non-claim, discarded from Board IR, counted, published to MCP, and no version moved. The
contrast that makes the line real is #192's custom pads, which *did* add modelled Board IR
geometry and therefore did move `0.3.0` → `0.4.0`. The test is not "did the adapter start
accepting something", it is "did the emitted document's accepted set change".

## Consequences

- **The `setup` refusal becomes locatable.** A board carrying an unaccepted `setup` field still
  refuses at `kicad_pcb.setup`, but the three edge attributes now refuse at
  `kicad_pcb.setup.stackup.<field>`, and a malformed stackup layer at
  `kicad_pcb.setup.stackup.layer[N]`. B-129's `unmaskable` classification of this class was a
  property of the diagnostic, and it is partly repaired.
- **Two counts join `unmodelled_counts`**, `unmodelled_setup_field_count` and
  `unmodelled_stackup_layer_count`, taking the published map from six entries to eight. This is a
  visible MCP surface change and the migration note carries it.
- **The census instrument is spent, deliberately, and says so.**
  `benchmark_public_setup_field_census.ACCEPTED_SETUP_HEADS` was a live mirror of the adapter's
  accepted set; it is now frozen at the value B-130 was taken against, and the drift guard becomes
  a **containment** check — the adapter may widen, which is what this decision does, but a head
  accepted at B-130 and refused later invalidates the artifact's reading and fails the run. A
  rerun therefore still reproduces B-130 rather than silently answering a different question.
- **Every accepted field carries a closed payload grammar: no nested child, exact arity, checked
  token kind, refused at the field's own locator.** *Amended 2026-08-28, before merge, after
  review of #225 (thread `PRRT_kwDOTrPIR86c8Hfh`).* The first draft of this record closed the
  *head* allowlists at three levels and left every leaf's payload unread, which defeated its own
  purpose: a head allowlist constrains **which** children may appear and says nothing about what
  nests inside one, so `(grid_origin (zone_defaults ...))` smuggled past the very head this record
  names as deliberately refused. All thirteen malformation classes — nested children, wrong arity,
  non-numeric origins and constants, a quoted `"no"` where a bare flag is meant — were accepted at
  every level, the nested stackup grammar included. **A counted non-claim is a validated
  construct**: acceptance means "well formed and deliberately not modelled", never "bytes nobody
  read". The grammars are in `_SETUP_SCALAR_PAYLOADS`, `_STACKUP_SCALAR_PAYLOADS` and
  `_STACKUP_LAYER_PAYLOADS`, and they can only over-refuse, because every value they check is
  discarded.
- **A repeated accepted field is an ambiguous document, uniformly.** This record's first draft
  documented an asymmetry — a repeated `stackup` refused, a repeated `grid_origin` converted and
  counted two — and pinned it. Closing the payload grammars **removed** it: every accepted field
  now resolves through `child()`, so any duplicate refuses with `syntax.duplicate_field` and
  publishes no partial count. The asymmetry was a consequence of the counted heads being
  unvalidated, not a decision; it is recorded here as retired rather than deleted.
- **No board's first refusal moves on the public cohort.** Every one of the ten refuses in front
  of `setup`. This decision is measurable only behind B-129's masks, and B-131 records that
  differential rather than claiming a conversion this slice did not produce.

## Alternatives considered

- **Accept the whole `setup` block, or `stackup` as a unit.** Rejected: it admits `edge_plating`
  and `edge_connector` by the same token, which is the one thing in the block that could be an
  under-approximation. A closed nested grammar costs a few lines and closes that.
- **Accept the three edge attributes as counted non-claims too**, which KiCad's own model
  supports. Rejected on consistency with `_validate_neutral_via_treatment`, above — and the
  measured price of refusing is zero boards on this cohort, so nothing is bought by the
  inconsistency.
- **Model the stackup in Board IR** — layer thicknesses, `epsilon_r`, `loss_tangent` — which would
  make impedance work possible. Rejected here, not forever: it adds modelled fields and therefore
  *would* move the schema under ADR-0105, and nothing measured today needs it. Carried as the
  named residual in R-178 rather than left implicit.
- **Refuse `aux_axis_origin` because fabrication output is offset by it.** Rejected: Board IR is
  in absolute board coordinates and claims nothing about fab-output frames, so the refusal would
  buy no soundness and cost a board on the cohort. The count is the honest answer.
- **Widen the census instrument's mirror instead of freezing it**, so it keeps running against the
  live accepted set. Rejected: it would make a rerun collapse `unsupported_head_sets` to `none`
  and silently answer a different question with the same schema name, which is the failure
  ADR-0105 is about in a different file.

## References

- [The setup-field research note](../research/kicad-setup-field-semantics-v1.md) — every citation
  above, with paths and line numbers from KiCad branches `9.0` and `10.0`
- [B-130](../ledgers/benchmark-ledger.md), the closed field census; B-131, the differential
- [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md), invoked and found not to apply
- [ADR-0099](0099-pad-fabrication-properties-and-named-pad-refusals.md), the typed-non-claim shape this
  follows
- [D-227](../ledgers/decision-ledger.md), [R-178](../ledgers/risk-register.md),
  [SEC-164](../ledgers/security-ledger.md)
