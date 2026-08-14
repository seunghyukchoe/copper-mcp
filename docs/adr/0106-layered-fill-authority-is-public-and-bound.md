# ADR-0106: A layered candidate records its obstacle model before the layered seam may reach one

- Status: Accepted
- Date: 2026-08-14
- Owners: CopperMCP maintainers
- Related: [ADR-0021](0021-zone-fill-authority.md);
  [ADR-0039](0039-fill-aware-routing-obstacles.md);
  [ADR-0040](0040-public-fill-routing-provenance.md);
  [ADR-0070](0070-layered-fill-aware-obstacles.md);
  [ADR-0101](0101-fill-currency-is-not-in-the-document.md);
  [ADR-0103](0103-a-candidate-records-the-model-that-produced-it.md);
  [ADR-0104](0104-fill-vertex-budget-behind-a-parse.md);
  issue #164; issue #63; issue #167;
  `D-198`, `R-152`, `SEC-146`

## Context

[ADR-0070](0070-layered-fill-aware-obstacles.md) gave the ordered-layer adapter the ability to
retire a foreign zone's outline envelope in favour of freshness-verified pour islands, gated four
ways. [ADR-0040](0040-public-fill-routing-provenance.md) then made the *single-layer* equivalent
publicly explainable: a `routing_effect` label so a caller can tell an exact-pour candidate from an
envelope one. The layered half stopped after the first step, and
[issue #164](https://github.com/seunghyukchoe/copper-mcp/issues/164) filed the second.

The [post-0.8.0 audit](../audit/2026-08-14-post-0.8.0-audit.md) found that this is not a
documentation gap. It is a **parity divergence, and it runs in both directions.**

**Verified**: `LayeredRouteCandidate` recorded `candidate_id`, `base_revision`, `start_pad_id`,
`end_pad_id`, `patch`, `cost`, `metrics`, `settings`, `router_version`, `policy` and `seed` — and no
fill binding of any kind. So ADR-0103's invariant — **a candidate replays under the model that
produced it** — was simply unenforced on this path. `render_kicad_layered_candidate_board` replayed
through `LayeredBoardRouter.propose(snapshot, request)` with whatever `verified_fill` the caller's
request happened to carry, and compared the result to the candidate. Nothing bound those two
together.

**Verified**: the public layered seam supplied no fill at all. `layered_route_preview.py` built its
`LayeredRouteRequest` without `verified_fill`, so it took the dataclass default of `()`. The
divergence was therefore **latent rather than live**.

Latent is not safe here, because ADR-0103 named two triggers that would make it live and **one has
already fired**: [ADR-0104](0104-fill-vertex-budget-behind-a-parse.md) raised `max_fill_vertices`
from 50,000 to 500,000 in the same release. The other trigger is exactly what #164 asks for. That
is why the ordering below is a decision and not a preference.

The direction-of-error analysis is ADR-0103's, unchanged, and it is why one equality is the whole
fix:

- **Understated** — a candidate routed under the exact pour, replayed without it. The zone outline
  envelope over-approximates the pour it replaced, so the replay searches a *stricter* model than
  the route did. On this repository's own layered fixture that is not an error but a **different
  route**: 8,000 nm becomes 14,000 nm, and the serializer then blames the candidate for a
  disagreement its own verifier caused. That is issue #163's shape, on this path.
- **Overstated** — a candidate routed under the envelope, replayed *with* fill. The replay searches
  a *looser* model: the evidence retires envelopes the original search never saw retired. A replay
  that agreed under that model would confirm geometry the router never proved. This is the
  dangerous direction, and nothing about the shape of the defect prevents a caller from reaching
  it once the public flag exists.

## Decision

**Item 0 first: a layered candidate records the obstacle model that produced it, and the layered
replay refuses any other one. Only then does the public flag exist.**

1. `LayeredRouteCandidate` gains `fill_binding: str | None`, computed by the **same**
   `fill_binding_for` the single-layer path uses, over the **same** `VerifiedFill` values
   `LayeredRouteRequest.verified_fill` already carries. This is the load-bearing shape choice and
   it is argued below.
2. `LayeredBoardRouter.replay(snapshot, candidate, request)` is the layered analogue of
   `AStarRouter.replay`. It refuses with a new `LayeredRouteFailureCode.FILL_EVIDENCE_MISMATCH`
   unless `fill_binding_for(request.verified_fill)` equals the candidate's recorded binding, and
   only then delegates to `propose`. **One equality enforces both directions**, and that is the
   whole safety argument. Neither direction is reachable without changing that one comparison.
3. `render_kicad_layered_candidate_board` replays through that method and reports a mismatch under
   its own message — `"candidate was routed under verified zone fill that was not supplied for
   replay"` — instead of `"candidate does not match a deterministic router replay"`.
4. `LayeredRoutePreviewRequestContract` gains `include_fill_authority`, opt-in, with the same
   fail-closed `stale_fill` refusal `preview_route` has. `preview_layered_route` runs
   `run_zone_fill_authority` and passes the islands to the adapter's existing `verified_fill`.
5. A routed layered response carries `fill_authority` with one closed `routing_effect` literal —
   the same four ADR-0040 labels, on the same `RouteFillAuthorityContract`. It is selected over the
   **signal layers the ordered-layer search reached**, not over one caller-named layer, because a
   layered route is not confined to a layer the caller chose.
6. `LiveLayeredRoutePreviewRequestContract` **pins** `include_fill_authority` to `Literal[False]`,
   and `RoutingJobRequestContract` pins it too. Both are argued below.

### Why the binding reuses the single-layer digest rather than defining a layered one

`LayeredRouteRequest.verified_fill` is `tuple[VerifiedFill, ...]` — literally the type
`routing.astar` defines and the single-layer router consumes. A separate layered digest would be a
second definition of one fact: two functions that must agree about what "the same fill" is, with
nothing forcing them to. Reusing `fill_binding_for` makes the agreement structural. It also
inherits, unchanged and for the same reasons, ADR-0103's two properties: `fill_binding_for(())` is
`None`, because an empty pour and no pour give the router the same obstacle model and must give it
the same candidate; and the binding covers every field of every island **in the order the caller
supplied them**, so any difference at all in the evidence changes it. That is the
over-approximating side of the choice, which is the side this repository takes for obstacles.

Island order is where that last property earns its keep here. Two islands supplied in either order
are the same obstacle *set* and produce a candidate identical in every field but the binding — the
committed test proves that by constructing it. A replay comparing only geometry would agree. The
recorded binding is what refuses, which is what makes the equality a statement about provenance
rather than an incidental geometry check.

### Why no published content address moves

`fill_binding` enters `canonical_layered_candidate_bytes` **only when it is not `None`**, exactly as
ADR-0103 arranged for the single-layer candidate, and for exactly the same reasons:

- A candidate routed under the conservative envelopes is the same proposal it always was, so its
  published address must not move.
- Emitting `"fill_binding":null` would move **every** layered candidate address at once to record
  an absence — including the two-, three- and four-layer identities pinned in
  `tests/test_layered_board_adapter.py` and the durable export pinned in
  `tests/test_golden_identities.py` — and would break every *persisted* layered candidate from
  every earlier router version, because `verify_layered_candidate_id` recomputes the address from a
  rehydrated candidate's own fields.

`LAYERED_ROUTER_VERSION` is therefore **not** bumped and no golden pin moves. The pins' stability is
now a *stated consequence*: each pinned-identity test asserts `fill_binding is None` for its
fixture, and a committed mutant that emits the key unconditionally is killed by them.

One address that is not a candidate's had to be protected separately. `routing_job_service._spec`
content-addresses the persisted job request document, and that digest is golden-pinned as
`ROUTING_JOB_SERVICE_REQUEST_DIGEST`. `include_drc` was already excluded from that document because
a job cannot grant it; `include_fill_authority` joins it in one named set, so the envelope names
neither and the digest does not move. An envelope that named a capability nothing can grant would
re-address every queued job in an existing ledger to record it.

### Why the live layered path pins the flag to `Literal[False]`

Recorded either way, as #164 asked. **Pinned.** Zone fill authority proves a *file's* cached fill
fresh, by refilling a private disposable copy of that file and comparing canonical fill geometry. A
live proposal routes an IPC snapshot of an editor that may hold unsaved changes, so there is no file
whose cache the proof would be about. Accepting the flag could therefore only mean ignoring it,
which is a silently unhonoured authority request — the failure mode this project pins
`include_drc: Literal[False]` on the same contract to avoid. `LiveRoutePreviewRequestContract`
already pins the single-layer analogue, so this is parity rather than a new posture, and the live
parser refuses an explicit `true` at the boundary rather than letting the schema be the only guard.

`RoutingJobRequestContract` pins it for a different reason with the same shape: a job runs in a
later process against bytes it re-reads, holding no fill evidence, so a candidate carrying a binding
could never be replayed from its persisted envelope. This is ADR-0103's token rule — a capability
whose exercise is guaranteed to refuse must not be issued — applied to a queued job.

### What is *not* claimed

**No route-quality claim attaches to any of this.** `B-105` measured fill-aware routing on the real
board corpus at **zero changed verdicts**; the only measured benefit is the synthetic B-021/B-086
corridor. The justification for this record is provenance and parity, and it is worth doing on those
terms alone. No `B-` number is spent here (see Consequences).

### The both-directions proof, as run

`docs/mutants/2026-08-14-layered-fill-contract.json` carries 18 mutants, all `killed`
(`python_version` and `platform` are in the harness report). Two of them are the argument:

| Mutant | Understated-direction test | Overstated-direction test |
|---|---|---|
| `LF02` — equality guards only the *reported* (understated) direction | **passes** — survives | **fails** — kills |
| `LF03` — equality guards only the *dangerous* (overstated) direction | **fails** — kills | **passes** — survives |

Each half of the equality is therefore covered by a test the other half's test cannot substitute
for. A suite in which `LF02` survived would be a suite whose only fill-replay coverage was of the
direction that had already been reported — which is exactly the gap
[#169](https://github.com/seunghyukchoe/copper-mcp/issues/169) named on the single-layer path, so
the same shape is pinned here. `LF01` deletes the comparison outright and dies to both.

`LF04` is the third leg: it empties island geometry from the shared canonical fill bytes, and dies
only to the reordered-pour test — the case where the obstacle *set* is identical and the candidate
matches in every field but the binding. That is what shows the equality is a provenance check
rather than a geometry check wearing a digest.

## Consequences

- `preview_layered_route(include_fill_authority=True)` returns a `fill_authority` record with a
  closed `routing_effect`, and a candidate whose `fill_binding` says which model produced it.
  `include_drc` with it is supported: the preview forwards the evidence in the request the DRC path
  already carries, so the replay inside the serializer finds the model it needs.
- **A stale cache refuses.** `stale_fill` is now a layered diagnostic code and appears in
  `LayeredRouteDiagnosticContract`. `fill_evidence_mismatch` deliberately does **not**: only a
  replay produces it and a replay is never a preview response, which is the same reason
  `RouteDiagnosticContract` omits its single-layer twin.
- **Expect `invalid_request` on real boards.** The ordered-layer adapter refuses any island above
  4,096 vertices, and it refuses the *whole request* rather than skipping the island — the audit
  verified there is no per-island degradation to the envelope, contrary to what
  [issue #167](https://github.com/seunghyukchoe/copper-mcp/issues/167) describes. `B-108` measured
  **14 of 18 corpus boards** carrying an island above that ceiling, widest 43,889. So on most real
  zoned boards this flag will refuse rather than route. That is over-refusal, the safe direction,
  and it is #167's to fix; it is recorded as `R-152` so the new flag's real reachability is not
  overstated.
- A fill-bound layered candidate cannot reach live apply, and nothing had to be added there to
  ensure it. `layered_candidate_from_document` ignores keys it does not read and its result is
  re-hashed against the claimed identity; because the binding is now *part of* that identity, a
  manifest claiming one rebuilds without it and fails the recomputation. The committed test is what
  makes that absence evidence rather than an assumption.
- Every layered response document gains a `fill_authority` key, `null` on all four outcomes except a
  routed one that asked for it. The layered *candidate* document gains `fill_binding`, `null` in the
  ordinary case — named explicitly in the response, omitted from the canonical identity, which is
  the same split ADR-0103 established.

## Alternatives considered

**Ship `include_fill_authority` first and add the binding after.** Rejected, and this is the whole
of arbitration delta A1. It would make the divergence reachable through the public surface for
however long the binding took, in a direction that confirms geometry the router never proved. The
audit's ordering constraint says "with or before"; "before" is what landed, and the binding's tests
and mutants were written and failing before any public field existed.

**Define a layered-specific fill binding.** Rejected — see above. Two digests over the same values
is one fact with two authorities.

**Add `fill_evidence_mismatch` to `LayeredRouteDiagnosticContract` for symmetry.** Rejected. A code
in a closed output literal is a claim that a response can carry it. This one cannot: the only
producer is `replay`, whose caller raises a `KiCadLayeredRoutePatchError` rather than returning a
document. Publishing it would widen the advertised schema to describe an unreachable state, and
`RouteDiagnosticContract` already declined the identical widening.

**Refuse a fill-bound candidate inside `verify_layered_candidate`**, mirroring the way
`verify_candidate_path` refuses one on the single-layer side. Rejected: that validator is
structural-only — identity, revision, endpoints, continuity, budgets — and models no obstacles at
all, so it makes no copper claim that fill could falsify. It is also called by the serializer
*after* the replay, so refusing there would make `include_fill_authority` with `include_drc`
permanently unusable, which is the surface #164 exists to deliver. The seam that genuinely cannot
establish the model is live apply, and that one fails closed already, by construction.

**Compute the layered `routing_effect` over one named layer**, reusing the single-layer helper
unchanged. Rejected: the single-layer router is given a layer and searches it, so "the selected
layer" is meaningful there. An ordered-layer search reaches the whole signal stack, so an island on
any searched layer is an island that could have shaped the route, and restricting the label to one
of them would under-report the evidence's role.
