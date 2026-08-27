# Footprint field acceptance, and the widened meaning of `unmodelled_group_count`

Board IR now converts boards whose footprints declare schematic provenance, per-footprint
solder-mask and solder-paste defaults, an attaching zone-connection default, or a footprint-local
group. No schema version moves: `BOARD_IR_SCHEMA_VERSION` stays `0.4.0` and
`schemas/board-ir/0.4.0.schema.json` is byte-unchanged, because no accepted field enters
`BoardIRSnapshot` — each is validated and discarded (ADR-0105, D-228, ADR-0123).

## One published number changes meaning

`unmodelled_group_count`, in `inspect_board_ir`'s `unmodelled_counts` map, previously counted
**root** `(group …)` expressions only. It now counts **every** accepted `(group …)`, at board root
and inside a footprint.

KiCad parses and writes both through the same code — a footprint's group is dispatched to the same
`parseGROUP` and formatted by the same routine — so they are one construct, and one number answers
the only question the count exists for: *how many groupings did this conversion discard?* Two
counters would have answered neither.

**What a caller must do.** If you compare a stored `unmodelled_group_count` against a fresh one and
your board has footprint-local groups, expect the fresh number to be larger. Do not treat the
difference as new groups appearing on the board. Nothing else about the count changes: it is still
a cardinality of discarded editor organisation, still absent from any snapshot field, and still
zero for a refused conversion.

## One number is added

`unmodelled_footprint_field_count` joins `unmodelled_counts`, taking it from eight entries to nine.
It counts the `(footprint …)` children this change admits without modelling, across every footprint
on the board:

- `sheetfile` and `sheetname`
- `solder_mask_margin`, `solder_paste_margin`, `solder_paste_margin_ratio`, and the legacy
  `solder_paste_ratio` spelling KiCad 8 wrote

It deliberately does **not** count `group` (see above) or `zone_connect`, which is validated to be
inert for the attachment claim Board IR publishes rather than merely discarded.

A caller reading `unmodelled_counts` as a fixed-size map must widen it. The map is the single
disclosure surface for every measured non-claim; a client that enumerates its keys will see the new
one, and a client that reads keys by name is unaffected.

## What a snapshot alone no longer carries — stated as a loss

- **Per-footprint stencil and mask defaults.** A consumer generating fabrication output from Board
  IR alone will emit board-default apertures where the designer set per-footprint ones. The count
  is the disclosure of that, not its remedy.
- **The footprint's originating sheet.** Hierarchical-design provenance is gone from the snapshot.
- **The sharper case:** `sheetname` and footprint-local group membership are *selectors* a custom
  DRC rule can use — `memberOfSheet()`, `hasComponentClass()`, `memberOfGroup()` — to raise a
  clearance above the netclass value. CopperMCP does not read `.kicad_dru` at all and never has, so
  a rule-derived clearance was already invisible to it; this change widens the set of board content
  whose only route to copper runs through that unread file. See R-179.

## What still refuses, and now says why

Twenty further footprint heads from KiCad's own `parseFOOTPRINT` grammar refuse **by name** instead
of through the previous field-less sentence `footprint contains an unsupported semantic field`.
A refusal message you were matching on may therefore change from that sentence to
`footprint field '<name>' is unsupported`. **No board's verdict changes with this**: every one of
those heads already refused. Only the message and the reason moved.

Three refusals are new in substance rather than in wording:

- `(clearance …)` — at any value. It is a *replacement*, not a maximum: KiCad resolves it before
  custom-rule iteration, so it beats netclass and rules alike and can lower effective clearance to
  the board minimum, while sizing the void the copper pour leaves around every pad of the footprint.
- `(zone_connect 0)` — the only written mode that detaches a footprint's pads from their pour.
  `1`, `2` and `3` are accepted; anything outside that set, including a quoted `"2"` or a `-1`,
  refuses.
- A **locked** footprint-local group — as a locked root group already did. Lock propagates to
  members through KiCad's own query-time derivation, and lock is an authorization gate here.

A head KiCad's parser does not accept still refuses with the original field-less sentence, and no
token read from a board is ever interpolated into a message.

## What does not change

Apply authority, single-use tokens, board writes, revision checks, candidate identity, routing,
DRC, the CLI, and every existing snapshot field. No `check_schema_sets` exemption was added.
