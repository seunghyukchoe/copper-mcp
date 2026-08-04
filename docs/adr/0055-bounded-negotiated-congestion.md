# ADR-0055: Add a bounded negotiated-congestion coordinator

- **Status:** Accepted
- **Date:** 2026-08-05

## Context

The reference router could produce deterministic, revision-bound single-net candidates, but it
had no global mechanism for two nets that independently selected the same lattice resource. That
is the central quality gap between a single-net maze search and a practical multi-net router. The
PathFinder literature describes a useful pattern: allow shared resources in intermediate
iterations, increase present and historical costs for overused resources, then rip up and reroute
until the final allocation is legal ([McMurchie and Ebeling, 1995](https://doi.org/10.1109/fpga.1995.242049);
the later publisher summary explains the same present/history cost formulation
[here](https://doi.org/10.1016/B978-012370522-8.50024-8)). FreeRouting likewise documents
heuristic maze expansion followed by rip-up and optimization, so this is a route-quality
fundamental rather than an AI-specific shortcut.

## Decision

Add a candidate-only `negotiate_routes` coordinator around the existing A* backend:

- accept a bounded tuple of distinct two-pin requests on one signal layer, one world-coordinate
  grid, and one immutable Board IR revision;
- maintain exact unit-edge and lattice-vertex occupancy, present penalties, and capped historical
  overflow pressure in an immutable-policy ledger;
- reroute nets in stable `(net_id, seed)` order, reorder conflicted nets deterministically, and
  stop on zero structural lattice overflow or a fixed iteration ceiling;
- re-identify accepted candidates with a negotiated policy digest, so ordinary A* and negotiated
  candidates cannot share a misleading identity;
- return candidates, already-connected records, unrouted IDs, overflow resources, iteration and
  rip-up counts, and a redacted fixed diagnostic; and
- keep physical clearance, multilayer vias, electrical constraints, KiCad serialization, DRC,
  apply, and FreeRouting parity as separate gates.

The A* hook is an internal integer search-ordering term. It never changes the candidate's physical
`RouteCost`, never writes board bytes, and fails closed for a malformed callback or penalty.

## Evidence and limits

`tests/test_routing_congestion.py` covers deterministic replay, baseline conflict detection,
policy-bound candidate identity, cancellation, malformed penalties, and request validation. B-036
uses a committed KiCad fixture converted through the Board IR adapter: the sequential baseline has
one lattice overflow unit, while three negotiated replays complete with zero overflow and a 26 mm
total wire length (the baseline is 16 mm). This is structural lattice evidence only; it is not a
KiCad DRC, electrical, fabrication, multilayer, or general-board FreeRouting result. The next
quality gate is exact pairwise clearance/resource capacity and a held-out corpus comparison.
