# ADR-0111: Carry custom-pad copper separately from its attachment anchor

- Status: Accepted
- Date: 2026-08-15
- Owners: `@seunghyukchoe`
- Related: [ADR-0100](0100-custom-pads-have-an-envelope-and-nowhere-to-put-it.md),
  [ADR-0105](0105-a-schema-version-moves-with-its-accepted-set.md),
  [ADR-0110](0110-placement-boundary-verdicts-bracket-kicad-parity.md),
  [pad geometry reader survey](../research/pad-geometry-reader-survey-v1.md),
  [custom-pad envelope research](../research/kicad-custom-pad-envelope-v1.md),
  [issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116)

## Context

ADR-0100 established that a custom KiCad pad has copper outside its anchor, while the existing
Board IR `Pad` fields were consumed in both directions of error. The obstacle model must contain
all copper; an attachment core must remain inside copper. One rectangle cannot satisfy both.

The reader survey was re-derived independently from the code graph at index/source SHA
`114debed66c5fa6d86aa84b73d90352704c855c2`: 23 sites across 14 modules, including over-, under-,
exact/parity, and carrier readers. This is the basis for the split below, rather than a hand-picked
list of the first routing call sites.

KiCad's writer grammar supplies enough source geometry to bound a custom pad without pretending to
reconstruct it exactly. The accepted primitive heads are `gr_line`, `gr_arc`, `gr_circle`,
`gr_rect`, `gr_poly`, and `gr_curve`; non-copper `gr_bbox` and `gr_vector` proxy items are accepted
only as bounded, non-copper inputs. Decimal source coordinates and dimensions are converted to
exact integer nanometres. Curves use their control-point box, and arcs use an outward-rounded
circumcircle bound. These are containing bounds, not KiCad polygon parity proofs.

The capability measurement was frozen before implementation: on the 18-save corpus, 13/18 was
predicted to become 15/18, specifically by clearing the custom-pad blocker on `phono-v2-main` and
`phono-v3-main`; their newly visible topology blockers were expected to be disjoint Edge.Cuts and
courtyard topology. B-117 subsequently measured exactly 15/18 on that frozen selection, with source
hashes unchanged before and after. A later 20-run measurement is 16/20 but is explicitly non-stable
and does not replace the frozen 18-save claim.

## Decision

### Two geometry roles in Board IR 0.4.0

`Pad` retains its `shape`, `size_x_nm`, and `size_y_nm` as the KiCad anchor. For an accepted custom
pad it additionally carries an optional `PadCopperEnvelope`, a local integer AABB with
`min_x_nm`, `min_y_nm`, `max_x_nm`, and `max_y_nm`. The envelope must contain the complete anchor
and every accepted primitive bound. Existing non-custom pads may omit it.

The roles are closed and directional:

| Reader requirement | Representation | Claim |
|---|---|---|
| Over-approximation: routing obstacles, region windows, pad bounds, via-in-pad refusal | `PadCopperEnvelope` | Contains the copper; widening is allowed, shrinking is not. |
| Under-approximation: connectivity and attachment cores | Anchor shape/size | Lies inside the copper; primitive-only copper is never an attachment target. |
| Exact/public observation | Both, explicitly labelled | Discloses the two roles; it does not claim exact primitive or KiCad parity. |

The adapter accepts only the closed primitive grammar named above. Unknown heads, malformed
coordinates, duplicate required fields, invalid fill/width data, and unbounded proxies refuse
before a snapshot is published. The primitive union is not converted into `PadShape.CUSTOM` and
is not treated as an exact polygon.

For quarter-turn placement, the local envelope uses the Board IR y-down transform exactly. For
arbitrary rotations, obstacle readers use a containing farthest-corner circle/AABB; this can
refuse a legal route but cannot allow a track through unmodelled copper. The attachment anchor
does not widen with that obstacle approximation.

### Versioning

The accepted set widens when `custom` becomes convertible, so the schema moves to Board IR
`0.4.0`. The `0.3.0` schema and its accepted set are frozen under ADR-0105; no custom-pad envelope
is backported into it. Circuit Scene moves to `0.4.0` to disclose the two geometry roles and the
`geometry_model` marker without making an old scene consumer guess what a pad rectangle means.

## Consequences

- Custom pads that satisfy the closed grammar convert instead of refusing at `options` or
  `primitives`, and the refusal remains fail-closed for everything outside the grammar.
- Every over-reader must select the envelope and every under-reader must select the anchor. A new
  reader that cannot establish its direction must refuse rather than silently choose the envelope.
- Public scene geometry can include `copper_envelope_nm` with
  `copper_envelope_frame: "pad_local"` and
  `geometry_model: "anchor_with_custom_copper_envelope"`; the three fields are an all-or-none
  disclosure, not an exact-shape or parity verdict.
- Obstacle safety is conservative at arbitrary rotation. Exact primitive geometry and exact
  equivalence with KiCad are deliberately not claims of this ADR.
- The frozen 18-save measurement remains the acceptance metric: predicted 13→15 and measured
  conversion must be reported per board, including any newly revealed refusal. The 20-run 16/20
  result is context only because it is non-stable.

## Alternatives rejected

- **Map custom pads to `PadShape.RECT` using the primitive union box.** Rejected: the box is safe
  for obstacles but would make the attachment core claim copper that may not exist.
- **Keep the anchor rectangle for every reader.** Rejected: it under-reads real custom copper as an
  obstacle, allowing a route through metal.
- **Publish one exact custom polygon.** Deferred: the current contract has no exact polygon/parity
  proof, and the required public split is already achieved by a bounded envelope plus anchor.
- **Add a `PadShape.CUSTOM` enum member without a separate field.** Rejected: it recreates the
  same opposite-direction ambiguity and would widen a closed field without carrying both claims.

## Validation boundary

The deterministic adapter, codec, schema, routing, placement, scene, and mutation specifications
must each test both directions: primitive-only copper is present in every over-reader, while no
under-reader or connectivity result reaches primitive-only copper. KiCad parity remains an explicit
non-claim until an independent exact geometry oracle exists.
