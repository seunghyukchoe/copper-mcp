# Migrating a deployment from CopperMCP 0.10.0 to 0.11.0

This note is the deployer's delta, audited item by item against the `0.11.0` CHANGELOG section.
Every entry there is classified below as either a caller-visible change with an action, or an
explicit non-claim with the reason it needs none.

## 0. No schema version moves, and no snapshot needs re-conversion

**`BOARD_IR_SCHEMA_VERSION` does not move in this release. It is `0.4.0` at `v0.10.0` and `0.4.0`
at `0.11.0`.** This was checked against the constant itself
(`src/copper_mcp/board_ir/types.py`, line 10) at both points, not inferred from the absence of a
CHANGELOG entry. `schemas/` has no diff at all across `v0.10.0..0.11.0`, so no published schema
file's accepted set moves either.

The consequence:

- **do not** re-convert persisted Board IR;
- **do not** invalidate snapshot digests, candidate caches or scene caches on account of the
  version;
- `inspect_board_ir` continues to report `ir_schema_version: "0.4.0"`;
- `schemas/board-ir/0.4.0.schema.json` is the active schema and is byte-unchanged. `0.1.0`,
  `0.2.0` and `0.3.0` remain frozen legacy files.

This holds even though §2 and §3 widen what the adapter *accepts*, and the reason is
[ADR-0105](../adr/0105-a-schema-version-moves-with-its-accepted-set.md): the accepted set that
governs a schema version is the **emitted document's**, and nothing this release accepts enters
`BoardIRSnapshot`. Every newly accepted field is validated and discarded, with the erasure
disclosed through a count on the MCP summary — a surface no file under `schemas/` describes.
`scripts/check_schema_sets.py` passes with no exemption added; that is the mechanical
confirmation rather than this paragraph.

0.9.0 required a two-hop `0.2.0` → `0.4.0` re-conversion
([the 0.9.0 note](copper-mcp-0.9.0.md), [board-ir-0.4](board-ir-0.4.md)). If that migration is
still outstanding it remains outstanding — installing 0.11.0 neither performs nor excuses it, and
neither did 0.10.0.

`SCENE_VERSION`, `NEGOTIATED_ROUTER_VERSION`, `ROUTER_VERSION`, the candidate versions, the four
preview response versions 0.10.0 moved (`preview_route` and `preview_layered_route` at `1.1`,
`preview_placement` at `0.2.0`, placement **candidates** at `0.1.0`) and every published schema
file are likewise unmoved. **What moves in 0.11.0 is a dependency range, the set of boards that
convert, and the size of one disclosure map — not the IR and not any response version.**

## 1. The `mcp` dependency cap rises from `<2.1.0` to `<2.2.0`

**`mcp` moves from `>=2.0.0,<2.1.0` to `>=2.0.0,<2.2.0`.** This is a resolver-visible constraint
and the first thing to check on upgrade, because it reverses 0.10.0 §6 — which is the only
migration item in this release that *undoes* rather than extends a previous one.
[The dedicated note](mcp-2-1-refusal-contract.md) is the short form; this section is what a
deployer must act on.

### What 0.10.0 told you, and what changed underneath it

0.10.0 §6 held the range below 2.1 and said, in as many words, *do not override the cap to take
2.1: on that line CopperMCP's refusals are silent to the caller*. That instruction is now
obsolete, and the reason is a fix rather than a re-measurement.

From `mcp` 2.1.0 the SDK classifies a failure escaping a tool body: `ToolError`, `ResourceError`
and `MCPError` stay anticipated and keep their message, and everything else becomes
`UnexpectedToolError` whose text is replaced by a bare `Error executing tool <name>`. CopperMCP's
deliberate boundary refusals are typed `ValueError` subclasses, so on 2.1 every refusal reason
stopped reaching the model. `mcp_server` now translates a **closed, audited enumeration** of
refusal types to `ToolError` at the adapter, so a refusal keeps its reason on 2.1 and a crash is
correctly classified as one ([ADR-0121](../adr/0121-a-refusal-is-an-answer-and-a-crash-is-not.md),
`D-226`, `SEC-163`).

### What a deployer must do

- **If your environment does not pin `mcp` independently: nothing.** A fresh resolution may now
  land on 2.1.x for the first time, and that is the intended outcome.
- **If you pinned inside `>=2.0.0,<2.1.0` on 0.10.0's instruction, you may relax the pin**, or
  leave it — 2.0.x remains inside the declared range and nothing observable changes on that line.
  The server raised typed refusals before and raises them now; 2.0.x rewrapped every escaping
  exception as an anticipated `ToolError` with its text preserved either way.
- **Do not read `<2.2.0` as caution about an untested-but-probably-fine 2.2.** It is an evidence
  boundary. The full suite is run against **both** ends of the declared range, `2.0.0` and
  `2.1.1`. A *minor* release inside 2.x is what moved this contract in the first place, so
  semantic versioning is not evidence here, and the bound tracks what has been exercised rather
  than what SemVer permits. Moving past it means re-running the dual matrix — full suite against
  the new resolution and against `2.0.0`, plus `scripts/evaluate_excessive_agency.py` and
  [the refusal-contract mutants](../mutants/2026-08-27-mcp-refusal-contract.json) on each.

### The one behavioural difference a 2.1 client sees

**Refusal messages are unaffected.** No string that reaches a caller changes on either line; the
translation restores a surface on 2.1 rather than opening one.

What does change on 2.1 is the *crash* half: an unhandled server defect no longer leaks its text.
A client that was parsing exception text out of a crash — never a supported contract — will see
that text stop arriving, with the traceback kept in the server log.

A client that wants to tell the two apart on 2.1 can catch
`mcp.server.mcpserver.exceptions.UnexpectedToolError`. **Note the subclass relationship**:
`UnexpectedToolError` subclasses `ToolError`, so `isinstance(error, ToolError)` is true for a
crash as well. An "is this a refusal?" check must exclude `UnexpectedToolError` **explicitly**
rather than rely on `isinstance`. This is the single most likely integration error against the
2.1 line, and it is the same shape as the excessive-agency harness's own discriminator, which
compares class *names* against a closed set for exactly this reason.

### Stated as a non-claim

The audit behind the translation is a claim about how **one specific SDK version** classifies an
escaping exception. It is not a proof that the enumeration is complete for all time: a refusal
type added later without an audit entry, and any `mcp` release outside the tested matrix, are
both still uncovered. `R-176` carries that residue and is `Mitigated / MCP`, not closed.

## 2. Board IR accepts five more `setup` fields, so more boards convert

[The dedicated note](board-ir-setup-fields.md) is the short form. This is an **acceptance
widening**: a board whose `(setup …)` block carries a physical stackup, a drill-and-place origin,
an editor grid origin, or solder-paste stencil defaults **converted with an error in 0.10.0 and
converts now**.

Accepted, validated and discarded as typed non-claims: `stackup`, `aux_axis_origin`,
`grid_origin`, `pad_to_paste_clearance`, `pad_to_paste_clearance_ratio`. None constrains copper
geometry or electrical clearance. The last two are the paste twins of `pad_to_mask_clearance` and
`solder_mask_min_width`, already accepted on exactly this argument
([ADR-0122](../adr/0122-the-stackup-is-read-per-field-because-three-of-its-fields-are-not-about-z.md),
`D-227`, `R-178`, `SEC-164`, `B-130`, `B-131`).

**The stackup is read through a closed nested grammar, not admitted as a unit.** Inside an
accepted `stackup`, `edge_plating`, `edge_connector` and `castellated_pads` still refuse unless
explicitly `no`, and KiCad 10's `(zone_defaults …)` still refuses because a hatched-fill phase is
copper geometry.

**What a deployer must act on is the direction of the change.** A deployment that special-cased,
suppressed, alerted on or routed around a `setup`-block refusal will now receive a converted
board where it received an error. Retire the workaround; a code path that treats "conversion
succeeded" as unexpected will misbehave.

## 3. Footprint fields are accepted, and one published count changes meaning

[The dedicated note](footprint-field-acceptance.md) is the short form. This section has the one
item in the release that can silently change a **number a caller already stores**.

### The acceptance widening

Board IR now converts boards whose footprints declare schematic provenance, per-footprint
solder-mask or solder-paste defaults, an attaching zone-connection default, or a footprint-local
group. Accepted and counted: `sheetfile`, `sheetname`, `solder_mask_margin`,
`solder_paste_margin`, `solder_paste_margin_ratio` and KiCad 8's legacy `solder_paste_ratio`
spelling. Accepted conditionally: `zone_connect` in its attaching modes `1`/`2`/`3` only, and an
**unlocked** footprint-local group, which takes the same validator, the same closed child grammar
and the same lock refusal a root group already had
([ADR-0123](../adr/0123-a-container-refusal-that-names-no-field-is-the-defect.md), `D-228`,
`R-179`, `SEC-165`, `B-132`, `B-133`). The same retire-the-workaround action as §2 applies.

### `unmodelled_group_count` now spans footprints — read this before comparing stored numbers

`unmodelled_group_count` previously counted **root** `(group …)` expressions only. It now counts
**every** accepted group, at board root and inside a footprint. KiCad dispatches a footprint's
group to the same `parseGROUP` and writes it through the same formatter, so the two are one
construct and one number answers the question the count exists for.

**A caller comparing a stored `unmodelled_group_count` against a fresh one, on a board with
footprint-local groups, must expect the fresh number to be larger.** Do not read the difference
as new groups appearing on the board, and do not alert on it as drift. Nothing else about the
count changes: it is still a cardinality of discarded editor organisation, still absent from any
snapshot field, and still zero for a refused conversion.

This is the **only** number in this release whose meaning moves under an unchanged name. Every
other change here either adds a key or widens what converts.

## 4. `unmodelled_counts` grows from six entries to nine

`inspect_board_ir`'s `unmodelled_counts` map gains three keys across §2 and §3:

| Key | What it counts |
|---|---|
| `unmodelled_setup_field_count` | the five `setup` heads of §2 the board carried, as expressions |
| `unmodelled_stackup_layer_count` | `(layer …)` entries inside an accepted stackup, dielectrics included |
| `unmodelled_footprint_field_count` | the six counted `footprint` heads of §3, across every footprint |

**A client that asserted `len(unmodelled_counts) == 6`, or compared the map against a hard-coded
six-key set, must widen it.** A client that reads keys by name is unaffected. All three keys are
present on every supported board, zeros included.

Two deliberate absences from the new footprint count, because they are the sort of thing a
reconciliation script will trip on:

- `group` is **not** in `unmodelled_footprint_field_count`. It is counted by
  `unmodelled_group_count` (§3), because splitting one construct across two counters would make
  neither answer *how many groupings did I lose*.
- `zone_connect` is in **neither** count. It is validated to be inert for the attachment claim
  Board IR publishes rather than merely discarded, which is a different disclosure and is carried
  by ADR-0091's existing reasoning.

`unmodelled_stackup_layer_count` is deliberately comparable against `copper_layer_ids`: it counts
every physical stack entry, so the gap is the physical stack — thicknesses, materials, dielectric
constants, loss tangents — the snapshot does not carry.

## 5. Refusal message and locator changes that a caller can observe

Two families of caller-observable text move. **No refusal message is a contract and none ever
was** — the golden set is a regression detector, and the instruction to branch on typed codes and
locators rather than prose is unchanged. These are listed because a deployment that matched prose
anyway will notice, and because one of them is the release's stated purpose rather than a side
effect.

### Twenty footprint refusals now name their field

Twenty heads from KiCad's own `parseFOOTPRINT` grammar move from the field-less
`footprint contains an unsupported semantic field` to `footprint field '<name>' is unsupported`.

**No board's verdict changes.** Every one of those heads already refused; only the message moved.
A head absent from the table still refuses through the allowlist with the original field-less
sentence, and **no token read from a board is ever interpolated into a message** — the
interpolated name is a literal selected by lookup from a fixed tuple.

Three of those refusals are new in substance rather than in wording, and a board carrying any of
them refuses where a reader of §3 might expect acceptance:

- `(clearance …)` refuses **at any value**, including zero. It is a replacement, not a maximum:
  KiCad resolves it before custom-rule iteration, so it beats netclass and rules alike and can
  lower effective clearance to the board minimum, while sizing the void the pour leaves around
  every pad. A narrowed acceptance admitting only the provably inert zero was declined **on
  evidence** rather than on caution — `B-132` measured `clearance_zero: 0` on the cohort, so it
  would have cleared no board.
- `(zone_connect 0)` refuses — the one written mode that detaches a footprint's pads from their
  pour. Anything outside `1`/`2`/`3`, including a quoted `"2"` or a `-1`, refuses too.
- A **locked** footprint-local group refuses, as a locked root group already did.

### The `setup` refusal locator becomes more precise

An unaccepted direct `setup` child still refuses at `kicad_pcb.setup`. But a board-edge attribute
now refuses at `kicad_pcb.setup.stackup.edge_plating` (or `.edge_connector`,
`.castellated_pads`), and a malformed stack entry at `kicad_pcb.setup.stackup.layer[N]`.

**A caller matching a locator against the exact string `kicad_pcb.setup` will stop matching those
cases.** The locator is more precise than it was, not less; a prefix match still works.

## 6. Two exception types split out of the request vocabulary

This reaches a caller only through §1's classification, but it is a source-level contract change
for anything importing these types.

- **`ApplyResultInvariantError` (new, a `RuntimeError`)** carries the fifteen post-construction
  invariants that were raised as `ApplyRequestError` — an `applied` result with no new revision,
  an `applied_but_unverified` result with no diagnostic, a `refused` result that nonetheless
  reports one. Those can fire **after** an authorized write, and translating them as refusals
  would have told a caller its board was untouched at the moment the board may have changed. The
  new type is deliberately **not** in the audited refusal set, so it reaches the SDK as a crash.
  Request parsing keeps `ApplyRequestError`, so a caller catching that for malformed payloads is
  unaffected.
- **`ManifestContractError` (new, a `ValueError`)** replaces the bare `ValueError` that
  `validate_candidate`, `compare_candidates` and every other protocol-boundary manifest rejection
  raised. **Callers catching `ValueError` are unaffected — it is a subclass.**

Neither type changes any message, any response shape, or any verdict.

## 7. Changes that are real but reach no caller

Each of these has a `0.11.0` CHANGELOG entry and is listed here so the audit is complete rather
than selective. None requires deployment action.

- **The Freerouting benchmark runner survives `EPERM` from `killpg`.** `_kill_process` handled
  `ProcessLookupError` but not `PermissionError`, and `killpg(2)` reports `EPERM` once the child
  has exited and its PID — and with it the session's PGID — has been recycled onto a process this
  user may not signal. The arm is deliberately narrow, because the same `EPERM` also covers a
  fatal case, so it is accepted only against two proofs in order: `poll()` reaps the leader and
  preserves its exit status, then the null signal must report the whole *group* gone. This is
  `scripts/benchmark_freerouting_comparison.py`, a benchmark runner with no MCP tool, CLI or
  apply peer; it is in this release because a loaded host could fail an otherwise correct run
  ([issue #223](https://github.com/seunghyukchoe/copper-mcp/issues/223), eight mutants, all
  killed).
- **Two public field censuses and their differentials** — `B-130`/`B-131` for `setup` and
  `B-132`/`B-133` for `footprint` — are read-only, aggregate-only, digest-bound measuring
  instruments under `scripts/`. They add no production code, no acceleration and no
  public-contract change. They are evidence, and they are the reason §2 and §3 are decisions
  rather than guesses: each acceptance was measured over the six public boards the previous
  census found blocked, and each widening was predicted before the adapter was touched.
- **The refusal-contract audit and its mutation spec** (ADR-0121, thirteen then seventeen mutants,
  all killed) are review artifacts. The excessive-agency evaluation artifact is **unchanged** and
  replays byte-identically under both `mcp` 2.0.0 and 2.1.1; what moved is what its numbers mean,
  because under 2.1 the harness can tell a refusal from a crash for the first time.

## 8. What this release explicitly does not change

Stated as non-claims, because an absent entry and a verified absence are not the same thing:

- **no schema version moves** — Board IR stays `0.4.0`; Scene, router and candidate versions are
  unmoved, `schemas/` has no diff, and no published schema file's accepted set changes;
- **no response version moves** — the four 0.10.0 moved (`preview_route` `1.1`,
  `preview_layered_route` `1.1`, `preview_placement` `0.2.0`, placement candidates `0.1.0`) stay
  where 0.10.0 left them, and no preview, bundle or durable-job response gains or loses a field;
- **no MCP tool is added or removed.** 0.10.0 added `verify_external_route_candidate`; 0.11.0 adds
  nothing, and a client enumerating tools sees the same list;
- **no persisted artifact needs migration** — no snapshot, candidate, scene or job record;
- **no apply or write authority is added, widened or relaxed** anywhere. Apply flags, single-use
  tokens, revision checks and every board-write gate are untouched by §1, §2, §3 and §6 alike.
  In particular, accepting a field is not modelling it and is never an authorization;
- **no refusal message text is promised** — §5 lists what moved precisely because the golden set
  is a regression detector rather than a contract. Continue branching on typed codes and
  locators, not prose;
- **no `mcp` 2.2 or 3.x claim** follows from §1. `<2.2.0` is the tested boundary, not a prediction;
- **no routing-quality, electrical, SI, PI, EMC, thermal, fabrication or hardware claim** is made
  by any entry in this release. §2 and §3 in particular *widen* the set of board content whose
  only route to copper runs through a file this adapter has never read: `sheetname` and group
  membership are selectors a custom `.kicad_dru` rule can use to raise a clearance above the
  netclass value, and CopperMCP does not read `.kicad_dru` at all. `R-179` carries that residue;
- **no stackup, fabrication-frame, solder-paste or soldermask geometry claim** follows from §2.
  Layer order, thickness, material, dielectric constant, loss tangent and surface finish are
  validated and discarded; `aux_axis_origin` is the offset KiCad applies when writing drill,
  place and Gerber output, so fabrication output generated from a snapshot alone would land in a
  different frame.

## 9. CI and release-operator behavior

Only operators carrying the upstream workflows or running `make check` are affected. Everything
0.10.0 §7 said still holds. Two things move:

- **`.github/ci-budget-calibration.json` is re-recorded at this boundary**, per its own `update`
  rule, because the suite grew again — from roughly 3,200 tests at the v0.10.0 boundary to
  roughly 3,550 across the refusal-contract, setup-semantics and footprint-semantics arc. **No
  budget is raised**: CI remains 120 minutes, release verification 120, release publication 10,
  and all three still clear the half rule with the worst measured leg at 2,374 s. Re-record from
  `success` conclusions only.
- **`scripts/check_schema_sets.py` gains `v0.11.0` in `RELEASE_TAGS`.** During the cut this is
  the one listed tag that does not yet exist; every earlier listed tag must already exist, and any
  repository tag not listed still fails.

The release environment must still install `.[dev,security]` — `pip-audit` is in the `security`
extra, not `dev`.

## 10. Deployment checklist

Before switching traffic to 0.11.0:

- any independent `mcp` pin held inside `>=2.0.0,<2.1.0` on 0.10.0's instruction may be relaxed
  to `>=2.0.0,<2.2.0`, and a fresh resolution may land on 2.1.x; **0.10.0 §6's "do not take 2.1"
  is retired**;
- a client that distinguishes refusals from crashes on the 2.1 line excludes
  `UnexpectedToolError` **explicitly** rather than by `isinstance(error, ToolError)`;
- any client that parsed text out of an unhandled server crash stops relying on it — refusal
  messages are unaffected, crash text is withheld on 2.1;
- workarounds that suppressed, alerted on or routed around `setup`-block and footprint-field
  conversion refusals are retired, and no code path treats a successful conversion of a
  previously refused board as unexpected;
- **any stored `unmodelled_group_count` is re-baselined before comparison** on boards with
  footprint-local groups — the fresh number will be larger and that is not drift;
- clients reading `unmodelled_counts` as a fixed-size map widen it from six keys to nine, and do
  not expect `group` or `zone_connect` to appear in `unmodelled_footprint_field_count`;
- anything matching a conversion diagnostic against the exact locator `kicad_pcb.setup` uses a
  prefix match, since stackup refusals now carry a deeper locator;
- anything matching the prose `footprint contains an unsupported semantic field` is retired in
  favour of the typed code — twenty heads now say `footprint field '<name>' is unsupported`;
- code catching `ApplyRequestError` for post-write result invariants moves to
  `ApplyResultInvariantError`; code catching it for malformed apply requests is unaffected, and
  code catching `ValueError` around manifest decoding is unaffected;
- **no Board IR re-conversion is scheduled for this release** — if one is pending it is 0.9.0's,
  not this one's and not 0.10.0's; and
- release operators keep `.github/ci-budget-calibration.json` synchronized with successful hosted
  durations and install `.[dev,security]`.
