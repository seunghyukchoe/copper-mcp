# ADR-0025: Apply a route candidate by splicing bytes, not by rewriting a board

- Status: Accepted
- Date: 2026-08-04
- Owners: `@seunghyukchoe`
- Related: ADR-0005, ADR-0007, ADR-0008

## Context

Every candidate this project produces has been immutable and advisory. M3 is where one of them
is allowed to change a file the user cares about — which makes the interesting question not
"how do we write the board?" but "what may we destroy while doing it?"

A `.kicad_pcb` is a hand-editable, comment-free but formatting-rich S-expression document.
Reserialising it from Board IR would be catastrophic: Board IR deliberately models a *subset*,
so a round trip through it would silently drop every construct outside that subset. The edit has
to be surgical, and everything not part of the patch has to come back bit-identical.

This ADR covers what is implemented now — the span-splice CST and the pure apply engine. The
mutating path is designed in the same slice's report but deliberately not built yet.

## Decision

### The CST reuses the existing parser rather than adding a second one

`parse_sexpr` already records where each expression starts (`SExpr.offset`), enforces the
budget ceilings, and rejects invalid UTF-8. What it does not record is where an expression
*ends*. `adapters/cst.py` supplies that, plus the splice built on it.

**Offsets are character indices, not byte offsets.** `parse_sexpr` decodes strictly before
tokenising, so its offsets count characters. Both of this repository's reference boards contain
multi-byte characters — an em-dash in CopperTone (166,070 bytes against 166,068 characters) and
a `µ` in the Board IR subset fixture — so treating one as the other would corrupt precisely the
boards we test against.

Working in characters is nonetheless byte-exact, because strict UTF-8 decoding is injective:
`source.decode("utf-8").encode("utf-8") == source`, verified on all 26 committed boards. The
discipline is therefore *decode once, splice in characters, encode once*, and `splice_source`
exists so callers never hold the two domains apart themselves.

A parallel byte-level tokeniser was rejected: it would duplicate the security-critical budget
enforcement, giving those ceilings a second place to drift, and it would buy nothing that the
round-trip property does not already provide.

**Splices apply from the highest offset downwards**, so each one's coordinates still describe
the original text when its turn comes. **Overlapping splices are refused outright** rather than
resolved — last-wins, longest-wins and merge are all silent guesses about intent, and two
insertions at the same point are ambiguous in ordering for the same reason.

`kicad_file.py` is untouched and is not the reuse candidate: it is a regex metadata scraper for
the shallow inspection manifest, with no tree and no offsets.

### Route patches are inserted at the root's closing delimiter

Measured, not assumed. After a real `kicad-cli --save-board`, CopperTone's root children run
`segment×11, via×6, segment×10, via×2, segment×12, via×1, segment×20, zone×4, embedded_fonts` —
**KiCad has no "segment section"**; its writer interleaves tracks and vias. There is therefore
no conventional insertion point to match, and "after the last segment" would match nothing.

Appending before the root close was tested directly: KiCad reads the added segment, reports
**0 DRC violations and 0 unconnected items**, and *preserves* the trailing position across its
own save. It is also the only position that modifies no existing span at all, which is what
makes "every untouched byte is identical" trivially total rather than an assertion over many
edited regions. On CopperTone it leaves 99.999% of the file before the splice and 2 bytes after
it untouched.

### The three-part assertion

1. **Every untouched byte is identical**, checked in bytes rather than characters by encoding
   the prefix and suffix and comparing them against the source directly.
2. **The result reparses through the fail-closed adapter** with no diagnostics.
3. **The resulting Board IR equals the source IR plus the candidate exactly** — same objects
   everywhere else, only the source revision moved.

### An applied board is not stamped as ours

`render_kicad_candidate_board` rewrites `generator` to `copper-mcp`, which is right: that board
is a disposable derivative we authored. The applied board is the opposite — the user's file,
authored by KiCad, to which we are adding tracks. Stamping it would both misattribute authorship
and break assertion 1. The engine therefore does **not** call `_rewrite_writer_metadata`.

### A candidate is never trusted from its manifest

`candidate_from_dict` accepts a manifest at face value and cannot detect a forged identity, so
the engine does not use it. It recomputes the identity with `verify_candidate_id` and replays
the geometry against the board with `_replay_candidate` before a single byte is spliced. A
tampered candidate whose digest has been recomputed to match its own contents still fails
replay.

## Consequences

- The splice module has a real caller on day one: `_rewrite_writer_metadata` was refactored onto
  it, and the existing candidate-DRC suite is the regression proof. ADR-0007's replay path now
  shares the same span machinery.
- The engine is pure. It returns the bytes an apply *would* write plus the evidence that they
  are correct, and there is deliberately no function in `copper_mcp.apply` that touches a
  filesystem. Verified against real KiCad: the applied board opens, the previously unconnected
  net becomes connected, no DRC error is introduced, and KiCad keeps the added segments when it
  rewrites the board itself.
- `ApplyVerification` records `kicad_opened_board` and `drc_after_apply` as one-value
  `not_run` literals. This engine never runs KiCad, so there is no value in which it could
  claim otherwise — the same device as the scene's `trust` field and placement's
  `courtyard_overlap`.
- Placement apply stays deferred behind footprint-modelling Board IR 0.2, and is sequenced after
  route apply so M3 does not stall on an M4 dependency. A pose edit is not additive, so the
  assertion above would not be total for it.

## Designed but not yet shipped

Stated so nothing here is mistaken for a capability:

- **No mutating path.** No lockfile refusal, no compare-and-swap, no pre-apply copy, no atomic
  replacement. Nothing writes.
- **No authorization tokens.** The operator opt-in flag and the single-use HMAC apply token
  bound to `(candidate_id, base_revision, relative_path)` are designed and not implemented.
- **No `apply_candidate` tool or CLI command.**
- **No merge, no lock override, no IPC apply, no placement apply, no batch apply.** These are
  non-goals for v0.1, not omissions. IPC in particular is excluded on a technical ground rather
  than an effort one: it mutates an in-memory document whose state cannot be bound to a file
  digest, so revision binding would be unsound there.

## Prior art

The lossless-CST / span-splice pattern is the standard approach for partial edits that preserve
untouched bytes — Oil Shell's lossless syntax tree, and the rowan/cstree red-green trees. The
essential property is the same in all of them: the tree is a view over the source, not a
replacement for it, so anything the tree does not model still survives an edit.

`sexpdata` was considered and rejected: it is not formatting-preserving, which disqualifies it
for apply regardless of its licence.

## Alternatives considered

- **Reserialise the board from Board IR**: rejected outright. Board IR models a documented
  subset, so every unmodelled construct would vanish silently.
- **A parallel byte-level tokeniser**: rejected. It duplicates budget enforcement and the
  round-trip property already makes character-domain splicing byte-exact.
- **Insert after the last existing segment**: rejected on measurement — there is no segment
  section to insert after, and any interior insertion point would put existing spans at risk.
- **Resolve overlapping splices instead of refusing them**: rejected. Every resolution rule is a
  guess about intent that the caller never stated.
- **Stamp the applied board with our writer id**: rejected. It is not our board.

## References

- [ADR-0005](0005-canonical-board-ir.md)
- [ADR-0007](0007-disposable-kicad-candidate-snapshot.md)
- [Safe candidate application references](../research/safe-apply-references.md)
