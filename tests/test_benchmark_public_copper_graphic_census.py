"""Focused safety and determinism tests for the public copper-graphic census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.config import Settings
from scripts import benchmark_fixed_point_masking_census as masking
from scripts import benchmark_public_copper_graphic_census as census

_GRAPHIC_LOCATOR = "kicad_pcb.footprint[0].graphic"
_ZONE_LOCATOR = "kicad_pcb.footprint[0].zone"

# One filled, explicitly stroked copper polygon -- the shape B-136 measured 56 of.
_COPPER_POLY = (
    "(fp_poly (pts (xy 0 0) (xy 1 0) (xy 1 1) (xy 0 1) (xy 0 0)) "
    '(stroke (width 0.1) (type solid)) (fill yes) (layer "F.Cu") (uuid "p"))'
)
_SILK_LINE = '(fp_line (start 0 0) (end 1 0) (stroke (width 0.1) (type solid)) (layer "F.SilkS"))'


def _footprint(body: str) -> str:
    return f'(footprint "L:R" (layer "F.Cu") (uuid "u") (at 0 0) {body})'


def _board(marker: str, *, body: str = _COPPER_POLY) -> bytes:
    layers = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal))'
    outline = '(gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts")) '
    outline += '(gr_line (start 10 10) (end 0 10) (layer "Edge.Cuts"))'
    return (
        f"(kicad_pcb (version 20240108) {layers} {outline} {_footprint(body)} (marker {marker}))"
    ).encode()


def _entries(corpus: Path, *, bodies: dict[int, str] | None = None) -> tuple[list[dict], str]:
    """Thirteen entries whose markers drive the stub converter's terminal for each board."""

    overrides = bodies or {}
    markers = {
        0: "graphic",
        1: "graphic",
        2: "zone",
        3: "zone",
        4: "zone",
        5: "edge",
    }
    entries: list[dict[str, str]] = []
    for index in range(13):
        relative = f"board-{index:02d}.kicad_pcb"
        marker = f"{markers.get(index, 'other')}-{index:02d}"
        source = _board(marker, body=overrides.get(index, _COPPER_POLY))
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
    tmp_path: Path, *, bodies: dict[int, str] | None = None
) -> tuple[Path, Path, str, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(corpus, bodies=bodies)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema": masking.SCHEMA, "entries": entries, "fingerprint": fingerprint}),
        encoding="utf-8",
    )
    loaded, _ = masking.load_manifest(path)
    snapshots = masking.capture_snapshots(corpus, loaded, max_bytes=1_000_000)
    return corpus, path, fingerprint, census._selection_commitment(snapshots[:2])


def _diagnostic(code: str, message: str, locator: str) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=None,
        diagnostics=(SimpleNamespace(code=code, source_locator=locator, message=message),),
    )


def _converter(source: bytes, _settings: Settings) -> SimpleNamespace:
    if b"(marker graphic-" in source:
        return _diagnostic(census.WALL_CODE, census.COPPER_GRAPHIC_MESSAGE, _GRAPHIC_LOCATOR)
    if b"(marker zone-" in source:
        return _diagnostic(census.WALL_CODE, census.FOOTPRINT_ZONE_MESSAGE, _ZONE_LOCATOR)
    if b"(marker edge-" in source:
        return _diagnostic(census.WALL_CODE, census.EDGE_CUTS_GRAPHIC_MESSAGE, _GRAPHIC_LOCATOR)
    return _diagnostic("unsupported.construct", masking.ROOT_SEMANTIC_MESSAGE, "kicad_pcb.child[2]")


def _measure(
    corpus: Path,
    manifest: Path,
    fingerprint: str,
    commitment: str | None,
    *,
    converter: census.Converter | None = _converter,
) -> dict[str, Any]:
    with (
        patch.object(census, "PREDECLARED_COHORT_FINGERPRINT", fingerprint),
        patch.object(census, "PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT", commitment),
    ):
        return census.measure(
            corpus,
            manifest,
            Settings(workspace=corpus, max_board_bytes=1_000_000),
            converter=converter,
        )


def test_the_census_measures_the_two_copper_graphic_terminals(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    result = _measure(corpus, manifest, fingerprint, commitment)

    assert result["aggregates"]["boards"] == census.EXPECTED_COPPER_GRAPHIC_TERMINALS
    assert result["aggregates"]["copper_layer_heads"]["fp_poly"] == 2
    assert result["source_census"]["b133_successor_partition"] == {
        "copper_graphic": 2,
        "footprint_zone": 3,
        "edge_cuts_graphic": 1,
        "other": 4,
    }


def test_the_b133_successor_partition_is_required_before_anything_is_aggregated(
    tmp_path: Path,
) -> None:
    """The continuity link to B-133, and it has to be a *guard* rather than a reported number.

    A census that merely published the partition would keep aggregating over a drifted population
    and leave a reader to notice. This one refuses, so a re-derived corpus or a changed adapter
    cannot quietly produce an artifact that describes different boards under the same schema name.
    """

    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    def shifted(source: bytes, settings: Settings) -> SimpleNamespace:
        # One of the three zone boards now stops at the Edge.Cuts graphic instead: 2/2/2, not
        # 2/3/1.
        if b"(marker zone-02)" in source:
            return _diagnostic(census.WALL_CODE, census.EDGE_CUTS_GRAPHIC_MESSAGE, _GRAPHIC_LOCATOR)
        return _converter(source, settings)

    with pytest.raises(ValueError, match="successor partition drifted"):
        _measure(corpus, manifest, fingerprint, commitment, converter=shifted)


def test_a_membership_swap_at_the_same_count_fails_the_frozen_commitment(
    tmp_path: Path,
) -> None:
    """`EXPECTED_COPPER_GRAPHIC_TERMINALS` alone cannot catch two-for-two substitution."""

    corpus, manifest, fingerprint, _ = _manifest(tmp_path)
    wrong = "sha256:" + "0" * 64

    with pytest.raises(ValueError, match="membership drifted"):
        _measure(corpus, manifest, fingerprint, wrong)


def test_every_partition_is_reconciled_against_the_population_it_partitions(
    tmp_path: Path,
) -> None:
    """The #226 review lesson, enforced rather than described.

    A partition with a silent gap publishes a zero that reads as "measured and absent" when it
    means "not classified". Each of these sums is checked by `measure` itself; this asserts the
    same equalities from outside so a mutant that deletes the check has something to fail.
    """

    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    result = _measure(corpus, manifest, fingerprint, commitment)
    aggregates = result["aggregates"]

    layer_routed = sum(aggregates["layer_routed_heads"]["occurrences"].values())
    assert sum(aggregates["layer_classes"].values()) == layer_routed

    copper = sum(aggregates["copper_layer_heads"].values())
    assert aggregates["layer_classes"]["single_copper"] == copper
    for partition in aggregates["copper_payload"].values():
        assert sum(partition.values()) == copper

    polygons = aggregates["copper_layer_heads"]["fp_poly"]
    geometry = aggregates["copper_polygon_geometry"]
    for key in ("vertex_count", "convexity", "simplicity", "vertex_distinctness"):
        assert sum(geometry[key].values()) == polygons
    assert sum(aggregates["envelope_cost"]["area_over_bounding_box"].values()) == polygons


def test_a_gap_in_a_payload_partition_fails_the_run(tmp_path: Path) -> None:
    """Runs `measure`'s reconciliation rather than comparing two sums beside it."""

    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    with patch.object(census, "_fill_bucket", lambda node: "fill_not_in_the_vocabulary"):
        with pytest.raises(ValueError, match="fill buckets do not partition"):
            _measure(corpus, manifest, fingerprint, commitment)


def test_a_bucket_that_never_reaches_the_artifact_fails_the_run(tmp_path: Path) -> None:
    """The reconciliation the payload partitions alone do not reach.

    `_closed_counts` silently drops any key outside its vocabulary, so a classifier returning an
    undeclared bucket publishes a partition of zeros. For most partitions the population total is
    computed elsewhere and the shortfall shows up as a partition failure. The `fp_poly` child
    grammar has no such second witness -- its total *is* its own projection -- so without the
    raw-against-projection check a child head outside the vocabulary would vanish from the
    artifact with nothing to notice. This drives that check specifically, which is why the mutant
    that disables it does not survive.
    """

    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)
    original = census._bucket

    def leaky(head: str, vocabulary: frozenset[str]) -> str:
        if vocabulary is census.POLY_CHILD_HEADS and head == "fill":
            return "a_bucket_nobody_declared"
        return original(head, vocabulary)

    with patch.object(census, "_bucket", leaky):
        with pytest.raises(ValueError, match="poly_child buckets do not partition"):
            _measure(corpus, manifest, fingerprint, commitment)


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ("*.Cu", "multi_copper"),
        ("F&B.Cu", "multi_copper"),
        ("In7.Cu", "absent_or_malformed"),
        ("F.SilkS", "non_routing"),
        ("Edge.Cuts", "edge_cuts"),
        ("F.CrtYd", "courtyard"),
    ],
)
def test_the_layer_partition_separates_the_wildcards_from_one_declared_copper_layer(
    tmp_path: Path, layer: str, expected: str
) -> None:
    """A `Segment` names one layer, so a wildcard is a different modelling question.

    `In7.Cu` is a real spelling this fixture does not declare, and it must not be counted as
    copper the census could have bounded.
    """

    body = _COPPER_POLY.replace('(layer "F.Cu")', f'(layer "{layer}")')
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path, bodies={0: body, 1: body})

    result = _measure(corpus, manifest, fingerprint, commitment)

    assert result["aggregates"]["layer_classes"][expected] == 2
    assert result["aggregates"]["layer_classes"]["single_copper"] == 0
    assert result["aggregates"]["copper_layer_heads"]["fp_poly"] == 0


def test_a_curved_polygon_side_is_partitioned_out_rather_than_read_as_vertices(
    tmp_path: Path,
) -> None:
    """A curved side bulges outside the hull of the listed points, so it is a different question.

    Reading its control points as vertices would let the census report an envelope cost for a
    shape the envelope does not contain, which is the one error this bucket exists to prevent.
    """

    body = _COPPER_POLY.replace("(xy 1 0)", "(arc (start 1 0) (mid 1.5 0.5) (end 1 1))", 1)
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path, bodies={0: body, 1: body})

    result = _measure(corpus, manifest, fingerprint, commitment)

    payload = result["aggregates"]["copper_payload"]["pts"]
    assert payload["pts_with_curved_child"] == 2
    assert payload["pts_xy_only"] == 0


def test_a_documentation_layer_graphic_is_counted_and_never_called_copper(
    tmp_path: Path,
) -> None:
    """The zeros only mean something if the same kinds are seen elsewhere on the same boards."""

    corpus, manifest, fingerprint, commitment = _manifest(
        tmp_path, bodies={0: _COPPER_POLY + _SILK_LINE, 1: _COPPER_POLY + _SILK_LINE}
    )

    result = _measure(corpus, manifest, fingerprint, commitment)
    aggregates = result["aggregates"]

    assert aggregates["layer_routed_heads"]["occurrences"]["fp_line"] == 2
    assert aggregates["copper_layer_heads"]["fp_line"] == 0
    assert aggregates["layer_classes"]["non_routing"] == 2


def test_no_ratio_coordinate_or_board_byte_reaches_the_artifact(tmp_path: Path) -> None:
    """Aggregate-only, one step stricter than the sibling censuses: ratios become buckets."""

    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    result = _measure(corpus, manifest, fingerprint, commitment)

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for value in node:
                walk(value)
        else:
            assert not isinstance(node, float), "a ratio must reach the artifact as a bucket"

    walk(result["aggregates"])
    assert result["privacy"] == {
        "aggregate_only": True,
        "atom_values_committed": 0,
        "board_bytes_committed": 0,
        "board_digests_committed": 0,
        "board_identities_committed": 0,
        "board_paths_committed": 0,
        "coordinates_committed": 0,
        "ratios_committed": 0,
    }


def test_the_census_refuses_to_run_with_any_write_authority(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path)

    with (
        patch.object(census, "PREDECLARED_COHORT_FINGERPRINT", fingerprint),
        patch.object(census, "PREDECLARED_COPPER_GRAPHIC_SELECTION_COMMITMENT", commitment),
    ):
        with pytest.raises(ValueError, match="read-only"):
            census.measure(
                corpus,
                manifest,
                Settings(workspace=corpus, max_board_bytes=1_000_000, allow_apply=True),
                converter=_converter,
            )


def test_an_unassigned_selection_commitment_fails_rather_than_measuring(
    tmp_path: Path,
) -> None:
    corpus, manifest, fingerprint, _ = _manifest(tmp_path)

    with pytest.raises(ValueError, match="unassigned"):
        _measure(corpus, manifest, fingerprint, None)
