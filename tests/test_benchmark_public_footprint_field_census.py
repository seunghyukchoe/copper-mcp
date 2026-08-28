"""Focused safety and determinism tests for the public footprint-field census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.adapters.sexpr import parse_sexpr
from copper_mcp.config import Settings
from scripts import benchmark_fixed_point_masking_census as masking
from scripts import benchmark_public_footprint_field_census as census
from scripts import benchmark_public_setup_field_census as setup_census

_WALL_LOCATOR = "kicad_pcb.footprint[0].unsupported"


def _footprint(body: str) -> str:
    return f'(footprint "L:R" (layer "F.Cu") (uuid "u") (at 0 0) {body})'


def _board(marker: str, *, body: str = '(sheetfile "a.kicad_sch")', root_extra: str = "") -> bytes:
    return (
        f"(kicad_pcb (version 20240108) {root_extra} {_footprint(body)} (marker {marker}))"
    ).encode()


def _entries(
    corpus: Path,
    *,
    wall_count: int = 6,
    body_override: str | None = None,
) -> tuple[list[dict[str, str]], str]:
    entries: list[dict[str, str]] = []
    for index in range(13):
        relative = f"board-{index:02d}.kicad_pcb"
        if index < wall_count:
            marker = f"wall-{index:02d}"
            body = (
                body_override
                if index == 0 and body_override is not None
                else '(sheetfile "a.kicad_sch")'
            )
            source = _board(marker, body=body)
        else:
            source = _board(f"other-{index:02d}", body='(descr "plain")')
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
    wall_count: int = 6,
    body_override: str | None = None,
) -> tuple[Path, Path, str, str, str]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(corpus, wall_count=wall_count, body_override=body_override)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema": masking.SCHEMA, "entries": entries, "fingerprint": fingerprint}),
        encoding="utf-8",
    )
    loaded, _ = masking.load_manifest(path)
    snapshots = masking.capture_snapshots(corpus, loaded, max_bytes=1_000_000)
    selected = snapshots[:wall_count]
    return (
        corpus,
        path,
        fingerprint,
        census._selection_commitment(selected),
        setup_census._selection_commitment(selected),
    )


def _settings(corpus: Path) -> Settings:
    return Settings(workspace=corpus, max_board_bytes=1_000_000)


def _diagnostic(code: str, message: str, locator: str) -> SimpleNamespace:
    return SimpleNamespace(
        snapshot=None,
        diagnostics=(SimpleNamespace(code=code, source_locator=locator, message=message),),
    )


def _converter(source: bytes, _settings: Settings) -> SimpleNamespace:
    if b"(marker wall-" in source:
        return _diagnostic(
            census.FOOTPRINT_WALL_CODE,
            census.FOOTPRINT_WALL_MESSAGE,
            _WALL_LOCATOR,
        )
    return _diagnostic(
        "unsupported.construct",
        masking.ROOT_SEMANTIC_MESSAGE,
        "kicad_pcb.child[2]",
    )


def _measure(
    corpus: Path,
    manifest: Path,
    fingerprint: str,
    commitment: str | None,
    setup_commitment: str | None,
    *,
    settings: Settings | None = None,
    converter: census.Converter | None = _converter,
) -> dict[str, Any]:
    with (
        patch.object(census, "PREDECLARED_COHORT_FINGERPRINT", fingerprint),
        patch.object(census, "PREDECLARED_FOOTPRINT_SELECTION_COMMITMENT", commitment),
        patch.object(
            setup_census,
            "PREDECLARED_SETUP_SELECTION_COMMITMENT",
            setup_commitment,
        ),
    ):
        return census.measure(
            corpus,
            manifest,
            _settings(corpus) if settings is None else settings,
            converter=converter,
        )


def test_the_frozen_accepted_vocabulary_is_contained_by_the_adapters() -> None:
    """Containment, not equality, for the reason B-130's own guard had to learn.

    The accompanying decision widens `_FOOTPRINT_METADATA_HEADS` past this frozen set. Left a
    mirror, a rerun would report a different refused surface under the same schema name and the
    artifact would stop being replayable. Widening is therefore allowed and narrowing is not.
    """

    assert census.ACCEPTED_FOOTPRINT_HEADS <= census.kicad_board_ir._FOOTPRINT_METADATA_HEADS
    assert not census.REFUSED_FOOTPRINT_HEADS & census.ACCEPTED_FOOTPRINT_HEADS
    assert census.OTHER not in census.REFUSED_FOOTPRINT_HEADS


def test_a_widened_adapter_vocabulary_still_runs_the_census(tmp_path: Path) -> None:
    """Runs `measure`'s guard rather than comparing two constants beside it.

    A mutant restoring an equality check survives a test that only asserts containment between
    the constants, because the assertion holds without the guard ever executing. B-131 recorded
    exactly that survivor on the sibling instrument.
    """

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    widened = census.kicad_board_ir._FOOTPRINT_METADATA_HEADS | {"a_newly_accepted_head"}

    with patch.object(census.kicad_board_ir, "_FOOTPRINT_METADATA_HEADS", widened):
        result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    assert result["aggregates"]["boards"] == census.EXPECTED_FOOTPRINT_TERMINALS


def test_a_narrowed_adapter_vocabulary_fails_the_census(tmp_path: Path) -> None:
    """An absence is evidence only if the observation could have reported a presence."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    narrowed = census.ACCEPTED_FOOTPRINT_HEADS - {"descr"}

    with patch.object(census.kicad_board_ir, "_FOOTPRINT_METADATA_HEADS", narrowed):
        with pytest.raises(ValueError, match="accepted footprint vocabulary drifted"):
            _measure(corpus, manifest, fingerprint, commitment, setup_commitment)


def test_head_classification_follows_the_adapters_branch_order_not_its_tables() -> None:
    """`property` is in the accepted table *and* on the layer-aware path; the path wins.

    The adapter tests `head.startswith("fp_") or head in {"property", "point"}` before it consults
    `_FOOTPRINT_METADATA_HEADS`, so a `property` never reaches the container refusal. A census
    that bucketed by table membership would under-count the layer-aware surface and could never
    report the refused one correctly.
    """

    assert "property" in census.ACCEPTED_FOOTPRINT_HEADS
    assert census._classify_head("property") == "layer_routed"
    assert census._classify_head("point") == "layer_routed"
    assert census._classify_head("fp_line") == "layer_routed"
    assert census._classify_head("zone") == "zone"
    assert census._classify_head("pad") == "accepted"
    assert census._classify_head("sheetfile") == "refused"
    assert census._classify_head("a_head_kicad_cannot_write") == census.OTHER


def test_an_unknown_refused_head_lands_in_other_without_echoing_it(tmp_path: Path) -> None:
    """The bucket that made B-131's refutation legible, exercised here in its own right."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path,
        body_override='(a_head_kicad_cannot_write "secret-value")',
    )
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    refused = result["aggregates"]["refused_fields"]
    assert refused["occurrences"][census.OTHER] == 1
    assert refused["board_presence"][census.OTHER] == 1
    assert result["aggregates"]["refused_field_classes"]["board_presence"][census.OTHER] == 1
    assert "secret-value" not in json.dumps(result)
    assert "a_head_kicad_cannot_write" not in json.dumps(result)


def test_every_declared_refused_head_is_reported_even_at_zero(tmp_path: Path) -> None:
    """A vocabulary derived from the grammar reports absence; one derived from the boards cannot."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    occurrences = result["aggregates"]["refused_fields"]["occurrences"]
    assert set(occurrences) == census.REFUSED_FOOTPRINT_HEADS | {census.OTHER}
    assert occurrences["zone_connect"] == 0
    assert occurrences["sheetfile"] == 6


@pytest.mark.parametrize(
    ("payload", "expected"),
    [
        # Well formed, and the inert value.
        ("(clearance 0)", ("clearance_zero",)),
        ("(clearance 0.0)", ("clearance_zero",)),
        ("(clearance -0.0)", ("clearance_zero",)),
        ("(clearance +0)", ("clearance_zero",)),
        # Well formed, and the value that must be refused.
        ("(clearance 0.2)", ("clearance_nonzero",)),
        ("(clearance -0.2)", ("clearance_nonzero",)),
        ("(clearance 1e3)", ("clearance_nonzero",)),
        # Malformed -- every one of these was silently *absent* before #226's review, and so was
        # indistinguishable in the artifact from a well-formed non-zero clearance.
        ("(clearance garbage)", ("clearance_invalid",)),
        ("(clearance nan)", ("clearance_invalid",)),
        ("(clearance NaN)", ("clearance_invalid",)),
        ("(clearance inf)", ("clearance_invalid",)),
        ("(clearance -inf)", ("clearance_invalid",)),
        ("(clearance Infinity)", ("clearance_invalid",)),
        ('(clearance "0")', ("clearance_invalid",)),
        ('(clearance "0.2")', ("clearance_invalid",)),
        ("(clearance)", ("clearance_invalid",)),
        ("(clearance 0 0)", ("clearance_invalid",)),
        ("(clearance 0.2 locked)", ("clearance_invalid",)),
        ("(clearance (zone_connect 0))", ("clearance_invalid",)),
        ("(clearance 0x10)", ("clearance_invalid",)),
        ("(clearance .)", ("clearance_invalid",)),
        # Well formed zone connections.
        ("(zone_connect 0)", ("zone_connect_detaching",)),
        ("(zone_connect 1)", ("zone_connect_attaching",)),
        ("(zone_connect 2)", ("zone_connect_attaching",)),
        ("(zone_connect 3)", ("zone_connect_attaching",)),
        # Outside the written domain, or malformed -- also silently absent before the review.
        ("(zone_connect -1)", ("zone_connect_invalid",)),
        ("(zone_connect 4)", ("zone_connect_invalid",)),
        ("(zone_connect 2.0)", ("zone_connect_invalid",)),
        ("(zone_connect nan)", ("zone_connect_invalid",)),
        ("(zone_connect garbage)", ("zone_connect_invalid",)),
        ('(zone_connect "2")', ("zone_connect_invalid",)),
        ("(zone_connect)", ("zone_connect_invalid",)),
        ("(zone_connect 1 2)", ("zone_connect_invalid",)),
        ("(zone_connect (members 1))", ("zone_connect_invalid",)),
        # A head with no partition emits nothing at all, which is a different thing from
        # `_invalid` and must stay different.
        ("(sheetfile 0)", ()),
        ('(sheetname "x")', ()),
    ],
)
def test_payload_predicates_are_closed_in_both_directions(
    payload: str, expected: tuple[str, ...], tmp_path: Path
) -> None:
    """`0` is inert for `clearance` and detaching for `zone_connect`; the census must not conflate.

    The two heads sit in the same class and their safe value sets are disjoint, so a single
    "is it zero" predicate would have called the one dangerous `zone_connect` value safe.

    Every malformation class is asserted to produce a **named** bucket rather than nothing. The
    first version of this function returned `()` for all of them, which the artifact could not
    distinguish from a well-formed value that simply was not the interesting one.
    """

    limits = masking.parse_limits_for(Settings(workspace=tmp_path))
    node = parse_sexpr(payload.encode(), limits)
    head = node.head
    assert head is not None
    assert census._payload_predicates(head, node) == expected
    # Whatever a partitioned head emits, it is exactly one declared bucket -- never zero, and
    # never a name outside the closed vocabulary.
    if head in census.PARTITIONED_PAYLOAD_HEADS:
        assert len(expected) == 1
        assert expected[0] in census.PARTITIONED_PAYLOAD_HEADS[head]
    assert set(expected) <= set(census.PAYLOAD_PREDICATES)


def test_a_detaching_zone_connect_is_counted_apart_from_an_attaching_one(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path,
        body_override="(zone_connect 0)",
    )
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    predicates = result["aggregates"]["payload_predicate_occurrences"]
    assert predicates == {
        "clearance_invalid": 0,
        "clearance_nonzero": 0,
        "clearance_zero": 0,
        "zone_connect_attaching": 0,
        "zone_connect_detaching": 1,
        "zone_connect_invalid": 0,
    }


def test_a_footprint_group_is_censused_through_its_own_nested_vocabulary(tmp_path: Path) -> None:
    """A container counted without a grammar for its children is an unread container."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path,
        body_override='(group "g" (uuid "gu") (members "m1" "m2") (surprise 1))',
    )
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    group = result["aggregates"]["footprint_group"]
    assert group["node_count"] == 1
    assert group["field_occurrences"]["uuid"] == 1
    assert group["field_occurrences"]["members"] == 1
    assert group["field_occurrences"][census.OTHER] == 1
    assert group["field_shape_occurrences"]["members:many_atoms"] == 1
    assert "surprise" not in json.dumps(result)


def test_measure_requires_an_assigned_selection_commitment(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, _, setup_commitment = _manifest(tmp_path)

    with pytest.raises(ValueError, match="selection commitment is unassigned"):
        _measure(corpus, manifest, fingerprint, None, setup_commitment)


def test_measure_rejects_a_malformed_selection_commitment(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, _, setup_commitment = _manifest(tmp_path)

    with pytest.raises(ValueError, match="selection commitment is malformed"):
        _measure(corpus, manifest, fingerprint, "sha256:nothex", setup_commitment)


def test_measure_rejects_same_count_selection_membership_drift(tmp_path: Path) -> None:
    """The swap `EXPECTED_FOOTPRINT_TERMINALS` cannot see: same count, different six."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    loaded, _ = masking.load_manifest(manifest)
    snapshots = masking.capture_snapshots(corpus, loaded, max_bytes=1_000_000)
    swapped = [*snapshots[1:6], snapshots[6]]
    assert len(swapped) == census.EXPECTED_FOOTPRINT_TERMINALS

    drifted = census._selection_commitment(swapped)
    assert drifted != commitment

    with pytest.raises(ValueError, match="footprint-terminal membership drifted"):
        _measure(corpus, manifest, fingerprint, drifted, setup_commitment)


def test_measure_requires_the_setup_censuss_own_six_boards(tmp_path: Path) -> None:
    """Continuity with B-130 is a check, not a sentence in a ledger row.

    The selection rule here differs from the setup census's — B-129's blocker vocabulary has no
    member for this wall — so "the same six boards" has to be proved rather than asserted.
    """

    corpus, manifest, fingerprint, commitment, _ = _manifest(tmp_path)
    loaded, _ = masking.load_manifest(manifest)
    snapshots = masking.capture_snapshots(corpus, loaded, max_bytes=1_000_000)
    other_six = setup_census._selection_commitment(snapshots[1:7])

    with pytest.raises(ValueError, match="not the setup census's six boards"):
        _measure(corpus, manifest, fingerprint, commitment, other_six)


def test_selection_requires_the_footprint_wall_and_not_merely_the_other_bucket(
    tmp_path: Path,
) -> None:
    """`other` is a catch-all; selecting on it alone would admit any future unnamed terminal."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)

    def _other_but_not_the_wall(source: bytes, _settings: Settings) -> SimpleNamespace:
        if b"(marker wall-" in source:
            return _diagnostic(
                "unsupported.construct",
                "some other unnamed terminal",
                "kicad_pcb.footprint[0].unsupported",
            )
        return _diagnostic(
            "unsupported.construct",
            masking.ROOT_SEMANTIC_MESSAGE,
            "kicad_pcb.child[2]",
        )

    unnamed = _other_but_not_the_wall(b"(marker wall-00)", _settings(corpus))
    assert masking._terminal_blocker(unnamed) == census.OTHER

    with pytest.raises(ValueError, match="footprint-terminal population drifted"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            setup_commitment,
            converter=_other_but_not_the_wall,
        )


def test_a_wall_diagnostic_at_a_non_footprint_locator_is_not_selected(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)

    def _wall_elsewhere(source: bytes, _settings: Settings) -> SimpleNamespace:
        return _diagnostic(
            census.FOOTPRINT_WALL_CODE,
            census.FOOTPRINT_WALL_MESSAGE,
            "kicad_pcb.setup",
        )

    with pytest.raises(ValueError, match="footprint-terminal population drifted"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            setup_commitment,
            converter=_wall_elsewhere,
        )


def test_private_entries_are_never_selected_even_when_they_hit_the_wall(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)

    def _everything_is_the_wall(source: bytes, _settings: Settings) -> SimpleNamespace:
        return _diagnostic(
            census.FOOTPRINT_WALL_CODE,
            census.FOOTPRINT_WALL_MESSAGE,
            _WALL_LOCATOR,
        )

    with pytest.raises(ValueError, match="expected 6, got 10"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            setup_commitment,
            converter=_everything_is_the_wall,
        )


def test_measure_is_deterministic_aggregate_only_and_source_preserving(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    before = {path.name: path.read_bytes() for path in sorted(corpus.iterdir())}

    first = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)
    second = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    assert first == second
    assert first["privacy"] == {
        "aggregate_only": True,
        "atom_values_committed": 0,
        "board_identities_committed": 0,
        "board_paths_committed": 0,
        "board_digests_committed": 0,
        "board_bytes_committed": 0,
    }
    assert first["source_census"]["same_cohort_as_setup_census"] is True
    assert first["claim_scope"]["footprint_field_acceptance"] is False
    assert first["claim_scope"]["pad_field_surface_measured"] is False

    encoded = json.dumps(first)
    for name in before:
        assert name not in encoded
    for entry_id in (f"opaque-{index:02d}" for index in range(13)):
        assert entry_id not in encoded
    assert {path.name: path.read_bytes() for path in sorted(corpus.iterdir())} == before


def test_source_change_after_capture_is_refused(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    original = masking.capture_snapshots

    def _capture_then_mutate(*args: Any, **kwargs: Any) -> Any:
        snapshots = original(*args, **kwargs)
        (corpus / "board-00.kicad_pcb").write_bytes(b"(kicad_pcb (version 20240108))")
        return snapshots

    with patch.object(masking, "capture_snapshots", _capture_then_mutate):
        with pytest.raises(ValueError, match="source changed"):
            _measure(corpus, manifest, fingerprint, commitment, setup_commitment)


def test_measure_is_read_only(tmp_path: Path) -> None:
    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(tmp_path)
    settings = Settings(workspace=corpus, max_board_bytes=1_000_000, allow_apply=True)

    with pytest.raises(ValueError, match="read-only"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            commitment,
            setup_commitment,
            settings=settings,
        )


def test_a_source_without_a_footprint_is_refused(tmp_path: Path) -> None:
    corpus, _manifest_path, _fingerprint, _commitment, _setup = _manifest(tmp_path)
    settings = _settings(corpus)
    with pytest.raises(ValueError, match="at least one footprint"):
        census._footprints(b"(kicad_pcb (version 20240108))", settings)


@pytest.mark.parametrize(
    "source",
    [
        b'(kicad_pcb (version 20240108) ("quoted" 1))',
        b"(kicad_pcb (version 20240108) bare_atom)",
        b'(not_kicad_pcb (footprint "L:R"))',
    ],
)
def test_structurally_unexpected_roots_are_refused(source: bytes, tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        census._footprints(source, Settings(workspace=tmp_path))


def test_cli_parser_refuses_a_fingerprint_override() -> None:
    parser = census._build_parser()
    with pytest.raises(SystemExit):
        parser.parse_args(
            [
                "--corpus",
                "c",
                "--manifest",
                "m.json",
                "--output",
                "o.json",
                "--fingerprint",
                "sha256:0",
            ]
        )


def test_a_walk_that_disagrees_with_the_classifier_is_refused(tmp_path: Path) -> None:
    """The two loops must agree, or the diagnostic being read is not the terminal being classified.

    `_classify_source_detail` is the unmodified B-129 instrument and returns a closed blocker class
    with no diagnostic text; the walk re-derives the terminal from the same module's primitives so
    the wall can be identified by name. Composing two loops is only sound while they land in the
    same place, so the depths are compared rather than assumed — and a non-deterministic converter
    is what proves the comparison is live.
    """

    corpus = tmp_path / "corpus"
    corpus.mkdir()
    curve = '(gr_circle (center 0 0) (end 1 0) (layer "Edge.Cuts") (width 0.1))'
    entries: list[dict[str, str]] = []
    for index in range(13):
        relative = f"board-{index:02d}.kicad_pcb"
        source = _board(f"wall-{index:02d}", root_extra=curve)
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
    fingerprint = "sha256:" + hashlib.sha256(material).hexdigest()[:32]
    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps({"schema": masking.SCHEMA, "entries": entries, "fingerprint": fingerprint}),
        encoding="utf-8",
    )

    served: set[bytes] = set()

    def _curve_once_then_the_wall(source: bytes, _settings: Settings) -> SimpleNamespace:
        """Maskable on a source's first sighting, terminal afterwards.

        The classifier sees the curve, masks it and terminates one pass deeper; the walk, running
        second, is served the wall immediately and terminates at depth 0.
        """

        if source not in served:
            served.add(source)
            return _diagnostic(
                "unsupported.construct",
                masking.EDGE_CURVE_MESSAGE,
                "kicad_pcb.graphic",
            )
        return _diagnostic(
            census.FOOTPRINT_WALL_CODE,
            census.FOOTPRINT_WALL_MESSAGE,
            _WALL_LOCATOR,
        )

    with pytest.raises(ValueError, match="walk disagrees with the fixed-point classifier"):
        _measure(
            corpus,
            manifest,
            fingerprint,
            census.PREDECLARED_FOOTPRINT_SELECTION_COMMITMENT,
            setup_census.PREDECLARED_SETUP_SELECTION_COMMITMENT,
            converter=_curve_once_then_the_wall,
        )


def test_the_declared_predicate_vocabulary_is_exactly_the_partitions(tmp_path: Path) -> None:
    """No bucket may exist outside a partition, and no partition may name an undeclared bucket."""

    declared = set(census.PAYLOAD_PREDICATES)
    partitioned = {
        bucket for buckets in census.PARTITIONED_PAYLOAD_HEADS.values() for bucket in buckets
    }

    assert declared == partitioned
    assert len(census.PAYLOAD_PREDICATES) == len(declared)
    # Every partitioned head is a head the refused vocabulary can actually report, or its buckets
    # could never be reconciled against an occurrence count.
    assert set(census.PARTITIONED_PAYLOAD_HEADS) <= census.REFUSED_FOOTPRINT_HEADS


@pytest.mark.parametrize(
    ("body", "bucket"),
    [
        ("(clearance garbage)", "clearance_invalid"),
        ("(clearance nan)", "clearance_invalid"),
        ('(clearance "0")', "clearance_invalid"),
        ("(clearance 0.2)", "clearance_nonzero"),
        ("(clearance 0)", "clearance_zero"),
        ("(zone_connect garbage)", "zone_connect_invalid"),
        ("(zone_connect -1)", "zone_connect_invalid"),
    ],
)
def test_a_malformed_copper_interacting_payload_is_counted_not_absent(
    body: str, bucket: str, tmp_path: Path
) -> None:
    """The artifact must be able to say "malformed", or its zeros are not evidence.

    Before #226's review a malformed `clearance` produced no predicate at all, so
    `clearance_zero: 0` was consistent with a board carrying `(clearance garbage)` *and* with one
    carrying a well-formed non-zero value. The reader could not tell, and neither could the
    instrument.
    """

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path, body_override=body
    )
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    predicates = result["aggregates"]["payload_predicate_occurrences"]
    assert set(predicates) == set(census.PAYLOAD_PREDICATES)
    assert predicates[bucket] == 1
    assert sum(predicates.values()) == 1


def test_every_partition_reconciles_against_its_heads_occurrence_count(tmp_path: Path) -> None:
    """The invariant that makes a zero readable: buckets must account for every occurrence."""

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path, body_override="(clearance garbage) (zone_connect 2)"
    )
    result = _measure(corpus, manifest, fingerprint, commitment, setup_commitment)

    aggregates = result["aggregates"]
    occurrences = aggregates["refused_fields"]["occurrences"]
    predicates = aggregates["payload_predicate_occurrences"]

    for head, buckets in census.PARTITIONED_PAYLOAD_HEADS.items():
        assert sum(predicates[bucket] for bucket in buckets) == occurrences[head]
    assert occurrences["clearance"] == 1
    assert occurrences["zone_connect"] == 1


def test_a_partition_that_stops_covering_its_head_fails_the_run(tmp_path: Path) -> None:
    """An absence is evidence only if the observation could report a presence.

    The guard is exercised rather than asserted beside: a classifier that silently drops a payload
    is exactly the defect #226's review found, so the run must fail rather than publish a zero.
    """

    corpus, manifest, fingerprint, commitment, setup_commitment = _manifest(
        tmp_path, body_override="(clearance garbage)"
    )

    classify = census._payload_predicates

    def _silently_drops_the_malformed(head: str, node: Any) -> tuple[str, ...]:
        original = classify(head, node)
        return () if original == ("clearance_invalid",) else original

    with patch.object(census, "_payload_predicates", _silently_drops_the_malformed):
        with pytest.raises(ValueError, match="do not partition their head's occurrences"):
            _measure(corpus, manifest, fingerprint, commitment, setup_commitment)
