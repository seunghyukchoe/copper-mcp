"""Focused closure, safety, and determinism tests for the root-zone census."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.adapters.sexpr import SExprError
from copper_mcp.config import Settings
from scripts import benchmark_fixed_point_masking_census as masking
from scripts import benchmark_public_copper_graphic_census as copper_graphic_census
from scripts import benchmark_public_root_zone_field_census as census

_ZONE_CONTAINER_LOCATOR = "kicad_pcb.zone[4]"
_ZONE_FILL_LOCATOR = "kicad_pcb.zone[0].fill"
_FOOTPRINT_ZONE_LOCATOR = "kicad_pcb.footprint[0].zone"
_EDGE_GRAPHIC_LOCATOR = "kicad_pcb.footprint[0].graphic"
_HOSTILE_ZONE_HEAD = "private_board_token_6f7b"
_HOSTILE_FILL_HEAD = "private_fill_token_3a91"

_ZONE_A = f"""
(zone locked
  (attr (teardrop))
  (connect_pads (clearance 0.2))
  (fill yes
    (mode polygon)
    (smoothing none)
    (island_removal_mode 0)
    (thermal_gap 0.3)
    (thermal_bridge_width 0.3)
    ({_HOSTILE_FILL_HEAD} "never publish me"))
  (filled_areas_thickness yes)
  (layer "F.Cu")
  (polygon (pts (xy 0 0) (xy 1 0) (xy 0 1)))
  ({_HOSTILE_ZONE_HEAD} "never publish me"))
"""

_ZONE_B = """
(zone "locked"
  (layers "F.Cu" "B.Cu")
  (keepout (tracks not_allowed))
  (placement (enabled no))
  (fill yes yes
    (mode hatch)
    (smoothing fillet)
    (island_removal_mode 2))
  (filled_areas_thickness no))
(zone
  (placement (enabled no)))
"""


def _board(marker: str, zones: str) -> bytes:
    return (f"(kicad_pcb (version 20240108) (marker {marker}) {zones})").encode()


def _entries(
    corpus: Path,
    *,
    zone_overrides: dict[int, str] | None = None,
) -> tuple[list[dict[str, str]], str]:
    markers = {
        0: "zone-container",
        1: "zone-fill",
        2: "footprint-zone",
        3: "footprint-zone",
        4: "footprint-zone",
        5: "edge-cuts",
    }
    zones = {0: _ZONE_A, 1: _ZONE_B, **(zone_overrides or {})}
    entries: list[dict[str, str]] = []
    for index in range(13):
        relative = f"board-{index:02d}.kicad_pcb"
        marker = f"{markers.get(index, 'other')}-{index:02d}"
        source = _board(marker, zones.get(index, '(zone (layer "F.Cu"))'))
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
    zone_overrides: dict[int, str] | None = None,
) -> tuple[Path, Path, str, str, str, tuple[masking.Snapshot, ...]]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(corpus, zone_overrides=zone_overrides)
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": masking.SCHEMA,
                "entries": entries,
                "fingerprint": fingerprint,
            }
        ),
        encoding="utf-8",
    )
    loaded, _ = masking.load_manifest(manifest)
    snapshots = tuple(masking.capture_snapshots(corpus, loaded, max_bytes=1_000_000))
    selected = snapshots[:2]
    return (
        corpus,
        manifest,
        fingerprint,
        census._selection_commitment(selected),
        copper_graphic_census._selection_commitment(selected),
        snapshots,
    )


def _diagnostic(code: str, message: str, locator: str) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=None,
        diagnostics=(SimpleNamespace(code=code, message=message, source_locator=locator),),
    )


def _converter(source: bytes, _settings: Settings) -> SimpleNamespace:
    if b"(marker zone-container-" in source:
        return _diagnostic(
            census.WALL_CODE,
            census.ROOT_ZONE_WALL_MESSAGE,
            _ZONE_CONTAINER_LOCATOR,
        )
    if b"(marker zone-fill-" in source:
        return _diagnostic(
            census.WALL_CODE,
            census.ROOT_ZONE_WALL_MESSAGE,
            _ZONE_FILL_LOCATOR,
        )
    if b"(marker footprint-zone-" in source:
        return _diagnostic(
            census.WALL_CODE,
            census.FOOTPRINT_ZONE_MESSAGE,
            _FOOTPRINT_ZONE_LOCATOR,
        )
    if b"(marker edge-cuts-" in source:
        return _diagnostic(
            census.WALL_CODE,
            census.EDGE_CUTS_GRAPHIC_MESSAGE,
            _EDGE_GRAPHIC_LOCATOR,
        )
    return _diagnostic(
        census.WALL_CODE,
        masking.ROOT_SEMANTIC_MESSAGE,
        "kicad_pcb.child[2]",
    )


def _settings(corpus: Path, **updates: Any) -> Settings:
    return Settings(
        workspace=corpus,
        max_board_bytes=1_000_000,
        **updates,
    )


def _measure(
    corpus: Path,
    manifest: Path,
    fingerprint: str,
    commitment: str | None,
    predecessor_commitment: str,
    *,
    converter: census.Converter | None = _converter,
    settings: Settings | None = None,
) -> dict[str, Any]:
    with (
        patch.object(census, "PREDECLARED_COHORT_FINGERPRINT", fingerprint),
        patch.object(
            census,
            "PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT",
            commitment,
        ),
        patch.object(
            copper_graphic_census,
            "PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT",
            predecessor_commitment,
        ),
    ):
        return census.measure(
            corpus,
            manifest,
            settings or _settings(corpus),
            converter=converter,
        )


def test_contract_freezes_the_kicad_9_and_10_grammars_before_measurement() -> None:
    assert census.SCHEMA == "copper-mcp/public-root-zone-field-census/v1"
    assert census.EXPECTED_CAPTURED == 13
    assert census.EXPECTED_PUBLIC == 10
    assert census.PREDECLARED_COHORT_FINGERPRINT == "sha256:bfec8210d6d4eb746ffdbfb3b70309ce"
    assert census.PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT is None
    assert census.SHAPE_BUCKETS == (
        "empty",
        "one_atom",
        "many_atoms",
        "one_child",
        "many_children",
        "mixed",
    )
    assert census.SHAPE_BUCKETS == census.setup_census.SHAPE_BUCKETS
    assert census.ROOT_ZONE_HEADS == {
        "attr",
        "connect_pads",
        "fill",
        "fill_segments",
        "filled_areas_thickness",
        "filled_polygon",
        "hatch",
        "keepout",
        "layer",
        "layers",
        "locked",
        "min_thickness",
        "name",
        "net",
        "net_name",
        "placement",
        "polygon",
        "priority",
        "property",
        "tstamp",
        "uuid",
    }
    assert census.FILL_CHILD_HEADS == {
        "arc_segments",
        "hatch_border_algorithm",
        "hatch_gap",
        "hatch_min_hole_area",
        "hatch_orientation",
        "hatch_smoothing_level",
        "hatch_smoothing_value",
        "hatch_thickness",
        "island_area_min",
        "island_removal_mode",
        "mode",
        "radius",
        "smoothing",
        "thermal_bridge_width",
        "thermal_gap",
    }


def test_measure_requires_the_exact_b137_partition_and_b136_membership(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)

    result = _measure(corpus, manifest, fingerprint, commitment, predecessor)

    assert result["source_census"]["b137_successor_partition"] == {
        "zone_container": 1,
        "zone_fill": 1,
        "footprint_zone": 3,
        "edge_cuts_graphic": 1,
        "other": 4,
    }
    assert result["source_census"]["same_cohort_as_b136"] is True
    assert result["aggregates"]["boards"] == 2
    assert result["aggregates"]["root_zone_count"] == 3


def test_the_fixed_point_walk_reaches_the_zone_wall_after_a_real_mask(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(
        tmp_path,
        zone_overrides={0: "(dimension (type aligned)) " + _ZONE_A},
    )

    def masked_then_zone(source: bytes, settings: Settings) -> SimpleNamespace:
        if b"(marker zone-container-00)" in source and b"(dimension" in source:
            return _diagnostic(
                census.WALL_CODE,
                masking.ROOT_DIMENSION_MESSAGE,
                "kicad_pcb.child[2]",
            )
        return _converter(source, settings)

    result = _measure(
        corpus,
        manifest,
        fingerprint,
        commitment,
        predecessor,
        converter=masked_then_zone,
    )

    assert result["source_census"]["b137_successor_partition_matches"] is True
    assert result["aggregates"]["boards"] == 2


def test_the_b129_cohort_fingerprint_is_an_execution_guard(tmp_path: Path) -> None:
    corpus, manifest, _fingerprint, commitment, predecessor, _ = _manifest(tmp_path)

    with pytest.raises(ValueError, match="cohort fingerprint does not match"):
        _measure(
            corpus,
            manifest,
            "sha256:" + "0" * 32,
            commitment,
            predecessor,
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("code", "syntax.invalid"),
        ("message", "different fixed sentence"),
        ("locator", "kicad_pcb.zone[0].fill.extra"),
    ],
)
def test_root_wall_code_message_and_locator_are_exact_selection_guards(
    field: str,
    replacement: str,
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)

    def drifted(source: bytes, settings: Settings) -> SimpleNamespace:
        result = _converter(source, settings)
        if b"(marker zone-fill-" not in source:
            return result
        diagnostic = result.diagnostics[0]
        values = {
            "code": diagnostic.code,
            "message": diagnostic.message,
            "locator": diagnostic.source_locator,
        }
        values[field] = replacement
        return _diagnostic(values["code"], values["message"], values["locator"])

    with pytest.raises(ValueError, match="B-137 successor partition drifted"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            predecessor,
            converter=drifted,
        )


def test_terminal_walk_must_agree_with_the_fixed_point_classifier(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)
    original = census._terminal_depth_and_class

    def disagree(
        source: bytes,
        settings: Settings,
        *,
        converter: census.Converter | None,
    ) -> tuple[int, str]:
        depth, successor = original(source, settings, converter=converter)
        return depth + 1, successor

    with patch.object(census, "_terminal_depth_and_class", disagree):
        with pytest.raises(ValueError, match="walk disagrees"):
            _measure(corpus, manifest, fingerprint, commitment, predecessor)


def test_a_same_count_membership_swap_fails_the_new_commitment(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, _predecessor, snapshots = _manifest(tmp_path)

    def swapped(source: bytes, settings: Settings) -> SimpleNamespace:
        if b"(marker zone-fill-01)" in source:
            return _diagnostic(
                census.WALL_CODE,
                census.FOOTPRINT_ZONE_MESSAGE,
                _FOOTPRINT_ZONE_LOCATOR,
            )
        if b"(marker footprint-zone-02)" in source:
            return _diagnostic(
                census.WALL_CODE,
                census.ROOT_ZONE_WALL_MESSAGE,
                _ZONE_FILL_LOCATOR,
            )
        return _converter(source, settings)

    swapped_predecessor = copper_graphic_census._selection_commitment((snapshots[0], snapshots[2]))
    with pytest.raises(ValueError, match="root-zone terminal membership drifted"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            swapped_predecessor,
            converter=swapped,
        )


def test_b136_predecessor_commitment_mismatch_fails_before_aggregation(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, commitment, _predecessor, _ = _manifest(tmp_path)

    with pytest.raises(ValueError, match="not B-136's two boards"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            "sha256:" + "0" * 64,
        )


def test_unassigned_selection_commitment_refuses_before_measurement(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, _commitment, predecessor, _ = _manifest(tmp_path)

    with pytest.raises(ValueError, match=r"root-zone.*unassigned"):
        _measure(corpus, manifest, fingerprint, None, predecessor)


def test_closed_other_and_value_partitions_are_total_and_non_echoing(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)

    result = _measure(corpus, manifest, fingerprint, commitment, predecessor)
    aggregates = result["aggregates"]
    zones = aggregates["root_zone_count"]

    assert aggregates["root_zone_children"]["occurrences"]["other"] == 1
    assert aggregates["fill_children"]["occurrences"]["other"] == 1
    assert aggregates["zone_kind"] == {
        "solid_candidate": 1,
        "keepout_only": 0,
        "placement_only": 1,
        "keepout_and_placement": 1,
        "duplicate_or_malformed": 0,
    }
    assert aggregates["direct_atoms"] == {
        "none": 1,
        "locked_once": 1,
        "locked_repeated": 0,
        "other_or_quoted": 1,
    }
    assert aggregates["fill_marker"] == {
        "fill_absent": 1,
        "marker_absent": 0,
        "yes_once": 1,
        "yes_repeated": 1,
        "other_or_quoted": 0,
        "duplicate_fill": 0,
    }
    assert aggregates["mode"] == {
        "absent": 1,
        "polygon": 1,
        "hatch": 1,
        "segment": 0,
        "invalid": 0,
    }
    assert aggregates["smoothing"] == {
        "absent": 1,
        "none": 1,
        "chamfer": 0,
        "fillet": 1,
        "invalid": 0,
    }
    assert aggregates["island_removal"] == {
        "absent": 1,
        "always_0": 1,
        "never_1": 0,
        "minimum_area_2": 1,
        "invalid": 0,
    }
    assert aggregates["filled_area_thickness"] == {
        "absent": 1,
        "yes": 1,
        "no": 1,
        "invalid": 0,
    }
    for name in (
        "zone_kind",
        "direct_atoms",
        "fill_marker",
        "mode",
        "smoothing",
        "island_removal",
        "filled_area_thickness",
    ):
        assert sum(aggregates[name].values()) == zones

    encoded = json.dumps(result, sort_keys=True)
    assert _HOSTILE_ZONE_HEAD not in encoded
    assert _HOSTILE_FILL_HEAD not in encoded
    assert "never publish me" not in encoded


def test_malformed_and_remaining_value_forms_stay_in_closed_buckets(tmp_path: Path) -> None:
    body = """
    (zone locked locked
      (keepout (tracks not_allowed))
      (fill)
      (filled_areas_thickness "yes"))
    (zone
      (placement (enabled no))
      (placement (enabled no))
      (fill yes (mode "hatch") (smoothing other) (island_removal_mode 3))
      (fill yes))
    (zone
      (fill "yes" (mode segment) (smoothing chamfer) (island_removal_mode 1)))
    """
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(
        tmp_path,
        zone_overrides={0: body, 1: "(zone (keepout (tracks allowed)))"},
    )

    result = _measure(corpus, manifest, fingerprint, commitment, predecessor)
    aggregates = result["aggregates"]

    assert aggregates["zone_kind"]["keepout_only"] == 2
    assert aggregates["zone_kind"]["duplicate_or_malformed"] == 1
    assert aggregates["direct_atoms"]["locked_repeated"] == 1
    assert aggregates["fill_marker"]["marker_absent"] == 1
    assert aggregates["fill_marker"]["duplicate_fill"] == 1
    assert aggregates["fill_marker"]["other_or_quoted"] == 1
    assert aggregates["mode"]["segment"] == 1
    assert aggregates["mode"]["invalid"] == 1
    assert aggregates["smoothing"]["chamfer"] == 1
    assert aggregates["smoothing"]["invalid"] == 1
    assert aggregates["island_removal"]["never_1"] == 1
    assert aggregates["island_removal"]["invalid"] == 1
    assert aggregates["filled_area_thickness"]["invalid"] == 1


def test_root_and_fill_shape_vocabularies_reach_all_six_buckets(tmp_path: Path) -> None:
    source = b"""(kicad_pcb
      (zone
        (attr)
        (connect_pads one)
        (hatch one two)
        (keepout (tracks not_allowed))
        (polygon (a 1) (b 2))
        (name one (child two))
        (fill yes
          (arc_segments)
          (mode polygon)
          (hatch_orientation one two)
          (hatch_border_algorithm (value one))
          (hatch_gap (a one) (b two))
          (thermal_gap one (child two)))))"""
    root = census._root(source, _settings(tmp_path))

    observation = census._observe(root)

    root_shapes = CounterKeyView(observation.root_shapes)
    fill_shapes = CounterKeyView(observation.fill_shapes)
    for shape in census.SHAPE_BUCKETS:
        assert root_shapes.has_suffix(f":{shape}"), shape
        assert fill_shapes.has_suffix(f":{shape}"), shape


class CounterKeyView:
    """Tiny assertion helper that keeps the test about published shape keys."""

    def __init__(self, values: Any) -> None:
        self._values = values

    def has_suffix(self, suffix: str) -> bool:
        return any(key.endswith(suffix) and count > 0 for key, count in self._values.items())


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (b"(not_a_board (zone))", "must be kicad_pcb"),
        (b"(kicad_pcb stray (zone))", "only child expressions"),
        (b"(kicad_pcb (version 1))", "at least one root zone"),
        (b'(kicad_pcb ("quoted-root-child" 1) (zone))', "unquoted symbolic head"),
    ],
)
def test_structurally_malformed_sources_fail_closed(
    source: bytes,
    message: str,
    tmp_path: Path,
) -> None:
    with pytest.raises(ValueError, match=message):
        census._root(source, _settings(tmp_path))


def test_a_quoted_zone_field_head_fails_instead_of_entering_other(tmp_path: Path) -> None:
    root = census._root(
        b'(kicad_pcb (zone ("private head" 1)))',
        _settings(tmp_path),
    )

    with pytest.raises(ValueError, match="unquoted symbolic head"):
        census._observe(root)


def test_published_projection_reconciliation_rejects_an_undeclared_bucket(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)
    original = census._bucket

    def leaky(head: str, vocabulary: frozenset[str]) -> str:
        if head == _HOSTILE_ZONE_HEAD and vocabulary is census.ROOT_ZONE_HEADS:
            return "undeclared_private_bucket"
        return original(head, vocabulary)

    with patch.object(census, "_bucket", leaky):
        with pytest.raises(ValueError, match="root-zone field buckets do not partition"):
            _measure(corpus, manifest, fingerprint, commitment, predecessor)


def test_source_change_after_capture_is_refused(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)
    original = masking.capture_snapshots

    def capture_then_mutate(*args: Any, **kwargs: Any) -> Any:
        snapshots = original(*args, **kwargs)
        (corpus / "board-00.kicad_pcb").write_bytes(b"(kicad_pcb (zone))")
        return snapshots

    with patch.object(masking, "capture_snapshots", capture_then_mutate):
        with pytest.raises(ValueError, match="source changed"):
            _measure(corpus, manifest, fingerprint, commitment, predecessor)


@pytest.mark.parametrize("flag", ["allow_apply", "allow_live_ipc", "allow_live_apply"])
def test_measure_refuses_every_write_or_live_authority(flag: str, tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(tmp_path)

    with pytest.raises(ValueError, match="read-only"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            predecessor,
            settings=_settings(corpus, **{flag: True}),
        )


def test_measure_is_deterministic_aggregate_only_and_source_preserving(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, predecessor, snapshots = _manifest(tmp_path)
    before = {path.name: path.read_bytes() for path in sorted(corpus.iterdir())}

    first = _measure(corpus, manifest, fingerprint, commitment, predecessor)
    second = _measure(corpus, manifest, fingerprint, commitment, predecessor)

    assert first == second
    assert commitment == census._selection_commitment(snapshots[:2])
    assert commitment != census._selection_commitment((snapshots[1], snapshots[0]))
    assert first["privacy"] == {
        "aggregate_only": True,
        "atom_values_committed": 0,
        "coordinates_committed": 0,
        "geometry_values_committed": 0,
        "board_identities_committed": 0,
        "board_paths_committed": 0,
        "board_digests_committed": 0,
        "board_bytes_committed": 0,
    }
    assert first["claim_scope"] == {
        "measurement_only": True,
        "root_zone_acceptance": False,
        "cached_fill_validation": False,
        "conversion_success": False,
        "board_ir_schema_change": False,
        "production_behavior_change": False,
    }
    encoded = json.dumps(first, sort_keys=True)
    for path_name in before:
        assert path_name not in encoded
    for snapshot in snapshots:
        assert snapshot.entry.digest not in encoded
    for identity in (f"opaque-{index:02d}" for index in range(13)):
        assert identity not in encoded
    assert {path.name: path.read_bytes() for path in sorted(corpus.iterdir())} == before


def test_real_main_refuses_the_unassigned_commitment_without_publishing(
    tmp_path: Path,
) -> None:
    corpus, manifest, _fingerprint, _commitment, _predecessor, _ = _manifest(tmp_path)
    output = tmp_path / "unassigned-result.json"
    argv = [
        str(Path(census.__file__)),
        "--corpus",
        str(corpus),
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ]

    assert census.PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT is None
    with (
        patch.object(sys, "argv", argv),
        patch.object(masking, "_git_state", return_value=("frozen-commit", False)),
        pytest.raises(ValueError, match=r"root-zone.*unassigned"),
    ):
        census.main()

    assert not output.exists()
    assert census.PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT is None


def test_main_honors_a_low_parse_budget_before_observation_or_publication(
    tmp_path: Path,
) -> None:
    geometry = (
        "(zone (polygon (pts " + " ".join(f"(xy {index} {index})" for index in range(128)) + ")) )"
    )
    corpus, manifest, fingerprint, commitment, predecessor, _ = _manifest(
        tmp_path,
        zone_overrides={0: geometry},
    )
    output = tmp_path / "budget-result.json"
    argv = [
        str(Path(census.__file__)),
        "--corpus",
        str(corpus),
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ]
    low_budget_settings = _settings(corpus, max_parse_tokens=32)

    def select_first_two(
        snapshots: tuple[masking.Snapshot, ...],
        **_kwargs: Any,
    ) -> tuple[tuple[masking.Snapshot, ...], dict[str, int]]:
        return tuple(snapshots[:2]), dict(census.PREDECLARED_SUCCESSOR_PARTITION)

    with (
        patch.object(sys, "argv", argv),
        patch.object(masking, "_git_state", return_value=("frozen-commit", False)),
        patch.object(census, "Settings", return_value=low_budget_settings),
        patch.object(census, "PREDECLARED_COHORT_FINGERPRINT", fingerprint),
        patch.object(census, "PREDECLARED_ROOT_ZONE_SELECTION_COMMITMENT", commitment),
        patch.object(
            copper_graphic_census,
            "PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT",
            predecessor,
        ),
        patch.object(census, "_select_root_zone_terminals", side_effect=select_first_two),
        patch.object(census, "_observe") as observe,
        patch.object(census.setup_census, "_write_output") as publish,
        pytest.raises(SExprError) as caught,
    ):
        census.main()

    assert caught.value.code == "budget.exceeded.tokens"
    observe.assert_not_called()
    publish.assert_not_called()
    assert not output.exists()


def test_main_uses_the_create_exclusive_sibling_output_boundary(
    tmp_path: Path,
) -> None:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    manifest = tmp_path / "manifest.json"
    manifest.write_text("{}", encoding="utf-8")
    output = tmp_path / "result.json"
    argv = [
        str(Path(census.__file__)),
        "--corpus",
        str(corpus),
        "--manifest",
        str(manifest),
        "--output",
        str(output),
    ]

    with (
        patch.object(sys, "argv", argv),
        patch.object(masking, "_git_state", return_value=("frozen-commit", False)),
        patch.object(census, "measure", return_value={"schema": census.SCHEMA}),
    ):
        assert census.main() == 0

    document = json.loads(output.read_text(encoding="utf-8"))
    assert document["schema"] == census.SCHEMA
    assert document["run_id"].startswith("sha256:")

    with (
        patch.object(sys, "argv", argv),
        patch.object(masking, "_git_state", return_value=("frozen-commit", False)),
        patch.object(census, "measure", return_value={"schema": census.SCHEMA}),
    ):
        with pytest.raises(SystemExit, match="new path"):
            census.main()
