# ADR-0103: A candidate records the obstacle model that produced it, and a replay refuses every other one

- Status: Accepted
- Date: 2026-08-13
- Owners: CopperMCP maintainers
- Related: [ADR-0021](0021-zone-fill-authority.md);
  [ADR-0039](0039-fill-aware-routing-obstacles.md);
  [ADR-0040](0040-public-fill-routing-provenance.md);
  [ADR-0025](0025-file-level-candidate-apply.md);
  [ADR-0047](0047-redacted-candidate-manifest-persistence.md);
  [ADR-0048](0048-durable-routing-request-result-export.md);
  [ADR-0101](0101-fill-currency-is-not-in-the-document.md);
  issue #163; issue #63

## Context

`AStarRouter.replay` rebuilt a `RouteRequest` from the candidate's own fields and called
`propose` with no `verified_fill`. A `RouteCandidate` carried no record of the fill it was routed
under, so there was nothing to rebuild one from. Under ADR-0039 the fill-aware path can produce a
candidate that routes through a pour void the conservative zone envelope blocks; that candidate
cannot reproduce under an obstacle model that still has the envelope in it.

The failure is demonstrated on B-021's own fixture, and on real KiCad bytes through the published
surface:

| Surface | Route | Replay | Result |
|---|---:|---:|---|
| B-021 synthetic fixture (`scripts/benchmark_fill_aware_routing.py`) | 8,000 nm | 14,000 nm | replayed the *envelope* route, no diagnostic |
| `tests/fixtures/route-candidate/blocked-zone.kicad_pcb` | 20,000,000 nm, 0 bends | 29,500,000 nm, 2 bends | `KiCadRoutePatchError("candidate does not match a deterministic router replay")` |

Two published surfaces broke. `preview_route(include_fill_authority=True, include_drc=True)`
refused a legitimate candidate through `run_route_candidate_drc` →
`render_kicad_candidate_board` → `_replay_candidate`, reported to the caller as
`"route candidate failed replay-verified KiCad serialization"`. And
`include_apply_token=True` minted a token whose apply reached the same `_replay_candidate` and
was guaranteed to refuse.

**The direction of the error was benign, and that is the load-bearing fact.** Obstacles in this
repository over-approximate, and the zone envelope over-approximates the exact pour: everything
the envelope calls copper the pour may or may not, and everything the pour calls copper the
envelope does too. So a replay that lost the fill was *stricter* than the route that produced it,
and the disagreement surfaced as a refusal — the safe side — mis-attributed to the candidate. The
same defect with the models the other way round would confirm geometry the router never proved.
Any fix must make the dangerous direction impossible by construction rather than by care, because
nothing about the shape of the bug prevents a future caller from threading fill into a replay of a
candidate that never saw it.

The invariant this states, and which the suite asserted nowhere: **a candidate replays under the
model that produced it.** A replay that silently substitutes a different obstacle model is not a
replay.

The defect is latent rather than guarded, and the reason is coverage rather than a check.
Re-measured here on the private working corpus with a conversion-only census (B-107): **1 of 13
converting saves carries an `F.Cu` zone at all**, a single four-vertex outline, against 21 on
`B.Cu` and 27 across the two inner layers, which reproduces
[B-105](../ledgers/benchmark-ledger.md)'s independent finding on the same tree. So the
single-layer fill shrink has at most one board there it could engage on, and nothing in the
shipped runner asks it to. B-105 further measured that seven of the twelve zoned boards carry a
pour past the shipped `max_fill_vertices` and two more are `stale_fill`; reading a pour needs
KiCad and that half is cited rather than re-measured here. The defect stops being latent the
moment the vertex budget is raised or the layered path gets a public contract — and neither of
those is a guard.

## Decision

**A candidate records the obstacle model that produced it, as a binding rather than as the
evidence, and a replay refuses any model that is not that one.**

1. `RouteCandidate` gains `fill_binding: str | None` — the sha256 content address of the
   freshness-verified zone fill the router was handed, or `None` when it was handed none and
   searched against the conservative envelope. `fill_binding_for(())` is `None`, because an empty
   pour and no pour give the router the same obstacle model and must give it the same candidate.
2. The binding covers every field of every island, in the order the caller supplied them. Any
   difference at all in the evidence — including its order — changes the binding. That is the
   over-approximating side of the choice, which is the side this repository takes for obstacles.
3. `AStarRouter.replay` accepts `verified_fill` and refuses, with a new
   `RouteFailureCode.FILL_EVIDENCE_MISMATCH`, unless `fill_binding_for(verified_fill)` equals the
   candidate's recorded binding. **One equality enforces both directions**, and that is the whole
   safety argument: a candidate with a binding replayed without evidence refuses, and a candidate
   with *no* binding replayed *with* evidence refuses too. The second is the dangerous direction,
   and it is now unreachable without changing this equality.
4. `render_kicad_candidate_board`, `run_route_candidate_drc` and `apply_route_candidate` accept
   and forward the evidence, and `_replay_candidate` reports a fill mismatch under its own message
   — `"candidate was routed under verified zone fill that was not supplied for replay"` — instead
   of blaming the candidate for a disagreement the verifier caused.
5. `preview_route` passes the fill it already holds to candidate DRC, so
   `include_fill_authority` with `include_drc` works.
6. `preview_route` **withholds the apply token** for a candidate the exact pour shaped. Apply runs
   in a later process and holds no fill evidence, so it can only replay under the envelope. A
   token is a capability, and a capability whose exercise is guaranteed to refuse must not be
   issued. This is the same rule that already withholds a token for a board the append-only apply
   engine could never accept. The candidate and its DRC evidence are still returned.
7. `verify_candidate_path` refuses a foreign candidate carrying a binding. That validator models
   zones by their envelope and holds no fill evidence, so validating such a path would be this
   same defect with the blame pointed at a foreign candidate.

### Why the published content addresses do not move

`fill_binding` is present in the canonical identity payload **only when it is not `None`**. This
is not an encoding convenience; it is the point:

- A candidate routed under the envelope is the same proposal it always was — same geometry, same
  cost, same recorded work, same settings — so its published content address must not move.
- Emitting `"fill_binding":null` would move **every** candidate address at once to record an
  absence, and would also break every *persisted* candidate from every earlier router version:
  `verify_candidate_id` recomputes the address from a rehydrated candidate's own fields, and a
  rehydrated pre-ADR-0103 candidate would gain a key its stored address does not cover. Under
  `tests/test_golden_identities.py`'s own standard that is a corrupted artifact, not a cosmetic
  diff.

So `ROUTER_VERSION` is **not** bumped and no pin in `tests/test_golden_identities.py` moves. The
route-candidate pin and its payload byte count are unchanged and are now tied to the stated
reason: the golden test asserts `fill_binding is None` for that fixture, so the pin's stability is
a consequence rather than a coincidence. The identity that *does* move is a fill-routed
candidate's, and that population has no pinned address anywhere and could not survive a replay at
all before this change, so no caller can be holding a usable one.

Safety for pre-ADR-0103 candidates is unchanged or strictly more refusing. An old candidate that
was fill-routed carries no binding, so a replay handed the matching fill refuses on the binding,
and a replay handed nothing refuses on the geometry as it did before. Both fail closed.

## Consequences

- `include_fill_authority` with `include_drc` is a supported combination and returns authoritative
  DRC evidence for a fill-routed candidate.
- `include_fill_authority` with `include_apply_token` returns a candidate and **no token**, where
  it previously returned a token that could not be applied. Callers that treated a present token
  as unconditional must handle its absence, which they already had to for a non-appliable board.
- Applying a fill-routed candidate remains unavailable. Making it available means re-establishing
  fill evidence inside the apply process — a KiCad refill from the destructive tool, on bytes the
  apply compare-and-swap already pins — which is its own design with its own security review, and
  is filed separately rather than smuggled into a bug fix.
- Every verifier of a routed candidate now has a parameter it must be given deliberately. Omitting
  it verifies a candidate routed under nothing, which is the ordinary case and the safe default.
- `fill_binding` appears in the `preview_route` and `route_bundle` candidate documents, in the
  apply manifest, and in `RouteCandidateContract`, only when it exists.

## Alternatives considered

**Thread `verified_fill` through the replay call sites without recording anything on the
candidate** (issue #163's option 2). Rejected: it fixes the false refusal and leaves the dangerous
direction wide open. Nothing would bind the fill a verifier supplies to the fill that produced the
route, so a verifier holding *different* fill — including fill that opens corridors the envelope
closed — would replay against it and agree. The recorded binding is exactly what makes that
impossible, and it costs one digest.

**Refuse to replay a fill-routed candidate, and refuse the flag combination at the request
boundary** (issue #163's option 3; the cheaper of the two shapes this change was scoped between).
Rejected as the *whole* fix, and adopted as part of it. It is honest — it turns a confusing
downstream refusal into an accurate boundary one — but it leaves `include_fill_authority` with
`include_drc` permanently unusable, which is the surface a caller actually wants, and by itself it
still does not prevent the dangerous direction: it says nothing about a replay of an
*envelope*-routed candidate handed fill. What it gets right is kept: a verifier that cannot
establish the model refuses under its own message, at `_replay_candidate`, at the apply engine, and
in the token gate.

**Carry the fill islands themselves on the candidate.** Rejected. It would make replay
self-contained — attractive, because it is what would let apply work in a later process — and the
freshness argument survives, since the island's `source_revision` pins the board bytes the apply
compare-and-swap already re-checks. But a candidate is a small, content-addressed value that
travels through MCP responses, redacted manifests ([ADR-0047](0047-redacted-candidate-manifest-persistence.md)), durable exports
([ADR-0048](0048-durable-routing-request-result-export.md)) and job
ledgers, and a pour bounded only by `max_fill_vertices` is not small. It would also make the
candidate its own authority on where copper is, which is precisely what ADR-0021 refuses: the
router believes fill only as evidence someone else proved current.

**Bump `ROUTER_VERSION` and move the pins anyway**, to record that a new-router envelope candidate
positively asserts "no fill shaped me" where an old one merely omits the question. Rejected: it
changes no geometry and no behaviour for that population, it makes every stored `candidate_id`,
`bundle_id` and exported candidate unreproducible, and the safety analysis above shows old
candidates already fail closed in both directions. Moving a published address is a cost imposed on
every caller, and there has to be something on the other side of it.
