# KiCad copper layer numbering

Research date: 2026-08-06.  This note supports the correction recorded as
[D-152](../ledgers/decision-ledger.md) and [R-115](../ledgers/risk-register.md), and grounds the
copper-stack validation in `src/copper_mcp/adapters/kicad_board_ir.py`.  No external code is
copied; the quoted fragments below are short excerpts from KiCad's published API documentation,
cited so the arithmetic can be re-derived rather than trusted.

It answers exactly one question — **what integer does KiCad write next to each copper layer name
in a `.kicad_pcb` file, and in what order does it write them** — and refuses to claim anything
about non-copper layers, about the pre-4.0 legacy board format, or about KiCad 11.

## The question, and why inference from one board was not enough

CopperMCP's Board IR adapter validated the copper stack by requiring the file's layer ordinal to
equal `declaration_position * 2`, i.e. `F.Cu=0, In1.Cu=2, In2.Cu=4, B.Cu=6` for a four-layer
board.  That rule is satisfied *coincidentally* by every two-layer board, which is all the
repository's fixtures contained, so it survived until a real four-layer board was converted
(issue #104).

A single counter-example board tells you the old rule is wrong.  It does not tell you what the
right rule is, and in particular it cannot tell you whether the numbering is stable across the
KiCad versions this project may be asked to read.  Both facts are established below from KiCad's
own sources.

## Finding 1 — the format defines the ordinal but not its values

The board S-expression specification defines each entry of the board `(layers …)` block as:

```
(layers
  (
    ORDINAL
    "CANONICAL_NAME"
    TYPE
    ["USER_NAME"]
  )
  ;; remaining layers...
)
```

`ORDINAL` is described as the layer stack position number, present "mostly to ensure correct
mapping when the number of layers is increased in the future"; `CANONICAL_NAME` is the internal
name (`F.Cu`, `In1.Cu` … `In30.Cu`, `B.Cu`), and `USER_NAME` is a display-only alias.  Source:
[KiCad S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/) and
[S-expression introduction](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/), the latter
carrying the canonical-name table and the statement that user-defined layer names are used only
for display and output.

The specification deliberately does **not** enumerate the ordinal values.  Anyone validating them
has to read them out of the implementation, which is what the next two findings do.  This is the
first load-bearing negative in this note: a reader who stops at the file-format page has no basis
for *any* arithmetic rule, including the one this project shipped.

## Finding 2 — the current numbering is F.Cu=0, B.Cu=2, In*N*.Cu=2+2*N*

KiCad's `PCB_LAYER_ID` enumeration assigns copper layers the even values and interleaves the
technical layers on the odd ones:

```
F_Cu  = 0,
B_Cu  = 2,
In1_Cu = 4,
In2_Cu = 6,
...
In30_Cu = 62,
```

Source: [`layer_ids.h`](https://docs.kicad.org/doxygen/layer__ids_8h_source.html).

The same numbering is confirmed independently by the name-formatting routine, which recovers the
inner-layer number by halving the offset from `In1_Cu`:

```c
case PCB_LAYER_ID::F_Cu: return wxT( "F.Cu" );
case PCB_LAYER_ID::B_Cu: return wxT( "B.Cu" );
...
return wxString::Format( wxT( "In%d.Cu" ), ( aLayer - PCB_LAYER_ID::In1_Cu ) / 2 + 1 );
```

Source: [`layer_id.cpp`](https://docs.kicad.org/doxygen/layer__id_8cpp_source.html).

So `In1.Cu = 4` and, in closed form, **In*N*.Cu = 4 + 2(*N*−1) = 2 + 2*N*** for 1 ≤ *N* ≤ 30, with
`F.Cu = 0` and `B.Cu = 2`.  Note the consequence that makes the old rule look plausible: on a
two-layer board the only copper layers are `F.Cu=0` and `B.Cu=2`, which is *also* what
`position * 2` produces.  The two rules agree on exactly the boards CopperMCP had fixtures for and
disagree on every other board.

## Finding 3 — the numbering *did* change, at KiCad 9, and the old scheme is a different rule

The pre-V9 enumeration numbered copper layers consecutively with the back layer last:
`F_Cu = 0`, `In1_Cu … In30_Cu = 1 … 30`, `B_Cu = 31`.  KiCad carries an explicit converter from
those values, used by its V9 board migration:

```c
case 0:  return F_Cu;
case 31: return B_Cu;
...
if( aLegacyId < 31 )
    return static_cast<PCB_LAYER_ID>( In1_Cu + ( aLegacyId - 1 ) * 2 );
```

Source: [`layer_id.cpp`](https://docs.kicad.org/doxygen/layer__id_8cpp_source.html), declared in
[`layer_ids.h`](https://docs.kicad.org/doxygen/layer__ids_8h_source.html) as
`PCB_LAYER_ID BoardLayerFromLegacyId( int aLegacyId );` and documented there as retrieving a layer
ID from an integer converted from a legacy (pre-V9) enum value.

This is the second load-bearing fact, and it is the reason this note exists rather than a one-line
constant.  **There is no single copper numbering that is correct for all KiCad versions.**  A
four-layer board written by KiCad 7 or 8 reads `(0 "F.Cu") (1 "In1.Cu") (2 "In2.Cu") (31 "B.Cu")`;
the same board written by KiCad 9 or 10 reads `(0 "F.Cu") (4 "In1.Cu") (6 "In2.Cu") (2 "B.Cu")`.
Under the current numbering, ordinal `1` is `F.Mask` and ordinal `31` is a technical layer — so a
validator that accepted both schemes at once would accept boards whose copper stack it had
misread.

CopperMCP does not have to choose between them, because the choice is already made upstream: the
adapter accepts exactly one board format version (`_SUPPORTED_KICAD_PCB_VERSIONS = {"20260206"}`)
and refuses everything else with a typed `unsupported.version` diagnostic **before** the layer
block is examined.  That version is a KiCad 10 format, so only Finding 2's numbering can reach the
stack validator.  The legacy scheme is recorded here so that the day the supported-version set
grows, the person widening it sees that widening it past V9 is not a version-string edit but a
second numbering rule — and the adapter comment points at this note for that reason.

## Finding 4 — declaration order is the physical stack, not the numeric order

The writer emits copper layers by iterating the enabled copper stack:

```cpp
for( PCB_LAYER_ID layer : aBoard->GetEnabledLayers().CuStack() )
    m_out->Print( "(%d %s %s %s)", layer, ... LSET::Name( layer ) ... );
```

Source: [`pcb_io_kicad_sexpr.cpp`](https://docs.kicad.org/doxygen/pcb__io__kicad__sexpr_8cpp_source.html),
`PCB_IO_KICAD_SEXPR::formatBoardLayers`.  `CuStack()` is front-to-back physical order, so the
emitted sequence is `F.Cu, In1.Cu, …, In`*N*`.Cu, B.Cu` — which is **not** ascending ordinal order
(`0, 4, 6, 2` for four layers).

The two orderings must therefore be validated as separate invariants, and conflating them is what
produced the defect:

- **Declaration order** (positional): entry 0 is `F.Cu`, entry *k* for 1 ≤ *k* ≤ *n*−2 is
  `In`*k*`.Cu`, and the last entry is `B.Cu`.
- **Numeric ordinal** (per entry, independent of position): `F.Cu → 0`, `B.Cu → 2`,
  `In`*N*`.Cu → 2+2N`.

A four-layer board satisfies both simultaneously and satisfies neither `position * 2` nor
"ordinals ascend".

## What this means for the adapter

The stack validator checks the name against the declaration position, the ordinal against the
name, and rejects duplicate ordinals and duplicate names.  Concretely it still refuses, with the
existing typed `unsupported.construct` diagnostic:

- a stack declared in numeric order (`F.Cu, B.Cu, In1.Cu, …`) — declaration order is not physical;
- inner layers numbered under the old CopperMCP rule (`In1.Cu = 2`) — that is `B.Cu`'s ordinal;
- inner layers numbered under the pre-V9 KiCad rule (`In1.Cu = 1`, `B.Cu = 31`) — a real numbering,
  but not this format version's, and the refusal names the version constraint rather than guessing;
- a skipped inner index (`In1.Cu` then `In3.Cu`), a misnamed position, a back layer that is not
  `B.Cu`, a missing front or back copper layer, or two entries sharing an ordinal.

`Layer.index` in Board IR stays the **declaration position**, not the KiCad ordinal, so the IR
continues to expose a dense 0…*n*−1 front-to-back stack and no two-layer content address moves.

## Refusals

This note does not claim anything about: the ordinals of non-copper layers beyond the parenthetical
observations above; the pre-4.0 legacy board format documented separately at
[legacy PCB format](https://dev-docs.kicad.org/en/file-formats/legacy-pcb/); jumper or user copper
types; boards with more than 30 inner copper layers; or any numbering KiCad 11 may adopt.
