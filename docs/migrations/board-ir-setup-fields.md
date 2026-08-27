# Board IR accepts five more `setup` fields, and discloses all five as non-claims

**There is no required deployer action, and the Board IR schema does not move.** This note exists
because two things change that a caller can see: some boards that were refused now convert, and
the `unmodelled_counts` map published by `inspect_board_ir` grows from six entries to eight.

Records: [`ADR-0122`](../adr/0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md),
[`D-227`](../ledgers/decision-ledger.md), [`R-178`](../ledgers/risk-register.md),
[`SEC-164`](../ledgers/security-ledger.md), [`B-130`](../ledgers/benchmark-ledger.md) and B-131,
[the setup-field research note](../research/kicad-setup-field-semantics-v1.md),
[issue #188](https://github.com/seunghyukchoe/copper-mcp/issues/188).

## What now converts

A board whose `(setup …)` block carries any of these no longer refuses:

| Field | What it is | Why reading it past is sound |
|---|---|---|
| `stackup` | the physical layer stack | describes the board in Z; the copper layer set comes from the root `(layers …)` section, which the adapter already reads |
| `aux_axis_origin` | the drill-and-place file origin | a *reporting* origin — no board object moves with it |
| `grid_origin` | the editor's grid anchor | pure editor state |
| `pad_to_paste_clearance` | solder-paste stencil aperture offset | reaches `F.Paste`/`B.Paste` only, never copper |
| `pad_to_paste_clearance_ratio` | the same, as a fraction of pad size | as above |

The last two are the paste twins of `pad_to_mask_clearance` and `solder_mask_min_width`, which
were already accepted on exactly this argument.

## What still refuses, and where the refusal now points

Inside an accepted `stackup`, three attributes still refuse unless explicitly `no`:
`edge_plating`, `edge_connector` and `castellated_pads`. **This is deliberate over-refusal.** KiCad
derives no geometry from any of the three — they reach only the Gerber job file and the fab
documentation tables — but they assert conductive or removed material at the board edge that no
pad, track, via, zone or graphic represents, which is the same claim `capping` and `filling`
already refuse under. B-130 measured all three at zero occurrences on the cohort, so the price is
recorded rather than guessed.

KiCad 10's `(zone_defaults …)` also still refuses: it carries a hatched zone fill's default phase,
which is copper geometry for anything that re-fills.

**The refusal locator moves, and this is the one thing a diagnostic-reading caller should note.**
An unaccepted direct `setup` child still refuses at `kicad_pcb.setup`. But a board-edge attribute
now refuses at `kicad_pcb.setup.stackup.edge_plating` (or `.edge_connector`,
`.castellated_pads`), and a malformed stack entry at `kicad_pcb.setup.stackup.layer[N]`. A caller
matching a locator against the exact string `kicad_pcb.setup` will stop matching those cases.
Refusal *messages* are not a contract and never were; the locator is more precise than it was, not
less.

## What a client sees in `unmodelled_counts`

Two new keys, present on every supported board, zeros included:

- `unmodelled_setup_field_count` — how many of the five fields above the board carried, counted as
  expressions.
- `unmodelled_stackup_layer_count` — how many `(layer …)` entries the accepted stackup held,
  dielectrics included. It is deliberately comparable against `copper_layer_ids`: the gap is the
  physical stack the snapshot does not carry.

A client that asserted `len(unmodelled_counts) == 6`, or compared the map against a hard-coded set
of six keys, must widen. A client that reads keys by name is unaffected.

## What Board IR still does not claim

This is the point of the counts, and it is a widening of an existing non-claim rather than a new
one.

- **No stackup is modelled.** Layer order, thickness, material, dielectric constant, loss tangent
  and surface finish are validated and discarded. Board IR supports no impedance, propagation
  delay, or board-thickness claim, and a consumer that rebuilds a board from a snapshot alone
  loses the whole physical stack.
- **No fabrication-output frame is claimed.** Board IR is in absolute board coordinates.
  `aux_axis_origin` is the offset KiCad applies when writing drill, place and Gerber output, so
  fabrication output generated from a snapshot alone would land in a different frame.
- **No solder paste or soldermask geometry is claimed**, and that now explicitly includes the DRC
  tests over them — mask bridging, mask-web width, silk-over-mask clearance and the paste-margin
  constraints. None of those touches copper geometry or copper-to-copper clearance.

## Verifying nothing else moved

`schemas/board-ir/0.4.0.schema.json` is byte-unchanged and `BOARD_IR_SCHEMA_VERSION` is still
`0.4.0`. Under [`ADR-0105`](../adr/0105-a-schema-version-moves-with-its-accepted-set.md) the
accepted set that governs a version is the *emitted document's*, and nothing is added to the
snapshot; the two counts travel on the MCP summary, which no file under `schemas/` describes.
`scripts/check_schema_sets.py` runs in `make lint` and passes with no exemption added — that is
the mechanical confirmation, not this paragraph. Persisted `0.4.0` envelopes decode unchanged and
no snapshot digest, constraint digest or golden identity moves.
