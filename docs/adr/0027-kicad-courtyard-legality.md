# ADR-0027: Legalize rectangular courtyards against a versioned KiCad cache oracle

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0004, ADR-0024, ADR-0026, R-034, R-035

## Context

ADR-0026 made supported courtyard rings exact, revision-bound Board IR geometry but deliberately
left `courtyard_overlap` at `not_modelled`. A picture or a carried contour is not evidence that a
legalizer ran. Placement fidelity now requires a bounded check whose boundary cases agree with the
authoritative editor rather than with an intuitive rectangle predicate.

KiCad 10.0.5 does not compare the saved centerlines directly. It polygonizes each courtyard with a
0.005 mm maximum error and contracts the cached outline by 0.005 mm so nominal touch is legal. It
then compares front with front and back with back. As a result, two axis-aligned rectangles first
violate at exactly 10,000 nm of nominal penetration, not at 1 nm. Missing courtyards are a separate,
default-ignored check. Project custom rules can set positive or negative courtyard clearance, but
Board IR does not carry that rule context.

Multiple courtyard rectangles add another topology boundary. KiCad converts all same-layer
outlines on one footprint into one polygon set: local 10.0.5 JSON DRC measurements found strict
disjoint shapes valid, edge-touch malformed, partial overlap merged, and nesting interpreted as a
hole. Board IR 0.2 does not encode union/hole roles and the rectangular evaluator treats rings as
independent solids, so intersecting same-footprint rectangles cannot receive a sound verdict.

Tiny cached shapes have a second non-linear boundary. With `missing_courtyard` enabled, coincident
square and X-short rectangles are clear at the tested 10,050 nm adjacent case and overlap at
10,051 nm; the Y-short transition occurs slightly earlier. A plain 5,000 nm integer inset would
falsely call some shapes in that orientation-sensitive band collisions.

## Decision

CopperMCP publishes Placement `0.2.0` with required two-valued courtyard overlap:

```text
courtyard_overlap = proven_clear | violated
courtyard_policy = kicad-10.0.5-rect-cache-v1
```

The deterministic legalizer will:

1. require each Board IR courtyard to be an axis-aligned rectangle and require multiple rectangles
   on one footprint to be strictly disjoint; touching, overlapping, and nested sets fail closed;
2. require both rectangle dimensions to be at least 10,051 nm, the conservative square/X-short
   adjacent boundary and safely above the repeated Y-short collision cases; smaller rectangles
   return typed `unsupported_geometry` rather than an approximate verdict;
3. start from the immutable Board IR 0.2 footprint collection rather than reparsing the board;
4. keep unmoved rings in their canonical board frame and transform moved rings from source pose to
   proposed orthogonal pose with exact integer nanometre arithmetic;
5. contract each transformed rectangle by 5,000 nm on every edge, matching the pinned KiCad cache;
6. compare distinct footprints only on the same physical side, with contact between contracted
   rectangles counted as collision;
7. include padless, locked, and otherwise unmoved footprints as fixed obstacles;
8. skip source footprints with no courtyard, matching the overlap provider, while reporting their
   count separately; and
9. charge every footprint scan, transformed vertex, footprint-pair bound, and rectangle-pair
   predicate to the existing shared check/deadline budget.

Evidence reports `courtyard_footprints_checked`, `courtyard_pairs_checked`, and
`missing_courtyard_footprints`, so a vacuous clear result cannot look like complete geometry
coverage. `courtyard_overlap` is required at construction and has no default; a future call site
cannot silently mint a result without running the evaluator. A courtyard violation participates in
the same immutable candidate gate as pad overlap, outline containment, and footprint keepouts.

Because the public output enum widens from a one-value literal and candidate canonical bytes now
contain new evidence, `PLACEMENT_VERSION` becomes `0.2.0`. Candidate IDs intentionally change.
Board IR and Circuit Scene remain at 0.2.0, `validate-snap-v1` remains the ordering policy, and the
Python package remains on the unreleased 0.4 development line.

## Consequences

- The supported rectangular subset moves from 0/9 determinate courtyard-oracle cases to a target
  of 9/9, including the 9,999/10,000 nm boundary, with no false positive or false negative against
  local KiCad 10.0.5.
- A proposed pose can be refused solely because its supported courtyard collides, even when its pad
  geometry is clear. Padless mechanical footprints are no longer invisible obstacles.
- Ambiguous same-footprint topology is rejected before a snapshot or candidate exists. This is a
  conservative limitation: some rejected overlap/nesting forms are valid KiCad unions or holes.
- Rectangles below 10,051 nm on either axis remain valid Board IR observations but receive a typed
  placement refusal. This avoids both false collisions and treating an empty KiCad cache as proof.
- Existing MCP clients generated against Placement 0.1's closed enum must regenerate for 0.2.
  There is no placement apply consumer, so old placement candidate IDs are not actionable.
- The policy is intentionally patch-version-specific. A KiCad cache-semantic change requires a new
  oracle matrix and policy/version review, not an unannounced tolerance change.
- `proven_clear` is not custom `courtyard_clearance`, missing-courtyard approval, full KiCad DRC,
  general topology, back-side source import, electrical validation, or authorization to apply.

## Alternatives considered

- **Compare saved rectangles with ordinary open overlap:** rejected because it produces false
  positives for 1 through 9,999 nm of penetration relative to KiCad 10.0.5.
- **Use an arbitrary epsilon without naming KiCad:** rejected because an unexplained tolerance is
  neither reproducible nor safely upgradeable.
- **Treat every same-footprint rectangle as an independent solid:** rejected because KiCad's merged
  polygon topology can make overlapping outlines a union and nested outlines a hole. Strict
  disjointness is the bounded sound subset until those relationships are represented.
- **Run KiCad DRC for every preview:** deferred. No placement serializer can yet prove that a
  disposable moved board preserves all unmodelled footprint content, and implicit external-tool
  execution would change the current latency and availability contract.
- **Treat missing courtyards as overlap failure:** rejected because KiCad models missing geometry as
  a separate check. Coverage counts expose the gap without inventing an overlap.
- **Implement custom positive/negative clearance now:** rejected until rule context is captured and
  revision-bound. Calling zero-clearance overlap “clearance” would overclaim.

## Verification

- [Research and source matrix](../research/courtyard-legality-references.md)
- `scripts/benchmark_placement_courtyards.py`
- Placement and adapter tests for transformed non-square rectangles, the exact 9,999/10,000 nm
  penetration threshold, the 10,050/10,051 nm tiny-cache support boundary, strict-disjoint/
  edge-touch/overlap/nesting topology, fixed padless obstacles including a non-orthogonal stored
  pose, opposite sides, missing-shape and pair-budget exhaustion, candidate identity, MCP schema,
  and deterministic replay
- Local KiCad CLI 10.0.5 JSON DRC comparison; hosted CI remains dependency-light and skips the
  external executable where it is unavailable. The committed runner also repeats twelve tiny-cache
  cases five times with `missing_courtyard` enabled.
