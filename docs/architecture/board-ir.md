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
  refused rather than clamped. See [ADR-0077](../adr/0077-roundrect-corner-radius-rounding.md).
- An **unlocked** root `(group ...)` is editor organisation and is read past rather than refused.
  KiCad models it as a "transparent container" whose position is derived from its members', with no
  layer and no net, and every member is a root object converted on its own terms — so the copper,
  outline and nets a document holds are the same set with the group read as without it. The
  grouping is not modelled: Board IR has no membership relation, so the number of accepted and
  unmodelled groups is reported as `ConversionResult.unmodelled_group_count`. A **locked** group is
  refused, because `BOARD_ITEM::IsLocked()` derives every member's lock from it and Board IR would
  otherwise convert those members as unlocked; lock is an authorization gate, not a hint. A group
  carrying any child head outside KiCad's own writer vocabulary is refused rather than assumed
  inert. See [ADR-0090](../adr/0090-root-level-board-groups.md).
- A pad whose kind token is `connect` — KiCad's `PAD_ATTRIB::CONN`, the edge-card connector
  finger — converts as `PadKind.SMD`. KiCad's own model makes the two the same pad wherever copper
  is at stake: its connectivity engine, its push-and-shove router, its layer trimming and its hole
  suppression all put `CONN` and `SMD` in one shared case body. At least ten things do differ —
  solder paste, the Gerber aperture attribute, pick-and-place "exclude all TH", the Edge.Cuts
  clearance DRC exemption, a distinct property-system value user DRC rules can name, and four
  reporting surfaces — and every one is outside what a Board IR `Pad` claims. `PadKind` gains
  **no member**: nothing in this repository would read it, and widening the published `0.2.0`
  enum in place would break a consumer promised a closed three-value domain. The token is
  therefore discarded, and `ConversionResult.edge_connector_pad_count` reports how many pads it
  happened to — an **in-process** count that reaches no MCP contract, CLI output or scene, exactly
  like the group count above. What bounds the loss is the write path: both patch adapters are
  source-preserving splices, so the `connect` token survives in the `.kicad_pcb` and KiCad's own
  DRC and fabrication output still see an edge connector. See
  [ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md) and R-141.
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
| Components | Footprint identity, board-frame origin, normalized rotation, side, lock state, total pad ownership, and up to 64 exact simple courtyard shapes — rings whose every edge is horizontal, vertical, or an exact 45-degree chamfer, plus circles of exact integer radius ([ADR-0080](../adr/0080-chamfered-and-circular-courtyards.md)). The 64 ceiling is a fixed schema limit reported as `schema.limit`, not an operator budget. |
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
| Outline | Exactly one contour on `Edge.Cuts`, drawn either as one unfilled `gr_rect` or as `gr_line` segments that chain, by exact endpoint coincidence, into one closed simple loop. See [ADR-0076](../adr/0076-segment-assembled-edge-cuts-outline.md). |
| Footprints/pads | Footprints on `F.Cu` or `B.Cu` with rotations in 90-degree increments, exact origin/side/lock/pad ownership, and optional unfilled `fp_rect`, `fp_poly`, or closed complete `fp_line` courtyard centerlines on the matching layer — every edge horizontal, vertical, or an exact 45-degree chamfer — plus unfilled `fp_circle` outlines whose radius is an exact integer nanometre; all four KiCad pad kinds, `smd`, `thru_hole`, `np_thru_hole` and `connect` — the last being the edge-connector pad, which converts as `PadKind.SMD` and is counted in `ConversionResult.edge_connector_pad_count` per [ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md); circle, rect, oval, and roundrect shapes; round or oval drills; copper layer names, `*.Cu`, and `F&B.Cu`; and a pad `zone_connect` override of `1`, `2` or `3` — the three that attach the pad to a same-net pour — accepted as a proven no-op and modelled as nothing, per [ADR-0091](../adr/0091-attaching-pad-zone-connect-overrides.md). |
| Routed copper | Straight `segment` items, exact start/mid/end `arc` items, and through vias spanning the declared copper stack. Copper carrying no routable net converts as an obstacle with `net_id` `None` rather than refusing the board, per [ADR-0078](../adr/0078-netless-copper-as-obstacle.md); it is an obstacle only and contributes nothing to connectivity. |
| Net-tie copper | A footprint declaring `net_tie_pad_groups` may draw its deliberate short as an `fp_poly` on `F.Cu`/`B.Cu`. That polygon converts to netless obstacle copper under the same `net_id` `None` contract, so the short is modelled as something to route around and never as a connection. Every other primitive a net-tie footprint could draw the short with — `fp_line`, `fp_arc`, `fp_rect`, `fp_circle` — is refused by name. See [ADR-0092](../adr/0092-net-tie-copper-as-netless-obstacle.md). |
| Zones | Net-bound, single-copper-layer, solid zones with one polygon loop; explicit priority, thermal/through-hole-thermal/solid/none pad connection, always/never island removal, clearance, and conditionally required thermal dimensions. |
| Keepouts | Copper-layer sets, exactly one polygon loop, the five modeled prohibition flags, and lock state. |
| Constraints | A caller-supplied `KiCadConstraintProfile` containing net classes, a default class, optional per-net-name assignments, differential-pair rules, and length rules. |

The source revision is the SHA-256 digest of the exact input bytes. One native UUID or legacy tstamp
is used for item identity when available; simultaneous identity fields are ambiguous and rejected.
An outline contour assembled from `Edge.Cuts` `gr_line` segments takes a composite native identity,
`contour:assembled:` plus a hash of the sorted set of its member segments' own UUIDs, provided every
member carries exactly one native identity and no value repeats within the member set — see
[ADR-0087](../adr/0087-composite-native-identity-for-assembled-outlines.md). Otherwise identity is
derived deterministically from the source revision and source locator, and every source-preserving
patch path refuses a snapshot containing such a `:derived:` identity. Net IDs are deterministic from
net names.

The converter performs a version-specific semantic preflight. Root and footprint graphics on any
copper layer are rejected. Footprint graphics on `Edge.Cuts` are also rejected, and the only accepted
root `Edge.Cuts` primitives are one unfilled rectangle and straight `gr_line` segments.
Non-routing documentation graphics may be ignored. Supported unfilled orthogonal courtyard centerlines are the exception: they become
canonical board-frame rings. For the supported front/back footprint subset, KiCad's authored board-frame child
coordinates are imported as written; the adapter does not apply a second mirror when a footprint
is on `B.Cu`. `filled_polygon` data is treated as a derived KiCad fill cache: v0.2 records zone fill intent,
not cached fill geometry, fill freshness, or connectivity proof.

### Rejected today

The adapter fails closed on any unsupported construct required to represent the board faithfully,
including:

- `Edge.Cuts` arcs, circles, polygons and curves; more than one outline contour, including a
  rectangle drawn alongside a segment loop; and any segment set that is not exactly one closed
  simple loop — an open contour, a near-miss gap KiCad's own chaining tolerance would close, a
  branching spur, a duplicate or zero-length segment, a self-intersection, or two disjoint loops.
  The outline is routing room, so it is never repaired into something larger than what was drawn;
- `Edge.Cuts` outline holes;
- root or footprint-local text/graphics on copper — including a root `gr_text` on `F.Cu`, which is
  real copper and so would have to be *over*-approximated to be admitted at all, and a containing
  glyph envelope needs font metrics Board IR does not model
  ([#141](https://github.com/seunghyukchoe/copper-mcp/issues/141)) — and any footprint-local
  `Edge.Cuts` primitive. The net-tie `fp_poly` above is the one exception, and only when its
  footprint declares `net_tie_pad_groups`;
- footprint rotations not divisible by 90 degrees;
- courtyard ring edges at any slope other than horizontal, vertical, or an exact 45-degree chamfer
  (`|dx| == |dy|`); an `fp_circle` courtyard whose radius is not an exact integer nanometre, or one
  whose bounding box meets any sibling courtyard shape's bounding box — a circle cannot join the
  even-odd ring-nesting hierarchy, so an overlap there would silently subtract keep-out area, and
  the box test is deliberately conservative (it may refuse an exotic legal arrangement, never admit
  an unsound one); `fp_arc` courtyards; and filled, open, branching,
  duplicate-edge, mixed-layer, or otherwise unsupported courtyard topology, a courtyard layer that
  disagrees with the supported front/back footprint, and more than 64 courtyard shapes on one
  footprint;
- custom or other unmodeled pad shapes and custom pad primitives. Pad **kind** and pad **shape**
  are two separate refusals, because one message covering both positional tokens of a pad header
  named neither. All four of KiCad's documented kinds (`PAD_ATTRIB`: PTH, SMD, CONN, NPTH) are now
  modelled, so a refused kind token is not a documented pad kind at all and refuses without being
  named, with the indexed locator still saying which pad. A **copper-less `connect` pad** is the
  one form that still refuses: the paste/mask aperture skip tests the source token and requires
  literally `smd`, so an aperture-shaped edge-connector pad is refused rather than read past
  ([ADR-0096](../adr/0096-edge-connector-pads-convert-as-smd.md));
- root sections the KiCad format defines and Board IR v0.2 does not model, each refused by name
  from a closed table: `dimension` objects, embedded `image`s, and root board `property` text
  variables ([#140](https://github.com/seunghyukchoe/copper-mcp/issues/140)). A root head absent
  from that table refuses without being named, with the indexed locator still saying where it sits;
- blind, buried, or microvias in KiCad input;
- multiple polygon loops or holes in a zone/keepout, multi-layer copper zones, hatched fills,
  smoothing, minimum-area island removal, and other unmodeled zone semantics;
- non-neutral capping/filling/covering/plugging, non-default board via tenting, per-via tenting
  overrides, and pad/via copper-removal or custom-connectivity options;
- a pad `zone_connect` of `0`, which detaches the pad from its pour, and any value outside
  KiCad's `ZONE_CONNECTION` enum; and pad-level `clearance`, `offset`, `options`, `primitives`,
  `thermal_bridge_angle`, `thermal_bridge_width` and `thermal_gap`, each refused by name;
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
object count, vertices, intersection work, and diagnostics. An in-process caller may supply its own
positive `ParseLimits` value.

Six of these are **operator-settable**, and are taken as configured rather than clamped down:
`max_tokens`, `max_nodes`, `max_children_per_list`, `max_objects`, `max_total_vertices` and
`max_intersection_tests`, through the matching `COPPER_MCP_MAX_PARSE_*` environment variables and
the single `parse_budgets.parse_limits_for()` seam, so a budget moves for every board-reading
service at once or for none ([ADR-0079](../adr/0079-discriminated-configurable-parse-budgets.md);
before it, thirteen call sites hardcoded the structural budgets and an operator could move only the
byte ceiling). `max_input_bytes` is the deliberate exception and keeps `min` semantics against
`COPPER_MCP_MAX_BOARD_BYTES`, which also governs workspace reads, DRC captures and live-editor
serializations and must not widen the parser as a side effect. `max_depth`, `max_atom_chars`,
`max_vertices_per_ring` and `max_diagnostics` are deliberately not exposed: they bound the shape of
one construct rather than the scale of a document.

These limits are compatibility and security boundaries, not promises that every
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
