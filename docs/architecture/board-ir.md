# Board IR and KiCad Adapter Contracts

Board IR is the deterministic board snapshot that future routing, replay, benchmark, and MCP layers
can share. The current contract is `copper.board-ir` version `0.1.0`. It is implemented as a pure
domain package, strict JSON codec, JSON Schema, and a narrow read-only KiCad converter.

This slice does **not** implement routing, candidate preview, board mutation, candidate application,
or an MCP tool that exposes Board IR. The existing MCP inspection and DRC services remain separate
until an application-service integration is designed.

See [ADR-0005](../adr/0005-canonical-board-ir.md) for the durable decision and versioning rules.

## Contract map

| Artifact | Current responsibility |
|---|---|
| `copper_mcp.board_ir.types` | Immutable typed units, geometry, constraints, items, and snapshot envelope. |
| `copper_mcp.board_ir.validation` | Reference, identity, budget, degeneracy, and exact polygon-topology checks. |
| `copper_mcp.board_ir.canonical` | Normalization, canonical JSON bytes, constraint digest, and snapshot digest. |
| `copper_mcp.board_ir.codec` | Strict bounded decoding of untrusted `0.1.0` JSON. |
| `copper_mcp.adapters.kicad_board_ir` | Fail-closed conversion of the documented KiCad subset from bytes. |
| [`0.1.0.schema.json`](../../schemas/board-ir/0.1.0.schema.json) | Portable serialized-envelope contract. |

The board model has no dependency on MCP, GUI APIs, provider SDKs, routing backends, or filesystem
access. The source adapter accepts bytes supplied by its caller and does not mutate or retain them.

## Envelope

The serialized shape is:

```json
{
  "schema": "copper.board-ir",
  "schema_version": "0.1.0",
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
- Integers are capped at `2^53 - 1` in magnitude so JSON consumers do not lose precision. Positive
  widths, diameters, and drills cannot be zero; typed non-negative values may be zero.
- Millimetre/degree source tokens use ordinary decimal notation and must convert exactly. There is no
  rounding path for sub-nanometre or sub-microdegree input.
- IDs use type prefixes such as `layer:`, `net:`, `class:`, `pad:`, `via:`, `segment:`, `arc:`,
  `zone:`, `keepout:`, `contour:`, and `rule:`. IDs and display names have different roles.
- All references must resolve. Layer indices and object IDs are unique, every net has exactly one
  net-class assignment, via spans follow copper-stack order, and locked state is explicit.
- Rings contain at least three distinct vertices, omit a duplicated closing point, have nonzero area,
  and cannot self-intersect. Outer rings are canonical counter-clockwise; holes are clockwise.

## Typed model coverage

| Area | v0.1 representation |
|---|---|
| Board | One or more named outline contours, each with an outer ring and optional holes. |
| Stack | Ordered copper layers of kind `signal`, `plane`, or `mixed`. |
| Connectivity | Stable net IDs with UTF-8 display names. |
| Constraints | Net classes, one assignment per net, differential-pair rules, and min/max length rules. |
| Terminations | SMD, through-hole, and NPTH pads; circle, rectangle, oval, and rounded-rectangle shapes. |
| Existing copper | Straight segments, exact three-point arcs, through/blind-buried/micro via types in the model, and solid zones. |
| Exclusions | Multi-layer polygonal keepouts with explicit track, via, pad, zone, and footprint prohibitions. |
| Provenance | Source format/version/generator, source SHA-256 revision, constraint digest, and snapshot digest. |

Model coverage is broader than the initial KiCad adapter. In particular, the model can represent
outline holes and non-through via kinds even though the converter does not yet import them.

## Canonicalization and digests

Before hashing, construction normalizes:

- copper layers by stack index;
- nets and board items by stable ID;
- constraint records by stable ID or assigned net ID;
- pad and keepout layer sets by stack order;
- via start/end layers into stack order; and
- ring start point, orientation, contour order, and hole order.

Canonical JSON is strict UTF-8 with sorted keys, compact separators, no floating-point/non-finite
numbers, and one trailing newline. `constraint_digest` is SHA-256 over canonical constraints and the
sorted net-ID set. `snapshot_digest` is SHA-256 over canonical `content`; it intentionally excludes
the envelope so the digest is not recursive. Both use the `sha256:` prefix.

Decoding is also strict: the byte budget is checked first, duplicate and unknown keys fail, floats
fail, the schema discriminator/version must match exactly, integer and structure budgets apply, and
the normalized result must pass semantic validation and both digest checks.

## KiCad read-only subset

`parse_kicad_bytes(source, profile, limits)` parses exactly one bounded UTF-8 S-expression and
returns a `ConversionResult`. A successful result contains a verified snapshot. Any conversion error
contains a bounded machine-readable diagnostic and no snapshot.

### Accepted today

| KiCad construct | Accepted subset |
|---|---|
| Board metadata | `kicad_pcb`, numeric `version`, optional `generator`, and declared `.Cu` layers whose kinds map to the Board IR layer model. |
| Nets | Named item-level net references and legacy numeric root declarations. |
| Outline | Exactly one `gr_rect` on `Edge.Cuts`; it becomes the single imported contour. |
| Footprints/pads | Footprints on `F.Cu` with rotations in 90-degree increments; `smd`, `thru_hole`, and `np_thru_hole` pads; circle, rect, oval, and roundrect shapes; round or oval drills; copper layer names and `*.Cu`. |
| Routed copper | Net-bound straight `segment` items, exact start/mid/end `arc` items, and through vias spanning the declared copper stack. |
| Zones | Net-bound, single-copper-layer, solid zones with exactly one polygon loop and typed clearance/thermal dimensions. |
| Keepouts | Copper-layer sets, exactly one polygon loop, the five modeled prohibition flags, and lock state. |
| Constraints | A caller-supplied `KiCadConstraintProfile` containing net classes, a default class, optional per-net-name assignments, differential-pair rules, and length rules. |

The source revision is the SHA-256 digest of the exact input bytes. Native UUID/tstamp values are
used for item identity when available; otherwise identity is derived deterministically from the
source revision and source locator. Net IDs are deterministic from net names.

### Rejected today

The adapter fails closed on any unsupported construct required to represent the board faithfully,
including:

- `Edge.Cuts` lines, arcs, circles, polygons, curves, additional rectangles, and mixed/non-rectangular
  outlines;
- back-side footprints and footprint rotations not divisible by 90 degrees;
- custom or other unmodeled pad shapes and custom pad primitives;
- blind, buried, or microvias in KiCad input;
- multiple polygon loops or holes in a zone/keepout, multi-layer copper zones, and non-solid fills;
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
input below each individual cap is cheap.

Diagnostics expose a stable code, `warning` or `error` severity, bounded message, source locator, and
optional object kind/ID. They do not include or echo the source board. The current adapter returns the
first conversion error, so downstream code must treat `snapshot is None` as a hard failure rather
than attempting partial routing.

## Contract fixtures

- [`schema-valid.json`](../../tests/fixtures/board-ir-v0.1/schema-valid.json) is the golden canonical
  snapshot produced by the synthetic KiCad subset.
- [`schema-invalid.json`](../../tests/fixtures/board-ir-v0.1/schema-invalid.json) is rejected by both
  the JSON Schema and runtime decoder.
- [`subset.kicad_pcb`](../../tests/fixtures/board-ir-v0.1/subset.kicad_pcb) exercises pads, a segment,
  an arc, a through via, a solid zone, a keepout, exact UTF-8 net identity, and typed constraints.
- [`malformed-unbalanced.kicad_pcb`](../../tests/fixtures/board-ir-v0.1/malformed-unbalanced.kicad_pcb)
  exercises bounded fail-closed S-expression parsing.

## Evolution rules

Breaking canonical changes require a new schema version, fixtures, compatibility tests, migration
guidance, ADR review, and changelog entry. Source-adapter coverage may expand under `0.1.0` only when
the resulting canonical meaning is unchanged and the accepted/rejected matrix plus fixtures are
updated. Unknown Board IR versions and unknown fields remain errors.

Related contracts remain separate:

- [`board-manifest.schema.json`](../../schemas/board-manifest.schema.json) is the existing bounded
  inspection manifest used by current services.
- [`candidate.schema.json`](../../schemas/candidate.schema.json) is the current candidate metadata
  contract; it is not yet a Board IR geometry patch.
