# Parse-budget calibration and its DoS cost

**Research date:** 2026-08-06
**Reviewed against:** CopperMCP at `c4dddba`, KiCad board format version 20260206
**Covers:** how many S-expression tokens, nodes, list children, Board IR objects, ring vertices,
and polygon intersection tests one megabyte of real KiCad board actually costs; what the shipped
`ParseLimits` defaults therefore admit and refuse; and what raising them costs against deliberately
adversarial input.
**Refuses to claim:** that these densities generalise to KiCad file-format versions other than the
20260206-era boards measured, to `.kicad_sch` schematics (a different grammar with its own budgets),
or to any board shape not represented in the 38-board sample below. It is a calibration snapshot,
not a model of KiCad.

## 1. The defect

`ParseLimits` shipped eleven independent budgets. Exactly one of them, `max_input_bytes`, could be
moved by an operator, and only downward: every board-reading service built its limits as

```python
default_limits = ParseLimits()
limits = replace(
    default_limits,
    max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
)
```

copied to thirteen call sites. The other ten were hardcoded.

That would be tolerable if the byte ceiling were the binding constraint. It was not. A real
4,386,848-byte four-layer board was refused at the defaults, and the budget that refused it was
`max_nodes` at 500,000 — a knob with no environment variable at all. The operator's only available
control, `COPPER_MCP_MAX_BOARD_BYTES`, moved a ceiling the board was already five times inside
(issue #112).

The refusal said `budget.exceeded` and nothing else, so which of the ten had run out was not
recoverable from the response: `inspect_board_ir` reports `conversion_diagnostic_counts`, which is a
`Counter` over diagnostic *codes*, and the messages that did distinguish them ("node budget
exceeded", "token budget exceeded") are dropped before the caller sees anything.

## 2. Method

Every `.kicad_pcb` reachable from this repository was parsed with all budgets raised far beyond
reach, and the exact quantities each budget charges were counted using the same accounting the
enforcing code uses — one node per atom and one per closed list, as `parse_sexpr` charges them; the
object groups `validate_content` sums; the ring-pair count `_validate_ring` performs. The
operator's private four-layer board was measured in place and never copied into the repository.

Sample: 40 `.kicad_pcb` files (37 convertible, 1 unsupported format version, 1 missing a required
field, 1 deliberately malformed) plus the private board. The `tscircuit-benchmark` corpus at
`benchmarks/corpora/` was **not** usable for this measurement and that is worth stating plainly: it
is SimpleRouteJson, not KiCad, so it exercises the benchmark import seam and not the S-expression
budgets under calibration here. Using it would have produced a number about a different grammar.

## 3. Measured density

Densities are per mebibyte of source, over the 37 convertible boards.

| Quantity | Minimum | Median | Maximum | Budget it charges |
|---|---:|---:|---:|---|
| S-expression tokens | 168,109 | 185,249 | 215,168 | `max_tokens` |
| S-expression nodes | 129,520 | 143,997 | 169,092 | `max_nodes` |
| Board IR objects | 1,130 | 9,502 | 14,899 | `max_objects` |

The token and node densities are remarkably tight — a factor of 1.3 across four orders of magnitude
of file size — because they are a property of the *text format* rather than of the design. That is
what makes a byte ceiling a usable proxy for them at all.

Three quantities are not densities and are reported as observed maxima:

| Quantity | Observed maximum | Where | Budget |
|---|---:|---|---|
| Children in one list | 29,133 | private 4.4 MB board (its `kicad_pcb` root) | `max_children_per_list` |
| Nesting depth | 6 | every board, including the 4.4 MB one | `max_depth` |
| Total ring vertices | 60 | `ne5532-stereo-summing-routing-v1` | `max_total_vertices` |
| Polygon intersection tests | 46 | CopperTone buffer | `max_intersection_tests` |

Selected rows, largest first:

| Board | Bytes | Tokens | Nodes | Nodes/MiB | Widest list | Depth |
|---|---:|---:|---:|---:|---:|---:|
| private four-layer board | 4,386,848 | 830,950 | 648,510 | 154,940 | 29,133 | 6 |
| `hardware/coppertone-buffer` | 166,070 | 32,801 | 25,853 | 163,237 | 3,191 | 6 |
| `route-candidate/zone-fill-islands` | 10,435 | 2,080 | 1,621 | 162,889 | 95 | 6 |
| `audio/ne5532-stereo-summing-routing-v1` | 9,308 | 1,910 | 1,501 | 169,092 | 20 | 5 |
| `board-ir-v0.2/courtyard-orthogonal-chains` | 4,942 | 903 | 689 | 146,190 | 35 | 6 |
| `board-ir-v0.1/subset` | 1,906 | 373 | 285 | 156,791 | 11 | 5 |

The private board's 648,510 nodes against a 500,000 budget is the whole of issue #112.

## 4. The defaults, and where each number comes from

The rule: **a board that fits inside `max_input_bytes` (16 MiB) should normally fit inside every
scale budget too.** Applied at the *densest* observation rather than the median, so it is a
worst-case rule.

| Budget | Was | Now | Derivation |
|---|---:|---:|---|
| `max_input_bytes` | 16 MiB | 16 MiB | unchanged; the headline ceiling |
| `max_tokens` | 1,000,000 | **4,000,000** | 16 × 215,168 = 3,442,688, rounded up |
| `max_nodes` | 500,000 | **3,000,000** | 16 × 169,092 = 2,705,472, rounded up |
| `max_children_per_list` | 100,000 | **500,000** | 16 × 6,970 = 111,520, ×4.5 for a flatter root |
| `max_objects` | 250,000 | 250,000 | 16 × 14,899 = 238,384 already fits; and see §5 |
| `max_total_vertices` | 1,000,000 | **2,000,000** | shortest legal `(xy a b)` is 10 B ⇒ ≤1,677,721 |
| `max_intersection_tests` | 2,000,000 | 2,000,000 | not byte-derived; see §5 |
| `max_depth` | 128 | 128 | observed maximum is 6; a factor of 21 in hand |
| `max_atom_chars` | 4,096 | 4,096 | bounds one token, not the document |
| `max_vertices_per_ring` | 100,000 | 100,000 | schema-bounded; reached via intersection tests first |

Multiplying everything by four and moving on would have been wrong in both directions: `max_nodes`
needed six times, and `max_intersection_tests` needed nothing at all.

A side effect worth naming: the canonical *writer* enforces the `ParseLimits` **defaults** against
its own output (`_enforce_default_budget`), so that anything CopperMCP writes stays readable by a
default-configured decoder. Raising the defaults was therefore not optional — leaving them while
raising only the settings would have produced a parser that accepts boards whose snapshots the
writer then refuses to emit.

## 5. Two budgets deliberately not derived from the byte ceiling

**`max_intersection_tests` is superlinear.** `_validate_ring` compares every pair of edges, so a
ring of *n* points costs about *n²/2* tests:

| Ring points | Intersection tests | Wall time |
|---:|---:|---:|
| 500 | 124,250 | 0.10 s |
| 1,000 | 498,500 | 0.41 s |
| 2,001 | 1,997,000 | 1.65 s |
| 4,000 | 7,994,000 | 6.89 s |

That is 0.83 µs per test, stable across the range. The 2,000,000 default is therefore a ~1.65 s
ceiling, and it binds at a ring of roughly 2,000 points — a construct occupying about 40 KiB, which
is nothing against a 16 MiB byte ceiling. Sizing this budget "to whatever fits in the byte ceiling"
would buy a multi-minute refusal instead of a fast one. The observed maximum across the whole
corpus is 46 tests, so the default has four orders of magnitude in hand for real boards.

**`max_objects` is already at a fixed schema ceiling.** `validate_content` refuses at
`min(limits.max_objects, _SCHEMA_MAX_OBJECTS)` with `_SCHEMA_MAX_OBJECTS = 250_000`, so any
configured value above 250,000 changes nothing. Its environment range therefore ends there: a
setting that silently does nothing is worse than one that refuses.

## 6. DoS cost of the new defaults

These budgets are the defence against a hostile `.kicad_pcb`, so raising them has to be priced.
Four adversarial shapes were built to fill the whole 16 MiB byte ceiling, each maximising a
different budget, and parsed at the old and the new defaults. Peak RSS is reported net of a
~60 MiB interpreter baseline.

| Shape | What it maximises | Old defaults | New defaults |
|---|---|---|---|
| flat (`(kicad_pcb a a a …)`) | nodes per byte | `children_per_list`, 1.0 MiB, 0.06 s | `children_per_list`, 6.1 MiB, 0.27 s |
| wide (one enormous child list) | one list's width | `children_per_list`, 1.0 MiB, 0.06 s | `children_per_list`, 5.9 MiB, 0.32 s |
| tree (narrow lists, maximal count) | node count | `nodes`, 4.0 MiB, 0.27 s | `nodes`, 23.8 MiB, 2.43 s |
| deep (repeated 126-level nesting) | retained objects | `nodes`, 61.1 MiB, 0.70 s | `tokens`, **244.3 MiB, 2.91 s** |
| strings (maximal quoted atoms) | tokenizer accumulator | parsed, 21.2 MiB, 1.22 s | parsed, 21.2 MiB, 1.23 s |

**The worst case rose from 61 MiB / 0.70 s to 244 MiB / 2.91 s.** Every shape still refuses, in
bounded time, with a typed code that names the budget.

Two findings make that number usable rather than merely recorded.

**Peak parse-arena residency is linear in `max_tokens`, at about 61 bytes per admitted token.**
Old defaults: 61.1 MiB at ~1,000,000 tokens. New defaults: 244.3 MiB at 4,000,000 tokens. The same
constant, twice, across a factor of four. It holds because the memory worst case is maximal nesting,
where each level spends two tokens and retains one list object. So `max_tokens` — not
`max_input_bytes` — is the parser's memory control, and the arithmetic an operator needs is
`peak ≈ 61 B × max_tokens`.

**Lowering the byte ceiling does not mitigate the deep shape.** At a 4 MiB byte ceiling with the
new structural defaults, the deep shape still costs 244.3 MiB and 2.91 s, because 4 MiB of
`(((…)))` already exhausts a 4,000,000-token budget. An operator hardening against this must lower
`COPPER_MCP_MAX_PARSE_TOKENS`, and `COPPER_MCP_MAX_PARSE_TOKENS=1000000` restores exactly the
pre-change 61 MiB posture while keeping the discriminated refusal. That is the mitigation, and it is
the one an operator can now actually reach.

## 7. Result on the board that motivated the issue

The private 4,386,848-byte four-layer board, at the shipped defaults:

- S-expression parse: **succeeds** (648,510 nodes against 3,000,000; 830,950 tokens against
  4,000,000; widest list 29,133 against 500,000), 1.48 s, 89.3 MiB peak RSS.
- At the previous defaults the same board refused with `budget.exceeded.nodes`.
- Conversion then refuses with `unsupported.construct` — "root graphic on copper or Edge.Cuts is
  unsupported". That is issue #111 (Edge.Cuts `gr_line` outlines), a separate defect being fixed in
  parallel, and it is **not** a budget refusal. The budget blocker is gone; the construct blocker
  is next.

## 8. What this does not establish

- Nothing here says the board *converts*. It says the parser admits it.
- The densities are from 38 boards, one of which is an order of magnitude larger than the rest.
  A 40 MB board is outside the sample and outside the 16 MiB ceiling either way.
- The memory law was measured on CPython 3.12 on macOS/arm64. The constant is an implementation
  detail of that runtime, not a portable guarantee; the *linearity* is a property of the parser.
- No claim is made about `.kicad_sch` or Circuit Intent budgets, which are a separate grammar with
  separate limits and are deliberately untouched by this calibration.
