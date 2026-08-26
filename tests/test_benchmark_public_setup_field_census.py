"""Focused safety and determinism tests for the public setup-field census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from copper_mcp.config import Settings
from scripts import benchmark_public_setup_field_census as census
from scripts import benchmark_fixed_point_masking_census as masking


def _setup() -> str:
    return """
    (setup
      (pad_to_mask_clearance 0)
      (stackup
        (layer "F.Cu" (type "copper") (thickness 0.035))
        (layer "dielectric 1" (type "core") (material "FR4")
          (epsilon_r 4.5) (loss_tangent 0.02))
        (copper_finish "ENIG")
        (dielectric_constraints yes)))
    """


def _board(marker: str, *, setup: str | None = None) -> bytes:
    setup_text = _setup() if setup is None else setup
    return (
        "(kicad_pcb "
        "(version 20240108) "
        f"{setup_text} "
        f"(marker {marker}))"
    ).encode()


def _entries(
    corpus: Path,
    *,
    setup_count: int = 6,
    setup_override: str | None = None,
) -> tuple[list[dict[str, str]], str]:
    entries: list[dict[str, str]] = []
    for index in range(13):
        relative = f"board-{index:02d}.kicad_pcb"
        if index < setup_count:
            source = _board(
                "setup",
                setup=setup_override if index == 0 and setup_override is not None else None,
            )
        else:
            source = _board("other", setup="(setup (pad_to_mask_clearance 0))")
        (corpus / relative).write_bytes(source)
        entries.append(
            {
                "id": f"opaque-{index:02d}",
                "visibility": "public" if index < 10 else "private",
                "path": relative,
                "sha256": "sha256:" + hashlib.sha256(source).hexdigest(),
            }
        )
    material = "".join(
        f"{entry['id']}:{entry['visibility']}:{entry['path']}:{entry['sha256']}\n"
        for entry in entries
    ).encode()
    return entries, "sha256:" + hashlib.sha256(material).hexdigest()[:32]


def _manifest(
    tmp_path: Path,
    *,
    setup_count: int = 6,
    setup_override: str | None = None,
) -> tuple[Path, Path, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(
        corpus,
        setup_count=setup_count,
        setup_override=setup_override,
    )
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": masking.SCHEMA,
                "entries": entries,
                "fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    return corpus, path, fingerprint


def _settings(corpus: Path) -> Settings:
    return Settings(workspace=corpus, max_board_bytes=1_000_000)


def _diagnostic(message: str, locator: str) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=None,
        diagnostics=(
            SimpleNamespace(
                code="unsupported.construct",
                source_locator=locator,
                message=message,
            ),
        ),
    )


def _converter(source: bytes, _settings: Settings) -> SimpleNamespace:
    if b"(marker setup)" in source:
        return _diagnostic(masking.SETUP_SEMANTIC_MESSAGE, "kicad_pcb.setup")
    return _diagnostic(
        masking.ROOT_SEMANTIC_MESSAGE,
        "kicad_pcb.child[2]",
    )


def test_closed_accepted_vocabulary_matches_the_adapter() -> None:
    assert census.ACCEPTED_SETUP_HEADS == census.kicad_board_ir._SETUP_METADATA_HEADS
    assert "stackup" not in census.ACCEPTED_SETUP_HEADS
    assert "stackup" in census.DIRECT_SETUP_HEADS
    assert census.OTHER not in census.DIRECT_SETUP_HEADS


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        (b"(field)", "empty"),
        (b"(field one)", "one_atom"),
        (b"(field one two)", "many_atoms"),
        (b"(field (child one))", "one_child"),
        (b"(field (child one) (child two))", "many_children"),
        (b"(field one (child two))", "mixed"),
    ],
)
def test_shape_classification_is_closed(source: bytes, expected: str, tmp_path: Path) -> None:
    node = census.parse_sexpr(source, masking.parse_limits_for(_settings(tmp_path)))
    assert census._shape(node) == expected


def test_unknown_heads_and_atom_values_are_bucketed_without_echo(tmp_path: Path) -> None:
    source = b"""
    (setup
      (stackup
        (layer "SECRET-LAYER"
          (type "copper")
          (vendor-layer-field "SECRET-LAYER-VALUE"))
        (vendor-stackup-field "SECRET-STACKUP-VALUE"))
      (vendor-direct-field "SECRET-DIRECT-VALUE"))
    """
    setup = census.parse_sexpr(source, masking.parse_limits_for(_settings(tmp_path)))
    observation = census._observe_setup(setup)

    assert observation.direct_occurrences == {"stackup": 1, "other": 1}
    assert observation.unsupported_set == "stackup_plus_other"
    assert observation.stackup_occurrences == {"layer": 1, "other": 1}
    assert observation.layer_field_occurrences == {"type": 1, "other": 1}
    serialized = repr(observation)
    for secret in (
        "SECRET-LAYER",
        "SECRET-LAYER-VALUE",
        "SECRET-STACKUP-VALUE",
        "SECRET-DIRECT-VALUE",
        "vendor-direct-field",
        "vendor-stackup-field",
        "vendor-layer-field",
    ):
        assert secret not in serialized


def test_stackup_layer_shape_is_fail_closed(tmp_path: Path) -> None:
    source = b"(setup (stackup (layer (type copper))))"
    setup = census.parse_sexpr(source, masking.parse_limits_for(_settings(tmp_path)))
    with pytest.raises(ValueError, match="positional atom"):
        census._observe_setup(setup)


def test_measure_is_deterministic_aggregate_only_and_source_preserving(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint = _manifest(tmp_path)
    result = census.measure(
        corpus,
        manifest,
        _settings(corpus),
        expected_fingerprint=fingerprint,
        converter=_converter,
    )
    again = census.measure(
        corpus,
        manifest,
        _settings(corpus),
        expected_fingerprint=fingerprint,
        converter=_converter,
    )

    assert result == again
    assert result["schema"] == census.SCHEMA
    assert result["source_census"] == {
        "source_schema": masking.SCHEMA,
        "cohort_fingerprint": fingerprint,
        "captured_entries": 13,
        "public_entries": 10,
        "setup_terminal_entries": 6,
        "selection_rule": "fixed_point_terminal_setup_semantics",
    }
    aggregates = result["aggregates"]
    assert aggregates["boards"] == 6
    assert aggregates["direct_setup"]["occurrences"]["pad_to_mask_clearance"] == 6
    assert aggregates["direct_setup"]["occurrences"]["stackup"] == 6
    assert aggregates["direct_setup"]["board_presence"]["stackup"] == 6
    assert aggregates["unsupported_head_sets"] == {
        "none": 0,
        "stackup_only": 6,
        "stackup_plus_other": 0,
        "other_only": 0,
    }
    stackup = aggregates["stackup"]
    assert stackup["node_count"] == 6
    assert stackup["field_occurrences"]["layer"] == 12
    assert stackup["field_occurrences"]["copper_finish"] == 6
    assert stackup["field_occurrences"]["dielectric_constraints"] == 6
    assert stackup["layer_count"] == 12
    assert stackup["layer_field_occurrences"]["type"] == 12
    assert stackup["layer_field_occurrences"]["thickness"] == 6
    assert stackup["layer_field_occurrences"]["material"] == 6
    assert stackup["layer_field_occurrences"]["epsilon_r"] == 6
    assert stackup["layer_field_occurrences"]["loss_tangent"] == 6
    assert result["source_hashes_unchanged"] is True
    assert result["privacy"]["board_bytes_committed"] == 0
    assert result["claim_scope"]["setup_acceptance"] is False

    serialized = json.dumps(result, sort_keys=True)
    for forbidden in (
        "opaque-00",
        "board-00.kicad_pcb",
        "SECRET",
        "(kicad_pcb",
        '"F.Cu"',
        '"ENIG"',
        '"FR4"',
    ):
        assert forbidden not in serialized


def test_measure_rejects_fingerprint_and_setup_population_drift(tmp_path: Path) -> None:
    corpus, manifest, _fingerprint = _manifest(tmp_path)
    with pytest.raises(ValueError, match="predeclared"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint="sha256:" + "0" * 32,
            converter=_converter,
        )

    other = tmp_path / "other"
    other.mkdir()
    corpus, manifest, fingerprint = _manifest(other, setup_count=5)
    with pytest.raises(ValueError, match="population drifted"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint=fingerprint,
            converter=_converter,
        )


@pytest.mark.parametrize(
    "setup_override",
    [
        "",
        "(setup (pad_to_mask_clearance 0)) (setup (stackup))",
        "(setup bare-atom)",
    ],
)
def test_selected_sources_require_one_structurally_closed_setup(
    tmp_path: Path,
    setup_override: str,
) -> None:
    corpus, manifest, fingerprint = _manifest(
        tmp_path,
        setup_override=setup_override,
    )
    with pytest.raises(ValueError, match="setup"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint=fingerprint,
            converter=_converter,
        )


def test_source_change_after_capture_is_refused(tmp_path: Path) -> None:
    corpus, manifest, fingerprint = _manifest(tmp_path)
    changed = False

    def mutating_converter(source: bytes, settings: Settings) -> SimpleNamespace:
        nonlocal changed
        if not changed:
            changed = True
            (corpus / "board-00.kicad_pcb").write_bytes(source + b"\n")
        return _converter(source, settings)

    with pytest.raises(ValueError, match="source changed"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint=fingerprint,
            converter=mutating_converter,
        )


def test_private_entries_are_never_selected_even_if_converter_reports_setup(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint = _manifest(tmp_path)

    def all_setup(source: bytes, settings: Settings) -> SimpleNamespace:
        if b"(marker setup)" in source or b"(marker other)" in source:
            return _diagnostic(masking.SETUP_SEMANTIC_MESSAGE, "kicad_pcb.setup")
        return _converter(source, settings)

    with pytest.raises(ValueError, match="expected 6, got 10"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint=fingerprint,
            converter=all_setup,
        )


def test_measure_is_read_only(tmp_path: Path) -> None:
    corpus, manifest, fingerprint = _manifest(tmp_path)
    settings = Settings(workspace=corpus, allow_apply=True)
    with pytest.raises(ValueError, match="read-only"):
        census.measure(
            corpus,
            manifest,
            settings,
            expected_fingerprint=fingerprint,
            converter=_converter,
        )
