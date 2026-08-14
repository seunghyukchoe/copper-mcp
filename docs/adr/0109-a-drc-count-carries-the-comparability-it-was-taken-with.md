# ADR-0109: A published DRC count carries the comparability it was taken with

- Status: Accepted
- Date: 2026-08-14
- Owners: `@seunghyukchoe`
- Related: [D-201](../ledgers/decision-ledger.md), [R-154](../ledgers/risk-register.md),
  [B-111](../ledgers/benchmark-ledger.md),
  [issue #170](https://github.com/seunghyukchoe/copper-mcp/issues/170),
  [ADR-0004](0004-authoritative-kicad-drc.md),
  [ADR-0095](0095-copper-text-has-no-derivable-envelope.md),
  [ADR-0098](0098-reproducible-mutation-evidence.md),
  [`docs/mutants/2026-08-14-drc-comparability.json`](../mutants/2026-08-14-drc-comparability.json),
  the post-0.8.0 audit §2.2 and plan items P4.1 / P4.1a

## Context

`B-107` ran `scripts/benchmark_real_board_capability.py` twice at the **same commit** over
**byte-identical** board files. Nine boards' records differed, and every one of them differed only
in the `drc` section: `error_count` moved 936 → 941 on one board, `hole_clearance` moved 201 → 202,
and a whole violation type (`tracks_crossing`, 4) appeared in one run and not the other. No
conversion, route, placement or scene verdict moved.

`B-108` corroborates the mechanism independently while calibrating a different budget: the counts
**saturate near per-rule caps** — 199/200/201, 499/500/502 — and which rules fill the caps varies
run to run. It also sizes the problem: baseline-against-baseline disagreement reaches an absolute
difference of **32 against 109 across a real change**, a factor of 3.4. The noise does not swamp a
real signal, but it is the **same order of magnitude** as one, which is exactly the regime in which
a differential quoting a single invocation is unsafe.

ADR-0004 delegates DRC authority to KiCad, and this project quotes DRC counts as evidence in ledger
rows and benchmark artifacts. So a `drc` section is not a function of the inputs its artifact
records, and a `run_id` computed over that artifact promises more than it delivers. The audit
verified that **no risk row and no benchmark correction row existed for this**: the exposure was
prose only.

**KiCad's own saturation source has not been read.** `B-108`'s caps are observed behaviour and the
mechanism behind them is inferred. That is a `not_run`, carried forward rather than resolved here.

## Decision

**No numeric tolerance.** A tolerance would be a constant fitted to the oracle, and ADR-0095 already
refused that shape of argument in another domain: an envelope is *derived* and then oracle-checked,
and a constant fitted to the oracle inverts the direction of the derivation. A tolerance would also
have to be re-fitted whenever KiCad's caps moved, which is the property that made the counts
unusable in the first place.

Instead, **one required one-value literal, and one prohibition.**

Every DRC section a benchmark artifact publishes carries `drc_comparability`, exactly one of:

| Literal | Means |
|---|---|
| `single_invocation` | The counts are one invocation's answer. Publishable as an observation. |
| `repeated_agreement` | N ≥ 2 invocations, taken **at one commit over byte-identical inputs**, agreed **exactly** on every published field. |
| `repeated_disagreement` | N ≥ 2 invocations did not agree. The counts stay published; what is withdrawn is the claim that they describe the board. |

**The prohibition: a before/after differential may not cite a DRC count whose comparability is not
`repeated_agreement`.**

Both halves are mechanical.

* `src/copper_mcp/benchmarks/drc_comparability.py` derives the literal from the observations a
  runner took, and `drc_differential` is the sanctioned way to compute a delta — it refuses unless
  **both** sides are `repeated_agreement`.
* `require_qualified` is the **emission gate**. It walks the whole report rather than the section
  a runner remembered to pass, and four DRC-recording runners call it before writing:
  `benchmark_real_board_capability.py`, `benchmark_layered_kicad_drc.py`,
  `benchmark_public_placement_drc.py` and `benchmark_placement_drc.py`.
* **The fifth is deferred, with the reason recorded rather than the runner quietly skipped.**
  `benchmark_route_bundle.py` cannot be wired yet: its committed artifact records `script_sha256`
  **of the runner itself**, so editing the runner invalidates a published binding between the
  artifact and the source that produced it — and that artifact is regenerated only in the release
  commit that bumps the version, because its DRC evidence is version-bound by construction. Adding
  the emission call now would trade a real binding for a gate the artifact sweep already provides
  for the same file. It sits in `DEFERRED_RUNNERS` with the reason and the event that lifts it,
  and a test asserts the pin that is the reason still exists.
* `scripts/check_drc_comparability.py` is the **repository gate**, in `make lint` and in CI. It
  sweeps every committed artifact, fails on an unqualified DRC section, fails on a delta published
  beside a non-`repeated_agreement` literal, and requires each registered runner to import the
  module.

**An aggregate takes the weakest literal of its inputs.** A total summing one `repeated_agreement`
board and one `repeated_disagreement` board is a number that moves between runs, and calling it
otherwise would launder the disagreement through an addition.

**The `drc-summary` schema is not touched, and the boundary is the point.** That schema is the
**live payload** a caller receives from `run_board_drc` — one invocation by construction, where a
literal would be a constant field. This policy governs the **benchmark projection**: the artifact
that quotes a count *as evidence*, where another number could have been quoted instead and the
difference would matter. A test pins the schema against the literal so that a later slice cannot
widen it on this policy's authority, which would be the accepted-set drift ADR-0105 exists to
prevent.

**Existing counts are qualified, not retracted**, following the `B-102` pattern. `B-111` is the
correction row; `PRE_POLICY` in the checker is keyed `(artifact, section path)`, each entry naming
it, and an entry matching nothing fails the run. Rewriting a committed artifact to insert the field
would change its `run_id`, which is a digest of its own content, and a re-derived `run_id` over
numbers nobody re-measured is a worse record than an honest exemption.

## Consequences

**A finding this landed on.** A runner whose artifact pins the runner's own bytes cannot be
edited without invalidating that artifact, so an emission gate and a source-digest binding are in
tension by construction. That is not a defect in either; it is a scheduling constraint, and the
right response is to name it and the event that resolves it rather than to weaken one of them.
`R-154` does not need to carry it, because half (1) of the checker covers the artifact regardless.

**What improves.** Every count this project publishes from here on says what it is. The corpus
runner gains `--drc-repetitions`, so `repeated_agreement` is reachable rather than aspirational;
it defaults to `1` because KiCad dominates that runner's wall clock, and at the default every
section is honestly `single_invocation` and no differential may cite it. Two runners
(`benchmark_layered_kicad_drc.py`, `benchmark_public_placement_drc.py`) already repeated their
invocations and compared them, and now publish what that comparison found instead of keeping it
private.

**What becomes harder.** A before/after DRC claim now costs at least two KiCad invocations per side.
That is the honest price of the claim, and `B-105`/`B-107` are the record that the claims made so
far did not pay it.

**What this does not establish.** That N observations were taken at one commit over identical bytes
is the runner's assertion; the module cannot check it and says so. Nothing here reads prose, so a
ledger row quoting a bare count is outside the gate's reach — `B-111` is what qualifies the ones
already written, and it is reviewer-owned from here.

**What is not claimed.** *That KiCad is wrong.* *That any recorded count was false when it was
taken.* The finding is that the quantity is not stable under repetition. Saturation is
**corroborated** by `B-108` and **unestablished**: KiCad's source has not been read, and the
mechanism behind the caps is inferred from their values.

**What has not been run.** No N-run characterisation of the distribution exists. That is issue
#170's step 1 and plan item **P4.1a**, and it stays open: the literal ships on what `B-107` and
`B-108` already found, and the characterisation is what would *size* the mitigation rather than
what justifies it. `R-154` carries the open risk — `R-146` covers corpus decay, which is a
different hazard and would mask this one.

**Evidence.** 18 committed mutants,
[`docs/mutants/2026-08-14-drc-comparability.json`](../mutants/2026-08-14-drc-comparability.json),
covering the literal omitted, a `single_invocation` count admitted into a differential, the
prohibition neutered, the count that moved excluded from the comparison, an aggregate taking the
strongest rather than the weakest, the sweep losing its list descent, the exemption list becoming a
suppression mechanism, a runner dropping the gate, and the live schema being widened — **18
mutants, 18 killed, 0 survivors, 0 `not_run`**, run through `scripts/mutation_harness.py` per
ADR-0098; Python 3.12.13 on macOS-26.5.2-arm64, `baseline_returncode: 0`, spec
`sha256:2c6d18f8684e9434c27c1b5942a1365f06211b101d66a797fd8ccfa2da5e24a1`. Read the mapping and
not the count: **DC04 and DC08 are the pair that matters.** DC04 widens the admissible set to
include `single_invocation`, which is the prohibition failing open; DC08 adds `error_count` to
the keys excluded from the agreement comparison, which is the *literal* failing open — a section
claiming `repeated_agreement` while the number it publishes moved. A gate that survived either
would be decorative in exactly the way this policy exists to prevent.

## Alternatives considered

**A declared numeric tolerance.** Refused above: it is an oracle-fitted constant, which ADR-0095
already ruled out, and it would need re-fitting whenever KiCad's caps moved.

**Record the raw report digest and stop publishing counts.** Considered — it is issue #170's third
option. Refused because it discards information a reader wants and can use honestly: a
`single_invocation` count is a real observation of a real board, and the defect is the *comparison*
rather than the observation. Withdrawing the number would also make every existing row
unreconstructable rather than merely qualified.

**Stop recording DRC counts in comparative artifacts entirely.** Refused for the same reason, and
because it makes the rule invisible: a missing field says nothing, where a `single_invocation`
literal says exactly what happened. This is `R-148`'s lesson — a typed `not_run` is a result and a
missing row is not.

**Add the literal to `drc-summary` too, for uniformity.** Refused: it would be constant in every
live response, and widening a published schema for a field that carries no information is precisely
the accepted-set drift ADR-0105 froze `0.2.0` over. `DC18` is the mutant that keeps it out.

**Rewrite the committed artifacts to carry `single_invocation`.** Refused: their `run_id` is a
digest of their own content, so the edit either invalidates the recorded identity or produces a
re-derived one over numbers nobody re-measured. `B-111` and a keyed exemption say the same thing
without falsifying a record.
