# ADR-0099: A pad refusal names the field it refused, and seven of eight fabrication properties convert

- Status: Accepted
- Date: 2026-08-13
- Owners: CopperMCP maintainers
- Related: [ADR-0004](0004-authoritative-kicad-drc.md);
  [ADR-0005](0005-canonical-board-ir.md);
  [ADR-0021](0021-zone-fill-authority.md);
  [ADR-0091](0091-attaching-pad-zone-connect-overrides.md);
  [ADR-0094](0094-root-board-properties-as-metadata.md);
  [ADR-0096](0096-edge-connector-pads-convert-as-smd.md);
  [pad fabrication property research](../research/kicad-pad-fabrication-property-v1.md);
  issue #152; issue #124; PR #135

## Context

A board in the survey corpus refused with `unsupported.construct` — "expression contains an
unsupported semantic field" — at `kicad_pcb.footprint[N].pad[N]`. The message names no field, so
the refusal says a pad carries something unsupported and nothing else. An operator cannot act on it
without opening the board.

**This is the second report of the same defect, and the first fix was correct but partial.** Issue
#124 reported it; PR #135 found that seven named pad refusals were *unreachable dead code* — the
closed pad allowlist ran first and swallowed them all — and repaired the seven by running the named
loop ahead of the allowlist. That reordering was right and is unchanged here. What it did not do
was widen the named table: KiCad's `parsePAD` accepts 39 top-level heads, the adapter modelled
thirteen and named seven, and the other **nineteen** kept emitting the field-less sentence. Issue
#152 is one of those nineteen surfacing on a real board.

**Every head count in this ADR is a `master` figure and is stated as one**, because it is not
version-stable: shipping **KiCad 10.0.5 has 38**, not 39, the difference being exactly
`sim_electrical_type`. Read against 10.0.5 the named table would be 25 rather than 26 and the
newly-named count 18 rather than 19. Nothing behaves differently — a head the running KiCad cannot
write simply never arrives — but a bare "39" would go stale at the next release, so the tree is
named wherever the number is.

So the first question was not "how should this construct convert" but "which field is it, and is
this preemption again". The answers, measured on the corpus with a conversion-only probe:

- The field is a pad **`property`** — KiCad's `PAD_PROP` fabrication annotation. It is the only
  unmodelled pad head anywhere in the corpus, appearing 39 times across **six of the eighteen
  saves**, in every case as `(property pad_prop_heatsink)`. Six carry it; **two** refuse *on* it,
  the rest refusing earlier or converting. Issue #152 records one blocked save and there are two,
  which changes what closing it is worth — the second is not a phono board and is blocked by this
  and nothing else.
- It is **genuine absence, not preemption**. Unlike ADR-0091's seven, `property` is in no named
  table and no allowlist. There was no reachable specific message being swallowed; there was no
  specific message at all.

That splits the work in two, and both halves are decided here because leaving either undone leaves
issue #152 half-closed: the message defect is a class, and the construct is a construct.

## Decision

### 1. The named-refusal table is KiCad's pad grammar minus what converts

`_UNSUPPORTED_PAD_FIELDS` grows from seven heads to twenty-six: every top-level head
`PCB_IO_KICAD_SEXPR_PARSER::parsePAD` accepts, minus the thirteen the adapter models, minus
`property`, plus the legacy `offset` that ADR-0091 already pinned. Each refuses with the sentence
ADR-0091 established, `pad field '<head>' is unsupported`, unchanged byte for byte for the original
seven.

**This half changes messages and never verdicts.** Every one of the nineteen new heads already
refused, through the allowlist, with a sentence that named nothing. The loop now reaches them
earlier and names them. No board's conversion outcome moves, so no direction-of-error argument is
owed for any of the nineteen — which is precisely why they can all be taken at once.

The generic sentence stays reachable, deliberately, for a head KiCad itself cannot write. A test
states the joint coverage as a property (`_UNSUPPORTED_PAD_FIELDS` and `_SUPPORTED_PAD_FIELDS`
together contain KiCad's whole pad grammar) rather than leaving two lists for a reader to diff, and
every one of the twenty-six sentences is pinned, which is what PR #135 did for its seven.

### 2. Seven of the eight writable fabrication properties are accepted; `pad_prop_castellated` is refused

The accepted subset is a **closed table**, not prose — ADR-0092's prose subset admitted two forms it
did not mean, and D-179 recorded the correction.

**Shape.** Exactly one positional atom; that atom **bare**, never quoted; no child expression; at
most one `property` per pad.

**Value.**

| Token | `PAD_PROP` | Verdict |
|---|---|---|
| `pad_prop_bga` | `BGA` | Accepted |
| `pad_prop_fiducial_glob` | `FIDUCIAL_GLBL` | Accepted |
| `pad_prop_fiducial_loc` | `FIDUCIAL_LOCAL` | Accepted |
| `pad_prop_heatsink` | `HEATSINK` | Accepted |
| `pad_prop_mechanical` | `MECHANICAL` | Accepted |
| `pad_prop_pressfit` | `PRESSFIT` | Accepted |
| `pad_prop_testpoint` | `TESTPOINT` | Accepted |
| `pad_prop_castellated` | `CASTELLATED` | **Refused** |
| `none` | `NONE` | **Refused** — see below |
| anything else | — | **Refused** |

Nothing is modelled. There is no new `Pad` field, no `canonical._pad()` change, no Board IR schema
version, and no pinned golden identity moves. The accepted token is *counted*, on ADR-0096's
precedent — see Consequences.

`none` is refused although KiCad's reader accepts it, because KiCad's *writer* cannot emit it: the
writer emits the token only for a non-`NONE` value, so an absent expression **is** `NONE`. That is
the same reader/writer asymmetry ADR-0091 recorded for `zone_connect`'s unwritten `INHERITED`, and
it takes the same answer — accept the shapes the format's own writer produces, and refuse the rest.

Three further shapes KiCad's reader tolerates are refused, and the third is not pedantry. Its
`T_property` arm is a `while( token != T_RIGHT )` loop with the `Expecting(...)` compiled out, so it
silently accepts an empty property, silently skips an unknown token, and accepts several tokens with
the **last** one winning. `(property pad_prop_heatsink pad_prop_castellated)` therefore resolves in
KiCad to the one value this decision must not discard. Admitting multi-atom forms would have been a
route past the equality test.

### 3. Why the seven are safe, and why the eighth is not

**The direction-of-error invariant decides it, and one value fails.** A complete sweep of `PAD_PROP`
over KiCad master — every enumerator literal including `NONE`, plus the bare accessor in both its
spellings, so a site testing `== NONE` is not invisible — is tabulated in the research note. Every
consumer is a fabrication-file attribute, a DRC advisory, a footprint type hint, a statistic, a 3D
export or the editor. Board IR emits no Gerber, no drill file and no position file; ADR-0004
delegates DRC to KiCad, which runs over the original bytes where the token survives.

`CASTELLATED` is the exception, and **the reason is not the one the first revision of this ADR
gave.** That revision said KiCad's `AddEdgeExclusion` marks a region routing may not enter, so
discarding the token would offer routing space the board lacks. That is backwards, adversarial
review of PR #157 caught it, and it is corrected here rather than quietly reworded, because the
error ran in the direction that flattered the conclusion.

**What the exclusion actually does.** `Edge.Cuts` is itself the obstacle: `syncGraphicalItem`
(`pns_kicad_iface.cpp:2044-2076`) syncs every `Edge_Cuts` shape as a `PNS::SOLID` across all copper
layers with `SetRoutable( false )`. The castellated pad's hole is then registered as a
**forgiveness** region — `ITEM::collideSimple` (`pns_item.cpp:226-252`) waives a collision with an
`Edge_Cuts`-parented obstacle when the collision point falls inside one, via the point-in-shape scan
at `pns_node.cpp:797-806`, and the DRC provider waives it twice more
(`drc_test_provider_edge_clearance.cpp:209-215` for a track, `:434-438` for the pad), under a
comment reading "Edge collisions are allowed inside the holes of castellated pads".

So the token **grants** routing space near the edge. **Discarding it leaves CopperMCP stricter than
KiCad — over-refusal, which is the allowed direction.** The direction-of-error invariant therefore
does *not* require this refusal, and claiming it did was the error.

**The refusal stands on a weaker and narrower argument, stated as the caution it is.** Fabrication
routes the half-holes out of the physical board; `Edge.Cuts` keeps them. Board IR's outline
therefore claims board area that will not exist once the panel is cut — and KiCad's DRC is *more
permissive* exactly there, so ADR-0004's authoritative-DRC backstop is at its weakest precisely
where Board IR would over-claim. That is a reason to be careful with a construct no corpus board
carries, not a geometric necessity, and it is recorded as such so that a later change can overturn
it on cost rather than having to overturn a false invariant claim first.

Two consequences of the accept are named rather than left implied, because both are constraints and
a reading of "fabrication annotations are inert" has to survive them:

- **`HEATSINK` suppresses a DRC test.** `DRC_TEST_PROVIDER_COURTYARD_CLEARANCE` returns early for a
  heatsink pad, exempting it from `DRCE_PTH_IN_COURTYARD`/`DRCE_NPTH_IN_COURTYARD`. That is a DRC
  verdict, produced by KiCad from the unmodified file, and courtyards convert here as
  OVER-approximating obstacles in either case, so nothing this adapter claims moves.
- **The property is nameable in a custom DRC rule.** `pad.cpp:4110` registers it as
  `Fabrication Property`, and KiCad's own shipped rule help conditions a `zone_connection`
  constraint on `'Heatsink pad'`. This is D-184's problem again and takes D-184's answer:
  `.kicad_dru` has never been parsed here (ADR-0005), no rule expression is evaluated anywhere, and
  `kicad_cli.py` — the one surface that honours a custom rule — hands the original board bytes and
  the project's own `.kicad_dru` to KiCad.

**The with/without equality is reported as what it is, and is not the acceptance argument.** A board
carrying an accepted token converts to content equal to the same board without it in every field but
`source.revision`. That equality holds *by construction* for any accepted value — it would hold
identically if `pad_prop_castellated` were admitted, because the converter propagates nothing. It
establishes that acceptance changes no modelled geometry, that the schema is untouched, and that the
goldens are frozen. It establishes nothing about safety. D-178 and D-184 each recorded a version of
this trap after using the argument anyway; this ADR states it up front instead.

## Consequences

- **A pad refusal names its field for every head KiCad can write.** Twenty-six sentences, all
  pinned. The unactionable sentence survives only for a head KiCad cannot produce.
- **One more board converts on the survey corpus: 12 of 18 saves → 13 of 18.** Say which
  denominator: the tree holds **18 saves but only 17 distinct board contents**, because two saves
  are byte-identical and the runner's derived-stem exclusion does not catch it. By distinct content
  the same result reads 12 of 17 → 13 of 17; the duplicated pair is not among the boards that move,
  so the delta is one either way. Measured conversion-only against the runner's own board selection
  and adapter entry, with per-board digests taken in a before→after→before order so a mid-run edit
  by the designer would be visible rather than silent. B-103 records it.
- **The discarded token is counted, not dropped in silence.**
  `ConversionResult.unmodelled_pad_property_count` follows `edge_connector_pad_count` (ADR-0096) and
  `unmodelled_board_property_count` (ADR-0094) exactly: a count rather than a diagnostic, because
  every caller reads a non-empty `diagnostics` tuple as a refusal. It counts converted copper pads
  only — validation runs before the aperture skip and counting after it, which is why they are two
  steps — and the newly-converting save reports 29. R-141's caveat carries over unchanged: the count
  is in-process, reaches no MCP contract, and from a client the discard is still silent.
- **A second board advanced its refusal rather than converting, and that is not a win.** It now
  stops at `pad field 'options' is unsupported`, one construct deeper. Conversion refuses on the
  first error, so a refusal names the frontier and never the depth; this is the fifth time in three
  days that clearing a blocker has exposed another behind it. It is reported as an advance, is not
  counted anywhere as a conversion, and needs no new issue: the construct behind it is the
  custom-shape SMD pad already filed as #153.
- **The refusal surface widens by one accepted head.** `property` joins the pad allowlist, so its
  value is validated *before* the aperture-pad skip — otherwise a stencil opening would be a way to
  carry an unvalidated, possibly castellated, token past every check. ADR-0091 learned this for
  `zone_connect`; the test is repeated here for `property`.
- **Circuit Scene is deliberately not widened, and this is the D-184 check run rather than skipped.**
  Accepting root board properties silently broke the one surface whose job is to disclose every
  board-author-controlled string, because that surface had a branch resting on "the adapter refuses
  any board carrying one". `circuit_scene.py` collects root and *footprint* properties and does not
  collect **pad** properties — and here that is correct rather than an oversight, because a pad
  property is not author-controlled text. Its accepted domain is seven fixed tokens compared by
  equality against an adapter table; a converted board's pad property is always one of seven strings
  this repository wrote, so there is nothing a disclosure surface could reveal that the table does
  not already say. A root property's key and value are arbitrary strings, which is why that one had
  to be collected.
- **The consumer sweep is narrower than "complete", and is now stated as what it is.** It covers
  every `PAD_PROP` enumerator literal plus the accessor under the receiver names `pad` and `aPad`.
  Review found a third spelling — `a->GetProperty()` at `drc_test_provider_library_parity.cpp:371`,
  a DRC report comparing the pad against its library footprint. It is inert for Board IR, so the
  accept survives; the completeness claim did not.
- **No message echoes a board byte.** The one token a refusal names is emitted from the
  `_REFUSED_PAD_PROPERTY` constant after an equality test, exactly as SEC-136's copper-graphic table
  does. The standing regression is extended with a pad-property case.
- **The refusal of `pad_prop_castellated` is a caution, not an invariant, and the record says so.**
  Anyone reading this ADR to decide whether to accept the value should start from "it is sound and
  deferred on cost", not from "the invariant forbids it". A first revision of this ADR said the
  latter, in eight places including a user-facing refusal message, and every one is corrected.
- **The acceptance is a standing risk, recorded as R-144.** It rests on a sweep of one upstream tree
  on one date. A future KiCad that gives any accepted value geometric meaning — most plausibly
  `PRESSFIT`, whose hole tolerance is the kind of thing a filler or router could one day read —
  makes this unsound, and nothing in the repository will notice.

## Alternatives considered

**Name `property` only, and leave the other nineteen heads generic.** This is what issue #152
literally asks for and it was rejected as the same partial fix twice. PR #135 named seven and the
eighth head produced an identical issue three weeks later; naming the eighth would queue up the
ninth. The nineteen cost nothing to name because none of them changes a verdict.

**Refuse `property` by name and decide the construct separately.** Cheap, honest, and it would have
closed the message half of #152. Rejected because the evidence needed to name it responsibly — that
`property` is a fabrication annotation and not a geometry field — is most of the evidence needed to
decide it, and splitting the two would have meant reading the same KiCad sweep twice.

**Accept only `pad_prop_heatsink`, the one value on the corpus.** Rejected as arbitrary. The table
has to be closed over all eight tokens regardless, and `BGA`, `TESTPOINT`, the two fiducials,
`MECHANICAL` and `PRESSFIT` are provably as inert as `HEATSINK` by the same sweep. A subset chosen
by what one private tree happens to carry is not a decision on the merits.

**Model the property as a `Pad` field.** The coherent long-run shape, deferred on cost exactly as
ADR-0091 deferred `PadZoneConnection`: a new field in `Pad` and `canonical._pad()` moves
`BOARD_IR_V02_SNAPSHOT_DIGEST` and cascades into the route candidate, placement candidate, bundle,
scene, render, job, manifest, export and attestation identities — a Board IR schema version spent on
a field nothing reads. `pad_prop_castellated` becomes acceptable in the same change, because a
modelled exclusion region can be honoured rather than discarded.

**Accept `pad_prop_castellated` and model its hole as an all-net obstacle.** **Deferred on cost, and
explicitly not rejected on soundness** — the first revision of this ADR rejected it, and that
rejection inherited the inverted mechanism above. An all-net obstacle over the hole is an
*over-approximating* obstacle, which is the safe direction by this project's own convention, so the
narrower accept is sound. What it costs is a `Pad`-adjacent obstacle surface and its identity
cascade, spent on a construct **no board in the corpus carries** — so it would ship untested against
real hardware, which is the same reason ADR-0091 gave for deferring `PadZoneConnection`. It becomes
the obvious change the moment a real board carries one, and it does not need this ADR overturned
first.
