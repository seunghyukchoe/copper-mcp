# Negotiated physical-clearance acceptance gate

## Scope

This research note records the deliberately bounded fidelity slice added after ADR-0055.  The
negotiated coordinator still routes distinct two-pin requests on one common signal layer and
lattice.  Its existing edge/vertex ledger detects only resource sharing.  A lattice-clean result
can therefore contain parallel copper that is physically too close once the tracks' widths are
considered.

The new gate checks every pair of returned candidates before the coordinator accepts an iteration.
For each pair it obtains the two assigned Board IR net classes, requires the candidate width to
equal its net-class track width, and uses the stricter of the two class clearances.  Each orthogonal
segment is its centreline swept by a closed disc of half the track width.  Coordinates are doubled,
so odd nanometre widths have exact half-width bounds without floating point.  The squared Euclidean
distance between centrelines is compared with the squared sum of both half widths and the doubled
clearance: equality is legal and only a smaller separation fails.  The construction is deterministic,
integer-only, and has a checked pair-comparison budget plus a cooperative cancellation cadence.

An iteration that fails this gate cannot contribute candidate copper to the best result or the
published response.  The coordinator continues within its ordinary bounded iteration budget, but
does not yet feed the physical conflict back as a spatial reroute penalty.  This is acceptance
fidelity, not a complete negotiated physical router.

## Primary sources and design implications

- [KiCad PCB Editor: net classes](https://docs.kicad.org/master/en/pcbnew/pcbnew.html#net-classes)
  documents that net classes configure routing and clearance rules, while board constraints remain
  minimums.  Board IR's immutable one-assignment representation is the applicable source for this
  slice; it does not attempt KiCad's multi-class priority or custom-rule evaluation.
- [KiCad custom design-rule source documentation](https://gitlab.com/kicad/code/kicad/-/blob/f75c72ebb547987ec856d8e9b58561dcf2f99db5/pcbnew/dialogs/panel_setup_rules_help.md)
  defines `clearance` as electrical spacing between copper objects of different nets and distinguishes
  the more general `physical_clearance` check.  The gate is intentionally only a same-layer,
  different-net candidate-pair check rather than an implementation of either full KiCad rule system.
- [McMurchie and Ebeling, *PathFinder*](https://doi.org/10.1109/fpga.1995.242049) describes negotiated
  routing with present and historical congestion costs.  ADR-0055 already uses that bounded
  allocation pattern.  Physical verification remains a separate acceptance condition so it cannot
  be mistaken for an A* cost or silently weaken an immutable candidate.
- [PathFinder paper copy](https://janders.eecg.utoronto.ca/1387/readings/pathfinder.pdf) is the primary
  algorithm reference consulted for the distinction between iterated resource negotiation and final
  legal allocation.

## Residual limits

This is not KiCad DRC, fabrication clearance analysis, multilayer verification, a FreeRouting
equivalent, or proof of board-wide clearance.  It excludes board copper already present before the
candidate set, pads, vias, arcs, zones, custom KiCad rules, net-class priority aggregation,
differential-pair constraints, edge and hole clearance, and other board-wide geometry.  The
orthogonal centreline-plus-disc segment model is exact only for the stated model; it is intentionally
documented rather than presented as a KiCad geometry claim.
