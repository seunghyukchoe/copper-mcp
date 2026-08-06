# KiCad UUID uniqueness, and what a board file actually guarantees

Research date: 2026-08-07.  This note supports the decision recorded as
[D-158](../ledgers/decision-ledger.md) and the residual risk
[R-119](../ledgers/risk-register.md), and grounds the identity derivation in
`src/copper_mcp/adapters/kicad_board_ir.py`.  No external code is copied; the quoted fragments
below are short excerpts from KiCad's published documentation and issue tracker, cited so the
claim can be re-checked rather than trusted.

It answers exactly one question — **may a `.kicad_pcb` file name two different objects with the
same `uuid`, and if so, what may a reader conclude from a `uuid`** — and refuses to claim anything
about schematic UUIDs, about the `path` field that links a footprint to a schematic symbol, or
about whether any particular board's duplicates were authored by KiCad or by a third-party tool.

## The defect this note explains

The Board IR adapter projected a KiCad `uuid` (or its legacy `tstamp` spelling) directly onto a
Board IR geometry identity: `footprint:kicad:<uuid>`, `pad:kicad:<uuid>`, and so on.  Board IR
requires geometry identities to be unique — footprints own pads by ID, and every patch names its
target by ID — so `validate_content` refused any content in which two objects shared one.

Real boards tripped it.  Issue #116's survey recorded one board refused with the generic
`converted Board IR content failed semantic validation`; instrumenting that refusal showed the
underlying rule was `identity.duplicate` on `geometry ID`.  A structural survey of the same
twelve-board tree found the same reuse on **nine of twelve boards**, always and only on footprints
and their pads, never on segments, arcs, vias, or zones.  On one board, 113 footprints carried
just 11 distinct UUIDs, and the correspondence was exact: every instance of a given footprint
*type* shared one UUID, with 45 distinct resistors — distinct reference designators, distinct
positions — all named `2927ef2a…`.  Their pads collided the same way, because the whole footprint
block is repeated verbatim apart from pose and reference.

So on these boards a `uuid` names a footprint **type**, not a footprint **instance**.  That is the
false assumption, and it is the same shape as the one behind issue #104: a rule inferred from
fixtures written under the code's own beliefs, which every real board then contradicts.

## Finding 1 — the format says "should be", not "must be"

The board S-expression format defines the identity token as:

> "The UUID attribute is a Version 4 (random) UUID that should be globally unique. KiCad UUIDs are
> generated using the mt19937 Mersenne Twister algorithm."

Source: [KiCad S-expression introduction](https://dev-docs.kicad.org/en/file-formats/sexpr-intro/),
which also notes that files converted from pre-6.0 KiCad have their *locally* unique timestamps
re-encoded into UUID form — so not every value in the field is even drawn from the random
generator.

This is the load-bearing negative.  "Should be globally unique" states an expectation of the
writer.  It grants no permission to a **reader** to treat the field as a key, and the format
defines no uniqueness constraint a conforming file must satisfy.  A reader that keys on it is
relying on a property the format never promised.

## Finding 2 — KiCad's own workflows produce duplicates, and have for years

Two tracked defects show the expectation being violated by KiCad itself rather than by a
third-party writer:

- Copy-pasting a footprint on a board gives the pasted footprint a fresh UUID but leaves its
  **children** — the pads — carrying the originals', with the reported consequence that DRC
  "cannot correctly distinguish between the pads in the original footprint and the pasted
  footprint".  Reported against 8.0.6.  Source:
  [kicad#19052](https://gitlab.com/kicad/code/kicad/-/work_items/19052).
- Running *Update schematic from PCB* with "re-link footprints to schematic symbols" selected
  produced duplicate UUIDs for most footprints on the board.  Source:
  [kicad#8461](https://gitlab.com/kicad/code/kicad/-/issues/8461).

Neither report matches the observed pattern exactly — #19052 duplicates pads but not their
parents, and the surveyed boards duplicate both — and this note deliberately does not guess which
tool or workflow produced the boards in hand.  What the two reports establish is narrower and
sufficient: **duplicate UUIDs are a real, reproducible state of a real KiCad board**, not a
corruption that a reader may assume away.  The surveyed boards declare
`(generator "pcbnew") (generator_version "10.0")` and carry no `(path …)` fields at all, so
whatever wrote them, the duplicates survived in a file KiCad reads.

## What this means for the adapter

Board IR identity is a per-object invariant and stays one; `validate_content` still refuses
content in which two geometry objects share an ID, and this note is not an argument for relaxing
it.  The correction is upstream of the validator, in what the converter is entitled to project:

- A native KiCad identity used **once** in a file remains that object's Board IR identity, exactly
  as before.  No existing content address moves; no board in this repository reuses a UUID.
- A native identity used by **two or more** objects of one kind is not an identity of any of them.
  Every object sharing it degrades — together, symmetrically — to the existing revision-derived
  name `{kind}:derived:sha256(revision, kind, locator)`, which is unique by locator and stable for
  a given file revision.  Giving the first claimant the native name and derived names to the rest
  would assert an identity the file does not support.
- The measurement is exact rather than inferred: the converter assigns every identity, counts the
  reuse it actually produced, and re-runs the conversion once with that set.  A pre-pass over the
  parse tree would have had to re-derive which `zone` expression becomes a zone and which becomes
  a keepout, and being over-conservative there would move content addresses on boards that convert
  today.

The safety consequence is deliberate and is the reason this is not a silent repair.  A board that
names 45 resistors with one UUID cannot be **written back** by that UUID without risking the wrong
component, and every source-preserving patch path in CopperMCP already refuses a snapshot that
contains any `:derived:` geometry identity (ADR-0026, `_require_native_geometry_identities`).  So
this change unblocks *inspection* of such a board and leaves *mutation* of it refused, which is
the conservative direction.

## Refusals

This note does not claim: that KiCad guarantees UUID uniqueness anywhere (Finding 1 is the
opposite); which workflow produced the duplicates in the surveyed tree; anything about UUIDs in
`.kicad_sch` schematics, in `.kicad_mod` library files (the KiCad 10 library footprints checked
carry no `uuid` at all), or in the `path` field; that a derived identity is a substitute for a
native one for any write-back purpose; or that a future KiCad version will not start enforcing
uniqueness on load, which would make the fallback unreachable rather than wrong.
