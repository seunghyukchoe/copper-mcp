# ADR-0096: An edge-connector pad converts as an SMD pad, and the discarded token is counted

- Status: Accepted
- Date: 2026-08-12
- Owners: `@seunghyukchoe`
- Related: [Issue #138](https://github.com/seunghyukchoe/copper-mcp/issues/138), ADR-0004,
  ADR-0013, ADR-0021, ADR-0076, ADR-0090, ADR-0091, ADR-0092,
  [KiCad `connect` pad research](../research/kicad-connect-pad-attribute-v1.md)

## Context

A KiCad pad header's second positional token is its attribute, and one of the four values it may
take is `connect` — `PAD_ATTRIB::CONN`, the edge-card connector finger. Board IR refused it. One
real board, `phono-preamp/tier1-rev-a`, carries two of them on one footprint, and after ADR-0091
(`zone_connect`) and ADR-0092 (net ties) it was that board's last blocker.

Adding `"connect": PadKind.<something>` to the adapter's kind table would have made the board
convert in one line, and that is precisely why it was filed as an issue rather than shipped as a
patch. `PadKind` is published into `canonical._pad()`, so it is inside `BOARD_IR_V02_SNAPSHOT_DIGEST`
and every address derived from it, and it is enumerated as a closed `enum` in the **published**
`schemas/board-ir/0.2.0.schema.json`. A one-line map entry is a contract decision wearing a
dictionary's clothes.

### What `connect` provably is

Established by enumerating and reading **every** occurrence of `PAD_ATTRIB::CONN` in KiCad's
source outside the foreign-format import plug-ins — 35 occurrences across 17 files at commit
`42cc8ba`. Every citation is in the [research note](../research/kicad-connect-pad-attribute-v1.md).

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

Exactly three things differ, and all three sit outside what a Board IR `Pad` claims:

1. **Solder paste** — a paste layer on a `CONN` pad raises `DRCE_PADSTACK` (`pad.cpp:3252-3257`),
   after which control falls through into the `SMD` case. Board IR models no paste layer.
2. **Gerber aperture attribute** — `CONNECTORPAD` rather than `SMDPAD_CUDEF`
   (`plot_brditems_plotter.cpp:206-227`). CopperMCP emits no Gerber.
3. **Edge.Cuts clearance DRC exemption** — a `CONN` pad is skipped by the board-edge clearance
   test, grouped with `PAD_PROP::CASTELLATED` (`drc_test_provider_edge_clearance.cpp:431-439`).
   A finger is meant to reach the edge.

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
- **The distinction is genuinely gone from the IR.** A consumer reading `kind == "smd"` cannot
  recover that the designer wrote `connect`, and one generating fabrication output would put paste
  on a finger. R-141 carries that; the count is the only signal.
- `phono-preamp/tier1-rev-a` converts. The measured corpus effect is recorded in the benchmark
  ledger, not claimed here.

## Alternatives considered

- **Add a `PadKind.EDGE_CONNECTOR` member.** Rejected, and the cost is much sharper than the
  generic "a new member is a schema change". `BOARD_IR_SCHEMA_VERSION` is written into the
  canonical payload (`canonical.py:570-571`), so it is inside every Board IR digest. That forces a
  choice between two bad options. Widening the enum **in place** at `0.2.0` corrupts a published
  contract: a consumer holding `0.2.0` was promised a closed three-value domain and would reject a
  snapshot CopperMCP calls valid, silently, at a version that did not change. Bumping to `0.3.0`
  instead moves `BOARD_IR_V02_SNAPSHOT_DIGEST` and its byte count and cascades into the route
  candidate, layered candidate, placement candidate, bundle, scene, render, job, manifest, export
  and attestation identities — every content address in the repository — for a member **nothing
  reads**: no router, no placer, no scene, no DRC path branches on `PadKind` other than the
  `Pad` invariants themselves. It would also need the schema's `if kind == "smd" then drill is
  null else drill is positive` clause rewritten, and the matching `Pad.__post_init__` invariants,
  or a `connect` pad would be required to carry a drill it cannot have. That is ADR-0091's
  rejected `PadZoneConnection` alternative with a larger blast radius and the same conclusion:
  when a surface needs the distinction, that is the change to make, and it should spend a schema
  version on a field with a consumer.
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
- [Decision ledger D-186](../ledgers/decision-ledger.md),
  [Risk register R-141](../ledgers/risk-register.md)
