# ADR-0007: Disposable KiCad snapshots for route-candidate validation

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

An immutable Board IR route candidate must become valid KiCad source before the authoritative CLI
can evaluate it. Editing the live board would cross the apply boundary, while regenerating the whole
board from the deliberately narrow Board IR would discard source constructs that the adapter does
not own. Trusting only a candidate hash would also allow a correctly hashed but independently forged
patch to reach KiCad.

## Decision

`render_kicad_candidate_board()` is a pure, bounded bridge that returns new bytes and never writes the
source board. It accepts a candidate only when all of these bindings hold:

1. the supplied KiCad bytes and typed constraint profile reproduce the supplied Board IR snapshot;
2. the candidate base revision and SHA-256 identity are valid;
3. the bounded reference A* router reproduces the complete candidate exactly;
4. candidate net, layer, pad endpoints, settings, and geometry therefore remain bound to that source;
5. every modeled source geometry object has a native KiCad UUID/tstamp, so rewriting derivative
   metadata cannot churn revision-derived Board IR identities;
6. every orthogonal edge receives a deterministic UUIDv5 identity and exact nanometre-to-millimetre
   spelling within the configured input and total-object budgets;
7. native UUID/timestamp identities are collected once under parser bounds before collision checks;
8. the disposable derivative identifies `copper-mcp` and its package version as the KiCad writer;
   and
9. the rendered board parses back into Board IR with no modeled change except source revision,
   writer provenance, and the appended route segments.

The bridge returns disposable board bytes only. It does not invoke KiCad, produce a validated status,
write a preview, expose MCP, or apply a patch. A separately bounded DRC orchestration layer must copy
the full KiCad rule context, run the fixed CLI command, bind evidence to both base and candidate
identity, and discard stale results.

## Consequences

The first routing candidate now has a deterministic, source-preserving path to a private KiCad board.
Unit tests cover replay, tamper, stale source and candidate revisions, byte and total-object budget
rejection, precomputed native-identity collisions, writer provenance, and successful Board IR round
trips. Identity-less modeled geometry is rejected explicitly instead of being remapped across a
source-revision change. Where KiCad 10 is installed, an integration test confirms the disposable
two-pad candidate has zero violations and zero unconnected items while both source and rendered
files remain unchanged by DRC.

This evidence applies only to the committed synthetic fixture and supported A* subset. It is not
general KiCad compatibility, whole-board routing, production throughput, electrical validation, DFM,
or fabrication approval.

## Alternatives considered

- Mutate the source board before validation: rejected because validation must not cross the explicit
  apply authorization boundary.
- Regenerate a whole KiCad board from Board IR: rejected because Board IR v0.1 intentionally models
  only a strict subset and cannot preserve unowned source constructs.
- Serialize any content-addressed candidate: rejected because a valid hash proves integrity, not that
  the reviewed deterministic router produced geometry for this board.
