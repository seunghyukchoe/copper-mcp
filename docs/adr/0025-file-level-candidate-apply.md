# ADR-0025: Apply a route candidate by splicing bytes, not by rewriting a board

- Status: Accepted (mutating path added 2026-08-04)
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

## The mutating path (shipped 2026-08-04)

The engine above computes bytes; this is what is allowed to write them. The order of the checks
is the design, and each one refuses rather than repairing.

- **Operator opt-in.** `COPPER_MCP_ALLOW_APPLY` must be exactly `"0"` or `"1"`, default off.
  Exact membership rather than truthiness: `bool("false")` is `True`, and a flag that enables
  board mutation must never be switched on by an ambiguous spelling.
- **A single-use token, enforced server-side.** Issued by `preview_route` only for a routed
  candidate, only when apply is enabled, and only for a board the append-only engine could
  actually apply to - a derived-identity board gets no token, because minting one for a board
  the apply path would reject was how an uncaught crash reached the destructive tool. Bound to
  `(candidate_id, base_revision, board_revision, relative_path)` under an HMAC whose key exists
  only in this process and verified with `compare_digest`; the expiry sits inside the MAC so
  editing it fails. Single use comes from the binding itself - a successful apply changes the
  board revision, so the token can never match again - with a consumed-nonce set on top to turn
  a replay into a precise `token_already_used`. That set is swept by **expiry, not by a count
  cap**: evicting a still-valid nonce would re-enable a replay, and the documented undo restores
  the exact revision that nonce was bound to. **Restarting the server invalidates outstanding
  tokens**, which is the right default for a short-lived confirmation. The token is verified
  *before* the board is read or parsed, so an unauthorized caller cannot make the tool do
  expensive work, and the manifest geometry is bounded before any of it is materialised.
- **A lockfile is a hard refusal, checked twice.** A `~name.lck` sibling means a GUI may hold
  the board open, and pcbnew has no external-change watcher, so a later save would silently
  overwrite the applied board. It is checked up front and **again under the exclusive lock,
  immediately before the rename**, because a GUI opened in between would otherwise land on top
  of the applied board. The file is named in the error and **never removed** - stale locks are a
  known KiCad bug, but deleting one is the operator's judgement, not ours.
- **Compare and swap under an exclusive lock.** The board is opened, an exclusive `flock` is
  taken on that descriptor, and the lock is **held across the compare-and-swap and the rename**.
  Under the lock the board's current bytes are re-read and refused unless they still hash to the
  digest the caller previewed. Two applies from the same base therefore serialise: the loser
  blocks until the winner's rename completes, then sees the winner's bytes under the swap and
  refuses instead of clobbering them. A mismatch is **never auto-refreshed**.
- **A pre-apply copy is written first**, timestamped and content-addressed, into a
  `.copper-mcp-backups/` subdirectory - not beside the board, where a `pre-apply.kicad_pcb`
  would itself be a valid apply target and cascade. Its path is returned; if it cannot be
  written the apply stops, because no copy means no way back. Copies are kept to a bounded count
  per board and carry the board's own permission bits. KiCad's `-bak` files are never touched.
- **Publication is atomic, and failures are reported by whether the rename happened.**
  `replace_workspace_file` writes an `O_EXCL` temporary in the target's own directory, copies
  the target's mode onto it, `fsync`s it, renames over the name through a held directory
  descriptor, and `fsync`s the directory. A failure *before* the rename leaves the board
  untouched and is a clean refusal. A failure *after* it means the board is already changed:
  that is reported as `applied_but_unverified` with the real new revision - never as "nothing
  changed" - and a **guarded** rollback runs that restores the pre-apply bytes only if the file
  still holds exactly what this apply wrote. If a concurrent writer has landed since, the file
  is left alone rather than clobbered.
- **Unsafe filesystems are refused where detectable.** `statvfs` names the filesystem on macOS
  and the BSDs but not on Linux, so a negative result means *not detected*, never *known safe* -
  which is why detection refuses rather than reassures.

`os.rename` is used rather than `os.replace`. On POSIX both are the same `renameat` syscall and
both replace atomically; `os.replace` exists to give Windows those semantics and does not accept
`dir_fd` on macOS, so using it would forfeit the descriptor anchoring that keeps the operation
confined.

### The undo story, stated plainly

**The pre-apply copy is the undo, and restoring it is manual.** The user copies that file back
over the board. There is no `undo_apply` tool, no journal, and no automatic revert. This is not
a KiCad undo step and never appears in KiCad's undo stack; a real single-undo transaction needs
the IPC API, which is deferred.

### Why the CLI does not take a token

The token defends the MCP surface, where a *model* drives the tools and must not be able to
apply something the operator never previewed. On the CLI the operator is the one typing the
command, and the signing key lives only inside the issuing process - so a token minted by an
earlier `preview-route` run could never verify in a later `apply-candidate` run. Requiring one
would be a flag that can only ever be satisfied by a value the same process just made up.

The CLI's authorization is therefore the operator flag plus `--expect-board-revision`, the
compare-and-swap the operator states explicitly. The token is minted in-process for exactly
that binding, so the service keeps one code path and its token check stays a real invariant
rather than a branch skipped on this route.

## Still not shipped

- **No merge, no lock override, no IPC apply, no placement apply, no batch apply.** These are
  non-goals for v0.1, not omissions. IPC in particular is excluded on a technical ground rather
  than an effort one: it mutates an in-memory document whose state cannot be bound to a file
  digest, so revision binding would be unsound there.
- **No DRC evidence on an applied board.** The verification matrix reports `kicad_opened_board`
  and `drc_after_apply` as one-value `not_run` literals, because this path never runs KiCad.

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
