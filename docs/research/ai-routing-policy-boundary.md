# AI routing policy boundary

**Snapshot date:** 2026-08-05

## Decision

`copper_mcp.routing.policy` is a deliberately small advisory seam.  Its closed input has only a
board-revision digest, bounded integer cell windows, per-net scalar priority/congestion/demand
features, and coordinator-supplied corridor/repair-window options.  Its closed decision can only:

- order every already-known net once;
- select up to two supplied corridor windows per net; and
- select up to two supplied repair windows per net.

There are no path vertices, route widths, layers, pad locations, cost overrides, `RoutePatch`,
`RouteCandidate`, Board IR, board bytes, validation result, or apply authority in the module.
Integration must recheck a decision's input digest and require each selected window to be an exact
member of the coordinator-provided set.  A future local model, GNN, or RL policy therefore cannot
invent a corridor, emit copper, skip deterministic search, or bypass candidate/DRC/apply gates.

`DeterministicReferencePolicy` is the initial regression baseline: it orders by descending
criticality, congestion, and demand (then stable net ID), prefers lower corridor congestion and
detour, and prefers higher-conflict repair windows.  All tie breakers are explicit.  Canonical JSON
and SHA-256 bind inputs and decisions; repeated equal inputs produce equal decisions and equal
redacted traces.

## Input and trace privacy

The JSON decoder is closed-shape, duplicate-key rejecting, UTF-8 and 64 kB bounded, depth/value
bounded, integer-only, and rejects booleans used as numbers.  Inputs are frozen dataclasses and
cap nets, candidate windows, action windows, coordinates, and scores.

The training-oriented `RoutingPolicyTrace` retains input/decision digests, policy identity,
counts, and deterministic 24-hex-character ordinal action tokens.  The digests are deliberately
content-addressed, so they are linkable pseudonymous bindings—not secret redactions.  A reader who
knows or can guess a complete low-entropy canonical input or decision (including its board
revision) can dictionary-test it against a published digest; traces therefore remain within their
intended trust boundary.  Tokens are separately derived solely from the coordinator's canonical
option ordinal, action category, and published decision position—not from an input digest plus a
net ID, window JSON, coordinate, or scalar feature.  A token alone cannot be tested against a
guessed raw name or candidate window.  The trace omits raw net IDs, revision text, coordinates,
bounds, scalar features, pads, paths, widths, board bytes, prompts, model output, and candidate
geometry.  It is a reproducibility label for an offline local corpus, not a board export or a
claim that a selected policy action is physically valid.

## Evidence and transfer limits

### PCB evidence

- Liao et al., [*A Deep Reinforcement Learning Approach for Global Routing*](https://arxiv.org/abs/1906.08809)
  (authors' primary preprint) frames global routing for PCB or IC-like problems and reports a
  simulated learned policy versus sequential A*.  It supports studying learned **policy choices**
  and dataset generation; it does not establish KiCad DRC, PCB fabrication, signal integrity, or
  direct learned-copper safety.
- Li et al., [*SER-NET: A Neuralize PCB Simultaneous Escape Routing Method Using Deep
  Reinforcement Learning*](https://doi.org/10.1109/ICICM63644.2024.10814469) is a primary IEEE
  conference publication specifically about PCB escape routing.  It motivates retaining a PCB
  evaluation lane, but its specialized escape-routing scope is not evidence for arbitrary
  whole-board routing, physical sign-off, or direct integration into CopperMCP.
- McMurchie and Ebeling, [*PathFinder: a negotiation-based performance-driven router for
  FPGAs*](https://doi.org/10.1109/FPGA.1995.242049), is primary evidence for bounded routing
  ordering/repair scheduling through congestion feedback.  Its FPGA routing-resource model is not
  PCB clearance or manufacturing evidence; CopperMCP's existing deterministic congestion loop is
  the applicable safe baseline.

### IC-transfer evidence (explicitly not PCB proof)

- Goldie et al., [*Chip Placement with Deep Reinforcement Learning*](https://arxiv.org/abs/2004.10746)
  is primary IC-placement work showing learned graph/netlist representations and policy/value
  transfer across blocks.  It motivates a future local GNN/RL feature encoder and offline training
  trace, but does **not** demonstrate PCB routing clearance, irregular board geometry, vias,
  impedance/SI constraints, KiCad compatibility, or manufacturability.

## Evaluation plan

1. Keep the deterministic reference policy as a corpus baseline and require byte-identical
   decision/trace replay for repeated canonical inputs.
2. Compare learned policy choices against that baseline only on held-out, license-reviewed PCB
   fixtures, with fixed router/validator versions, input and policy digests, and redacted traces.
3. Measure route completion, router work, candidate cost, deterministic validation, and separate
   KiCad DRC outcomes.  Never score a policy as physically valid merely because it emitted an
   action trace.
4. Admit a learned policy only behind this same closed decision contract; raw model output must be
   decoded/validated before it can influence routing search order.

## Non-claims

This seam does not train, ship, call, or upload to a model; it does not prove that learning improves
PCB routing; it does not model all PCB constraints; and it does not add a public MCP tool or any
board mutation capability.  The future integration point is a coordinator that consumes advisory
choices before deterministic route construction, candidate verification, authoritative DRC, and
explicit apply authorization.

## Public benchmark provenance

The accompanying replay artifact records `implementation_commit` for the public policy contract
and a distinct `evidence_source_commit` for the later revision containing the exact replay harness
and fixture.  Both values are part of the content-addressed report, alongside script and fixture
hashes.  This keeps implementation lineage and reproducible evidence explicit rather than assigning
replay authority to an unreachable pre-integration branch commit.
