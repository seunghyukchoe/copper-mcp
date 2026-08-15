"""Direct JSON Schema conformance for every schema published under `schemas/`.

CopperMCP's Python boundary types (``copper_mcp.models``) already have decode/validate tests that
exercise their own runtime rules. Those tests never load the *published* JSON Schema files, so a
schema file could drift from the model that is supposed to satisfy it and nothing would notice.
That is not hypothetical: ``schemas/drc-summary.schema.json`` sets ``additionalProperties: false``
and did not declare the ``clean`` field that ``DrcSummary.to_dict()`` has always emitted, so
CopperMCP's own published DRC payload failed CopperMCP's own published schema (issue #11).

This module closes that gap in three layers, weakest to strongest:

1. **Fixtures.** One minimal ``valid.json`` per directly covered schema, plus one
   ``invalid-<condition>.json`` per malformed condition. Each invalid fixture differs from
   ``valid.json`` in exactly one way, and ``_EXPECTED_REJECTIONS`` names the exact validation
   errors it must produce — so a fixture that starts failing for an unrelated reason fails the
   suite instead of quietly still "passing".
2. **Emitted payloads.** Each ``valid.json`` is asserted byte-equal to what the model's
   ``to_dict()`` actually publishes, round-tripped through JSON. A fixture the code cannot produce
   proves nothing about the code.
3. **Field parity.** ``set(to_dict())`` is asserted equal to the schema's declared property names,
   and the schema's ``required`` list a subset of them. This is the invariant the ``clean`` defect
   violated, checked without needing a fixture to notice.

``_SCHEMA_COVERAGE`` records how *every* file under `schemas/` is covered, including the schemas
proved elsewhere, and names the exact test function that carries the proof.
``test_every_published_schema_has_a_named_proof`` fails if a new schema file appears with no entry,
and ``test_every_recorded_proof_still_exists`` fails if a named proof is renamed or deleted — so
neither a new schema nor a silently removed test can leave a published schema unverified.

Adding coverage for a new schema is deliberately mechanical; `docs/development.md` has the recipe.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from copper_mcp.models import (
    BoardCounts,
    BoardManifest,
    CandidateMetrics,
    CandidateSummary,
    DrcSummary,
)

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_ROOT = ROOT / "schemas"
FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "schema-conformance"

_SHA_A = "a" * 64
_SHA_B = "b" * 64
_SHA_C = "c" * 64
_SHA_D = "d" * 64


@dataclass(frozen=True)
class _Proof:
    """How one published schema is proved to accept what CopperMCP really produces.

    ``kind`` is a closed vocabulary rather than free prose:

    - ``emitted_payload`` — a test builds a payload through the production code path and validates
      that payload against the schema file. This is the strongest available claim.
    - ``committed_artifact`` — the published artifact is a file in this repository rather than
      something an in-process call returns, and a test validates those exact committed bytes.
    - ``legacy_no_emitter`` — the schema is retained for documents produced by an earlier release.
      No code path emits it any more (the active codec refuses it by design), so an
      ``emitted_payload`` proof is not available and the committed golden document is what is
      checked. This is a one-value literal for a non-claim, not a softer version of the others.
    """

    module: str
    test: str
    kind: str
    note: str


_EMITTED = "emitted_payload"
_COMMITTED = "committed_artifact"
_LEGACY = "legacy_no_emitter"
_THIS_MODULE = "tests/test_schema_conformance.py"

# Every JSON Schema file under `schemas/`, and the exact test that proves it accepts what
# CopperMCP publishes. A file missing from this mapping fails
# `test_every_published_schema_has_a_named_proof`; an entry naming a test that no longer exists
# fails `test_every_recorded_proof_still_exists`.
_SCHEMA_COVERAGE: dict[str, _Proof] = {
    "board-manifest.schema.json": _Proof(
        module=_THIS_MODULE,
        test="test_board_manifest_emitted_payload_satisfies_its_published_schema",
        kind=_EMITTED,
        note="BoardManifest.to_dict()",
    ),
    "candidate.schema.json": _Proof(
        module=_THIS_MODULE,
        test="test_candidate_emitted_payload_satisfies_its_published_schema",
        kind=_EMITTED,
        note="CandidateSummary.to_dict()",
    ),
    "drc-summary.schema.json": _Proof(
        module=_THIS_MODULE,
        test="test_drc_summary_emitted_payload_satisfies_its_published_schema",
        kind=_EMITTED,
        note="DrcSummary.to_dict(), the payload issue #11 found the schema rejecting",
    ),
    "board-ir/0.4.0.schema.json": _Proof(
        module="tests/test_board_ir_schema.py",
        test="test_adapter_output_matches_golden_fixture_and_schema",
        kind=_EMITTED,
        note=(
            "encode_snapshot(parse_kicad_bytes(...)) validated against the schema file; the "
            "optional far-side courtyard keys are additionally proved by "
            "test_schema_accepts_an_emitted_far_side_courtyard_payload_and_closes_it in the "
            "same module, which the subset board cannot exercise"
        ),
    ),
    "board-ir/0.3.0.schema.json": _Proof(
        module="tests/test_board_ir_schema.py",
        test="test_the_frozen_v0_3_0_schema_still_accepts_the_envelope_it_was_published_beside",
        kind=_LEGACY,
        note="byte-frozen after the 0.4.0 custom-pad accepted-set widening",
    ),
    "board-ir/0.2.0.schema.json": _Proof(
        module="tests/test_board_ir_schema.py",
        test="test_the_frozen_v0_2_0_schema_still_accepts_the_envelope_it_was_published_beside",
        kind=_LEGACY,
        note=(
            "byte-frozen by ADR-0105 and no longer emitted; the proof is that it still accepts "
            "the 0.2.0-as-published envelope, recovered from the active golden by substituting "
            "the version string"
        ),
    ),
    "board-ir/0.1.0.schema.json": _Proof(
        module="tests/test_board_ir_schema.py",
        test="test_legacy_v0_1_schema_remains_valid_and_accepts_its_golden_snapshot",
        kind=_LEGACY,
        note="decode_snapshot_json refuses a 0.1 envelope, so no live emitter exists to check",
    ),
    "circuit-intent/0.1.0.schema.json": _Proof(
        module="tests/test_circuit_ir.py",
        test="test_fixture_strictly_decodes_encodes_and_matches_schema",
        kind=_EMITTED,
        note="encode_snapshot(...) output validated against the schema file",
    ),
    "circuit-schematic-build/0.1.0.schema.json": _Proof(
        module="tests/test_circuit_intent_service.py",
        test="test_build_metadata_matches_the_published_schema",
        kind=_EMITTED,
        note="CircuitSchematicBuild.to_dict() validated against the schema file",
    ),
    "audio-benchmark-catalog/0.1.0.schema.json": _Proof(
        module="tests/test_audio_benchmarks.py",
        test="test_catalog_and_capability_run_are_valid_and_deterministic",
        kind=_COMMITTED,
        note="scripts/check_audio_benchmarks.py validates benchmarks/audio/catalog.json",
    ),
    "kicad-pcm/pcm.v1.schema.json": _Proof(
        module="tests/test_pcm_package.py",
        test="test_packaged_metadata_validates_against_both_published_schemas",
        kind=_EMITTED,
        note="KiCad's own vendored schema; the built PCM metadata is validated against it",
    ),
    "kicad-pcm/pcm.v2.schema.json": _Proof(
        module="tests/test_pcm_package.py",
        test="test_submission_metadata_validates_against_both_published_schemas",
        kind=_EMITTED,
        note="KiCad's own vendored schema; the built PCM submission is validated against it",
    ),
}

# Schemas whose fixtures live in this module, in `tests/fixtures/schema-conformance/<name>/`.
_DIRECT_SCHEMAS = ("board-manifest", "candidate", "drc-summary")

# The exact validation errors each invalid fixture must produce, as ``(keyword, field)`` pairs.
# ``field`` is the dotted location the error is about: for ``required`` and
# ``additionalProperties`` the error's own path stops at the enclosing object, so the field name is
# recovered structurally (see ``_subject``) — otherwise the two DRC ``required`` fixtures below
# would be indistinguishable, and the one that pins issue #11 would prove nothing specific.
# Every invalid fixture differs from its `valid.json` in exactly one field, and an unexpected extra
# error fails the test loudly. One changed field may legitimately produce more than one error now
# that the DRC schema pins the `passed`/`clean` derivation: a negative `error_count` is both an
# out-of-range count *and* a report whose derived flags no longer follow from its counts. Both are
# recorded rather than one being papered over.
_EXPECTED_REJECTIONS: dict[tuple[str, str], set[tuple[str, str]]] = {
    ("board-manifest", "invalid-missing-required-field.json"): {("required", "counts")},
    ("board-manifest", "invalid-unexpected-additional-property.json"): {
        ("additionalProperties", "unexpected_field")
    },
    ("board-manifest", "invalid-unexpected-nested-additional-property.json"): {
        ("additionalProperties", "counts.unexpected_nested_field")
    },
    ("board-manifest", "invalid-negative-count.json"): {("minimum", "counts.vias")},
    ("board-manifest", "invalid-malformed-sha256-revision.json"): {("pattern", "revision")},
    ("board-manifest", "invalid-wrong-schema-version.json"): {("const", "schema_version")},
    ("candidate", "invalid-missing-required-field.json"): {("required", "policy")},
    ("candidate", "invalid-unexpected-additional-property.json"): {
        ("additionalProperties", "unexpected_field")
    },
    ("candidate", "invalid-unexpected-nested-additional-property.json"): {
        ("additionalProperties", "metrics.unexpected_nested_field")
    },
    ("candidate", "invalid-negative-count.json"): {("minimum", "metrics.vias")},
    ("candidate", "invalid-malformed-sha256-candidate-id.json"): {("pattern", "candidate_id")},
    ("candidate", "invalid-wrong-schema-version.json"): {("const", "schema_version")},
    ("drc-summary", "invalid-missing-required-field.json"): {("required", "passed")},
    ("drc-summary", "invalid-missing-derived-clean-field.json"): {("required", "clean")},
    ("drc-summary", "invalid-unexpected-additional-property.json"): {
        ("additionalProperties", "unexpected_field")
    },
    ("drc-summary", "invalid-clean-true-beside-a-finding.json"): {("const", "clean")},
    ("drc-summary", "invalid-clean-false-on-a-report-with-no-findings.json"): {("const", "clean")},
    ("drc-summary", "invalid-passed-true-beside-an-error.json"): {("const", "passed")},
    ("drc-summary", "invalid-passed-false-on-a-report-with-no-errors.json"): {("const", "passed")},
    # A negative `error_count` is out of range *and* falsifies both derived flags, which the schema
    # now pins. All three are recorded so the fixture keeps proving every consequence it has.
    ("drc-summary", "invalid-negative-count.json"): {
        ("minimum", "error_count"),
        ("const", "passed"),
        ("const", "clean"),
    },
    ("drc-summary", "invalid-malformed-sha256-base-revision.json"): {("pattern", "base_revision")},
    ("drc-summary", "invalid-wrong-schema-version.json"): {("const", "schema_version")},
}


def _load_json(path: Path) -> dict[str, Any]:
    document = json.loads(path.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _schema(name: str) -> dict[str, Any]:
    return _load_json(SCHEMA_ROOT / f"{name}.schema.json")


def _validator(name: str) -> Draft202012Validator:
    schema = _schema(name)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fixture(name: str, filename: str) -> dict[str, Any]:
    return _load_json(FIXTURE_ROOT / name / filename)


def _invalid_fixtures() -> list[tuple[str, Path]]:
    return [
        (name, path)
        for name in _DIRECT_SCHEMAS
        for path in sorted((FIXTURE_ROOT / name).glob("invalid-*.json"))
    ]


def _as_wire_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip a Python payload through JSON so conformance judges the wire bytes.

    A dataclass ``to_dict()`` may carry a ``tuple`` where the JSON wire format has an array (for
    example ``CandidateSummary.warnings``). A caller of CopperMCP never receives that Python
    object, only the JSON bytes produced by serializing it, so this is the representative shape a
    schema conformance test must check.
    """

    document = json.loads(json.dumps(payload))
    assert isinstance(document, dict)
    return document


def _subject(error: Any) -> str:
    """The dotted field one validation error is about.

    For most keywords the error's own path already points at the offending field. ``required`` and
    ``additionalProperties`` are about a property of the object the path stops at, so the field is
    recovered *structurally* — by diffing the instance against the schema — rather than by parsing
    the human-readable message, which is not a contract jsonschema owes anyone.
    """

    path = ".".join(str(part) for part in error.absolute_path)
    keyword = str(error.validator)
    if keyword == "required":
        fields = sorted(set(error.validator_value) - set(error.instance))
    elif keyword == "additionalProperties":
        fields = sorted(set(error.instance) - set(error.schema.get("properties", {})))
    else:
        return path
    assert fields, f"expected {keyword} to name at least one field: {error.message}"
    return ".".join(part for part in (path, "+".join(fields)) if part)


def _field_parity(schema: dict[str, Any], emitted: set[str]) -> tuple[set[str], set[str]]:
    """Compare a schema's declared property names against the keys a model really emits.

    Returns ``(declared but never emitted, emitted but never declared)``. The second element is
    what was non-empty for the DRC summary before issue #11's defect was fixed: ``{"clean"}``.
    """

    declared = set(schema["properties"])
    return declared - emitted, emitted - declared


def _coverage_gaps(found: set[str], recorded: set[str]) -> tuple[set[str], set[str]]:
    """Compare the schema files on disk against the schemas ``_SCHEMA_COVERAGE`` records.

    Returns ``(on disk with no recorded proof, recorded but no longer on disk)``. The first element
    is the one that matters most: a new schema file shipping with nothing proving it accepts a real
    payload is exactly how issue #11's defect would recur.
    """

    return found - recorded, recorded - found


def _proof_is_defined(source: str, test: str) -> bool:
    """Whether a module's source still defines the test function a proof entry names."""

    return f"def {test}(" in source


def _rejections(name: str, payload: dict[str, Any]) -> set[tuple[str, str]]:
    return {
        (str(error.validator), _subject(error)) for error in _validator(name).iter_errors(payload)
    }


def _emitted_board_manifest() -> dict[str, Any]:
    manifest = BoardManifest(
        board_id="board:0123456789abcdef",
        revision=f"sha256:{_SHA_A}",
        relative_path="boards/example.kicad_pcb",
        format="kicad_pcb",
        size_bytes=12345,
        counts=BoardCounts(copper_layers=2, footprints=3, nets=4, segments=10, vias=2, zones=1),
        source_version="8.0.0",
        source_generator="pcbnew",
    )
    return _as_wire_json(manifest.to_dict())


def _emitted_candidate() -> dict[str, Any]:
    candidate = CandidateSummary(
        candidate_id=f"sha256:{_SHA_A}",
        base_revision=f"sha256:{_SHA_B}",
        status="proposed",
        metrics=CandidateMetrics(
            hard_drc_errors=0,
            unrouted_connections=0,
            vias=2,
            wire_length_mm=12.5,
            runtime_seconds=0.5,
        ),
        router_version="0.6.0",
        policy="default",
        seed=1,
    )
    return _as_wire_json(candidate.to_dict())


def _emitted_drc_summary() -> dict[str, Any]:
    summary = DrcSummary(
        base_revision=f"sha256:{_SHA_C}",
        drc_context_revision=f"sha256:{_SHA_D}",
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={},
        passed=True,
    )
    return _as_wire_json(summary.to_dict())


_EMITTERS: dict[str, Callable[[], dict[str, Any]]] = {
    "board-manifest": _emitted_board_manifest,
    "candidate": _emitted_candidate,
    "drc-summary": _emitted_drc_summary,
}


def test_every_published_schema_has_a_named_proof() -> None:
    """A new file under `schemas/` cannot ship without a recorded proof that it accepts reality."""

    found = {str(path.relative_to(SCHEMA_ROOT)) for path in SCHEMA_ROOT.rglob("*.schema.json")}
    unproved, stale = _coverage_gaps(found, set(_SCHEMA_COVERAGE))

    assert (unproved, stale) == (set(), set()), (
        "schemas/ and _SCHEMA_COVERAGE disagree — add the new schema's proof (or record why it "
        f"needs none): unproved on disk={sorted(unproved)} recorded but missing={sorted(stale)}"
    )


def test_the_coverage_completeness_check_is_sensitive_in_both_directions() -> None:
    """Guard the guard: the check that catches an unproved schema needs its own evidence.

    Comparing two sets that are equal today proves nothing about what happens when they diverge,
    and divergence is the entire point of this guard. The second case is the one that keeps issue
    #11's class of defect from recurring: a schema file added with no proof recorded for it.
    """

    assert _coverage_gaps({"a.schema.json"}, {"a.schema.json"}) == (set(), set())
    assert _coverage_gaps({"a.schema.json", "new.schema.json"}, {"a.schema.json"}) == (
        {"new.schema.json"},
        set(),
    )
    assert _coverage_gaps({"a.schema.json"}, {"a.schema.json", "deleted.schema.json"}) == (
        set(),
        {"deleted.schema.json"},
    )


def test_the_proof_existence_check_notices_a_renamed_or_deleted_proof() -> None:
    """Guard the guard: a substring check that is never shown failing is not a check.

    ``_SCHEMA_COVERAGE`` names proofs that live in other modules, so the only thing tying an entry
    to a real test is this lookup. If it silently returned true, every entry would keep claiming a
    proof that had been renamed away.
    """

    source = "def test_a_real_proof() -> None:\n    pass\n"

    assert _proof_is_defined(source, "test_a_real_proof")
    assert not _proof_is_defined(source, "test_a_proof_that_was_renamed")


@pytest.mark.parametrize(
    "schema_path", sorted(_SCHEMA_COVERAGE), ids=lambda path: path.replace("/", "-")
)
def test_every_recorded_proof_still_exists(schema_path: str) -> None:
    """Guard the guard: a renamed or deleted proof must fail here, not silently stop proving."""

    proof = _SCHEMA_COVERAGE[schema_path]

    assert proof.kind in {_EMITTED, _COMMITTED, _LEGACY}, proof.kind
    module = ROOT / proof.module
    assert module.is_file(), f"{schema_path} names a proof module that does not exist: {module}"
    assert _proof_is_defined(module.read_text(encoding="utf-8"), proof.test), (
        f"{schema_path} names {proof.test} in {proof.module}, which no longer defines it"
    )


@pytest.mark.parametrize("name", _DIRECT_SCHEMAS)
def test_schema_is_a_valid_draft_2020_12_document(name: str) -> None:
    """Each schema this module covers must itself be a well-formed Draft 2020-12 document."""

    Draft202012Validator.check_schema(_schema(name))


@pytest.mark.parametrize("name", _DIRECT_SCHEMAS)
def test_schema_declares_exactly_the_fields_its_model_publishes(name: str) -> None:
    """The invariant issue #11 caught being violated, checked without a fixture in the way.

    ``schemas/drc-summary.schema.json`` closed itself with ``additionalProperties: false`` and then
    omitted ``clean``, which ``DrcSummary.to_dict()`` has always emitted. Comparing the declared
    property names against the emitted key set catches that directly. ``required`` is checked as a
    subset because a schema may legitimately declare an optional property — ``source_version`` and
    ``source_generator`` on the board manifest are nullable and not required.
    """

    schema = _schema(name)
    emitted = set(_EMITTERS[name]())
    never_emitted, never_declared = _field_parity(schema, emitted)

    assert (never_emitted, never_declared) == (set(), set()), (
        f"{name}.schema.json declares {sorted(never_emitted)} the model never emits, and the model "
        f"emits {sorted(never_declared)} the schema never declares"
    )
    assert set(schema["required"]) <= emitted


def test_the_field_parity_check_is_sensitive_in_both_directions() -> None:
    """Guard the guard: an equality that only ever sees equal inputs proves nothing.

    ``_field_parity`` is the check that would have caught issue #11 on its own, so it needs its own
    evidence that it reports a mismatch rather than quietly tolerating one. The second case below
    is the exact shape of the real defect: a model emitting ``clean`` that the schema never
    declares.
    """

    emitted = {"passed", "clean"}
    over_declaring = {"properties": {"passed": {}, "clean": {}, "never_emitted": {}}}
    under_declaring = {"properties": {"passed": {}}}

    assert _field_parity({"properties": {"passed": {}, "clean": {}}}, emitted) == (set(), set())
    assert _field_parity(over_declaring, emitted) == ({"never_emitted"}, set())
    assert _field_parity(under_declaring, emitted) == (set(), {"clean"})


def test_board_manifest_emitted_payload_satisfies_its_published_schema() -> None:
    """`BoardManifest.to_dict()` is exactly the committed fixture, and the schema accepts it."""

    payload = _emitted_board_manifest()
    fixture = _fixture("board-manifest", "valid.json")

    assert payload == fixture
    assert _rejections("board-manifest", payload) == set()


def test_candidate_emitted_payload_satisfies_its_published_schema() -> None:
    """`CandidateSummary.to_dict()` is exactly the committed fixture, and the schema accepts it."""

    payload = _emitted_candidate()
    fixture = _fixture("candidate", "valid.json")

    assert payload == fixture
    assert _rejections("candidate", payload) == set()


def test_drc_summary_emitted_payload_satisfies_its_published_schema() -> None:
    """The regression test for issue #11's defect: this failed before `clean` was declared.

    ``DrcSummary.to_dict()`` emits ``clean``; the schema set ``additionalProperties: false`` and
    did not declare it, so this payload — the one ``attestation.py``,
    ``post_placement_observation.py`` and ``route_preview.py`` all publish — was rejected by
    CopperMCP's own published schema.
    """

    payload = _emitted_drc_summary()
    fixture = _fixture("drc-summary", "valid.json")

    assert "clean" in payload
    assert payload == fixture
    assert _rejections("drc-summary", payload) == set()


def test_drc_summary_schema_still_requires_the_derived_clean_field() -> None:
    """`clean` is required, not merely permitted, so the schema cannot drift back into disagreement.

    The MCP wire contract ``RouteDrcSummaryContract`` is closed and declares ``clean`` as a
    required field, so a payload without one is already rejected there. Declaring it optional here
    would let the two published contracts disagree in the opposite direction.
    """

    schema = _schema("drc-summary")
    without_clean = {key: value for key, value in _emitted_drc_summary().items() if key != "clean"}

    assert schema["properties"]["clean"]["type"] == "boolean"
    assert "clean" in schema["required"]
    assert _rejections("drc-summary", without_clean) == {("required", "clean")}


#: ``(error, warning, exclusion, unconnected, ignored, violation_type_counts)`` combinations that
#: exercise each count's independent contribution to ``passed`` and ``clean``. Every row satisfies
#: the model's own rule that the violation-type counts sum to
#: ``error + warning + exclusion + unconnected``.
_COUNT_GRID = [
    (0, 0, 0, 0, 0, {}),
    (0, 1, 0, 0, 0, {"silk_over_copper": 1}),
    (2, 0, 0, 0, 0, {"clearance": 2}),
    (0, 0, 1, 0, 0, {"courtyards_overlap": 1}),
    (0, 0, 0, 1, 0, {"unconnected_items": 1}),
    (0, 0, 0, 0, 3, {}),
    # Every count is zero, yet a violation type is present with a zero count, so the report is
    # `passed` but **not** `clean`. This row is why the schema's `clean` condition also requires
    # `violation_type_counts` to be empty rather than only checking the five counts.
    (0, 0, 0, 0, 0, {"silk_over_copper": 0}),
]


def _summary_payload(
    errors: int,
    warnings: int,
    exclusions: int,
    unconnected: int,
    ignored: int,
    counts: dict[str, int],
) -> dict[str, Any]:
    summary = DrcSummary(
        base_revision=f"sha256:{_SHA_C}",
        drc_context_revision=f"sha256:{_SHA_D}",
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=errors,
        warning_count=warnings,
        exclusion_count=exclusions,
        ignored_check_count=ignored,
        unconnected_count=unconnected,
        violation_type_counts=counts,
        passed=errors == 0 and unconnected == 0,
    )
    return _as_wire_json(summary.to_dict())


@pytest.mark.parametrize(
    "row", _COUNT_GRID, ids=lambda row: f"e{row[0]}w{row[1]}x{row[2]}u{row[3]}i{row[4]}"
)
def test_the_schema_derives_passed_and_clean_exactly_as_the_model_does(
    row: tuple[int, int, int, int, int, dict[str, int]],
) -> None:
    """The schema must agree with `DrcSummary` on both derived flags, in both directions.

    Declaring `passed` and `clean` as free booleans would let a payload claim `clean: true` beside a
    nonzero count and still validate — a **false claim about a board**, reachable through the
    contract CopperMCP publishes, which the runtime model and the closed MCP contract both refuse.
    That is a worse failure than the `additionalProperties` defect this branch started from: that
    one made a true payload fail, this one would make a false payload pass. So the schema pins the
    derivation, and this test checks it against the code's own answer rather than against a
    restatement of it: the honest payload is accepted, and flipping either flag is rejected.
    """

    payload = _summary_payload(*row)

    assert _rejections("drc-summary", payload) == set()
    assert _rejections("drc-summary", {**payload, "clean": not payload["clean"]}) == {
        ("const", "clean")
    }
    assert _rejections("drc-summary", {**payload, "passed": not payload["passed"]}) == {
        ("const", "passed")
    }


def test_field_parity_compares_names_only_and_cannot_see_the_derivation() -> None:
    """State the limit plainly: `_field_parity` would not have caught the consistency gap.

    `_field_parity` compares the schema's declared property *names* against the emitted key set. It
    is blind to what a schema says about a value, so it reported no mismatch while `clean` was
    declared as an unconstrained boolean and a lying payload validated. Naming that here keeps the
    coverage story honest — the `allOf` derivation above is what closes the semantic gap, and the
    grid test above is what proves it.
    """

    schema = _schema("drc-summary")
    emitted = set(_EMITTERS["drc-summary"]())
    # The schema as it stood before this change: same property names, no derivation pinned.
    unconstrained = {key: value for key, value in schema.items() if key != "allOf"}

    # Field parity is satisfied by both, because both declare exactly the emitted names.
    assert _field_parity(schema, emitted) == (set(), set())
    assert _field_parity(unconstrained, emitted) == (set(), set())

    # Yet only one of them refuses a payload that claims to be clean beside a real finding.
    lying = {**_emitted_drc_summary(), "warning_count": 1, "violation_type_counts": {"silk": 1}}
    assert list(Draft202012Validator(unconstrained).iter_errors(lying)) == []
    assert list(Draft202012Validator(schema).iter_errors(lying)) != []


@pytest.mark.parametrize(
    ("name", "invalid_path"), _invalid_fixtures(), ids=lambda value: getattr(value, "name", value)
)
def test_invalid_fixture_is_rejected_for_exactly_the_reason_it_names(
    name: str, invalid_path: Path
) -> None:
    """Every `invalid-*.json` must fail, and fail for precisely the condition its filename names.

    Asserting merely "some error" is too weak: a fixture that drifts out of date fails for a new,
    unrelated reason and still looks like it is doing its job. Each fixture differs from its
    `valid.json` in exactly one way, so the expected error set is exact.
    """

    key = (name, invalid_path.name)

    assert key in _EXPECTED_REJECTIONS, (
        f"{invalid_path.name} has no entry in _EXPECTED_REJECTIONS; name the errors it must produce"
    )
    assert _rejections(name, _load_json(invalid_path)) == _EXPECTED_REJECTIONS[key]


@pytest.mark.parametrize("name", _DIRECT_SCHEMAS)
def test_every_expected_rejection_has_a_fixture_on_disk(name: str) -> None:
    """The reverse of the check above: a deleted fixture must not silently drop its condition."""

    on_disk = {path.name for path in (FIXTURE_ROOT / name).glob("invalid-*.json")}
    recorded = {filename for schema, filename in _EXPECTED_REJECTIONS if schema == name}

    assert on_disk == recorded
