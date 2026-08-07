# An identity for an outline assembled from Edge.Cuts segments

Research date: 2026-08-08. This note supports [ADR-0087](../adr/0087-composite-native-identity-for-assembled-outlines.md),
the decision recorded as [D-174](../ledgers/decision-ledger.md), the security review
[SEC-131](../ledgers/security-ledger.md), and the residual risk
[R-131](../ledgers/risk-register.md). It builds on
[KiCad UUID uniqueness](kicad-uuid-uniqueness-v1.md) (D-158) and the outline-assembly research
behind [ADR-0076](../adr/0076-segment-assembled-edge-cuts-outline.md). No external code is copied;
quoted fragments are short excerpts from KiCad's published developer documentation, cited so the
claims can be re-checked rather than trusted.

It answers exactly one question: **can a Board IR contour assembled from many `Edge.Cuts`
`gr_line` segments carry an identity that is (a) stable under every edit that leaves the outline
alone and (b) resolvable back to specific objects in the source file** — the two properties an
apply-gated identity needs and the revision-derived name lacks. It claims nothing about arcs,
polygons, or curves on `Edge.Cuts` (still refused, ADR-0076), and nothing about schematic UUIDs.

## The defect this note explains

Issue #126, measured read-only over the same twelve-board tree as #116: every board that converts
is refused by both apply gates. At this branch's baseline that is 11 of 11 converting boards
refused, and on three of them (`cue`, `fdr`, `mtr`) the assembled outline is the **only** derived
identity on the board — every footprint, pad, segment, via and zone is native. The outline is
derived on 11 of 11, because all of them draw `Edge.Cuts` with `gr_line` segments and only a
single `gr_rect` yields a native contour identity. Every committed fixture used `gr_rect`: the
same fixture-authored-from-the-code's-own-assumption failure as #104 and #116.

## Finding 1 — the format gives every outline member its own required identity

KiCad's board format defines the identity token on graphic items, and for graphic lines it is a
required token, not an optional one:

> "The UUID attribute is a Version 4 (random) UUID that should be globally unique."

with graphic lines carrying `(uuid UUID)` alongside `start`, `end`, `stroke`, and `layer`.
Files converted from pre-6.0 KiCad "have their locally-unique timestamps re-encoded in UUID
format", which is why the adapter accepts the legacy `tstamp` spelling wherever it accepts
`uuid`. Source: [KiCad S-expression introduction](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/),
re-read 2026-08-08.

So the contour's members are individually named by the file itself, durably: KiCad assigns the
uuid when the line is created and edits geometry, not identity, afterwards. What the contour
lacks is a *single* native identity — which is a fact about arity, not about resolvability.

## Finding 2 — "should be unique" still grants no key, so the composite must check its inputs

D-158's central finding carries over unchanged: "should be globally unique" is an expectation of
the writer, not a guarantee to a reader, and real boards reuse uuids across footprint instances.
A composite identity built from member uuids is therefore only as resolvable as its inputs, and
the derivation must refuse to produce a native-looking name from inputs it cannot resolve:

- a member with no `uuid`/`tstamp`, or with both, contributes no usable name;
- a value repeated **inside the member set** names neither member (the D-158 rule at composite
  scope).

In both cases the honest output is the existing revision-derived name, which every
source-preserving patch path already refuses (ADR-0026). Reuse *across* kinds (a `gr_line`
sharing a value with a footprint) does not threaten the composite: resolution is scoped to the
root `Edge.Cuts` `gr_line` population, and Board IR's own reuse accounting is kind-scoped for the
same reason.

## Finding 3 — measured: every surveyed real outline is composite-nameable

Read-only survey of `~/Desktop/13_Audio/projects/**/*.kicad_pcb`, excluding `.history/` and the
derived stems `routed-source`, `best-board`, `-placed` (12 boards):

| Property | Result |
|---|---|
| Boards drawing `Edge.Cuts` with `gr_line` (vs `gr_rect`) | **12 of 12** (0 `gr_rect`) |
| Outline members per board | 4 |
| Members carrying exactly one native identity | **48 of 48** |
| Member values pairwise distinct within their board | **12 of 12** |

So the composite derivation applies to every surveyed board with no degradation, and the
degradation path is exercised only by constructed fixtures — which is the right way around for a
fallback whose purpose is to fail closed.

## Finding 4 — the revision-derived name is structurally unappliable, not just gated

`_derived_identity` hashes `source_revision \0 kind \0 locator`, and the source revision is the
SHA-256 of the exact input bytes. Every apply surface proves its output equals the source
snapshot outside the target expressions after re-parsing — and the patch itself moves the
revision, so a revision-derived contour id changes on every apply and the equality can never
hold. The gate refusal (`source geometry uses revision-derived identities`) is therefore not an
over-strict check that scoping could relax: behind it, the round-trip proof is unsatisfiable.
Fixing the identity, rather than scoping the gates, is what lets all five equality proofs stand
unmodified.

Measured effect (same survey, same predicates, before → after): route gate 0 of 11 → **3 of 11**
pass; placement gate 0 of 11 → **3 of 11** pass; the 8 remaining refusals are all D-158
footprint/pad uuid reuse, unchanged and intended. The 12th board still refuses conversion
(`unsupported.construct`), untouched by this slice.

## What this note does not claim

- That member uuids are globally unique (Finding 2 refuses to rely on it).
- That a board whose outline members lack uuids becomes appliable — it degrades to derived and
  stays refused.
- That any curve-bearing outline converts (ADR-0076's refusals stand).
- That the three newly-appliable boards pass any downstream check beyond the identity gates
  measured here.
