# ADR-0156: Project operating point is bounded observation

- Status: Proposed; publication held pending corrected-source validation
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0150](0150-confined-spice-source-preserves-declared-settings.md),
  [ADR-0151](0151-operating-point-numbers-are-not-physics-authority.md),
  [ADR-0153](0153-confined-ngspice-execution-confirms-cleanup.md),
  [ADR-0154](0154-operating-point-cases-bind-explicit-inputs.md),
  [ADR-0155](0155-operating-point-topology-is-conservative-preflight.md)

## Decision

Derive and retain one internally verified project SPICE export acquisition for the lifetime of one
bounded nominal-DC operation. Explicit cases bind declared rails, loads, temperature and native
pins; conservative topology preflight runs before deck naming. Deck construction consumes exact
captured model definitions and produces only the fixed executor input.

For every admitted case, run the fixed runtime exactly twice under the shared deadline. Require
clean bounded diagnostics, the expected complete operating-point vector schema, finite retained
numeric spelling, matching replay observations and fixed image identity. After report hashing,
recapture declared artifacts and recheck original project source bytes. The public SPICE-export
signature, report and digest namespaces remain unchanged.

The supported profile is passive/diode nominal DC only. Case duration is not exercised in this DC
operation. Calibration and model accuracy remain not_run; engineering validation is inconclusive
and apply authority is none.

## Non-authority and remaining gates

This does not establish SI, PI, thermal or EMC performance, physical correctness, convergence,
complete source authenticity beyond its bounded freshness checks, human approval, a new MCP surface
or any mutation/apply capability. It does not invoke a live provider. Broader components, transient
behavior, calibration, independent engineering criteria, protected-hosted validation and release
review remain separate gates.

The matched full evidence for original checkpoint 45b42f1 predates the shared-container cleanup
P1 found in PR300. It remains historical evidence for that original source only; a corrected-source
full validation is required before publication.
