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


def _big_poly(layer: str = "F.Cu") -> str:
    """A 4 mm square copper polygon -- large enough that a union error crosses a ratio bucket."""

    return (
        "(fp_poly (pts (xy 0 0) (xy 4 0) (xy 4 4) (xy 0 4) (xy 0 0)) "
        f'(stroke (width 0) (type solid)) (fill yes) (layer "{layer}") (uuid "p"))'
    )


def _carrier(uuid: str, at: str, body: str) -> str:
    return f'(footprint "L:R" (layer "F.Cu") (uuid "{uuid}") (at {at}) {body})'


def _board(marker: str, *, body: str = _COPPER_POLY, footprints: str | None = None) -> bytes:
    layers = '(layers (0 "F.Cu" signal) (2 "B.Cu" signal))'
    outline = '(gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts")) '
    outline += '(gr_line (start 10 10) (end 0 10) (layer "Edge.Cuts"))'
    return (
        f"(kicad_pcb (version 20240108) {layers} {outline} "
        f"{footprints if footprints is not None else _footprint(body)} (marker {marker}))"
    ).encode()


def _entries(
    corpus: Path,
    *,
    bodies: dict[int, str] | None = None,
    footprints: dict[int, str] | None = None,
) -> tuple[list[dict], str]:
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
        source = _board(
            marker,
            body=overrides.get(index, _COPPER_POLY),
            footprints=(footprints or {}).get(index),
        )
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
    bodies: dict[int, str] | None = None,
    footprints: dict[int, str] | None = None,
) -> tuple[Path, Path, str, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(corpus, bodies=bodies, footprints=footprints)
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


# --- Frame of reference (Codex P1 on #230) ----------------------------------------------------
#
# `pts` coordinates are footprint-LOCAL. The first version of this instrument unioned every
# envelope box without ever applying its carrier's `(at x y [angle])`, so two polygons drawn at
# the same local coordinates in different footprints were counted as one region of board area they
# do not share, and the published cost figure was computed in a frame no board is drawn in.


def _two_carriers(at_a: str, at_b: str) -> str:
    return _carrier("a", at_a, _big_poly()) + " " + _carrier("b", at_b, _big_poly())


def _union_bucket(result: dict[str, Any]) -> str:
    buckets = result["aggregates"]["envelope_cost"]["board_envelope_union_over_board_bounding_box"]
    live = [name for name, count in buckets.items() if count]
    assert len(live) == 1, buckets
    return live[0]


def test_envelopes_from_two_carriers_are_unioned_in_board_coordinates(tmp_path: Path) -> None:
    """Two carriers whose *local* frames coincide but whose *board* frames do not.

    Both footprints draw the same 4 mm square at the same local coordinates. Placed, they cover
    two disjoint 16 mm^2 regions of a 100 mm^2 board -- about 32%. Unioned in local coordinates
    they collapse onto each other and read about 16%, which is a different ratio bucket. The
    assertion is on the *published* figure rather than on the helper, so a transform applied
    anywhere other than before the union still fails.
    """

    placed = {0: _two_carriers("0 0", "5 5"), 1: _two_carriers("0 0", "5 5")}
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path, footprints=placed)

    result = _measure(corpus, manifest, fingerprint, commitment)

    assert _union_bucket(result) == "lt_0_50"
    # Both carriers are counted, and the union is not one box.
    assert result["aggregates"]["carriers"]["footprints_with_copper_graphics"] == 4


def test_two_carriers_at_one_place_do_share_their_area(tmp_path: Path) -> None:
    """The control: when the board really does put them on top of each other, the union merges.

    Without this, the test above would pass for an instrument that simply double-counted every
    envelope instead of unioning them.
    """

    stacked = {0: _two_carriers("0 0", "0 0"), 1: _two_carriers("0 0", "0 0")}
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path, footprints=stacked)

    result = _measure(corpus, manifest, fingerprint, commitment)

    assert _union_bucket(result) == "lt_0_25"


def test_a_quarter_turn_places_the_envelope_by_kicads_own_convention() -> None:
    """`(x, y) -> (y, -x)`, not the `(-y, x)` a y-up reading gives -- the two differ by a mirror.

    KiCad's `(at x y angle)` angle is counter-clockwise *on screen* while stored board coordinates
    have y increasing downward, so a positive angle is clockwise in the stored values. Getting this
    backwards mirrors every rotated carrier's envelope to the wrong side of its origin, which no
    area-only assertion would catch.
    """

    box = (1_000, 2_000, 3_000, 5_000)

    assert census._place_box(box, 0, 0, 0, "0") == box
    assert census._place_box(box, 0, 0, 1, "90") == (2_000, -3_000, 5_000, -1_000)
    assert census._place_box(box, 0, 0, 2, "180") == (-3_000, -5_000, -1_000, -2_000)
    assert census._place_box(box, 0, 0, 3, "270") == (-5_000, 1_000, -2_000, 3_000)
    # The origin translates the placed box and nothing else.
    assert census._place_box(box, 100, 200, 1, "90") == (2_100, -2_800, 5_100, -800)


def test_a_non_quarter_angle_is_bounded_outward_rather_than_approximated() -> None:
    """The census claims a cost *bound*, so a rotated box is answered by a box that contains it."""

    box = (0, 0, 4_000, 2_000)
    placed = census._place_box(box, 0, 0, None, "30")

    import math

    radians = math.radians(30.0)
    cos_t, sin_t = math.cos(radians), math.sin(radians)
    for x, y in ((0, 0), (4_000, 0), (4_000, 2_000), (0, 2_000)):
        rx, ry = x * cos_t + y * sin_t, -x * sin_t + y * cos_t
        assert placed[0] <= rx <= placed[2]
        assert placed[1] <= ry <= placed[3]
    # And it is an over-approximation of the rotated rectangle, not a claim of equality.
    assert (placed[2] - placed[0]) * (placed[3] - placed[1]) > 4_000 * 2_000


def test_the_carrier_rotation_partition_is_total_and_published(tmp_path: Path) -> None:
    """A census that places envelopes has to say whether the transform it applied was exact."""

    mixed = {
        0: _carrier("a", "0 0 90", _big_poly()) + " " + _carrier("b", "5 5 30", _big_poly()),
        1: _carrier("a", "0 0 90", _big_poly()) + " " + _carrier("b", "5 5 30", _big_poly()),
    }
    corpus, manifest, fingerprint, commitment = _manifest(tmp_path, footprints=mixed)

    result = _measure(corpus, manifest, fingerprint, commitment)
    rotations = result["aggregates"]["carriers"]["rotations"]

    assert rotations == {"quarter_turn": 2, "other_angle": 2, "absent_or_malformed": 0}
    assert (
        sum(rotations.values())
        == result["aggregates"]["carriers"]["footprints_with_copper_graphics"]
    )


def test_the_per_primitive_aggregates_do_not_depend_on_the_frame(tmp_path: Path) -> None:
    """Counts and grammar predicates are per-primitive, so placing a carrier must not move them.

    This is the check that lets B-136's head, layer, payload and geometry numbers be carried across
    the frame fix rather than re-argued: they are computed from the polygon alone.
    """

    at_origin = {0: _carrier("a", "0 0", _big_poly()), 1: _carrier("a", "0 0", _big_poly())}
    moved = {0: _carrier("a", "7 3 90", _big_poly()), 1: _carrier("a", "7 3 90", _big_poly())}

    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    corpus_a, manifest_a, fp_a, commit_a = _manifest(tmp_path / "a", footprints=at_origin)
    corpus_b, manifest_b, fp_b, commit_b = _manifest(tmp_path / "b", footprints=moved)
    first = _measure(corpus_a, manifest_a, fp_a, commit_a)["aggregates"]
    second = _measure(corpus_b, manifest_b, fp_b, commit_b)["aggregates"]

    for key in (
        "layer_routed_heads",
        "layer_classes",
        "copper_layer_heads",
        "copper_payload",
        "copper_polygon_children",
        "copper_polygon_geometry",
    ):
        assert first[key] == second[key], key
    # The envelope cost is exactly what may move, and the shape ratio moves with the frame only
    # because a quarter turn swaps the box's sides -- its *area* does not.
    assert (
        first["envelope_cost"]["area_over_bounding_box"]
        == (second["envelope_cost"]["area_over_bounding_box"])
    )


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
