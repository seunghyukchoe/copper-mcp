# Exact local repair negotiated-integration gate

## Status

**Negotiated integration remains declined at the current public-contract boundary — 2026-08-05.**
This note records a predeclared integration experiment. The standalone local operator remains
useful and independently tested. An internal, non-publishing Board-IR candidate-path acceptance
gate now exists as a prerequisite, but publishing local-repair geometry through the current
negotiated coordinator would still bypass other mandatory acceptance properties.

## Predeclared fixture and measurement

`crossing-local-repair-gate-v1` is the existing deterministic two-net fixture in
`tests/test_routing_congestion.py` (`_crossing_snapshot`, `_requests`), run at source commit
`965d8fc97ddeb720251cb7863c7b62310637f301` with `max_iterations=4`. It is deliberately fixed
before any tuning:

- two 200 um tracks share a 1 mm lattice on a 12 mm x 10 mm single-layer board;
- the horizontal net initially occupies the direct centre corridor; and
- the vertical net is the attempted local-repair net.

The acceptance criterion for a future opt-in repair integration is strict: across ten replays it
must reduce one named coordinator metric (iterations, rip-ups, or total router expansions) by at
least 10% against this exact no-repair coordinator, while preserving completed status, zero
overflow units, the exact Board-IR revision binding, independent Board-IR obstacle acceptance,
candidate-pair physical acceptance, and deterministic replay. It must not count the local
operator's work as free.

The baseline measurement is already fully routed, so it does not justify an integration claim:

| Metric | No-repair coordinator result |
| --- | ---: |
| Equal replays | 10 / 10 |
| Status | `completed` |
| Iterations / rip-ups | 1 / 0 |
| Overflow units | 0 |
| Total wire length | 26,000,000 nm |
| Candidate-pair physical checks | 3 |
| Horizontal / vertical A* expansions | 8 / 189 |
| Horizontal / vertical A* obstacle checks | 130 / 2,334 |

The vertical route already takes the deterministic left detour. Calling the local operator here
can at most reproduce a route the reference router has already accepted; it cannot satisfy the
predeclared improvement threshold. This result is a negative control, not evidence that local
repair is ineffective on a future held-out corpus.

## Required immutable contract

For a later experiment, the coordinator—not a policy, model, caller, or local-repair object—must
derive a fresh immutable repair input only after a complete rejected allocation. Its content
address must bind, at minimum:

1. the canonical Board IR snapshot digest and negotiated-envelope digest;
2. an explicit, versioned world-coordinate grid origin and step;
3. the target request identity, selected iteration, and selected conflicting candidate IDs;
4. a bounded repair window derived from those values by a named deterministic algorithm; and
5. a canonical occupancy projection whose schema states how existing Board IR obstacles,
   same-pass candidate copper, width, and clearance become blocked lattice cells.

`RepairWindowCandidate` alone cannot supply this contract: it carries only a net ID, bounds, and
conflict score. `LocalRepairRequest.blocked_cells` is canonical for its abstract input but does
not state the Board IR source, coordinate origin, clearance inflation, or relation to an accepted
negotiated allocation. A policy-selected window is therefore insufficient provenance even when
the local result's own input and route digests verify.

## Blocking safety gates

The present coordinator cannot meet the preceding contract without expanding authority in the
reference-routing boundary:

- `CongestionLedger` records used unit vertices and edges for allocation pressure. It does not
  expose a width-and-clearance-aware cell occupancy projection for Board IR pads, tracks,
  keepouts, zones, or already-present copper. Treating its resource keys as `blocked_cells` would
  silently change their meaning and can under-model physical exclusion.
- `exact_local_repair` intentionally accepts only an abstract lattice window and cells. Its
  verifier proves route shape and its own digests; it does not validate a proposed path against
  the Board IR obstacle model.
- The current public `AStarRouter.propose` can independently construct a route, but it has no
  candidate-path validation entry point. `verify_candidate_id` proves only that a candidate is
  self-consistent. The negotiated physical gate compares returned candidate pairs; it does not
  prove a newly constructed candidate avoids pre-existing board obstacles. Requiring semantic
  equality with a fresh A* proposal would be safe but makes local repair unable to alter geometry,
  so it cannot demonstrate a routing-quality improvement.
- ADR-0064 intentionally derives neutral bounds and empty repair candidates for policy-enabled
  negotiation, then rejects every non-empty `repair_windows` selection. Admitting one would
  require a new versioned coordinator/profile transaction and candidate-identity binding, not a
  permissive exception to the existing profile.
- The envelope meters router expansions, obstacle checks, and pairwise physical checks but has no
  authorised, compositional budget for a local-repair expansion plus an independent Board-IR
  route-path validator. Adding unmetered work would violate the bounded-operation contract.

Consequently, no local proposal was converted into `RouteCandidate`, no candidate identity was
changed, no policy window was admitted, and no board was mutated.

## Candidate-path validator prerequisite

Before any local-repair integration, this branch introduces the intentionally internal
`routing.candidate_path_validator` seam. It accepts one already immutable `RouteCandidate`, its
matching `RouteRequest`, and a Board IR snapshot; it publishes only a typed, redacted validation
result. It does not construct a candidate, expose an MCP tool, admit a policy or model, serialize
KiCad, mutate a board, or replace the existing candidate-pair physical-clearance gate.

The validator first caps raw candidate text and path cardinality before reconstructing or hashing
it, then reconstructs current exact request/candidate values, verifies the candidate content
digest, and invokes the reference router's exact integer Board-IR preparation and checks every
decompressed lattice edge with its existing obstacle predicate. Its local-repair path window is
capped at 4,096 unit edges. Reconstruction and validation observe cancellation/deadline hooks;
the validator checks one final time before publishing acceptance. The independent acceptance
evidence therefore covers the current single-layer, two-pin reference subset: exact revision,
grid, net class width, track/pad/keepout/zone clearance authority, and the explicit
unsupported-geometry refusals already embodied by that subset. Endpoints must be legal current
source/target lattice nodes, so a path may attach to existing same-net copper but cannot nominate
a foreign-net endpoint. Candidate vias and multilayer geometry are refused rather than inferred
from a two-dimensional path.

The fixture is predeclared as `candidate-path-validator-detour-v1` in
`tests/test_routing_candidate_path_validator.py`: a 1 mm grid, 200 um track, 100 um clearance,
and a foreign vertical 200 um track from `(5,3)` mm to `(5,7)` mm. The exact 16-edge lower detour
must be accepted identically in ten replays; a direct crossing must be rejected at its fourth
unit edge. The fixture additionally exercises a valid same-net attachment, foreign endpoint
refusal, zero-length and repeated-vertex refusal, preflight refusal of an oversized raw path
before identity work, and cancellation both before geometry and just before acceptance. Acceptance
additionally requires distinct typed outcomes for stale revision and an exhausted path-edge cap.
These are a path-acceptance prerequisite, not a claim of a negotiated-routing improvement.

The coordinator must account the validator's path-edge and obstacle work in a future independent
envelope before it can bind a local route to a candidate. The current coordinator has not been
changed, and the predeclared >=10% negotiated-quality threshold above remains unmet.

## Research basis

- Larry McMurchie and Carl Ebeling, *PathFinder: A Negotiation-Based Performance-Driven Router
  for FPGAs* (1995), primary paper:
  https://janders.eecg.utoronto.ca/1387/readings/pathfinder.pdf . It supports bounded iterative
  congestion negotiation, but its discrete FPGA-resource model is not evidence that a PCB
  occupancy projection may ignore width, clearance, or board obstacles.
- E. W. Dijkstra, *A Note on Two Problems in Connexion with Graphs* (1959), primary record and
  full text:
  https://eudml.org/doc/131436 and https://doi.org/10.1007/BF01386390 . It supports the
  non-negative shortest-path basis used by the local operator; it does not validate a route in a
  separate physical geometry model.
- C. Y. Lee, *An Algorithm for Path Connections and Its Applications* (1961), primary paper:
  https://janders.eecg.utoronto.ca/1387_2015/readings/lee.pdf . Its lattice path formulation
  motivates the local search representation, not direct publication of an unvalidated PCB path.
- KiCad's PCB Editor documentation on net classes and routing/clearance rules:
  https://docs.kicad.org/master/en/pcbnew/pcbnew.html#net-classes . The repository's current
  Board IR slice is intentionally narrower than KiCad's complete design-rule system, so no
  KiCad-DRC or fabrication claim follows from this gate.

## Safe next slice

The reviewed internal validator is now the starting point, not a future prerequisite. The next
implementation must add a separately reviewed, opt-in coordinator transaction that derives
versioned repair windows and a width-and-clearance-aware occupancy projection from the immutable
Board IR and rejected allocation. It must meter both local-repair and validator work in one
independent envelope, bind the resulting repair and validation evidence into a new candidate
identity, and then require the normal candidate-pair physical-clearance gate. A held-out fixture
must meet the predeclared >=10% negotiated-quality criterion before any improvement claim. The
no-profile `NegotiatedRoutingResult` shape and v2 candidate identity must remain byte-for-byte
unchanged.

## Nonclaims

This declined experiment makes no claim of routing improvement, FreeRouting parity, KiCad DRC,
manufacturing validity, model effectiveness, live-KiCad control, or safe board apply. It records
why those claims would be less safe if the current abstract local operator were directly wired
into publication.

## Gate evidence attribution correction — 2026-08-05

B-071 preserves the historical replay of a related KiCad-derived fixture with 512 grid nodes,
20,000 expansions, 128 obstacles, and 200,000 obstacle checks. Those settings are not equivalent
to the predeclared semantic `_crossing_snapshot` and `_requests` experiment, so B-071 remains
audit history but is not relied on for that attribution.

B-072 supersedes only that attribution. Its benchmark helper independently reconstructs source
commit `965d8fc97ddeb720251cb7863c7b62310637f301`, pins the expected snapshot digest
`sha256:9ad048f6f439a7e71be4c1f115d8a205f00c92f0853e0c140725906c1acdb245`, and uses the
predeclared 256-node / 5,000-expansion / 64-obstacle / 100,000-obstacle-check settings. A focused
regression compares the helper's Board IR, requests, digest, and settings with the original
builder; the benchmark imports no test code.

The corrected replay remains declined: all ten replays complete with zero overflow in one
iteration, zero ripups, and no rejected allocation. It consequently performs zero local-repair or
path-validator work. This correction introduces no transaction, profile, public surface, routing
integration, or routing-quality claim. See [B-072](../ledgers/benchmark-ledger.md).
