# ADR-0155: Operating-point topology is conservative preflight

- Status: Proposed; isolated preflight controls validated
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0151](0151-operating-point-numbers-are-not-physics-authority.md),
  [ADR-0153](0153-confined-ngspice-execution-confirms-cleanup.md),
  [ADR-0154](0154-operating-point-cases-bind-explicit-inputs.md)

## Decision

Use a private conservative preflight before later operating-point execution. It reparses fresh
captured SPICE model source bytes, admits only positive R/C/L values, and expands subcircuits
iteratively under fixed source, instance-path, node and aggregate-work ceilings. Malformed,
unavailable, unsupported or deadline-expired records refuse.

Native hierarchical names are checked for case-folded collisions before hierarchy renaming, so a
top-level name cannot alias an internal instance name. The graph requires minimum incidence and
DC reachability to canonical ground. It rejects voltage/inductor constraint loops, including
parallel and self-loop forms. Current sources contribute connection information but do not create
a DC path; capacitors do not create a DC path either.

## Non-authority and remaining gates

This is topology preflight only. It does not authenticate source records, choose a backend, create
a deck, execute ngspice, prove convergence, establish physical correctness or model accuracy, or
produce engineering, approval or apply authority. A later coordinator must still bind authenticated
source/model/case inputs, construct the bounded deck, execute and interpret the fixed runtime,
recheck freshness and apply separate engineering criteria.
