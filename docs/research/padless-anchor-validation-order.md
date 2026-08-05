# Padless proposal-anchor validation order

- **Status:** Implemented boundary note
- **Date:** 2026-08-05

## Question

Which refusal wins when a placement request contains either a declared or anchored real, padless
footprint and an unrelated syntactic contradiction in its rules?

## Evidence

CopperMCP's placement contract deliberately permits only ref-anchored moves: candidate
coordinates are derived from the board geometry, not supplied by a model.  It also reserves
`infeasible_constraints` for a proof that a rule set cannot hold, specifically its small,
syntactic contradiction subset.  See [ADR-0024](../adr/0024-placement-intent-and-legalization.md).

Board IR v0.2 preserves footprint identity and total pad ownership while still allowing a
footprint to have no pads.  The placement view therefore retains that identity in
`padless_refs`, outside the placeable-footprint map: it can truthfully distinguish an
unsupported real footprint from an unresolved reference.  See the [Board IR contract](../architecture/board-ir.md)
and [ADR-0062](../adr/0062-stationary-padless-courtyard-envelope.md).

That boundary follows KiCad's model as well.  KiCad documents pads as objects added to a
footprint and documents the copper-layer shape and electrical behavior of pad types; it does
not make a graphics-only footprint a copper termination.  CopperMCP's smaller supported
subset accordingly needs a pad hull before it can evaluate a placement-anchor edge.

- [KiCad PCB Editor: Footprint pads](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#footprint-pads)
- [KiCad developer format: Board common syntax](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html#board-common-syntax)

## Decision

Before syntactic infeasibility analysis, scan declared subjects in request order, then the existing
rule-reference scan, then every explicit proposal anchor in input order. If any reference names a
known padless footprint, return the established `unsupported_geometry` diagnostic and publish no
candidate. `None` means the proposal is self-anchored and therefore is not an external reference to
validate.

The scan does not change legal proposal resolution, candidate canonicalization, reference ordering,
or the result for a rule set with no padless reference. Thus a pure
side/orientation/opposite-edge contradiction continues to return `infeasible_constraints`.

## Security rationale and regression

Validation must report the earliest unsupported boundary before it makes a stronger claim about
the rest of the request.  Otherwise untrusted input can mask an unavailable geometry capability
behind a later, unrelated contradiction, which confuses callers about what their request
contains and makes the refusal order dependent on incidental rule composition.  The regression
covers each supported proposal `anchor_point` (`center`, `north`, `south`, `east`, and `west`) plus
a padless declared subject, combines each with unrelated contradictory side rules, and asserts the
exact stable `unsupported_geometry` result with no candidate. Existing failure-taxonomy coverage
retains the pure-contradiction `infeasible_constraints` result.
