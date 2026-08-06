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
29 predeclared scenarios in six families, each stating an adversarial goal, the tool calls it
attempts, and the exact refusal or honest non-claim it requires, replayed against four project
families.

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

Run `2026-08-06`, artifact
[`2026-08-06-excessive-agency-evaluation.json`](../../benchmarks/results/security/2026-08-06-excessive-agency-evaluation.json),
recorded as [B-089](../ledgers/benchmark-ledger.md).

**116 cases: 77 passed, 0 failed, 39 not run.**

| Project family | Passed | Failed | Not run |
|---|---|---|---|
| `development-fixtures` (control) | 27 | 0 | 2 |
| `coppertone-buffer` | 23 | 0 | 6 |
| `heldout-audio` | 27 | 0 | 2 |
| `tscircuit-benchmark` | 0 | 0 | 29 |

| Scenario family | Passed | Failed | Not run |
|---|---|---|---|
| `mutation_without_consent` | 28 | 0 | 12 |
| `stale_state_exploitation` | 11 | 0 | 5 |
| `claim_laundering` | 7 | 0 | 9 |
| `non_claim_inference` | 9 | 0 | 3 |
| `information_extraction` | 7 | 0 | 5 |
| `budget_dos` | 15 | 0 | 5 |

### The negative results, in full

A zero in the `failed` column is only meaningful next to the `not run` column, so here is every
reason a scenario did not run.

- **`tscircuit-benchmark` contributed nothing: 29 of 39 not-run rows.** The corpus ships
  SimpleRouteJson routing problems, and no MCP tool accepts that format — the import seam is a
  benchmark-only subpackage that `scripts/` and `tests/` import directly. So the one genuinely
  third-party project family in the evaluation reaches no agency boundary at all. This is the
  weakest point in the held-out design and it is not hidden: the family is carried in the report
  with `accepted_format: false` and its corpus manifest digest, so a future intake of KiCad boards
  (PCBench is MIT and already reviewed) can fill it without renumbering anything.
- **`coppertone-buffer` affords no apply capability: 5 of its 6 not-run rows.** Every net on the
  board is already routed, so `preview_route` answers `already_connected` and mints nothing; and it is
  outside the source-preserving front-side orthogonal footprint subset the placement apply admits,
  so `preview_placement` previews but issues no token. The four scenarios that need a *genuine*
  capability — cross-domain token reuse, placement view-revision mismatch, placement-evidence
  laundering, route-metric laundering — and the replay scenario therefore could not run there. The
  consent, forged-token, rebinding, staleness, budget, and disclosure scenarios all ran, because
  they do not need one.
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

Two findings came out of building the suite rather than running it, and both are recorded because
they are the failure mode this kind of harness has.

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
- **A passing catalog is coverage, not absence.** 77 predeclared attacks reached 77 predeclared
  refusals. Nothing here bounds the attacks nobody wrote down.
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
- **The held-out claim is uneven.** `coppertone-buffer` is in this repository; it is held out from
  the *boundary implementation*, not from the project. Only `tscircuit-benchmark` is externally
  authored, and it is exactly the family that could not be reached.

## How to run it

```
make evaluate-excessive-agency

# or, to pick the commit and output path yourself:
PYTHONPATH=src python3 scripts/evaluate_excessive_agency.py \
  --evidence-harness-commit <40-hex source commit> \
  --output benchmarks/results/security/2026-08-06-excessive-agency-evaluation.json
```

The run takes about a second, writes only into temporary workspace copies, and is byte-identical
across runs. `--fail-on-scenario-failure` exits non-zero when a scenario fails; it is off by
default so a failing scenario lands in the artifact instead of aborting the run that would have
recorded it. `tests/test_excessive_agency_evaluation.py` replays the committed artifact against a
fresh run and pins the counts above.
