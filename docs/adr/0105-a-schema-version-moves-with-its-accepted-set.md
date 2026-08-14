# ADR-0105: A schema version moves with its accepted set, and `0.2.0` is frozen where it stands

- Status: Accepted
- Date: 2026-08-14
- Owners: `@seunghyukchoe`
- Related: [Issue #172](https://github.com/seunghyukchoe/copper-mcp/issues/172),
  [ADR-0096](0096-edge-connector-pads-convert-as-smd.md) (amended by this record),
  [ADR-0097](0097-courtyard-layer-decides-the-side.md), ADR-0098,
  [the post-0.8.0 audit](../audit/2026-08-14-post-0.8.0-audit.md) §3.1 and plan items P3.1/P3.2

## Context

A JSON Schema file is a promise about which documents are acceptable. Two copies of a file can
carry the same `$id`, the same filename and the same declared version and still accept different
documents, and a consumer who downloaded the earlier copy has no way to learn that.

Issue #172 found one instance: [ADR-0097](0097-courtyard-layer-decides-the-side.md) added
`far_side_courtyards` and `far_side_courtyard_circles` to `$defs/footprint` in
`schemas/board-ir/0.2.0.schema.json` at `v0.8.0`, where `additionalProperties: false` is in force,
without moving the version. A consumer holding the `v0.7.0` copy rejects a valid `v0.8.0` snapshot
of any board carrying a far-side courtyard.

### The sweep, which is what promotes this from hygiene to a gate

The post-0.8.0 audit swept every schema at every release tag. **Verified**, and re-derived
independently for this record by `scripts/check_schema_sets.py` over all eight tags:

| Release | Schema | Change | Direction |
|---|---|---|---|
| `v0.3.0` | `audio-benchmark-catalog/0.1.0` | `+expected_pad_count` as a **required** key; `+multi-pin-route-preview` enum member | **narrowing** and widening |
| `v0.7.0` | `board-ir/0.2.0` | `+courtyard_circles` and its `$def`; `net_id` widened to accept `null` on via, segment and arc | widening |
| `v0.7.0` | `drc-summary` | `+clean` as a **required** key, with an `allOf` pinning both derivations | **narrowing** |
| `v0.8.0` | `board-ir/0.2.0` | `+far_side_courtyards`, `+far_side_courtyard_circles` — #172's instance | widening |

Four instances, three releases, three files, **two directions**. The controls hold: `board-ir/0.1.0`
is byte-identical from `v0.2.0` (where it first appears) through `v0.8.0`, and `circuit-intent`,
`circuit-schematic-build`, `candidate` and `board-manifest` never change after introduction. The
drift is a property of four specific edits, not of the repository's habits generally.

Two consequences the single-instance framing misses.

1. **Two of the four break in the direction #172 does not discuss.** A required-key addition is a
   *narrowing*: a document that validated yesterday fails today. #172 argues about widening, the
   forgiving direction, where the older consumer over-refuses. A narrowing invalidates documents
   already written. The gate below therefore fires on both, and says which.
2. **A `0.2.0` document could have been produced under three different accepted sets**, spanning
   `v0.5.0` (where `0.2.0` first appears) through `v0.8.0`, and they are not interchangeable.
   Because `$defs/footprint` carries `additionalProperties: false`, a document produced at `v0.8.0`
   carrying `far_side_courtyards` is *rejected* by the `0.2.0` schema as published at `v0.5.0`. One
   version string, three accepted sets, one-directional incompatibility.

### #172's thesis, restated rather than adopted

#172 frames ADR-0096 as the rule and ADR-0097 as the violation. That is right about the sequence and
wrong about the class. **ADR-0096 was the first articulation of the rule, not the rule itself**, so
the two instances that *predate* ADR-0096 — `audio-benchmark-catalog` at `v0.3.0` and `drc-summary`
at `v0.7.0` — are instances of the same class and not exceptions to it. A Board IR-only decision
would fix one of three affected files.

### The bump's cost, verified rather than read

ADR-0096 declined a bump to `0.3.0` on costs it had reasoned rather than measured, then retracted
the largest of them: the schema version is **not** inside any Board IR digest. PR #149 measured what
a bump really costs. The audit carried that measurement as `read-but-not-verified`. It was re-run
against this tree before this decision was written, and the result is one correction and otherwise a
confirmation.

**Verified** by setting `BOARD_IR_SCHEMA_VERSION = "0.3.0"` and re-deriving:

- The snapshot digest does **not** move: `sha256:157661bf…`, unchanged. So do the constraint digest
  and the source revision. The digest is taken over `_content_payload`, which carries no schema
  version; the version appears only in the envelope. Downstream identities bind
  `base_revision = snapshot_digest`, so **there is no cascade**.
- The encoded envelope is **4,280 bytes before and after** — `"0.2.0"` and `"0.3.0"` are the same
  width — and differs at **exactly one byte, index 4,182, `2` against `3`**. Substituting `0.2.0`
  back reproduces the `v0.8.0` bytes exactly, digest
  `sha256:3a4edf3732624c836860a112d3d060778b6e6b28f3ee6fc5f8863eeacba0efe6`.
- **Six tests move, not five**, all fixture-or-version-string. The correction is small and it is
  recorded rather than smoothed: `D-186` measured five at `PR #149`'s tree, and ADR-0097 later added
  a fourth test to `test_board_ir_schema.py` — `test_schema_accepts_an_emitted_far_side_courtyard_payload_and_closes_it`,
  which validates an emitted payload against the schema's `schema_version` `const`. The other five
  are exactly the ones `D-186` named. The class of the cost is unchanged; the count was measured
  once and then aged. That is itself an instance of what this ADR is about.
- `codec.py`'s envelope version check refuses every persisted `0.2.0` envelope with a **typed** code
  (`schema.invalid`), not an uncaught exception. This is the largest true cost and it is real for
  anyone storing snapshots: they must re-convert from the source board.

**Not claimed:** that the six are the whole cost outside this repository. A consumer's stored
envelopes are the cost this project cannot measure, and the migration note states it plainly.

## Decision

**`BOARD_IR_SCHEMA_VERSION` becomes `0.3.0`. `schemas/board-ir/0.2.0.schema.json` is frozen
permanently at its `v0.8.0` bytes. The three earlier in-place changes are not corrected
retroactively. A checker fails any future accepted-set change at an unmoved version, in either
direction.**

### The rule, stated once

> A published schema's accepted set may change only when its declared version changes. The accepted
> set is the property names of every object, its `additionalProperties` setting, its `required`
> list, every `enum`, every `const`, and every union `type`.

The rule covers `schemas/**/*.json` — Board IR, `drc-summary`, `audio-benchmark-catalog`, and
everything published later — and not only Board IR.

### Freeze, do not correct

`0.2.0` stays exactly as `v0.8.0` published it. Three reasons, and the first is sufficient.

1. **A retroactive correction cannot reach the copies that matter.** The bytes are in eight release
   tags, in the sdist and wheel on PyPI, and in whatever a consumer downloaded. Editing the file in
   `main` produces a *fourth* accepted set for `0.2.0` and makes the situation strictly worse.
2. **It is the same discipline the ledgers already run under.** Rule 3 of
   [the ledger rules](../ledgers/README.md) forbids editing a landed row even when it is plainly
   wrong; the correction is a new record. A published schema is a landed row with more readers.
3. **The frozen copy is still true of the documents it was published beside**, and that is checked:
   `test_the_frozen_v0_2_0_schema_still_accepts_the_envelope_it_was_published_beside`.

**What a consumer is owed instead is a plain statement**, and the migration note carries it:
`0.2.0`-as-published spans **three** accepted sets across `v0.5.0`–`v0.8.0`, and the authoritative
copy is **the one shipped alongside the release that produced the snapshot** — not the newest, and
not the one bearing the matching version string.

### ADR-0096 is amended, by name

ADR-0096 articulated this rule and then declined the bump whose cost it had not measured. Its
reasoning is not withdrawn — it was right that widening `0.2.0` in place is unacceptable, and right
that an option at a known price is worth more than an option at a guessed one. What is amended is
the conclusion drawn from an unmeasured premise:

- ADR-0096's argument (2) — that nothing reads a fourth `PadKind` member, so a version is not worth
  spending — **still stands** and is untouched. This ADR spends the version for a different reason:
  three accepted sets already exist under one string, so the version is not being *spent* so much as
  *reconciled*.
- ADR-0096's implicit premise that the bump was expensive is **retired**. #149 measured it and this
  record re-verified it: one envelope byte, six tests, one typed codec refusal.
- The irony ADR-0096 recorded — that the digest finding makes the rejected alternative cheaper —
  resolves here in the direction it pointed. `PadKind` still gains **no** member; that decision is
  unchanged and is not reopened by this one.

### The gate

`scripts/check_schema_sets.py`, in `make lint` and in CI.

**Why a script rather than an extension of `tests/test_schema_conformance.py`**, which was the other
candidate. The check reads every release tag through `git show`. That makes it a checker of the
repository rather than of the code: it cannot run from an sdist, where `.git` is absent, and
`test_schema_conformance.py` is explicitly about a different question — does a schema accept what
CopperMCP emits — which must keep working offline in a source tarball. Every checker of this class
already lives in `scripts/` and runs in `make lint`, and `check_doc_links.py` already shells out to
git. The gate's *pure* half — accepted-set extraction and direction classification — is imported and
unit-tested from `tests/test_check_schema_sets.py`, so the git dependency does not make the logic
untestable.

It sweeps two axes: every consecutive release tag, which is what puts the four historical instances
on the record as exemptions rather than as folklore, and the newest tag against the working tree,
which is the half that catches the next break before it merges. Two things fail separately because
the comparison cannot see them: a published schema being **removed**, and a **release tag missing
from `RELEASE_TAGS`**, which would leave the working-tree half comparing against a tag that is no
longer the newest.

**Removal is not the only silent route, and saying it was is a correction this record owes.** An
accepted-set gate cannot enforce a byte freeze, and adversarial review proved it: all 2,046 lines of
`0.2.0.schema.json` were rewritten — reindented, every multi-member `enum` reversed — and the gate
plus all 107 schema tests stayed green, *correctly*, because the accepted set had not moved. There
are therefore three silent routes, not one: removal, an accepted-set-neutral rewrite, and an edit to
a keyword the extractor does not watch (`pattern`, `maximum`, `$ref`). All three are closed for the
two frozen files by a **byte pin** —
`test_a_frozen_published_schema_keeps_the_exact_bytes_it_shipped_with`, digest and byte count for
`0.1.0` and `0.2.0` both. The active `0.3.0` is deliberately *not* pinned, and a test says why: a
schema that is expected to change would turn every legitimate edit into a pin update, which is how
a pin becomes a rubber stamp.

**Exemptions follow `EXEMPT_LABEL_RECORDS`' discipline**, with two clauses this record's first
draft did not have. `EXEMPT_DRIFT` is keyed `(file, declared version, tag)`, each entry names
`D-197`, and four entries make it green. What is **enforced**:

- **An entry matching no real drift fails the run**, so none can be added and then forgotten.
- **An entry's tag must be a release tag.** Adversarial review defeated the first draft by adding
  real drift plus one line keyed `(file, version, "the working tree")` citing a nonexistent row —
  and the run passed. Only a *published* break is unrepairable, so only a published break is
  exemptible; anything in the working tree can simply be fixed. A working-tree key is now both
  rejected and **inapplicable**, so the drift it tried to suppress is reported too.
- **An entry's recorded direction must be one the comparison observed.** `narrowing` and `widening`
  inside a reason were unverified prose, and a mutant flipping the word on the ADR-0097 entry
  survived everything. The check is *containment*, not equality, and `drc-summary` is why: a
  required-key addition is simultaneously a widening of the property set and a narrowing of the
  `required` list, and the entry records the net effect on a consumer. A reason must claim only
  directions the change has; it is not required to enumerate them all.

What is **not** enforced, said plainly rather than left to be assumed: that the ledger row an entry
cites exists, or says what the entry claims it says. The earlier sentence "a fifth cannot be added
without a ledger row" was **false as written** — nothing reads `D-197`. What a fifth cannot do is
name a non-release tag or a direction the change does not have. The citation itself is review
discipline, exactly as it is in `check_doc_links.py`.

**What the gate does not own**, stated because an absence is evidence only if the observation could
report a presence. It has no opinion about whether a change is *correct*, only about whether the
version moved with it. It cannot see a semantic change expressed through keywords outside the list
above — a tightened `pattern`, a lowered `maximum`, a changed `$ref` target. Those are real drift
and this checker is blind to them. It is a floor, and widening the extracted set is how the floor
rises.

### What the mutation run found

Per [ADR-0098](0098-reproducible-mutation-evidence.md), stated so it is auditable rather than
asserted. Spec: [`docs/mutants/2026-08-14-schema-set-drift.json`](../mutants/2026-08-14-schema-set-drift.json).
Invocation:

```bash
PYTHONPATH=src:. python scripts/mutation_harness.py \
  docs/mutants/2026-08-14-schema-set-drift.json --report report.json
```

**22 mutants: 21 killed, 1 survived and declared equivalent with its argument.** No
`stale_anchor`, no `invalid_run`. Python 3.12.13 on `macOS-26.5.2-arm64`. Every mutant carries its
anchor and its named killing tests in the spec, and every kill is proved in both directions by the
harness. This is a claim about those twenty-two, not about the mutation space.

Five results are worth recording rather than just counting.

- **Both directions are covered by real edits rather than synthetic ones.** `SD1` removes the
  exemption for ADR-0097's *actual historical* widening, so the gate must detect the real
  `far_side_courtyards` insertion against the real `v0.7.0`→`v0.8.0` bytes; `SD2` re-applies an edit
  of exactly that shape to the now-frozen file live. `SD3` is the narrowing — a key added to a
  `required` list — and `SD5` makes the classifier call a narrowing a widening, which the direction
  assertions kill. A gate that fired in one direction only would survive `SD3` and `SD5`.
- **`SD6` is the mutant that matters most and is the easiest to omit.** It deletes the working-tree
  comparison, leaving the historical sweep intact — so the gate stays green, keeps reporting four
  matched exemptions, and records the past while catching nothing. It is killed only because two
  tests assert on the working-tree axis specifically. An "it passes" test would not have found it.
- **Six mutants exist because a review defeated the first draft, and each closed a real hole.**
  `SD10` (a site's appearance claimed a direction it cannot know), `SD11` (a removed published
  schema), `SD12` (a stale `RELEASE_TAGS`), `SD13` (a working-tree exemption smuggling live drift),
  `SD14`/`SD15` (an unverified direction word, and the exact flip the reviewer used), and `FZ1` (a
  frozen schema rewritten without moving its accepted set). Every one of them was **green before**.
- **`CV5` is the most useful result in the run, and it is a survivor.** It was declared `killed`,
  applied, and survived: making the *inner* refusal message echo the caller's declared version
  changes nothing a caller sees, because every `BoardIRValidationError` raised inside
  `decode_snapshot_json`'s `try` block has its message discarded and only its code kept. So the
  non-echo property is enforced by the normalisation, not by the test — which also means the test
  proving it was, at that site, incapable of reporting a presence. `CV6` mutates the *tail*, which
  is the site that can echo, and is killed there. The mutant is left in the spec as
  `equivalent` with its argument, because the survival is the evidence.
- **`CV1` came back `stale_anchor` rather than passing quietly**, because the version refusal it
  anchored to had been rewritten by this same change. Re-anchored and re-run. That is the guard
  ADR-0098 added working as intended: a mutant that silently stops applying is a passing check
  measuring nothing.

## Consequences

- **No pinned identity moves.** Every digest in `tests/test_golden_identities.py` is unchanged, and
  the byte count is unchanged. This is a property of the design and it is the premise the decision
  rests on, so it was verified rather than assumed.
- **One committed fixture moves, at one byte.** `tests/fixtures/board-ir-v0.2/schema-valid.json`
  now carries a `0.3.0` envelope.
  `test_the_version_bump_moved_the_envelope_by_its_version_string_and_nothing_else` proves the move
  by construction: substituting `0.2.0` back reproduces the published bytes exactly, against a
  recorded digest. The `0.2.0` artifact is therefore not committed twice.
- **The fixture directory keeps its name.** `tests/fixtures/board-ir-v0.2/` holds a `0.3.0` envelope
  and eleven `.kicad_pcb` boards that no version bump touches; `board-ir-v0.1/subset.kicad_pcb` has
  been the source board for the active golden since `0.2.0`. The directory names the era the boards
  were authored in. Renaming it would move thirty path references across nine test modules and five
  benchmark scripts to record a version change that touched one file.
- **A persisted `0.2.0` envelope no longer decodes, and the refusal now says the true why.**
  Re-convert from the source board; there is no auto-migration, exactly as `0.1` → `0.2` had none.
  The **diagnostic vocabulary gains one code**, `schema.version`, separate from `schema.invalid`.

  This record's first draft kept the generic code and deferred the message to plan item P3.7. That
  was wrong, and adversarial review was right to reject it. `schema.invalid`'s message reads *"JSON
  does not conform to Board IR v0.2"* — and a persisted `0.2.0` envelope conforms to
  `0.2.0`-as-published **exactly**; it is refused *because* it does, at a version this build has
  superseded. That is not a stale label for a later sweep to tidy: it is a wrong *why* on a refusal
  surface, on the one path this decision created. P3.7's golden set pins messages that are true;
  it does not launder one that is false.

  The new message is derived from `BOARD_IR_SCHEMA_VERSION` rather than restated, so it cannot go
  stale the way its predecessor did, and it names only the version this build accepts — never the
  version the document declared, which is caller-controlled and which no diagnostic echoes.
  `0.1` envelopes move to the same code for the same reason, which is a behaviour change the
  migration note carries. Pinned by
  `test_the_codec_refuses_a_persisted_v0_2_0_envelope_with_a_discriminated_code`,
  `test_the_version_refusal_never_echoes_the_version_the_document_declared` and
  `test_the_version_code_separates_a_stale_version_from_bytes_that_are_not_board_ir`.
- **`BoardIrSummary.ir_schema_version` reports `0.3.0`.** This is a visible MCP contract change and
  the migration note leads with it.
- **`schemas/board-ir/0.3.0.schema.json` differs from `0.2.0` in three strings only** — `$id`,
  `title`, and the `schema_version` `const`. Substituting them back reproduces `0.2.0` byte for
  byte. The accepted set is deliberately identical: this bump reconciles a version with the set it
  already had, and folding a content change into it would make the two indistinguishable later.
- **The other refusal messages reading `Board IR v0.2` are deliberately not touched, and the line
  between them and the one that was is stated.** About twenty remain. Each names the IR *model
  generation*, which this change does not move — no modelled field, type or invariant changes — so
  each is imprecise but **true**. The one that was fixed was **false**: it told a caller their
  conforming document did not conform. Imprecise-but-true prose across a published refusal surface
  is plan item P3.7's golden set; a false *why* on a path this decision created is this decision's.
  This ADR does **not** claim the remaining twenty are unambiguous to a reader who has only the
  string.
- **`make lint` gains a git dependency.** The gate exits with a clear message when `git` is absent
  rather than passing silently. CI already checks out with `fetch-depth: 0`.

## Alternatives considered

- **Correct `0.2.0` in place and re-publish.** Rejected: it produces a fourth accepted set for one
  version string and cannot reach a downloaded copy. This is the option that makes the problem worse
  while looking like the fix.
- **Bump only `board-ir` and leave `drc-summary` and `audio-benchmark-catalog` alone.** Rejected:
  it fixes one of three affected files and would encode #172's single-instance framing as policy.
  Neither of the other two is bumped *here* either — no document under them has been re-emitted —
  but both are inside the rule and inside the gate, which is the part that matters going forward.
- **A numeric or fuzzy tolerance on "how much drift is acceptable".** Rejected on the reasoning
  ADR-0095 gives against oracle-fitted constants. A version either moved with the set or it did not.
- **Deprecate `0.2.0` with a compatibility shim in the codec** that reads a `0.2.0` envelope and
  re-labels it. Rejected: it would have the decoder accept a document under a version whose accepted
  set it can no longer name, which is the same failure in a different place. Re-conversion from the
  source board is exact and already the `0.1` → `0.2` precedent.
- **Extend `tests/test_schema_conformance.py` instead of adding a script.** Rejected on the
  sdist/git-dependency argument above; the pure logic is unit-tested from that direction anyway.
- **A gate that fires on widening only**, matching #172's framing. Rejected: it would miss two of
  the four instances that motivated it, and the two it would miss are the more severe direction.

## References

- [ADR-0096](0096-edge-connector-pads-convert-as-smd.md), amended by this record
- [ADR-0097](0097-courtyard-layer-decides-the-side.md), whose widening #172 reports
- [The post-0.8.0 audit](../audit/2026-08-14-post-0.8.0-audit.md), §3.1 and plan items P3.1/P3.2
- [Decision ledger D-197](../ledgers/decision-ledger.md), which carries the verification and the
  six-not-five correction
- [Risk register R-151](../ledgers/risk-register.md), which carries what the gate cannot see
- [The 0.9.0 migration note](../migrations/copper-mcp-0.9.0.md)
- [`scripts/check_schema_sets.py`](../../scripts/check_schema_sets.py),
  [`tests/test_check_schema_sets.py`](../../tests/test_check_schema_sets.py)
- [`docs/mutants/2026-08-14-schema-set-drift.json`](../mutants/2026-08-14-schema-set-drift.json)
