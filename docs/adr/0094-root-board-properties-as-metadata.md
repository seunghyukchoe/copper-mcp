# ADR-0094: A root board property is a text variable, accepted and counted rather than modelled

- Status: Accepted
- Date: 2026-08-12
- Owners: `@seunghyukchoe`
- Related: [Issue #140](https://github.com/seunghyukchoe/copper-mcp/issues/140),
  [Issue #116](https://github.com/seunghyukchoe/copper-mcp/issues/116), ADR-0005, ADR-0090,
  ADR-0091, ADR-0092,
  [KiCad root board properties research](../research/kicad-root-board-properties-v1.md),
  [D-184](../ledgers/decision-ledger.md), [R-139](../ledgers/risk-register.md),
  [SEC-135](../ledgers/security-ledger.md)

## Context

Two root-level `(property "<key>" "<value>")` expressions refused four whole boards:

```text
code:    unsupported.construct
message: root board properties are unsupported
locator: kicad_pcb.child[7]
```

The refusal was deliberate and named — `property` was an entry in `_UNMODELLED_ROOT_HEADS` — so
this decision is about whether the refusal should stand, not about the diagnostic. Four of the
seventeen currently-saved boards in the corpus refused here, and issue #140 expected removing it to
convert all four. **It converts none of them**, for reasons the "Consequences" section measures
rather than argues; that outcome does not change whether the refusal is right, which is what this
record decides.

This is structurally ADR-0090's problem restated: a root construct that is organisation rather
than geometry. ADR-0090's outcome is the template — accept on a closed shape, count the accept in
`ConversionResult` rather than dropping it silently, name a refused construct only from a closed
table, and refuse anything carrying a real constraint. ADR-0090 also refused a *locked* group
specifically, because lock propagates to members. The analogous question here is: **is there a
property key that changes what the board *is* rather than what it is *called*?**

**The answer is not the comfortable one, and the first draft of this decision had it wrong.** A
root board property is *not* unconditionally cosmetic. It is one entry of `BOARD::m_properties`,
whose reader is `BOARD::ResolveTextVar`, and substitution has termini that are real board content:

- `PCB_TEXT::GetShownText` resolves through it, and **text on a copper layer is plotted copper**
  whose glyphs depend on the value.
- `PCB_BARCODE::AssembleBarcode` builds its module pattern from the shown text.
- `DRC_ENGINE::loadRules` expands the same tokens over a `.kicad_dru` **before** parsing it, so a
  rule reading `(constraint clearance (min ${MIN_CLR}))` **takes its clearance from a board
  property**. That is a constraint, and it is the counterexample any "metadata is inert" argument
  has to survive.

So "it is metadata, therefore it is safe" is not available. What is available is narrower and
checkable: **every one of those termini is already refused by, or already outside, this adapter —
for its own reasons, and independently of whether any property is present.**

| Terminus | Existing behaviour, unchanged by this decision |
|---|---|
| Root text on a copper layer | refused — "root graphic on copper is unsupported" (issue #141) |
| Footprint text on a copper layer | refused — "footprint graphic on a copper layer is unmodelled copper" |
| `(barcode …)` | not in the root vocabulary; refused without being named |
| `.kicad_dru` custom rules | never parsed by this adapter (ADR-0005, and the Board IR contract) |

and Board IR **carries no text at all**. A `Footprint` is an id, an origin, a rotation, a side, pad
ids and courtyards; no reference designator, field, or title block is modelled, and the only
document string reaching a snapshot is a layer name, which KiCad does not expand variables into.

The custom-rule case is not answered by "we do not read it", and it does not have to be:
CopperMCP's authoritative DRC surface does not re-implement rules. `kicad_cli.py` carries
`.kicad_pro` and `.kicad_dru` into the DRC context and runs **KiCad** over the board bytes, and the
write-back path preserves a root property byte-for-byte. The one surface that claims to honour a
custom rule still honours it, with the property expanded by KiCad from the real value.

**There is no reserved key.** No board property key is special-cased anywhere in KiCad's board
code — the format's reserved property keys (`ki_keywords`, `ki_description`, `ki_locked`,
`ki_fp_filters`) are *symbol* properties — and a key colliding with a built-in text-variable token
is *shadowed* by the resolver rather than empowered by it. Refusing a specific key would be a rule
with no domain behind it. This is the locked-group question asked and answered, not skipped.

## Decision

**1. Accept a root `(property …)` as board metadata, on a closed field table.** The head joins the
root vocabulary; the root allowlist does not otherwise open. The accepted subset is stated as a
table rather than as prose, because ADR-0092's prose subset admitted two forms it did not mean and
neither was found by reading it:

| Field | Required | Permitted |
|---|---|---|
| positional atom 0 — the key | yes | exactly one atom, **quoted** in the source; any string |
| positional atom 1 — the value | yes | exactly one atom, **quoted** in the source; any string |
| any further positional atom | — | none; a third direct atom refuses |
| any child expression | — | none; any `(…)` child refuses |

Every row is pinned by a test. Every form outside the table is one **KiCad's own parser rejects**:
`parseBoardProperty` is `NeedSYMBOL(); NeedSYMBOL(); NeedRIGHT();`, so a third atom or a nested
expression throws there too. The single place this is narrower than KiCad is quoting —
`NeedSYMBOL` admits a bare token, while `formatProperties` writes both halves through `Quotew`,
which quotes unconditionally. An unquoted atom is a form KiCad's writer cannot emit, appears
nowhere in the surveyed tree, and refuses; over-refusal is the conservative direction.

**2. Accept every key.** See the reserved-key finding above. A key is board-author text and is read
past without being echoed into a diagnostic, an identity, or a snapshot.

**3. Do not model the map, and say so in a field.**
`ConversionResult.unmodelled_board_property_count` reports how many root properties were accepted
and not modelled, following D-157's measured-field pattern and ADR-0090's precedent: every caller
of `parse_kicad_bytes` treats a non-empty `diagnostics` tuple as a refusal, so a warning would
refuse the board this decision exists to admit. It counts **expressions, not KiCad map entries** —
`std::map::insert` silently keeps the first value for a repeated key, so a document with a
duplicate has more expressions than entries, and the count states the quantity this adapter can
establish exactly.

**4. Leave the closed refusal table otherwise intact.** `property` leaves
`_UNMODELLED_ROOT_HEADS`; `dimension` and `image` remain, and the rule that a refusal names a
construct only by an equality-selected value from that table is untouched.

## Consequences

**No additional board converts, and issue #140's premise was wrong.** The issue states that four
saves "refuse for this and nothing else", and the largest single conversion win was the reason this
was taken first. Measured on the same 17-board corpus with the same runner before and after: **11
of 17 → 11 of 17.** All four saves still refuse, each now naming the construct behind the one this
removes:

| Boards | Refusal before | Refusal after |
|---:|---|---|
| 3 | `unsupported.construct` — root board properties are unsupported | `unsupported.transform` — courtyard layer does not match its footprint side, at `kicad_pcb.footprint[N].courtyard[N]` |
| 1 | `unsupported.construct` — root board properties are unsupported | `unsupported.construct` — expression contains an unsupported semantic field, at `kicad_pcb.footprint[N].pad[N]` |

The premise failed for a structural reason worth naming: **conversion refuses on the first error,
so a refusal names the first blocker in document order and says nothing about how many are behind
it.** Reading "refuses for this and nothing else" out of a single diagnostic is reading an
existential as a universal, and it is a mistake this project has now made twice — D-179 recorded the
same shape when a net-tie board turned out to carry three stacked blockers. Confirmed independently
of the change: deleting the property expressions from each board's own bytes and converting the
result on unmodified `main` reproduces exactly the refusals in the "after" column, so the two
newly-visible constructs were always there and are in no way caused by this decision.

**Advancing a refusal through a stack is a real result and is deliberately not reported as a
conversion.** What this decision buys is that the stack is now one shorter and its next blocker is
named and located, which is how such a stack is measured at all. Two constructs are now visible
that no record previously named — a courtyard whose layer disagrees with its footprint's side, and
an unsupported field inside a pad — and each deserves its own issue and its own decision.

No new benchmark identifier is allocated for the measurement: the runner and the corpus are
B-099's, it is a before/after count rather than a new question, and the run artifact is not
committed because it is derived from a private working tree. The counts are recorded in D-184.

**The direction-of-error invariant is untouched.** Obstacles may only be over-approximated,
connectivity and the board outline only under-approximated. A root property contributes no
quantity in either direction, because every path by which its value could become a quantity
terminates in a construct this adapter already refuses or never reads. Nothing is rounded, nothing
is widened, nothing is dropped from the routing room.

**The with/without equality is reported as what it is, and is not the safety argument.** Converting
a fixture with a root property and converting the same bytes without it produces Board IR content
equal in `outline`, `copper_layers`, `nets`, `constraints`, `constraint_digest`, `footprints`,
`pads`, `vias`, `segments`, `arcs`, `zones` and `keepouts`, differing only in `source`, whose
revision is the digest of the bytes. **That is a modelled-as-nothing and schema-stability result
and carries no soundness evidence.** It compares two outputs of the same reader, and that reader
reads no property at all, so it holds by construction for *any* value — including one that matters
in KiCad. D-178 recorded that trap once and ADR-0090 recorded the case where it hid a real defect;
this record does not repeat either. Safety rests on section "Context" above: the format's real
semantics plus the fact that every substitution terminus is already closed.

**A test pins that the accept did not open the path it depends on.** A board carrying a property
*and* `${KEY}` text on a copper layer still refuses, and `(barcode …)` still refuses. If a future
change ever models copper text, that test fails and this accept must be re-argued rather than
inherited.

**Write-back is verified rather than assumed.** A placement splice over a board carrying a root
property leaves its bytes, and the whole document tail from it onward, byte-identical. That is what
keeps the authoritative-DRC argument true in practice: the `.kicad_dru` expansion KiCad performs on
the patched board sees the same value the designer wrote.

**No content address moves.** A board with no root property converts identically; a board with one
previously produced no snapshot at all, so there is nothing whose digest could change. No Board IR
schema version, field, or golden identity is affected — `ConversionResult` is an adapter result,
not part of the canonical content that is digested, and `tests/test_golden_identities.py` passes
unmodified with every pinned digest unchanged.

**The residual is a modelling gap, and it is stated.** Board IR has no text-variable map, so a
caller that rebuilt a board from a snapshot alone would lose it, and text rendered from Board IR
would leave `${KEY}` unexpanded. Neither is a thing CopperMCP does today — every write-back path is
source-preserving — but neither is prevented by anything structural. R-139 records it.

**A KiCad-side round-trip hazard is recorded, not repaired.** `BOARD::SynchronizeProperties()`
replaces the entire map from `PROJECT::GetTextVars()` on save, and the Board Setup → Text Variables
panel edits the `.kicad_pro` rather than the board — so root properties are effectively a cache of
the project's text variables and a board-only key does not survive a GUI save with a project
loaded. Resolution precedence also flipped after 9.0 (project-first on `master`, board-first on
`9.0`). CopperMCP reads neither source, so neither reaches any claim it makes; it is recorded so a
future round-trip feature does not rediscover it the hard way.

**The refusal for an out-of-shape property is new, and it is a widening of refusals in one
direction only.** Before this change every root property refused. After it, only a property outside
the field table does — a strictly smaller set, and one that is empty on the surveyed corpus.

## Alternatives considered

**Keep refusing.** Rejected, and the measurement above is not a reason to reconsider. Refusing a
whole board for two lines of user-defined text is wrong on its own terms — `property` is one of the
root sections the format enumerates, and the domain argument for accepting it is checkable rather
than hopeful. That the four affected boards still refuse for something else makes the change
*worth less*, not less correct: a refusal that is right for the wrong reason is still a defect, and
leaving it in place would also have left the constructs behind it invisible.

**Accept on the head alone, without the field table.** Rejected, and this is the alternative
ADR-0090's locked group exists to warn about. The acceptance argument is conditional on what the
expression contains: a `property` carrying a `layer` or an `at` is not the two-string pair this
decision reasoned about, and waving it through on the strength of its head would be assuming the
conclusion. Each condition is checked and each refuses.

**Accept an unquoted key or value, since KiCad's parser does.** Rejected. The writer cannot emit
one, the corpus contains none, and the accepted subset is built from what KiCad *writes* rather
than from the widest thing it will read. If a non-KiCad writer ever produces one, it will refuse
with a located, typed diagnostic rather than being silently absorbed into a subset nobody surveyed.

**Refuse a key that looks load-bearing — a denylist of names.** Rejected as a rule with no domain
behind it. No key is special-cased in KiCad, so no list could be principled; it would be a guess
dressed as a check, and it would refuse boards for the spelling of a string. The load-bearing
question is real, and it is answered where it actually lives — at the substitution termini, all of
which are already closed.

**Model the map in Board IR, as a text-variable dictionary.** Rejected for this change, not
forever. It is a schema addition with identity, ordering, and digest consequences, it would put
board-author strings into the canonical content that every downstream identity is derived from —
which is a security surface, not just a schema cost — and nothing in the routing or placement
surfaces consumes it. Recording the count is the honest interim.

**Emit a warning diagnostic instead of a count.** Rejected for the reason D-157 recorded and
ADR-0090 restated: every caller treats a non-empty diagnostics tuple as a refusal, so a warning
would refuse the four boards this decision exists to admit.
