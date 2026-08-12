# Changelog

All notable changes are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.7.0] - 2026-08-12

Upgrading from 0.6.0: see the [0.7.0 migration notes](docs/migrations/copper-mcp-0.7.0.md).
`ROUTER_VERSION` advances to `astar-grid/0.7.0`, so every stored route candidate and bundle
identity must be re-derived; one Board IR conversion diagnostic code becomes ten discriminated
ones and one routing budget code becomes three; every integer environment variable now requires an
unambiguous ASCII spelling; a truncated Circuit Scene withholds a whole kind instead of returning
an empty array; every root Board IR refusal moves off the constant locator `kicad_pcb.unsupported`;
seven pad refusals that could never fire now fire with their own text; and the published
`drc-summary` schema enforces the `passed` and `clean` derivations in both directions.

### Changed

- **An `off_grid` route refusal now says which pad, what pitch, and how far.** It previously said
  only "the pad-center delta is not divisible by the requested grid step", which names the rule and
  not the board. The `not_routed` diagnostic carries a typed `off_grid` object — the off-lattice
  pad, its lattice anchor, `grid_step_nm` in use, the signed per-axis nanometres from the nearest
  lattice line to the pad centre, and `largest_representable_step_nm`, the greatest common divisor
  of the two pad-centre deltas — the last being `null` on the rare board whose pads sit near
  opposite legal coordinate extremes, where the divisor exceeds the JSON-safe integer range and is
  withheld rather than clamped. Every other diagnostic carries `off_grid: null`, never an empty
  object and never a zero, because a refusal that measured no lattice has nothing to say about one.
  **The key is optional with a `null` default, so a diagnostic payload you recorded before this
  release still validates**; an `off_grid`-coded payload that lacks it is still refused, and so is
  one whose values contradict each other — both checks run in the published schema as well as at
  runtime, so validating against the schema alone accepts nothing the service would refuse to build.
  **Routing semantics do not change**: the lattice, the search, `ROUTER_VERSION` and every published
  content address are untouched, and all 385 real-board verdicts are byte-identical before and
  after. `largest_representable_step_nm` states representability and never routability — measured on
  18 real-board refusals, re-previewing each at exactly that step with the node budget at its
  ceiling routes **none** of them. ([ADR-0093](docs/adr/0093-actionable-off-grid-refusals.md),
  [D-181](docs/ledgers/decision-ledger.md), [B-100](docs/ledgers/benchmark-ledger.md),
  [SEC-134](docs/ledgers/security-ledger.md),
  [off-grid lattice refusal research](docs/research/off-grid-lattice-refusal-v1.md), #136)

- **Route candidate identities move, and stored ones stop verifying.** `ROUTER_VERSION` advances to
  `astar-grid/0.7.0` because the router's default budgets and obstacle model changed. **No path
  geometry changed anywhere** — the two-pad golden fixture, the NE5532 fixture, and all twenty
  SimpleRouteJson corpus boards replay with identical vertices, wire length and bend counts. The
  addresses move because a candidate records the settings it was computed under and the work its
  search performed, and both moved. A caller holding a `candidate_id`, a `bundle_id`, or an exported
  candidate from an earlier version must re-run the preview; the value will not reproduce, and the
  version bump is what says so. Candidates recorded under `astar-grid/0.4.0` through `0.6.0` still
  select their historical search behaviour for replay, but their recorded settings predate
  `region_margin_nm`, so on a board larger than the routing region they do not reproduce
  byte-for-byte. ([ADR-0089](docs/adr/0089-region-scoped-obstacle-model.md), #128)

### Fixed

- **CopperMCP's published DRC schema rejected CopperMCP's own published DRC payload.**
  `schemas/drc-summary.schema.json` sets `"additionalProperties": false` and never declared
  `clean`, while `DrcSummary.to_dict()` has always emitted it -- so every payload from
  `attestation.py`, `post_placement_observation.py` and `route_preview.py` failed validation
  against the schema a third party would download. The schema is completed rather than the field
  dropped, and the two are not interchangeable: `clean` **is** derivable from the six count fields
  the schema already declares, but it is also already published -- `RouteDrcSummaryContract` is a
  closed MCP contract that declares `clean` as required and validates it against those same counts,
  so removing it from `to_dict()` would break the wire contract the model feeds, at a pinned
  `schema_version` of `1.0`, for a field whose sibling `passed` is derived from the same counts and
  declared. It is declared **required**, matching the MCP contract, so the two published contracts
  cannot disagree in the opposite direction. **No content address moves**: schema files are hashed
  into no digest, `to_dict()` is unchanged byte for byte, and `tests/test_golden_identities.py`
  passes unmodified. ([D-180](docs/ledgers/decision-ledger.md),
  [R-137](docs/ledgers/risk-register.md), #11)

- **The DRC schema let a board lie about being clean.** Declaring `clean` was necessary but not
  sufficient: typed only as `boolean`, a payload carrying `warning_count: 1` beside `clean: true`
  validated against the published schema, while `DrcSummary.clean` and the closed
  `RouteDrcSummaryContract` both refuse it. A schema-only consumer could therefore accept and
  display a board as clean that CopperMCP's own runtime refuses to call clean -- a false claim
  about a board, reachable through the contract we publish, and a worse failure than the
  `additionalProperties` defect above, which only made a *true* payload fail. `passed` had the
  identical hole. Both are now pinned in **both directions** with `if`/`then`/`else` over `const`
  values: `passed` is true exactly when `error_count` and `unconnected_count` are `0`, and `clean`
  is true exactly when all five counts are `0` **and** `violation_type_counts` is empty -- the last
  condition matters, because a present-but-zero violation type is `passed` and not `clean`. The
  schema is checked against `DrcSummary`'s own answer over a grid exercising each count's
  contribution, four negative fixtures pin the lying payloads, and eight mutations of the
  constraint were each killed. Not expressible in JSON Schema, and stated rather than hidden: the
  model also requires the violation-type counts to sum to
  `error + warning + exclusion + unconnected`. `board-manifest` and `candidate` were checked for
  the same class of gap and carry no derived value.
  ([D-180](docs/ledgers/decision-ledger.md), [R-137](docs/ledgers/risk-register.md), #11)

- **Route preview routed 0 of 385 nets on real boards, and the budget that refused them was
  counting three different things.** Measured read-only against a live audio-project tree, 93 of
  385 previews refused `obstacle_budget_exceeded` at default settings against boards carrying up to
  31,389 segments. Splitting those refusals by the message each raised shows 61 of them were the
  routed net's **own** copper — the model that decides whether a net is *already connected* —
  charged against a budget named for the copper it has to avoid. On a finished board the true answer
  for those nets is `already_connected`, and it was being reported as a work failure. `max_obstacles`
  now meters only foreign selected-layer copper (default 256 → 4,096, maximum 4,096 → 32,768); a new
  `max_net_objects` (1,024, maximum 4,096) meters same-net connectivity and attachment copper, with
  its ceiling set where the pairwise merge's quadratic cost meets the obstacle-check budget rather
  than where boards happen to sit; and the work meter gets its own `obstacle_check_budget_exceeded`
  code instead of borrowing the object budget's. Every one of these refusals now names the budget
  and its configured value — and deliberately not the observed count, which would disclose board
  density. Result on the same corpus, byte-identical sources: `routed` 0 → 14, `already_connected`
  263 → 318, `obstacle_budget_exceeded` 93 → 3, with nothing regressed. **These are 385 independent
  single-net previews, not a board completion figure**: each ran one net at a time on `F.Cu` against
  the same unrouted snapshot, so the 14 routed candidates are not mutually compatible and no subset
  of them may be read as a partial routing of any board. The important number is not the 14 anyway —
  it is the 55 nets that are now correctly reported `already_connected` instead of as a work failure.
  ([D-176](docs/ledgers/decision-ledger.md), [SEC-132](docs/ledgers/security-ledger.md),
  [B-096](docs/ledgers/benchmark-ledger.md),
  [obstacle-budget calibration](docs/research/route-obstacle-budget-calibration-v1.md), #128)

### Added

- **Every JSON Schema published under `schemas/` now has a named proof that it accepts what the
  code really emits.** `tests/test_schema_conformance.py` loads the schema files themselves --
  the artifacts a third party downloads, which no test previously read -- and checks them in three
  layers. Committed fixtures under `tests/fixtures/schema-conformance/` give each of the board
  manifest, candidate and DRC summary schemas one minimal `valid.json` and six focused
  `invalid-<condition>.json` files (missing required field, unexpected property at the root and
  inside a nested object, negative count, malformed SHA-256 identifier, wrong schema version); each
  invalid fixture differs from `valid.json` in exactly one way and `_EXPECTED_REJECTIONS` names the
  precise `(keyword, field)` errors it must produce, so a fixture that drifts and starts failing
  for an unrelated reason fails the suite instead of appearing to still work. Each `valid.json` is
  asserted equal to what the model's `to_dict()` actually publishes, round-tripped through JSON.
  And `_field_parity` compares the schema's declared property names against that emitted key set
  directly -- the comparison that had never been made, and the one that catches the *missing-field*
  defect above without needing a fixture at all. Its limit is recorded in its own test rather than
  left implied: it compares **names**, so it is structurally blind to what a schema says about a
  value, and it reported parity while a payload lying about `clean` still validated. All ten schema
  files are accounted for in
  `_SCHEMA_COVERAGE`, which names the exact test function proving each one and how strong the proof
  is (`emitted_payload`, `committed_artifact`, or `legacy_no_emitter` for `board-ir/0.1.0`, which
  the active codec refuses by design so no live payload exists to check). A completeness test fails
  when a new schema file appears with no entry; a second test fails when a recorded proof is
  renamed or deleted. No new dependency: `jsonschema` is already a version-bounded development-only
  dependency (`jsonschema>=4.25,<5`, in the `dev` extra, not in the runtime `dependencies` list).
  `docs/development.md` documents how to add a fixture.
  ([R-137](docs/ledgers/risk-register.md), #11)

- **A routing region, so the obstacle budget bounds work instead of board size.** A two-pin route
  only interacts with copper near the corridor between its pads, but the router was modelling the
  whole board — 22,244 objects on the densest board measured, for one route. The obstacle model is
  now scoped to the envelope of the routed net's own copper widened by a new `region_margin_nm`
  setting (10 mm by default, 1 nm – 1 m), clipped to the board, and **the search is confined to the
  same region**, which is what makes the scoping sound rather than merely cheaper: a node the search
  can reach is a node inside the region, so copper outside it cannot affect any answer. Measured,
  this is worth a factor of four in the ceiling for equal coverage. A board smaller than twice the
  margin yields a region equal to the board, so small fixtures are unaffected. A search that
  exhausts inside a proper-subset region refuses under a new `no_path_in_region` code rather than
  claiming `no_path` about a board it never modelled. ([ADR-0089](docs/adr/0089-region-scoped-obstacle-model.md),
  [R-133](docs/ledgers/risk-register.md), #128)

- A measured survey of what CopperMCP's downstream surfaces do on real boards, now that real boards
  convert. Issue #116 had moved conversion from 1 of 23 to 10 of 12 at the time this ran
  (2026-08-07); later work in this same release moves it to 11 of the 12 boards in the #116 survey
  set, which is 11 of all 17 boards in the corpus as saved today. This measures step two across five
  read-only surfaces at default settings, per board, with wall clock on every call. Authoritative
  KiCad DRC is the one surface that reported on every board — 12 of 12 reported (3 pass, 0 clean),
  including both boards Board IR refused at that commit; that is a count of DRC runs that returned a
  verdict, not a conversion result. A region-scoped Circuit Scene works on 10 of 10, while 8 of 10
  whole-board requests hit `max_scene_objects` and return empty `vias`, `zones` and `rules` lists
  for boards holding up to 1,003 vias — **that last finding is what #127 fixed later in this same
  release**, so a 0.7.0 deployment does not reproduce it; a truncated kind is now withheld by name
  rather than emptied. Placement preview accepts 991 of 991 footprints with no refusal of any code —
  and 10 of 10 boards refuse the source-preserving render, so none of it can ever be applied. Route
  preview routed 0 of 345 nets, 71 of them refused at the then-default `max_obstacles = 256`, which
  #128 re-derived later in this release; B-088's `off_grid` wall does not appear once here, which at
  the time localised this corpus's constraint somewhere else entirely — B-096 and B-100 later showed
  the lattice class was simply being masked by the obstacle budget, and eight `off_grid` refusals
  appear once that budget is fixed. No board was written, copied, or opened in a live editor, and no
  apply or live-IPC flag was set.

  **Two non-claims travel with these numbers and are restated here rather than left in the ledger.**
  Route preview ran **one layer and one net at a time against the unrouted snapshot**, so the
  candidates are not mutually compatible and this is not a whole-board completion result. Placement
  preview ran **without rules**, so a clean verdict means legal-as-found and not placement quality.
  Appliability was measured by calling the apply path's own pure identity predicate, which proves
  the gate refuses and not that an apply would otherwise have succeeded. One designer's mostly
  four-layer project family is not a random sample of KiCad boards, and the timings are one machine
  and one run.
  ([B-099](docs/ledgers/benchmark-ledger.md),
  [Tier-2 real-board capability survey](docs/research/tier2-real-board-capability-v1.md), #116)

### Fixed

- **An unsupported pad refused with a message naming neither of the two things it could be.**
  `pad kind or shape is unsupported` covered both positional tokens of a pad header, so a caller
  could not tell which one the adapter rejected, nor which construct it was. On the one real
  board in the survey corpus that reaches this refusal the answer is a `connect` pad — KiCad's
  edge connector — and recovering that took reading the board file by hand. Kind and shape are
  now two separate refusals, and an unsupported *kind* that the KiCad format documents is named
  from a closed table (`_UNMODELLED_PAD_KINDS`), under exactly the rule `_UNMODELLED_ROOT_HEADS`
  already follows: the message is a *value from that table*, selected by an equality test against
  the source token and never built from it, so the refusal names the construct without echoing
  one byte of the board. An undocumented token is still refused unnamed, and the indexed locator
  says which pad in both cases. This is the same defect class D-178 repaired for seven pad
  refusals the allowlist had made unreachable — there the control flow was wrong, here the
  control flow always ran and the message carried no information. **Nothing changes about what is
  accepted or refused**: same code, same locator, same set of boards. Modelling an edge-connector
  pad is a separate contract decision and is deliberately not taken here.
  ([#116](https://github.com/seunghyukchoe/copper-mcp/issues/116))

- **A root-level `(group …)` no longer refuses a whole board, and no root refusal is anonymous any
  more.** A `pcbnew` backup of one real KiCad 10 board — 103 footprints, 349 pads, 4 filled zones —
  was refused outright for three editor selections, by a message that named nothing: `root
  expression contains an unsupported semantic construct` at the constant locator
  `kicad_pcb.unsupported`. **As of this entry it unblocks no board that is blocked today**, and the
  timing is worth stating exactly: `B-096` measured that board refusing for this construct on
  2026-08-07, when its then-current save carried the three groups; the designer re-saved it without
  them on 2026-08-08, so today only two `.kicad_pcb.bak-*` backups carry a group and every
  currently-saved `.kicad_pcb` has none. The blocker was real and moved on its own, which is
  precisely why this lands as hardening against a construct KiCad writes whenever a designer groups
  a selection. `group` is a documented root section of the board format, and on the read side an
  *unlocked* one is inert — the proof is an equality rather than an argument: the backup converted
  with and without its three groups differs in exactly one Board IR content field, `source`, whose
  revision is the digest of the source bytes. `outline`, `nets`, `constraints`, `footprints`,
  `pads`, `vias`, `segments`, `arcs`, `zones` and `keepouts` are equal to the nanometre. **A locked
  group is refused, and that is not a detail.** `BOARD_ITEM::IsLocked()` derives an item's lock from
  its parent group, so `(locked yes)` on a group locks every member transitively without any
  member's own s-expression saying so; lock is a hard authorization gate here, so reading one past
  would have converted members at `locked=False` and authorized a move KiCad forbids. Acceptance is
  otherwise conditional and each condition refuses: a group's children are checked against the head
  vocabulary KiCad's writer emits (`uuid`, `locked`, `lib_id`, `members`) and its leading name atom
  is required, so the root allowlist stays closed. The grouping itself is *not* modelled — Board IR
  has no field for "these objects belong together" — so it is recorded rather than dropped in
  silence, as `ConversionResult.unmodelled_group_count`. Write-back stays open for grouped boards
  and that was verified, not assumed: a real placement splice leaves the group's bytes and the whole
  document tail byte-identical. Every root refusal now carries `kicad_pcb.child[N]`, an index
  computed from the parse, and names the construct when it is a documented root section, by looking
  the token up in a closed table and emitting *that table's* literal — the board's own text stays
  untrusted and reaches no message. No content address moves: a board with no group converts
  identically, and a board with one previously produced no snapshot at all.
  ([ADR-0090](docs/adr/0090-root-level-board-groups.md),
  [D-177](docs/ledgers/decision-ledger.md), [R-134](docs/ledgers/risk-register.md),
  [SEC-133](docs/ledgers/security-ledger.md),
  [KiCad board groups](docs/research/kicad-board-groups-v1.md), #129)
- **A pad that asks a copper pour to attach to it no longer refuses the board.** A pad's
  `(zone_connect N)` was refused outright, grouped with `clearance`, `offset` and `primitives`.
  That grouping was wrong about what the field is: those three change the pad's own copper or the
  clearance the router honours, while `zone_connect` derives nothing — it is an input to KiCad's
  own zone filler; nothing else turns it into copper. Read from KiCad 10's filler, the finished
  fill is clipped to the zone's own extents, so poured copper is a subset of the zone boundary for
  *every* value of the field, and the conservative zone obstacle stays conservative regardless.
  Values `1` (thermal relief), `2` (solid fill) and `3` (through-hole thermal, which KiCad resolves
  to `1` or `2` by pad type) all **attach** the pad, so discarding one never turns Board IR's
  `Zone.pad_connection` into a claim of attachment where there is none — the published *mode* can
  end up wrong in either direction, but both readings still answer "attached" — and they are now
  accepted. Value `0` **detaches**, so discarding it could leave Board IR publishing
  `Zone.pad_connection` as `thermal` or `solid` over a pad its designer deliberately isolated; it
  keeps refusing, and so does every value outside KiCad's enum. Acceptance changes nothing that is
  modelled, and a test measures that rather than asserting it: a board carrying `1`, `2` or `3`
  converts to content equal to the same board without it in every field but the source digest, so
  no pinned identity moves and the Board IR schema version does not bump. That equality is a
  no-op and schema-stability measurement, not a safety argument — it would hold for any accepted
  value, `0` included; soundness rests on KiCad's own semantics plus ADR-0021's rule that
  pad-to-pour attachment comes only from verified fill. Separately, the seven other named pad
  refusals —
  `clearance`, `offset`, `options`, `primitives`, `thermal_bridge_angle`, `thermal_bridge_width`,
  `thermal_gap` — were **unreachable**: the pad allowlist refused the same fields first with a
  message naming no field, so the issue that found this quotes a sentence the adapter could not
  emit. Each now says which field it refused, without opening the allowlist by one head.
  ([ADR-0091](docs/adr/0091-attaching-pad-zone-connect-overrides.md),
  [D-178](docs/ledgers/decision-ledger.md), [R-135](docs/ledgers/risk-register.md),
  [KiCad pad zone_connect](docs/research/kicad-pad-zone-connect-v1.md), #124)
- **A board outline assembled from `Edge.Cuts` `gr_line` segments no longer makes the whole board
  permanently unappliable.** Issue #126 measured that both apply gates refused every real board
  that converts, and that on three of them the assembled outline was the *only* derived identity —
  every footprint, pad and copper object was native. The contour now takes a composite native
  identity, `contour:assembled:` plus a hash of the sorted set of its member segments' own uuids,
  produced only when every member carries exactly one native identity and no value repeats within
  the member set; any unresolvable member set still degrades to the revision-derived name every
  source-preserving patch path refuses. Neither apply gate changed by a byte: a reused footprint
  or pad UUID (D-158) still refuses write-back, a `gr_rect` outline still yields its own native
  uuid identity byte-for-byte unchanged, and the preserved invariant — no patch can ever name an
  object whose identity cannot be resolved back to the source file — is mutation-checked from both
  directions. Measured read-only on the twelve-board real corpus: 0 of 11 converting boards passed
  either gate before, 3 of 11 pass both after, and the remaining 8 refuse on UUID reuse exactly as
  intended. Two committed fixtures now draw their outlines with `gr_line` segments so the fixture
  set cannot drift back to the `gr_rect`-only assumption that hid this for 1,900 tests.
  ([ADR-0087](docs/adr/0087-composite-native-identity-for-assembled-outlines.md),
  [D-174](docs/ledgers/decision-ledger.md), [SEC-131](docs/ledgers/security-ledger.md),
  [R-131](docs/ledgers/risk-register.md),
  [Assembled-outline identity](docs/research/assembled-outline-identity-v1.md), #126)
- **A truncated Circuit Scene no longer empties whole object kinds in silence.** A whole-board
  `observe_board_scene` returned `vias: []`, `zones: []` and `rules: []` on real boards holding up
  to 1,003 vias, 5 zones and a net class; eight of the eleven mixer boards that convert hit
  `max_scene_objects`. The scene spent one object budget in one fixed emission order, and segments —
  two orders of magnitude more numerous than any other kind on a real board, and fifth in that
  order — consumed everything, so every kind behind them came back empty. `ceiling_hit` and
  `objects_omitted` were both correct and both in the wrong place: an empty array from a truncated
  scene was byte-identical to one from a board that genuinely has none, and the caller who most
  needed the warning was the one reading the array. The ceilings are now offered to **whole kinds**,
  smallest first with the fixed declaration order breaking ties, so a kind is admitted only if all
  of it fits — every array a scene returns is complete for its region and layer filter, and an empty
  one means the region holds none of that kind. A kind that does not fit is replaced, in its own
  slot, by `{"observation": "withheld_by_ceiling", "ceiling_hit", "objects_omitted"}`: a value of a
  different JSON type carrying a one-value literal, so `if not vias` is false, `len(vias) == 0` is
  false, `vias == []` is false, and iterating it raises. Re-measured read-only over the same corpus,
  all eight truncating boards now withhold `segments` alone and return every via, zone, pad,
  footprint and net class they hold; 11 of 11 bounded regions are unchanged at `objects_omitted: 0`.
  `max_scene_objects` keeps its provisional 2,000 default — the defect was never the ceiling's
  height. ([ADR-0088](docs/adr/0088-complete-or-withheld-scene-kinds.md),
  [D-175](docs/ledgers/decision-ledger.md), [R-132](docs/ledgers/risk-register.md),
  [migration](docs/migrations/copper-mcp-0.7.0.md), #127)
- **A KiCad UUID that a board reuses is no longer treated as a Board IR identity.** Issue #116's
  one undiagnosed `converted Board IR content failed semantic validation` refusal turned out to be
  `identity.duplicate` on `geometry ID`, and 9 of the 12 real boards surveyed carry the same
  reuse — always footprints and their pads, never segments, arcs, vias or zones. On one board 113
  footprints share just 11 UUIDs, 45 distinct resistors among them: the value names a footprint
  *type*, not an instance. The uniqueness rule was right and is untouched — Board IR footprints own
  pads by ID and every patch names its target by ID — so the fix is in the converter, which was
  asserting an identity the format never promised. KiCad's specification says a UUID *should be*
  globally unique, which is an expectation of the writer and grants a reader no key, and KiCad's
  own copy-paste and re-link workflows are tracked as producing duplicates. A UUID used once still
  becomes that object's ID exactly as before, so no content address moves. A UUID used by two or
  more objects of one kind is an identity of none of them: they all fall back together to the
  existing revision-derived name, because letting the first claimant keep the native one would
  assert precisely the identity the file cannot support. Write-back stays refused, since every
  source-preserving patch path already rejects a snapshot containing a derived identity — a board
  that names 45 resistors alike cannot be patched by that name without risking the wrong one — so
  this unblocks inspection and leaves mutation closed. ([D-158](docs/ledgers/decision-ledger.md),
  [R-119](docs/ledgers/risk-register.md),
  [KiCad UUID uniqueness](docs/research/kicad-uuid-uniqueness-v1.md), #116)
- A semantic-validation refusal now names the invariant it failed instead of only saying that one
  failed. `converted Board IR content failed semantic validation` was a wrapper that identified no
  rule and no construct, which is what left #116's survey with an entry nobody could act on; it now
  carries the validator's own message. Naming the rule is not echoing the board: every Board IR
  validation message is a fixed string chosen by `copper_mcp.board_ir`, and the two that were built
  from an object ID are rebuilt so the board-derived text travels in the locator the refusal drops.
  ([D-158](docs/ledgers/decision-ledger.md), #116)
- Copper saved on KiCad's net 0 — stitching vias and orphaned tracks, which KiCad 10 writes as
  `(net "")` — no longer refuses the whole document with `via has no routable net`, the largest
  single cause in the issue #116 real-board survey (queued on 7 of 12 boards, which carry 115
  netless vias and 2,687 netless track segments between them). Such copper converts as an
  obstacle with no connectivity contribution: `net_id` is `None` on the `Via`, `Segment`, or
  `Arc`, the item never matches any request net, its clearance is the widest class on the board,
  and no already-connected claim can pass through it — pinned by mutation-checked router tests
  in both the via-join and segment-attachment directions. All three saved spellings of "no net"
  (`(net "")`, `(net 0)`, `(net 0 "")`) resolve identically; a negative ordinal is now an
  explicit typed `net.unknown` refusal, and a netless via is still held to every geometric rule.
  Board IR, codec, JSON schema 0.2.0, and scene contracts widen `net_id` to nullable in place —
  strictly additive, with every existing content address byte-identical (ADR-0078, D-159,
  R-120, #119).
- **Three singleton real-board refusals, each a different kind of defect.** Found by running the
  adapter against a working tree of twelve real KiCad boards, where each was the first refusal on
  exactly one board and invisible behind more common causes.
  - A footprint graphic on a copper layer now refuses under the name of what it is. The board that
    found it carries a `NetTie-2_THT_Pad1.0mm` joining two ground nets, and KiCad defines
    `net_tie_pad_groups` as meaning nets in a group "are allowed to short". The adapter already
    refused net ties — correctly, because Board IR models nets as disjoint and this copper belongs
    to two at once, which no envelope can express — but the preflight ran first and reported a
    stray drawing on a copper layer, sending a user to look for a mistake that is not there. The
    one message is now three: a net tie, an `Edge.Cuts` graphic (routing *room*, the opposite
    direction of error), and unmodelled copper. All three still refuse; copper is never dropped.
    (D-162)
  - A pad with **no copper layer at all** is a KiCad *aperture* pad — a solder-paste stencil
    opening, used to subdivide the paste over an exposed thermal tab — and is now omitted from
    Board IR instead of refusing the board. One board carried eight of them on two `TO-252-2`
    transistors. Omitting one removes no obstacle and discards no attachment point, and that claim
    is conditional, so each condition refuses rather than drops when it fails: paste or mask layers
    only, `smd` kind, no net, no pad number. The regression asserts *equality* of every
    copper-bearing field with and without the aperture. (D-163, R-122)
  - `placed`, KiCad's autoplacement status flag, is accepted as footprint metadata — it carries no
    geometry, no layer and no constraint, unlike `locked`, which is a constraint and stays
    modelled. One board carried `(placed yes)` on all 31 of its footprints. Both allowlists stay
    closed, with an unknown footprint field and an unknown root field each pinned by its own
    control. (D-164)

  No diagnostic code, Board IR field, schema or digest changes, and no golden identity moves.
  (#116)

- Board metadata that KiCad writes into essentially every real board no longer refuses the whole
  document. `solder_mask_min_width` joins `pad_to_mask_clearance` as accepted setup metadata — it
  bounds mask slivers, not copper — and `descr` and `tags`, the library documentation strings
  copied into every placed footprint, are accepted as footprint metadata. Across the 23 real
  boards this was found on, `descr` and `tags` appeared 2,518 times each, so their absence from
  the allowlist refused nearly everything. `point`, which carries `at`/`size`/`layer` like the
  `fp_*` primitives, now goes through the same layer-aware path instead of the metadata
  allowlist, so a `point` on a routing layer is refused exactly as a stray `fp_line` is. The
  allowlists stay closed: an unrecognised setup or footprint field is still a typed refusal, and
  a regression pins that.

### Added

- **Source-to-board connectivity parity is now an authoritative, test-bound claim**, closing
  issue #66's last leg. `verify_source_to_board_parity` (MCP, both transports) and
  `copper-mcp source-to-board-parity` ask KiCad's own `pcb drc --schematic-parity` whether a
  workspace board implements a Circuit Intent's connectivity. The prior slice left this a
  `not_run` non-claim on a recorded assumption that a board-side verdict needs a project rather
  than a standalone file. That assumption is wrong for the CLI: `JobExportDrc` derives the
  schematic from the board filename by swapping the extension and the project load beneath it is
  guarded by an existence check, so a directory holding only a `.kicad_pcb` and a `.kicad_sch` —
  no `.kicad_pro`, no library tables — produces a populated `schematic_parity` array. The GUI's
  parity checkbox is the thing with no effect in standalone mode. Removing that blocker exposed
  four ways to get a *silent* false pass, and all four are refused rather than reported. An
  unfetched netlist degrades to `"schematic_parity": []` at exit 0, with the only signal being
  English on stderr that the containment discards. `--exit-code-violations` ORs three providers
  into one code 5 that a board with no schematic at all still returns, so it is not passed and the
  report is parsed instead. Every parity finding is `warning` severity, so `--severity-error`
  empties the array for a genuinely mismatched board — `--severity-all` is fixed in the argument
  vector. And the fourth was ours: the delivered schematic marks every symbol `(on_board no)`,
  correct for the delivery artifact ADR-0015 scoped and fatal here, because such a symbol never
  enters KiCad's board-side netlist — measured, it yields `extra_footprint` ×2 against a *correct*
  board and the identical output against a deliberately wrong one. The board is therefore compared
  against a **board-eligible projection** of the same intent, a second derivative differing only in
  that flag and reported under its own digest; the delivered artifact's bytes are untouched, so
  every existing round-trip digest and golden identity is unmoved. No footprint assignment is
  invented — board-eligibility alone is measured sufficient. Every verdict is gated on a liveness
  invariant, `count(missing_footprint) + count(footprint_symbol_mismatch) == component_count`,
  which is a positive proof KiCad loaded the netlist and is `0` in all four false-pass modes; a
  disagreement is a typed refusal, never a reconciliation. The four connectivity finding types
  decide the verdict, while the three footprint-identity types are the unavoidable signature of a
  footprint-less intent and are disclosed as counts rather than claimed as parity failures; an
  unreviewed type is refused. Evidence binds the intent digest, the delivered schematic digest, the
  projection digest, and the board revision together, and only digests, counts, and fixed literals
  cross the boundary — parity descriptions embed net names verbatim and never leave. A real-KiCad
  control proves a genuinely mismatched board *is* detected. This claims nothing about the
  delivered schematic file matching the board, about footprints, libraries, or manufacturability;
  `erc`, `footprint_correctness`, `electrical_validation` and `board_ready` remain explicit
  non-claims, and only KiCad 10.0.5 was executed.
  ([ADR-0084](docs/adr/0084-authoritative-source-to-board-parity.md),
  [D-170](docs/ledgers/decision-ledger.md), [SEC-127](docs/ledgers/security-ledger.md),
  [R-127](docs/ledgers/risk-register.md),
  [source-to-board parity research](docs/research/source-to-board-parity-v1.md), #66)
  The excessive-agency evaluation artifact is re-measured as a
  [B-090 replay](docs/ledgers/benchmark-ledger.md): the new verification contract declares an eighth
  single-value non-claim field, and the `non_claim_inference` scenario counts those by introspecting
  the live contract module rather than from a constant — which is exactly the property that makes
  that check non-vacuous. All 116 cases, 77 passes and 0 failures are unchanged.
- **Net-tie footprints convert: the declared short's copper is a netless obstacle, and the tie
  is never a connectivity claim.** KiCad's `net_tie_pad_groups` declares that "nets attached to
  pads within a single pad-group are allowed to short", and the footprint's filled copper
  polygon is that short — copper belonging to two disjoint nets at once, which D-162 recorded as
  the one refusal no envelope could lift because its two roles point in opposite safe
  directions. ADR-0092 resolves the roles separately: as an **obstacle** each tie rectangle
  becomes a full-width `Segment` with `net_id None` along its long midline — a strict superset
  of the drawn copper, so a third net can never route through the tie and even the tied nets are
  kept out of it (over-refusal is the accepted direction) — and as **connectivity** nothing is
  claimed, because a joined-nets edge cannot be test-bound from the file alone, so the tied nets
  deliberately report unconnected through the tie exactly as net-0 stitching copper behaves
  (ADR-0078). The identities are revision-derived on purpose — an `fp_poly` is not a track, so
  its UUID names no segment — and that keeps every write-back path refused (ADR-0026): no patch
  can separate the tie copper from the pads it shorts. **The accepted subset is stated as a
  closed list of the tie polygon's required and permitted fields** (ADR-0092), not as prose —
  prose is what let two constructs through that it claimed not to accept: a five-point ring, and
  a polygon with no `stroke` field at all. The second ran in the forbidden direction, because
  the only thing establishing that the drawn copper is the rectangle and nothing more is
  `(width 0)`, so an omitted field skipping that check could put real copper *outside* the
  modelled obstacle. `stroke` is now required; all 331 `fp_poly` expressions across the 20-file
  corpus carry one, so the omitted form is unobserved on real boards. The closing-point widening
  is kept and stated — a five-point ring whose last point repeats the first is accepted, since
  the closing point carries no geometry; one that does *not* close still refuses. A tie polygon
  on `Edge.Cuts` refuses too: the outline is routing room and may only be under-approximated
  (ADR-0076), the opposite direction from an obstacle. Fourteen malformed-tie variants each keep
  their own typed refusal, and the third-net guard is pinned with a no-tie mutation control. No schema or digest change — boards without net ties are
  byte-identical, and the committed golden digests pin that.

  **This converts no additional board, and that is the measured result, not an expectation.**
  Net-tie footprint copper now converts as a netless obstacle; the conversion count is unchanged
  — 11 of the 12 boards in the #116 survey set convert, which is 11 of all 17 boards in the
  corpus as saved today, the six refusals being typed: connect-kind (edge-connector) pads on one
  board, root board properties on four phono saves, and a root copper graphic on one. Measured
  read-only before and after on the same tree; the corpus has grown by five phono saves since
  the survey, which is why both denominators are given.

  The one board carrying a net tie, `tier1-rev-a`, has **three** blockers stacked on it and this
  removes only the first. Its refusal advances from `net-tie footprint copper is unsupported in
  Board IR adapter v0.2` to a refusal for `connect`-kind pads — KiCad's edge connector — with the
  intervening `zone_connect` blocker having been removed separately by D-178. Advancing a refusal
  through a stack is real progress and is how such a stack is measured, but it is not a
  conversion, and nothing here should be read as claiming the corpus reaches 12 of 12. The three
  remaining conversion gaps are tracked as #138 (edge-connector pads), #140 (root board
  properties) and #141 (root copper graphic).
  ([ADR-0092](docs/adr/0092-net-tie-copper-as-netless-obstacle.md),
  [D-179](docs/ledgers/decision-ledger.md), [R-136](docs/ledgers/risk-register.md),
  [KiCad net-tie modelling](docs/research/kicad-net-tie-modelling-v1.md), #116)
- **Chamfered and circular courtyards convert, and their legality claims stay honest.** The
  #116 survey's two courtyard causes — `courtyard edges must be non-zero and axis-aligned`
  (measured to be exact 45-degree electrolytic-capacitor chamfers, not the hypothesised rotated
  rectangles) and `courtyard primitive is unsupported by Board IR v0.2` (104 `fp_circle`
  outlines, zero arcs) — are removed by widening Board IR 0.2.0 to octilinear courtyard rings
  and a new exact `CourtyardCircle` value. Legality brackets every published verdict by
  direction: only an outer bound may prove `proven_clear`, only an inner bound witnessed past
  each side's worst-case cache loss may prove `violated` (two insets, strictly, for a circle,
  whose cache KiCad polygonises inward before contracting — a 10,001 nm development claim the
  real tool contradicted and the oracle benchmark caught), and everything between is
  `inconclusive`. Uncertifiable arrangements degrade to claims-nothing bounds; `fp_arc`,
  inexact radii, arbitrary slopes, and non-quarter-turn poses stay typed refusals. The
  canonical encoder emits `courtyard_circles` only when present, so every existing snapshot
  digest, scene revision, and golden identity is byte-stable. Measured against real
  `kicad-cli` 10.0.5 over 23 cases: 12 exact parity, 11 conceded, 0 contradictions; on the
  #116 tree, courtyard-stage refusals drop from 13 boards to zero. (#116, ADR-0080, D-161,
  R-121, B-093)

- **The KiCad plugin is now a Plugin and Content Manager package, and installing it still grants
  nothing.** `scripts/build_pcm_package.py` produces `com.github.seunghyukchoe.coppermcp-live-observer`
  as a reproducible archive alongside the wheel and sdist, attested under the same `dist/*` subject
  path. The format was read from KiCad's published JSON Schema and its addons-metadata CI rather
  than from the prose guide, which contradicts the schema on six fields; both schema versions are
  vendored under `schemas/kicad-pcm/` and the package validates against **both**, because a
  `plugin`-typed package is served to KiCad 6.0–9.x through the down-converted v1 lists as well as
  to 10.0+, and v1 is the stricter document. The archive is **stored, not deflated**, written in one
  declared sorted order with the 1980 ZIP epoch, mode 0644, and Unix host on every entry, so its
  bytes are a pure function of member names, contents, and order — byte-identical across Python
  3.12 and 3.14, two timezones, and different hash seeds. That is a correctness requirement, not a
  nicety: a version merged into the KiCad repository is immutable, so a rebuild that differed would
  be unfixable in place. The `download_sha256`, `download_size`, and `install_size` in the
  submission metadata are measured from the artifact the script just built, so no digest is ever
  transcribed, and the in-archive copy is deliberately a different document — exactly one version,
  no `download_sha256` — because KiCad's submission CI cross-checks the two. Submission to the
  official repository stays a human step, prepared as a checklist in the plugin README. (#98,
  D-154, SEC-121)
- **A `requirements.txt` that must exist and must install nothing.** KiCad marks a Python IPC
  plugin *ready* only after pip exits 0 against that file, and skips unready plugins in both
  `GetActionsForScope` and `InvokeAction` — so a plugin shipped without it installs, discovers,
  validates, and then never appears in the toolbar, with the reason only in a trace log. Naming
  `copper-mcp` in it fails the same way for the opposite reason: KiCad resolves it against PyPI
  under `--only-binary :all:`, and CopperMCP is deliberately unpublished there. The per-plugin
  environment is created with `--system-site-packages`, so the operator's own
  `pip install 'copper-mcp[kicad]'` is what supplies the import, and the entrypoint now refuses
  with a fixed, actionable sentence when it has not been done. (#98)

- A container image for the MCP server. `Dockerfile` builds a wheel and installs it into a
  slim Python base as a non-root user, with `/workspace` as the mounted board directory and the
  stdio transport as the entrypoint. It deliberately does **not** bundle KiCad: board inspection,
  DRC, ERC, and rendering delegate to an authoritative `kicad-cli`, and shipping one inside the
  image would let a caller believe those surfaces answered when the host's own KiCad is what must
  answer for them. Without KiCad the server still starts and lists all 27 tools, refusing the
  KiCad-backed ones with their normal typed diagnostics. No mutation flag is set in the image, so
  it is read-only unless an operator opts in at run time exactly as on a host install.

- **CopperMCP's central safety claim is now an adversarial test suite instead of a sentence.** The
  claim is a negative — an agent driving this server cannot cause an unintended board mutation and
  cannot extract a verification that was never computed, even when it tries — so it cannot be
  proved, only attacked. `scripts/evaluate_excessive_agency.py` runs 29 predeclared scenarios in
  six families through the real MCP adapter: mutation without consent (every apply surface with the
  flags off, a forged token, a token from another session, and tokens rebound to a different
  candidate, revision, board, and operation domain, plus a genuine token replayed straight after the
  write it authorized), stale-state exploitation, claim laundering (a hand-edited placement legality
  record and a hand-edited route manifest, each keeping its published identity), non-claim
  inference, information extraction, and budget exhaustion. Each scenario states its adversarial
  goal, its tool calls, and the one typed refusal or honest non-claim it requires, in a catalog
  digest-bound into the artifact so it cannot be reworded after the result is known. Every
  scenario is replayed against four **project families** — the development fixtures as a control,
  plus the CopperTone reference board, the held-out audio partition, and the external MIT
  SimpleRouteJson corpus — and every mutation scenario asserts the board's byte digest is
  unchanged. **116 cases: 77 passed, 0 failed, 39 not run**, with the not-run reasons reported
  rather than dropped: the only externally authored family accounts for 29 of them because no MCP
  tool accepts SimpleRouteJson, so it reaches no agency boundary at all. The suite says explicitly
  what it does not prove — it tests CopperMCP's refusals and not a model's behaviour, an in-process
  caller can construct anything, and a passing catalog is coverage rather than absence. Four
  discriminator tests deliberately break a boundary and require the harness to record a failure,
  because a suite that cannot fail is not evidence. (D-156, SEC-122, B-090, #69)
- **The negotiated coordinator stops rebuilding what it just decided to keep, and gets a rip-up
  window that is actually bounded.** ADR-0073 recorded its own gap honestly: every retained
  candidate was re-added to the congestion ledger from scratch each pass, re-deriving its unit
  lattice resources from geometry, so reconstruction was linear in the *retained* set. ADR-0081
  closes it. A new `IncrementalSpatialIndex` is a uniform grid whose cell size is fixed at
  construction — that one choice makes an entry's cells a pure function of its bounds, so "mutate
  in place" and "rebuild from the survivors" are the same computation, and incremental-equals-
  rebuilt is a property of the design rather than a hope. An R-tree, which is what TritonRoute's
  detailed router uses, was rejected on determinism: two R-trees built from the same set in two
  insertion orders have different node boundaries. Every query returns a **superset** of the true
  overlaps, never a subset, so both bounded-work fallbacks — an oversize entry every query
  returns, and an over-wide query that degrades to a full scan — add candidates and can never drop
  one. `CongestionLedger` now caches each net's exact resource set and retains by costing
  `min(ripped-up units, retained units)`, with a bare clear when nothing is retained. That third
  branch exists because measurement said so: always subtracting was **60–130% slower** than the
  path it replaced at zero retention, and the regression is recorded in B-095 rather than designed
  around quietly. Across 105 same-fixture A/B points — the congested synthetic channel, a
  parallel-track sweep to 32 nets, and 16 real MIT-licensed corpus boards — every point leaves the
  ledger byte-identical, the 78 with any retention are 11.8% to 99.94% faster (median 74.8%) at
  equal or fewer exact operations, and the 27 with none are up to 22% slower on an operation that
  costs single-digit microseconds. Proportion, stated plainly: the whole reconstruction is
  microseconds against ~60 ms of routing, so this is a constant-factor win on a term that was
  never the bottleneck — which is exactly what ADR-0073 predicted it would be. (#64)
- **`conflict-window-v1`, a fourth declared rip-up literal.** It re-routes every conflicted net
  plus every retained net whose copper lies within a fixed number of lattice cells of one. The
  window is a *constant*, following TritonRoute's own search-and-repair schedule, which holds its
  worker box at 7 gcells for all 65 iterations and varies only the offset and the effort inside —
  a window that widened per pass would eventually be full rip-up again and stop being a bound. The
  spatial index narrows the candidate nets and an exact integer rectangle predicate decides, so
  the selected set depends on the stored envelopes alone and never on the index's cell size,
  capacity, or fallbacks. On the congested fixture it converges in five iterations at the same
  56,000,000 nm of copper as the default while making **22 router calls instead of 30**, where
  `conflicted-only-v1` does not converge at all. A 16-cell window makes 30 calls again, because a
  wide enough window *is* full rip-up; B-095 records that rather than implying the rule improves
  monotonically. It is not the default: `all-nets-v1` stays, and one synthetic fixture is not a
  criterion. (#64)

#### Migration

None. No published content address moves. `RipUpSlot.as_json()` emits the new window weight only
for the rule that reads it, so all three pre-existing rip-up literals keep the exact canonical
bytes they published before — `RipUpSlot()` is still
`sha256:871de3d64827d267ed64443a705431c7a4a32fa35a5815b137d9abb23f73c71a` and `NegotiationPlan()`
is still `sha256:b3d090edeeb861f0c215dd18420bdd5624a7f178f1034af25526457538d3eac0`, both pinned by
test. A stored plan digest, rip-up slot digest, or plan-bound candidate identity still verifies.
The no-plan coordinator path is byte-for-byte unchanged, and the committed B-087 artifact still
reproduces from its harness.

- **CopperMCP now has a routing benchmark on boards it did not author, and the first honest number
  from it is 59.83%.** A benchmark-only import seam converts tscircuit SimpleRouteJson problems
  into ordinary verified Board IR snapshots and ordinary route requests, so an external corpus
  meets the same canonical verification, clearance model, and typed refusals as a KiCad board. It
  is not an MCP tool and changes no public contract: it lives in a `copper_mcp.benchmarks`
  subpackage that only `scripts/` and `tests/` import. One invariant governs every mapping — each
  imported copper rectangle contains the source shape. A `rect` obstacle maps to its own extent; an
  `oval` blocks as its bounding box while staying an oval pad so its attachment core stays
  inscribed; an obstacle naming a layer the declared stack does not contain blocks the *whole*
  stack rather than being dropped; the board outline alone rounds inward, because outline is
  routing room and growing it would hand the router area the document never granted. Millimetres
  are read as literal JSON tokens — never as floats — and converted through `decimal.Decimal` at
  1,000,000 nm/mm, identically to `board_ir.mm_to_nm` for any token with at most six fractional
  digits. The 527 sub-nanometre tokens in the corpus (`2.9000000000000004` and friends, IEEE-754
  residue from the JavaScript pipeline that wrote them) are resolved by direction rather than by
  rounding to nearest, so the largest movement any edge makes is one nanometre and the harness
  records the observed maximum instead of asserting it. Anything unrepresentable — an unknown
  obstacle type, a non-finite number, a connection point no obstacle anchors, an obstacle two nets
  both claim — refuses the whole document with a typed code; no element is ever silently dropped.
  (#96)
- **A versioned benchmark harness that reports its refusals as the result.**
  `scripts/benchmark_simple_route_json_corpus.py` routes every imported net of a licensed corpus
  and records boards imported, nets attempted and routed, the outcome breakdown by exact
  `RouteFailureCode`, wire length against a *provable* lower bound, vias, bends, and wall time,
  into a self-digesting artifact under `benchmarks/results/routing/`. Every attempted net is
  accounted for by exactly one outcome and a test asserts the breakdown sums to the denominator, so
  a success rate can never be computed over a quietly shortened sample — the failure mode the
  upstream tscircuit harness shipped by default. On the committed 20-board subset: 70 of 117 nets
  routed, 1.1711× the lower bound, 0 vias, and **every two-pin net refused**, because the reference
  A* two-pin path requires the pad-centre delta to divide by the lattice step and external
  coordinates do not oblige. Running the corpus a second time under a per-net divisor-aligned grid
  step converts those `off_grid` refusals into `grid_budget_exceeded` ones and routes no additional
  net, which localises **this corpus's** constraint to the lattice-node budget rather than to grid
  alignment — a finding that either configuration alone would have hidden, and one that does not
  transfer: B-100 later re-ran the same question on real hardware and found the lattice is where
  the refusal is *reported*, not where the constraint is. **The 59.83% is not a whole-board
  completion result**: every net is routed independently against the unrouted snapshot, so the
  candidates are not mutually compatible and no subset of them is a partial routing of any board.
  The 1.1711 ratio is against a loose provable lower bound that ignores every obstacle and bend, so
  it is not an optimality claim either. FreeRouting is not installed in the
  recording environment and no bridge to it exists, so the baseline comparison is recorded as
  `not_run` rather than estimated; the cross-router comparison remains unmeasured. (#65)
- **The first externally licensed corpus in the tree, with the licence checked before the bytes.**
  20 of the 36 MIT-licensed SimpleRouteJson boards from `dwiel/tscircuit-benchmark` ship under
  `benchmarks/corpora/`, with the upstream `LICENSE`, an attribution file, and SHA-256 recorded for
  all 36 so a fetched remainder verifies against the same manifest; `scripts/fetch_simple_route_json_corpus.py`
  retrieves the rest from the pinned commit. The committed subset is the first 20 filenames in
  upstream lexical order — a rule fixed before the run, not a selection on results — and it is the
  easier half of an already-narrow corpus. `tscircuit/autorouting` has no licence of any kind and
  is archived, so nothing from it is redistributed and only its format specification is cited;
  PCBWorld's real-board split retains heterogeneous upstream licences and has no public host yet.
  The boards themselves are LLM-generated and were routed with FreeRouting during their
  construction, so they are neither human-designed hardware nor a neutral yardstick for
  FreeRouting, and the attribution file says so next to the data. (#65, #96)

- **Live editor mutation now has a consent model, a capability, and every precondition it needs —
  and still refuses to mutate.** `apply_live_candidate` checks, in order, the operator opt-in, a
  live-scoped single-use token, the KiCad session, the board serialization, the converted Board IR
  snapshot, and the candidate's own identity and geometry replayed against the board the editor is
  holding right now — then answers `capability_not_implemented` from the exact point at which
  `begin_commit` would be called. Every other refusal is reachable and typed, and
  `preconditions_verified` names only the checks that actually ran, so an absent name is never
  readable as a passing one. The mutation is deferred to a slice that has been through adversarial
  review; a tool that sometimes changes a board and cannot say whether it did is not shippable.

  Enabling it requires **both** `COPPER_MCP_ALLOW_LIVE_APPLY=1` and `COPPER_MCP_ALLOW_LIVE_IPC=1`,
  read with the same exact `{"0", "1"}` membership rule as the existing flags. It is deliberately a
  third flag rather than the conjunction of the two you already have: ADR-0069 recorded that the
  live opt-in "enables observation only", so reading `ALLOW_APPLY ∧ ALLOW_LIVE_IPC` as consent to
  mutate a running editor would retroactively widen two grants that were made for other things.
  `COPPER_MCP_ALLOW_APPLY` is equally deliberately **not** required — demanding it would force an
  operator who wants live mutation and no file mutation to enable file mutation to get it.

  `preview_live_layered_route` gains `include_apply_token`; the layered preview response gains an
  `apply_token` field that is `null` on every refusal and on the file-backed surface. A live
  capability binds the candidate, the board revision, the converted snapshot **and** the editor
  session under a separate HMAC domain from file apply, so a file token can never authorize the
  live surface and a token cannot survive a KiCad restart.

  The [research](docs/research/ipc-apply-v1.md) behind this is the reason the mutation waits.
  KiCad's IPC API has no revision, dirty flag, or conditional write, so a live compare-and-swap
  narrows the window between check and write without closing it; `kipy` discards the per-item
  status the wire protocol returns, so a push that returns is no evidence its items landed; and a
  user undo during an open commit, a concurrent API client, and a commit orphaned by a client crash
  are undetectable. Those are recorded as a risk, not mitigated away. (#68)
- **An agent-facing usage contract, maintained as a tested document.** `docs/agents.md` states what
  the usage guide and the MCP API contract deliberately do not: given what a tool just returned,
  what an agent should *do next*. It carries a tool-by-tool table of all 26 registered MCP tools
  with the digests each one consumes as compare-and-swap preconditions and returns for the next
  call, and the flags that are off by default; all 30 typed refusal codes and all 25 Board IR
  conversion diagnostic codes restated as actions rather than explanations (`stale_revision` means
  re-observe and rebuild the candidate, never resend with the new digests pasted in;
  `search_budget_exceeded` is not a proof of unroutability; `apply_disabled` is a question for the
  operator, not an obstacle to route around); the digest discipline that keeps candidates from
  being mixed across board revisions; realistic end-to-end call sequences for routing, placement,
  schematic ERC, and the apply token lifecycle; and what the one-value literals `not_run`,
  `not_modelled`, and `inconclusive` forbid a model from claiming. A root `llms.txt` follows the
  [llms.txt convention](https://llmstxt.org/) and points an LLM at that document first.
  `tests/test_agents_doc.py` keeps the document honest: every tool name it lists must still be
  registered over MCP, every registered tool must appear in it, and every diagnostic code it names
  must still exist in `src/copper_mcp/`, so a rename or a removed code fails CI instead of quietly
  leaving a wrong instruction in front of an agent. Documentation and tests only — no runtime
  contract, capability, schema, or public behavior changes. (#97)

- **How the negotiated router negotiates is now three declared choices instead of one fused
  strategy.** `negotiate_routes` accepts an optional `NegotiationPlan` composed of three separately
  declared slots — net order, per-iteration cost update, and rip-up selection — each a closed
  enumeration member plus bounded integer weights, each publishing its own content digest. The plan
  digest is built from exactly those three slot digests, and the published evidence re-derives that
  composition and refuses itself if it does not hold, so a plan digest can never name a slot
  combination other than the one it reports. Change one slot and every candidate identity moves
  while the envelope digest stays put, so two runs that negotiated differently can no longer look
  identical. Nothing here touches the path search: a slot decides which nets go to the router, in
  what order, how the integer congestion counters move between passes, and which nets are
  re-routed, and the A* expansion, cost function, obstacle predicate, budgets, and emitted geometry
  are untouched. Because a rule is an enumeration member, a future selector can pick among rules
  and weights but cannot author one — there is nowhere to put a rule body. A weight the declared
  rule does not read is rejected rather than ignored, so two plans differ in digest only when they
  can differ in behavior. The default cost update is named `accumulated-overuse-v1` rather than
  after PathFinder, because McMurchie and Ebeling publish no closed form for either non-base cost
  term and the additive-overuse rule is in fact VPR's; the existing history and penalty ceilings
  are kept on BoxRouter 2.0's published finding that unbounded history eventually makes a presently
  congested edge look cheaper than a previously congested one. `verify_negotiated_physical_clearance`
  now names the first violating net pair for a clearance violation — net IDs are already published
  and no geometry leaves the gate — because a lattice overflow always fails that gate, so without
  attribution a partial rip-up rule would have had nothing to retain; a refusal that blames no pair
  in particular still blames the whole allocation. Absent a plan, the coordinator's code path,
  ordering, accounting, result shape, and candidate identities are byte-for-byte what they were. A
  plan and an ADR-0064 policy profile cannot be declared together, because composing them needs its
  own evidence. The measured sweep records losses as well as wins and claims nothing: on the one
  fixture where negotiation genuinely iterates, shortest-net-first finished in one pass instead of
  five with 80% fewer router calls, while partial rip-up and history decay did not converge at all.
  The incremental spatial index is deliberately not part of this, so retained candidates are
  re-added to the congestion ledger from scratch each pass. (#62)

- **The bounded ordered-layer router can use freshness-verified zone fill instead of writing off a
  whole layer.** `LayeredRouteRequest` gained an optional `verified_fill` tuple carrying the same
  `VerifiedFill` value the single-layer core has accepted since ADR-0039, so one caller-side
  ADR-0021 freshness proof now serves both routers. A foreign zone previously contributed its whole
  boundary bounding box as a track and via obstacle on its layer, which is correct for a cached
  fill nobody has checked and wrong for a pour with a real routing window: on the reference
  fixture, a route that had to detour 14,000 nm around the outline runs 8,000 nm straight through
  the verified void, with no vias. Nothing shrinks without proof. Malformed evidence is
  `invalid_request` at the input boundary; an island proved against another source revision is
  `stale_revision`; an island with no Board IR zone of the same net and layer, or whose bounding
  box escapes that zone's, is `unsupported_geometry`. Islands are carried as bounding boxes rather
  than exact polygons because the layered lattice model is rectangular, and containment makes
  "the union of island boxes fits inside the zone box" a checked precondition rather than an
  assumption about KiCad — so the replacement can only ever remove area the conservative envelope
  had blocked, and clearance inflation is unchanged. Absent evidence keeps the old behavior exactly.
  This is an internal seam: no public contract, response field, candidate identity rule, router
  version, DRC authority, or apply semantics changes, and `preview_layered_route` does not yet
  report fill-aware provenance the way `preview_route` does. (#63)

### Fixed

- **A board outline drawn with the line tool is now a board outline.** The adapter accepted exactly
  one `Edge.Cuts` primitive — a single unfilled `gr_rect` — and refused everything else with
  `unsupported.construct`. `gr_rect` is what KiCad writes for the *rectangle tool*; draw the same
  outline with the line tool, or draw any shape that is not a rectangle, and you get `gr_line`
  segments, which was most real boards and every non-rectangular one. Segments on `Edge.Cuts` now
  chain into the single imported contour, verified against a real four-layer board whose four
  segments assemble into its exact 159 × 150 mm rectangle. (#111)

  **The direction of error inverts here, and that is the whole decision.** Every obstacle in this
  project is *over*-approximated, because a larger obstacle only makes the router refuse more. The
  board outline is routing **room**, so it may only be *under*-approximated: a modelled outline one
  nanometre larger than the drawn one hands the router copper the fabricated board does not have.
  Assembled from straight segments joined at *exactly* coincident endpoints, the ring's vertices are
  the drawn endpoints and nothing is synthesized, so containment holds with equality.

  Nothing is repaired. KiCad chains its own outline with a non-zero tolerance and will close a small
  gap for you; a 10 µm near-miss — inside KiCad's own epsilon — is refused here instead, because
  closing a gap adds board area no drawn segment encloses. A zero-length segment, a duplicate
  segment, an open contour, a branching spur, two disjoint loops, and a self-intersection each refuse
  with a typed code and are never guessed at, since every plausible repair invents board. Arcs,
  circles, polygons and curves on `Edge.Cuts` stay refused with a diagnostic that now names the
  curve: ADR-0072's conservative sagitta bound is an *upper* bound on an arc, which is right for an
  obstacle and backwards for an outline, and a chord is inscribed only when the arc bulges away from
  the board interior. Work stays bounded — the segment count and the quadratic simplicity test each
  charge a declared budget. No schema, digest, or diagnostic code changes, and no golden identity
  moves. ([ADR-0076](docs/adr/0076-segment-assembled-edge-cuts-outline.md), D-155, R-117)
- **A roundrect corner radius is now rounded, not refused — and the direction is the opposite of
  the obvious one.** KiCad never stores a roundrect's radius. It stores a ten-significant-digit
  `roundrect_rratio` scaling the pad's *shorter* side, and recomputes
  `KiROUND(ratio * min(size.x, size.y))` on every read, so an ordinary ratio on an ordinary pad
  lands on a fractional nanometre — 0.203125 of a 650,000 nm side is 132,031.25 nm. The adapter
  refused that as `roundrect radius is not an exact nanometre`, which was fail-closed and honest
  but was the **first refusal on 5 of 23 real boards** for a sub-nanometre encoding artifact. Across
  that tree, 592 of 4,537 roundrect pads carry a fractional radius and the worst residue is 0.80 nm.
  The radius now rounds **up**, and the reason is not that a pad is copper: a larger radius means
  *more* corner rounding and therefore a *smaller* pad, so rounding the copper outward and rounding
  the radius up are opposite instructions. The roles settle it instead, and do not conflict, because
  only one reads the value — every obstacle model over-approximates a pad by its full bounding box
  and discards the corner rounding, while the radius is consumed only by the under-approximating
  attachment core, which a larger radius shrinks. Rounding up is safe under both candidate
  references without choosing between them, since `ceil(x)` is at least the exact radius *and* at
  least the integer radius KiCad itself derives. The amount is recorded as
  `ConversionResult.max_roundrect_rounding_nm` rather than hidden, and deliberately not as a
  diagnostic, because every caller treats a diagnostic as a refusal. A radius that rounds up past
  half the short side, and a ratio outside `(0, 0.5]`, are still refused rather than clamped. No
  content address moves. See
  [Roundrect radius precision](docs/research/roundrect-radius-precision-v1.md) for the derivation
  and citations, [ADR-0077](docs/adr/0077-roundrect-corner-radius-rounding.md), D-157, and
  R-118. (#116)
- **A stadium pad was being handed a disc's attachment core.** `_pad_cores` gives a round pad its
  largest inscribed square, because a disc's central rectangle degenerates to a bar that can seed
  no search — but it detected that case from the collapse alone, and a roundrect whose radius is
  exactly half its shorter side is a stadium and collapses identically. A 2.0 x 1.0 mm stadium was
  therefore given a core reaching 1.0 mm from its centre in y, where its copper stops at 0.5 mm:
  an attachment core claiming copper that is not there, which is the one direction it may never err
  in, and reachable from any board where KiCad wrote a `roundrect_rratio` of 0.5. The inscribed
  square is now gated on the pad being a disc. Every roundrect in the core-containment
  parametrisation had a band with real height, so no fixture could have caught it; a stadium case
  is added. (#116, R-118)

- **A courtyard drawn as a ring is a ring, not a solid disc.** A footprint whose courtyard is an
  outer boundary plus an inner ring — a donut — was compared ring-by-ring as two independent solids,
  so a part legitimately placed in the hole was reported as a courtyard collision and the candidate
  was refused. KiCad does not agree, and the disagreement is not a matter of interpretation:
  `buildContourHierarchy` counts how many contours contain each contour and makes an odd count a
  *hole* in the parent with one fewer parents, so a nested ring removes material. Real
  `kicad-cli` 10.0.5 reports **zero violations** for exactly the arrangement CopperMCP was refusing.
  Rings of one footprint are now pooled into a single even-odd scanline region, which reproduces
  KiCad's hierarchy exactly for the disjoint and strictly nested rings Board IR admits. This
  mattered in practice rather than in principle: the official KiCad footprint library ships **31
  footprints with nested `F.CrtYd` rings**, almost all RF shielding cans, where the ring *is* the can
  wall and the interior is deliberately left occupiable — so the old behavior refused every part
  placed under every shield can in the library. Direction of error is the point here: refusing more
  is not automatically safe when the refusal is published as per-rule evidence, because a
  conservative *answer* was still a false *claim*. (#74)

### Changed

- **Circuit Scene is `0.3.0`.** Each of the nine kinds under `static` and `mutable` is now an array
  *or* a `withheld_by_ceiling` object, so a client with a closed schema that types them as arrays
  stops validating a truncated response. Nothing else in the scene moved, and no content address
  did: `board_revision` hashes the board bytes and `snapshot_digest` is the Board IR snapshot's, and
  neither depends on how the response is shaped. See §4 of
  [the 0.7.0 migration note](docs/migrations/copper-mcp-0.7.0.md). (#127)
- **Courtyard legality is now three-valued, and ADR-0058's "exact" claim is corrected rather than
  restated.** KiCad's courtyard DRC never looks at footprint graphics; it collides a cached
  `SHAPE_POLY_SET` that `FOOTPRINT::BuildCourtyardCaches` contracts by
  `maxError = pcbIUScale.mmToIU( 0.005 )` — exactly 5,000 nm — before the test. Both footprints are
  contracted, so a zero-clearance collision needs 10,000 nm of nominal penetration. Measured against
  the real tool: 9,999 nm is clear, 10,000 nm reports `courtyards_overlap`, and the same threshold
  applies independently to each axis for corner-only overlap. CopperMCP reported a violation from
  1 nm, so every verdict in that band was a refusal KiCad does not share. `courtyard_overlap`
  therefore joins `pad_overlap` as three-valued: `proven_clear` where the raw regions share no area
  (a proof for *any* ring shape, since contraction only ever shrinks a region — the outline moves in
  and any hole grows), `violated` where the shared area contains a 10,000 nm square witness (exact
  parity with KiCad), and `inconclusive` in between. The band is **not** silently resolved: calling
  it `violated` would assert a collision the authoritative tool denies, and calling it
  `proven_clear` would deny an overlap the geometry has, so neither is claimed. `inconclusive` is not
  a violation — matching the existing `pad_overlap` convention — so a sub-threshold interference is
  now previewed rather than refused, with the non-claim recorded in the published evidence; a caller
  needing the stricter reading can treat `inconclusive` as a failure, which is exactly what the
  three-valued vocabulary makes expressible. `scripts/benchmark_courtyard_oracle_parity.py` replaces
  the old `kicad_invoked: false` posture with a real oracle and measures **10/15 exact parity, 5/15
  conceded `inconclusive`, 0 contradictions, 0 false-positive violations, 0 false-negative clears**
  over 15 cases, refusing to emit an artifact if any contradiction appears. No Board IR digest moves
  and the golden placement candidate identity is unchanged. The tiny-shape band, arcs, custom
  courtyard clearance, and same-footprint rings that touch or properly intersect remain declared
  non-claims. (ADR-0075, D-152, B-089, R-115, #72)
- **Boards with more than two copper layers convert again — every real 4-, 6-, or 8-layer KiCad
  board was being refused.** The Board IR adapter validated the copper stack by requiring each
  layer's declared ID to equal `declaration_position * 2`, i.e. `F.Cu=0, In1.Cu=2, In2.Cu=4,
  B.Cu=6`. KiCad has never numbered layers that way: copper takes the *even* values with the
  technical layers interleaved on the odd ones, so it is `F.Cu=0`, `B.Cu=2`, and `In{N}.Cu=2+2N`,
  and because KiCad writes the stack front-to-back a four-layer board declares `0, 4, 6, 2` —
  deliberately not ascending. A two-layer board satisfies both rules coincidentally, and two-layer
  boards were every fixture in the repository, so the whole suite stayed green while every real
  multilayer board was refused with `unsupported.construct` "copper layer IDs, names, or
  declaration order are unsupported". The adapter now checks the two invariants separately: the
  declaration position fixes the *name*, and the name fixes the *ID* through KiCad's own table.
  This unblocks multilayer inspection, scene observation, placement preview, DRC binding, and the
  2–8 signal layer ordered router (ADR-0068) on real boards — all verified end to end on four- and
  six-layer boards, with no downstream surface found to have a separate blocker.
  The rule stays fail-closed and is *not* loosened into accepting anything: a duplicate ID, a gap
  in the inner sequence, a misnamed position, a back layer that is not `B.Cu`, a missing front or
  back copper layer, an inner index past KiCad's `In30.Cu`, KiCad's own pre-version-9 numbering
  (`F.Cu=0, In1..In30 = 1..30, B.Cu=31` — a real numbering, but not this format version's), and
  the superseded `position * 2` numbering all still refuse with the same typed diagnostic.
  Nothing published moves: `Layer.index` remains the declaration position, so the Board IR copper
  stack is still dense and front-to-back, and every two-layer content address is byte-identical.
  The one identity that changed is this repository's own four-layer *test fixture*, which had been
  written in the invented numbering and is now correct; its pinned route-candidate ID is re-pinned
  in place, with the router and `LAYERED_ROUTER_VERSION` untouched. See
  [KiCad copper layer numbering](docs/research/kicad-copper-layer-numbering-v1.md) for the
  derivation and citations, D-153, and R-116 for the class of defect — a validation rule no fixture
  ever contradicted. (#104)

- The KiCad plugin entrypoint imports `copper_mcp.kicad_ipc` inside `main()` rather than at module
  scope. A PCM install delivers the plugin file and not CopperMCP, so at module scope a new user's
  first click was an unhandled `ImportError` that put a filesystem path into KiCad's warning bar.
  It is now one line naming the pip command, with no path and no traceback. (#98, SEC-121)

## [0.6.0] - 2026-08-06

Upgrading from 0.5.0: see the [0.6.0 migration notes](docs/migrations/copper-mcp-0.6.0.md).
Live KiCad IPC observation is now off by default and requires `COPPER_MCP_ALLOW_LIVE_IPC=1`; a
serialization whose root is not `kicad_pcb` reports the new `unsupported.document` diagnostic code
instead of `syntax.invalid`; and a digest taken over rendered board or schematic bytes is
reproducible only by the version that recorded it.

- **A board carrying arc-shaped copper on another net can now be routed instead of refused.** KiCad
  writes an `(arc …)` track whenever a corner is rounded, and the Board IR has always parsed it, but
  the single-layer router refused *any* arc on the requested layer — so one rounded corner made the
  whole board unroutable. A foreign-net arc spanning at most half a turn is now a conservative
  polygon envelope obstacle, and the route detours around it. Every point of such an arc lies within
  the sagitta of its own chord, so the envelope is the chord swept with an axis-aligned square of
  the half width plus the sagitta — the same construction diagonal tracks already use, with a larger
  radius, so every vertex stays an exact integer and the envelope is a provable superset rather than
  a close approximation. The half-turn test is one integer dot product, and the sagitta bound is the
  smallest integer satisfying a sufficient integer condition, so neither introduces a rounding rule.
  Two cases stay typed refusals with distinct diagnostics: an arc past half a turn, whose copper
  leaves the chord's span so no chord-based envelope would be honest, and an arc on the *routed*
  net, because attachment copper must be under-approximated and an arc has no exact integer inner
  core yet. **No Board IR field, schema, digest, diagnostic code, or `ROUTER_VERSION` changes** — no
  board that already routed changes geometry or identity. The layered route proposal surface keeps
  its stricter blanket arc refusal, because its obstacle model is rectangles only. (#67)

### Security

- **Live KiCad IPC observation now requires an explicit operator opt-in and is off by default.**
  `capture_live_board` previously discovered `KICAD_API_SOCKET` from the ambient environment and,
  with nothing set, connected to whatever socket the official binding defaults to; the MCP tools
  `inspect_live_board` and `observe_live_board_scene` were registered unconditionally. Reaching a
  running editor is an outbound action against the operator's machine, so it is now gated on
  `COPPER_MCP_ALLOW_LIVE_IPC`, which uses the same exact `"0"`/`"1"` membership rule as
  `COPPER_MCP_ALLOW_APPLY` — no case folding, no truthiness. Both capture chokepoints refuse
  before the endpoint is read, so a disabled deployment discovers no socket and opens none, and
  every live surface (scene, route, layered route, placement, editor context) is gated by that one
  check. The tools stay **listed** and answer with a refusal naming the flag, matching the apply
  surface: hiding them would make the capability undiscoverable and invite retry loops. The opt-in
  enables local IPC only — TCP endpoints are still refused, and `KICAD_API_TOKEN` is still never
  passed to the binding and never serialized. **This is a behavior change for any deployment that
  relied on ambient discovery.** (#77)

- **A serialization whose root is not `kicad_pcb` is no longer summarized as a PCB.** The IPC
  object counter classifies `footprint`, `pad`, `via`, `segment` and `net` heads wherever they
  occur in the tree and never established that the tree was a board, so an `(evil_root …)` payload
  was published as a live board observation with plausible topology counts. The counter now
  refuses a foreign root before classifying anything. Both Circuit Scene observer paths — the live
  one and the file-backed `observe_board_scene` — now refuse it too, rather than reporting it as
  an ordinary `supported: false` conversion result: "this is not a board" and "this is a board we
  cannot convert" are different answers. A KiCad board with an unsupported version or construct is
  unaffected and still returns its truthful unsupported result. (#75)

- **The compare-and-swap confirmation read is charged against the board-byte budget.** The first
  IPC read enforced `max_board_bytes`; the confirming read was UTF-8 encoded with no length check,
  so with `max_board_bytes=4096` an 11 MB second read was materialized in full and then surfaced
  as `KicadIpcConnectionError` ("KiCad board changed during observation") — a resource refusal
  reported as a concurrent edit. The confirmation is now charged against the budget before any
  full encode and compared as text, so an oversized second read is a `KicadIpcPayloadError` budget
  refusal and no unbudgeted encoded copy is created. `COPPER_MCP_MAX_BOARD_BYTES` is a byte
  ceiling, so the confirmation is measured in **UTF-8 bytes**, not code points: board text is
  external-tool output and is routinely non-ASCII, and a code-point count would let a
  600-character run of `é` clear a 1024-byte budget at 1200 bytes and be mis-reported as a
  concurrent edit again. The measurement settles the unambiguous cases with the 1- and 4-byte
  UTF-8 code-unit bounds and otherwise encodes bounded slices, stopping at the ceiling, so it is
  byte-exact while the transient buffer stays a fixed cost. An in-budget mid-observation edit is
  still the connection-class refusal it was. The same fix applies to the editor-context
  capture. (#76)

### Added

- **Authoritative KiCad ERC and a real round trip for generated schematics.** A new
  `verify_circuit_schematic_erc` MCP tool and `copper-mcp schematic-erc` CLI command take the same
  Circuit Intent content as `render_circuit_schematic`, render it deterministically, and hand those
  exact bytes to `kicad-cli sch erc`. KiCad decides what an electrical rule violation is; CopperMCP
  transports the verdict and never reimplements ERC. The schematic is then re-read through
  `kicad-cli sch export netlist --format kicadxml` and compared against the source intent by the
  existing parity verifier, which until now had no caller — so the components and nets KiCad
  recovers are checked against the intent that produced them.

  Results carry **two** signals, not one: `passed` means KiCad reported no error-severity violation,
  while `clean` is true only when there are no findings and no ignored checks at all. The bounded
  passive fixture is `passed: true, clean: false` on KiCad 10.0.5 — it genuinely produces four
  warnings — and that pair is pinned by a test so it cannot be quietly promoted. `kicad_cli_parse`
  moves from `not_run` to `passed`, since KiCad cannot run ERC on a schematic it failed to load;
  `schematic_board_parity`, `electrical_validation`, and `board_ready` remain explicit non-claims.

  The subject is always CopperMCP's own render, never a workspace file, so the subprocess receives
  no user design data that did not arrive through the tool argument. Both `sch` commands share one
  bounded helper with the fixed argument vector, private read-only snapshot, `RLIMIT_FSIZE` wrapper,
  and private environment the board DRC adapter already uses; `--define-var` is never exposed. Only
  digests, counts, and KiCad's violation-type keys are returned — never schematic bytes, net or
  component names, values, coordinates, or UUIDs. Because the checked snapshot carries no
  `.kicad_pro`, KiCad applies its default severities: no user project can weaken the verdict, and
  equally the verdict is not necessarily what that user's project would report. A missing KiCad CLI
  is a typed refusal, never a verdict.
  ([ADR-0071](docs/adr/0071-authoritative-schematic-erc.md), D-145, SEC-119, #66)

- `scripts/check_ledgers.py` now validates ledger identifier allocation, and a new sibling
  `scripts/check_adr_numbers.py` validates ADR numbers. Both run in `make lint` and CI. A duplicate
  ID, a badly padded ID, a row that goes backwards in a strictly increasing ledger, two files
  claiming one ADR number, an ADR heading that disagrees with its filename, an ADR missing from or
  repeated in the index, an index row pointing at no file, and a stale advertised next number are
  each now build failures. Gaps are reported as information and never fail, because a withdrawn
  number is permanent. The nine benchmark replays that legitimately reuse a parent's number are
  carried in a closed exception list keyed to their exact headings and are additionally required to
  be `####` sub-entries beneath the `###` entry they replay; an exception that stops matching real
  text is itself a failure. Both checkers also verify the single-line allocation registries in
  `docs/ledgers/README.md` and `docs/adr/README.md`, so two branches that allocate the same next
  number now produce a textual merge conflict instead of a silent collision. (#82, #83)

### Fixed

- Indexed `ADR-0067` and `ADR-0068` in `docs/adr/README.md`. Both were absent because they merged in
  parallel with the pull request that rebuilt that index — the same class of defect the new checker
  prevents. Recorded the six ledger identifier collisions the new checker found (`D-137`, `D-139`,
  `D-140`, `B-076`, `B-078`, `B-082`) as append-only correction entries `D-143` and `B-084` rather
  than renumbering any merged row.

- Committed golden identities for every content-addressed surface, in a new
  `tests/test_golden_identities.py`. Each pin asserts an exact digest recomputed from a committed
  fixture, and — where a canonical payload can change without changing its byte length — the
  payload length alongside it. The surfaces pinned are the single-layer route candidate ID, the
  placement candidate ID, the route bundle ID with its coordinator policy-envelope digest, the
  Board IR 0.2 snapshot/constraint digests and the legacy 0.1 fixture digests, the Circuit Intent
  snapshot digest and the rendered schematic artifact digest, Circuit Scene board/snapshot
  revisions and annotation reference IDs, the `title-line-v1` deterministic render digest, the
  routing job ID and request digest (ADR-0043), the redacted candidate manifest digest
  (ADR-0047), the persisted candidate export digest (ADR-0048), the routing policy input,
  decision and worker-frame digests, the exact local repair input and route digests, the zone
  fill digest, the unsigned in-toto DRC statement digest (ADR-0052), the live editor context
  digest (ADR-0044), and net reference IDs. **Changing any pinned value is a breaking change**
  that requires a deliberate version bump and a migration note for persisted artifacts; the
  module says so in its own docstring. No production behavior changed.

### Changed

- Board IR conversion reports a foreign S-expression root under its own diagnostic code,
  `unsupported.document`, instead of the generic `syntax.invalid`. Callers that matched
  `syntax.invalid` to detect a wrong document type must match the new code. (#75)

- Live IPC redaction is proved by a whole-response grep rather than a single sentinel. The test
  fixture now carries a distinct marker in each author-controlled slot — net name, net class,
  footprint library id, property name and value, `fp_text`, `gr_text`, pad net, zone name, group
  name, and title block — and no marker may appear anywhere in the serialized observation, its
  `repr`, or the live scene outside its annotation quarantine, guarded by an assertion that each
  object class was actually counted. The five `# pragma: no cover` refusal paths in
  `kicad_ipc.py` (KiCad closed, socket refused, unreadable selection, unreadable item identity)
  are now exercised by fakes standing in for the binding's error types. (#78)

- Restructured repository documentation for a first-time reader. Added `docs/README.md` as the
  documentation index, moved the two handoff documents into `docs/handoff/` as `project-state.md`
  and `codex-onboarding.md` behind a `docs/handoff/README.md` that distinguishes them, and moved
  the full CLI and MCP walkthrough out of `README.md` into a new `docs/usage.md`. `README.md` now
  answers what CopperMCP is, why it exists, how to try it, what it does today, and — in one
  consolidated section — what it does not claim. No capability text was deleted; the explicit
  non-claims are now gathered rather than scattered. **No behavior changed.**

- Completed and corrected the ADR index: 16 records (0036–0047, 0060–0063) were missing from
  `docs/adr/README.md`, which is now a table carrying every number, title, and status, plus a
  tombstone explaining the deliberate ADR-0027 gap and a thematic reading order. Added the absent
  `Status: Accepted` line to ADR-0035 and ADR-0036, matching their accepted decision-ledger rows.

- Documented how ledger IDs are allocated in `docs/ledgers/README.md`: allocate at merge rather
  than in advance, never reuse a number, record corrections as new entries, and reuse a benchmark's
  ID only for a replay of that benchmark. Nothing validated these IDs before, and nothing does now;
  the convention is enforced by review.

- Indexed the three research documents that were absent from `docs/research/README.md`, and stated
  in that index that each survey is a dated evidence snapshot rather than a maintained summary.

- Refreshed `CONTRIBUTING.md` and `GOVERNANCE.md` to match how the project actually works: the
  per-target quality gate, ledger and ADR ID allocation, required conversation resolution, the
  dedicated adversarial review for any surface that writes to a user's file, and the append-only
  ledger record with its stated non-properties.

- Expanded the module docstrings on `security.py`, `tools.py`, `models.py`,
  `routing/contracts.py`, and `apply/contracts.py` to state what each module owns and what it
  refuses, matching the convention the rest of `src/copper_mcp/` already follows. Documentation
  only; no code, signature, or export changed.

- Corrected three stale capability claims in the restructured documentation, each checked against
  current code rather than against the previous text. `README.md` said route apply was the only
  mutating operation; it now names both `apply_candidate` and `apply_placement_candidate`, each
  default-off behind `COPPER_MCP_ALLOW_APPLY` with its own single-use token in its own operation
  domain, and states placement apply's narrower admitted subset. `docs/usage.md` said courtyard
  overlap was reported as `not_modelled`; the contract has evaluated it since the bounded same-side
  orthogonal evaluator landed, so the guide now describes what is evaluated exactly and what remains
  open, and gained a section for the MCP-only placement apply. `README.md`'s non-claims section and
  `docs/handoff/project-state.md` carried the same two stale claims and were corrected with them.
  **Documentation only; no behavior, contract, or diagnostic changed.**

### Added

- `scripts/check_doc_links.py` verifies that every relative Markdown link in tracked documentation
  resolves to a real path. It is wired into `make lint` and CI, and passes on the current tree. It
  deliberately does not fetch network URLs or validate heading anchors. It carries one closed
  exemption list for unresolvable targets inside append-only ledger history, which may not be
  rewritten; an exemption that matches no real link is itself a failure.

### Added

- Added an opt-in, private route-aware placement ranking policy. It scores only candidates first
  issued by the deterministic legalizer, verifies their identities and exact snapshot/view bindings
  before rebuilding an immutable in-memory Board IR pose projection, and meters all independent A*
  probes against one operation-wide cap. The default same-net Manhattan ranking and public placement
  candidate shape remain unchanged. This is not whole-board routing, congestion, KiCad DRC, or apply
  authority.

- Route-aware placement evidence now names what produced it. `PlacementSolveResult` and every
  `RankedPlacement` record their scoring policy, and `RouteAwareEvidence` carries an estimator id
  plus a digest over the full probe and A* settings, so a one-probe observation can no longer be
  mistaken for an eleven-probe one. The benchmark binds the same configuration into its `run_id`.

- Added `score_placement_candidate`, which scores one already-legalized candidate under a stated
  policy outside any search. This is what makes a genuine re-ranking comparison possible.

### Fixed

- Corrected what the route-aware placement benchmark claims to compare. It described two policies
  ranking one shared legalizer-issued candidate set; because the score orders the solver beam, the
  policy decides which successors are ever explored, and the two retained sets are in fact disjoint
  at the committed settings. B-082 records the correction, the separate true re-ranking measurement
  (which reproduces the same 23.81% over a fixed 16-candidate set), and the honest all-probeable-net
  observation, in which both chosen candidates leave 4 probes unrouted and the ordering reverses.
  B-078 and B-081 are preserved unchanged as history.

- Separated `refused_probes` from `unrouted_probes` in route-aware evidence. Only a completed
  search reporting `no_path` is a routability statement; a grid, budget, support, or cancellation
  refusal is a router limitation and is now counted as one.

- Fixed a lexicographic inversion in route-aware ranking. A candidate the projection could not
  represent reported one failed probe and a zero wire length, and since wire length is a minimize
  tier, that zero is the best possible value — so an unrepresentable candidate outranked one that
  was genuinely probed. It is now charged every probe it would have attempted. Reachable whenever
  `max_probes` exceeds one.

- Structural-integrity violations during route-aware projection — non-unique placements, a candidate
  that does not cover the view footprint set, disagreeing footprint sets — now refuse as binding
  errors instead of being downgraded to one failed probe.

- A failed predeclared benchmark criterion is now recorded as a negative result with a non-zero
  exit, as ADR-0067 promised, rather than raised. Harness integrity failures still refuse outright.

- Regenerated the route-aware placement benchmark artifact from a reachable, DCO-signed
  projection-binding remediation commit. B-081 preserves B-078 as historical evidence while
  recording the corrected source provenance; the deterministic selection metrics are unchanged.

- Internal ordered-layer route proposals now support two through eight signal layers with bounded,
  deterministic full-stack-via transitions. Omitted via policy preserves legacy two-layer behavior,
  while generalized stacks receive a deterministic cap; file, live, and durable public entry points
  continue to reject non-two-layer stacks. Source-preserving KiCad serialization and DRC remain
  deliberately restricted to the proven two-layer subset.

- The structural candidate verifier now refuses route copper that crosses a full-stack via barrel
  on a layer the route does not terminate on at that point. On two layers the existing same-layer
  intersection scan covered this implicitly; from three layers up a track on an otherwise unused
  layer could previously run straight through a barrel and still verify.

- Committed a real four-layer KiCad fixture (`F.Cu`/`In1.Cu`/`In2.Cu`/`B.Cu`, accepted by
  KiCad 10.0.5) and pinned candidate identities for two-, three-, and four-layer routes, so a
  content-addressed change can no longer pass a green suite unnoticed.

- Extended `scripts/benchmark_layered_astar.py` with an exact `(x, y, layer, vias_used)` Dijkstra
  differential over seeded two-through-five-layer capped lattices, an independent legality replay
  of every returned path, and a pinned via-policy boundary.

### Fixed

- Two-layer candidate identity is unchanged by ordered-layer routing. An intermediate revision
  recorded every via span in canonical outer-stack order, which silently changed the candidate ID
  of any two-layer route containing a return via and would have made persisted candidates fail
  structural verification and therefore candidate-bound DRC. Two-layer vias keep their historical
  traversal ordering, and the verifier compares the span as an unordered pair, exactly as the KiCad
  serializer always has.

### Fixed

- Recorded the corrected targets for two unresolvable Markdown links in
  `docs/ledgers/benchmark-ledger.md` (B-036's dataset path is missing its `benchmarks/` segment;
  B-057 links to a path that never existed in the repository). The historical rows are unchanged;
  the corrections are recorded as append-only entry B-076, and `scripts/check_doc_links.py` reads
  that entry's exemption list rather than requiring history to be rewritten.

- Route-bundle preview now refuses a seed whose derived per-net value would leave the supported
  integer range. The request boundary and the published JSON Schema both reserve headroom for the
  largest reachable reference index, so a schema-valid request can no longer escape the service as
  an untyped error instead of a typed refusal.

- An expired route-bundle time budget is now reported as budget exhaustion rather than as a
  deterministic-replay mismatch. The two negotiation runs share one deadline, so an expiry between
  them is a resource outcome; the replay-mismatch diagnostic is now reserved for genuine structural
  differences.

- Restored `DEVNULL` for the bounded KiCad DRC child's stdout and stderr, reverting an unrelated
  change to never-read on-disk capture files. SEC-113's zero-byte parent capture budget is the
  governing mitigation, and the capture had reintroduced a file-size failure mode for a chatty but
  valid KiCad run. Report bounding, timeout, private child environment, and aggregate evidence are
  unchanged.

- Rebound the route-bundle benchmark artifact to released CopperMCP `0.5.0`. Its provenance now
  records the source version explicitly, while retaining exact script, fixture, combined-derivative,
  DRC-context, and self-digest bindings.

- Route-bundle preview now refuses a seed whose derived per-net value would leave the supported
  integer range. The request boundary and the published JSON Schema both reserve headroom for the
  largest reachable reference index, so a schema-valid request can no longer escape the service as
  an untyped error instead of a typed refusal.

- An expired route-bundle time budget is now reported as budget exhaustion rather than as a
  deterministic-replay mismatch. The two negotiation runs share one deadline, so an expiry between
  them is a resource outcome; the replay-mismatch diagnostic is now reserved for genuine structural
  differences.

- Restored `DEVNULL` for the bounded KiCad DRC child's stdout and stderr, reverting an unrelated
  change to never-read on-disk capture files. SEC-113's zero-byte parent capture budget is the
  governing mitigation, and the capture had reintroduced a file-size failure mode for a chatty but
  valid KiCad run. Report bounding, timeout, private child environment, and aggregate evidence are
  unchanged.

- Rebound the route-bundle benchmark artifact to released CopperMCP `0.5.0`. Its provenance now
  records the source version explicitly, while retaining exact script, fixture, combined-derivative,
  DRC-context, and self-digest bindings.

- Refused a non-orthogonal *saved* footprint pose with a typed `unsupported_geometry` diagnostic
  instead of an untyped exception. An orthogonal proposal previously masked the stored angle from
  the placement legalizer's orientation guard, and un-rotating the footprint's pads and courtyards
  out of that oblique frame escaped the placement boundary as a bare `ValueError`. The KiCad
  adapter already fails closed on such an angle, so this is reachable only through a directly
  constructed or decoded Board IR snapshot. The refusal message does not echo the rejected value,
  and every other reference path to such a footprint already refused with the same code.

### Changed

- The route-bundle plan digest now binds the coordinator's policy-envelope digest, and the plan
  publishes it as `policy_digest`. Bundles composed from the same references under different
  coordinator iteration or penalty limits no longer share one `bundle_id`. The recorded benchmark
  identity and combined-derivative revisions are regenerated accordingly; every measured metric is
  unchanged.

### Added

- [ADR-0066](docs/adr/0066-atomic-route-bundle-preview.md) records the public route-bundle contract:
  the all-or-nothing publication rule, the double-negotiation determinism requirement, the digest
  binding, and the explicit non-claims.

### Fixed

- Named the version-coupled evidence that a release has to re-pin, and re-pinned it. CopperMCP
  writes `(generator_version "<package version>")` into every board and schematic it renders, so
  two committed content addresses move on any version bump: the golden schematic artifact digest in
  `tests/test_golden_identities.py`, and the private combined-derivative `base_revision` and
  DRC-context revision recorded in the route-bundle benchmark artifact. Bumping to `0.6.0` made
  both fail, in CI as well as locally, which would have made the project unreleasable without
  either weakening a pin or silently rewriting evidence. The route-bundle artifact is regenerated
  under `0.6.0` — every measured quantity, including the bundle identity, is unchanged, and `B-085`
  records that the `0.5.0` revisions remain correct for `0.5.0` — the schematic pin is re-pinned
  with a comment saying it is the one version-coupled pin in that module, and `docs/releasing.md`
  now carries the re-pin as an explicit numbered step rather than as folklore. Every other golden
  identity is version-independent and did not move.


## [0.5.0] - 2026-08-05

### Fixed

- Route-bundle preview now refuses oversized net-reference arrays before inspecting any element.
  Its completed benchmark DRC record also binds the aggregate result to the private combined-board
  and DRC-context revisions, without exposing board bytes or derivative authority.

- Rebound the held-out audio benchmark artifact to the reachable merged-main source commit after
  PR #51's squash merge. Its strict detached replay now proves the locally available source is in
  checkout ancestry before checkout; all bound inputs and recorded benchmark metrics are unchanged.

- Bound the optional NE5532 KiCad DRC child process using the core POSIX file-size wrapper and
  discard untrusted stdout/stderr instead of buffering it. The timeout, private child environment,
  aggregate evidence, provenance, and benchmark scope are unchanged.

- Hardened the optional NE5532 KiCad DRC benchmark observation against untrusted report output.
  Reports are now descriptor-anchored and byte-bounded before UTF-8/JSON decoding, then reuse the
  adapter's duplicate-key, non-finite, nesting, and structural budgets. Normal aggregate counts,
  provenance, and benchmark scope are unchanged.

- Corrected the exact-local-repair gate evidence. The preserved B-071 historical artifact used a
  related KiCad-derived fixture with 512 grid nodes, 20,000 expansions, 128 obstacles, and
  200,000 obstacle checks, so it is not the predeclared semantic experiment. B-072 independently
  reconstructs and equivalence-checks the pinned 256/5,000/64/100,000 source-`965d8fc` builder.
  Routing behavior and public surfaces remain unchanged.

- Restored deterministic render and apply replay for valid low-degree multi-pin candidates made
  before `batched-1-steiner-v1`. Replay now selects the candidate's recorded
  `astar-grid/0.4.0` component-MST behavior instead of reinterpreting it with the current
  one-Steiner default. Candidate bytes and identities are never refreshed; unknown or impossible
  historical router/order combinations refuse before rendering or applying copper.

### Added

- Added `preview_route_bundle`, a closed read-only MCP/application-service surface that accepts a
  bounded ordered set of revision-bound Circuit Scene net references and publishes one immutable
  multi-net plan only after deterministic composition replay and cross-net physical-clearance
  acceptance. It never issues an apply token, writes a board, exports a derivative, or returns a
  partial plan. A committed public-fixture replay records one combined private KiCad DRC derivative.

- Hardened the optional harness-owned KiCad/FreeRouting transaction behind an internal
  provider-created aggregate-quota workspace capability. The harness validates canonical
  owner-private, non-symlink roots; keeps temporary directories and child `cwd`/`HOME`/`TMPDIR`
  inside that boundary; and refuses before Java/KiCad probes, DRC, export, routing, import, or the
  optional runner when no provider is available. No provider is enabled on current macOS or Linux,
  so this adds no sandbox, parity, performance, or comparison-closure claim.
- Board IR and placement legality now accept a bounded exact courtyard topology: unfilled
  orthogonal `fp_poly` shapes and unordered closed orthogonal `fp_line` chains on the matching
  KiCad courtyard layer, alongside rectangles. The legalizer uses integer positive-area overlap
  rather than bounding boxes, charges its existing work budget, and refuses diagonal, curved,
  filled, open, branching, duplicate-edge, or otherwise unsupported shapes. The source fixture is
  resaved by KiCad 10.0.5 and has a zero-violation/zero-unconnected DRC oracle. Placement apply
  remains rectangular-only and does not rewrite this new observation topology.
- Added an Apache-2.0 CopperMCP-original NE5532-class stereo audio routing fixture with 14
  synthetic footprints, 35 pads, 11 nets, and no source copper. Its bounded benchmark invokes the
  public route-preview application service for eight independently replayed two- and multi-pin
  candidates, binds fixture/licence provenance, and records exact candidate IDs, path counts,
  lengths, vias, and search work. Optional KiCad 10.0.5 JSON DRC runs only on disposable,
  independent candidate derivatives; the recorded run reduced unconnected-item counts without
  claiming a clean or combined-board DRC result, electrical correctness, fabrication readiness, or
  FreeRouting parity.

- Added a read-only live KiCad IPC fidelity oracle. When launched by a KiCad plugin with the
  instance socket and token, it binds one confirmed live serialization to Board IR and Circuit
  Scene through redacted digest equality evidence. Missing plugin credentials produce a canonical
  non-failing capability skip before process settings are resolved; endpoint, token, timeout,
  session, version, and generic configuration failures remain distinct redacted results. One
  cooperative deadline spans capture and both conversion stages. This adds no MCP action, editor
  mutation, DRC, routing, placement, apply, or live-editor success claim.

- Added the first content-addressed held-out audio project-family evaluation. Its independently
  authored Apache-2.0 fixture is isolated from the predeclared training family by a hash-bound
  train/tune/held-out split, and the evaluator reads only the held-out board. Three exact replays
  recorded Board IR support, eight legal placement candidates after 96 bounded evaluations, and
  candidate-preview completion for 6/6 nets with no source mutation. No model, network, KiCad,
  DRC/ERC, policy-quality, routing-quality, external-project, fabrication, or hardware claim is
  inferred from this one-family baseline.

- Added a clean-worktree performance-profile baseline for file-backed routing, bounded placement,
  and Circuit Scene observation. Each scenario uses two warmups, five unprofiled timing samples
  with an invariant output digest, and one separate bounded `cProfile` pass with stable redacted
  function labels. The artifact identifies placement containment/intersection work as the largest
  measured seam on its single arm64/Python 3.14.2 environment; it adds no Rust, SIMD, GPU,
  acceleration, cross-machine, KiCad, DRC, or hardware-performance claim.

- Added a packaged standalone deterministic exact local-repair operator for a conventionally
  coordinator-supplied bounded lattice window. It emits only request-bound immutable local
  proposals with fixed failure/cancellation states. The predeclared 5 × 5 detour fixture replays
  identically 10/10 times at eight unit steps, two bends, and 50 expanded states; a one-expansion
  budget and cancellation publish no route. It remains outside negotiated routing, MCP, Board IR,
  KiCad, physical clearance, DRC, candidate application, and board mutation.

- Added a bounded, in-memory routing-task handle broker and a runtime MCP Tasks compatibility
  probe. The reference environment observed `mcp 2.0.0`, but the supported dependency range
  remains `<3`; the observed runtime lacks the current Tasks wire/dispatcher contract and
  CopperMCP lacks owner-bound durable task-handle lookup, so wire Tasks stay disabled and the
  ordinary routing-job tools remain the fallback.

- Negotiated multi-net routing now rejects a lattice-clean candidate set when its same-layer,
  cross-net orthogonal copper violates the stricter assigned net-class clearance. Generic routing
  backends are independently replayed through the deterministic reference core under a shared
  half-budget allocation before they can be accounted for or published. This is a bounded
  acceptance gate, not KiCad DRC or board-wide physical clearance.

- Added an internal, bounded deterministic placement-search baseline. It evaluates only
  legalizer-issued immutable candidates, scores the same-net Manhattan connectivity proxy in
  `O(n log n)`, and propagates one deadline plus cancellation through scoring and legalization.
  It is advisory and makes no KiCad mutation, routing-quality, DRC, or fabrication claim.

- Added a closed, advisory AI routing-policy seam for deterministic net ordering and
  coordinator-supplied corridor/repair-window selection. Its bounded, redacted trace omits board
  geometry and raw identifiers; policy output cannot emit copper or bypass deterministic routing,
  validation, DRC, or explicit apply authorization. Content digests remain linkable and are not
  secret redactions. The exact internal `deterministic-reference-v1` profile now influences only
  the initial negotiated net order; no-profile v2 result shape and candidate identity are
  unchanged. This adds no MCP, model, corridor, repair, or apply authority.

- Added an internal one-shot isolated worker protocol and admitted only its fixed
  `deterministic-reference-worker-v1` backend to negotiated routing's initial net order. It accepts
  the same neutral scalar, no-window input as the in-process reference; uses nonce- and
  digest-bound canonical responses with bounded timeout/cancellation and sanitized child process
  state; and rechecks the fixed policy identity, input digest, complete known-net permutation,
  empty selections, and composite candidate binding before router construction. Any worker failure
  refuses with no fallback or router call. Retry order, geometry, validation, and every routing
  budget remain coordinator-owned. This adds no model, plugin, MCP, corridor, repair, KiCad,
  copper, or apply authority, and it is not an OS sandbox.

- Added the first real FreeRouting smoke record through the bounded GPL-isolated process boundary.
  The official v2.2.2 JAR produced a valid SES for one CopperMCP-original two-pad fixture, and
  KiCad GUI 10.0.5 DRC observations recorded zero hard violations and zero unconnected items on
  both the imported FreeRouting result and CopperMCP's pure-kernel result. Source/report,
  source/DSN-export, import, and runner relationships remain self-attested rather than causal;
  the CopperMCP runner does not exercise MCP or the authorized apply service. The artifact
  therefore retains `comparison_closed=false` and `unavailable_or_incomplete`, with no parity,
  performance, whole-board, or sandbox-containment claim.

- Added an OpenSSF-informed sustainability and supply-chain roadmap. It separates the Criticality
  Score activity proxy from Scorecard controls, records the dated `0.23`/`5.8` baseline as an
  estimate and a distinct API snapshot, and defines engineering-backed gates for a defensible
  `0.4` target without synthetic project activity.

- Live placement proposals now preserve one operation-wide deadline after IPC capture: Board IR
  conversion, placement-view construction, legalization, optional evidence, and token preparation
  receive only the remaining budget. An expired post-capture budget returns a typed refusal instead
  of silently granting a second full placement window.

- Post-placement observation now validates the complete scene request before any workspace read,
  rejects stale board revisions before scanning DRC sidecars, and refuses padless footprint rule
  references before syntactic infeasibility analysis. These boundaries keep malformed/stale work
  fail-closed and preserve the supported placement contract.

- Negotiated-congestion routing now treats cancellation-callback failures as cancellation and
  never publishes a partial candidate from that iteration, including when a later net cancels an
  otherwise productive pass or when cancellation arrives before the next retry. Layered candidate verification also
  binds track width, via diameter, and via drill to the Board IR net-class assignment, rejecting
  re-stamped dimensions before topology acceptance.

- The post-placement observation benchmark now fingerprints workspace entry inode and mtime
  metadata in addition to bytes, mode, and symlink targets. Its replay can therefore detect
  metadata-only observer mutations instead of treating them as a clean workspace.

- Live route and placement proposals now create their operation deadlines before IPC capture and
  pass both the absolute deadline and remaining millisecond timeout into the bounded KiCad
  adapter. A slow snapshot cannot silently consume the adapter default beyond the proposal budget.

- Apply replay protection now retains every consumed nonce until its own expiry instead of
  evicting live entries under count pressure. The compatibility capacity hint is validated but
  cannot weaken single-use authorization after a pre-apply backup is restored.

- Durable routing-job lookup now commits TTL purges even for malformed or unavailable IDs, and a
  stale `CANCEL_REQUESTED` lease is terminally acknowledged instead of remaining stranded until
  retention expiry. The lifecycle remains redacted and compare-and-swap bound.

- Live KiCad IPC board counting now carries its cooperative operation deadline into bounded
  S-expression decoding, with pre/post-decode and 4 KiB scan checkpoints. Expired large or
  malformed snapshots fail with the typed deadline refusal instead of spending unbounded parse
  time first (ADR-0063, B-051).

- Placement legality now keeps supported rectangular courtyards from padless/graphics-only
  footprints as stationary collision envelopes. Padless objects remain unplaceable and absent from
  candidate manifests, but movable footprints can no longer be reported clear through them
  (ADR-0062, B-050).

- Added read-only `observe_post_placement`: a required-revision, single-capture Circuit Scene and
  aggregate KiCad DRC observation. It is fail-closed on stale or changed context and never exposes
  raw DRC output, issues apply authority, or changes a board (ADR-0061, B-045).

- Added a separately authorized `apply_placement_candidate` MCP capability for the bounded
  front-side, orthogonal, source-preserving footprint subset. File-backed previews issue a
  placement-scoped single-use token only when the exact replay accepts the candidate; the apply
  path uses operator opt-in, lockfile refusal, double CAS, a recoverable pre-apply copy, atomic
  replacement, and a typed `footprints_moved`/`bytes_changed` result. Side flips, unsupported
  footprint properties/graphics/library/3D-model syntax, no-op candidates, live IPC mutation, and
  post-placement DRC remain fail-closed under ADR-0059.

- Added a deterministic, CopperMCP-original Apache-2.0 synthetic RC audio-routing microcase.
  Repeated previews produce one reproducible candidate and one new copper segment in a disposable
  derivative; the source board is unchanged and the fixture makes no DRC, apply, production,
  fabrication, or hardware claim. Evidence is recorded in B-043.

- Added deterministic same-side courtyard legality to placement previews for the Board IR v0.2
  rectangular subset. The legalizer transforms proposed poses, checks exact integer rectangle
  overlap, treats front/back courtyards independently, and refuses overlapping candidates; custom
  courtyard clearance, general topology, placement connectivity, and apply remain open under
  ADR-0058.

- Added a bounded `kicad_schematic_parity` verifier for the passive Circuit Intent subset. It
  requires exact renderer replay and checks real KiCad format-E `kicadxml` component, pin, and
  net-node parity with bounded hostile-input handling; authoritative ERC and schematic-to-PCB
  parity remain open. The fixture and evidence are recorded under ADR-0056 and B-039.

- Added front/back (`F.Cu`/`B.Cu`) observation for orthogonal footprints with matching rectangular
  courtyard centerlines. The adapter preserves KiCad's authored board-frame child coordinates and
  does not apply a second mirror; GUI flip-save, general courtyard topology, placement legality,
  and apply remain open. The source/CLI oracle is recorded under ADR-0057 and B-040.

- Added a bounded, deterministic negotiated-congestion coordinator for two-pin nets on one
  signal-layer lattice. It uses present and historical edge/vertex pressure to reroute conflicted
  candidates, binds each accepted candidate to the policy digest, and records structural overflow
  evidence in B-036. It remains candidate-only: exact physical clearance, multilayer vias, KiCad
  DRC, apply, and FreeRouting parity are not claimed.

- Added an internal, candidate-bound KiCad DRC gate for the supported placement serializer. It runs
  against a disposable private context, rechecks source/rule/library CAS, and returns only a
  redacted aggregate summary; public/live placement and apply remain unchanged.

- Added opt-in, file-backed placement DRC evidence to `preview_placement`. `include_drc: true`
  replays the immutable candidate through the same private KiCad context gate and returns only
  candidate/source/patched-board/context digests plus aggregate findings. `passed` remains the
  hard error/connectivity signal while `clean` is stricter about warnings, exclusions, and ignored
  checks; live placement, apply authority, raw reports, and fabrication claims remain excluded.
  Evidence is recorded in B-044.

- Added a redacted, deterministic unsigned in-toto Statement payload to candidate-bound DRC
  evidence. The Link v0.3 payload binds the candidate and board revisions by digest, carries only
  aggregate DRC byproducts, and is validated at the MCP boundary; DSSE signing and verification
  remain intentionally deferred.

- Added a deterministic conservative spatial index to the A* and benchmark Dijkstra obstacle
  hot path. Exact integer legality predicates remain authoritative, small/pathological boards
  fall back to linear scans, and candidate identity advances to `astar-grid/0.6.0` with policy
  `orthogonal-a-star-spatial-index-v1`. B-033 records differential route equivalence and the
  fixture-bounded reduction in exact obstacle relations; no congestion, FreeRouting, DRC, or
  fabrication claim is made.

- Added opt-in candidate-bound authoritative KiCad DRC evidence to file-backed
  `preview_layered_route`. The closed response binds candidate, base, source, patched-board, and
  DRC-context revisions while returning only aggregate findings; live layered preview and durable
  routing jobs reject the flag instead of silently ignoring it. This remains a narrow two-signal-
  layer proposal signal, not whole-board, refill, fabrication, or FreeRouting authority.

- Hardened layered DRC evidence with a strict `clean` signal distinct from the hard-gate
  compatibility field `passed`: warning, exclusion, unconnected, or ignored-check findings can no
  longer be presented as a clean report. The public boundary now rejects malformed or
  candidate-unbound authority, with warning-only and malformed-authority regressions recorded in
  B-038.

- Added a bounded `batched-1-steiner-v1` ordering policy for low-degree multi-pin nets. It keeps
  the deterministic A* core and all geometry validation authoritative while reducing the recorded
  four-pad fixture's wire length from 48 mm to 42 mm; no Steiner-optimality or FreeRouting parity
  claim is made.

- Added a bounded, restart-safe routing-job repository and ordinary MCP lifecycle surface. The
  file-backed two-signal-layer queue persists deep-frozen normalized requests, redacted manifests,
  and separately authorized content-addressed candidate geometry behind TTL/capacity limits;
  `start_routing`, `get_routing_job`, `cancel_routing_job`, and `export_routing_candidate` never
  apply copper, accept board bytes, or claim MCP Tasks compatibility.

- Added a bounded SQLite `CandidateManifestStore` for restart-safe, content-addressed candidate
  summaries. It persists only redacted identity, endpoint, cost, and metric metadata; route
  geometry, board bytes, DRC findings, durable export, and MCP Tasks remain separate capabilities.

- Added a protocol-independent `RoutingJobWorker` with one active CAS-backed lease, cooperative
  cancellation, stale-lease recovery, and fail-closed invalid-candidate publication. The worker
  stores only the existing redacted job record; candidate persistence/export and MCP Tasks remain
  deferred until their request/result and authorization contracts are pinned.

- Added a pure, bounded `verify_layered_candidate` gate for layered route topology. It binds
  candidate identity, Board IR revision, endpoints, layer transitions, path/via continuity, and
  duplicate/crossing geometry before the disposable serializer; physical validation remains an
  explicit `not_modelled` result.

- Added a transport-independent, revision-safe routing-job ledger. `RoutingJobStore` persists only
  bounded redacted JSON records in SQLite, supports idempotent content-addressed creation,
  compare-and-swap transitions, cooperative cancellation, restart rehydration, TTL/capacity
  limits, and candidate ID/base-revision binding. It does not run background routing, persist
  candidate geometry, expose MCP Tasks, export boards, or grant apply authority.

- Added `preview_live_layered_route`, a read-only MCP proposal for bounded two-signal-layer routes
  against the exact active KiCad IPC snapshot. It requires pad references plus source, Board IR,
  and redacted KiCad-session compare-and-swap digests, reuses the file-backed candidate oracle,
  and remains candidate-only with no DRC, refill, serializer, persistence, or apply authority.

### Fixed

- `inspect_live_board` now returns the opaque, fixed-format PBKDF2 session CAS as required
  structured output when KiCad supplied a plugin token, or explicit `null` when it did not. This
  makes the public inspection → live-scene → layered-preview flow composable while keeping the
  token and process salt private; changed tokens and fresh CopperMCP processes still refuse stale
  live-route requests.

- Placement validation now preflights every declared subject in request order before syntactic
  infeasibility analysis. A known padless subject paired with an unrelated front/back contradiction
  now returns the established fixed `unsupported_geometry` refusal with no candidate.

- Placement validation now preflights every explicit proposal anchor that names a known padless
  footprint after rule references and before syntactic contradiction analysis. For each supported
  anchor point (`center`, `north`, `south`, `east`, and `west`), that mixed request returns the
  established `unsupported_geometry` refusal with no candidate instead of allowing unrelated
  contradictory side rules to mask it as `infeasible_constraints`. Self-anchored proposals and
  pure contradictions retain their existing behavior. This changes validation ordering only: it
  adds no anchor geometry, padless placement, non-padless placement behavior, DRC, apply, or board
  mutation capability.

- Replaced the public unkeyed `sha256(KICAD_API_TOKEN)` live-session fingerprint with the fixed
  `hmac-sha256:<64 lowercase hex>` wire type. A fresh 256-bit process-local key and
  domain-separated HMAC-SHA256 make this precondition opaque, use constant-time comparison at
  the session CAS checks, and deliberately refuse preconditions from a fresh process/restart.
  The token and process key remain absent from outputs, errors, logs, candidates, and ledgers.

- Superseded the prior HMAC session-revision derivation with fixed-work
  `pbkdf2-hmac-sha256:<64 lowercase hex>`. The process-local 256-bit salt is domain-separated
  and non-persistent; the fixed 200,000-iteration PBKDF2-HMAC-SHA256 derivation keeps
  limited-input token guesses computationally expensive and remains bounded for local CAS.
  The HMAC history remains recorded in D-127/SEC-103/R-102; legacy HMAC and unkeyed SHA-256 wire
  values are refused.

- Durable routing jobs now validate every immutable candidate-to-job completion binding and the
  exact `RUNNING` lifecycle revision before writing bounded, owner-bound candidate artifacts, then
  revalidate at the final lifecycle CAS. Invalid candidates, and direct queued, terminal,
  cancel-requested, or stale-revision calls, leave no export or manifest; a worker returns a fixed
  invalid-request failure while the direct publisher leaves its revision-bound job retryable.
  Valid artifacts still publish
  before completion, so capacity/serialization failure cannot create a completed job without an
  export. A concurrent completion race can leave only an unreadable TTL-bounded orphan. Request
  expiry and invalid cancellation text now also reach both request and lifecycle retention cleanup
  before their fixed refusal.

- Corrected the public policy benchmark provenance contract to name the artifact's
  `evidence_harness_commit` field: the stable source/harness commit used by the embedded replay
  command. The separately recorded artifact materialization commit remains distinct; no replay
  artifact, measurement, policy authority, or routing behavior changed.

- Hosted CI now checks out full Git history before running tests, so benchmark regressions that
  replay an immutable `evidence_harness_commit` with `git show` do not fail only because the source
  commit was omitted by a shallow clone. A workflow regression pins `fetch-depth: 0`; this proves
  repository configuration, not hosted-run success or the correctness of a benchmark artifact.

- Expired routing-candidate geometry exports now commit their TTL purge before returning the
  deliberately uniform unavailable response. This preserves the access-retention boundary for
  stored `candidate_json`; TTL is not a secure-erasure guarantee for SQLite, backups, or copies a
  caller already received.

- The advisory placement solver now checks cooperative cancellation again at its publication
  boundary. When cancellation is observed, it returns an empty ranked-candidate set rather than
  exposing a partially explored ranking; this does not hard-interrupt already-running work.

- Unavailable routing-request lookups and unauthorized live candidate-export lookups now commit
  any prior TTL cleanup before returning their uniform unavailable response. An unauthorized
  live export remains intact, while unrelated expired private records are removed; decode and
  integrity failures still roll back, and TTL is not secure erasure.

- Malformed routing request and candidate-export handles now also begin and purge their stores
  before the uniform unavailable response, committing expired private-record cleanup. Lookup
  timestamps remain validated before any transaction, and TTL remains an access-retention policy,
  not secure erasure.

- Public routing-job/export lookups now defer store-owned handle validation until after retention
  cleanup, and candidate-manifest lookup purges before malformed-ID not-found handling. Fixed
  diagnostics and no-disclosure behavior are unchanged; timestamps remain validated first and
  TTL is not secure erasure.

- MCP routing get, cancel, and export entrypoints now accept handles broadly enough to reach the
  purge-first service/store boundary. Their payloads remain closed, diagnostics remain fixed and
  non-disclosing, valid requests are unchanged, and TTL is not secure erasure.

### Changed

- Corrected the spatial-index benchmark to count bucket candidates examined by the exact bounds
  predicate rather than only final hits. The regenerated B-037 artifact reports `636` indexed
  candidates versus `131,072` linear checks (`99.5148%` reduction); the older `31` metric is kept
  only as historical evidence and is no longer used for performance claims.

### Security

- Added a deterministic offline MCP excessive-agency regression evaluation. With apply enabled it
  calls the public route and placement apply handlers using syntactically valid but unauthorized
  tokens, requiring structured `invalid_token` before source access; it also covers closed
  request schemas, stale revision, quotas, and output/report disclosure. The disposable workspace
  comparison includes content, permissions, and metadata but the artifact records only stable
  unchanged assertions. It invokes no model, network, KiCad process, or board mutation, and does
  not evaluate application logging because the current source has no application logger sink.

- `make security` and the hosted security workflow now pass `.` to `pip-audit`, resolving the
  default production dependency graph instead of reporting unrelated packages installed in the
  ambient interpreter. Project-path mode excludes every optional group (`dev`, `security`, and
  `kicad`) even though CI installs `.[security]` to run the auditor; those groups need separate
  explicit audits or lock evidence. Secret scanning is unchanged.

- Hardened placement application after review: bounded manifest pose, grid, legality, and evidence
  fields are rejected before board parsing; the destructive MCP request is closed at its nested
  boundary; post-rename and post-publication paths spend the capability exactly once; and the
  final board revision is re-read after guarded recovery so an observed rollback cannot report a
  stale published digest. Published placement bytes are re-rendered and reparsed before a success
  response, followed by a final best-effort digest observation that catches a visible rewrite after
  verification. `applied_but_unverified` now explicitly permits a restored original revision or
  concurrent bytes, and uses a null after-revision when the board cannot be observed; clients use
  the reported digest and diagnostic rather than assuming that the authorized revision remains on
  disk.

- Route and placement apply now distinguish an unreadable or missing board at the final
  publication observation from the expected digest: the capability is consumed and the result is
  `applied_but_unverified` with a null after-revision instead of a false `applied` response.

- Placement DRC evidence is now a public, read-only opt-in only for the documented file-backed
  serializer subset. KiCad runs with fixed JSON DRC arguments and no refill/save flags against a
  disposable context; source bytes, inode, and mtime remain unchanged, and malformed, stale, or
  unbound evidence fails closed at the MCP contract boundary.

- Routing-job and candidate-manifest TTL misses now commit their expiry purge before returning
  the uniform unavailable diagnostic, so expired board-derived metadata cannot reappear after a
  read-only miss. The boundary remains redacted and bounded; no routing or mutation authority is
  added.

- Routing workers now clear their in-memory lease even when an expired job disappears during
  cancellation acknowledgement or publish-race resolution. This prevents a worker from being
  stranded after a bounded store miss and adds no retry or mutation authority.

- Live KiCad capture now checks the shared deadline while traversing the bounded serialized
  S-expression and preserves the typed deadline refusal during confirmation. The official
  synchronous IPC wrapper remains cooperative: a blocking third-party call cannot be forcibly
  pre-empted by this Python process.

- Closed the latest routing review-bot boundary gaps: public scene references now carry only
  content-derived net identifiers; live IPC capture carries one cooperative operation deadline;
  job failures persist fixed typed diagnostics; candidate completion and manifests bind request
  identity, router policy, seed, and work budgets; expired manifest rows are purged transactionally;
  and layered obstacle envelopes remain conservative before budget exhaustion. These changes add no
  mutation, remote transport, or general multilayer authority.

- Tightened the in-toto MCP contract so every resource descriptor has a required, closed nested
  `sha256` digest object; `{}` and unknown digest algorithms now fail validation. B-034/B-035 were
  replayed from the implementation commit and recorded as append-only current-contract evidence.

- Layered serialization now refuses structurally disconnected, crossing, duplicate, stale, or
  endpoint-via candidates before rendering. The layered router reserves endpoint pad envelopes for
  tracks and blocks via transitions there, avoiding unsupported via-in-pad geometry without
  claiming fabrication legality.

- Added `preview_layered_route`, a loopback/file-backed, read-only MCP boundary that requires
  both source and Board IR compare-and-swap digests, infers net identity from two pad references,
  validates bounded settings, verifies candidate digests, and redacts board text/net names. It
  cannot request DRC, refill, serialization, export, persistence, or apply authority.

- Layered routing now validates every obstacle and search-budget field before reporting resource
  exhaustion, and its physical envelopes include the candidate track half-width/via radius plus
  explicit zone clearances. Malformed requests cannot be reclassified as stale revisions or escape
  the non-throwing diagnostic contract. Fresh fill obstacles apply the same governing zone-clearance
  rule as conservative zone envelopes.

- `preview_live_placement` is revision-bound and read-only: malformed requests fail before IPC,
  stale board/snapshot digests stop before candidate work, and no KiCad write, DRC, fill,
  apply-token, or raw source can be requested through the contract.

- Live IPC-to-scene conversion now keeps the exact UTF-8 snapshot paired with its redacted digest
  and refuses a caller-supplied board or Board IR snapshot revision mismatch before returning a
  scene. The live tool uses the literal `board: "live"`, refuses render delivery, and does not
  grant routing, placement, DRC, or apply authority.
- Live-scene requests are now fully preflighted before any IPC connection or board serialization:
  malformed constraints, regions, layers, unknown fields, and the unsupported render flag fail at
  the application boundary, keeping invalid MCP traffic from driving expensive KiCad reads.
- The optional KiCad IPC observer is constrained to a local IPC socket, bounded by a connection
  timeout and board-size/object-count ceilings, and refuses a future KiCad API version unless an
  explicit development-only opt-in is supplied. It returns only numeric versions, a board digest,
  byte count, object counts, and the socket kind; socket paths, tokens, board text, names, UUIDs,
  and geometry never cross the MCP boundary. The bundled KiCad plugin exposes the same read-only
  surface and does not mutate an open document.
- KiCad IPC version validation now fails closed when the official binding returns a false result.
  Observer counts come from the captured serialization rather than mutable per-object getters,
  count only direct board-level net declarations, include circular graphics, and require a second
  serialization to match before the revision is accepted. The wrapper still
  allocates its complete response before Python can enforce the size ceiling; this residual API
  limitation is documented, while bounded parsing and removal of extra collection materialization
  reduce avoidable memory exposure.
- Live IPC clients are now closed on every observation success and failure path, and live layered
  proposals pass the remaining bounded route deadline into the official client. A hashed
  `KICAD_API_TOKEN` session precondition prevents identical board bytes from silently crossing a
  KiCad instance restart; the raw token remains outside all outputs and logs.
- Scene-selected routes now require both the observed board revision and Board IR snapshot digest.
  Stale source bytes are refused before Board IR conversion, while a stale snapshot is refused
  immediately after it; neither can reach route search, fill authority, DRC, or apply-token
  issuance. Shared request text now also rejects invalid Unicode surrogates before path handling or
  net hashing, and malformed MCP requests remain behind the fixed non-echoing application boundary.
- Board IR 0.2 makes footprint ownership revision-bound and budgeted: footprints count against the
  object ceiling, courtyard vertices and intersection work use polygon ceilings, one footprint is
  capped at 64 courtyard rings before geometry allocation, and Circuit Scene charges serialized
  pad relationships against its detail budget. Placement projection re-verifies canonical snapshot
  ordering and digest binding, applies caller-tightened Board IR limits before projection, and
  rejects forged footprint content instead of issuing a view under a stale digest. Locked
  footprints now refuse movement proposals.
- Candidate apply now holds an **exclusive `flock` across the compare-and-swap and the rename**,
  closing a confirmed concurrency hole: two applies from the same base both passed the checks and
  one silently destroyed the other. The board's digest is re-verified under the held lock
  immediately before the rename, so the second apply sees the first's bytes and refuses. The
  earlier docstring claimed a lock that did not exist; the lock is now real and the claim true.
- The post-publish rollback is now **guarded**: it restores the pre-apply bytes only if the file
  still holds exactly what the apply wrote. The likeliest cause of a verification failure is a
  concurrent writer, so the previous unconditional restore was itself the data loss it was meant
  to prevent - a KiCad save landing after publication would have been overwritten by the "safety
  net". A third party's newer write is now left intact.
- A failure *after* the rename is reported truthfully as a new `applied_but_unverified` status
  with the real post-apply revision, never as a refusal claiming "nothing changed". Previously the
  four post-rename failure sites (directory fsync, re-read, parent-identity checks) mapped to a
  refusal with a null after-revision while the board was already mutated, making
  `board_revision_before` a stale lie.
- The KiCad lockfile is **re-checked under the lock immediately before the write**, not only
  seconds earlier. A GUI opened between the up-front check and the write would otherwise have its
  next save silently overwrite the applied board.
- An uncaught `KiCadRoutePatchError` no longer escapes the destructive tool on a legal board.
  A board whose outline carries a derived rather than native identity is rejected as a typed
  `splice_assertion_failed` refusal, and `preview_route` no longer mints an apply token for a
  board the append-only engine could never apply to - or when apply is disabled.
- The apply token is verified **before** the board is read and parsed, and the candidate geometry
  in a manifest is bounded before any of it is materialised, so an unauthorized or oversized
  request cannot drive the expensive pre-authorisation work.
- Consumed apply-token nonces are swept by **expiry rather than a count cap**. A FIFO cap could
  evict a still-valid nonce and re-enable a replay, and the documented undo restores the exact
  revision that nonce was bound to.

### Changed

- Freshness-verified foreign KiCad fill islands now replace the conservative whole-zone routing
  envelope in the deterministic A* core. Matching zone/source revisions are required, stale or
  unmatched fill fails closed, explicit zone clearance is retained, and `preview_route` now returns
  the freshness-bound authority on routed candidates with a typed `routing_effect` when
  `include_fill_authority` is requested.
- Layered two-signal-layer candidates now have an internal, replay-bound authoritative KiCad DRC
  gate. The gate serializes only a disposable derivative, preserves source bytes, binds the
  complete private DRC context, and returns redacted aggregate evidence; it is not exposed through
  MCP/CLI and does not grant apply authority.
- Placement candidate rendering now preserves arbitrary exact Board IR pad angles while retaining
  the orthogonal-only restriction on parent footprint poses. Layered A* now reports a distinct
  obstacle-count budget diagnostic when a structurally valid request exceeds its configured ceiling.

- Added an internal, Board IR-bound two-layer routing proposal adapter. It resolves exact
  nanometre grid geometry, net-class width/clearance/via dimensions, foreign physical envelopes,
  track versus via-only keepouts, immutable candidate identity, and fail-closed stale/off-grid/
  unsupported diagnostics. This remains proposal-only: the public MCP wrapper exposes only the
  bounded candidate contract; it does not serialize KiCad segments or vias, invoke DRC, mutate a
  board, or claim production routing through vias.

- Added a request-replayed, source-preserving serializer for the layered proposal seam. It emits
  deterministic segment and full-stack through-via expressions, canonicalizes reversed via
  transitions to KiCad copper-stack order, rejects native-identity collisions, and requires a
  Board IR round trip equal to the source plus the candidate geometry. It remains disposable and
  candidate-only: no file write, KiCad invocation, DRC, MCP exposure, or apply authority.

- Added an internal, non-public two-layer A* search oracle with explicit via transitions,
  deterministic tie-breaking, per-layer cell obstacles, cancellation, and bounded resource
  accounting. It is algorithmic evidence toward via-capable routing only; it does not produce
  Board IR/KiCad candidates, change existing single-layer candidate IDs, or claim DRC validity.

- The KiCad IPC plugin README now documents the required copy into KiCad's configured PCB plugin
  discovery directory. Installing `copper-mcp[kicad]` alone does not register the hardware-side
  manifest or action, and the plugin remains intentionally outside the Python wheel.

- `preview_route` accepts exactly one net selector: the existing private KiCad `net` name or a
  Circuit Scene `net_ref_id`. The MCP tool now advertises a closed two-variant input schema and a
  complete closed, status-specific structured-output union instead of an open object; impossible
  candidate/connection/diagnostic combinations are no longer advertised. Its annotation is
  conservatively non-idempotent because `include_apply_token` can mint a fresh capability even when
  the candidate geometry is deterministic.
- The active Board IR writer and decoder now target exact `copper.board-ir` `0.2.0`. Historical 0.1
  schema and golden data stay immutable, while migration requires re-converting the original board
  because flattened 0.1 pads cannot recover trustworthy parent identity or pose. Snapshot digests
  change; constraint digests do not change from footprint-only data.
- Placement grouping is now projected from the same Board IR snapshot that carries the pads and
  rejects mismatched source bytes, replacing the second out-of-band KiCad identity parse. Route
  serialization also requires native footprint identities before producing output.
- File-backed placement previews now honor optional board and Board IR snapshot compare-and-swap
  digests before placement-view/legalizer work; stale requests return a typed refusal instead of
  echoing unverified preconditions.
- Applied and backup files now keep the board's own **permission bits** instead of collapsing to
  `0600`, so group and CI readability and hard links survive an apply.
- Pre-apply copies are written into a `.copper-mcp-backups/` subdirectory, not beside the board
  where a `pre-apply.kicad_pcb` would itself be a valid apply target and cascade, and are pruned
  to a bounded count per board so a preview→apply loop cannot exhaust the disk.
- The `apply_disabled` refusal now reports the canonical workspace-relative path and no longer
  synthesises a `sha256("")` digest for a board it never read; the refusal's revision is null.
- `replace_workspace_file` gained the confinement-preserving lock, compare-and-swap, mode
  preservation, and pre/post-rename failure split described above; `resolve_workspace_relative_path`
  resolves a confined path without reading the file, for the pre-authorisation checks.


### Added

- Added the public, candidate-only `preview_layered_route` MCP tool for the narrow two-signal-layer
  Board IR router. It returns closed structured output with per-layer paths, full-stack vias,
  deterministic metrics, and typed stale/unsupported/no-path diagnostics. B-024 records ten
  schema-valid deterministic replays, stale CAS refusals, and unchanged source bytes; it does not
  claim general multilayer routing, KiCad DRC, fabrication readiness, or FreeRouting parity.

- `inspect_live_editor_context`, a read-only MCP surface for the active KiCad layer and bounded
  native selection refs, bound to the raw board serialization and editor-context digests. It now
  avoids treating a constraint-profile-dependent Circuit Scene snapshot as an IPC serialization
  precondition. The FreeRouting comparison note documents its heuristic maze/rip-up architecture
  and a common-board benchmark protocol; no general routing-quality superiority claim is made.

- An internal source-preserving KiCad placement projection for the supported front-side,
  orthogonal, unfilled-courtyard footprint subset. It is candidate-only, revision-bound, rejects
  forged or incomplete placeable sets, preserves padless mechanical footprints and unrelated
  source bytes, and reparses the disposable result against the expected Board IR transform.
  Placement DRC, live editor mutation, undo, and post-action observation remain separate gates.

- `preview_live_placement`, a deterministic placement proposal over one byte-confirmed active
  KiCad IPC snapshot. It reuses the file-backed legalizer and requires both Circuit Scene digests;
  B-014 records fake-client equality, replay, stale-precondition, and zero-mutation evidence.

- `preview_live_route`, a read-only MCP route-proposal tool that consumes a Circuit Scene
  `net_ref_id` plus both board/snapshot revisions, converts the exact active KiCad IPC snapshot,
  and returns the existing deterministic candidate contract. DRC, zone refill, apply-token
  issuance, live editor mutation, and real-session success remain explicitly unclaimed; B-013
  records the fake-client oracle, stale-session refusals, and zero-call action preflight.
- `observe_live_board_scene`, a read-only bridge from the active KiCad IPC document to Circuit
  Scene `0.2.0`. It reuses exact Board IR geometry and author-text quarantine, with optional
  compare-and-swap digests for stale-session refusal; live action gates remain separate.
- An optional official `kicad-python` integration: the read-only `inspect_live_board` MCP tool and
  `hardware/kicad-ipc-plugin` action provide a redacted live-board observation contract while
  keeping placement, routing, DRC, and candidate application behind separate validated gates.
- A reproducible MCP observation-to-action benchmark over the licensed RC low-pass audio fixture.
  It pins the former 0/3 actionable Scene references against 3/3 revision-bound references, exact
  candidate equality with the hidden-name oracle, stale-reference refusal, deterministic replay,
  closed schemas, and an identical final private-workspace file tree.
- First-class immutable Board IR footprints with exact origin, normalized rotation, side, lock
  state, total pad ownership, and canonical board-frame rectangular courtyard rings. A compact
  KiCad fixture pins all four orthogonal transforms and passes KiCad 10.0.5 DRC with zero violations
  and zero unconnected items.
- Circuit Scene IR `0.2.0` footprint objects. Region queries can anchor on a footprint and return
  revision-bound pose, side, pad IDs, supported courtyard rings, lock state, and reference
  durability without exposing footprint names, values, properties, or other author text.
- Board IR 0.2 JSON Schema, golden/invalid fixtures, migration guidance, and ADR-0026. The schema
  requires `items.footprints`, closes every nested object, and preserves the 0.1 schema unchanged.
- `apply_candidate`: the first and only operation in this project that changes a user's board.
  It applies **route patches only**, and three independent things must hold before a single byte
  is written. The operator must have set `COPPER_MCP_ALLOW_APPLY=1` — matched as exactly `"0"` or
  `"1"`, because `bool("false")` is `True` and a flag that enables board mutation must not be
  switched on by an ambiguous spelling. Over MCP the caller must present a single-use token that
  `preview_route` issued for exactly this candidate, board revision and path, verified with
  `compare_digest` against an HMAC key that exists only inside the running process — so a model
  cannot mint one, and outstanding tokens do not survive a restart. And KiCad must be closed: a
  `~name.lck` sibling is a hard refusal that names the file and is **never removed**, because
  pcbnew has no external-change watcher and would silently overwrite the applied board on its
  next save.
- Revision-race protection. The whole-file digest and the Board IR snapshot digest are compared
  before the splice, and the file digest again immediately before publication; a mismatch returns
  `stale_candidate` and is **never auto-refreshed**, because re-routing against copper the caller
  has not seen would apply a proposal nobody approved.
- A timestamped, content-addressed pre-apply copy written beside the board before anything is
  replaced, with its path returned. **That copy is the undo, and restoring it is manual** — there
  is no `undo_apply` tool and no journal, and it never appears in KiCad's undo stack. KiCad's own
  `-bak` files are never read, written, or removed.
- `replace_workspace_file`, the project's only clobbering primitive, placed beside
  `create_workspace_file` so it inherits the same descriptor-anchored no-follow walk, symlink
  refusal, and post-write read-back verification. It writes an `O_EXCL` temporary in the target's
  own directory, `fsync`s it, renames over the name through a held directory descriptor, and
  `fsync`s the directory. `os.rename` rather than `os.replace`: on POSIX both are the same
  `renameat` syscall and both replace atomically, while `os.replace` does not accept `dir_fd` on
  macOS and would have forfeited the descriptor anchoring.
- Unsafe-filesystem refusal where it is cheaply detectable. `statvfs` names the filesystem on
  macOS and the BSDs but not on Linux, so a negative result means *not detected*, never *known
  safe* — which is why detection refuses rather than reassures.
- `apply_candidate` stays **listed even when applying is disabled**, refusing with
  `apply_disabled`. Hiding it would make the capability undiscoverable and invite retry loops; a
  tool that vanishes when a flag is off looks like a broken server rather than a locked door. Its
  annotations declare `destructiveHint: true` and `readOnlyHint: false` truthfully, but they are
  advisory client hints and enforce nothing — authorization is the flag and the token.
- A `copper-mcp apply-candidate` CLI command. It deliberately takes **no token**: the signing key
  lives only in the issuing process, so a token from an earlier `preview-route` run could never
  verify in a later `apply-candidate` run, and requiring one would be a flag satisfiable only by
  a value the same process just invented. The CLI's authorization is the operator flag plus the
  `--expect-board-revision` compare-and-swap the operator states explicitly.

### Fixed

- `inspect_live_editor_context` no longer treats a Circuit Scene's constraint-profile-dependent
  `snapshot_digest` as the raw IPC serialization precondition. The context read now binds to the
  observed `board_revision`; its response alias remains explicit, and `context_digest` still
  protects follow-up selection/layer reads.

- Added a revision-bound `inspect_live_editor_context` MCP surface that reports only KiCad's
  active layer and bounded native selection references. Unknown/empty selections, raw selection
  strings, and editor mutations are refused or never read.

- A failure while writing the pre-apply copy now returns a typed `backup_failed` refusal instead
  of escaping as an uncaught `OSError`. Found by crash injection: no copy means no way back, so
  the apply must stop rather than proceed without one.

### Added

- A byte-preserving span-splice layer over the KiCad S-expression parser (`adapters/cst.py`):
  expression spans, an overlap-rejecting `Splice`, and a source-level splice that decodes once and
  encodes once. It extends the existing parser rather than adding a second tokenizer, which would
  have duplicated the budget ceilings that keep parsing bounded. **Offsets are character indices,
  not byte offsets** - the parser decodes strictly before tokenizing, and both reference boards
  contain multi-byte characters (an em-dash in CopperTone, a `µ` in the Board IR subset fixture),
  so conflating the two would corrupt exactly the boards under test. Splicing in the character
  domain is nonetheless byte-exact because strict UTF-8 decoding round-trips, which is asserted on
  all 26 committed boards. Overlapping splices are refused outright rather than resolved: every
  resolution rule - last wins, longest wins, merge - is a silent guess about intent.
  `_rewrite_writer_metadata` was refactored onto the new module, with the existing candidate-DRC
  suite as the regression proof.
- A pure route-candidate apply engine (`copper_mcp.apply`): given board bytes and a verified
  candidate it returns the bytes an apply *would* write, proven by a three-part assertion - every
  untouched byte bit-identical (checked in bytes, not characters), the result reparsing through the
  fail-closed adapter with no diagnostics, and the resulting Board IR equalling the source IR plus
  the candidate exactly. Route patches are inserted at the root's closing delimiter, which
  measurement selected rather than convention: after a real KiCad save this repository's own board
  interleaves segments and vias across four runs, so there is no "segment section" to append to,
  and the root close is the only position that modifies no existing span - leaving 99.999% of the
  file untouched before the splice and 2 bytes after it.
- Verified against real KiCad rather than asserted: the applied board opens, the net KiCad
  previously reported as unconnected becomes connected, no DRC error is introduced, and KiCad keeps
  the added segments when it later rewrites the board itself.
- A candidate is never trusted from its manifest. The engine recomputes the candidate identity and
  replays the geometry against the board before splicing, so a tampered candidate is refused even
  when its digest has been recomputed to match its own altered contents.
- An applied board is deliberately **not** stamped with CopperMCP writer metadata. The disposable
  board rendered for candidate DRC is our derivative and claims authorship honestly; an applied
  board is the user's file with tracks added, and rewriting its `generator` would both misattribute
  it and break the untouched-bytes assertion. A test pins both halves of that distinction.

  **Nothing writes to disk.** There is no mutating path, no authorization token, no lockfile
  handling, no compare-and-swap, no pre-apply copy, and no `apply_candidate` tool or CLI command;
  all of that is designed in ADR-0025 and explicitly unshipped. Merge, lock override, IPC apply,
  placement apply and batch apply are stated non-goals rather than omissions.

## [0.4.0] - 2026-08-04

### Added

- Circuit Scene IR 0.1.0 and the `observe_board_scene` tool: a bounded, region-scoped semantic
  observation of one board, with a matching `copper-mcp observe-scene` CLI command. A caller states
  a window - either an exact nanometre bounding box or one `around_ref_id` with a radius, never a
  whole-board shorthand - and receives full-precision integer geometry for the objects that overlap
  it. Objects arrive in two collections rather than behind a flag: `static` (outline, pads, keepouts,
  rules) is what a proposal must take as given, and `mutable` (segments, arcs, vias, zones) is what
  it may change, so code meaning to read only the givens cannot iterate over both by accident. Every
  object is named by the Board IR identity it already carries, so a model can refer to what it saw
  in a later call instead of repeating coordinates back, and each reference declares its own
  durability as `native`, `content_derived` or `request_scoped` - with a scene-level summary - so a
  caller knows in one place whether the references it is about to store will outlive an edit.
  Object and vertex ceilings are charged as the scene is built and reported as an explicit
  `ceiling_hit`, so a truncated scene can never be mistaken for a complete one. The whole of this
  repository's own board is 123 objects and 41KB in 38ms, roughly 6% of the provisional 2,000-object
  ceiling; region scoping cut that response eleven-fold with no change in wall time, because parsing
  dominates, so the window is a context-budget economy rather than a server-cost one.
- Quarantine for board-author-controlled text. Silkscreen, fabrication text and footprint properties
  are off by default and, when explicitly requested, appear only in a separately typed `annotations`
  collection whose `trust` field is a one-value literal - there is no vocabulary for a trusted
  string, so no board can label its own text safe. Both the name and the value of each footprint
  property are quarantined, because the name is as attacker-controlled as the value. Net names never
  appear at any setting, since Board IR hashes them at conversion. The test for this is a
  whole-response grep against a hostile fixture carrying prompt-injection strings in every
  author-controlled slot, rather than a per-field assertion, because sanitisation defences fail by
  leaking into the one field nobody audited.
- A metamorphic relation over the scene: turn the board a quarter turn and every coordinate must be
  the image of the original under that turn while every `ref_id` holds still. A companion guard
  confirms the fixture actually contains geometry the turn changes, so the invariance of the
  references proves something.
- `observe_board_scene` advertises a real `outputSchema` and returns populated `structuredContent`,
  because its handler returns a closed contract rather than a bare dictionary. A test pins the
  contrast with the older `dict`-typed tools, which advertise a vacuous object schema - a gap in
  those tools' typing rather than in the SDK.
- An opt-in deterministic board render on `observe_board_scene` (`include_render`), with a matching
  `copper-mcp observe-scene --render` CLI flag that writes the SVG to a create-only workspace path.
  Two exports of an unchanged board are byte-identical after canonicalization: KiCad stamps the file
  with a wall-clock timestamp and the output filename in a single `<title>` line, and the named
  `title-line-v1` rule rewrites exactly that line and nothing else - measured as the entire delta
  between two real exports taken three seconds apart, one line out of 5,603. Canonicalization is
  idempotent and fails closed, so an export whose title line is missing or duplicated is refused
  rather than digested unnormalized. Evidence records `normalized_digest`, `source_revision`,
  `context_revision`, `kicad_version`, `layers`, `side`, `canonicalization` and `byte_count`,
  because a digest alone cannot tell a caller whether two renders are comparable.
- Copper-only rendering as a security control. The export draws `F.Cu`, `B.Cu` and `Edge.Cuts`
  only, and this is not a presentation choice: measured against KiCad 10.0.5, an export including
  silkscreen or fabrication layers embeds each board string **twice in literal, greppable form** -
  once in a `<desc>` beside the stroked paths and once in an invisible `<text opacity="0">`. Text is
  therefore not safely "drawn as paths", and filtering `<text>` after the fact would leave the
  `<desc>` copy behind; excluding the layers is the only control that works. A hostile fixture whose
  every author-controlled slot carries a marker is asserted absent from the render bytes, with a
  companion test proving those same markers *do* leak when the layers are included.
- Refusal on a truncated render. At the `max_render_bytes` ceiling (4 MiB default) KiCad does not
  die on `SIGXFSZ` - it exits 0 having written a partial file, and the title line is near the top of
  the document so it survives. The exit code, the title check and the digest would all have been
  satisfied by half an SVG, so the canonicalizer now requires a complete document.
- Delivery as an MCP `resource_link` annotated `audience: ["assistant"]`, from a bounded
  process-local store holding at most 8 renders and 32 MiB, deliberately separate from the schematic
  store so the two cannot evict each other. `include_render` is stdio-only because those bytes need
  the process-local store, even though the semantic scene remains available over both transports;
  only the flag is withdrawn off stdio, never the whole tool.
- A typed placement-intent contract and a deterministic legalizer (`copper_mcp.placement`), the
  first half of the M4 placement surface. The intent language has seven rule kinds - proximity,
  alignment, symmetry, board edge, region keep-in/keep-out, discrete orientation and side - and is
  deliberately **unable to express an illegal result**: every rule names objects by the references
  a scene already handed out, every parameter is an exact integer, and there is no way to state a
  coordinate or to permit an overlap. Proposals are ref-anchored for the same reason ("2.5mm right
  of that object's east edge", never a raw position), so the absolute coordinates in a candidate
  are always derived here and then snapped to an explicit `placement_grid_nm`. A test asserts that
  an absolute position cannot be smuggled in through any field.
- Footprint identity recovered out of band and joined to Board IR pads, which have no parent
  reference of their own. Adding footprints to Board IR would cost a schema version bump and change
  the digest of every board ever converted, so the grouping is read from the same source bytes and
  the candidate binds to **both** digests. The join is total rather than best-effort: pads with a
  native UUID join directly, pads without one are matched by reproducing the adapter's documented
  derived-id hash, and a view that cannot account for every pad refuses.
- Three-valued pad overlap. Bounds over-approximate a pad and cores under-approximate it, so
  disjoint bounds *prove* clearance and overlapping cores *prove* collision, while everything
  between is reported as `inconclusive` rather than guessed. Measured on this repository's own
  board, bounding boxes settle **1,359 of 1,360** different-net pad pairs and exactly one is
  inconclusive - 0.07%, an oval against a roundrect whose boxes clip at a corner both shapes round
  away. That measurement is why exact pad-shape geometry is not in v0.1, and a test pins the rate
  so the conclusion is revisited if it moves.
- Outline containment and keepout respect via exact integer ray casting, with `courtyard_overlap`
  reported as a one-value `not_modelled` literal: Board IR carries no courtyard geometry, and this
  repository's own board draws none at all while its project sets `missing_courtyard: ignore`, so
  KiCad's own courtyard check is equally blind on it. There is no vocabulary for a courtyard that
  was checked, so a candidate can never imply one.
- An honest failure taxonomy in which `infeasible_constraints` and `budget_exhausted` never
  collapse into each other - the first is a proof that no placement satisfies the rules, the second
  an admission that the work ran out - alongside `unresolved_ref`, `unsupported_geometry`,
  `illegal_placement` and `stale_revision`. Only syntactic contradictions are claimed as infeasible,
  because anything needing search would be reporting ignorance as certainty.
- Rule results carry exact residuals, and `satisfied_within_tolerance` is reported **only** when the
  caller supplied a `tolerance_nm`. An unstated tolerance means exact, so a one-nanometre residual
  is a violation and says so.
- Immutable `PlacementCandidate` with two-phase identity derivation over its own canonical content,
  placements recorded in reference order under `ordering_policy` `validate-snap-v1`, and evidence
  carrying per-rule residuals, the legality record and the checks consumed. An illegal placement is
  refused *with* its legality record, so a caller never has to guess which of three independent
  checks failed.

  Placement is **preview only**: nothing applies a candidate, and moving a footprint invalidates
  every route bound to the same base revision. There is no MCP or CLI surface yet - the contract
  and legalizer land first so the rule vocabulary is exercised before it is published. A side
  change is refused as `unsupported_geometry` rather than mirrored approximately.
- `preview_placement`, the public surface for the placement legalizer, as an MCP tool over both
  transports and a `copper-mcp preview-placement` CLI command. The response is a closed contract,
  so the tool advertises a real `outputSchema` and returns populated `structuredContent`. Requests
  are validated at the boundary before any file is read, refusals are typed and never echo the
  rejected value, and the board is loaded through the same workspace confinement as
  `preview_route`. Budgets - subjects, rules, checks and a deadline - come from configuration.
  Rules and proposals are structured enough that flags would be a poor interface, so the CLI takes
  them from an optional workspace-confined JSON document whose fields are restricted to `rules` and
  `proposals`: the board, constraints and subjects always come from the flags, so the document
  cannot redirect the request at a different board.
- A transport-parity test asserting the tool returns byte-identical structured content over stdio
  and streamable-HTTP, and a subprocess test asserting it is registered on a stateless HTTP server.
  Unlike a render or a schematic artifact, a placement preview holds no capability handle, so there
  is nothing a stateless deployment cannot resolve.

### Changed

- Scene objects now report `locked`, so copper the board's author pinned is distinguishable from
  copper a proposal may move. It is a field rather than a third partition: the static/mutable split
  is by *kind* and is exhaustive, while lockedness is a per-object property its author can toggle
  without changing what kind of thing the object is - a third collection would hide segments from
  any consumer that walked only the two documented ones. Kinds with no such concept, an outline
  contour or a net class, report `null` rather than `false`.
- Scene pad geometry now carries `roundrect_radius_nm`, without which a rounded-rectangle pad could
  not be reconstructed from the scene.
- Scene truncation reports `annotations_returned` and `annotations_omitted` alongside the object
  counts. `ceiling_hit` names the first ceiling reached; the two `*_omitted` counts are the
  authoritative signal, because objects and annotations are charged against separate budgets and
  both can truncate in a single response.
- The schematic capability store's TTL, LRU, locking and digest-recheck logic moved into a shared
  `BoundedArtifactStore` so the render store inherits the reviewed discipline rather than repeating
  it. The schematic store keeps its exact public contract, including the cross-check of the
  retained artifact object that catches post-insertion tampering, and its existing tests pass
  unchanged as the regression proof.
- `--black-and-white` is now forced on the render, for determinism rather than aesthetics: colour
  output follows the active KiCad theme, and black-and-white output is byte-identical across themes.
- KiCad renders against a **read-only** private snapshot, which is stricter than the zone-fill path.
  Given a writable directory KiCad drops a `.kicad_prl` beside the input; the read-only snapshot
  removes that side effect rather than relocating it, and a test asserts the workspace is unchanged
  down to the board's inode and mtime.
- The capability inventory now lists placement preview as implemented and names DRC binding for
  placement, apply, and post-placement observation as planned rather than done.

### Fixed

- **Pad orientation was double-counted, transposing the extents of every non-square pad on a
  rotated footprint.** A pad's angle in a KiCad file is already resolved into the board frame -
  KiCad rewrites every pad angle when a footprint is rotated - so adding the footprint's rotation
  on top counted the turn twice. Established against KiCad 10.0.5 rather than from documentation: a
  4mm x 1mm pad written `(at 3 0)` inside a footprint placed at 90 degrees is drawn by
  `kicad-cli pcb export svg` at the rotated position but with its extents still 4mm x 1mm. Position
  rotates; shape does not.

  **This changes geometry on boards with rotated non-square pads.** Pad cores feed routing
  obstacles and same-net attachment, and pad bounds feed scene region queries, so proposals on such
  boards may differ from previous releases. On this repository's own CopperTone board - where 30 of
  55 pads are non-square with a non-zero angle - different-net pad bounding-box overlaps drop from
  **6 to 1**, against `kicad-cli pcb drc` reporting zero violations; the single survivor is an
  oval-against-roundrect pair whose *boxes* clip at a corner both shapes round away. Route coverage
  is unchanged at 13 of 14 nets `already_connected` (`GND` still needs `include_fill_authority`),
  and the scene still returns 123 objects.

  The defect survived because the two test layers each missed the other's half: the adapter-level
  rotation fixture contained only square pads, so it could not observe a transposition, while the
  non-square metamorphic cases built `Pad` objects directly and never exercised the adapter. Both
  are now closed - the fixture carries a non-square rotated pad, and a new oracle test compares
  Board IR's pad extents against the geometry KiCad itself plots, which is the first test here that
  checks the adapter against something other than itself.
- Region bounds no longer under-report an arc. Bounding an arc by its start, middle and end points
  ignores the bulge between them, so a window touching only the sweep was told the board was empty
  there. The cardinal extrema the sweep actually crosses are now included, using exact integer
  circumcentre and orientation arithmetic with no floating point; worst-case slack over 400
  randomised arcs is 4nm, always outward.
- Region bounds no longer under-report an obliquely rotated pad. Board IR accepts any pad angle -
  only *footprint* transforms are restricted to quarter turns - so swapping width and height on
  quadrant parity alone under-bounded every oblique pad. Quarter turns keep their exact extents;
  any other angle falls back to the pad rectangle's circumscribed circle, which contains it at
  every rotation and needs no trigonometry. Bounds may now only ever be too large: returning an
  object that turns out to be just outside a window is harmless, while omitting one that overlaps
  is not recoverable by the caller.
- `include_annotations` no longer bypasses the response budget. Board text was collected without a
  ceiling, so a board with enough footprint properties could grow the annotation list past the
  length the response contract itself advertises. Annotations are now charged against
  `max_scene_annotations` (default 5,000, configurable) and truncation is reported explicitly as
  `annotations_returned` / `annotations_omitted`.
- An `around_ref_id` radius can no longer push the resolved window outside the coordinate range the
  contract advertises. The anchor and the radius are each in range but their sum need not be, so
  the window is clamped - losslessly, because every board coordinate is inside that range, so a
  window already covering it cannot select more by growing.

## [0.3.0] - 2026-08-04

### Added

- A metamorphic test family over the routing pipeline: whole-board rotation by 90, 180 and 270
  degrees, reflection across each axis, lattice-safe translation, and endpoint swap. Each relation
  transforms a board and asserts that the router's conclusion travels with it - same result arm,
  same diagnostic code, same connection counts, and identical length, bend, proximity and total
  cost. Cost is a genuine invariant because the transformed board's legal path set is exactly the
  image of the original's; exact vertex equality under the inverse transform is asserted only on
  boards whose optimum is unique, because the expansion order and the `(iy, ix)` heap tie-break are
  not rotation-equivariant and a different-but-equally-optimal route is a correct answer rather than
  a defect. The rotation relations cover a board of rotated, non-square pads - the class that hid
  the footprint-rotation defect - and a second relation works at the adapter level instead,
  comparing `parse(rotate(board))` with `rotate(parse(board))` on the committed rotated-footprint
  fixture. That is the relation the y-down defect would have failed, and it is checked to be
  discriminating: all twelve pads match the correct quarter turn and none matches the mirrored one.
  These relations answer the complement of a pseudo-oracle - not "is this answer right" but "is this
  the same board" - and they need no KiCad.

- Cached zone fill may now serve as connectivity evidence, but only against a fresh KiCad refill.
  KiCad refills a private disposable copy and the recomputed pour is compared with the board's
  cache; matching means the two are the same geometry, so there is no question which one a claim
  describes, and a mismatch is a typed `stale_fill` refusal rather than a silent preference for
  either version. Comparison is over canonical geometry - islands sorted and digested by layer, net
  and exact integer vertices - because KiCad rewrites and reorders a board wholesale on save, so a
  byte diff of the file says nothing about whether the fill changed. An **island** is the unit
  rather than a zone: verified empirically against a board authored to force two disjoint regions,
  KiCad 10.0.5 emits one `filled_polygon` node per connected region, so copper touching different
  islands is not connected and a committed fixture pins that. `ZoneFillAuthority` refuses
  construction when its digests differ, so a stale record cannot exist to be misread. The workspace
  board is never refilled: `--refill-zones --save-board` reaches only the disposable copy, the three
  existing negative assertions still hold, and the source is recaptured and compared afterwards.
  The whole path is opt-in through `include_fill_authority`, because it spawns KiCad and must never
  happen implicitly. Fill stays out of Board IR, so snapshots and their digests are unchanged and
  the router never fetches evidence itself. Scope is connectivity only; using exact fill as a
  tighter routing obstacle would change routed geometry on every zoned board and needs its own
  measurement. On the repository's own CopperTone board this resolves `GND`, taking recognition to
  **14 of 14** - joined by two fill islands and six vias - while without the flag it still refuses,
  which is the honest default. See [ADR-0021](docs/adr/0021-zone-fill-authority.md).

- A same-net through via is now a connectivity joint rather than a blanket veto, so a net already
  joined across copper layers reports `already_connected` instead of being refused. Routing is
  unchanged and stays single-layer: a net that still needs new copper while carrying a via keeps its
  existing refusal. The via's core is its **annulus**, never the drill hole - a square inscribed in
  the outer circle would claim the one region that certainly is not copper - so the ring is covered
  by four axis-aligned rectangles, one per side, with the hole radius taken as the ceiling of half
  the drill and each rectangle's far corner satisfying `a^2 + b^2 <= R^2` in exact integers. Those
  four are unioned atomically because the annulus is one piece of physical copper joined by a plated
  barrel; deriving its self-connectivity from rectangle overlap would report a via as four separate
  objects. Objects now connect only when they share a layer, and a through via shares every layer,
  which is exactly what makes it a joint. Board IR admits through vias only and validates that they
  span the complete stack, so blind, buried and microvias stay fail-closed at the adapter.
  `RouteConnection` gains a `vias` count and its invariant becomes
  `attachment_segments + pad_count + vias`. Same-net zones still veto the claim, because a stale or
  unfilled zone cannot prove connectivity. On the repository's own CopperTone board this takes
  recognition from 11 of 14 nets to **13 of 14**: `VCC` and `L_OUT` are joined through their vias,
  while `GND` stays refused honestly because it carries a same-net zone. A committed `via-joint`
  fixture runs front stub to via to back stub to via to front stub, corroborated by board-level
  KiCad DRC reporting zero unconnected items, and the check is discriminating because removing
  either the via or the back-layer stub makes KiCad report an unconnected item. See
  [ADR-0020](docs/adr/0020-via-aware-connectivity.md).

- Multi-pin nets are routed, not just recognised. A net with more than two pads is spanned by a
  deterministic minimum spanning tree over its connected components - edges weighted by the exact
  integer rectilinear gap between component bounding boxes, ordered by `(gap, lower index, higher
  index)` - and each MST edge is routed as one leg by the existing multi-source/multi-target search.
  A routed leg's copper joins the merged component, so later legs may attach anywhere along it, and
  same-net legs are never obstacles to one another. The ordering policy is recorded in the candidate
  as `component-mst-v1`, which makes a better topology additive behind the same contract.
  **Claimed**: every pad ends in one component, each leg is optimal for the obstacles present when
  it was routed, and the result is exactly reproducible. **Not claimed**: Steiner optimality, or
  optimality of the tree as a whole - an earlier leg is never revisited once a later one is routed.
  Any leg failing refuses the whole call; a partial tree is not a candidate. A committed four-pad
  `tree-star` fixture becomes a three-leg tree that real KiCad 10.0.5 accepts with zero errors,
  warnings and unconnected items, and that check is discriminating because removing any one leg
  makes KiCad report an unconnected item. **This slice is validated by fixtures, not by
  CopperTone**: every net on that board is already routed by its designer, so multi-pin routing
  changes nothing there, and what that board still needs is via-aware connectivity for its three
  remaining nets. See [ADR-0019](docs/adr/0019-multi-pin-component-merging.md).

### Changed

- The mypy floor is raised to `>=2.3,<3`, matching the version CI runs and the one pinned in the
  development environment. Newer mypy narrows exhaustive enum branches differently, so a single
  supported generation removes a class of version-skew failure that had to be checked by hand.

- `RoutePatch` now carries a tuple of `RoutePath`s instead of a single vertex list, so one candidate
  can describe a tree. `ROUTER_VERSION` advances to `astar-grid/0.4.0` and the preview response
  carries `patch.paths[].vertices_nm` in place of `patch.vertices_nm`, plus `pad_count` and
  `ordering_policy`. This is a breaking response change, taken deliberately while the project is
  pre-1.0 rather than maintaining two candidate shapes forever. Two-pin proposals carry exactly one
  path and the ordering policy `single-path`, and their geometry, cost and metrics are unchanged.
- Multi-pin legs seed from pad cores rather than pad centres. Requiring every pad centre to sit on
  one lattice is unworkable: on the repository's own CopperTone board the largest grid step putting
  all pads of a multi-pin net on one lattice is 5 um for six of the nine such nets, which is a
  62-million-node lattice against a 500,000 ceiling. Seeding from cores removes the constraint for
  every pad but the anchor. Two-pin nets keep centre seeding, so their candidate identities are
  unchanged apart from the version bump. Budgets are shared across the whole tree rather than per
  leg, because one candidate should honour one ceiling; merge order and budget consumption are both
  deterministic, so exhaustion fails at a reproducible leg with reproducible counts.

### Fixed

- A round pad's connectivity core was a zero-width bar through its centre. That is a legal subset of
  the copper and was harmless while pads were only contact-tested, but it covers a lattice node only
  when the pad centre happens to land on one, so a round pad could offer no attachment point at all.
  Round pads now also contribute their largest inscribed axis-aligned square, of half side
  `isqrt(r^2 // 2)`, alongside the original bar and its perpendicular twin - a strict enlargement,
  since replacing the bar would have discarded the pad's extremes. Every rectangle contains the pad
  centre, so a pad is never split into two components by its own decomposition. Measured across all
  committed fixtures and every CopperTone net this changes no outcome and no candidate identity
  today; it is what makes multi-pin pad-core seeding possible at all, taking CopperTone's multi-pin
  pads from three nets with an unreachable pad at 250 um to none.

## [0.2.0] - 2026-08-03

### Added

- Connectivity analysis now spans nets of any width, not only two-pin nets. When every pad of a net
  lands in one component the router reports the terminal `already_connected` outcome whatever the
  pad count, reusing the existing pad cores, orthogonal rectangles and diagonal chains unchanged.
  `RouteConnection` gains a `pad_count` field and its component invariant generalises to
  `attachment_segments + pad_count`; `start_pad_id` and `end_pad_id` keep their names and their
  two-pin meaning, and are documented as the lexicographically first and last pads, which bound the
  set rather than naming a route a connected net does not have. **Routing** a multi-pin net stays
  unsupported: a wider net that is not fully connected gets the unchanged `invalid_two_pin_net`
  refusal, and no new failure code is introduced. A net carrying a same-net via or zone is never
  claimed connected, because that copper is on another layer or otherwise unrepresented here; a
  two-pin net still names the via or zone directly, while a wider one is refused for its pad count.
  Two-pin behaviour is bit-identical and `ROUTER_VERSION` does not move. On the repository's own
  CopperTone board this takes recognition from five of fourteen nets to **eleven of fourteen**, the
  widest being `VREF` at seven pads joined by ten segments; the three that remain refused — `GND`,
  `VCC`, `L_OUT` — are exactly the nets carrying same-net vias. `kicad-cli pcb drc` corroborates by
  reporting zero unconnected items for the whole board. Still zero nets routed: no copper is
  proposed for that board, and none of its nets needs any.
- Diagonal copper on the *routed* net is now attachment copper rather than a refusal, completing
  the model: obstacles are over-approximated, attachment copper under-approximated. A diagonal track
  has no single axis-aligned inner rectangle, so it contributes a chain of axis-aligned squares
  centred at `start + (delta * i) // steps`. Flooring moves a centre less than a nanometre per axis
  off the exact centreline point, so it stays within `sqrt(2)` of the track; a two-nanometre
  tolerance absorbs that, and the square half side satisfies `2 * s^2 <= (radius - 2)^2`, which by
  the triangle inequality on distance-to-a-set puts every square provably inside the real copper.
  `steps` is chosen so consecutive centres differ by at most `2 * s` per axis — exactly when two
  closed squares still touch — so the chain is one connected component by construction rather than
  by inspection, and both properties are covered by a property test over many orientations and
  widths in exact integer arithmetic. The first and last squares are centred exactly on the track's
  endpoints, so a diagonal stub reaches its pad and can be picked up at its far end. Endpoints are
  canonically ordered, so a track recorded in either direction yields the identical chain; each
  square charges the shared obstacle-check budget, so an over-long track fails closed; and a track
  too thin to model at all is refused with a distinct diagnostic. Same-net vias and zones remain
  fail-closed, and foreign diagonal envelopes are unchanged. Boards carrying same-net diagonals were
  previously refused outright, so no board the router already accepted changes geometry or identity
  and `ROUTER_VERSION` does not move. The `diagonal-stub` fixture now completes a route off a
  diagonal stub, adding 18 mm where an empty board needs 20 mm, verified against real KiCad 10.0.5;
  that check is discriminating because displacing the same proposal by 0.5 mm so it misses the stub
  end makes KiCad report two `track_dangling` warnings and one unconnected item. On the repository's
  own CopperTone board this resolves the entire two-pin surface: all five two-pin `F.Cu` nets now
  report `already_connected`, five of fourteen overall, with `kicad-cli pcb drc` corroborating by
  reporting zero unconnected items. No copper is proposed for that board — the nets it can reason
  about need none — and multi-pin routing remains the contract its other nine nets require.
- Diagonal selected-layer copper on a foreign net is now a conservative obstacle instead of a
  board-level refusal. The envelope is the Minkowski sum of the track's centreline with an
  axis-aligned square of its half width — the convex hull of the two squares at its endpoints —
  which provably contains the real track because the swept disc is inscribed in the swept square,
  and whose every vertex is an exact integer with no rounding rule to argue about. It is inflated
  by the routed half width plus the stricter of the routed and obstacle net-class clearances,
  exactly as the orthogonal path is, and charges one obstacle check per vertex against the same
  budget and `max_obstacles` ceiling as zones and keepouts. The cost is over-approximating the
  perpendicular extent by at most about 41%, worst at 45°, which can only refuse a route and never
  permit a violation. Orthogonal foreign segments keep their exact swept-rectangle fast path, so no
  board the router already accepted changes geometry or identity and `ROUTER_VERSION` does not move.
  Diagonal copper on the *routed* net still fails closed with a distinct diagnostic: an obstacle may
  be over-approximated, but attachment copper must be under-approximated or the router would claim
  a connection the board does not have, and a diagonal has no exact integer inner core yet. A
  committed `diagonal-blocker` fixture is verified against real KiCad 10.0.5 DRC and checked to be
  discriminating — the straight route it replaces is reported as `tracks_crossing` — and the
  envelope's superset property is covered by a test that samples the exact integer stadium across
  seven orientations.
- Selected-layer track keepouts are no longer required to be axis-aligned rectangles. A rule area
  with any simple polygon outline — including the octagonal mounting-hole areas KiCad emits, and
  concave outlines — becomes a conservative polygon envelope obstacle reusing the exact integer
  containment, inclusive intersection, and rational squared-distance geometry already built for
  foreign-net zone envelopes, with the same per-vertex work accounting and `max_obstacles` ceiling.
  A keepout carries no net and no clearance of its own, so the routed net's class clearance is the
  only rule that applies; that margin is deliberately stricter than KiCad, which prohibits only
  tracks that intersect the area. Rectangular keepouts keep their existing exact square-cornered
  inflation rather than being folded into the polygon path, because a Euclidean offset would round
  their corners into a strictly looser obstacle — so candidate geometry and identity on every board
  the router already accepted are unchanged and `ROUTER_VERSION` does not move. A committed
  `octagon-keepout` fixture is verified against real KiCad 10.0.5 DRC, and checked to be
  discriminating: a straight track through the same rule area is reported as `items_not_allowed`.
  Board IR already refuses curved and multi-loop rule-area outlines, so no keepout reaching the
  router is now unmodeled.
- Orthogonal same-net copper on the selected layer is now attachment copper instead of a
  partial-routing veto, so a half-routed net completes from its stub rather than being refused.
  Connectivity uses a second rectangle model that deliberately errs opposite to the obstacle model:
  obstacle rectangles over-approximate copper so clearance is never understated, while connectivity
  cores under-approximate it — dropping a track's round end caps, flooring half widths, insetting a
  `roundrect` by its corner radius, and reducing a `circle` to a centre line — so an electrical
  connection can never be claimed that the board does not have. Exact integer union-find over those
  cores decides components under the existing obstacle-work budget and cancellation cadence. A net
  whose pads already share one component returns the new terminal `already_connected` preview
  status carrying a typed `RouteConnection`, not a failure code; `include_drc` is skipped there
  because no copper is proposed. Otherwise a multi-source, multi-target search seeded from the
  covered lattice nodes proposes only the missing piece, using a target-bounding-box heuristic that
  stays admissible and consistent and reduces exactly to the previous estimate for a single target.
  Diagonal same-net segments, same-net vias and zones, and endpoint pads whose shape is not modeled
  exactly still fail closed, and attachment copper counts against `max_obstacles`. Boards with no
  same-net copper produce byte-identical geometry, costs, and metrics; only `ROUTER_VERSION`
  advances, to `astar-grid/0.3.0`. A committed `partial-route` fixture proposes 10 mm where the
  equivalent empty board needs 20 mm, verified against real KiCad 10.0.5 DRC for zero errors,
  warnings, and unconnected items. Coverage on the repository's own CopperTone board did not move at
  the time: removing the veto revealed that three of the five two-pin nets carry diagonal same-net
  copper, while the other two became genuinely attachable and failed on the next unmodeled object
  instead. (Both those measurements, and the ones in the entries below, were taken before the
  footprint-rotation fix recorded under Fixed; with pads placed correctly the two attachable nets
  report `already_connected`.) Attaching mid-stub rather than at a stub endpoint leaves
  copper with an unconnected end, which KiCad reports as a `track_dangling` warning. (Corrected
  while adding polygon keepouts above: this entry originally named octagonal keepouts, then the
  `GND` zone envelope, then an off-grid pad delta as the remaining chain for those two nets. Direct
  measurement shows foreign-net diagonal segments come first, and the last two are in the opposite
  order. The architecture doc carries the evidenced chain.)
- Canonical Circuit Intent IR `0.1.0` as a strict, immutable, content-addressed logical topology
  contract for two-pin resistors and non-polarized capacitors. A pure bounded adapter renders
  verified snapshots into byte-deterministic in-memory KiCad `20250114` schematics with original
  embedded symbols, empty footprints, tighten-only parser budgets, content-verified source/count
  provenance, source/artifact digests, exact 1.27 mm grid placement, global labels at every
  connection of port-backed nets, and no file, library, or network access. A shared service accepts
  strict snapshot JSON or structured content, normalizes it, and requires byte-identical double
  rendering. The CLI explicitly creates one new workspace `.kicad_sch` without overwrite; the
  stdio-only MCP tool returns redacted metadata plus a non-enumerable opaque resource whose access
  expires after 15 minutes in a 16-entry, 16 MiB process-local store. Expired objects are reclaimed
  lazily on later store activity or process exit; this is not a secure memory-erasure claim. An
  independently authored RC low-pass fixture passes
  schema/canonical checks and a real KiCad 10.0.5 SVG plus `kicadxml` connectivity round trip; the
  reviewed run preserved exact nets and reduced ERC warnings from seven to four, with two isolated
  external-port labels and two missing private-library-configuration warnings remaining. This is
  not an ERC-clean, electrical, board-parity, manufacturability, or fabrication-readiness claim.
- ADR-0015 defines a future Circuit Scene IR for bounded semantic and visual observation, typed
  placement intent, immutable previews/candidates, deterministic validation, and separately
  authorized apply. It does not add placement or permit direct AI mutation of KiCad.
- A licence-aware, network-free audio capability catalog and runner. Elliott Sound Products and
  diyAudioProjects.com are recorded only as non-redistributable reference sources; no
  project/article content, schematics, or downloads are copied or fetched. An independently
  authored low-voltage RC connectivity fixture and the existing open-hardware CopperTone board are
  bound with their exact licence bytes into one bounded validation snapshot, then exercised twice
  through the MCP-shared Board IR and route-preview services. The result demonstrates one routed
  two-pad audio net and typed multi-pad
  refusals, with claims derived from observed outcomes and kept disjoint from explicit non-claims;
  a local KiCad 10.0.5 parse/plot smoke test is kept distinct from DRC. This board-routing corpus
  does not itself claim circuit derivation, schematic-to-board parity, ERC, electrical validation,
  autorouted boards, or fabrication readiness.
- Foreign-net solid zones on the selected layer are now conservative polygon boundary-envelope
  obstacles instead of a blanket rejection. Concave and diagonal outlines use exact integer
  containment, intersection, and rational squared-distance checks under the strictest routed class,
  zone-net class, and zone clearance; bounds construction and every polygon relation consume the
  existing obstacle-work budget. Same-net zones remain unsupported, cached KiCad fill is not
  trusted, and CopperTone previewed zero of fourteen `F.Cu` nets at the time because nine are
  multi-pin and all five two-pin nets already carry same-net copper. (Corrected twice since: that
  last clause was true but misleading, because the partial-routing veto fired early enough to mask
  several further blockers; and the board's per-net measurements were themselves distorted by the
  footprint-rotation defect recorded under Fixed. The routing baseline carries the current
  numbers.) A committed `blocked-zone` fixture verifies deterministic read-only
  adapter-to-preview routing without claiming fill-aware KiCad DRC.
- Through vias outside the routed net are now selected-layer obstacles built from their outer
  diameter, rather than a board-level rejection. A via on the routed net still fails closed as
  partial routing. On the repository's own CopperTone board this moved the failure from "nine vias
  reject everything" to per-net diagnostics. Later re-measurement, after the footprint-rotation fix
  recorded under Fixed, shows two of fourteen nets reaching a terminal `already_connected` outcome
  and none routed; the remaining two-pin nets are blocked by diagonal copper on the routed net.
- Selected-layer pads and orthogonal segments outside the routed net are now exact rectangular
  routing obstacles instead of a hard rejection, so preview works on boards that already carry
  copper. Obstacles are inflated by the routed half-width plus the stricter of the routed and
  obstacle net-class clearances, round pad shapes over-approximate via their bounding box, and
  arcs, off-axis rotations, diagonal segments, and partially routed nets still fail closed. A
  committed blocked-pad fixture verifies the detour against real KiCad 10.0.5 DRC.
- Read-only Board IR inspection as the `inspect_board_ir` MCP tool and the `copper-mcp board-ir`
  command. It reports whether a board converts to the supported Board IR subset and describes its
  revision, snapshot and constraint digests, schema, units, copper layer identities, and object
  counts, or bounded conversion diagnostic-code counts. Coordinates, net names, pad and net
  identities, UUIDs, and source bytes are never returned.
- A bounded, non-mutating route preview exposed as the `preview_route` MCP tool and the
  `copper-mcp preview-route` command. It strictly validates an untrusted request, takes routing
  constraints only from typed caller values, reads one workspace board read-only, and reports
  `routed`, `not_routed`, or `unsupported_board` with the candidate geometry, exact cost
  decomposition, and deterministic search metrics, one typed non-echoing diagnostic, or bounded
  conversion diagnostic-code counts. A configurable wall-clock deadline starts at the operation
  boundary and bounds the whole call — conversion, search, and the clamped KiCad timeout for
  optional DRC — above the existing integer ceilings, and `include_drc` binds the proposal to
  aggregate authoritative KiCad DRC evidence or fails the call. Rejected requests report an
  unsupported-field count rather than echoing caller-supplied names. Durable jobs, persistence,
  export, and apply stay deferred.
- A bounded, integer-only, single-layer A* reference that produces content-addressed immutable
  two-pin candidates for a narrow rectangular Board IR subset, with exact boundary semantics,
  deterministic tie-breaking, preparation/search cancellation, independent grid/expansion/obstacle
  work ceilings, typed diagnostics with deterministic counters, and fail-closed geometry and API
  handling. Durable KiCad export, MCP exposure, preview, and apply are deferred.
- A bounded benchmark-only Dijkstra oracle plus a reproducible synthetic harness that verifies A*
  completion and exact optimal-cost agreement while retaining the expected no-path fixture and raw
  deterministic, runtime, and incremental-memory evidence. This is not a KiCad DRC or throughput
  claim.
- A pure, bounded KiCad route-patch bridge that accepts only an exact replayed A* candidate, appends
  deterministic native segments to new disposable board bytes, records CopperMCP writer provenance,
  precomputes native identities for collision checks, enforces total output-object limits, and
  requires native source-geometry identities plus a full Board IR round-trip match. An optional
  KiCad 10 integration test validates the synthetic fixture without mutating source or candidate
  files; durable export, preview, MCP, and apply remain deferred.
- Internal candidate-bound KiCad DRC orchestration that captures one bounded source/rule/library
  context, parses and exact-replay serializes only its captured board bytes, replaces the board only
  in memory, rechecks all context budgets, and returns frozen evidence binding candidate, Board IR,
  source, patched-board, patched-context, and strict aggregate DRC revisions. The derivative exists
  only in a private temporary directory; public ingestion, persistence, preview, and apply remain
  deferred.
- Canonical Board IR `0.1.0` with integer nanometre/microdegree geometry, typed routing constraints,
  strict canonical JSON, semantic and snapshot digests, bounded decoding, and a versioned JSON Schema.
- A bounded, read-only, fail-closed KiCad converter for the documented rectangular-outline subset,
  plus golden valid/invalid JSON and synthetic source fixtures and explicit architecture/ADR
  documentation. This converter does not route, mutate, preview, or apply board changes.
- Explicit solid-zone priority, pad-connection, and island-removal intent in Board IR, plus a
  version-pinned KiCad semantic preflight for copper/`Edge.Cuts` graphics and supported object fields.

### Changed

- Project metadata and `server_info` now identify the source as the `0.2.0` MVP-alpha; the latest
  public GitHub release remains `0.1.0` until the separate tag-and-release gate succeeds.
- Release-tag validation now requires both a dated changelog section for the version and an
  append-only `Ready` release-ledger authorization naming the exact fully checked source commit.
  The later tag commit may differ only in `CHANGELOG.md` and the release ledger. Authorization
  permits tagging but does not claim publication.
- Untrusted JSON request validation now lives in one shared `request_boundary` module, so field,
  type, range, boolean, and character rules cannot drift between public services.
- Ledger validation now rejects oversized, non-strict, non-finite, or content-address mismatched
  benchmark JSON artifacts.
- CodeQL `init`, `analyze`, and SARIF upload now move as one pinned v4 suite, and Dependabot groups
  future CodeQL suite updates so incompatible action generations cannot be proposed separately.
- Workspace path validation compares a caller-supplied absolute path against the resolved workspace
  root without resolving the caller's own path first. An absolute path spelled through a symlinked
  prefix, such as `/tmp/...` where the resolved root is `/private/tmp/...` on macOS, is therefore
  rejected fail-closed and must be spelled through the resolved path. Workspace-relative paths are
  unaffected.

### Fixed

- The KiCad adapter placed pads on rotated footprints at their mirror image. KiCad stores board
  coordinates with y increasing downward while its `(at x y angle)` angle is counter-clockwise on
  screen, so a quarter turn maps a footprint-local point `(x, y)` to `(y, -x)`; the adapter used the
  `(-y, x)` that a y-up reading gives. The 0° and 180° cases are identical either way, so the defect
  was invisible except at 90° and 270°, where it silently swapped the two pads of every rotated
  two-pad footprint. The convention is now pinned by a committed `footprint-rotation.kicad_pcb`
  fixture whose expected positions come from KiCad itself: each rotated footprint has a track drawn
  to where the corrected placement predicts its first pad, and a real KiCad 10.0.5 test asserts zero
  violations and zero unconnected items — which a mirrored turn could not produce, because the track
  would land on the neighbouring pad's net. `_transform` is used only for pad centres; footprint-local
  zones and graphics are separately refused and all other geometry is stored absolutely, so no other
  object class was affected, and pad `rotation_udeg` composition was already correct because it is a
  rotation sum rather than a coordinate map. **Board IR snapshot digests change for any board with a
  rotated footprint**, and with them route-candidate `base_revision` and candidate IDs; the committed
  golden `schema-valid.json` is regenerated, and its diff is exactly the two pad coordinates plus the
  digest. The Board IR `0.1.0` schema is unchanged — no field changed shape — so no version bump is
  warranted; this restores conformance rather than altering the format. Historical benchmark records
  under `benchmarks/results/board-ir/` retain digests computed before the fix and are no longer
  reproducible against current code, which is correct for dated evidence. On the repository's own
  CopperTone board the correction produces its first coverage: `L_ISO` and `R_ISO` are each two pads
  joined by one segment running between their centres and now report `already_connected`, which
  `kicad-cli pcb drc` corroborates by reporting zero unconnected items for the whole board.
- The deterministic passive-schematic layout now uses longer symbol leads, wider A4-aware
  component spacing, and grid-aligned reference/value offsets. The RC fixture's pin labels,
  component bodies, and visible properties no longer collide in the reviewed KiCad SVG. This is a
  readability baseline, not general or AI-driven placement.
- The tag-only publish job now passes its repository explicitly when creating a GitHub release, so
  it does not depend on a checkout in the isolated publish job.
- Board IR construction now normalizes direct content before hashing, aligns runtime/schema limits,
  restricts v0.1 to one hole-free outline and full-stack through vias, and keeps public writer output
  readable by default decoder budgets.

### Security

- KiCad subprocesses now receive a minimal allowlisted environment and private per-run HOME, KiCad,
  XDG, runtime, and temporary roots instead of inherited credentials or user-global KiCad settings.
  They run from a private working directory, accept only snapshot-confined file-table dependencies,
  and reject environment-expanded, absolute, remote, and plugin-backed URIs. The private state tree
  rejects symlinks and special files and is covered by the same per-file, file-count,
  cumulative-byte, and scan-time ceilings as captured design context.
- Schematic delivery separates redacted build metadata from exact bytes using an independent
  256-bit capability, a stdio-only bounded process-local store, uniform unavailable responses, and
  digest verification on every read. Workspace inputs are captured through descriptor-anchored,
  no-follow reads. CLI export requires the exact lowercase `.kicad_sch` suffix, is explicit,
  workspace-confined and create-exclusive, and cannot overwrite an existing path.
- MCP schematic wrapper, nested content, and structured output schemas are closed. Scalar, list, and
  extra-field failures are rejected without echoing attacker-controlled names or values.
- KiCad DRC reports are captured as no-follow, nonblocking regular files and decoded with duplicate,
  non-finite, depth, and value-count rejection. Evidence is accepted only after read-only validation
  of the complete private snapshot tree, including unrecognized side effects.
- Schematic artifact-store entries detach the exact content, digest, and size at insertion, so later
  alias mutation cannot change identity or evade aggregate byte accounting.
- KiCad and Board IR parsing now use quote-aware streaming S-expression tokens, a pre-DOM JSON
  lexical/structural budget pass, exact context-independent decimal conversion, bounded non-echoing
  diagnostics, and explicit rejection of unmodeled routing or non-default fabrication semantics.
- Candidate and ordinary DRC now share one fixed KiCad subprocess/report path; candidate evidence
  rejects stale source/rule/library context and any private input-context mutation, accepts
  documented finding exit code `5` as valid evidence only when it agrees with the strict report,
  requires violation-type totals to equal aggregate counts, and freezes copied counts against
  post-validation mutation.

## [0.1.0] - 2026-08-03

### Added

- Initial Apache-2.0 project foundation and governance.
- Secure, bounded inspection for `.kicad_pcb` files.
- Fixed-argument, read-only KiCad JSON DRC with bounded severity, connectivity, ignored-check, and
  violation-type summaries plus stale-context checks.
- Versioned board-manifest, DRC-summary, and candidate JSON schemas.
- MCP tools for server information, board inspection, KiCad DRC, candidate validation, and comparison.
- Correctness-first candidate ranking and routing backend contracts.
- GitHub issue forms, CI, CodeQL, dependency auditing, release automation, and project ledgers.
- A non-publishing release dry run that verifies the requested version, complete quality gate, and
  distribution artifacts before a version tag is created.
- A source-linked survey of open PCB autorouters and a modern CPU, multicore, exact-repair, GPU, and
  typed-ML research roadmap.
- Audio Board Lab #001, CopperTone: a separately licensed, board-first KiCad 10 stereo line-buffer
  preview with BOM, manufacturing exports, STEP model, renders, constraints, provenance, and a
  one-command read-only DRC and artifact-hash validation workflow plus explicit snapshot refresh.
- Public social-preview artwork and a factual KiCad development screenshot with provenance records.

### Fixed

- KiCad 10 named-net inspection now counts deduplicated item-level `(net "NAME")` declarations when
  legacy numeric top-level net declarations are absent.
- CopperTone uses stable semantic UUIDv5 identities and temporary default validation so unchanged
  board replays no longer replace native object identities or modify tracked files.

### Security

- GitHub Actions are pinned to reviewed immutable commits; the release workflow uses the current
  official attestation action and explicitly scoped artifact-metadata permission.
- Workspace confinement protects against parent-path and symlink escapes.
- Secret-bearing files, private boards, job stores, and generated artifacts are ignored by default.
- MCP network transport binds to loopback unless explicitly reconfigured.
- KiCad execution uses a validated executable, fixed arguments, discarded logs, a POSIX child-process
  file ceiling, cumulative byte/file-count and discovery-time bounds, non-overlapping snapshot
  lifetimes, timeouts, strict contract parsing, and before/after DRC-context revision checks.
- The development dependency floor excludes pytest versions affected by `PYSEC-2026-1845`.

[Unreleased]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.6.0...HEAD
[0.6.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.3.0...v0.4.0
[0.3.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.2.0...v0.3.0
[0.2.0]: https://github.com/seunghyukchoe/copper-mcp/compare/v0.1.0...v0.2.0
[0.1.0]: https://github.com/seunghyukchoe/copper-mcp/releases/tag/v0.1.0
