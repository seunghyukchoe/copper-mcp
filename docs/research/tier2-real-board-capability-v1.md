# Tier-2 real-board capability survey

**Measurement date:** 2026-08-07 · **Measured at commit:** `086f6cd` (the `main` tip at measurement
time; re-checked on two boards at `7e68f3a` with identical verdicts) · **Ledger:** [B-096](../ledgers/benchmark-ledger.md)
· **Artifact:** [`2026-08-07-real-board-tier2-v1.json`](../../benchmarks/results/capability/2026-08-07-real-board-tier2-v1.json)

Issue #116 measured whether real KiCad boards **convert**. Between #118, #119, #120, #121, #122 and
#123 that number moved from 1 of 23 to 10 of 12 on the deduplicated corpus. Conversion is step one.
Nobody had measured what the surfaces *built on* conversion do once a board is in.

This is that measurement. Five read-only surfaces, twelve real boards, default settings, wall clock
recorded on every call. It answers one question per surface: **works, refuses, or too slow to use.**

It is a survey and nothing here is a fix. Every gap it found is filed rather than patched.

## What was measured, and what was deliberately not

| | |
|---|---|
| Corpus | 12 boards from one designer's working project tree, out of repository and not redistributable. `.history/` directories and derived stems (`routed-source`, `best-board`, `-placed`) excluded — those are copies of a board already counted. |
| Access | Read-only throughout. No board was written, copied into this repository, or opened in an editor. |
| Authority | `Settings` constructed directly, so `allow_apply`, `allow_live_ipc` and `allow_live_apply` are all `false` and no ambient environment variable can reach them. Appliability is measured by calling the apply path's own pure identity predicate on the converted snapshot — never by attempting an apply. |
| Settings | Defaults, unchanged. One net class for every board and every surface (clearance 200,000 nm, track 250,000 nm, via 600,000/300,000 nm), so a difference between two boards is a difference between the boards. |
| Runner | [`scripts/benchmark_real_board_capability.py`](../../scripts/benchmark_real_board_capability.py), which takes the corpus root as an argument and bakes no private path in. |
| Environment | Apple arm64; macOS 26.5.2; Python 3.12.13; **KiCad 10.0.5 invoked for real** at `/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli`. |
| Privacy | The artifact and this document carry counts, typed status and refusal codes, timings, and content-address digests. They carry **no** net name, no component reference, no coordinate, and no geometry. Board names are the corpus subdirectory names, one of which is already published in #116. |

Sampling, decided before the run and never by retrying: route preview is per-net and each call
re-converts the whole board, so at most **40 nets per board** were attempted, in Board IR canonical
order. Placement preview was run in batches of 64 with a **one-subject-per-call fallback on any
non-clean batch**, so every one of the 991 footprints still received its own verdict. Route preview
ran on `F.Cu` only.

Whole run: **692.8 s** for 12 boards across 5 surfaces.

## The one-line answer per surface

| Surface | Verdict | The number that decides it |
|---|---|---|
| Board IR conversion | **works** | 10 of 12 boards convert; 991 footprints, 2,851 pads, 100,020 segments, 3,204 vias, 39 zones, 652 nets modelled |
| Authoritative KiCad DRC | **works — the only surface that works on every board** | 12 of 12 reported, including both boards Board IR refuses; 0.61–1.96 s |
| Circuit Scene, bounded region | **works** | 10 of 10, no truncation, 10–605 objects, ≤ 3.7 s |
| Circuit Scene, whole board | **refuses silently** | 8 of 10 hit `max_scene_objects`; on all 8 the `vias`, `zones` and `rules` lists come back **empty** while the board holds up to 1,003 vias |
| Placement preview | **works, and can never be applied** | 991 of 991 footprints previewed, 0 refusals; **10 of 10 boards refuse the source-preserving render** |
| Route preview | **refuses** | **0 of 345 net previews routed.** 250 already connected, 71 over the obstacle budget, 23 not two-pin, 1 partially routed |

Nothing was too slow to be *unusable*, but the cost is real and it is per request: see
[Wall clock](#wall-clock) below.

## 1. Board IR conversion — the baseline

Counts are what the surfaces downstream are working with, so they are the denominator for
everything else on this page.

| Board | Layers | Footprints | Pads | Segments | Vias | Zones | Nets | Source | Convert |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `mixer/…/ch` | 4 | 202 | 580 | 31,389 | 1,003 | 5 | 147 | 9.10 MB | 3.55 s |
| `mixer/…/ctl` | 2 | 29 | 105 | 4,451 | 118 | 2 | 45 | 1.07 MB | 0.44 s |
| `mixer/…/cue` | — | — | — | — | — | — | — | 0.93 MB | **refused** |
| `mixer/…/fdr` | 2 | 17 | 32 | 34 | 1 | 1 | 10 | 0.10 MB | 0.02 s |
| `mixer/…/in` | 4 | 64 | 210 | 7,289 | 196 | 5 | 48 | 2.43 MB | 0.84 s |
| `mixer/…/iso` | 4 | 142 | 399 | 12,697 | 447 | 5 | 86 | 4.19 MB | 1.49 s |
| `mixer/…/mtr` | 2 | 31 | 71 | 80 | 7 | 1 | 15 | 0.16 MB | 0.04 s |
| `mixer/…/out` | 4 | 84 | 285 | 9,758 | 317 | 5 | 65 | 3.37 MB | 1.14 s |
| `mixer/…/ph` | 4 | 156 | 406 | 13,072 | 473 | 5 | 80 | 4.37 MB | 1.53 s |
| `mixer/…/pwr` | 4 | 113 | 330 | 6,344 | 126 | 5 | 64 | 2.95 MB | 0.89 s |
| `mixer/…/sum` | 4 | 153 | 433 | 14,906 | 516 | 5 | 92 | 4.87 MB | 1.83 s |
| `phono-preamp/…/tier1-rev-a` | — | — | — | — | — | — | — | 4.39 MB | **refused** |
| **Total (converting)** | | **991** | **2,851** | **100,020** | **3,204** | **39** | **652** | | |

The two refusals, both `unsupported.construct`:

- `tier1-rev-a` — `net-tie footprint copper is unsupported in Board IR adapter v0.2`. Known,
  diagnosed in #121, and a correct refusal: Board IR models nets as disjoint and net-tie copper
  belongs to two at once.
- `cue` — `root expression contains an unsupported semantic construct`, locator
  `kicad_pcb.unsupported`. Neither the message nor the locator names the construct. Local
  instrumentation identifies it as **three root-level `(group …)` expressions**. Filed as a gap:
  a group has no geometry, layer, net or constraint, and the refusal is undiagnosable from its
  own text.

### Identity mix — the number that decides everything downstream

The converter falls back to a revision-derived identity when a KiCad UUID names more than one
object ([D-158](../ledgers/decision-ledger.md), [`kicad-uuid-uniqueness-v1.md`](kicad-uuid-uniqueness-v1.md)),
and **every source-preserving write-back path refuses a snapshot containing one.**

| Board | Derived footprints | Derived pads | Derived outline | Any other derived kind |
|---|---:|---:|---:|---|
| `ch` | 199 / 202 | 530 / 580 | 1 / 1 | none |
| `ctl` | 25 / 29 | 55 / 105 | 1 / 1 | none |
| `fdr` | **0 / 17** | **0 / 32** | 1 / 1 | none |
| `in` | 58 / 64 | 167 / 210 | 1 / 1 | none |
| `iso` | 137 / 142 | 367 / 399 | 1 / 1 | none |
| `mtr` | **0 / 31** | **0 / 71** | 1 / 1 | none |
| `out` | 81 / 84 | 268 / 285 | 1 / 1 | none |
| `ph` | 155 / 156 | 403 / 406 | 1 / 1 | none |
| `pwr` | 110 / 113 | 320 / 330 | 1 / 1 | none |
| `sum` | 148 / 153 | 412 / 433 | 1 / 1 | none |

Read the `fdr` and `mtr` rows twice. Those two boards have **100 % native identities on every
footprint, pad, segment, via and zone**. Their only derived object is the board outline — and it is
derived on all ten. Segments, vias and zones are native everywhere, all 103,263 of them.

## 2. Authoritative KiCad DRC — works, everywhere

`run_board_drc` invoked real `kicad-cli` 10.0.5 on all twelve boards and returned a schema-valid,
revision-bound summary every time. It is the **only surface that works on a board Board IR refuses**:
`cue` and `tier1-rev-a` both produced full DRC reports.

| Board | Wall | Passed | Clean | Errors | Warnings | Unconnected | Distinct violation types |
|---|---:|---|---|---:|---:|---:|---:|
| `ch` | 1.96 s | no | no | 942 | 582 | 56 | 14 |
| `ctl` | 0.86 s | no | no | 526 | 25 | 8 | 8 |
| `cue` | 0.76 s | no | no | 210 | 429 | 110 | 15 |
| `fdr` | 0.61 s | **yes** | no | 0 | 4 | 0 | 1 |
| `in` | 0.95 s | no | no | 1,007 | 102 | 19 | 13 |
| `iso` | 1.17 s | no | no | 988 | 240 | 6 | 13 |
| `mtr` | 0.62 s | **yes** | no | 0 | 4 | 0 | 1 |
| `out` | 1.07 s | no | no | 882 | 155 | 18 | 12 |
| `ph` | 1.19 s | no | no | 1,125 | 295 | 18 | 12 |
| `pwr` | 1.00 s | no | no | 718 | 124 | 28 | 9 |
| `sum` | 1.39 s | no | no | 988 | 209 | 11 | 13 |
| `tier1-rev-a` | 1.35 s | **yes** | no | 0 | 37 | 0 | 3 |
| **Total** | | **3 / 12** | **0 / 12** | **7,386** | **2,206** | **274** | |

`clean` is false on every board including the three that pass, because `clean` means no violation of
any severity and all twelve carry at least a `lib_footprint_issues` or silkscreen warning. Keeping
`passed` and `clean` apart is doing exactly the work it should here.

The 274 unconnected items matter for section 5: these boards are finished designs, so the router has
almost nothing left to do on them — but they are not *fully* routed either, and nothing in the route
surface can be pointed at what remains.

**Verdict: works.** No refusal, no timeout, sub-2-second on a 9 MB board. This is the surface a
caller can rely on today.

## 3. Circuit Scene observation

### Bounded region — works

5 mm around the lexicographically first footprint reference, on all ten converting boards:

| Board | Wall | Objects returned | Omitted | Ceiling |
|---|---:|---:|---:|---|
| `ch` | 3.69 s | 289 | 0 | none |
| `ctl` | 0.46 s | 115 | 0 | none |
| `fdr` | 0.02 s | 10 | 0 | none |
| `in` | 0.85 s | 504 | 0 | none |
| `iso` | 1.59 s | 605 | 0 | none |
| `mtr` | 0.04 s | 55 | 0 | none |
| `out` | 1.18 s | 313 | 0 | none |
| `ph` | 1.62 s | 479 | 0 | none |
| `pwr` | 0.90 s | 305 | 0 | none |
| `sum` | 1.85 s | 518 | 0 | none |

Ten of ten, nothing truncated, every object kind represented. **A region-scoped scene is a working
surface.**

### Whole board — refuses in a way a caller cannot see

| Board | Objects returned | Objects omitted | Ceiling | Segments in scene | **Vias in scene** | **Zones in scene** | **Rules in scene** |
|---|---:|---:|---|---:|---:|---:|---:|
| `ch` | 2,000 | 31,181 | `max_scene_objects` | 1,217 | **0** of 1,003 | **0** of 5 | **0** of 1 |
| `ctl` | 2,000 | 2,707 | `max_scene_objects` | 1,865 | **0** of 118 | **0** of 2 | **0** of 1 |
| `fdr` | 87 | 0 | none | 34 | 1 of 1 | 1 of 1 | 1 of 1 |
| `in` | 2,000 | 5,766 | `max_scene_objects` | 1,725 | **0** of 196 | **0** of 5 | **0** of 1 |
| `iso` | 2,000 | 11,692 | `max_scene_objects` | 1,458 | **0** of 447 | **0** of 5 | **0** of 1 |
| `mtr` | 192 | 0 | none | 80 | 7 of 7 | 1 of 1 | 1 of 1 |
| `out` | 2,000 | 8,451 | `max_scene_objects` | 1,630 | **0** of 317 | **0** of 5 | **0** of 1 |
| `ph` | 2,000 | 12,114 | `max_scene_objects` | 1,437 | **0** of 473 | **0** of 5 | **0** of 1 |
| `pwr` | 2,000 | 4,920 | `max_scene_objects` | 1,556 | **0** of 126 | **0** of 5 | **0** of 1 |
| `sum` | 2,000 | 14,015 | `max_scene_objects` | 1,413 | **0** of 516 | **0** of 5 | **0** of 1 |

Eight of ten boards hit the ceiling, dropping **90,846 objects** in total. The count is not the
problem — truncation on a 33,000-object board is correct and expected. **The problem is which
objects survive.** The budget is spent in one fixed emission order — outline, footprints, pads,
keepouts, segments, arcs, vias, zones, rules — so segments consume whatever the static kinds leave
and every kind after them returns an empty list. On all eight truncated boards the response says
there are no vias, no zones, and no net-class rules.

`ceiling_hit` and `objects_omitted` do report that truncation happened. They do not report *what was
truncated*, and an empty `vias` list is indistinguishable from a board with no vias.

**Verdict: bounded regions work; the whole-board region refuses silently and is filed as a gap.**

## 4. Placement preview

Every one of the 991 footprints on the ten converting boards received a verdict.

| Board | Subjects | `previewed` | Any refusal | Median single-subject latency |
|---|---:|---:|---|---:|
| `ch` | 202 | 202 | none | 4,024.8 ms |
| `ctl` | 29 | 29 | none | 469.1 ms |
| `fdr` | 17 | 17 | none | 24.2 ms |
| `in` | 64 | 64 | none | 929.0 ms |
| `iso` | 142 | 142 | none | 1,708.1 ms |
| `mtr` | 31 | 31 | none | 58.4 ms |
| `out` | 84 | 84 | none | 1,257.4 ms |
| `ph` | 156 | 156 | none | 1,775.1 ms |
| `pwr` | 113 | 113 | none | 1,022.3 ms |
| `sum` | 153 | 153 | none | 2,146.2 ms |
| **Total** | **991** | **991** | **0** | |

**The narrow supported subset never bound.** Front-side, orthogonal, unfilled rectangular
courtyard: every footprint in this corpus already satisfies it — all 991 are front-side and at 0°
rotation, and #118's courtyard work covers the rest. Not one `unsupported_geometry`, not one
`illegal_placement`, not one `unresolved_ref`. This corpus does not test the subset boundary at all,
which is worth saying plainly: **the subset is narrow but it is not what is stopping anybody here.**

What is stopping everybody here is the next line.

### None of it can ever be applied

`render_kicad_placement_candidate_board` is the exact pure replay the apply-token mint runs before
handing out authority. It is a pure function over bytes and writes nothing, so the survey can call
it directly. On a previewed candidate from each of the ten boards:

| Board | Source-preserving render | Route apply gate |
|---|---|---|
| all ten | **refused** — `source geometry uses revision-derived identities` | **refused** — `modeled KiCad geometry requires native uuid or tstamp identities` |

Ten of ten, both paths. On eight boards duplicated KiCad UUIDs are a sufficient cause and that is
already recorded (D-158, R-119). On `fdr` and `mtr` they are not: those boards are 100 % native
except for one object, the board outline, and they still refuse.

The cause is that a `gr_line` Edge.Cuts outline — the ordinary way KiCad draws a board edge, and the
shape #111 taught the adapter to read — is assembled from many source expressions into one
`OutlineContour` that can take a UUID from none of them, so it is given
`contour:derived:<hash of kicad_pcb.edge_cuts>`. Both identity gates then see a derived id among
`content.outline` and refuse the whole board. Only a single `gr_rect` outline yields a native
contour identity — and **every committed fixture and the CopperTone reference board uses exactly
that shape**, which is why 1,900 tests never saw this. Same failure mode as #104 and #116: the
fixtures were authored from the same assumption as the code.

**Verdict: preview works on 991 of 991 footprints and produces nothing that can ever be applied.**
Filed as a gap.

## 5. Route preview

345 net previews across ten boards, on `F.Cu`, at default settings.

| Board | Nets | Attempted | `routed` | `already_connected` | `obstacle_budget_exceeded` | `invalid_two_pin_net` | `unsupported_geometry` | Median latency |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `ch` | 147 | 40 | **0** | 15 | 22 | 3 | 0 | 3,848.8 ms |
| `ctl` | 45 | 40 | **0** | 30 | 6 | 3 | 1 | 453.9 ms |
| `fdr` | 10 | 10 | **0** | 9 | 0 | 1 | 0 | 20.9 ms |
| `in` | 48 | 40 | **0** | 27 | 10 | 3 | 0 | 892.0 ms |
| `iso` | 86 | 40 | **0** | 33 | 5 | 2 | 0 | 1,592.2 ms |
| `mtr` | 15 | 15 | **0** | 14 | 0 | 1 | 0 | 39.3 ms |
| `out` | 65 | 40 | **0** | 31 | 7 | 2 | 0 | 1,200.0 ms |
| `ph` | 80 | 40 | **0** | 34 | 4 | 2 | 0 | 1,650.8 ms |
| `pwr` | 64 | 40 | **0** | 31 | 6 | 3 | 0 | 966.4 ms |
| `sum` | 92 | 40 | **0** | 26 | 11 | 3 | 0 | 1,952.8 ms |
| **Total** | **652** | **345** | **0 (0.0 %)** | **250 (72.5 %)** | **71 (20.6 %)** | **23 (6.7 %)** | **1 (0.3 %)** | |

Nothing routed. Not once.

Three distinct reasons, and they need separating because only one of them is a defect:

1. **`already_connected` — 250 of 345, and this is correct.** These are finished boards. The
   surface is telling the truth: every pad of the net already shares one selected-layer component.
   A caller pointing a router at a routed board gets the right answer.
2. **`obstacle_budget_exceeded` — 71 of 345, and this is the wall.** Two messages appear: *the
   same-net connectivity model exceeds the configured obstacle budget* and *the selected-layer
   obstacle count exceeds the configured obstacle budget*. The default obstacle budget is
   `max_obstacles = 256`. These boards carry 34 to 31,389 segments and 1 to 1,003 vias. The budget
   is not close.
3. **`invalid_two_pin_net` — 23 of 345.** *The selected net must resolve to exactly two pads on the
   selected layer.* The two-pin surface refusing a wider net is honest, not a defect; it bounds the
   route bundle's remaining work rather than naming a bug.

### The B-088 pattern does not hold here

[B-088](../ledgers/benchmark-ledger.md) measured 59.83 % completion on the external SimpleRouteJson
corpus with **every two-pin net refused `off_grid`**, and localised the constraint to lattice
alignment and the grid-node budget.

**Not one `off_grid` refusal appears on this corpus.** Not one `no_path`, not one
`grid_budget_exceeded`. A different constraint binds entirely: B-088 ran with the obstacle budget
raised to 2,048 on boards averaging 30 obstacles, so it reached the router; here, at the default
256, the obstacle model is exhausted before any lattice question is asked. The two results measure
two different walls, and neither generalises to the other.

**Verdict: refuses.** Filed as a gap.

## Wall clock

Nothing took minutes per call. Everything is O(board) *per request*, because every surface re-reads
and re-converts the whole board on every call — there is no snapshot reuse across requests.

| Call | Smallest board (`fdr`, 0.10 MB) | Largest board (`ch`, 9.10 MB) |
|---|---:|---:|
| `inspect_board_ir` | 20 ms | 3,551 ms |
| `run_board_drc` | 612 ms | 1,965 ms |
| `observe_board_scene`, whole board | 20 ms | 3,782 ms |
| `observe_board_scene`, 5 mm region | 20 ms | 3,686 ms |
| `preview_placement`, one subject | 24 ms | 4,025 ms (max 4,047 ms) |
| `preview_route`, one net | 21 ms | 3,849 ms (max 5,250 ms) |

The consequence is a per-*sweep* cost, not a per-call one: enumerating placement verdicts for `ch`'s
202 footprints one at a time costs about 13 minutes, and the 40-net route sweep on `ch` took 156 s
of which nearly all is repeated conversion. A bounded region does not help — the 5 mm scene costs
the same 3.7 s as the whole board, because the region filters emission and not parsing. Recorded as
an observation, not filed: no single call is unusable, and the fix (a revision-bound snapshot cache)
is a design decision rather than a defect.

## Gaps filed

Four, each with the counted evidence above:

1. **Every real board is permanently unappliable** — an assembled `gr_line` Edge.Cuts outline always
   carries a derived contour identity, and both identity gates refuse on it. 10 of 10 boards,
   including two with otherwise 100 % native identities.
2. **Whole-board Circuit Scene truncation empties whole object kinds** — 8 of 10 boards return
   `vias: []`, `zones: []` and `rules: []` while holding up to 1,003 vias.
3. **Route preview cannot model a real board at the default obstacle budget** — 0 of 345 routed,
   71 refused at `max_obstacles = 256` against boards holding up to 31,389 segments.
4. **A root-level `(group …)` refuses the whole board and names nothing** — 1 of 12 boards, with a
   message and a locator (`kicad_pcb.unsupported`) that identify neither the construct nor the
   field.

Ordering: gap 1 gates every mutation on every board and is the one to take first. Gap 3 gates the
routing product. Gaps 2 and 4 are correctness-of-report problems rather than capability ones.

## What this does not claim

- No electrical, thermal, signal-integrity, manufacturing or fabrication claim. A DRC report is
  KiCad's verdict transported, not an opinion about the board.
- No board was written, applied to, or opened in a live editor. Appliability was measured by calling
  the apply path's identity predicate, which proves the gate refuses — not that an apply would
  otherwise have succeeded.
- Route preview ran on one layer and one net at a time against the unrouted snapshot, so the
  candidates are not mutually compatible and this is **not** a whole-board completion result.
- Placement preview ran without rules, so a clean verdict means legal-as-found and says nothing
  about placement quality.
- The corpus is one designer's project family of mostly four-layer mixer boards. It is not a random
  sample of KiCad boards, and the two-board refusal rate is not an estimate of anything wider.
- Timings are one machine, one run. They establish the shape of the cost, not a performance target.
