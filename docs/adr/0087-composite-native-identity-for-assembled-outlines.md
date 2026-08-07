# ADR-0087: Name an assembled Edge.Cuts outline by the sorted set of its members' own uuids

- Status: Accepted
- Date: 2026-08-08
- Owners: CopperMCP maintainers
- Related: [ADR-0076](0076-segment-assembled-edge-cuts-outline.md) (supersedes its identity
  clause only); [ADR-0026](0026-first-class-footprints-in-board-ir.md);
  [Assembled-outline identity research](../research/assembled-outline-identity-v1.md);
  [KiCad UUID uniqueness research](../research/kicad-uuid-uniqueness-v1.md);
  [D-158](../ledgers/decision-ledger.md); issue #126

## Context

Every real project board that converts is permanently unappliable, and the sole remaining cause on
the cleanest of them is the board outline's name. Measured read-only across the twelve-board
corpus of issue #116 at this branch's baseline: eleven boards convert, and both apply gates —
`render_kicad_placement_candidate_board`'s `_require_native_geometry_identities` and the route
gate the apply engine and `_board_is_appliable` share — refuse all eleven. On eight, D-158's
reused footprint/pad UUIDs are a sufficient and correct cause. On three (`cue`, `fdr`, `mtr`),
**the assembled outline contour is the only derived identity on the board**: 0 derived footprints,
0 derived pads, 0 derived copper objects, 1 derived outline. Placement preview is healthy on all
of them. The fixtures never saw this because every committed fixture and the CopperTone board draw
their outline as the one shape that yields a native contour identity — a single `gr_rect` — which
is the third instance of the #104 pattern: fixtures encoding a shape real boards do not have. All
twelve real boards draw the outline the ordinary way, as `gr_line` segments.

ADR-0076 gave the assembled contour a **revision-derived** identity, with a stated reason: it
"deliberately does not adopt the `uuid` of any one segment, which would name a segment while
claiming to name the contour." That reason is sound and this record does not contradict it. But
the revision-derived name has a property ADR-0076 did not need and the apply paths cannot live
with: it hashes the source revision, so **every byte change anywhere in the file moves it**. A
source-preserving patch splices a footprint pose or appends a segment, re-parses, and proves the
result equals the source snapshot outside the target expressions — an equality no revision-derived
identity can survive, because the patch itself moves the revision. A derived outline id therefore
does not merely trip the gate; it makes the round-trip proof unsatisfiable in principle.

The refusal the gates implement is a real safety property (ADR-0026): a source-preserving patch
names its target by identity, and an identity that cannot be resolved back to a specific object in
the source file must never be patchable by. D-158's reused-UUID degradation and the net-tie work
lean on exactly this: *derived means unappliable* is a load-bearing equivalence, and this decision
must not weaken it.

## Decision

**A contour assembled from `Edge.Cuts` `gr_line` segments takes a composite native identity
derived from its member segments' own uuids, when and only when that set is resolvable. The apply
gates do not change at all.**

Concretely, in `_assembled_contour_identity`:

- Every member `gr_line` must carry **exactly one** usable native identity — one `uuid` or one
  legacy `tstamp` expression containing exactly one atom. The value is lowercased, exactly as
  `_identity` treats every other native identity.
- The member values must be **pairwise distinct within the member set**. A value claimed by two
  members is an identity of neither (the D-158 rule applied at composite scope).
- If both conditions hold, the identity is
  `contour:assembled:sha256("contour" \0 "assembled" \0 sorted-member-ids...)[:32]`.
- If either fails, the contour **degrades to the existing revision-derived name** — never a
  refusal of the board, and never a guess. The board stays inspectable and stays unappliable,
  which is the same disposition D-158 chose for reused footprint UUIDs.

The `:assembled:` marker is deliberately not `:kicad:`: a `contour:kicad:<value>` promises that
`<value>` is the object's own uuid in the file, and this value is not — it is a derivation whose
inputs are uuids. Keeping the markers distinct keeps both promises checkable.

### The invariant, stated

**No patch can ever name an object whose identity cannot be resolved back to the source file.**

This decision preserves it three ways, each tested:

1. **The gates are untouched.** Both still refuse any snapshot containing any `:derived:`
   identity anywhere in the modeled geometry. Nothing that was refused for a reused UUID, a
   missing UUID, or any other unresolvable identity becomes appliable. The mutation check removes
   each gate's scan and the reused-UUID and derived-outline regression tests fail.
2. **The composite identity is resolvable.** Resolution is the derivation run backwards: collect
   the root `Edge.Cuts` `gr_line` native identities from the source file, sort, hash, compare —
   a test performs exactly this recomputation independently of the adapter. Each member then
   resolves to its own source expression by its own uuid, unique within the member set by
   construction. The contour names a specific set of source expressions or it does not get this
   name at all.
3. **The fallback is the guard.** Any member set that cannot be resolved — a member with no
   native identity, with two, or with a value repeated in the set — degrades to `:derived:` and
   stays refused by the unchanged gates. The mutation check deletes the fallback and four tests
   fail before any patch path is reached.

### Stability, proven rather than hoped

The composite name is a function of the member uuid set and nothing else. KiCad assigns a graphic
line's uuid at creation and rewrites geometry, not identity, when the line is edited; the format
lists `uuid` as a required token on graphic lines (research note, findings 1-2). Therefore:

- A **placement splice** rewrites footprint `at` expressions and pad angles; a **route apply**
  appends segments before the root's closing delimiter. Neither touches an `Edge.Cuts` byte, so
  the contour id is identical on both sides of the round-trip equality check — which is why no
  equality logic changes in any of the five render/apply surfaces.
- An **unrelated edit** (moving a footprint, renaming a net) moves the source revision but not the
  contour id. This is the exact property the revision-derived name lacked, pinned by test.
- **File order and drawing direction** are not inputs: the set is sorted before hashing, matching
  ADR-0076's rule that the assembled ring depends on the segment set alone.
- **Editing the outline itself** — adding, removing, or replacing a segment — moves the id. That
  is correct: the named member set is no longer the same set.

## Consequences

- Measured on the same corpus, read-only, after the change: the three boards whose only derived
  identity was the outline now pass **both** gates (0 of 11 → 3 of 11); the eight D-158 boards
  still refuse both. Every committed fixture with a `gr_rect` outline is byte-for-byte unaffected.
- The snapshot digest of any board with a `gr_line` outline whose members carry uuids changes,
  because its contour id changes. No committed golden pins such a board: the golden-identity
  suite passes unchanged, and the `source-to-board-parity` fixtures' outline members carry no
  uuids, so they keep their derived contour ids and digests. The new committed fixtures
  (`two-pad-segment-outline.kicad_pcb`, `footprint-pose-courtyard-segment-outline.kicad_pcb`) pin
  the assembled path so the fixture set cannot drift back to `gr_rect`-only (issue #126's
  regression requirement).
- Circuit Scene reports an `:assembled:` reference as `native` durability: it survives unrelated
  edits and resolves to the file's own identities, which is what that signal means to a caller
  deciding whether a stored reference must be re-read. `all_board_refs_native` thereby stays
  aligned with what the apply gates would actually accept.
- ADR-0076's identity clause is superseded by this record; its assembly, direction-of-error, and
  no-repair decisions are untouched. Composing all members' uuids does not trip its objection to
  adopting one member's uuid: the name names the set.
- D-158 reused-UUID boards and every other derived-identity construct remain unappliable. This
  record changes which contours are derived; it does not change what derived means.

## Alternatives considered

**Scope each gate to the object kinds its own patch can name** (the route gate to copper and the
pads it attaches to, the placement gate to footprints and pads — the issue's direction 1).
Rejected, for two reasons. First, it is the dangerous direction: it edits both gate predicates and
requires a per-patch theory of "nameable kinds" that every future patch surface must remember to
extend — a scoping error there is silent and unsafe, whereas a fallback error here leaves a board
refused. Second, it does not actually work alone: the revision-derived contour id moves when the
patch moves the revision, so every round-trip equality proof (placement render, route render,
layered render, bundle render, apply engine) would need a special case accepting an outline id
change as "unchanged outside the patch". Weakening five equality proofs to avoid one identity
derivation is the wrong trade.

**Adopt one member segment's uuid.** Rejected by ADR-0076 and still rejected: it names a segment
while claiming to name the contour, and it makes the contour id move or survive depending on which
member happened to be chosen.

**A fixed structural name** (for example `contour:edge-cuts`, resolvable because the adapter
enforces exactly one outline contour). Rejected: it is resolvable but not content-bound — every
board's assembled outline would carry the same id, so a stored reference could silently re-bind
across boards, and an outline edit would not move the name that claims to identify it.

**Register member uuids in the D-158 reuse ledger.** Rejected as unnecessary: reuse accounting is
kind-scoped, members are material for one composite rather than modeled objects, and the only
reuse that threatens the composite's resolvability — inside the member set — is checked directly
and degrades to derived.
