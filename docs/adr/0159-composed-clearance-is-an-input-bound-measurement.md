# ADR-0159: Composed clearance is an input-bound measurement

- Status: Proposed; private implementation reviewed, integration pending
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0133](0133-native-optimization-execution-and-host-confirmation.md),
  [ADR-0160](0160-custom-rule-loading-needs-native-witness.md)

## Decision

Provide a private measured/unavailable clearance observation over a complete supplied Board IR
snapshot. Bind it to the snapshot and constraint digests, method version and charged work. The
observation contains no geometry, native names or executable selection; its representation is
redacted. It is not authenticated source evidence, native custom-rule compliance or a DRC verdict.

The supported geometry includes arbitrary straight tracks, through-vias, circular pads and
quarter-turn rectangular, oval and roundrect pads. Each core uses exact doubled integer
coordinates and rational squared distances. Subtract the two swept radii, clamp physical
separation to zero, round down to whole nanometres, then subtract the stricter of the two assigned
IR net-class clearances. The minimum covers all foreign-net copper pairs sharing physical layers,
including existing copper and pads; no Manhattan proxy enters the measurement.

All examined pairs and geometric work consume the cumulative probe budget. Admission bounds
the complete nested input before hashing. Cancellation, expiry, malformed controls and stale
digests refuse without private exception context. Unsupported geometry/rule semantics or no
comparable pair produces unavailable with no invented margin. A negative margin is an IR-class
spacing deficit, not overlap depth or an engineering failure classification.

## Compatibility and limits

The existing optimization/v1 package remains unchanged: its zero-clearance baseline is not
silently relabelled as a measurement. A later explicit versioned package must carry availability,
measurement binding and method interpretation before using this result in production ranking.
The current helper alone does not close placement.measured_ranking or held-out improvement.

Custom copper envelopes, arcs, zones, unsupported rule/layer semantics and unassigned copper
are not silently omitted. The observation does not cover drill/edge clearance, unseen native
rules, fabrication limits, electrical intent, SI/PI/thermal/EMC or application authority.

## Evidence and remaining gates

The private implementation and parser-produced QuotedAtom compatibility were independently
reviewed. Seventy-three focused controls and two independent arithmetic-oracle tests passed;
the latter exercise 128 seeded projection cases and translation/reflection invariance.
Owned native circular-pad cases at 0.49/0.50/0.51 mm spacing agreed with an explicit 0.5 mm rule
after the fixture's unit error was corrected. These are fixture comparisons, not physical
calibration or broad native shape coverage. The newly integrated shared DRC correction still
requires fresh validation of this combined source.

Full validation, versioned ranking integration, equal-budget identity comparisons, native shape
coverage and the frozen held-out quality objective remain outstanding. No readiness, human
approval, live mutation or saving capability is added.
