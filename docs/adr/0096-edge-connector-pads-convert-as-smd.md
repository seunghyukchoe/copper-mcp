# ADR-0096: An edge-connector pad converts as an SMD pad, and the discarded token is counted

- Status: Accepted (amended 2026-08-12 after adversarial review of PR #149; see D-187)
- Date: 2026-08-12
- Owners: `@seunghyukchoe`
- Related: [Issue #138](https://github.com/seunghyukchoe/copper-mcp/issues/138), ADR-0004,
  ADR-0013, ADR-0021, ADR-0076, ADR-0090, ADR-0091, ADR-0092,
  [KiCad `connect` pad research](../research/kicad-connect-pad-attribute-v1.md),
  [D-187](../ledgers/decision-ledger.md), [R-142](../ledgers/risk-register.md)

## Context

A KiCad pad header's second positional token is its attribute, and one of the four values it may
take is `connect` — `PAD_ATTRIB::CONN`, the edge-card connector finger. Board IR refused it. One
real board, `phono-preamp/tier1-rev-a`, carries two of them on one footprint, and after ADR-0091
(`zone_connect`) and ADR-0092 (net ties) it was that board's last blocker.

Adding `"connect": PadKind.<something>` to the adapter's kind table would have made the board
convert in one line, and that is precisely why it was filed as an issue rather than shipped as a
patch. `PadKind` is published into `canonical._pad()`, so a pad's *kind value* is inside
`BOARD_IR_V02_SNAPSHOT_DIGEST` and every address derived from it, and the set of legal kinds is
enumerated as a closed `enum` in the **published** `schemas/board-ir/0.2.0.schema.json`. A
one-line map entry is a contract decision wearing a dictionary's clothes.

### What `connect` provably is

Established by two sweeps of KiCad's source outside the foreign-format import plug-ins: every
occurrence of `PAD_ATTRIB::CONN` — 35 across 17 files — and, because that sweep is structurally
blind to sites that test `== PAD_ATTRIB::SMD` *alone* where a `CONN` pad silently takes the other
branch, every occurrence of `PAD_ATTRIB::SMD` as well. The second sweep was added after
adversarial review of PR #149 found a site the first had missed; the blind spot is recorded in
the [research note](../research/kicad-connect-pad-attribute-v1.md), which carries every citation.
A test on `PTH` or `NPTH` cannot separate the two — it puts both in the same branch, checked
rather than assumed — so the union of the two sweeps is complete for direct attribute comparisons
in `pcbnew/`, and complete for nothing beyond that.

`PAD_ATTRIB` is a closed four-member enum (`padstack.h:96-105`) in bijection with the four header
tokens; `parsePAD` rejects anything else outright. `CONN` is documented in the enum's own comments
as an SMD pad that is absent from the solder-paste layer, carries a distinct Gerber attribute, and
is intended for edge-card connectors.

Every subsystem that could give it different *copper* gives it none:

- **Connectivity.** `connectivity_items.cpp:164-176` puts `SMD`, `NPTH` and `CONN` in one case
  that pins the connectivity item to a single copper layer. A `CONN` pad is a first-class
  electrical connection point, exactly as an `SMD` pad is.
- **Routing.** `pns_kicad_iface.cpp:1631-1648` gives `CONN` and `SMD` one shared case producing
  one solid on the pad's single copper layer.
- **Layers and hole.** `pad.cpp:1626-1641` trims both to at most one copper layer;
  `…_sexpr_parser.cpp:6433-6437` and `pad.cpp:2886-2891` force the drill to zero for both. The
  pad-properties dialog states in its own comment that the two are the same type of pad, differing
  only in a default non-technical layer set (`dialog_pad_properties.cpp:2143-2152`).

**At least ten** things differ — a lower bound, not an enumeration — and every one sits outside
what a Board IR `Pad` claims. The load-bearing classes:

1. **Solder paste** — a paste layer on a `CONN` pad raises `DRCE_PADSTACK` (`pad.cpp:3252-3257`),
   after which control falls through into the `SMD` case. Board IR models no paste layer.
2. **Gerber aperture attribute** — `CONNECTORPAD` rather than `SMDPAD_CUDEF`
   (`plot_brditems_plotter.cpp:206-227`). CopperMCP emits no Gerber.
3. **Pick-and-place exclusion** — `FOOTPRINT::HasThroughHolePads` (`footprint.cpp:4451-4460`)
   tests `!= PAD_ATTRIB::SMD`, so a `CONN` pad makes its whole footprint count as through-hole,
   and `place_file_exporter.cpp:145` drops it from the position file under "exclude all TH". This
   is the site the first sweep missed, and it is the reason the count is now a lower bound.
4. **Edge.Cuts clearance DRC exemption** — a `CONN` pad is skipped by the board-edge clearance
   test, grouped with `PAD_PROP::CASTELLATED` (`drc_test_provider_edge_clearance.cpp:431-439`).
   A finger is meant to reach the edge.
5. **A distinct value in the property system** — `PAD_DESC` (`pad.cpp:3665-3671`) maps `CONN` to
   `Edge connector`, so a *user-authored* KiCad DRC rule expression can select on it.
6. **Reporting and UI**, four more: footprint pad tallies (`footprint.cpp:1687-1700`, where a
   `CONN` pad counts as neither SMD nor THT), a board-statistics row
   (`board_statistics_report.cpp:112-114`), the clearance inspector's layer pick
   (`board_inspection_tool.cpp:818-833`), and the footprint editor's pad-area readout
   (`pad.cpp:2195-2205`). One further site (`pad.cpp:3229`, a `PAD_PROP::BGA` padstack error) is
   unreachable from CopperMCP, which refuses any pad carrying a `PAD_PROP`.

Three of these — 3, 5 and the statistics row — change output a user can see, and the reason that
does not become an exception is stated in the decision below: the output is produced by *KiCad*,
from a file in which the `connect` token survives.

**Plating is not a pad attribute in KiCad at all.** The issue's hypothesis that gold fingers carry
a plating semantic does not survive contact with the source: `PAD` has no plating field, the only
plated/unplated distinction is `PTH` versus `NPTH` and concerns the *hole*, and a `CONN` pad has
none. Surface finish lives in the board stackup. There is no plating distinction to lose.

### Direction of error

- **Obstacle (must over-approximate).** A `connect` pad is real copper on one layer with a shape,
  a size and a position. The SMD envelope covers exactly that copper, so nothing is lost.
- **Connectivity (must under-approximate).** KiCad's own connectivity engine builds the identical
  single-layer item for both attributes, so claiming attachment at a `connect` pad claims exactly
  what KiCad claims and no more. Pad-to-pour attachment continues to come only from verified fill
  (ADR-0021), unchanged.
- **Board outline (must under-approximate).** The edge-clearance exemption is the one difference
  that is not fabrication output, and it cannot be exploited. CopperMCP derives no edge clearance
  of its own — authoritative DRC is KiCad's (ADR-0004), which applies the exemption itself — and
  the routers reject a path endpoint outside the outline inset by half the track width, so a
  finger running past the outline fails to route rather than authorising copper off the board.
  Over-refusal, which is the safe direction.

## Decision

**A `connect` pad converts as `PadKind.SMD`. `PadKind` gains no member, no published schema
changes, and the discarded token is reported as `ConversionResult.edge_connector_pad_count`.**

### The accepted subset, as a closed field table

Stated as a table and not as prose, because ADR-0092 recorded what prose costs: its first version
described the net-tie subset in prose and two accepted constructs were not covered by it, one of
them running in the forbidden direction. A `connect` pad is admitted when, and only when, every
row below holds. Everything else is the refusal it already was.

| Field | Accepted | Refusal when not |
|---|---|---|
| header token 2 (kind) | exactly `connect` | `pad kind is unsupported` |
| header token 3 (shape) | `circle`, `rect`, `oval`, `roundrect` | `pad shape is unsupported` |
| `layers` | at least one copper layer, by the existing `_layer_ids` rule — identical to the `smd` path | `pad references no copper layer and is not a paste or mask aperture` |
| `drill` | absent | `SMD pads cannot carry a drill` (`geometry.invalid`) |
| `net` | present or absent; both legal, as for `smd` | — |
| `zone_connect` | `1`, `2` or `3` only, per ADR-0091 | that ADR's two refusals, unchanged |
| `remove_unused_layers` | absent, or `no` | `pads with removed copper layers are unsupported` |
| every other child head | the existing closed pad allowlist, unchanged | `pad field '<name>' is unsupported`, or the allowlist refusal |
| aperture form (no copper layer at all) | **not accepted** — the aperture skip tests the source token and requires literally `smd` | `pad references no copper layer and is not a paste or mask aperture` |

The last row is the only place the change is narrower than "treat the two tokens alike", and it is
deliberate. Before this ADR every `connect` pad refused, so a copper-less one keeps refusing —
unchanged behaviour, not a new restriction. KiCad's `IsAperturePad()` is attribute-independent
(`pad.h:562-565`), so such a pad is representable; but the paste-bearing form is the combination
KiCad's own padstack test calls an error, none is observed in the corpus, and over-refusal is the
conservative direction. Widening it later would be sound by the same no-copper argument the `smd`
aperture rests on.

### What the count is, and is not

`ConversionResult.edge_connector_pad_count` follows the D-157/ADR-0090 measured-field pattern: a
count, not a diagnostic, because every caller of `parse_kicad_bytes` treats a non-empty
`diagnostics` tuple as a refusal and a warning would refuse the board this change exists to admit.
It counts **converted** pads only — after the aperture skip and after every refusal — so it can
never report copper that was not modelled. It is a number, not a set: nothing tells a caller
*which* pads were edge connectors.

**And it is readable one layer deep only. From an MCP client the discard is silent.** The count
lives on `ConversionResult`, which is an in-process return value. `BoardIrSummary`
(`board_ir_service.py:100-181`) carries no field for it, so it reaches no MCP contract, no CLI
output and no Circuit Scene. This is not special to this count — `unmodelled_group_count` and
`max_roundrect_rounding_nm` are equally invisible there, and that is a pre-existing gap rather
than one this decision opens — but the first version of this ADR said the distinction was
"discarded loudly", and against the surface most consumers actually use, it is not. R-142 carries
it, and no surface is widened here to fix it, because doing so is a published-contract change of
its own and belongs in its own decision.

**What genuinely bounds the loss is the write path, and it is proved rather than asserted.** Both
patch adapters are source-preserving splices that rewrite only pose and route geometry, never a
pad header, so the `connect` token survives in the `.kicad_pcb` byte-for-byte — and therefore
KiCad's own DRC, position file, Gerbers and rule expressions all still see an edge connector, no
matter what CopperMCP did or did not tell its caller.
`test_a_placement_splice_leaves_an_edge_connector_pad_token_intact` renders a real placement move
over a board whose *moved* footprint carries `connect` pads — the hardest case the splice offers,
since it rewrites that footprint's own `at` and every one of its pads' — and asserts the tokens
are still there afterwards.

### Why the equality test proves what it proves, and nothing more

A test asserts that the same pad written `smd` and written `connect` produces identical Board IR
content in every field but `source`. **That equality is not evidence of soundness.** It holds by
construction for any two tokens the kind table sends to one member — it would hold identically if
`connect` had been mapped to `THROUGH_HOLE`. It establishes three things and no others: that the
mapping really is to `SMD`, that no converted field carries the source token, and that no pinned
identity moves for a board that gains one. Soundness rests entirely on the KiCad-domain argument
above. This is the same disclaimer ADR-0091 carries, for the same reason: that proof has been
mistaken for a safety argument twice, and the constant-partition test is what actually catches a
wrong table.

### A refusal table with nothing left to name

`_UNMODELLED_PAD_KINDS` is deleted rather than left as an empty dict. Its one entry was `connect`;
`PAD_ATTRIB` is closed at four tokens and all four are now modelled, so no documented-but-unmodelled
pad kind exists. A lookup that can never miss its default is the same dead code ADR-0091 found
behind the pad-field allowlist — seven named refusals that could not fire — and the fix there was
to delete the unreachable arrangement, not to keep it. A token reaching the refusal today is not a
documented pad kind at all, refuses unnamed, echoes no board bytes, and still carries an indexed
locator saying which pad.

**Reachability was checked before it was changed, not assumed.** The kind token is a *positional
atom*, so it is read past `_reject_unknown_children` (which constrains child heads) and past
`_validate_direct_atoms` (which constrains atoms beyond position three). The refusal fired: the
real board produced `unsupported.construct` / "edge-connector pads are unsupported" at
`kicad_pcb.footprint[33].pad[1]`. It did not have ADR-0091's defect.

### What the mutation run found

A manual run over 23 chosen mutants of the new logic — the kind table, the aperture skip's token
test, the count, the refusal that replaced the deleted table, and the `ConversionResult`
validation — killed 22. This is a claim about those 23, not about the mutation space.

Two results are worth recording rather than just counting:

- **A fifth key silently added to the kind table survived every behavioural test.** It admits a
  token no board carries, so no board changes. That is why the mapping is now a module constant
  with a test pinning its **whole domain** against KiCad's four tokens — the same shape of gap
  ADR-0091's constant-partition test closed for `zone_connect`, found the same way.
- **Moving the count ahead of the aperture skip is an equivalent mutant**, and provably so rather
  than by inspection. No `connect` pad is ever aperture-skipped, because the skip requires the
  literal `smd` token (and the mutant that widens it is killed); and every later refusal discards
  the whole result, because `ConversionResult` structurally refuses to carry a count without a
  snapshot (also pinned). The ordering is therefore unobservable through the public surface,
  defended structurally instead of behaviourally, and is left as written for readability.

## Consequences

- **No pinned identity moves.** `tests/test_golden_identities.py` passes unchanged. This is a
  property of the design, not luck: `PadKind` gains no member, `Pad` gains no field,
  `canonical._pad()` is untouched, `BOARD_IR_SCHEMA_VERSION` stays `0.2.0`, and no board that
  converted before converts differently. Only boards that previously refused change outcome.
- **No published schema changes.** `schemas/board-ir/0.2.0.schema.json` (its `kind` `enum` and
  both `if`/`then` clauses keyed on `"smd"` and `"np_through_hole"`),
  `schemas/board-ir/0.1.0.schema.json`, and the `kind` `Literal` on
  `PadGeometryContract` in `src/copper_mcp/mcp_contracts.py` all keep the same three-value
  pad-kind domain, and a converted `connect` pad validates against every one of them because it
  emits `"smd"` with a null drill.
- **`ConversionResult` gains a field.** A caller constructing one positionally is unaffected
  (it is keyword-defaulted and appended last), but a caller exhaustively destructuring one gains
  a field. No migration note is required and none is written, because no version constant moves
  and no persisted artifact stops verifying; the `CHANGELOG` entry is the whole notice.
- **The distinction is genuinely gone from the IR, and the count only partly compensates.** The
  count is invisible to every published surface (above), so an MCP client sees nothing at all. A
  consumer reading `kind == "smd"` cannot
  recover that the designer wrote `connect`, and one generating fabrication output would put paste
  on a finger. R-141 carries that; the count is the only signal.
- `phono-preamp/tier1-rev-a` converts. The measured corpus effect is stated in prose in
  decision-ledger row D-186; **no benchmark-ledger entry accompanies it**, because the runner's
  output derives from a private corpus and is deliberately not committed.

## Alternatives considered

- **Add a `PadKind.EDGE_CONNECTOR` member.** Rejected — but on a narrower argument than this
  ADR first gave, and the correction matters enough to state before the reasoning.

  **The retracted claim.** The first version of this record said `BOARD_IR_SCHEMA_VERSION` "sits
  inside every Board IR digest", so a bump to `0.3.0` would move `BOARD_IR_V02_SNAPSHOT_DIGEST`
  and its byte count and cascade into every content address in the project. **That is false, and
  it was reasoned rather than measured.** The snapshot digest is taken over `_content_payload`
  (`canonical.py:486`), which carries no schema version; the version appears only in the
  *envelope* (`canonical.py:571`). Setting `BOARD_IR_SCHEMA_VERSION = "0.3.0"` in a throwaway tree
  and recomputing gives a **byte-identical digest** (`sha256:157661bf…`) and an **identical
  encoded length** (4,280 — "0.3.0" and "0.2.0" are the same width). Downstream identities bind
  `base_revision = snapshot_digest`, so there is no cascade. See D-187.

  **What a bump actually costs**, measured by bumping the constant and running the suite rather
  than by reading the code: the committed envelope fixture
  `tests/fixtures/board-ir-v0.2/schema-valid.json` must be regenerated (its `schema_version`
  string, not its digest); `codec.py:843` refuses every persisted `0.2.0` envelope, which is a
  real migration for anyone storing snapshots; `BoardIrSummary.ir_schema_version` and a few
  committed benchmark artifacts carry the string. Bounded and mechanical, not a cascade.

  **Why the decision still stands on the true costs.** Three reasons survive, and one of them is
  by itself sufficient:

  1. **Widening the enum in place at `0.2.0` is still unacceptable, and that is the cheap-looking
     option.** A consumer holding the published `0.2.0` schema was promised a closed three-value
     domain; it would reject a snapshot CopperMCP calls valid, silently, at a version that did not
     move. Nothing about the digest finding touches this. It is on its own decisive against the
     no-bump form of the alternative.
  2. **Nothing reads a fourth member.** No router, placer, scene, DRC or apply path branches on
     `PadKind` outside `Pad`'s own invariants. Spending a schema version — and imposing
     `codec.py`'s refusal of stored `0.2.0` envelopes on real users — to record a distinction none
     of their tooling consumes is the cost ADR-0091 declined to pay for `PadZoneConnection`, on
     the same reasoning. Reversing that precedent should take a consumer, not a preference.
  3. **The alternative carries a correctness hazard the digest finding does not touch.** The
     schema's `if kind == "smd" then drill is null else drill is positive` clause and the matching
     `Pad.__post_init__` invariant would both need rewriting, or a `connect` pad would be required
     to carry a drill it cannot have.

  **And the honest counterweight, recorded rather than buried.** Two things found in review make
  the case *for* eventually modelling the distinction stronger than this ADR first implied: KiCad
  really does consume it outside fabrication metadata (the pick-and-place exclusion, and a
  property-system value user-authored DRC rules can name), and CopperMCP's own disclosure is
  weaker than claimed (R-142). What holds the decision here is that nothing in *this* repository
  reads it today, and that the write path preserves the token so no user artifact is degraded
  meanwhile. The bump is now *known* to be cheap — digest-stable, one fixture, one codec gate —
  which is itself a reason not to pre-pay for it: the option stays open at a price we have
  measured instead of guessed.
- **Keep refusing.** Rejected. The refusal is not supported by either direction-of-error rule
  once the source has been read, and ADR-0091 named this failure mode: a refusal without an
  argument is how a tool acquires superstitions.
- **Map to `SMD` and say nothing.** Rejected. The conversion discards a token the designer wrote,
  and D-178 exists because discarding a distinction silently is the failure mode. The count is the
  cheapest disclosure that does not refuse the board.
- **Emit a `warning`-severity diagnostic instead of a count.** Rejected for the reason ADR-0090
  gives: every caller treats a non-empty `diagnostics` tuple as a refusal, so a warning is a
  refusal with extra steps.
- **Refuse write-back for a board carrying a `connect` pad**, the way ADR-0092 refuses net-tie
  boards. Rejected as unnecessary: both patch adapters are source-preserving splices that rewrite
  only `at` expressions and route/via geometry, so the `connect` token survives byte-identically.
  ADR-0092's refusal exists because net-tie copper carries *derived* identities; a `connect` pad
  carries its own `uuid` and does not.
- **Widen the aperture skip to `connect` in the same change.** Rejected as scope: it is sound by
  the no-copper argument, unobserved in the corpus, and unrelated to the blocker. Refusing costs
  nothing measurable and keeps the change's blast radius exactly "copper-bearing `connect` pads
  now convert".

## References

- [ADR-0004](0004-authoritative-kicad-drc.md), [ADR-0021](0021-zone-fill-authority.md),
  [ADR-0090](0090-root-level-board-groups.md),
  [ADR-0091](0091-attaching-pad-zone-connect-overrides.md),
  [ADR-0092](0092-net-tie-copper-as-netless-obstacle.md)
- [KiCad `connect` pad research](../research/kicad-connect-pad-attribute-v1.md)
- [Decision ledger D-186](../ledgers/decision-ledger.md) and its correction
  [D-187](../ledgers/decision-ledger.md), which carries the retracted digest-cascade mechanism,
  the measured cost of a schema bump, and the divergence recount
- [Risk register R-141](../ledgers/risk-register.md) and
  [R-142](../ledgers/risk-register.md), which carries the in-process-only reach of the count
