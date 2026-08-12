# ADR-0097: A courtyard keeps out on the layer it is drawn on, not on its footprint's side

- Status: Accepted
- Date: 2026-08-12
- Owners: CopperMCP maintainers
- Related: [ADR-0058](0058-rectangular-courtyard-legality.md); [ADR-0065](0065-orthogonal-courtyard-chains.md);
  [ADR-0075](0075-courtyard-oracle-parity.md); [ADR-0080](0080-chamfered-and-circular-courtyards.md);
  [ADR-0088](0088-complete-or-withheld-scene-kinds.md);
  [courtyard layer versus footprint side research](../research/kicad-courtyard-layer-vs-footprint-side-v1.md);
  issue #151

## Context

Board IR refused any board on which a footprint's courtyard shape sat on the courtyard layer
opposite the footprint's copper side, with `unsupported.transform` and the message "courtyard
layer does not match its footprint side". Three of the four phono saves in the survey corpus stop
there, and it is the largest single conversion blocker remaining on that corpus.

**It was not our bug, and it was not a board defect.** Both were checked before anything was
designed, because either answer would have made the rest moot.

- *Not ours.* `_footprint_side` reads the footprint's own `(layer ...)` atom and derives nothing;
  `_transform` applies a translation and a quarter turn and never a mirror; no adapter path reads,
  writes or flips a courtyard shape's layer. The corpus agrees: the same stock footprint appears
  on `F.Cu` at 0° and on `B.Cu` at 180°, and in both poses carries `F.CrtYd` *and* `B.CrtYd`
  rectangles, mirrored between the poses by KiCad's own `FOOTPRINT::Flip`. A footprint carrying
  both layers before a flip carries both after it.
- *Not a defect.* Every mismatching footprint in the corpus is one unmodified stock KiCad
  footprint, `Connector_Wire:SolderWire-0.5sqmm_1x02_P4.6mm_D0.9mm_OD2.1mm_Relief`, declared
  `(layer "F.Cu")` in the shipped library and drawing its feed-through strain-relief slot on
  `B.CrtYd`. KiCad reports nothing about it — there is no check anywhere in `pcbnew` comparing a
  courtyard's layer against its footprint's side, `MISSING_COURTYARD` requires *both* layers to be
  empty, and a board carrying only that arrangement produces zero violations at `--severity-all`.
  KLC F5.3 does not merely permit a back courtyard, it requires one where the part needs back-side
  clearance.

**What it means is exact and measured.** `FOOTPRINT::BuildCourtyardCaches`
(`pcbnew/footprint.cpp:3664`) files each shape by the shape's own `F_CrtYd`/`B_CrtYd` layer into
two independent caches and never reads `IsFlipped()`, `GetSide()` or the footprint's `GetLayer()`;
`DRC_TEST_PROVIDER_COURTYARD_CLEARANCE` compares front against front and back against back with no
side test in the file. KiCad's own QA case "two footprints, overlap, different sides" puts both
footprints on the front and expects *no* collision, because their shape layers differ. Against
real `kicad-cli` 10.0.5, two coincident `B.CrtYd` squares collide whether their footprints are on
opposite sides or both on the front, and a `B.CrtYd` never collides with an `F.CrtYd`. The full
sweep, in both directions, is in the research note.

**The refusal was therefore the only thing standing between us and a false claim.** Under the
previous model, `_courtyard_overlap` paired footprints by `first.side != second.side: continue`,
so had the board been accepted with its far-side rings dropped — or attributed to the footprint's
own side — a back-side part placed under that feed-through connector would have been published as
`courtyard_overlap: proven_clear` where KiCad reports `courtyards_overlap`. That is a keep-out
silently deleted, in the one direction an obstacle may never err.

## Decision

**Board IR holds courtyards per courtyard layer. The footprint's side selects which stored set is
which layer; it never decides whether the footprint occupies a layer at all.**

1. **`Footprint` gains `far_side_courtyards` and `far_side_courtyard_circles`**, the shapes on the
   layer opposite `side`. `courtyards` and `courtyard_circles` keep their exact previous meaning —
   the shapes on the matching layer — so nothing already stored changes meaning. The `Footprint`
   properties `front_courtyards` and `back_courtyards` resolve the pair by layer name, and the
   legalizer's `_PlacedFootprint.on_layer` does the same for placed geometry. Neither reproduces
   KiCad's `GetCachedCourtyard` fallthrough, in which every layer that is not a back layer —
   including inner copper and `UNDEFINED_LAYER` — silently returns the front courtyard; the
   accessors here take a boolean naming the courtyard layer and admit no third answer.
2. **The two sets are never pooled.** They are separate even-odd regions on separate physical
   layers. Line chains are assembled one layer at a time, so a front edge and a back edge meeting
   at a shared vertex are two closed loops rather than one degree-four branch failure; and the
   circle-disjointness rule of ADR-0080 runs per layer, so the stock feed-through footprint —
   whose back rectangle is drawn *inside* its front one — is not refused for an even-odd parity
   hazard that cannot occur across layers. The same-layer control for each is still refused.
3. **`_courtyard_overlap` pairs by courtyard layer.** For every pair of footprints it compares
   front region against front region and back against back, independent of either footprint's
   side. On a board where every courtyard matches its footprint's side this enumerates exactly the
   pairs the old same-side gate enumerated, and a footprint with no courtyard on either layer is
   still skipped before it charges the pair budget.
4. **Everything that consumes a courtyard consumes both layers**: the placement view and its
   stationary envelopes, the route-scoring projection, the scene's footprint bounds and its vertex
   budget, and the moved-pose transforms. A far-side ring is part of the same rigid body and moves
   with its footprint; a proposal naming a different `side` is already refused as unmodelled, so no
   mirror is ever required and the two layers never trade contents.
5. **New keys are emitted only when non-empty**, in the canonical Board IR payload
   (`far_side_courtyards`, `far_side_courtyard_circles`), the schema, and the scene geometry
   (`far_side_courtyards_nm`, `far_side_courtyard_circles_nm`). Every board representable before
   this change encodes byte-for-byte as it did, so no snapshot digest, no scene revision and no
   committed golden identity moves, and no schema-version bump is required. This is exactly the
   mechanism ADR-0080 used for `courtyard_circles`.
6. **The 64-courtyard ceiling counts both layers against one total**, in the adapter and in the
   decoder, because the two paths disagreeing about one rule was the defect `schema.limit` exists
   to prevent.

### The accepted subset, as a closed field table

Stated as a table and not as prose, because ADR-0092's prose subset admitted two things it did not
mean. Anything not listed is refused.

| Source field | Required | Permitted values | Board IR destination |
| --- | --- | --- | --- |
| `footprint` → `layer` | yes, exactly one | `F.Cu`, `B.Cu` | `Footprint.side` |
| `fp_rect` \| `fp_poly` \| `fp_line` \| `fp_circle` → `layer` | yes, exactly one | `F.CrtYd`, `B.CrtYd` | selects near or far set by comparison with `Footprint.side`; any other layer is not a courtyard and takes its own existing path |
| `fp_rect` → `start`, `end` | yes | exact millimetre literals; non-zero width and height | ring of four points, near or far |
| `fp_poly` → `pts` | yes | octilinear chain; every edge horizontal, vertical, or \|dx\|=\|dy\| | ring, near or far |
| `fp_line` → `start`, `end` | yes | octilinear; the layer's segments must form closed non-branching loops **within that layer** | ring, near or far |
| `fp_circle` → `center`, `end` | yes | exact integer-nanometre radius, non-zero | `CourtyardCircle`, near or far |
| `fp_rect` \| `fp_poly` \| `fp_circle` → `fill` | no | absent, `none`, `no` | discarded |
| `fp_*` → `stroke`, `locked`, `tstamp`, `uuid` | no | any | discarded |
| `fp_arc` on a courtyard layer | — | none | refused, `unsupported.construct` |
| any other `fp_*` head on a courtyard layer | — | none | refused, `unsupported.construct` |

Two counts bound the whole: at most 64 courtyard shapes per footprint across **both** layers, and
the existing per-ring vertex budget applies to a far-side ring exactly as to a near one.

## Consequences

- **A refusal is replaced by a stricter verdict, not by a weaker one.** The three corpus boards
  that stopped here no longer stop here. On the two-footprint fixture, the same placement that the
  side-gated model would have called `proven_clear` is now `violated`, matching the tool.
- **The measured conversion count did not move, and this change does not claim it did.**
  Conversion stops at the first error, so a refusal names the frontier and never the stack behind
  it. On `main` the courtyard refusal is not reachable at all: the same three boards stop earlier,
  on root board properties (#150, still open). With #150 applied the courtyard refusal appears,
  this change removes it, and all three boards then stop on a *further* blocker —
  `pad field 'options' is unsupported`, a custom-shape SMD pad, filed as #153. The survey corpus
  reads **11 of 18 before and 11 of 18 after**, on `main` and on `main` + #150 alike, with all 18
  board digests identical across every run. (The corpus is a live tree and grew from 17 boards to
  18 during this work; the added board is refused by an unrelated construct both before and after,
  and the before→after→before triple reproduces itself exactly, so the growth is recorded rather
  than averaged over.) The frontier moved; the count did not. That is the expected outcome of
  removing one blocker from a stack, and this is the fourth instance of it in this project.
- **Write-back is unchanged and still refuses.** The source-preserving placement serializer accepts
  only front-side footprints whose courtyard rectangles are on `F.CrtYd`, so a feed-through part is
  previewable and not movable through it — the same asymmetry ADR-0080 recorded for chamfered and
  circular courtyards, and now pinned by its own test so that widening the serializer has to
  confront the far-side rectangle deliberately. `_expected_content` moves far-side rings correctly
  anyway, because the failure mode of getting that wrong is a silent geometry disagreement rather
  than a typed refusal.
- **Existing artifacts are unaffected.** No Board IR digest, scene revision, candidate identity or
  committed golden moves; `tests/test_golden_identities.py` passes unmodified.
- **A scene consumer must not union the two courtyard fields.** They are reported separately
  precisely because unioning them would read a keep-out onto a side that has none. The MCP contract
  declares both as optional closed fields so a consumer that ignores the new one keeps working and
  a consumer that reads it cannot receive an empty array.

## Alternatives considered

**Keep the refusal and reword it as a board defect.** Rejected on evidence, not on preference. The
offending footprint is a stock KiCad library part used unmodified, KiCad reports nothing about the
arrangement, and KLC F5.3 requires it for a part needing back-side clearance. Telling a designer to
fix their board would have been a false instruction, and pinning that message would have made it
permanent.

**Fold the far-side shapes into the footprint's own courtyard set.** This is the tempting
"over-approximate, therefore safe" move, and it is wrong here in both directions. It grows the
near-side keep-out with geometry that keeps out on the other layer, so it would publish `violated`
for the two arrangements real `kicad-cli` reports clear — a false claim, which ADR-0075 and
ADR-0080 already ruled out: on an evidence-publishing surface a conservative verdict that is not
KiCad's verdict is not a safe verdict. It would also *still* under-approximate the far side, since
those rings would only ever be compared against the near layer. Constraining both sides is neither
sound nor necessary, because the courtyard's own layer says which side it constrains.

**Drop the far-side shapes and convert the rest.** The cheapest change and the only unsafe one: it
deletes a keep-out and would publish `proven_clear` where KiCad reports an overlap. Refused for the
same reason the original refusal existed.

**Attach the far-side courtyard to a synthesised stationary footprint on the other side.** It gives
the right answer for a footprint that never moves and the wrong answer for one that does, since the
shadow would stay behind. Rejected as a correctness trap disguised as a small diff.

**Restructure the field as `front_courtyards` / `back_courtyards` in the stored payload.** Cleaner
to read, and it would have renamed a key present in every previously minted snapshot, moving every
digest and forcing a schema-version bump and a migration note for a naming preference. The stored
representation keeps `courtyards`; the by-layer reading is provided as accessors.

## References

- [KiCad 10.0.5 courtyard cache construction](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/footprint.cpp)
- [KiCad 10.0.5 courtyard DRC provider](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/pcbnew/drc/drc_test_provider_courtyard_clearance.cpp)
- [KiCad 10.0.5 courtyard-overlap QA tests](https://gitlab.com/kicad/code/kicad/-/blob/10.0.5/qa/tests/pcbnew/drc/test_drc_courtyard_overlap.cpp)
- [KiCad Library Convention F5.3](https://gitlab.com/kicad/libraries/klc/-/blob/master/content/footprint/F5/F5.3.adoc)
- [Courtyard layer versus footprint side research](../research/kicad-courtyard-layer-vs-footprint-side-v1.md)
