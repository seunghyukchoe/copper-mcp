# ADR-0076: Verify foreign route geometry without adopting it

- **Status:** Accepted
- **Date:** 2026-08-06
- **Owners:** `@seunghyukchoe`
- **Related:** ADR-0001, ADR-0004, ADR-0025, ADR-0066

## Context

The ecosystem is training ML proposers on the routing problem while explicitly deferring
obstacle avoidance and DRC compliance — tscircuit's Z01 release says so in as many words (see
the [research note](../research/foreign-candidate-verification-v1.md)). That deferred half is
this project's invariant: AI proposes, deterministic code disposes. Issue #99 asks for the seam
that makes CopperMCP the disposer for *anyone's* proposer.

A foreign proposer emits geometry, not a CopperMCP candidate: a SimpleRouteJson solution has
wire points and vias in floating-point millimetres, no `candidate_id`, no `base_revision`, and
no identity discipline at all. The existing surfaces each miss this input on purpose.
`validate_candidate` normalizes a manifest that already *claims* to be a CopperMCP candidate
and reads no board. `verify_layered_candidate` verifies a `LayeredRouteCandidate` whose
identity our own router minted. `validate_candidate_path` replays a native candidate on the
native lattice. None of them may accept foreign geometry, because each one's acceptance
*means* "this is ours".

This ADR records the contract for the new seam, and resolves six questions that would
otherwise each be answered by accident.

## Decision

Add `copper_mcp.foreign_route_verification.verify_foreign_simple_route_json` — a pure library
function with a CLI adapter (`copper-mcp verify-foreign-route`) and deliberately **no MCP tool
in this slice** — that takes a SimpleRouteJson problem document, a foreign solution document,
and the caller's expected problem digest, and returns a typed verdict with per-check evidence.

### 1. The input is two byte documents and a stated binding — nothing else

The accepted contract is: the problem document (bytes, parsed by the existing conservative
import seam of PR #103), the solution document (bytes; a JSON object whose `traces` array holds
`pcb_trace` records of `wire`/`via` route elements, each trace carrying the `connection_name`
it claims to route), and `expected_problem_sha256`. Coordinates are literal millimetre tokens
converted through `decimal.Decimal` exactly as the import seam converts them — never floats.
Everything outside this subset refuses the whole submission with a typed code: unknown root
keys, unknown route element types, traces without attribution, layers outside the declared
stack, oversized documents, NaN. Nothing is skipped, defaulted, or repaired.

Requiring `connection_name` on every trace is a deliberate tightening over the loosest
upstream shape: clearance between two traces is only *defined* when their nets are known, and
inferring ownership from geometric contact would be a silent guess — the one thing a verifier
must never do.

### 2. Identity: nothing is minted, and forgery is refused rather than ignored

A verified foreign route does **not** become a CopperMCP candidate. No `candidate_id` is
minted, because a candidate ID means "produced under our identity rules from our base
revision" — a claim that is false of foreign geometry no matter how thoroughly it verifies.
The result binds only content addresses the verifier computed itself: `problem_sha256` and
`solution_sha256` over the submitted bytes, and the `snapshot_digest` of the imported board.
Those say "these exact bytes were examined", and nothing more.

Laundering is blocked structurally, in three ways. The response dataclass has no
`candidate_id`, `base_revision`, or `apply_token` field — absent structurally, not
conditionally, the ADR-0066 device. A solution document that asserts any reserved CopperMCP
identity or authority key (`candidate_id`, `base_revision`, `apply_token`,
`authorization_digest`, and friends) is refused with `forged_identity` rather than having the
key ignored, because a discarded forgery is indistinguishable from a laundered one in the
response. And the native surfaces are unchanged: foreign geometry submitted *as if* native
still fails `verify_candidate_id`/`verify_layered_candidate_id` recomputation exactly as any
tampered manifest does (ADR-0025's rule that a candidate is never trusted from its manifest).

The result instead carries a one-value origin literal, `origin: "foreign_untrusted"`, so no
consumer can mistake the provenance.

### 3. A pass means exactly one bounded thing

The passing verdict literal is `clearance_and_connectivity_verified`, and the response carries
the claim as data: within the modelled subset, the over-approximated submitted geometry showed
no exact-clearance violation against the imported obstacle model, stayed inside the imported
outline, and the under-approximated geometry joins every pad of every multi-pad net — assuming
traces and vias are fabricated at exactly the stated widths and the declared import policy
dimensions. The response's own field names refuse to overstate: `kicad_drc: "not_run"`,
`repair: "not_attempted"`, `apply_authority: "none"`, plus an explicit `non_claims` list
(no manufacturability, no signal integrity, no netlist correctness, no adoption). KiCad DRC is
not claimed because an SRJ-imported board has no KiCad file to run it on; binding real DRC for
KiCad-backed boards is a later slice of #99, not a silent extension of this one.

### 4. Verification under-approximates acceptance; obstacles over-approximate

The import seam's direction of error is inherited and extended to the route itself:

- Route widths round **up** for clearance and containment, **down** for connectivity; the
  minimum-width check compares exact decimals, so rounding can never flatter a narrow trace.
- A via blocks — and, being a plated through barrel, joins — on **every** declared layer.
- Pad attachment uses the oval's inscribed core, shrunk by the import's recorded worst outward
  rounding, so "connected" is only claimed through copper the source document actually stated.
- A coordinate token inexact at nanometre resolution is rounded, and every subsequent
  comparison is slackened in the refusing direction by a worst-case displacement bound
  (3 doubled nanometres). An exact document pays no slack — an exactly-legal separation still
  verifies — and an inexact one can only be refused more often, never less.
- All geometry comparisons are exact integer arithmetic in doubled nanometres at arbitrary
  angles (orientation tests plus cross-product distance comparisons); there is no epsilon
  anywhere.
- Every geometric pair comparison spends one unit of a single closed budget; exhaustion is a
  `budget_exceeded` refusal, never a partial pass.

Connectivity is deliberately **pad-complete** rather than point-complete: every pad of a
multi-pad net must land in one connected component. That is stricter than joining only the
stated connection points, which keeps the error on the refusing side and matches what a DRC
unconnected-items check would demand.

### 5. No repair

The seam accepts or refuses. It never moves a vertex, widens a trace, or drops an offending
element, and the `repair: "not_attempted"` literal admits exactly one value so the contract
cannot drift quietly. Bounded local exact repair remains a separate authority (#90) with its
own record; connecting the two would destroy the verifier's ability to state what it verified.

### 6. No apply authority, now or by upgrade

A verified foreign route cannot become applyable through this seam. It mints no apply token,
and because it also mints no candidate identity, the existing token authorities have nothing
to bind one to — apply tokens bind `(candidate_id, base_revision, ...)`, and neither exists
here. The default is **no**, permanently, for this surface: if a foreign route should ever
become copper, the path is to re-propose it natively (deriving a candidate under our identity
rules from our base revision, re-verified by the native stack), never to promote the foreign
verdict.

### Relation to `validate_candidate`: sibling, not extension or replacement

Extension was rejected because `validate_candidate`'s acceptance asserts native identity — a
foreign route entering through that door is the laundering attack, built in. Replacement was
rejected because manifest normalization and ranking remain a real, different job. The two
surfaces are siblings with disjoint claims, and the disjointness is load-bearing: the foreign
result cannot be fed back into `validate_candidate` without forging fields that the native
path's recomputation then refuses.

### No MCP tool in this slice

The library and CLI are the complete slice. MCP exposure is deferred until the contract has
survived use against real ML-emitted solutions, because an MCP tool is a compatibility promise
to agents (docs/agents.md, mcp-api.md, schema) and this contract is one slice old. The CLI is
enough for third parties to gate their proposers in CI today.

## Consequences

- Any external proposer that can emit SimpleRouteJson solutions now has a deterministic,
  bounded, refusal-first gate: `verify-foreign-route` in CI, exit code 0, verdict as data.
- The verifier's obstacle model is the import seam's — rectangles that contain the source
  shapes. A route a finer model would accept can be refused here (over-approximation), and
  that is the chosen direction.
- Via and trace physicality is policy-conditional: SRJ solutions carry no via dimensions, so
  acceptance assumes the declared `ImportPolicy` sizes. The claim text says so; the risk is
  recorded (R-117).
- The import module gains a second caller and its docstring now says "import seam" rather than
  "benchmark seam"; its conservative mapping directions are now load-bearing for verification,
  which is exactly why they were specified as invariants rather than benchmark conveniences.
- Two documents that differ only in JSON formatting hash to different `solution_sha256`
  values. That is accepted: the address names the examined bytes, not a canonical route, and
  minting a canonical route identity is precisely what this seam refuses to do.

## Alternatives considered

**Convert the foreign route into a native candidate and reuse the existing verifiers.**
Rejected: minting an identity is adoption, and both native verifiers treat identity acceptance
as "ours". The trust question would be decided by a constructor call.

**Ignore unknown and reserved keys, verify geometry only.** Rejected: ignoring
`candidate_id` in the input produces responses that a caller can present alongside the forged
input as if the pair were coherent. Refusal keeps the forgery visible.

**Point-complete connectivity (join only the stated connection points).** Rejected for this
slice: it is weaker than pad-complete, requires re-parsing connection points the import does
not expose, and its only effect would be to *accept more* — the wrong direction to err in a
first slice.

**Epsilon-based floating-point geometry.** Rejected outright: an epsilon is a tunable lie in
a verifier. Doubled-nanometre integers keep every comparison exact, including for arbitrary-
angle segments.

**Expose an MCP tool now.** Rejected as premature compatibility surface; see above.

## References

- [Foreign candidate verification research](../research/foreign-candidate-verification-v1.md)
- [ADR-0001: Candidate-first mutation model](0001-candidate-first.md)
- [ADR-0025: Apply by splicing bytes](0025-file-level-candidate-apply.md) — "a candidate is
  never trusted from its manifest"
- [ADR-0066: Atomic route bundle preview](0066-atomic-route-bundle-preview.md) — structural
  absence of authority fields
- [D-154](../ledgers/decision-ledger.md), [SEC-121](../ledgers/security-ledger.md),
  [R-117](../ledgers/risk-register.md)
