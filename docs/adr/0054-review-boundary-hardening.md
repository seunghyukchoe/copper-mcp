# ADR-0054: Close review-bot boundary gaps in routing and live observation

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

The routing review surfaced several ways in which otherwise bounded proposals could be
misclassified, misbound, or retain more untrusted information than intended. The gaps were
independent of the search heuristic: public scene references still admitted raw net-name-shaped
values, live IPC deadlines were not carried across every capture call, durable records accepted
executor text, and candidate completion did not bind every request identity or work budget.

## Decision

Harden the existing contracts without adding mutation or remote authority:

- public scene net references are content-derived `net:name:<32 lowercase hex>` identifiers;
- live IPC capture checks one cooperative operation-wide deadline between every official call;
- routing failure diagnostics are selected from fixed typed messages and never persist executor
  text;
- job completion binds request kind, router version, policy, seed, and measured work limits;
- candidate manifests require the exact job and router binding, and expired rows are committed out
  of the store before a uniform unavailable response;
- layered obstacle derivation refuses before constructing beyond its configured ceiling, while
  candidate envelopes include track/via radius and the strictest known foreign-zone clearance;
- layered candidate budgets are validated before identity hashing, so malformed inputs cannot
  spend hashing work or be mislabeled as stale.

These remain deterministic, candidate-only, revision-bound operations. `apply_candidate`, KiCad
mutation, remote authentication, MCP Tasks, and general multilayer/congestion routing remain
separate capabilities.

## Evidence and limits

Focused routing, job, manifest, placement, MCP, and IPC tests cover the new boundaries. Benchmarks
B-026, B-027, B-031, B-032, and B-033 are replayed from the implementation commit, with append-only
corrections documenting their exact evidence. The IPC deadline is cooperative: a single blocking
official call cannot be pre-empted by Python, so hard wall-clock isolation remains future work.
