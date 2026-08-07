"""Direct JSON Schema conformance for the schemas published under `schemas/`.

CopperMCP's Python boundary types (``copper_mcp.models``) already have decode/validate tests that
exercise their own runtime rules. Those tests never load the *published* JSON Schema files, so a
schema file could silently drift from the model that is supposed to satisfy it and nothing would
notice. This module closes that gap for the three schemas named in issue #11 as lacking direct
coverage — board manifests, DRC summaries, and route-candidate ("candidate") metadata — by loading
each schema from `schemas/` and asserting a committed fixture passes or fails against it, in both
directions: a valid fixture must pass, and each deliberately malformed fixture must fail for the
specific reason it names.

Board IR (`schemas/board-ir/`), Circuit Intent (`schemas/circuit-intent/`), Circuit Schematic Build
(`schemas/circuit-schematic-build/`), and the audio benchmark catalog
(`schemas/audio-benchmark-catalog/`) already have this kind of direct, fixture-based schema
conformance test elsewhere (`test_board_ir_schema.py`, `test_circuit_ir.py`,
`test_circuit_intent_service.py`, `test_audio_benchmarks.py`). They are not duplicated here.
`test_every_schema_file_on_disk_is_covered_by_a_known_test_module` below is a completeness guard: it
fails the moment a new file is added under `schemas/` without a matching entry in `_KNOWN_SCHEMAS`,
so a future schema cannot silently ship without a fixture or a recorded reason it does not need one.
"""

from __future__ import annotations

import json
from copy import deepcopy
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

# Every JSON Schema file that exists under `schemas/` today, and how it is covered. A schema
# path missing from this mapping — added after this test was written — fails
# `test_every_schema_file_on_disk_is_covered_by_a_known_test_module` below, rather than shipping
# unverified.
_KNOWN_SCHEMAS: dict[str, str] = {
    "board-manifest.schema.json": "tests/test_schema_conformance.py (this module)",
    "candidate.schema.json": "tests/test_schema_conformance.py (this module)",
    "drc-summary.schema.json": "tests/test_schema_conformance.py (this module)",
    "board-ir/0.1.0.schema.json": "tests/test_board_ir_schema.py",
    "board-ir/0.2.0.schema.json": "tests/test_board_ir_schema.py",
    "circuit-intent/0.1.0.schema.json": "tests/test_circuit_ir.py",
    "circuit-schematic-build/0.1.0.schema.json": "tests/test_circuit_intent_service.py",
    "audio-benchmark-catalog/0.1.0.schema.json": "tests/test_audio_benchmarks.py",
}

# Schemas covered directly by this module, keyed by the same relative path used above.
_DIRECT_SCHEMAS = ("board-manifest.schema.json", "candidate.schema.json", "drc-summary.schema.json")


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _schema(relative_path: str) -> dict[str, Any]:
    return _load_json(SCHEMA_ROOT / relative_path)


def _validator(relative_path: str) -> Draft202012Validator:
    schema = _schema(relative_path)
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema)


def _fixture(schema_name: str, filename: str) -> dict[str, Any]:
    return _load_json(FIXTURE_ROOT / schema_name / filename)


def _invalid_fixtures(schema_name: str) -> list[Path]:
    return sorted((FIXTURE_ROOT / schema_name).glob("invalid-*.json"))


def _as_wire_json(payload: dict[str, Any]) -> dict[str, Any]:
    """Round-trip a Python payload through JSON so schema conformance judges wire bytes.

    A dataclass ``to_dict()`` may carry a ``tuple`` where the JSON wire format has an array (for
    example ``CandidateSummary.warnings``). A caller of CopperMCP never receives that Python
    object, only the JSON bytes produced by serializing it, so this is the representative shape a
    schema conformance test must check.
    """

    return json.loads(json.dumps(payload))


def test_every_schema_file_on_disk_is_covered_by_a_known_test_module() -> None:
    """Guard that every file under `schemas/` is accounted for, so a new one cannot ship silently.

    This does not re-run the coverage in the named modules; it only asserts the on-disk schema set
    matches the set this repository has decided is covered, so an uncovered addition fails loudly
    here instead of nowhere.
    """

    found = {str(path.relative_to(SCHEMA_ROOT)) for path in SCHEMA_ROOT.rglob("*.schema.json")}
    assert found == set(_KNOWN_SCHEMAS), (
        "schemas/ contains files with no recorded coverage (or _KNOWN_SCHEMAS is stale): "
        f"on disk={sorted(found)} known={sorted(_KNOWN_SCHEMAS)}"
    )


@pytest.mark.parametrize("schema_name", _DIRECT_SCHEMAS)
def test_schema_is_a_valid_draft_2020_12_document(schema_name: str) -> None:
    """Each schema this module covers must itself be a well-formed Draft 2020-12 document."""

    Draft202012Validator.check_schema(_schema(schema_name))


def test_board_manifest_valid_fixture_satisfies_its_published_schema() -> None:
    """The committed board-manifest fixture is exactly what `BoardManifest.to_dict()` publishes."""

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
    payload = _as_wire_json(manifest.to_dict())
    fixture = _fixture("board-manifest", "valid.json")

    assert payload == fixture
    assert list(_validator("board-manifest.schema.json").iter_errors(fixture)) == []


@pytest.mark.parametrize("invalid_path", _invalid_fixtures("board-manifest"), ids=lambda p: p.name)
def test_board_manifest_invalid_fixture_is_rejected_by_its_published_schema(
    invalid_path: Path,
) -> None:
    """Every `board-manifest/invalid-*.json` fixture must fail schema validation for its reason."""

    payload = _load_json(invalid_path)
    errors = list(_validator("board-manifest.schema.json").iter_errors(payload))

    assert errors, f"{invalid_path.name} was expected to fail schema validation but passed"


def test_candidate_valid_fixture_satisfies_its_published_schema() -> None:
    """The committed candidate fixture is exactly what `CandidateSummary.to_dict()` publishes."""

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
    payload = _as_wire_json(candidate.to_dict())
    fixture = _fixture("candidate", "valid.json")

    assert payload == fixture
    assert list(_validator("candidate.schema.json").iter_errors(fixture)) == []


@pytest.mark.parametrize("invalid_path", _invalid_fixtures("candidate"), ids=lambda p: p.name)
def test_candidate_invalid_fixture_is_rejected_by_its_published_schema(invalid_path: Path) -> None:
    """Every `candidate/invalid-*.json` fixture must fail schema validation for its named reason."""

    payload = _load_json(invalid_path)
    errors = list(_validator("candidate.schema.json").iter_errors(payload))

    assert errors, f"{invalid_path.name} was expected to fail schema validation but passed"


def test_drc_summary_valid_fixture_satisfies_its_published_schema() -> None:
    """The committed drc-summary fixture is a minimal, schema-conformant DRC summary payload.

    Unlike the board-manifest and candidate fixtures above, this one is *not* asserted equal to
    ``DrcSummary(...).to_dict()`` — see
    ``test_drc_summary_to_dict_has_a_field_its_published_schema_does_not_declare`` below for why.
    """

    fixture = _fixture("drc-summary", "valid.json")

    assert list(_validator("drc-summary.schema.json").iter_errors(fixture)) == []


@pytest.mark.parametrize("invalid_path", _invalid_fixtures("drc-summary"), ids=lambda p: p.name)
def test_drc_summary_invalid_fixture_is_rejected_by_its_published_schema(
    invalid_path: Path,
) -> None:
    """Every `drc-summary/invalid-*.json` fixture must fail schema validation for its reason."""

    payload = _load_json(invalid_path)
    errors = list(_validator("drc-summary.schema.json").iter_errors(payload))

    assert errors, f"{invalid_path.name} was expected to fail schema validation but passed"


def test_drc_summary_to_dict_has_a_field_its_published_schema_does_not_declare() -> None:
    """Pins a real schema/model mismatch found while writing this suite; out of scope to fix here.

    ``schemas/drc-summary.schema.json`` sets ``"additionalProperties": false`` and does not declare
    a ``clean`` property, but ``DrcSummary.to_dict()`` (used by ``attestation.py``,
    ``post_placement_observation.py``, and ``route_preview.py``) always includes one. This test
    documents the current, confirmed-real gap between the published schema and the model's actual
    JSON output so it cannot regress unnoticed and is not lost. Per the scope of issue #11, closing
    the gap (either by adding ``clean`` to the schema or dropping it from ``to_dict()``) is a
    separate, deliberate change to public schema semantics and must not be made here.
    """

    drc_summary = DrcSummary(
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
    payload = _as_wire_json(drc_summary.to_dict())
    errors = list(_validator("drc-summary.schema.json").iter_errors(payload))

    assert "clean" in payload
    assert any(
        error.validator == "additionalProperties" and "clean" in error.message for error in errors
    ), f"expected an 'additionalProperties' rejection naming 'clean', got: {errors}"

    without_clean = deepcopy(payload)
    del without_clean["clean"]
    assert list(_validator("drc-summary.schema.json").iter_errors(without_clean)) == []
