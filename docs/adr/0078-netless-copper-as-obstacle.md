# ADR-0078: Net-0 copper is an obstacle with no connectivity contribution

- Status: Accepted
- Date: 2026-08-07
- Owners: `@seunghyukchoe`
- Related: [Issue #119](https://github.com/seunghyukchoe/copper-mcp/issues/119),
  [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0011, ADR-0017,
  ADR-0072, ADR-0077, [KiCad net-0 research](../research/kicad-net0-copper-v1.md)

## Context

KiCad assigns net 0 — its own defined "unconnected net" — to copper that exists on the board
but has no netlist attachment: stitching vias placed over a pour, and tracks orphaned by
netlist changes. KiCad 10 writes it as `(net "")`. The Board IR adapter answered that value
with a whole-document refusal, `via has no routable net`, which the re-measured issue #116
survey showed queued up behind the courtyard causes on **7 of the 12 real project boards**
(115 netless vias). The survey for this change found the part the issue could not see: the
same boards carry **2,687 netless track segments** — roughly ten netless tracks for every
netless via — so any via-only fix would have moved the refusal message one line down and
unblocked zero boards.

A copper item with no resolvable net asks two different questions, and the single refusal
conflated them:

1. **Is it an obstacle?** Yes, unambiguously. A via barrel and its annulus, and a track's
   swept rectangle, are physical copper whatever their net. Obstacles over-approximate
   (ADR-0011, ADR-0017, ADR-0072); *dropping* this copper — which is what refusing to model
   it amounts to, from the router's point of view — is the one direction the obstacle rule
   forbids.
2. **Can it support a connectivity claim?** No. Connectivity under-approximates, and a net
   that cannot be resolved cannot be test-bound to any claim. Declining to *claim* a
   connection through it is correct and stays.

The board is readable; what is unverifiable is one claim about one object. Refusing the
document answered question 2 by also getting question 1 wrong.

## Decision

**Copper saved on KiCad's net 0 converts as an obstacle with no connectivity contribution:
`net_id` becomes `None` on `Via`, `Segment`, and `Arc`, uniformly.** All three saved
spellings of "no net" — `(net "")`, `(net 0)`, `(net 0 "")` — resolve to it. The rule
covers segments and arcs, not only the vias issue #119 names, because the survey shows the
copper population is the same phenomenon and the modelling argument does not distinguish
them.

What `None` means downstream is already the meaning both routers give a net that is not the
requested one:

- **Obstacle, everywhere geometry is consulted.** Every via becomes an obstacle regardless of
  net; a `None` net simply never equals the request net, so netless segments and arcs join
  the foreign-copper obstacle set. Clearance uses the widest net class on the board — the
  same rule netless (NPTH) pads already use — because it is the only choice that cannot
  under-inflate.
- **Absent from connectivity, structurally.** Same-net attachment copper, the
  already-connected claim, and the multilayer via-join recognition all select copper by
  `net_id == request.net_id`, which `None` can never satisfy. A route needing that via still
  reports unconnected; a route crossing its barrel is still refused.
- **Absent from the net set.** The empty name never becomes a `Net`; nothing can address,
  route, or constrain "the netless net".

Malformation stays refused. A negative net ordinal is KiCad's in-memory `ORPHANED` sentinel
that its writer never saves, so `(net -1)` is now an explicit typed `net.unknown` refusal
rather than an accidental synonym of net 0, and a netless via is still held to every
geometric rule (layer span, drill smaller than diameter, through-stack span).

Zones deliberately keep a required net. A netless copper *pour* did not occur in the corpus,
and a zone's semantics (thermal relief, pad connection, island removal) are all defined
relative to a net; admitting a netless zone would be a guess, not an observation.

Contract surfaces widen compatibly, without a digest migration: `net_id` becomes nullable
for vias, segments, and arcs in the Board IR dataclasses, the codec, JSON schema 0.2.0, and
the scene geometry contracts — the exact shape pads already had. Every previously valid
snapshot remains valid and every existing content address is byte-identical, which the
committed golden digests pin; the schema is widened in place rather than versioned because
the change is strictly additive to the accepted document set.

## Consequences

- The `via has no routable net` refusal — the largest single blocker in the issue #116
  survey — is gone as a cause: 0 of 12 real boards refuse for netless copper (vias *or*
  tracks), where 7 of 12 carried it. The remaining refusals are the unmerged courtyard
  causes (#118) and defects this refusal was masking: one board saved with duplicate
  KiCad UUIDs (`identity.duplicate`, a genuine native-identity collision, refused
  correctly), one with a copper-layer-less pad, one with an unsupported footprint field.
- Connectivity honesty is asymmetric by design and now visibly so: a physically stitched
  ground net will *never* be claimed connected through netless stitching vias, because the
  claim cannot be net-bound. That permanent under-claim is recorded as a risk-register row
  rather than smoothed over.
- Netless copper is cleared at the widest class on the board, which can force wider detours
  than the copper's true net would require. Over-refusal is the accepted direction.
- The guard is mutation-checked: making a `None` net compare equal to the request net flips
  the stitching-via join scenario to an already-connected claim and fails three router
  tests; the same mutation on the segment attachment guard fails a fourth.

## Alternatives considered

- **Per-object typed diagnostic, board still refused** (issue #119's fallback). Strictly
  more honest than the status quo but still answers the obstacle question wrongly: the
  board is readable and the copper is real. Rejected in favour of modelling the copper.
- **Vias only, as the issue title says.** Would have changed the refusal message on every
  affected board (`segment has no routable net` next) and unblocked nothing. The survey
  data, not the issue title, defines the phenomenon. Rejected.
- **Synthesizing a net (e.g. binding netless copper to the enclosing zone's net).** KiCad
  itself computes that attachment only at fill time, from connectivity this model does not
  trust (ADR-0070 refuses stale fill for exactly this reason). Inventing the attachment
  would let a router *claim* a connection through copper whose net is a guess — the
  under-approximation rule's one forbidden direction. Rejected.
- **Dropping netless copper from the model.** Under-approximates obstacles; a proposed
  route could cross a real barrel. Rejected without ceremony.
