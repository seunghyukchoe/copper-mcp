# Development Guide

## Supported environment

- Python 3.11–3.13.
- Git 2.40 or newer recommended.
- KiCad is optional for unit tests. When `kicad-cli` is available, the authoritative DRC integration
  test runs against a temporary copy of the synthetic fixture.

Create a virtual environment and install all checks:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e ".[dev,security]"
pre-commit install --install-hooks
```

## Validation levels

| Command | Purpose |
|---|---|
| `make test` | Pytest unit and contract suite. |
| `make lint` | Ruff plus version and ledger structure. |
| `make typecheck` | Strict static typing. |
| `make security` | Secret and dependency audit. |
| `make build` | Wheel and source distribution. |
| `make check` | Full pre-release gate. |

Tests should not require network access or proprietary boards. GPU and KiCad integration tests must
be separately marked and have deterministic CPU or fixture-based coverage where practical.

On macOS, CopperMCP also checks the standard KiCad application path. Elsewhere, put `kicad-cli` on
`PATH` or set `COPPER_MCP_KICAD_CLI`. Do not add CLI argument passthrough: the DRC adapter's fixed
argument vector is a security boundary. The current bounded-execution helper requires a POSIX host;
unsupported platforms fail closed before starting KiCad.

DRC runs against a private mirror of the board's workspace-relative context. Keep project-local
symbol/footprint libraries and library tables below `COPPER_MCP_WORKSPACE`; external environment or
global-library dependencies may make DRC results host-dependent and must be declared in benchmark
provenance. Tune `COPPER_MCP_MAX_DRC_CONTEXT_BYTES` only after reviewing the workspace scope.
File-count and discovery-time ceilings are separately configurable through
`COPPER_MCP_MAX_DRC_CONTEXT_FILES` and `COPPER_MCP_MAX_DRC_CONTEXT_SCAN_SECONDS`.

The internal `run_route_candidate_drc()` service accepts only a typed `RouteCandidate` and
`KiCadConstraintProfile`. It captures the original context, reconstructs Board IR from captured
bytes, invokes the replay-verified disposable serializer, and runs the patched in-memory context
through the same fixed KiCad DRC path. Its evidence binds candidate, Board IR, raw source, patched
board, patched context, and nested summary revisions. Tests for this boundary must prove negative
violation evidence, stale board/rule/library rejection, context budgets, temporary cleanup, deep
evidence immutability, and source inode, mtime, byte, and workspace-entry preservation where real
KiCad is available.

Its only public caller is `route_preview.preview_route()`, reached through the `preview_route` MCP
tool and `copper-mcp preview-route`, and only when the caller sets `include_drc`.
`COPPER_MCP_MAX_ROUTE_PREVIEW_SECONDS` bounds preview wall-clock time above the router's integer
budgets. Preview must remain free of file writes, durable candidates, and jobs; a candidate-file
export, persistence, or apply action still needs a separate public contract and security review.

`run_layered_route_candidate_drc()` follows the same boundary for the narrow two-signal-layer
proposal. It requires the original `LayeredRouteRequest`, replays the candidate before serialization,
and binds full-stack through-vias to the private KiCad DRC context. The layered evidence remains an
internal, read-only gate; it is not exposed through MCP or CLI and cannot issue an apply token.

## Public request boundary

Every public service that accepts a JSON-shaped request parses it through `request_boundary.py`.
Add or tighten a rule there rather than in a service, so field, type, range, boolean, and character
handling cannot drift between `inspect_board_ir`, `preview_route`, and whatever comes next. Services
keep their own `RequestError` subclass and translate at their own parse entry point, so callers can
still discriminate. Routing and clearance constraints are always caller-supplied typed values; a
board file must never be able to supply its own. Add a rejection test with every new field.

`board_ir_service.summarize_board_ir()` is the read-only structural surface. It must keep returning
counts, digests, units, and standard KiCad layer names only — never coordinates, net names, pad or
net identities, UUIDs, or source bytes — and its disclosure regression test must keep asserting
that.

## Board IR development

Board IR `0.1.0` is a strict public contract. Start with
[`docs/architecture/board-ir.md`](architecture/board-ir.md),
[`ADR-0005`](adr/0005-canonical-board-ir.md), and the
[`0.1.0` JSON Schema](../schemas/board-ir/0.1.0.schema.json).

The pure domain API is exported by `copper_mcp.board_ir`. Use `make_content` and `make_snapshot` for
programmatic construction, `encode_snapshot` for byte-stable JSON, and `decode_snapshot_json` for
untrusted bytes. Do not hand-build digest fields: construction computes the semantic constraint
digest, and decoding verifies both that digest and the snapshot digest.

The current KiCad entry point is `parse_kicad_bytes(source, profile, limits)`. It accepts source
bytes and a separate typed `KiCadConstraintProfile`, then returns a fail-closed `ConversionResult`.
It is intentionally not a filesystem writer or a route/apply service. A missing snapshot or error
diagnostic must stop downstream work; never continue with a partial board.

The v0.1 adapter is pinned to KiCad PCB format `20260206`. Treat every newly accepted S-expression
head, positional atom, graphics layer, zone option, or via treatment as a semantic change requiring
an explicit allowlist decision and a fail-closed regression. Cached `filled_polygon` geometry is not
authoritative Board IR content. Preserve quoted-versus-bare atom meaning, reject ambiguous native
identities, and keep diagnostic fields independent of source-controlled names. JSON input is budgeted
lexically before DOM construction. JSON Schema ceilings and operational `ParseLimits` are separate:
third-party schema-valid input can still be rejected by a local security budget, while public
CopperMCP writers must remain readable with the default limits.

When extending the model or adapter:

1. Decide whether canonical meaning changes. If it does, follow the versioning process in ADR-0005
   rather than changing `0.1.0` in place.
2. Preserve exact integer conversion and reject geometry that cannot be represented without an
   explicit, reviewed rule.
3. Add valid and invalid fixtures for the construct, including budget, graphics-layer,
   positional-atom, and malformed-input cases.
4. Add deterministic encode/decode, digest, geometry, and fail-closed adapter tests.
5. Update the accepted/rejected support matrix, decision/risk ledgers when applicable, and changelog.

Tests must use synthetic or redistributable fixtures. Private boards, raw diagnostic content, and
source bytes must not be copied into logs, snapshots, or MCP responses.

Run the instrumented CopperTone conversion benchmark from the repository root:

```bash
PYTHONPATH=src python scripts/benchmark_board_ir.py --iterations 7 --warmups 2
```

The JSON report records the exact commit and dirty state, input and snapshot digests, object counts,
structural limits, timing samples, and incremental peak memory while `tracemalloc` is enabled. This
measures KiCad-to-Board-IR conversion only; it is not an autorouting performance result. Check in a
result only from a clean tree and append, rather than replacing, benchmark evidence.

Run the synthetic two-pin optimality comparison from the repository root:

```bash
make PYTHON=.venv/bin/python benchmark-routing
```

This invokes the production A* candidate backend and a benchmark-only zero-heuristic Dijkstra
oracle over the same bounded integer state graph. The report retains the straight, detour,
exact-clearance, and expected-no-path fixtures; checks completion and exact cost agreement on every
iteration; and records deterministic counters plus instrumented runtime and incremental peak memory.
It does not invoke KiCad, perform authoritative DRC, establish production throughput, or compare
CopperMCP with another router. Generate recorded evidence to a path outside the repository so the
embedded Git dirty-state check remains meaningful, then append the reviewed result and ledger entry.

Run the external-corpus routing benchmark from the repository root:

```bash
make PYTHON=.venv/bin/python benchmark-external-corpus
```

This imports the committed MIT-licensed SimpleRouteJson corpus through the benchmark-only seam in
`copper_mcp.benchmarks` and routes every imported net with the existing single-layer router. It is
offline: it reads only files already in the tree and verifies each against
`benchmarks/corpora/tscircuit-benchmark/manifest.json` before importing it. The report records the
outcome of *every* attempted net under its exact `RouteFailureCode`, so the refusal breakdown is
part of the result rather than an error path, and a baseline that is not installed is recorded as
`not_run` rather than estimated. It does not invoke KiCad, apply copper, or compare CopperMCP with
another router. See [B-088](ledgers/benchmark-ledger.md) and the
[research note](research/open-baseline-benchmarks-v1.md) for the licensing determination and the
limits on what a number from this corpus can claim.

Run the excessive-agency evaluation from the repository root:

```bash
make PYTHON=.venv/bin/python evaluate-excessive-agency
```

This replays 29 predeclared adversarial scenarios — mutation without consent, stale-state
exploitation, claim laundering, non-claim inference, information extraction, and budget exhaustion
— through the real MCP adapter against four project families, three of them held out from the
boundary implementation. It takes about a second, is offline, copies every board into a temporary
workspace, and never touches the source tree. A scenario that cannot run is recorded as `not_run`
with a reason and stays in the denominator; `--fail-on-scenario-failure` (which the make target
passes) exits non-zero on a failure, and dropping it records the failure in the artifact instead.
The same suite runs under pytest in `tests/test_excessive_agency_evaluation.py`, which also replays
the committed artifact. See [SEC-121](ledgers/security-ledger.md),
[B-089](ledgers/benchmark-ledger.md), and the
[research note](research/excessive-agency-eval-v1.md) — particularly its list of what a passing run
does **not** prove.

### Adding an external corpus

1. **Check the licence first**, before any file is committed, and record the determination with the
   URLs it came from in a dated research note.
2. If redistribution is permitted, commit the upstream `LICENSE`, an `ATTRIBUTION.md`, a
   `manifest.json` with a SHA-256 for every upstream file, and a subset chosen by a rule fixed in
   advance. If it is not, commit the manifest and a fetch script and nothing else.
3. Record the corpus's provenance limits — whether the boards are human-designed, and whether any
   router was in the loop when they were built — next to the data.
4. Route it through Board IR and the existing typed refusals. An import path that special-cases the
   corpus is measuring the harness, not the router.

## Adding a schema conformance fixture

`schemas/` holds the published JSON Schema files for CopperMCP's wire-visible payloads. A schema
file can drift from the Python model that is supposed to satisfy it — a field renamed on one side,
not the other — without any test noticing, because the model's own boundary tests validate against
its own runtime rules, not against the schema file a third party would actually load. Direct schema
conformance tests close that gap by loading the schema itself and checking fixtures against it.

That gap was real, not theoretical. `schemas/drc-summary.schema.json` set
`"additionalProperties": false` and never declared the `clean` field that `DrcSummary.to_dict()`
has always emitted, so CopperMCP's own published DRC payload failed CopperMCP's own published
schema. See [D-180](ledgers/decision-ledger.md) for why the schema was completed rather than the
field dropped, and [R-137](ledgers/risk-register.md) for the residual risk.

`tests/test_schema_conformance.py` is the single place that records how *every* file under
`schemas/` is covered. Its `_SCHEMA_COVERAGE` mapping names, for each schema, the exact test
function that proves it accepts a real payload, and how strong that proof is:

- `emitted_payload` — a test builds the payload through the production code path and validates it
  against the schema file. This is the claim to aim for.
- `committed_artifact` — the published artifact is a file in this repository, and a test validates
  those exact committed bytes.
- `legacy_no_emitter` — the schema is kept for documents an earlier release produced and nothing
  emits it any more, so the strongest available check is the committed golden document. This is a
  one-value literal for a non-claim, not a softer `emitted_payload`.

Two guards keep the map honest: `test_every_published_schema_has_a_named_proof` fails when a file
appears under `schemas/` with no entry, and `test_every_recorded_proof_still_exists` fails when an
entry names a test that has been renamed or deleted.

To add a fixture for a schema (new or existing):

1. Put fixtures under `tests/fixtures/schema-conformance/<schema-name>/`: one `valid.json`, plus one
   `invalid-<condition>.json` per malformed condition. Name the invalid fixture after the condition
   it demonstrates (`invalid-missing-required-field.json`, `invalid-malformed-sha256-<field>.json`,
   `invalid-negative-count.json`, `invalid-unexpected-additional-property.json`,
   `invalid-wrong-schema-version.json`) so a reader never has to open the file to know what it
   tests. Keep fixtures minimal; do not paste a real board, job, or candidate wholesale.
2. Make each invalid fixture differ from `valid.json` in **exactly one** way, then record the errors
   it must produce in `_EXPECTED_REJECTIONS` as `(keyword, field)` pairs. Asserting only "some
   error" is too weak — a fixture that drifts out of date fails for a new, unrelated reason and
   still looks like it is working.
3. Where the schema has a Python model with a `to_dict()`, build the valid payload from a real
   instance, round-trip it through `json.dumps`/`json.loads` (a dataclass may hold a `tuple` where
   the wire format has a JSON array), and assert the committed fixture equals it. A fixture the
   code cannot produce proves nothing about the code. Register the builder in `_EMITTERS` so
   `_field_parity` compares the schema's declared property names against the emitted key set — that
   comparison alone would have caught the `clean` defect.
4. Add the schema's relative path to `_SCHEMA_COVERAGE`, naming the test function that carries the
   proof and its `kind`, even when that test lives in a different module.
5. If a fixture reveals that the real payload disagrees with the schema, **stop and decide the fix
   on its merits** rather than editing whichever side is easier. Ask which side is already
   published (an MCP contract in `src/copper_mcp/mcp_contracts.py` usually is), whether the payload
   is content-addressed or version-pinned, and whether `tests/test_golden_identities.py` would
   move. Record the decision in the decision ledger and the changelog. A schema is a public
   contract; changing one goes through the same review as any other (see "Adding a public contract"
   below).

The validator is `jsonschema.Draft202012Validator`, already a runtime dependency and version-bounded
in `pyproject.toml` — adding conformance coverage needs no new dependency. Call
`Draft202012Validator.check_schema` on a schema before using it, so a malformed schema fails as a
malformed schema rather than as a mysteriously permissive one.

## Adding a public contract

1. Open an RFC issue.
2. Write or update an ADR.
3. Add JSON Schema and valid/invalid fixtures.
4. Add compatibility tests.
5. Document migration and lifecycle expectations.
6. Update the decision ledger and changelog.

## Dependency policy

Runtime dependencies require a clear justification, active maintenance, compatible licence, and
security review. Standard-library solutions are preferred for small boundary functions. Lockfiles
will be introduced before the first release once the supported packaging workflow is finalized.
