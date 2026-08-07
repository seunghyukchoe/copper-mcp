# ADR-0090: A root board group is organisation, accepted and counted rather than modelled

- Status: Accepted
- Date: 2026-08-08
- Owners: `@seunghyukchoe`
- Related: [Issue #129](https://github.com/seunghyukchoe/copper-mcp/issues/129),
  [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0005, ADR-0072,
  ADR-0076, ADR-0077, [KiCad board groups research](../research/kicad-board-groups-v1.md),
  [B-096 real-board capability](../research/tier2-real-board-capability-v1.md)

## Context

A root-level `(group …)` refused an entire real KiCad 10 board with a message that named nothing:

```text
code:    unsupported.construct
message: root expression contains an unsupported semantic construct
locator: kicad_pcb.unsupported
```

`kicad_pcb.unsupported` is a constant. Neither the message nor the locator identified the head, the
field, or the position, so the refusal was unactionable from its own text — the same class of
defect #122 fixed one layer down.

**As of this record the decision unblocks no board that is blocked today, and the timing is stated
rather than implied.** B-096 records `cue` as 1 of 12 corpus boards refused for exactly this
construct, measured 2026-08-07, when that board's then-current save carried three groups. The
designer re-saved it without them on 2026-08-08; today the only documents in the tree carrying a
group are two `pcbnew` backups (`.kicad_pcb.bak-*`), and every currently-saved `.kicad_pcb` has
none. Both records are true of their own moment, and this one does not correct B-096. What follows
is that the blocker was real and resolved itself, so this lands as pre-emptive hardening against a
construct KiCad writes whenever a designer groups a selection — and that a designer grouping a
selection and later ungrouping it is, on the evidence of that very board, ordinary. Claiming a live
unblock today would repeat the measurement error #116 published a correction for.

Two questions had to be answered, and they are independent.

**What is a group?** It is one of the root sections the KiCad board file format enumerates —
"Header, General, Layers, Setup, Properties, Nets, Footprints, Graphic Items, Images, Tracks,
Zones, Groups" — not an anomaly. KiCad's own model settles most of what it carries: `PCB_GROUP` is
documented as "a transparent container - e.g., its position is derived from the position of its
members", its `SetLayer` is a no-op, and `IsOnCopperLayer` is false because "a group might have
members on a copper layer, but isn't itself on any layer". It has no net member and no design-rule
field. Membership is by UUID, and every member is a root object the adapter already converts on its
own terms. The writer emits exactly a quoted name, a `uuid`, an optional `locked`, an optional
design-block `lib_id`, and the `members` list.

**With one exception, and it is the one that matters.** `pcb_group.h` is not the whole model.
`BOARD_ITEM::IsLocked()` in `pcbnew/board_item.cpp` opens:

```cpp
if( EDA_GROUP* group = GetParentGroup() )
{
    if( group->AsEdaItem()->IsLocked() )
        return true;
}
```

A locked group therefore makes **every member locked**, transitively through nested groups, without
any member's own s-expression saying so. CopperMCP treats footprint lock as a hard authorization
gate, not a hint: `placement/solver.py` will not select a locked footprint as a subject,
`placement/legalizer.py` raises "moving a locked footprint is not authorized", and
`kicad_placement_patch.py` refuses "locked footprint movement is unsupported". So a group is *not*
uniformly inert, and the first version of this decision was wrong to say it was.

**How should a refusal name a construct?** Issue #129 proposed interpolating the rejected head into
the message, arguing that a format head is a fixed vocabulary term rather than board content. That
premise holds for a head the format defines and fails for one it does not: an arbitrary document can
carry an arbitrary head. The repository's standing invariant is that the board's own text is
untrusted data that never reaches an instruction-bearing field, and the existing regression
`test_diagnostics_never_echo_attacker_controlled_construct_names` already refuses that change
directly — one of its four cases is a root head named `SECRET_BEARER_TOKEN`.

## Decision

**1. Accept an *unlocked* root `(group …)` as editor organisation, on a closed shape.** The head
joins the root vocabulary; the allowlist does not open. A group's children are validated against
the head vocabulary KiCad's writer emits (`uuid`, `locked`, `lib_id`, `members`) — a depth-one head
check, not a full grammar — and its leading name atom is required with no other positional
semantics permitted. A group carrying anything else is a construct that has not been read, and
refuses.

**1a. Refuse a group carrying `(locked yes)`, or any `locked` value other than `no`.** A locked
group is a constraint-bearing construct, and reading it past would convert its member footprints at
`locked=False` and authorize a move KiCad forbids. The refusal names it: `a locked group locks its
members and is unsupported`, with `object_kind: group` and the child index.

**2. Do not model the grouping, and say so in a field.**
`ConversionResult.unmodelled_group_count` reports how many groups were accepted and not modelled.
It is a count and not a diagnostic, following D-157: every caller of `parse_kicad_bytes` treats a
non-empty `diagnostics` tuple as a refusal, so a warning would refuse the board this decision
exists to admit.

**3. Make every root refusal say where it refused.** The locator becomes `kicad_pcb.child[N]`,
where `N` is the index of the offending root child, computed from the parse and never read from the
document.

**4. Name the construct only from a closed table.** `_UNMODELLED_ROOT_HEADS` maps a documented root
section this adapter does not model to a fixed refusal sentence. The sentence emitted is a *value
from that table*, selected by an equality test against the source token and never built from it, so
the bytes reaching the caller are the adapter's. A head absent from the table is not a documented
root section, is refused without being named, and is still located by its index.

## Consequences

**The direction-of-error invariant is untouched for geometry, and made to fail closed for lock.**
Obstacles may only be over-approximated and connectivity and the board outline only
under-approximated. An unlocked group contributes no quantity to round in either direction, because
it owns no coordinate, no layer and no net. The proof is an equality: converting the real board with
its three groups and converting the same bytes with only those three expressions deleted produces
Board IR content differing in exactly one field, `source`, whose revision is the digest of the
source bytes. `outline`, `copper_layers`, `nets`, `constraints`, `constraint_digest`, `footprints`,
`pads`, `vias`, `segments`, `arcs`, `zones` and `keepouts` are equal to the nanometre.

Lock is the one axis where a group *is* asymmetric, and the two errors are not comparable: refusing
a locked group costs one board an inspection, while reading one past authorizes a physical move the
designer forbade. So it refuses.

**The equality proof could not have found the lock defect, and that is worth recording.** It
compares two outputs of the *same reader* — one with the group expressions present, one with them
deleted — and that reader never reads group lock. When the group is locked, both sides are
identically wrong, and their equality is preserved exactly because the constraint is being dropped
on both. A constraint that lives in the consuming application's runtime derivation rather than in
the members' own serialized fields is invisible to any differential over conversions, and can only
be found by reading the consumer's model. That is the general lesson: a with/without equality
bounds what an adapter *adds*, never what it *fails to read*.

**No content address moves.** A board with no group converts identically, and a board with a group
previously produced no snapshot at all, so there is nothing whose digest could change. No Board IR
schema version, field or golden identity is affected: `ConversionResult` is an adapter result, not
part of the canonical content that is digested.

**Write-back stays open for grouped boards, and that was verified rather than assumed.** Issue #129
named refusing write-back on any grouped board as the conservative fallback. The placement patch
path splices only the moved footprint's `at` expression and its absolute pad angles and carries
every other byte through unchanged, and a member's UUID does not change when the member moves. A
regression renders a real move over a fixture carrying a group and asserts the group's bytes, and
the whole document tail from the group onward, are byte-identical.

**The residual is a modelling gap, and it is stated.** Board IR still has no field meaning "these
objects belong together", so a caller can move one member of a group and break an intent nothing
told it about. The count makes that discoverable rather than invisible; it does not repair it.
R-134 records it.

**A refusal for a genuinely unknown head still does not name it.** This is a deliberate divergence
from issue #129's first suggestion, and it costs something: an operator meeting an undocumented root
head learns its position but not its spelling. The alternative costs more.

**A board that groups a locked selection now refuses where it used to refuse.** The outcome for
that board is unchanged — it did not convert before this decision and does not convert after — but
the reason it gives is now the true one, and named.

**The closed-shape check is one level deep.** It constrains which child heads may appear inside a
group, not what nests inside them, so an arbitrary s-expression can sit unread inside `(members
…)`. Nothing there can change modelled geometry or connectivity, because no group child is read for
either, and the one child that carries a constraint — `locked` — is read rather than allowlisted
through. Deepening it would buy nothing this decision depends on; the wording elsewhere says "the
head vocabulary the writer emits" rather than "the writer's grammar" so the claim matches the check.

## Alternatives considered

**Interpolate the rejected head into the message, as issue #129 proposed.** Rejected. A head is
board bytes, and the repository already has a passing regression that forbids exactly this. The
indexed locator recovers most of the actionability without the echo.

**Refuse the group but name it.** Rejected. It leaves a whole ordinary board unconvertible for three
editor selections that carry no copper, and the format defines `group` as a normal board section.

**Accept the group with no shape check, on the strength of its head alone.** Rejected. The
acceptance argument is conditional on what a group contains; a group carrying an unread child is not
covered by it. Each condition is checked and refuses, exactly as D-163 did for aperture pads. The
locked-group case is the proof that this mattered: the head alone would have said "inert".

**Propagate a locked group's lock to its member footprints by uuid, instead of refusing.** Rejected,
and it is the stronger alternative — it would convert strictly more boards. Three things would have
to be proved first, and none is free. (1) **Resolution must be total in the safe direction.** A
member uuid that does not resolve to a converted footprint must not silently become "not locked",
so every unresolved member would itself have to refuse — which returns most of the refusals this
alternative exists to avoid. (2) **Board IR has nowhere to put most of it.** `locked` exists on a
footprint and nowhere else, so a group holding segments, vias, zones or an outline contour would
have its lock modelled for the footprints and dropped for everything else: partially honoured, which
is the precise shape of failure being avoided. (3) **Lock is transitive through nested groups**, so
the propagation is a closure over a member graph read from board-author text, not a single lookup.
Refusing costs nothing measurable — every group in the surveyed tree is unlocked — and it leaves the
door open: `test_a_locked_group_never_converts_a_member_footprint_as_unlocked` is written over the
member's own `locked` flag precisely so that a future propagation must make it hold rather than
delete it.

**Model the grouping in Board IR.** Rejected for this change, not forever. A membership relation is
a schema addition with its own identity, ordering and patch-path consequences, and nothing in the
routing or placement surfaces consumes it yet. Recording the count is the honest interim: it neither
fabricates a claim nor hides the gap.

**Refuse write-back on any board carrying a group**, issue #129's conservative fallback. Rejected
because the byte-preservation it worried about is demonstrable, and refusing on an unverified worry
would have withheld a capability the evidence supports.
