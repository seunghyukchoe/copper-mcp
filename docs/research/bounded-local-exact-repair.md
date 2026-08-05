# Bounded local exact repair

## Question

Can a policy-selected repair window have useful operational semantics without admitting model
geometry, mutating a board, or weakening the existing candidate/physical-clearance gates?

## Decision

`copper_mcp.routing.repair.exact_local_repair` is a deterministic, candidate-only local operator.
It accepts a **conventionally coordinator-supplied** `RepairWindowCandidate`, two integer lattice
endpoints, a canonical bounded tuple of occupied cells, and a capped expansion budget. The local
type does not authenticate who supplied the window or claim ownership; that provenance remains a
future coordinator boundary. It returns either an
immutable lattice proposal with input/route content digests or a fixed no-route, budget,
cancellation, or invalid-boundary result.  It never receives Board IR, KiCad text, pad identifiers,
track widths, layers, candidates, policy/model output, or apply authority.

The algorithm is Dijkstra's positive-cost shortest-path method over `(cell, incoming-direction)`
states.  Its lexicographic cost is `(unit_steps, bends, complete_cell_sequence)`.  Splitting a cell
by incoming direction makes the incremental bend cost local; every move adds one unit step and a
non-negative bend increment, so the usual Dijkstra settlement condition applies.  The complete
cell sequence is only a deterministic tie-breaker and is bounded by the window-cell cap.  The
four-neighbour grid form is the classic Lee path-connection setting, but this implementation does
not claim Lee's full PCB semantics.

At the public boundary, the operator revalidates each current request field and reconstructs a
fresh immutable request before any callback or search. A malformed or type-confused current value
returns the fixed invalid result with zero search work. This does **not** authenticate an object,
seal its lifetime, or distinguish a post-construction change that forms another valid exact request
from a freshly submitted equivalent request. The request-bound verifier accepts a completed
proposal only when the current input and stored route digests recompute exactly; endpoints match;
every cell is in-window and unblocked; every segment is one orthogonal cell; cells are unique; and
the reported bend count recomputes. Non-completed results must publish no route or route digest and
carry the exact fixed status diagnostic. These checks validate geometry and work accounting before
a future integration can bind a local proposal to a route candidate; they do not establish origin,
ownership, or authentication.

Primary sources:

- E. W. Dijkstra, *A Note on Two Problems in Connexion with Graphs*, 1959,
  [DOI:10.1007/BF01386390](https://doi.org/10.1007/BF01386390).  This establishes the
  non-negative shortest-path basis used here.
- C. Y. Lee, *An Algorithm for Path Connections and Its Applications*, 1961,
  [DOI:10.1109/TEC.1961.5219222](https://doi.org/10.1109/TEC.1961.5219222).  This is the
  grid path-connection reference for the local lattice formulation.
- L. McMurchie and C. Ebeling, *PathFinder: A Negotiation-Based Performance-Driven Router for
  FPGAs*, 1995, [DOI:10.1145/368640.368806](https://doi.org/10.1145/368640.368806).  It
  motivates executing a bounded repair choice inside a negotiated-congestion loop, while its FPGA
  resource model is explicitly not PCB clearance or fabrication evidence.

## Predeclared fixture and acceptance criterion

`tests/test_routing_repair.py` declares `window-detour-v1` before any integration tuning:

- window cells are `[0,4] × [0,4]`;
- source `(0,2)` and target `(4,2)`;
- occupied cells are `(2,1)`, `(2,2)`, `(2,3)`;
- at most 64 expanded states.

The direct four-step route is blocked.  Acceptance requires ten byte/equality-identical replays of
the canonical upper detour, exactly 8 unit steps and 2 bends, zero blocked/out-of-window cells,
and no more than 64 expansions.  A one-expansion budget must return an atomic
`budget_exhausted` result with no route; cancellation or an exception in the cooperative callback
must return an atomic `cancelled` result with no route.

## Limits and next gate

This is an abstract, orthogonal, single-net lattice calculation.  It does **not** demonstrate a
quality improvement over the existing negotiated router, physical clearance, existing-board
copper avoidance, Board IR/KiCad binding, DRC, manufacturing validity, model effectiveness, or
safe apply.  It is intentionally not wired into `negotiate_routes` yet: doing so would require a
new ADR, conventionally coordinator-supplied repair-window derivation, revised profile validation and candidate
identity/versioning, reference-router replay, physical acceptance, and a held-out benchmark.
