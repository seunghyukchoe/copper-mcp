# Board IR and KiCad Adapter Contracts

Board IR is the deterministic board snapshot shared by routing, replay, placement, benchmark, and
MCP application layers. The current contract is `copper.board-ir` version `0.2.0`. It is implemented
as a pure domain package, strict JSON codec, JSON Schema, and a narrow read-only KiCad converter.

See [ADR-0005](../adr/0005-canonical-board-ir.md) for the original integer/digest contract and
[ADR-0026](../adr/0026-first-class-footprints-in-board-ir.md) for the 0.2 footprint model.

## Contract map

| Artifact | Current responsibility |
|---|---|
| `copper_mcp.board_ir.types` | Immutable typed units, geometry, constraints, items, and snapshot envelope. |
| `copper_mcp.board_ir.validation` | Reference, identity, budget, degeneracy, and exact polygon-topology checks. |
| `copper_mcp.board_ir.canonical` | Normalization, canonical JSON bytes, constraint digest, and snapshot digest. |
| `copper_mcp.board_ir.codec` | Strict bounded decoding of untrusted `0.2.0` JSON. |
| `copper_mcp.adapters.kicad_board_ir` | Fail-closed conversion of the documented KiCad subset from bytes. |
| [`0.2.0.schema.json`](../../schemas/board-ir/0.2.0.schema.json) | Active portable serialized-envelope contract. |
| [`0.1.0.schema.json`](../../schemas/board-ir/0.1.0.schema.json) | Immutable legacy compatibility contract. |

The board model has no dependency on MCP, GUI APIs, provider SDKs, routing backends, or filesystem
access. The source adapter accepts bytes supplied by its caller and does not mutate or retain them.

## Envelope

The serialized shape is:

```json
{
  "schema": "copper.board-ir",
  "schema_version": "0.2.0",
  "snapshot_digest": "sha256:<64 lowercase hex characters>",
  "content": {
    "units": {"distance": "nm", "angle": "udeg"},
    "source": {
      "format": "kicad_pcb",
      "format_version": "<source version>",
      "generator": "<source generator or null>",
      "revision": "sha256:<64 lowercase hex characters>"
    },
    "constraint_digest": "sha256:<64 lowercase hex characters>",
    "outline": {"contours": []},
    "copper_layers": [],
    "nets": [],
    "constraints": {},
    "items": {
      "arcs": [],
      "footprints": [],
      "keepouts": [],
      "pads": [],
      "segments": [],
      "vias": [],
      "zones": []
    }
  }
}
```

The abbreviated arrays and constraint object above illustrate placement only; they are not a valid
board. The schema is the field-level reference.

## Numeric and identity invariants

- Coordinates and dimensions use integer nanometres. Angles use integer microdegrees normalized to
  `[0, 360,000,000)`.
- Board IR keeps KiCad's coordinate frame verbatim: y increases downward, while a footprint's
  `(at x y angle)` angle is counter-clockwise *on screen*. A positive quarter turn therefore maps a
  footprint-local point `(x, y)` to `(y, -x)`, not `(-y, x)`. The two differ by a mirror and agree at
  0 and 180 degrees, so an error here is invisible on half of all boards; the KiCad adapter's map is
  pinned by a fixture whose expected pad positions are adjudicated by KiCad's own connectivity
  engine. Pad `rotation_udeg` is the pad's angle **as written**, not the sum of the footprint and
  pad angles: KiCad resolves a pad's orientation into the board frame and rewrites every pad angle
  when a footprint is rotated, so a footprint's placement turns where its pads *are* without
  turning what shape they present. Adding the two counted the turn twice and transposed the extents
  of every non-square pad on a rotated footprint. The convention is pinned against KiCad's own
  plotted geometry rather than against the format documentation, by exporting the fixture with
  `kicad-cli pcb export svg` and comparing drawn extents.
- Integers are capped at `2^53 - 1` in magnitude so JSON consumers do not lose precision. Positive
  widths, diameters, and drills cannot be zero; typed non-negative values may be zero.
- Millimetre/degree source tokens use ordinary decimal notation and must convert exactly. There is no
  rounding path for sub-nanometre or sub-microdegree input.
- A roundrect `roundrect_rratio` is the one exception, and it rounds in a single direction. KiCad
  stores a ratio of the pad's shorter side rather than a radius, so the product is routinely a
  fractional nanometre; it is rounded **up**, because the radius is read only by the
  under-approximating attachment core and a larger radius shrinks that core, while every obstacle
  model over-approximates the pad by its full bounding box and never consults the radius at all.
  The largest such round-up on a board is reported as `ConversionResult.max_roundrect_rounding_nm`.
  A ratio outside `(0, 0.5]`, or one whose rounded-up radius would exceed half the short side, is
  refused rather than clamped. See [ADR-0076](../adr/0076-roundrect-corner-radius-rounding.md).
- IDs use type prefixes such as `layer:`, `net:`, `class:`, `footprint:`, `pad:`, `via:`,
  `segment:`, `arc:`, `zone:`, `keepout:`, `contour:`, and `rule:`. IDs and display names have
  different roles.
- All references must resolve. Layer indices are contiguous physical ordinals, object IDs are
  unique, every net has exactly one net-class assignment, every pad belongs to exactly one
  footprint, through vias span the complete stack, and locked state is explicit. Padless
  footprints remain representable.
- Rings contain at least three distinct vertices, omit a duplicated closing point, have nonzero area,
  and cannot self-intersect. The v0.2 board has exactly one counter-clockwise outer ring and no
  outline holes.
- Circle pads have equal axes. SMD pads are drill-free, through-hole and NPTH pads require exact
  drill dimensions, and NPTH pads cannot carry an electrical net.

## Typed model coverage

| Area | v0.2 representation |
|---|---|
| Board | Exactly one named outer outline contour; holes are not representable in v0.2. |
| Stack | Ordered copper layers of kind `signal`, `plane`, or `mixed`. |
| Connectivity | Stable net IDs with UTF-8 display names. |
| Constraints | Net classes, one assignment per net, differential-pair rules, and min/max length rules. |
| Components | Footprint identity, board-frame origin, normalized rotation, side, lock state, total pad ownership, and up to 64 exact simple orthogonal courtyard rings. |
| Terminations | SMD, through-hole, and NPTH pads; circle, rectangle, oval, and rounded-rectangle shapes. |
| Existing copper | Straight segments, exact three-point arcs, full-stack through vias, and solid zones with priority, pad-connection, island-removal, clearance, and thermal intent. |
| Exclusions | Multi-layer polygonal keepouts with explicit track, via, pad, zone, and footprint prohibitions. |
| Provenance | Source format/version/generator, source SHA-256 revision, constraint digest, and snapshot digest. |

The v0.2 model and adapter intentionally share the same narrow outline and via topology. Adding
outline holes or blind, buried, or microvias requires a later schema version and migration review.

## Canonicalization and digests

Before hashing, construction normalizes:

- copper layers by stack index;
- nets and board items by stable ID;
- footprint pad ownership and normalized courtyard rings;
- constraint records by stable ID or assigned net ID;
- pad and keepout layer sets by stack order;
- via start/end layers into stack order; and
- ring start point and orientation.

Canonical JSON is strict UTF-8 with sorted keys, compact separators, no floating-point/non-finite
numbers, and one trailing newline. `constraint_digest` is SHA-256 over canonical constraints and the
sorted net-ID set. `snapshot_digest` is SHA-256 over canonical `content`; it intentionally excludes
the envelope so the digest is not recursive. Both use the `sha256:` prefix.

Decoding is also strict: the byte budget is checked first; a streaming lexical/structural pass rejects
duplicate properties and enforces string, depth, node, and per-container limits before allocating the
JSON object graph. Unknown keys and floats fail, the schema discriminator/version must match exactly,
and the normalized result must pass semantic validation and both digest checks. `make_snapshot`
normalizes direct domain objects before hashing, while `verify_snapshot` rejects a manually built
envelope whose content is not already canonical. Public snapshot writers also enforce the default
decoder's byte, node, depth, string, and per-container budgets.

## KiCad read-only subset

`parse_kicad_bytes(source, profile, limits)` parses exactly one bounded UTF-8 S-expression and
returns a `ConversionResult`. A successful result contains a verified snapshot. Any conversion error
contains a bounded machine-readable diagnostic and no snapshot.

### Accepted today

| KiCad construct | Accepted subset |
|---|---|
| Board metadata | Exact KiCad PCB format version `20260206`, optional `generator`, ordered copper declarations `0/F.Cu`, contiguous even-numbered inner layers, then `B.Cu`, and a narrow setup-metadata allowlist with KiCad-default front/back via tenting. |
| Nets | Quote-aware named item-level net references and legacy numeric root declarations; quoted numeric text remains a name while bare signed numeric tokens retain legacy net-code meaning. |
| Outline | Exactly one `gr_rect` on `Edge.Cuts`; it becomes the single imported contour. |
| Footprints/pads | Footprints on `F.Cu` or `B.Cu` with rotations in 90-degree increments, exact origin/side/lock/pad ownership, and optional unfilled `fp_rect`, orthogonal `fp_poly`, or closed orthogonal `fp_line` courtyard centerlines on the matching layer; `smd`, `thru_hole`, and `np_thru_hole` pads; circle, rect, oval, and roundrect shapes; round or oval drills; copper layer names, `*.Cu`, and `F&B.Cu`. |
| Routed copper | Net-bound straight `segment` items, exact start/mid/end `arc` items, and through vias spanning the declared copper stack. |
| Zones | Net-bound, single-copper-layer, solid zones with one polygon loop; explicit priority, thermal/through-hole-thermal/solid/none pad connection, always/never island removal, clearance, and conditionally required thermal dimensions. |
| Keepouts | Copper-layer sets, exactly one polygon loop, the five modeled prohibition flags, and lock state. |
| Constraints | A caller-supplied `KiCadConstraintProfile` containing net classes, a default class, optional per-net-name assignments, differential-pair rules, and length rules. |

The source revision is the SHA-256 digest of the exact input bytes. One native UUID or legacy tstamp
is used for item identity when available; simultaneous identity fields are ambiguous and rejected.
Otherwise identity is derived deterministically from the source revision and source locator. Net IDs
are deterministic from net names.

The converter performs a version-specific semantic preflight. Root and footprint graphics on any
copper layer are rejected. Footprint graphics on `Edge.Cuts` are also rejected, and the only accepted
root `Edge.Cuts` primitive is the one unfilled rectangle. Non-routing documentation graphics may be
ignored. Supported unfilled orthogonal courtyard centerlines are the exception: they become
canonical board-frame rings. For the supported front/back footprint subset, KiCad's authored board-frame child
coordinates are imported as written; the adapter does not apply a second mirror when a footprint
is on `B.Cu`. `filled_polygon` data is treated as a derived KiCad fill cache: v0.2 records zone fill intent,
not cached fill geometry, fill freshness, or connectivity proof.

### Rejected today

The adapter fails closed on any unsupported construct required to represent the board faithfully,
including:

- `Edge.Cuts` lines, arcs, circles, polygons, curves, additional rectangles, and mixed/non-rectangular
  outlines;
- root or footprint-local text/graphics on copper, and any footprint-local `Edge.Cuts` primitive;
- footprint rotations not divisible by 90 degrees;
- curved, diagonal, filled, open, branching, duplicate-edge, mixed-layer, or other unsupported
  courtyard topology, a courtyard layer that disagrees with the supported front/back footprint,
  and more than 64 courtyard rings on one footprint;
- custom or other unmodeled pad shapes and custom pad primitives;
- blind, buried, or microvias in KiCad input;
- multiple polygon loops or holes in a zone/keepout, multi-layer copper zones, hatched fills,
  smoothing, minimum-area island removal, and other unmodeled zone semantics;
- non-neutral capping/filling/covering/plugging, non-default board via tenting, per-via tenting
  overrides, and pad/via copper-removal or custom-connectivity options;
- setup defaults, stackup/routing-rule constructs, and other setup fields outside the documented
  non-routing metadata allowlist;
- simultaneous UUID/tstamp identities and malformed, unresolved, or unconnected legacy net codes;
- absent/unknown copper layers, malformed or unresolved nets, and constraints that reference missing
  nets or classes; and
- any numeric token that requires rounding or exceeds the integer range.

There is no fallback tessellation, best-effort omission, or silent constraint default beyond the
explicit default net class in the supplied profile. Project-level net classes and custom rules are
not parsed from `.kicad_pro` or `.kicad_dru` in this adapter.

## Resource limits and diagnostics

Default independent limits bound input bytes, parse depth, token/node counts, atom size, list width,
object count, vertices, intersection work, and diagnostics. Callers may supply a stricter positive
`ParseLimits` value. These limits are compatibility and security boundaries, not promises that every
input below each individual cap is cheap. The JSON Schema describes the portable structural ceiling;
an externally produced schema-valid document can still exceed a decoder's operational security
budget. CopperMCP's public writers never emit a snapshot that the default decoder would reject for
those operational limits.

Diagnostics expose a stable code, `warning` or `error` severity, bounded structural locator, and
optional object kind/ID. Messages and locators do not include or echo source values or attacker-made
construct names. The current adapter returns the first conversion error, so downstream code must
treat `snapshot is None` as a hard failure rather than attempting partial routing.

## Contract fixtures

- [`schema-valid.json`](../../tests/fixtures/board-ir-v0.2/schema-valid.json) is the active golden canonical
  snapshot produced by the synthetic KiCad subset.
- [`schema-invalid.json`](../../tests/fixtures/board-ir-v0.2/schema-invalid.json) is rejected by both
  the JSON Schema and runtime decoder.
- [`subset.kicad_pcb`](../../tests/fixtures/board-ir-v0.1/subset.kicad_pcb) exercises pads, a segment,
  an arc, a through via, a solid zone, a keepout, exact UTF-8 net identity, and typed constraints.
- [`footprint-pose-courtyard.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/footprint-pose-courtyard.kicad_pcb)
  pins footprint pose, ownership, lock state, and rectangular courtyard transforms at all four
  orthogonal rotations; KiCad 10.0.5 reports zero DRC violations and zero unconnected items.
- [`footprint-front-back-pose.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/footprint-front-back-pose.kicad_pcb)
  exercises the bounded `F.Cu`/`B.Cu` observation path, board-frame pad/courtyard coordinates,
  native identities, and KiCad CLI DRC. It is a source-format/CLI oracle, not evidence from a
  GUI flip-save round trip.
- [`courtyard-orthogonal-chains.kicad_pcb`](../../tests/fixtures/board-ir-v0.2/courtyard-orthogonal-chains.kicad_pcb)
  was resaved by KiCad 10.0.5 and pins one concave orthogonal polygon plus an unordered closed
  line chain, exact board-frame conversion, fail-closed malformed-chain cases, and a clean CLI
  DRC source oracle.
- [`malformed-unbalanced.kicad_pcb`](../../tests/fixtures/board-ir-v0.1/malformed-unbalanced.kicad_pcb)
  exercises bounded fail-closed S-expression parsing.

## Evolution rules

Breaking canonical changes require a new schema version, fixtures, compatibility tests, migration
guidance, ADR review, and changelog entry. Source-adapter coverage may expand under `0.2.0` only when
the resulting canonical meaning is unchanged and the accepted/rejected matrix plus fixtures are
updated. Unknown Board IR versions and unknown fields remain errors.

Serialized 0.1 snapshots cannot be upgraded by inventing missing parents. Preserve them against the
legacy schema and re-convert the original source as described in the
[0.2 migration guide](../migrations/board-ir-0.2.md).

Related contracts remain separate:

- [`board-manifest.schema.json`](../../schemas/board-manifest.schema.json) is the existing bounded
  inspection manifest used by current services.
- [`candidate.schema.json`](../../schemas/candidate.schema.json) is the current candidate metadata
  contract; it is not yet a Board IR geometry patch.
