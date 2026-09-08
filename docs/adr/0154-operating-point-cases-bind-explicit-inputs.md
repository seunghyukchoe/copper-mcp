# ADR-0154: Operating-point cases bind explicit inputs

- Status: Proposed; isolated binding controls validated
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0151](0151-operating-point-numbers-are-not-physics-authority.md),
  [ADR-0153](0153-confined-ngspice-execution-confirms-cleanup.md)

## Decision

Accept only bounded closed project-spice-operating-point-cases/v1 JSON. It binds its declaration
and project-capture digests to the internally admitted model-terminal binding, and requires one
through eight canonical unique case IDs. Parsing and resolution use copied bounded records, unique
identifiers, exact deadlines and a 128 KiB case-document ceiling.

Each case supplies an existing native ground pin that already normalizes to 0/GND, mappings for
every declared rail, explicit canonical energized-rail IDs, and explicit canonical current loads.
A mapping alone does not energize a rail. Only complete non-NC model pins may be selected. Rail
endpoints must be distinct. Loads retain supplied direction and must match either declared
rail-endpoint ordering, which permits explicit negative-rail direction without silently flipping
endpoints; an unrelated pair refuses.

Rail voltage is copied from the declaration. Load current and duration are copied from the named
declaration load case; duration is metadata and is not exercised by nominal DC binding. Case
temperature requires declared operating limits and must lie within their range.

## Non-authority and remaining gates

This private binding does not authenticate caller-created declarations or bindings as source proof,
native execution proof, convergence, model accuracy, calibrated physics, engineering evidence,
human approval or apply authority. It emits no SPICE command or deck and does not select a backend.
Later composition must derive authenticated source/model/case inputs, create a bounded deck, run
the fixed executor, interpret output, recheck freshness and apply separate engineering criteria.
