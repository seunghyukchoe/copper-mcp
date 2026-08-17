# tscircuit output-validation integration contract v1

**Research date:** 2026-08-17
**Upstream snapshot:** `tscircuit/tscircuit-autorouter` main at
`2010a730f172d979f95c11eb6836e922e565061d` (`@tscircuit/capacity-autorouter` 0.0.814)
**Status:** slice 1 contract complete; slice 2 internal import adapter implemented; no public
runtime surface added

## Question

What is the smallest sound seam between a complete tscircuit `SimpleRouteJson` result and
CopperMCP's existing external-candidate disposer, and what must remain an upstream whole-output
check instead of being implied by per-net CopperMCP verification?

The answer is two gates, not one:

1. tscircuit validates the complete returned trace set against the input problem before a solver or
   custom `algorithmFn` may report usable success.
2. A CopperMCP adapter converts one unambiguously bound, supported net at a time into the existing
   closed v1/v2 foreign-candidate documents. The existing disposer then reconstructs identity and
   checks that candidate against immutable Board IR.

Neither gate repairs geometry. Failure with a typed reason is the only permitted outcome when a
route cannot be represented or validated.

## Evidence that fixes the boundary

- [`SimpleRouteJson`](https://github.com/tscircuit/tscircuit-autorouter/blob/2010a730f172d979f95c11eb6836e922e565061d/lib/types/srj-types.ts)
  carries optional preloaded `traces`; every routed trace has both `pcb_trace_id` and required
  `connection_name`, and its route may contain `wire`, `via`, `jumper`, or `through_obstacle`
  entries. Connections may also carry root, merged, and internal net-name metadata.
- tscircuit already has a broad DRC composition in
  [`lib/testing/evaluate-relaxed-drc.ts`](https://github.com/tscircuit/tscircuit-autorouter/blob/2010a730f172d979f95c11eb6836e922e565061d/lib/testing/evaluate-relaxed-drc.ts)
  and [`lib/testing/getDrcErrors.ts`](https://github.com/tscircuit/tscircuit-autorouter/blob/2010a730f172d979f95c11eb6836e922e565061d/lib/testing/getDrcErrors.ts).
  It combines non-replaced preloaded copper with new routes, converts to Circuit JSON, and invokes
  typed checks for trace overlap/continuity, via-to-trace, pad-to-trace, and via-to-via clearance.
  This code is testing-only, depends on `@tscircuit/checks` which is a `devDependency`, and is not
  exported from `lib/index.ts` at the recorded snapshot. Moving it wholesale into a public runtime
  helper would therefore be a dependency and API decision, not the smallest first contribution.
- The conversion helper currently drops a wire or via whose layer is not a Circuit JSON layer and
  drops `jumper` and `through_obstacle` entries. A production validator must preflight and report
  those cases; silently omitting them before DRC could turn an unvalidated output into a false
  success.
- [tscircuit issue #1964](https://github.com/tscircuit/tscircuit-autorouter/issues/1964)
  records a deterministic different-net same-layer crossing returned with `solved=true`.
- [tscircuit issue #2058](https://github.com/tscircuit/tscircuit-autorouter/issues/2058)
  records post-processing that moved a via into neighbouring copper after the solver returned and
  explicitly asks whether reusable output validation should exist at the custom-router boundary.
- tscircuit's `AGENTS.md` requires unexpected solver states to fail loudly rather than fall back,
  requires invalid states to be made unrepresentable where practical, and permits one test per
  file. A validation failure must therefore never select another solver or keep `solved=true`.

## Ownership split

| Concern | tscircuit whole-output validator | CopperMCP adapter and disposer |
|---|---|---|
| Runtime shape of every returned route item | Owns | Rechecks only the supported converted subset |
| Preloaded route replacement semantics | Owns | Receives the already selected foreign result |
| Different-net candidate-to-candidate crossing | Owns | Not proved by one-candidate disposal |
| Via/trace, pad/trace, and via/via clearance in the complete result | Owns | Current foreign schema refuses every non-empty `vias` list |
| `allowViaInPad` | Owns | Not represented by the current accepted set |
| Binding an output trace to an input electrical net | Produces explicit relation data | Requires one unambiguous imported-net match |
| Decimal millimetres to integer nanometres | Source format stays in millimetres | Owns exact token conversion; never uses binary float identity |
| Board revision and candidate identity | No CopperMCP claim | Owns and derives both from coordinator state |
| Candidate versus immutable board obstacles | Advisory tscircuit DRC | Owns exact Board IR decision for the admitted subset |
| Authoritative KiCad DRC | Does not claim | Existing private, candidate-bound continuation owns it |
| Repair, apply, or mutation | Neither | Neither; existing apply remains a separate authorization |

The split is load-bearing. Calling the CopperMCP disposer separately for two clean candidates does
not prove that the candidates are clean against each other: neither candidate exists in the base
snapshot seen by the other. A future batch verifier may build one deterministic composite private
board and run one KiCad DRC, but that is a new capability and is not part of the first adapter.

## Upstream function contract

The narrow first upstream contribution should add a production topology/crossing validator using
the repository's existing segment math, without pulling the testing-only DRC composition into the
runtime bundle. The working contract is:

```ts
validateAutorouterOutput({ inputSrj, outputSrj }):
  AutorouterOutputValidationResult
```

The result is a discriminated, immutable structure carrying `valid` and ordered `diagnostics`.
Each diagnostic has a stable `code`, `connectionName`, and only the applicable peer connection or
trace ID, layer, coordinate, or segment index. The first PR's closed code set is:

- `UNKNOWN_CONNECTION`
- `INVALID_SEGMENT`
- `UNKNOWN_LAYER`
- `NON_FINITE_COORDINATE`
- `DIFFERENT_CONNECTION_SAME_LAYER_CROSSING`
- `DISCONNECTED_ROUTE_ENDPOINT`

The exact public function and field names may follow maintainer guidance, but the semantics are
fixed:

1. The function is pure with respect to both inputs and never rewrites, snaps, deletes, reconnects,
   or reorders route geometry.
2. It validates `output.traces` together with every non-replaced preloaded input trace.
3. It rejects an unknown route discriminator, non-finite number, invalid or undeclared layer,
   empty/degenerate route, or unsupported conversion instead of dropping it before DRC.
4. It preserves tscircuit's connectivity semantics. It never parses a composite
   `pcb_trace_id` or `connection_name` by underscore or another naming convention.
5. The first PR proves only runtime shape, connection/layer/endpoint topology, finite coordinates,
   and different-connection same-layer crossings. Its name, documentation, and result must not be
   read as pad/via/trace clearance or complete DRC.
6. `jumper` and `through_obstacle` refuse until a production conversion and checker model them
   without omission. A `via` may participate in topology, but v1 makes no via-clearance or
   via-in-pad claim.
7. A different-connection same-layer crossing or disconnected endpoint produces invalid output.
   Legal crossings on different layers remain a positive control.
8. The DRC-grade follow-up may reuse or promote the existing typed Circuit JSON checks only after
   maintainers accept the runtime dependency boundary. It must add via-to-trace, pad-to-trace,
   via-to-via, and `allowViaInPad` semantics without changing v1's topology result into a broader
   historical claim.
9. A via may overlap connected pad copper only when `allowViaInPad` is exactly true; the flag can
   never permit contact with another net. If the checker stack cannot decide that relation, the
   DRC-grade follow-up must report the gap rather than publish a pass.
10. Pipeline integration, when separately accepted, converts an invalid result to that solver's
   normal failed state. It does not fall back to a second solver and does not silently return a
   partial trace set.

The smallest reviewable upstream sequence is therefore:

1. `lib/validation/validateAutorouterOutput.ts` plus a `lib/index.ts` export, using existing segment
   math and no new runtime dependency;
2. one `tests/validation/validate-autorouter-output.test.ts` file containing a synthetic
   #1964-style crossing, legal different-layer control, unknown layer and endpoint cases;
3. a separate custom-`algorithmFn` consumption change after maintainers choose that boundary;
4. a separate DRC-grade via-in-pad/pad/trace-clearance extension with an explicit dependency review.

## CopperMCP adapter contract

The first CopperMCP implementation is an internal import-side adapter under the benchmark package,
not a production routing dependency or a new MCP, CLI, apply, or persistence surface. This keeps
ADR-0112's production/benchmark dependency boundary intact. It consumes the exact bytes of the
original SRJ problem, the imported `ImportedProblem`, and the returned trace document. It produces existing
`copper-mcp/external-route-candidate/v1` or `copper-mcp/external-route-patch/v2` dictionaries plus a
typed adapter refusal. It does not construct a `RouteCandidate` directly.

### Initial accepted set

- The source problem must re-import to the supplied snapshot and source revision.
- The returned root is closed for the fields the adapter reads, is within caller-independent byte,
  trace, point, and numeric-token ceilings, and contains no non-finite number or duplicate key.
- Every admitted trace is wire-only, single-layer, rectilinear, positive-width, and continuous.
- All points and widths convert from their literal JSON tokens through the existing exact-decimal
  millimetre-to-nanometre rule.
- A two-pad net becomes the existing v1 document. A multi-pad net becomes v2 only when its submitted
  paths retain the complete topology needed by the existing patch connectivity verifier.
- `via`, `jumper`, and `through_obstacle` entries refuse `unsupported_geometry` in this slice.
  This preserves the current foreign disposer accepted set; it does not translate an unsupported
  item into absence.

### Net binding

An output trace is admitted only when explicit relation data resolves to exactly one
`ImportedNet.source_connection_names` set. `connection_name`, `connectsTo`, the input connection's
declared root/merged references, and point or port identities may participate only as exact values.
Composite names are opaque strings: the adapter never splits `a__b`, underscores, or prefixes to
guess ownership.

If no explicit reference resolves, endpoint geometry may confirm an already selected net but may
not select one by itself. Ambiguous, absent, or contradictory ownership refuses the whole requested
net conversion. This may expose that the upstream output needs one stable root-net field; adding
that field upstream is preferable to teaching CopperMCP a naming convention.

### Result and refusal boundary

Adapter failures remain distinct from disposer failures. The adapter needs at least these stable
classes before it calls the existing verifier:

| Adapter code | Meaning |
|---|---|
| `malformed_document` | JSON or closed runtime shape is invalid |
| `budget_exceeded` | Source-controlled work exceeds a server-owned ceiling |
| `source_mismatch` | Output is not bound to the imported source problem |
| `ambiguous_net_ownership` | Explicit references resolve to zero or multiple imported nets |
| `unsupported_geometry` | A route kind, layer transition, diagonal, or topology is outside the first accepted set |
| `discontinuous_path` | Ordered wire points do not form a path |
| `endpoint_mismatch` | The path does not terminate on the selected imported endpoints |

After conversion, the existing disposer remains authoritative for `stale_revision`,
`obstacle_violation`, candidate identity, work accounting, and the other existing failure codes.
The adapter must not translate an adapter refusal into a fabricated disposer result.

## Fixture and licensing rule

The first tests should be CopperMCP-original minimal synthetic fixtures reproducing the geometric
classes described by #1964 and #2058. An issue body, gist, bug-report download, or application board
is not redistribution permission. No upstream JSON or board enters this repository until its exact
source licence and item provenance are recorded. If a useful item cannot be redistributed, retain
only its URL, expected digest, and a fetch recipe under the existing external-corpus policy.

The upstream tscircuit test should likewise be authored as the smallest new input that crosses two
different nets, not copied from the nRF52810 gist unless that artifact's licence is independently
established.

## Validation plan for slices 2 and 3

**Slice 2 implementation status:** complete in
`copper_mcp.benchmarks.simple_route_json_output`. Focused synthetic tests cover v1/v2 conversion,
repeatable downstream identity, exact source replay, relation binding, JSON-number integrity,
closed fields, work ceilings, and the initial geometry refusals. CopperMCP's synthetic #1964
pairwise gate is also implemented. The separate upstream topology validator and Pipeline 7 opt-in
are implemented and locally validated in an unpublished tscircuit checkout; DRC-grade #2058
diagnosis remains later work.

The contract is implemented only when all of the following are observed:

- a valid, via-free, single-layer two-pad result converts to v1 and is accepted by the existing
  disposer with a repeatable candidate ID;
- a valid multi-pad tree converts to v2 without deleting paths and is accepted;
- stale source, ambiguous ownership, an undeclared layer, a diagonal, a discontinuity, and every
  unsupported route kind refuse before candidate construction;
- a synthetic #1964-style pair passes per-net conversion but fails the separate whole-output gate,
  proving the two claims are not conflated;
- a synthetic #2058-style via refuses the first Copper adapter as unsupported; it must fail the
  later DRC-grade upstream validator with a targeted clearance diagnostic, while the first upstream
  topology PR explicitly makes no such claim;
- no fixture or source board is mutated, and no result claims complete-board, electrical, SI, PI,
  EMC, thermal, DFM, fabrication, or hardware validation.

## Deferred decisions

- A CopperMCP batch candidate schema and one composite candidate-bound KiCad DRC run.
- Multilayer and via admission into the external disposer.
- A stable upstream root-net identity field if current explicit references cannot resolve every
  legitimate merged output.
- Whether tscircuit core, the autorouter package, or both enforce the exported validator at custom
  `algorithmFn` consumption. The reusable helper can land before that ownership decision.
- Calibration or broad-phase replacement of the public tscircuit validator's locally implemented
  fixed, non-widenable v1 cardinality, diagnostic, and comparison-work ceilings.

No item above may be silently pulled into the first adapter or first upstream PR.
