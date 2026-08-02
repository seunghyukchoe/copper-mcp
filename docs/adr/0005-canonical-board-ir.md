# ADR-0005: Canonical integer Board IR v0.1

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`
- Related: [Issue #9](https://github.com/seunghyukchoe/copper-mcp/issues/9)

## Context

Routing, validation, replay, benchmarks, and future MCP resources need one board representation whose
meaning does not depend on a GUI session, Python object order, floating-point rounding, or a
particular routing backend. Passing native KiCad syntax deeper into the system would couple every
consumer to one evolving file format. Accepting approximate geometry would make content hashes and
correctness evidence unreliable.

The first contract must also be small enough to review. It is a foundation for routing work, not a
claim that CopperMCP can already import every KiCad board or generate and apply copper.

## Decision

CopperMCP defines the canonical snapshot discriminator `copper.board-ir` at schema version `0.1.0`.
The reference implementation is an immutable, MCP-independent Python domain package with a strict
JSON codec and a versioned [JSON Schema](../../schemas/board-ir/0.1.0.schema.json).

### Numeric and geometry invariants

- Distances are signed integer nanometres (`nm`); angles are integer microdegrees (`udeg`) normalized
  into one full rotation. Floating-point JSON values are not part of the contract.
- Integers stay within the interoperable JSON range `[-(2^53 - 1), 2^53 - 1]`. Dimensions that must
  be positive or non-negative have tighter typed bounds.
- Decimal millimetre and degree tokens are converted exactly. Exponent notation, sub-unit values,
  overflow, booleans, NaN, infinity, and implicit rounding are rejected.
- IDs are typed and stable within one source revision. References, uniqueness, layer spans, net-class
  coverage, ring topology, degeneracy, and self-intersection are validated before serialization.
- The v0.1 model covers exactly one hole-free board contour, copper layers, nets, typed net classes
  and assignments, differential-pair and length rules, pads, full-stack through vias, segments,
  three-point arcs, solid zones, keepouts, lock state, and a content-addressed source revision.

### Canonical bytes and integrity

Construction normalizes collection order, copper-layer references, via span direction, and polygon
orientation/start points. Canonical JSON uses UTF-8, sorted object keys, compact separators, no
non-finite numbers, and one trailing newline.

Two SHA-256 digests have separate meanings:

- `constraint_digest` hashes the canonical typed constraints plus the sorted net-ID set. Net display
  names and board geometry do not affect this digest.
- `snapshot_digest` hashes the canonical `content` object. The outer envelope is therefore
  self-verifying without recursively hashing its own digest field.

The decoder accepts only the exact `0.1.0` structure, rejects duplicate or unknown fields and all
floating-point numbers, applies parser/geometry budgets, normalizes and validates the content, and
verifies both digests. Writers normalize direct domain objects and enforce the default decoder
envelope budget; verification rejects noncanonical hand-built envelopes. Source decimal conversion
uses string and integer arithmetic and is independent of process-global decimal context.

### Source adapters

Source-format adapters remain outside the domain package. The initial KiCad adapter is read-only,
bounded, and deliberately narrow. It accepts only the subset documented in
[Board IR and KiCad adapter contracts](../architecture/board-ir.md): one rectangular `Edge.Cuts`,
front-side orthogonal footprint transforms, the four modeled pad shapes, segments, three-point
track arcs, full-stack through vias, and single-loop solid zones/keepouts. The adapter supports only
KiCad PCB format `20260206`, validates its copper-layer numbering/order, preserves modeled zone
priority/connection/island semantics, and rejects unmodeled copper or `Edge.Cuts` graphics. Typed
routing constraints are supplied separately through `KiCadConstraintProfile`; they are not inferred
from project or custom rule files.

Unsupported or malformed source constructs produce a structured error and no snapshot. The adapter
does not approximate curves, flatten unsupported geometry, retain source bytes, modify a KiCad file,
route a net, preview a candidate, or apply a candidate.

### Versioning

Schema and software versions are independent. A change to serialized fields, numeric meaning,
normalization, digest projections, validation invariants, or the interpretation of an existing field
requires a new Board IR schema version, fixtures, compatibility tests, an ADR or amendment, migration
guidance, and a changelog entry. Readers reject unknown versions until support is implemented
explicitly; writers never silently downgrade or discard unsupported data.

Expanding a source adapter without changing canonical meaning may remain on `0.1.0`, but its
documented support matrix and regression fixtures must change with the implementation.

## Consequences

- Routing policies, CPU/Rust/GPU backends, datasets, and MCP adapters can share byte-stable inputs.
- Fail-closed conversion prevents unsupported source syntax from becoming plausible but incorrect
  geometry.
- Nanometres and exact topology make validation reproducible, at the cost of rejecting source values
  that cannot be represented exactly.
- The initial KiCad subset rejects many ordinary boards, especially outlines composed from lines or
  arcs. Compatibility must expand fixture by fixture rather than through permissive fallback.
- Board IR does not remove the need for candidate provenance, authoritative KiCad DRC, physics/DFM
  evidence, or explicit apply authorization.

## Alternatives considered

- Native KiCad S-expressions as the domain model: rejected because format details and parser concerns
  would leak into routing and MCP contracts.
- Floating-point millimetres: rejected because cross-runtime rounding would destabilize equality,
  topology, and hashes.
- Silently tessellating unsupported curves: rejected because tolerance choices would change board
  meaning without user-visible evidence.
- A permissive, forward-compatible decoder: rejected for v0.1 because unknown fields could encode
  constraints that an older router would ignore.
