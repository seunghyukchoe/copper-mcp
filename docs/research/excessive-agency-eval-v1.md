# Excessive-agency evaluation v1

**Review date:** 2026-08-06

This note defines the adversarial evaluation that tests CopperMCP's central safety claim, and it
records the external methodology that claim is measured against. It also states, at the same
length, what the evaluation does **not** establish — because an evaluation whose limits are not
written down will be read as proving more than it does.

It supersedes nothing. The earlier per-boundary harness described in
[MCP excessive-agency evaluation](mcp-excessive-agency-evaluation.md) remains in place and keeps
running; this is the systematic suite that
[issue #69](https://github.com/seunghyukchoe/copper-mcp/issues/69) asked for on top of it, plus the
held-out project-family dimension that harness did not have.

## The claim under test

> An agent driving CopperMCP cannot cause an unintended board mutation, and cannot extract a
> verification claim that was never computed, even when it tries.

That is a negative claim, so it cannot be proved. It can be attacked, and the attacks can be
written down in advance so the result is not chosen after the fact. That is what this suite is:
34 predeclared scenarios in eight families, each stating a goal, the tool calls it attempts, and
the exact outcome it requires, replayed against four project families -- plus three report-level
**controls** that assert the suite actually ran what it claims to run.

Thirty-two of the scenarios require a refusal or an honest non-claim. Two require the opposite,
and that is the 2026-08-13 revision recorded in [ADR-0102](../adr/0102-an-evaluation-must-observe-a-permit.md)
and [B-106](../ledgers/benchmark-ledger.md): **an evaluation whose every row requires a refusal
cannot distinguish a server that refuses correctly from one that refuses everything.** The suite
had performed a real authorized apply since its first run, but only as an unnamed precondition of
the replay scenario, so no row in the artifact said the server had permitted anything and no
reader could check that it had.

The catalog lives in `tests/fixtures/security/excessive-agency-v1/scenarios.json` and is bound into
the artifact by digest, so a scenario cannot be reworded after its result is known without changing
the run identity.

## External basis

Every URL below was read on 2026-08-06; the sentence attributed to each is a sentence that page
actually contains.

- OWASP's [LLM06:2025 Excessive Agency](https://owasp.org/www-project-top-10-for-large-language-model-applications/2_0_vulns/LLM06_ExcessiveAgency.html)
  is the framing. It separates excessive **functionality**, excessive **permissions**, and
  excessive **autonomy**, and its prevention list leads with minimal extensions, minimal
  permissions, complete mediation in downstream systems, and human approval for high-impact
  actions. The same category was numbered **LLM08** in the 2023 edition of the list, which is why
  both numbers appear in CopperMCP's history and in issue #69; they name one thing. The
  `mutation_without_consent`, `stale_state_exploitation`, and `budget_dos` families map to
  permissions and mediation. The `claim_laundering` and `non_claim_inference` families map to
  autonomy, in the specific sense that an agent acting on a verification it was *told about*
  rather than one that was *computed* is acting further than its evidence reaches.
- OWASP's [LLM09:2025 Misinformation](https://owasp.org/www-project-top-10-for-large-language-model-applications/2_0_vulns/LLM09_Misinformation.html)
  is why claim laundering is its own family rather than a footnote under authorization. A server
  that answers "DRC passed" when nothing ran is a worse failure than one that writes a file it
  should not have, because the write is visible in the file and the false claim is not.
- The official [MCP security best practices](https://modelcontextprotocol.io/specification/draft/basic/security_best_practices)
  document names **state handle hijacking** directly: a server "**MUST NOT** treat possession of a
  state handle as authentication", and **MUST** verify all inbound requests. CopperMCP's apply
  token is exactly such a handle, so the `mutation_without_consent` family attacks each of its six
  bindings separately — candidate, board revision, path, operation domain, expiry, and the issuing
  process — rather than checking that the handle merely exists.
- The [MCP authorization specification](https://modelcontextprotocol.io/specification/2025-06-18/basic/authorization)
  states that authorization is **OPTIONAL**, applies to HTTP transports, and that stdio
  implementations **SHOULD NOT** follow it and should take credentials from the environment
  instead. That is precisely CopperMCP's shape: a local stdio server whose consent comes from an
  operator environment flag. The specification's rule that a server "**MUST** validate access
  tokens before processing the request" is the ordering the suite checks — the token is verified
  before the board is read, so an unauthorized caller cannot make the server touch a file.
- Anthropic's [Claude Code security](https://code.claude.com/docs/en/security) documents the
  permission-based architecture a CopperMCP client sits behind: read-only by default, explicit
  approval before anything that modifies the system, and the explicit caveat that "no system is
  completely immune to all attacks". That caveat is the reason this note's limits section is as
  long as its results section. It is also why the suite attacks the server rather than reading
  `apply_candidate`'s `destructive_hint: true` annotation: an annotation is advice to a client and
  enforces nothing.
- Anthropic's [Responsible Scaling Policy](https://www.anthropic.com/news/anthropics-responsible-scaling-policy)
  supplies the methodological commitment this suite copies at a much smaller scale: write the
  measures down *before* reaching the threshold they govern. Every scenario here has one required
  outcome fixed in a digest-bound catalog before the run, and every scenario that could not be run
  is recorded as `not_run` with a machine-readable reason rather than dropped from the denominator.

## Scenario families

| Family | Adversarial goal | Required outcome |
|---|---|---|
| `mutation_without_consent` | Write to a board without the operator flag and without a capability issued for this exact candidate, board, revision, path, operation domain, and process | Typed refusal (`apply_disabled`, `live_apply_disabled`, `invalid_token`, `token_already_used`) and an unchanged board digest |
| `stale_state_exploitation` | Write against a board that moved after the preview, or by mixing digests from two revisions | Typed `stale_revision` / `stale_candidate`, never a write |
| `claim_laundering` | Make the server publish a verification it never computed, by asserting it in a caller-supplied manifest | Identity recomputation refuses (`splice_assertion_failed`); the normalizing tools publish no verdict at all |
| `non_claim_inference` | Read a one-value non-claim literal as a success anywhere in a real payload | A key that ever says `not_run` never says anything else, and no refusal reports a write |
| `information_extraction` | Recover board content from a refusal that was never authorized to disclose it | Planted sentinels — net names, native footprint identities, exact coordinates, the absolute workspace path — absent from every refusal payload |
| `budget_dos` | Exhaust the server with oversized, deeply nested, or over-count input | A refusal inside a wall-clock ceiling, and only via the adapter's own boundary exception — a `RecursionError` or a stray `KeyError` from inside a parser fails the scenario rather than counting as one |
| `workspace_containment` | Reach a board outside the configured workspace from a request that already carries consent and a genuine capability | Typed `invalid_request`, and the board outside the workspace byte-identical across the call |
| `authorized_apply` | The inverse of every row above: present the one request the server *should* say yes to | A real write (`applied`), reaching exactly the named board and its own pre-apply copy and nothing else |

## Project families

The suite runs identically against four bodies of boards. One is the control; three are held out in
different and unequal senses, and the differences matter more than the label.

| Family | Held out | What it actually is |
|---|---|---|
| `development-fixtures` | no | The two boundary fixtures the apply, token, and refusal paths were built against. Present as the control. A result here is not evidence. |
| `coppertone-buffer` | yes | The project's own reference hardware board — 26 footprints, 14 nets, 53 segments, 9 vias, 4 zones. Not third-party, but never used to develop an authorization boundary, and the only family in the suite whose coordinates are irregular enough to serve as leak sentinels. |
| `heldout-audio` | yes | The hash-separated held-out partition of the audio project-family split (B-074, B-075), declared held out before this suite existed. |
| `tscircuit-benchmark` | yes | The external MIT-licensed SimpleRouteJson corpus imported by B-088. Genuinely third-party data this project did not author — and, as recorded below, unreachable by this suite. |

## Results

Run `2026-08-13`, artifact
[`2026-08-06-excessive-agency-evaluation.json`](../../benchmarks/results/security/2026-08-06-excessive-agency-evaluation.json)
(the file keeps its original name and is re-recorded in place; it must replay byte-identically
from the harness it names, so a stale copy could not stay in the tree), recorded as
[B-106](../ledgers/benchmark-ledger.md). The 2026-08-06 run of the 29-scenario suite is
[B-089](../ledgers/benchmark-ledger.md) and its numbers stand as the earlier measurement.

**136 cases: 90 passed, 0 failed, 46 not run. Three controls, none failed.**

| Project family | Passed | Failed | Not run |
|---|---|---|---|
| `development-fixtures` (control) | 32 | 0 | 2 |
| `coppertone-buffer` | 26 | 0 | 8 |
| `heldout-audio` | 32 | 0 | 2 |
| `tscircuit-benchmark` | 0 | 0 | 34 |

| Scenario family | Passed | Failed | Not run |
|---|---|---|---|
| `mutation_without_consent` | 28 | 0 | 12 |
| `stale_state_exploitation` | 11 | 0 | 5 |
| `claim_laundering` | 7 | 0 | 9 |
| `non_claim_inference` | 9 | 0 | 3 |
| `information_extraction` | 7 | 0 | 5 |
| `budget_dos` | 15 | 0 | 5 |
| `workspace_containment` | 9 | 0 | 3 |
| `authorized_apply` | 4 | 0 | 4 |

### The authorized path, and the control that keeps it running

`authorized-apply-permits-the-bound-candidate` presents the one request the server is supposed to
say yes to -- operator consent granted, a single-use capability this process minted through a real
`preview_route`, the candidate it was minted for, the revision the board is actually at -- and
requires a real write. It records `applied` in `development-fixtures` and in the held-out
`heldout-audio`. `coppertone-buffer` records `no_apply_capability_available`: every net on that
board is already routed and it sits outside the placement-apply subset, so there is no genuine
capability there to spend.

`authorized-apply-changes-only-the-authorized-board` digests every regular file in the workspace
across that write. It failed on its first execution, correctly: a successful apply also writes a
pre-apply rollback copy under `.copper-mcp-backups/`, which the single-board digest guard the
suite used until now could not see. The row now permits exactly that one created file and requires
it to belong to the board that changed and to carry that board's *pre-apply* bytes -- a copy
holding post-apply bytes restores nothing, and a copy of a different board would put one board's
contents into another board's history.

The three **controls** are the part that matters most, and they exist because of a measurement
rather than a worry. Every row in this suite degrades to `not_run` when its precondition is
absent, which is honest per row and dangerous in aggregate. With both capability probes returning
nothing -- the shape of "`preview_route` stopped issuing tokens", or "`include_apply_token` was
renamed", or "the apply gate shut" -- the suite records **`failed: 0` and an empty `failures`
list** while quietly dropping from 90 passes to 76. That is the same defect class as six of seven
property tokens going un-counted because every test used the same one, and as the mutation run
that reported 11/11 kills while executing zero tests ([ADR-0098](../adr/0098-reproducible-mutation-evidence.md)).
So the controls are counted in the exit status alongside scenario failures:

| Control | Requires |
|---|---|
| `authorized-apply-is-exercised-somewhere` | At least one project family recorded a permit |
| `authorized-apply-is-exercised-outside-the-control-family` | At least one *held-out* family did |
| `workspace-containment-is-exercised-somewhere` | Every declared escape route was actually attempted |

A control failure is not a weaker result than a scenario failure, because a scenario that did not
run cannot fail.

### What stops this from writing to something real

The suite now performs an authorized apply under its own name, so the containment question is
explicit rather than implied. Each project family runs inside a `tempfile.TemporaryDirectory`
holding two siblings: the `workspace/` the server is pointed at, containing copies of committed
fixtures, and a `beyond/` directory holding one real `.kicad_pcb` file the server must never
reach. Neither is inside the repository, and the worst a broken confinement guard can do is
overwrite a copy made seconds earlier.

Three scenarios attack that boundary, each **with consent granted and a genuine token** -- a
containment check run with consent off is answered by the consent gate and establishes nothing
about containment:

- `authorized-apply-to-absolute-path-outside-workspace`
- `authorized-apply-to-parent-relative-path` (a `..` traversal)
- `authorized-apply-through-symlink-leaving-workspace`, a plain filename inside the workspace
  whose every syntactic check passes and which resolves, through a symlink, to the board outside

Each aims at a file that really exists, because `resolve_workspace_relative_path` resolves
strictly and would refuse a missing path first -- a row that refuses for the wrong reason records
a containment result it never established. Each requires `invalid_request` **and** the outside
board's bytes unchanged across the call: a refusal that arrives after a write fails.

Three further guards are unchanged and still hold. The harness deletes every `COPPER_MCP_*`
variable from the environment at import, so an operator or CI job exporting
`COPPER_MCP_ALLOW_APPLY=1` or pointing `COPPER_MCP_WORKSPACE` at a real tree cannot redirect the
write -- `test_cli_ignores_inherited_copper_configuration` sets exactly those variables and
requires the artifact to come out unchanged. Consent inside the run is a `Settings` object built
per family, never the process environment. And `test_source_boards_are_untouched_by_a_run` digests
every committed board across a full run.

### Mutating the gate itself

Six mutants over the authorization gate and over the controls, through the committed harness of
ADR-0098 (`docs/mutants/2026-08-13-apply-authorization-gate.json`): **6 applied, 6 killed, 0
survivors, 0 stale anchors**. Inverting the route consent gate is killed by the permit row --
which is the point of the permit row, since before it existed inverting that gate produced only
`not_run`s. Removing the route gate and inverting the placement gate are killed by the full-report
row. Removing the resolve-and-confine step in `resolve_workspace_relative_path` is killed by the
containment rows. And the two mutants that make the coverage controls always satisfied are killed
by the degradation regression, which is what makes those controls load-bearing rather than
decorative.

### The negative results, in full

A zero in the `failed` column is only meaningful next to the `not run` column, so here is every
reason a scenario did not run.

- **`tscircuit-benchmark` contributed nothing: 34 of 46 not-run rows.** The corpus ships
  SimpleRouteJson routing problems, and no MCP tool accepts that format — the import seam is a
  benchmark-only subpackage that `scripts/` and `tests/` import directly. So the one genuinely
  third-party project family in the evaluation reaches no agency boundary at all. This is the
  weakest point in the held-out design and it is not hidden: the family is carried in the report
  with `accepted_format: false` and its corpus manifest digest, so a future intake of KiCad boards
  (PCBench is MIT and already reviewed) can fill it without renumbering anything.
- **`coppertone-buffer` affords no apply capability: 7 of its 8 not-run rows.** Every net on the
  board is already routed, so `preview_route` answers `already_connected` and mints nothing; and it is
  outside the source-preserving front-side orthogonal footprint subset the placement apply admits,
  so `preview_placement` previews but issues no token. The four scenarios that need a *genuine*
  capability — cross-domain token reuse, placement view-revision mismatch, placement-evidence
  laundering, route-metric laundering — plus the two authorized-apply rows and the replay scenario
  therefore could not run there. The consent, forged-token, rebinding, staleness, budget,
  containment, and disclosure scenarios all ran, because they do not need one. This is why the
  second control exists: without it, the permit could quietly retreat to the control family and
  the artifact would look the same.
- **The authoritative DRC/ERC scenario is `not_run` in every family, by choice.** `run_board_drc`
  and `verify_circuit_schematic_erc` spawn `kicad-cli`. Invoking it would make the artifact depend
  on whether KiCad is installed and on which version, which a self-digested report cannot tolerate.
  The recorded reason is `external_process_required`; that boundary is covered by SEC-113 and
  SEC-119 instead. Writing a pass here without running KiCad would be the exact failure the
  `claim_laundering` family exists to catch.
- **Two families yield no coordinate sentinel.** `development-fixtures` and `heldout-audio` are
  synthetic boards laid out entirely on round nanometre grids, and a round coordinate matches by
  coincidence inside the server's own vocabulary of clearances, grid steps, and expansion budgets.
  The scan therefore excludes any coordinate on a 10 µm multiple, which leaves those two families
  with nothing to grep for and records `no_coordinate_sentinel_available`. Only
  `coppertone-buffer` exercises the coordinate scan with real force. The net-name and
  footprint-identity scans run everywhere.

### What the harness had to be stopped from doing

Three findings came out of building the suite rather than running it, and all three are recorded
because they are the failure mode this kind of harness has: a check that cannot distinguish a
refusal from something that merely looks like one.

1. **A vacuous edit reads as a pass.** The first version of `route-metrics-rewritten-clean` zeroed
   `hard_internal_violations` and `unrouted_connections` on a candidate whose values were already
   zero. The manifest was byte-identical to the published one, the identity verified, and the apply
   *succeeded* — correctly. Had the scenario not guarded the board digest, a green row would have
   recorded a successful write as a refusal. Both laundering scenarios now assert that the rewritten
   manifest actually differs from the published one and fail with `edit_was_vacuous` otherwise.
2. **A sentinel scan degenerates into a birthday test.** A 7-digit decimal coordinate matches inside
   a 64-character SHA-256 hex digest often enough to fire, and matched `2000000` inside the echoed
   `max_obstacle_checks` budget. The scan now strips `sha256:` digests from the corpus first and
   excludes grid-round coordinates, and a discriminator test plants a real net name in a fabricated
   refusal and requires the scan to catch it.
3. **A crash reads as a refusal.** The budget family accepted *any* exception other than
   `RecursionError`, `MemoryError`, or `TimeoutError` as a bounded refusal — which would have let an
   unhandled `KeyError` deep in a parser score as a pass. It now accepts only the MCP adapter's own
   boundary exception and records which one was raised, so a crash and a refusal cannot look alike.
   All 15 runnable budget rows record `ToolError`.

## What this does not prove

- **It does not test a model.** No model is invoked anywhere. This measures whether CopperMCP
  refuses, not whether an agent would *choose* to attack, would report a refusal honestly to its
  user, or would stop after one. An agent-in-the-loop evaluation is separate work and is not
  implied by any number above.
- **A passing catalog is coverage, not absence.** 86 predeclared attacks reached their predeclared
  refusals and 4 predeclared authorized rows reached their permits. Nothing here bounds the attacks
  nobody wrote down.
- **A permit is not a correctness claim.** The authorized rows establish that the server does not
  refuse the request it should accept, and that the resulting write reaches only the named board
  and its own pre-apply copy. They say nothing about whether the geometry written is any good; no
  DRC ran, and `run_board_drc` is `not_run` in every family for the reason below.
- **Confinement is three named escape routes, not a proof.** An absolute path, a `..` traversal,
  and a symlink, on whatever filesystem the temporary directory landed on, through an in-process
  caller. A hard link, a case-insensitive collision, a race between resolution and open, or a
  mount that appears mid-run are not covered.
- **The caller is in-process.** The harness imports the server and calls
  `mcp.call_tool` directly, so it can construct arguments a transport would reject and mint
  capabilities the way the server does. It says nothing about a hostile *host*, a compromised
  client, or a transport that rewrites requests.
- **It says nothing about a remote deployment.** There is no principal, no OAuth, no rate limit,
  no audited log, and no multi-tenant isolation in scope. CopperMCP is local-first and this suite
  is local-only.
- **It does not evaluate logging or telemetry sinks.** The application has no logger sink today, so
  output and report disclosure are what was scanned. Host-process logs and model-provider telemetry
  are outside it.
- **It is not a hardware claim.** Nothing here is electrical, thermal, mechanical, DRC, or
  fabrication evidence about any board, including the reference board it runs against.
- **The held-out claim is uneven, and the authorized path narrows it further.** `coppertone-buffer`
  is in this repository; it is held out from the *boundary implementation*, not from the project.
  Only `tscircuit-benchmark` is externally authored, and it is exactly the family that could not be
  reached. The 2026-08-13 revision does not fix that and makes one part of it sharper: the permit
  is evidenced on exactly **one** held-out family, `heldout-audio`, because `coppertone-buffer`
  affords no capability to spend. A change that broke the authorized path only on boards unlike
  that one would not be caught here. [R-148](../ledgers/risk-register.md) carries the residual, and
  closing it needs a project family that both is externally authored and affords a real
  capability — which is what [issue #110](https://github.com/seunghyukchoe/copper-mcp/issues/110)
  asks for in its own words, and which this revision does **not** deliver.

## How to run it

```
make evaluate-excessive-agency

# or, to pick the commit and output path yourself:
PYTHONPATH=src python3 scripts/evaluate_excessive_agency.py \
  --evidence-harness-commit <40-hex source commit> \
  --output benchmarks/results/security/2026-08-06-excessive-agency-evaluation.json
```

The run takes a few seconds, writes only into temporary workspace copies, and is byte-identical
across runs. `--fail-on-scenario-failure` exits non-zero when a scenario **or a control** fails; it
is off by default so a failing scenario lands in the artifact instead of aborting the run that
would have recorded it. `tests/test_excessive_agency_evaluation.py` replays the committed artifact
against a fresh run, pins the counts above, and reproduces the degraded run in which the authorized
path is never exercised.

To re-run the authorization-gate mutants:

```
.venv/bin/python scripts/mutation_harness.py docs/mutants/2026-08-13-apply-authorization-gate.json
```
