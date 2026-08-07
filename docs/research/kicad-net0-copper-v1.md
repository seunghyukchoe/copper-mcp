# KiCad net 0: the "no net" that real copper is saved on

Research date: 2026-08-07.  This note supports
[ADR-0078](../adr/0078-netless-copper-as-obstacle.md) and
[issue #119](https://github.com/seunghyukchoe/copper-mcp/issues/119).  No external code is
copied.  KiCad source references are to `master` as read on the research date; the net-0
semantics described are stable across KiCad 5 through 10.

## Official source findings

**Net 0 is defined, by KiCad itself, as the net of items that are not connected.**
`pcbnew/netinfo.h` declares, on `NETINFO_LIST` (lines 223–235): a constant "that holds the
'unconnected net' number (typically 0)" with the comment "all items 'connected' to this net are
actually not connected items"; an `ORPHANED` constant "(typically -1)" used to force
re-initialization when `SetNetCode` is called on board-connected items; and a NETINFO_ITEM
"meaning that there was no net assigned for an item, as there was no board storing net list
available."  Source:
[netinfo.h](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/netinfo.h).
Net 0 is therefore a **first-class value KiCad assigns to real board objects**, not a file
corruption: a via placed without attached copper, a track whose netlist attachment was removed,
and stitching copper awaiting a zone refill all live on net 0.

**The file format's nets section reserves ordinal 0 for the empty name.**  The KiCad
S-expression board documentation defines the net section entries as `ORDINAL` plus `NET_NAME`,
and its complete board example begins the section with `(net 0 "")`.  The `net` token on
segments and vias "defines by the net ordinal number which net in the net section the segment
is part of."  Source:
[KiCad S-expression board format](https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/).

**Newer writers omit or empty the net rather than writing ordinal 0.**  In the current board
writer, a pad's net is written only when it exists: `pcb_io_kicad_sexpr.cpp` prints
`(net %s)` with `GetNetname()` guarded by `GetNetCode() > 0` (approx. line 3390), so the *name*
form, not the ordinal form, is what modern files carry.  Source:
[pcb_io_kicad_sexpr.cpp](https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp).
KiCad 10 (`generator_version "10.0"`, format `20260206`) writes track and via nets by quoted
name, and writes net 0 as the empty name `(net "")` — observed on every KiCad-10 board in the
12-board survey corpus below and in this repository's own
`tests/fixtures/kicad10-named-nets.kicad_pcb`.

**Community evidence that net-0 vias are a normal editing outcome, not an authoring error.**
KiCad's tracker discusses vias explicitly assigned "no net" (net 0) and how DRC should treat
them ([kicad#10539](https://gitlab.com/kicad/code/kicad/-/issues/10539)); stitching vias placed
free of tracks rely on the zone fill, and the fill's net attachment is computed by
connectivity at fill time rather than stored as the via's own net.

## What the 12-board survey actually contains

Read-only survey of the 12 real KiCad-10 project boards behind issue #116/#119 (excluding
`.history/` and derived `routed-source` / `best-board` / `-placed` stems), scanning every
`(via ...)` and `(net ...)` reference in the raw files:

- **7 of 12 boards carry net-0 copper**, written exclusively as `(net "")`.  No board in the
  corpus uses the numeric `(net 0)` form on a copper item, and none omits the token entirely.
- Net-0 **vias**: 115 across the corpus (per board: 4, 2, 30, 2, 28, 20, 29).  Consistent
  with hand-placed stitching/spare vias over ground pours.
- Net-0 **segments**: 2,687 across the same 7 boards (347, 73, 788, 121, 472, 360, 526) —
  on every affected board, netless *tracks* outnumber netless vias by roughly an order of
  magnitude.  These are orphaned tracks: copper KiCad kept but whose netlist attachment is
  gone.
- Net-0 **arcs**: none observed, but the arc grammar carries the same `net` token, so the same
  spelling can appear on arcs.

Two consequences follow.  First, the refusal `via has no routable net` was only the *visible*
tip: any via-only fix would immediately re-refuse the same boards at
`segment has no routable net`, changing the message and unblocking nothing.  Second, the
copper is real — a net-0 via has a barrel and annulus, a net-0 track has width — so a model
that drops it under-approximates obstacles, which is the one direction the obstacle rule
forbids.

## The three saved spellings of "no net"

For one copper item, KiCad's "no net" has three source spellings, all of which must resolve
identically:

| Spelling | Producer |
|---|---|
| `(net "")` | KiCad 10 named-net writer (all corpus occurrences) |
| `(net 0)` | ordinal-form writers and older KiCad versions |
| `(net 0 "")` | two-field legacy form matching the root `(net 0 "")` declaration |

A **negative** ordinal (e.g. `(net -1)`) is *not* one of these: KiCad's `-1` is the in-memory
`ORPHANED` sentinel that the writer never saves, so its appearance in a file is malformation
and stays a typed `net.unknown` refusal.

## Sources

- KiCad S-expression board format, net section and track/via `net` token:
  <https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/>
- `NETINFO_LIST::UNCONNECTED` / `ORPHANED` semantics:
  <https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/netinfo.h>
- Board writer emitting nets by name, guarded on `GetNetCode() > 0`:
  <https://gitlab.com/kicad/code/kicad/-/blob/master/pcbnew/pcb_io/kicad_sexpr/pcb_io_kicad_sexpr.cpp>
- No-net via DRC discussion:
  <https://gitlab.com/kicad/code/kicad/-/issues/10539>
