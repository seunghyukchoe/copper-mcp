# ADR-0036: Bind the layered search oracle to a narrow Board IR proposal contract

- Status: Accepted
- Date: 2026-08-04
- Related: [D-052](../ledgers/decision-ledger.md), [D-053](../ledgers/decision-ledger.md)

## Context

ADR-0035 established that a two-layer `(x, y, layer)` search is useful evidence but is not a
board route. The current production candidate and KiCad serializer remain single-layer. KiCad's
Python board API exposes tracks, vias, zones, footprints, and pad geometry for inspection, while
the board S-expression represents segments and vias with explicit layer, net, diameter, drill, and
stack fields. Those facts make a typed adapter possible, but they do not make a lattice path a
KiCad-valid edit: source-preserving serialization, Board IR replay, and authoritative DRC are
still independent gates.

## Decision

Add a pure, candidate-only `LayeredBoardRouter` over Board IR v0.2 with this support matrix:

- exactly two ordered signal layers and one rectangular, hole-free outline;
- exactly two pads on the selected net, with explicit endpoint layers whenever a pad exposes both;
- nanometre grid attachment, bounded integer search, and net-class width/clearance/via dimensions;
- foreign pads, orthogonal foreign segments, full-stack through vias, and foreign zones represented
  as conservative rectangular envelopes; rotated pads use a safe sum-of-sides envelope;
- rectangular keepouts split into track obstacles and via-only obstacles; physical envelopes include
  separate via-radius clearance and edge bounds include the via diameter;
- immutable, content-addressed paths, explicit through-vias, metrics, settings, and failure codes;
- endpoint vias may land on a pad without a fabricated one-point path, because the pad supplies the
  terminal copper connection.

The adapter verifies the Board IR snapshot and revision before search, never writes files, never
invokes KiCad, never emits an apply token, and never labels its result DRC-clean. Selected-net
copper, arcs, length/differential constraints, non-rectangular board outlines, and unsupported
geometry fail closed. The existing single-layer contracts, IDs, serializer, MCP tools, and apply
path remain unchanged.

## Consequences

The project now has a measured Board IR-to-layered-proposal seam rather than only an abstract
oracle. It still does not provide production routing through vias. The next required gate is a
separate source-preserving segment/via serializer that reparses to equivalent Board IR and is
accepted only with authoritative KiCad DRC evidence. Multi-layer stacks, negotiated congestion,
rip-up/reroute, and FreeRouting comparisons remain separate roadmap work.

## Evidence and references

- `tests/test_layered_board_adapter.py` covers deterministic direct and via-required routes,
  physical track/via keepouts, stale/off-grid/ambiguous endpoint refusal, and candidate tamper
  detection.
- B-018 records six fixed synthetic cases, 60 deterministic replays, two emitted vias in the
  via-required case, source immutability, and digest tamper refusal.
- KiCad Python board inspection: [official board API](https://docs.kicad.org/kicad-python-main/board.html).
- KiCad board geometry fields: [official PCB S-expression format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/).
- Related: [ADR-0035](0035-internal-layered-search-oracle.md), [routing baseline](../architecture/routing-baseline.md).
