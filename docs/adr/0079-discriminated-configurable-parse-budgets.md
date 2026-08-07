# ADR-0079: Make the structural parse budgets operator-settable, and name the one that refused

- Status: Accepted
- Date: 2026-08-06
- Owners: `@seunghyukchoe`
- Related: ADR-0069, D-154, B-090, SEC-121, R-117, issue #112, issue #111

## Context

`ParseLimits` carries eleven independent budgets over untrusted KiCad and Board IR input. Only one
of them, `max_input_bytes`, was reachable from a deployment's environment, and only downward: every
board-reading service derived its limits from `min(ParseLimits().max_input_bytes,
settings.max_board_bytes)` and hardcoded the rest. The same five-line block appeared at thirteen
call sites.

Measurement (see [the calibration note](../research/parse-budget-calibration-v1.md)) shows the
budgets were also mis-scaled against each other. One mebibyte of ordinary KiCad source carries
129,520–169,092 S-expression nodes, so the 16 MiB byte ceiling implied up to about 2.7 million
nodes — against a node budget of 500,000. The node budget therefore refused every board above
roughly 3.2 MiB, and the byte ceiling could never bind. `COPPER_MCP_MAX_BOARD_BYTES` was not a
control over anything an operator would notice.

A real 4.4 MB four-layer board hit exactly that: refused at 648,510 nodes, with no knob to turn
(issue #112).

The refusal also could not say which budget ran out. All ten structural budgets raised the single
code `budget.exceeded`, and the messages that did distinguish them are dropped before a caller sees
the response — `inspect_board_ir` publishes `conversion_diagnostic_counts`, a `Counter` over codes.
So the answer an operator received was "some ceiling, somewhere", which is not actionable when six
of them are about to become settable.

One more asymmetry surfaced while cataloguing the raisers. The 64-courtyards-per-footprint rule is
a *fixed schema* constant, and the Board IR decoder already refuses it under `schema.limit` while
the KiCad adapter refused the identical rule under `budget.exceeded`.

## Decision

**Six structural budgets become operator settings**, each with its own environment variable
following the existing `COPPER_MCP_MAX_*` naming, each validated by the same bounded-integer helper
the other settings use, each refusing with a typed `ConfigurationError`:
`COPPER_MCP_MAX_PARSE_TOKENS`, `COPPER_MCP_MAX_PARSE_NODES`,
`COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST`, `COPPER_MCP_MAX_PARSE_OBJECTS`,
`COPPER_MCP_MAX_PARSE_TOTAL_VERTICES`, `COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS`.

**Every range ends where the budget stops being reachable.** Three different facts set that point:
the parser's own 16 MiB input ceiling (a token costs at least one byte, a node or list child at
least two, a vertex at least ten), the Board IR schema's fixed 250,000-object limit, and — for the
one budget whose cost is superlinear — an explicit ~17 s wall-clock ceiling. A setting that accepts
a number which changes nothing is worse than one that refuses it. This is emphatically not an
unbounded parser: it is a parser whose bounds an operator can now see and move within a stated
envelope.

**`max_depth`, `max_atom_chars`, `max_vertices_per_ring`, and `max_diagnostics` stay fixed.** They
bound the shape of a single construct rather than the scale of a document, and no measured board
comes within two orders of magnitude of any of them — real boards nest at most 6 levels deep against
a budget of 128.

**One seam derives limits from settings.** `copper_mcp.parse_budgets.parse_limits_for` replaces the
thirteen copies. `ParseLimits` itself keeps no dependency on process configuration. The six
structural budgets are taken as configured; `max_input_bytes` keeps its `min` semantics, because
`COPPER_MCP_MAX_BOARD_BYTES` also bounds workspace reads, DRC captures, and live serializations, and
must not widen the parser's exposure as a side effect of being raised for one of those.

**Defaults are re-derived from measurement**, at the densest observation rather than the median:
`max_tokens` 1,000,000 → 4,000,000, `max_nodes` 500,000 → 3,000,000, `max_children_per_list`
100,000 → 500,000, `max_total_vertices` 1,000,000 → 2,000,000. `max_objects` and
`max_intersection_tests` are unchanged, for stated reasons rather than by omission.

**`budget.exceeded` becomes ten discriminated codes** of the form `budget.exceeded.<field>`, where
`<field>` is the `ParseLimits` field name without its `max_` prefix — `budget.exceeded.nodes` is
raised by `max_nodes`, which is set by `COPPER_MCP_MAX_PARSE_NODES`. The knob and the refusal share
a name. `BUDGET_EXCEEDED_PREFIX` is exported so a caller that only needs "some budget" can match the
prefix and stay correct when an eleventh budget appears.

**The courtyard-count refusal moves to `schema.limit`**, matching what the Board IR decoder already
returned for the same rule. The resulting invariant is worth having: every `budget.exceeded.*` code
names a budget an operator can move, and every fixed ceiling is a `schema.limit`.

**A budget refusal names configuration only.** The budget name and its configured value are
process configuration, not board content. The message names the budget, the locator is a byte
offset, and nothing derived from the document appears in either.

## Consequences

Improved: an operator who is refused now learns which ceiling stopped them and can change it; the
byte ceiling becomes a real control because the structural budgets no longer bind first for
ordinary boards; the 4.4 MB board in issue #112 parses; and a future budget becomes settable at all
thirteen call sites at once or not at all.

**This is a breaking change to a published diagnostic code**, handled the way `unsupported.document`
was in 0.6.0: a caller matching the exact string `budget.exceeded` on the Board IR conversion
surface stops matching. The migration is in
[`docs/migrations/copper-mcp-0.7.0.md`](../migrations/copper-mcp-0.7.0.md). No content address
moves — `tests/test_golden_identities.py` is unchanged and passing, because budgets bound what is
admitted and never what is written.

Made harder, and priced rather than hidden: the adversarial worst case rises from 61 MiB / 0.70 s to
244 MiB / 2.91 s of transient parse arena. Peak residency is linear in `max_tokens` at ~61 bytes per
admitted token, so an operator restores the old posture exactly with
`COPPER_MCP_MAX_PARSE_TOKENS=1000000`. Lowering `COPPER_MCP_MAX_BOARD_BYTES` does *not* mitigate the
deep-nesting shape, which is why the token budget had to become settable in its own right. Recorded
in SEC-121 and B-090.

Also narrowed: `_bounded_int` now requires an optional sign followed by ASCII digits. `int()` alone
accepted `"1_000"` as 1000 and the Arabic-Indic `"٤"` as 4, so a deployment's environment could read
as one ceiling and enforce another. This tightens all thirteen integer settings, not only the new
ones, and is a deliberate fail-closed narrowing consistent with the exact-membership rule the
allow-flags already use.

Follow-up: `kicad_ipc.py` still hardcodes its own `max_tokens=2_000_000, max_nodes=1_000_000` for
the live-capture object counter, values chosen ad hoc and previously *more* permissive than the
file-backed path. The new defaults make the file path at least as permissive as the live one, so the
inconsistency no longer produces a surface where a board readable live is refused from disk; routing
that call site through `parse_limits_for` needs the settings plumbing it does not have today.

## Alternatives considered

**Raise the defaults without making them settable.** Rejected: it fixes one board and leaves the
next operator in exactly the position issue #112 describes, with no knob and a refusal that will not
say which ceiling it hit.

**Make them settable without re-deriving the defaults.** Rejected: it would require every operator
with an ordinary large board to discover and set two variables before the tool works at all, and it
leaves the byte ceiling still unable to bind.

**Multiply every budget by four.** Rejected by the measurement. `max_nodes` needed six times;
`max_intersection_tests` needed nothing and would have become a multi-minute refusal; `max_objects`
would have become a number that silently does nothing above the schema ceiling.

**Keep `budget.exceeded` and add a separate field to `Diagnostic` naming the budget.** Rejected:
the published summary counts *codes*, so a new field would not reach `conversion_diagnostic_counts`
and the operator's actual view would be unchanged.

**Discriminate the Circuit Intent budgets in the same pass.** Deferred deliberately. Those budgets
have no operator knobs, and the invariant this ADR establishes — a discriminated code exists exactly
where a knob exists — is more useful than uniformity for its own sake. When Circuit Intent budgets
become settable they should take the same treatment.

**Lower `max_depth` to shrink the adversarial worst case.** Attractive on the numbers — real boards
nest 6 deep against a budget of 128, and the memory worst case is a deep-nesting shape — but
rejected here as out of scope and asymmetric in risk: an over-tight depth ceiling refuses a legal
board, which is worse than a bounded 244 MiB transient. Recorded in R-117 so the option stays
visible rather than being silently forgotten.
