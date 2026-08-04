# ADR-0026: Make footprints revision-bound Board IR objects before moving them

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0022, ADR-0024, ADR-0025

## Context

Board IR 0.1 flattened pads into board space. Placement reconstructed their parent footprints by
parsing the KiCad source a second time, so the observation used to name a subject was not itself in
the snapshot digest. Circuit Scene could show pads but not the component pose or ownership relation
an AI needs to reason about placement. The same omission made a footprint edit impossible to verify
with the route apply engine's source-plus-patch equality rule.

KiCad courtyard geometry adds a second trap. Saved child coordinates are footprint-local and a
back-side instance is already flipped in the file; mirroring it again while reading would silently
double-mirror the board. Courtyards may also be made from lines, polygons, arcs, or disjoint loops.
A partial importer must therefore reject syntax it cannot preserve instead of manufacturing a
plausible contour.

## Decision

CopperMCP publishes `copper.board-ir` `0.2.0` with a first-class immutable `Footprint`:

```text
Footprint(id, origin, rotation_udeg, side, pad_ids, courtyards, locked)
```

- Every Board IR pad belongs to exactly one footprint. Padless mechanical or graphics-only
  footprints remain valid objects.
- Origins and rectangular courtyard rings are exact board-frame integer nanometres. Rotation is an
  integer microdegree value, side is a closed `front | back` enum, and the source lock is explicit.
- Footprints count against `max_objects`. Courtyard vertices and intersection work use the existing
  polygon budgets, and one footprint may carry at most 64 courtyard rings.
- Canonicalization sorts footprint IDs, pad ownership, and normalized rings. Pose, ownership,
  courtyard, side, or lock changes alter `snapshot_digest`; they do not alter `constraint_digest`,
  because none is a routing-rule input.
- The active writer and decoder accept exact 0.2 only. The 0.1 schema and golden fixture remain
  immutable compatibility evidence. There is no JSON-only migration: 0.1 did not contain enough
  information to reconstruct parent identity or pose, so migration re-converts the original board.

The current KiCad adapter remains a deliberately narrow sound subset: front-side footprints,
orthogonal rotations, and unfilled `fp_rect` geometry on the matching `F.CrtYd` layer. Other
courtyard primitives, a mismatched courtyard layer, back-side footprints, and non-orthogonal parent
transforms fail closed. This is an acceptance restriction, not a claim that KiCad lacks those
features. A future back-side adapter must interpret already-saved local coordinates without adding
another mirror.

Placement now projects subjects from the revision-bound Board IR footprint collection and requires
the supplied source bytes to match `content.source.revision`. A proposal may not move a locked
footprint. Circuit Scene 0.2 exposes footprints as static objects with pose, side, lock, pad IDs,
and courtyard rings; pad-ID relationships consume the same bounded detail budget as vertices.

## Consequences

- An MCP client can observe a component, name it, see which pads it owns, and submit that exact
  revision-bound reference to placement preview without relying on a second identity parser.
- Route apply now checks native identity for footprints too. A revision-derived footprint ID is
  refused before output generation because changing the source revision would change the ID.
- `courtyard_overlap` remains `not_modelled`. Carrying a contour is not evidence that a bounded,
  side-aware legality evaluator ran.
- Placement apply remains deferred. Board IR still omits author text, fabrication graphics, library
  identity, properties, and 3D-model pose, so rewriting a footprint cannot yet be proven faithful.
- General line-chain/polygon courtyard topology and back-side footprint support require their own
  source-oracle fixtures and contract review; unsupported boards receive no partial snapshot.

## Verification

The contract is pinned by strict schema/codec compatibility tests, ownership and budget boundaries,
metamorphic rigid transforms, a KiCad-authored pose/courtyard fixture at 0/90/180/270 degrees,
source-revision and locked-move refusals, route-apply identity checks, and KiCad 10.0.5 DRC of the
new fixture.

## References

- [Board IR contract](../architecture/board-ir.md)
- [Board IR 0.2 migration](../migrations/board-ir-0.2.md)
- [KiCad file format introduction](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [KiCad PCB Editor documentation](https://docs.kicad.org/10.0/en/pcbnew/pcbnew.html)
