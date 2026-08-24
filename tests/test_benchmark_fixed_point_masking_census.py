"""Focused safety and determinism tests for the fixed-point masking census."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from copper_mcp.config import Settings
from scripts import benchmark_fixed_point_masking_census as census


def _entries(corpus: Path, *, count: int = 13) -> list[dict[str, str]]:
    entries = []
    for index in range(count):
        relative = f"board-{index:02d}.kicad_pcb"
        source = b"(kicad_pcb (version 20240108) (dimension (gr_line)))"
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


def _manifest(tmp_path: Path) -> tuple[Path, Path]:
    corpus = tmp_path / "corpus"
    corpus.mkdir()
    entries, fingerprint = _entries(corpus)
    path = tmp_path / "manifest.json"
    path.write_text(
        json.dumps({"schema": census.SCHEMA, "entries": entries, "fingerprint": fingerprint}),
        encoding="utf-8",
    )
    return corpus, path


def _settings(corpus: Path) -> Settings:
    return Settings(workspace=corpus, max_board_bytes=1_000_000)


def _converter(source: bytes, _settings: Settings) -> SimpleNamespace:
    if b"(dimension (gr_line))" in source:
        return SimpleNamespace(
            snapshot=None,
            diagnostics=(
                SimpleNamespace(
                    code="unsupported.construct",
                    source_locator="kicad_pcb.child[1]",
                    message=census.ROOT_DIMENSION_MESSAGE,
                ),
            ),
        )
    return SimpleNamespace(snapshot=object(), diagnostics=())


def test_manifest_is_closed_and_requires_exact_10_plus_3_cohort(tmp_path: Path) -> None:
    corpus, manifest = _manifest(tmp_path)
    entries, fingerprint = census.load_manifest(manifest)
    assert len(entries) == 13
    assert sum(item.visibility == "public" for item in entries) == 10
    assert sum(item.visibility == "private" for item in entries) == 3
    assert fingerprint == census._manifest_fingerprint(entries)
    raw = json.loads(manifest.read_text())
    raw["entries"][0]["id"] = raw["entries"][1]["id"]
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="identity"):
        census.load_manifest(manifest)
    assert corpus.exists()


def test_manifest_rejects_malformed_schema_and_unsafe_or_duplicate_entries(tmp_path: Path) -> None:
    _corpus, manifest = _manifest(tmp_path)
    raw = json.loads(manifest.read_text())
    raw["entries"][0]["path"] = "../secret.kicad_pcb"
    manifest.write_text(json.dumps(raw))
    with pytest.raises(ValueError, match="unsafe"):
        census.load_manifest(manifest)


def test_external_predeclared_fingerprint_is_required_before_measurement(tmp_path: Path) -> None:
    corpus, manifest = _manifest(tmp_path)
    with pytest.raises(ValueError, match="predeclared"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint="sha256:" + "0" * 32,
            converter=_converter,
        )


def test_capture_rejects_digest_mismatch_oversized_and_symlink(tmp_path: Path) -> None:
    corpus, manifest = _manifest(tmp_path)
    entries, _ = census.load_manifest(manifest)
    bad = list(entries)
    bad[0] = census.CorpusEntry(
        bad[0].identity, bad[0].visibility, bad[0].relative, "sha256:" + "0" * 64
    )
    with pytest.raises(ValueError, match="digest"):
        census.capture_snapshots(corpus, bad, max_bytes=1_000_000)
    with pytest.raises(ValueError, match="budget"):
        census.capture_snapshots(corpus, entries, max_bytes=census.MAX_SOURCE_BYTES + 1)


def test_masking_uses_cst_span_and_stops_when_unknown_or_unmaskable(tmp_path: Path) -> None:
    corpus, _manifest_path = _manifest(tmp_path)
    settings = _settings(corpus)
    source = b"(kicad_pcb (version 20240108) (dimension (gr_line)))"

    def semantic_converter(data: bytes, _settings: Settings) -> SimpleNamespace:
        if b"(dimension (gr_line))" in data:
            return SimpleNamespace(
                snapshot=None,
                diagnostics=(
                    SimpleNamespace(
                        code="unsupported.construct",
                        source_locator="kicad_pcb.child[1]",
                        message=census.ROOT_DIMENSION_MESSAGE,
                    ),
                ),
            )
        return SimpleNamespace(snapshot=object(), diagnostics=())

    assert census.classify_source(source, settings, converter=semantic_converter) == (
        1,
        "converted",
    )
    unknown = lambda _source, _settings: SimpleNamespace(  # noqa: E731
        snapshot=None,
        diagnostics=(SimpleNamespace(code="unknown.blocker", source_locator="byte:1"),),
    )
    assert census.classify_source(source, settings, converter=unknown) == (0, "unmaskable")


def test_measure_is_aggregate_only_deterministic_and_source_preserving(tmp_path: Path) -> None:
    corpus, manifest = _manifest(tmp_path)
    _entries_loaded, fingerprint = census.load_manifest(manifest)
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
    assert result["aggregates"]["overall"]["terminal"] == {"converted": 13}
    assert result["aggregates"]["public"]["terminal"] == {"converted": 10}
    assert result["aggregates"]["private"]["terminal"] == {"converted": 3}
    assert result["aggregates"]["overall"]["terminal_blocker"] == {"none": 13}
    serialized = json.dumps(result, sort_keys=True)
    for forbidden in ("board-00", "kicad_pcb", "bad-0", "geometry", "digest"):
        assert forbidden not in serialized
    assert result["source_hashes_unchanged"] is True


@pytest.mark.parametrize(
    ("source", "message", "locator"),
    [
        (
            b"(kicad_pcb (version 20240108) (gr_arc (layer Edge.Cuts)))",
            census.EDGE_CURVE_MESSAGE,
            "kicad_pcb.graphic",
        ),
        (
            b'(kicad_pcb (version 20240108) (gr_text "x" (layer F.Cu)))',
            census.COPPER_TEXT_MESSAGE,
            "kicad_pcb.graphic",
        ),
        (
            b"(kicad_pcb (version 20240108) (dimension (gr_line)))",
            census.ROOT_DIMENSION_MESSAGE,
            "kicad_pcb.child[1]",
        ),
    ],
)
def test_each_closed_semantic_mask_class_reaches_conversion(
    tmp_path: Path, source: bytes, message: str, locator: str
) -> None:
    settings = _settings(tmp_path)

    def convert(data: bytes, _settings: Settings) -> SimpleNamespace:
        if data == source:
            return SimpleNamespace(
                snapshot=None,
                diagnostics=(
                    SimpleNamespace(
                        code="unsupported.construct",
                        message=message,
                        source_locator=locator,
                    ),
                ),
            )
        return SimpleNamespace(snapshot=object(), diagnostics=())

    assert census.classify_source(source, settings, converter=convert) == (1, "converted")


@pytest.mark.parametrize(
    ("source", "message"),
    [
        (
            b"(kicad_pcb (version 20240108) (gr_arc (layer Edge.Cuts)) "
            b"(gr_circle (layer Edge.Cuts)))",
            census.EDGE_CURVE_MESSAGE,
        ),
        (
            b'(kicad_pcb (version 20240108) (gr_text "a" (layer F.Cu)) '
            b'(gr_text_box "b" (layer In4.Cu)))',
            census.COPPER_TEXT_MESSAGE,
        ),
        (
            b"(kicad_pcb (version 20240108) (dimension (gr_line)) (dimension (gr_arc)))",
            census.ROOT_DIMENSION_MESSAGE,
        ),
    ],
)
def test_one_pass_masks_the_whole_predeclared_root_class(
    tmp_path: Path, source: bytes, message: str
) -> None:
    settings = _settings(tmp_path)

    locator = (
        "kicad_pcb.child[1]" if message == census.ROOT_DIMENSION_MESSAGE else "kicad_pcb.graphic"
    )

    def convert(data: bytes, _settings: Settings) -> SimpleNamespace:
        if data == source:
            return SimpleNamespace(
                snapshot=None,
                diagnostics=(
                    SimpleNamespace(
                        code="unsupported.construct",
                        message=message,
                        source_locator=locator,
                    ),
                ),
            )
        return SimpleNamespace(snapshot=object(), diagnostics=())

    assert census.classify_source(source, settings, converter=convert) == (1, "converted")


@pytest.mark.parametrize(
    "source",
    [
        b"(kicad_pcb (version 20240108) (footprint (gr_arc (layer Edge.Cuts))))",
        b'(kicad_pcb (version 20240108) (gr_text "x" (layer F.Cu) (layer B.Cu)))',
        b"(kicad_pcb (version 20240108) (gr_arc (layer F.Cu)))",
        b"(kicad_pcb (version 20240108) (unknown))",
    ],
)
def test_nested_multilayer_and_unknown_maskers_stop_unmaskable(
    tmp_path: Path, source: bytes
) -> None:
    settings = _settings(tmp_path)

    def refuse(_data: bytes, _settings: Settings) -> SimpleNamespace:
        return SimpleNamespace(
            snapshot=None,
            diagnostics=(
                SimpleNamespace(
                    code="unsupported.construct",
                    message=census.EDGE_CURVE_MESSAGE,
                    source_locator="kicad_pcb.graphic",
                ),
            ),
        )

    assert census.classify_source(source, settings, converter=refuse) == (0, "unmaskable")


@pytest.mark.parametrize(
    ("code", "message", "locator", "expected"),
    [
        (
            "unsupported.construct",
            census.SETUP_SEMANTIC_MESSAGE,
            "kicad_pcb.setup",
            "setup_semantics",
        ),
        (
            "unsupported.construct",
            census.ROOT_SEMANTIC_MESSAGE,
            "kicad_pcb.child[7]",
            "root_semantic_construct",
        ),
        ("syntax.invalid", census.LAYER_ARITY_MESSAGE, "kicad_pcb.graphic", "graphic_layer_arity"),
        (
            "unsupported.construct",
            census.COPPER_LAYER_KIND_MESSAGE,
            "kicad_pcb.layers",
            "copper_layer_kind",
        ),
        (
            "unsupported.topology",
            census.DISJOINT_OUTLINE_MESSAGE,
            "kicad_pcb",
            "disjoint_outline_topology",
        ),
        (
            "unsupported.topology",
            census.COURTYARD_TOPOLOGY_MESSAGE,
            "kicad_pcb.footprint[1].courtyard[2]",
            "courtyard_topology",
        ),
    ],
)
def test_terminal_blockers_are_closed_non_echoing_classes(
    code: str, message: str, locator: str, expected: str
) -> None:
    result = SimpleNamespace(
        diagnostics=(SimpleNamespace(code=code, message=message, source_locator=locator),)
    )
    assert census._terminal_blocker(result) == expected


def test_measure_rejects_source_mutation(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, manifest = _manifest(tmp_path)
    original = census.read_workspace_file
    reads = {"count": 0}

    def reread_then_mutate(*args: object, **kwargs: object) -> object:
        value = original(*args, **kwargs)
        reads["count"] += 1
        if reads["count"] == 14:
            (corpus / "board-00.kicad_pcb").write_bytes(b"(mutated)")
        return value

    monkeypatch.setattr(census, "read_workspace_file", reread_then_mutate)
    with pytest.raises(ValueError, match="changed"):
        census.measure(
            corpus,
            manifest,
            _settings(corpus),
            expected_fingerprint=census.load_manifest(manifest)[1],
            converter=_converter,
        )


def test_pass_budget_is_bounded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    corpus, _manifest_path = _manifest(tmp_path)
    settings = _settings(corpus)
    calls = 0

    def never_progress(source: bytes, _settings: Settings) -> SimpleNamespace:
        nonlocal calls
        calls += 1
        return SimpleNamespace(
            snapshot=None,
            diagnostics=(SimpleNamespace(code="unsupported.construct", source_locator="byte:0"),),
        )

    assert census.classify_source(b"(kicad_pcb (foo))", settings, converter=never_progress) == (
        0,
        "unmaskable",
    )
    assert calls == 1
