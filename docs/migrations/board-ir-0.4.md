# Migrating to Board IR 0.4

Board IR 0.4 widens the accepted set for custom pads by carrying an optional
`Pad.copper_envelope`. The field is a local, conservative axis-aligned envelope for copper
obstacle readers. The existing pad shape, size, and centre remain the attachment core used for
connectivity and under-approximating keep-in claims; the envelope must contain that core.

## Compatibility

- `schemas/board-ir/0.3.0.schema.json` is frozen permanently.
- The active decoder accepts `0.4.0` and rejects persisted `0.3.0` envelopes. There is no safe
  JSON rewrite: re-convert the original `.kicad_pcb` bytes with the same constraint profile.
- Ordinary boards have no envelope member. Their content payload, snapshot digest, and downstream
  candidate identities remain unchanged; only the envelope version string changes in the wrapper.
- A custom-pad content payload includes its envelope, so its content and snapshot digests are new
  addresses. Invalidate candidates and cached scenes bound to the old snapshot and rebind them to
  the newly converted board.

## Scene and geometry meaning

Circuit Scene 0.4 discloses both the attachment anchor and the custom copper envelope. The
`copper_envelope_nm`, `copper_envelope_frame: "pad_local"`, and
`geometry_model: "anchor_with_custom_copper_envelope"` fields occur together. Consumers that need
obstacle safety must transform and use that local envelope. Consumers making connectivity or
inside claims must use the anchor/core. A single rectangle is not a proof of exact custom-pad
geometry.

This release does not claim exact custom primitive geometry, copper-fill equivalence, or KiCad
parity. The envelope is intentionally conservative for obstacles; any future exact model must
add a separately measured representation and parity evidence.

## Measured capability change

Before implementation, the frozen 18-save selection was predicted to move from 13 to 15 converted
boards, with `phono-v2-main` and `phono-v3-main` as the two newly exposed saves. The measured result
was **13→15**, exactly matching the prediction. A separate 20-run exploratory set reached 16, but
that is not substituted for the stable 18-save result.

No source board is mutated by migration. Preserve the original board bytes and record the new
source revision and snapshot digest alongside any regenerated route or placement candidates.
