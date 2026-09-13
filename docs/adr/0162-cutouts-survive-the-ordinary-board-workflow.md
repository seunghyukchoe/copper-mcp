# ADR-0162: Cutouts survive the ordinary-board workflow

- Status: Proposed; integrated validation in progress
- Date: 2026-09-14
- Owners: CopperMCP maintainers
- Related: [ADR-0005](0005-canonical-board-ir.md),
  [ADR-0124](0124-an-outline-arc-is-inscribed-and-a-cut-is-refused.md),
  [ADR-0161](0161-measured-optimization-is-an-end-to-end-versioned-workflow.md)

## Behavior

The pinned PMW3610 development project contains a footprint-local rectangular board cutout
and a GND zone spanning two layers. Neither may be discarded to admit the project. The same
optimization/v2 consumer must retain these features during intake, placement comparison,
routing, fresh fill, native checking and immutable review-package export.

## Version and geometry

Board IR 0.4 remains the default for its existing subset, with unchanged schema bytes and
canonical identities. Explicit 0.5 adds at most 128 exact axis-aligned rectangular holes inside
the one outer contour. Holes must be strictly interior, disjoint and non-touching; topology
and duplicated owner geometry consume the existing vertex/intersection budgets. Native
`fp_rect` points use the established exact footprint transform, never snapping or repair.

Each hole is also carried in its footprint's `outline_cutouts`. Validation requires exact
one-owner coverage, canonicalization binds both copies, and ownership cannot be omitted while
retaining a hole. Ownership is not KiCad's lock flag. Scope admission, search, legalization and
projection prevent moving the owner; the native placement renderer independently refuses moving
any footprint carrying Edge.Cuts. Other declared footprints can still move.

Every pad must also respect the cutout's missing material: an over-approximating pad bound within
the closed rectangular hole proves the pad is removed, and any under-approximating core contact
proves intrusion. Inclusive bounds also cover a rotated pad whose enclosing circle reaches the
cut boundary while its actual copper remains inside.
Both are `violated`, not `inconclusive`, even when native edge-only DRC misses a pad wholly remote
from the cut edge. This is an explicit material constraint, not an asserted native parity result.
The pre-existing outer-edge bracket and no-hole behavior remain unchanged.

A native multi-layer solid zone is represented by per-layer Zone objects with an explicit
`source_zone_id`, identical source-derived rules and boundary, and unique source/layer identities.
The native source zone is never split or rewritten. Fresh fill still runs on the complete native
board and must preserve all non-cache geometry and rule context. Hatched fills, unsupported
island modes, multiple polygon loops and other unmodeled semantics still refuse.

Scene 0.5 carries hole geometry and charges every vertex. Scene 0.4 cannot accept that geometry;
ordinary no-hole output stays unchanged. Snapshot consumers preserve the explicit Board IR
version instead of reminting 0.5 content as 0.4. Older readers must reconvert with a compatible
implementation; deleting fields or changing a version label is not migration.

## Routing and retained connectivity

Rectangular-outer-board routing treats each cutout as an all-layer obstacle, using the same
track/via half-dimension margins as the outside board edge. Existing budget accounting applies.
Cutout candidates record astar-grid/0.8.0, layered-board-a-star/0.2.0 or
layered-tree-a-star/0.2.0; no-hole candidates keep their preceding identities. Historical single
layer replay cannot opt into the new cutout model. Nonrectangular outer routing remains bounded
by the preceding supported geometry, not the outer bounding box.

Retained pads, tracks, vias, arcs and verified fill must also clear cutouts before an
already-connected result. Conservative contact refuses rather than manufacturing copper across
missing material. The shared connectivity entry point checks this for optimization as well as
direct routing; a caller that already completed the same scan avoids charging it twice.

The sensor exposed a separate real connectivity limit: a diagonal track met a via annulus
outside its four inscribed rectangle cores. For 0.5, exact integer capsule/annulus contact proves
both outer-disc intersection and that the track is not wholly inside the drill. Copper radii
round inward and the drill rounds outward. No floating-point tolerance, native-result override,
or invented copper is used. Previous 0.4 connectivity behavior is retained.

## Evidence and limits

Focused controls cover version downgrade, ownership removal, schema conformance, scene ceilings,
cutout-avoiding single/layered/tree routes, retained copper across a cutout and annulus/drill
contact. The real native MCP development control compares identity and proposed placement under
equal declared budgets, routes two nets over four layers with a cutout, checks DRC, demonstrates
strict copper-length improvement and exports without application. Its explicit 3,000,000-check
job allowance reflects added obstacle work; production ceilings and deadlines are unchanged.

The pinned sensor board-stage run preserves 16 existing target connections, completes repeated
fresh fill and native DRC, and exports a review package. This is not new routing, held-out quality,
placement improvement on that board, complete manufacturing judgment or electrical sign-off.
Measured clearance may be unavailable; missing ERC/physics inputs remain inconclusive. A declared
required domain that fails or is inconclusive still blocks review/application. No apply, consent,
save, release or five-area readiness authority is added.
