# KiCad board groups: what a root `(group …)` is, and what it is not

Research date: 2026-08-08. This note supports
[issue #129](https://github.com/seunghyukchoe/copper-mcp/issues/129), decision
[D-177](../ledgers/decision-ledger.md), risk [R-134](../ledgers/risk-register.md), security review
[SEC-133](../ledgers/security-ledger.md) and [ADR-0090](../adr/0090-root-level-board-groups.md).
It reconciles with [`B-096`](../ledgers/benchmark-ledger.md) and
[`tier2-real-board-capability-v1.md`](tier2-real-board-capability-v1.md), which measured this
refusal on a live board a day earlier; see the reconciliation in section 1.
No board content from the surveyed working tree is reproduced here: the construct is described by
its format definition and by KiCad's own model of it, and the fixtures in
`tests/test_kicad_board_ir.py` are authored from those definitions.

## 1 — The measurement

Read-only survey of `~/Desktop/13_Audio/projects/**/*.kicad_pcb`, the same local working tree the
[assembled-outline note](assembled-outline-identity-v1.md) surveys, plus the two `pcbnew` backup
files one project directory carries. Every direct child of each `(kicad_pcb …)` root, counted by
head:

| Root head | Occurrences | In the adapter's root allowlist? |
|---|---:|---|
| `segment` | 122,316 | yes (`_ROOT_ROUTING_HEADS`) |
| `via` | 4,644 | yes |
| `footprint` | 2,037 | yes |
| `gr_line` | 68 | yes (`gr_*` branch) |
| `gr_text` | 66 | yes (`gr_*` branch) |
| `zone` | 58 | yes |
| `version`, `setup`, `paper`, `layers`, `generator_version`, `generator`, `general`, `embedded_fonts` | 17 each | yes (`_ROOT_METADATA_HEADS`) |
| **`group`** | **6** | **no — this was the whole refusal** |

`group` is the only head in the entire tree outside the allowlist, and it occurs on one document
lineage: three groups in each of two `pcbnew` backups of the same mixer board. Converting that
document reports, verbatim:

```text
code:    unsupported.construct
message: root expression contains an unsupported semantic construct
locator: kicad_pcb.unsupported
```

The document is an ordinary KiCad 10 board — `(version 20260206)`, `(generator "pcbnew")`,
`(generator_version "10.0")` — carrying 103 footprints, 349 pads and 4 filled zones, all of which
convert once the three group expressions are read past.

### Reconciling this with B-096, which measured the same board differently

[`B-096`](../ledgers/benchmark-ledger.md) and
[`tier2-real-board-capability-v1.md`](tier2-real-board-capability-v1.md) record `cue` as **1 of 12**
corpus boards refused for exactly this construct, on a corpus explicitly excluding `.history/` and
derived stems. That measurement is not wrong, and this note does not correct it. Both statements are
true, of different moments:

| | `cue.kicad_pcb` | `cue.kicad_pcb.bak-2253` |
|---|---|---|
| Last written | 2026-08-08 02:51 | 2026-08-07 22:53 |
| Root `(group …)` | 0 | 3 |
| Converts | yes | refused, before this change |

B-096 ran on 2026-08-07, when the then-current save carried the three groups. The designer re-saved
the board on 2026-08-08 without them, and only the backup preserves the earlier state. So B-096
measured a live blocker, and by the time this change was written the blocker had moved on its own.

**As of this note, no currently-saved board in the tree is unblocked by this change.** Every
currently-saved `.kicad_pcb` has zero `(group` occurrences; the only two documents carrying one are
`.kicad_pcb.bak-2253` and `.kicad_pcb.bak-preresize`. The change is therefore recorded as
pre-emptive hardening against a construct KiCad writes whenever a designer groups a selection —
which is what B-096's own board demonstrates is an ordinary thing for a designer to do and then
undo. Claiming a live unblock today would repeat the measurement error #116 published a correction
for: reading a stale or derived file as the state of the working tree.

With that qualifier in place, the #116 line "the twelve boards carry no root-level child outside
the existing allowlist at all" is corrected: the tree did carry one when B-096 ran. The earlier
sentence is left as written; this note is the correction.

## 2 — What the format says a group is

`group` is one of the root sections the KiCad board file format enumerates. The document's Layout
section lists them as Header, General, Layers, Setup, Properties, Nets, Footprints, Graphic Items,
Images, Tracks, Zones and Groups
([Board File Format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html)). It is a
first-class section of an ordinary board, not an anomaly, and the board format document defers its
grammar to the common definitions:

```text
(group
  "NAME"
  (id UUID)
  (members UUID1 ... UUIDN)
)
```

with the accompanying sentence "The `group` token defines a group of items"
([S-Expression Format, common definitions](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)).
The published grammar predates the `id` → `uuid` rename and omits the two optional flags; the
writer is authoritative for what actually reaches a file. `PCB_IO_KICAD_SEXPR::format( const
PCB_GROUP* )` emits, in order: the quoted group name, the group's `uuid`, `locked` when the group
is locked, `lib_id` when the group carries a design-block link, and the `members` list
([pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp)).
That is the complete child vocabulary, and it is what the adapter's `_ROOT_GROUP_HEADS` allowlist
contains.

## 3 — Whether it carries geometry or connectivity

It does not, and KiCad says so in its own model rather than by inference. From
[pcbnew/pcb_group.h](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_group.h):

- The class comment: "A set of BOARD_ITEMs (i.e., without duplicates). The group parent is always
  board, not logical parent group. The group is transparent container - e.g., its position is
  derived from the position of its members."
- `IsOnCopperLayer()` returns false, with the comment "A group might have members on a copper
  layer, but isn't itself on any layer."
- `SetLayer()` is a no-op. `GetPosition()` and `GetBoundingBox()` are derived from the members.
- No net member, no net accessor, no clearance or design-rule field.

Three facts follow, and together they are the *geometric* half of the acceptance argument:

1. **A group owns no coordinate.** Every coordinate it appears to have is read from a member.
2. **A group owns no layer.** It is layer-agnostic by construction, and explicitly not on copper.
3. **A group owns no member.** Membership is by UUID reference, and every member is a board object
   the adapter already converts at the root on its own terms.

So the set of copper, the board outline and the nets a document contains is the same set whether
the group expressions are read or ignored.

That is not the same as "a group carries no constraint", and section 3a is where the difference
lives. This header file is the whole of what a group *is*; it is not the whole of what KiCad
*derives* from one.

## 3a — Where that stops being true: a locked group

`pcb_group.h` is not the whole model, and section 3 above would be wrong if it were read as the
whole model. `BOARD_ITEM::IsLocked()` in
[pcbnew/board_item.cpp](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/board_item.cpp)
opens:

```cpp
bool BOARD_ITEM::IsLocked() const
{
    if( EDA_GROUP* group = GetParentGroup() )
    {
        if( group->AsEdaItem()->IsLocked() )
            return true;
    }
    ...
    return m_isLocked;
}
```

A locked group therefore makes **every member locked**, and transitively so, since the group's own
`IsLocked()` resolves through the same derivation for a nested parent. The constraint exists
nowhere in the member's serialized form: a footprint inside a locked group writes no `(locked yes)`
of its own.

That matters here because lock is not advisory in CopperMCP. It is an authorization gate in three
places:

| Surface | Behaviour on a locked footprint |
|---|---|
| `placement/solver.py` | will not resolve it as a placement subject |
| `placement/legalizer.py` | raises "moving a locked footprint is not authorized" |
| `adapters/kicad_placement_patch.py` | refuses "locked footprint movement is unsupported" |

Reading a locked group past would present its members at `locked=False` and let all three
authorize a move KiCad forbids. So a locked group is refused, with `a locked group locks its
members and is unsupported`. Every group in the surveyed tree is unlocked, so the refusal costs the
corpus nothing.

**Propagating the lock to members by uuid was considered and rejected**, and it is the stronger
alternative — it converts strictly more boards. Three obstacles, none free:

1. **Resolution must be total in the safe direction.** A member uuid that does not resolve to a
   converted footprint must not become "not locked" by default, so each unresolved member would
   have to refuse — recovering most of the refusals the alternative exists to avoid.
2. **Board IR has nowhere to put most of it.** `locked` exists on a footprint and on nothing else,
   so a locked group holding segments, vias, zones or an outline contour would be honoured for its
   footprints and silently dropped for the rest. Partially honouring a constraint is the exact
   shape of failure this refusal exists to prevent.
3. **The closure is transitive**, over a member graph read from board-author text.

The door is left open deliberately: `test_a_locked_group_never_converts_a_member_footprint_as_unlocked`
is written over the *member's* `locked` flag rather than over the group, so a future propagation
must make it hold rather than delete it.

## 4 — The direction-of-error argument, measured rather than asserted

CopperMCP's standing invariant is that obstacles may only be over-approximated and that
connectivity and the board outline may only be under-approximated. A group is not on either side of
that rule, because it contributes no quantity to round in either direction. That is a claim, so it
is measured:

Converting the real 769,739-byte board with its three `(group …)` expressions, and converting the
same bytes with only those three expressions deleted, produces Board IR content that differs in
**exactly one field**: `source`, whose `revision` is the SHA-256 of the source bytes and must move
when 3,141 bytes are removed. `outline`, `copper_layers`, `nets`, `constraints`,
`constraint_digest`, `footprints`, `pads`, `vias`, `segments`, `arcs`, `zones` and `keepouts` are
equal — every one of them, to the nanometre, by dataclass equality over exact integers.

The regression that pins this is
`test_a_root_group_is_editor_organisation_and_moves_no_geometry`, which asserts the same field
equality on a fixture rather than on the private board.

**And here is what that equality cannot prove.** It compares two outputs of the *same reader* — one
with the group expressions present, one with them deleted — and that reader never reads group lock.
On a board whose group is locked, both sides are identically wrong, and the equality holds
*because* the constraint is dropped on both. A differential over conversions bounds what an adapter
**adds**; it says nothing about what the adapter **fails to read**. The lock defect in section 3a
was found by reading KiCad's consuming model, not by any measurement over outputs, and that is the
transferable lesson from this note: when a construct's meaning lives in the consumer's runtime
derivation rather than in the serialized fields, no equality between conversions will surface it.

## 5 — The write side, verified rather than assumed

Issue #129 left this open and named the conservative fallback: if a group cannot be shown to
survive a write-back, accept the board for inspection and refuse to write any board carrying a
group. The verification succeeded, so the fallback is not needed.

`render_kicad_placement_candidate_board` parses the source, computes splices over the footprint
`at` expression and the absolute pad angles of the single moved footprint, and applies them to the
original text with `apply_splices`. Every byte outside a spliced span is carried through unchanged,
and a group is never inside one. Membership is by UUID, and a member's UUID does not change when
the member moves, so the group's meaning is also unchanged.

`test_a_placement_splice_leaves_a_root_group_byte_identical` renders a real placement move over a
fixture carrying a group and asserts that the group's bytes, and the whole tail of the document
from the group onward, are byte-identical to the source. If a future splice ever rewrites the tail
of a document, that test is what fails.

## 6 — What is deliberately not claimed

**No claim about locked groups other than that they refuse.** Nothing here establishes how a locked
group *should* be modelled; section 3a records why the propagation was not attempted.

**The closed-shape check is one level deep.** It constrains which child heads may appear inside a
group, not what nests inside them, so an arbitrary s-expression can sit unread inside `(members …)`
and a group with neither `uuid` nor `members` is accepted. None of that can change modelled
geometry or connectivity, because no group child is read for either; the one child that carries a
constraint, `locked`, is read rather than allowlisted through. The wording throughout is therefore
"the head vocabulary the writer emits", not "the writer's grammar".

**The grouping itself is not modelled.** Board IR has no field meaning "these objects belong
together", and this change does not add one. A caller that moves one member of a group the designer
meant to keep together breaks an intent nothing told it about. That is a modelling gap, not a
geometry gap, so it is recorded rather than approximated: `ConversionResult.unmodelled_group_count`
carries the number of accepted-and-unmodelled groups, following the pattern D-157 established for
the roundrect rounding residue. It is a count and not a diagnostic for the reason D-157 gives —
every caller of `parse_kicad_bytes` treats a non-empty `diagnostics` tuple as a refusal, so a
warning would refuse the board the change exists to admit. R-134 records the residual.

**Footprint-level groups are not in scope.** The footprint allowlist stays closed, and no surveyed
board carries a group inside a footprint.

**A group's `uuid` is not a Board IR identity.** It is never registered in the adapter's native
identity counter, so a group can neither claim an identity nor push another object onto the
revision-derived fallback.

## 7 — Naming the refusal without echoing the board

Issue #129's first suggestion was to interpolate the rejected head into the refusal message, on the
grounds that "a KiCad format head is a fixed vocabulary term chosen by the format, not board
content". That premise holds for a head the format defines and fails for one it does not: an
arbitrary document can carry an arbitrary head, and this repository's standing invariant is that
the board's own text is untrusted data that never reaches an instruction-bearing field. The
existing regression `test_diagnostics_never_echo_attacker_controlled_construct_names` already
refuses exactly that change — one of its four cases is a root-level head named
`SECRET_BEARER_TOKEN`.

The change reconciles the two:

- **The locator names the position.** `kicad_pcb.child[7]` replaces the constant
  `kicad_pcb.unsupported`. The index is computed from the parse, never read from the document, so
  it is actionable and echoes nothing. An operator can open the file and count to the child.
- **The message names the construct when the format defines it.** `_UNMODELLED_ROOT_HEADS` maps a
  documented root section to a fixed refusal sentence. The sentence emitted is a *value from that
  table*, selected by an equality test against the source token and never built from it — so the
  bytes that reach the caller are the adapter's, not the board's. The table is deliberately
  partial: an entry is added when the construct is cited, not guessed.
- **An undocumented head is refused without being named.** The generic sentence is unchanged, and
  the indexed locator still says where the construct sits.

## Sources

- [Board File Format — KiCad Developer Documentation](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/index.html)
- [S-Expression Format, common definitions — KiCad Developer Documentation](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/index.html)
- [pcbnew/pcb_group.h — KiCad source](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_group.h)
- [pcbnew/board_item.cpp — KiCad source](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/board_item.cpp)
- [pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp — KiCad source](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp)
