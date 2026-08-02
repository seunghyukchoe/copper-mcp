# ADR-0006: Bounded deterministic A* as the first routing reference

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

CopperMCP needs an executable routing baseline before multicore, negotiated-congestion, GPU, or
learned-policy work can be measured honestly. A broad first implementation would make it difficult
to distinguish unsupported geometry from routing failure and could accidentally present internal
checks as authoritative KiCad DRC.

## Decision

The first backend is an integer-only, four-neighbour A* search for one selected two-pad net on one
copper layer. It operates on a verified Board IR snapshot, uses explicit node and expansion budgets,
supports cancellation, and returns a content-addressed immutable patch rather than mutating a board.

Obstacle count and actual obstacle-relation checks have independent ceilings so Board IR object
budgets cannot multiply the search budget into unbounded work. Cancellation checkpoints also occur
during preparation and long obstacle scans. Malformed direct API object types return typed failures.

The accepted geometry is deliberately narrow: one axis-aligned rectangular outline, two target pads,
and optional axis-aligned rectangular track keepouts. Existing selected-layer copper, vias, zones,
additional pads, outline holes, and unsupported keepout geometry fail closed. Exact Board IR revision,
net-class width and clearance, settings, seed, policy, deterministic search metrics, and compressed
vertices are part of candidate provenance.

Selected-net differential-pair and length rules also fail closed until their objectives can be
represented and checked; the baseline accepts signal layers only.

This reference backend performs internal grid and obstacle checks only. KiCad export, candidate
preview, authoritative KiCad DRC, MCP exposure, and application remain separate future gates.

## Consequences

The project gains a replayable CPU oracle for tests and future differential implementations. Its
small support surface makes failures explicit and provides a credible baseline for profiling. It is
not a general PCB autorouter, does not route CopperTone, and cannot establish fabrication safety.

Future geometry support must add exact regression cases before widening acceptance. Accelerated
backends must match the canonical candidate contract or declare a separately versioned policy.

## Alternatives considered

- Start with multi-net negotiated congestion: deferred until single-net geometry and identity are
  executable and benchmarked.
- Call an external autorouter first: rejected as the reference contract because output semantics,
  resource bounds, and reproducibility would not be under project control.
- Let an AI model emit traces: rejected because deterministic geometry and correctness remain the
  authority boundary.
