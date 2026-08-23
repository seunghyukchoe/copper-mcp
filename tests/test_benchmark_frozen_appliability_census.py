from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from copper_mcp.security import WorkspaceViolationError
from scripts import benchmark_frozen_appliability_census as census


def _make_corpus(tmp_path: Path, count: int = 20) -> tuple[Path, list[str]]:
    corpus = tmp_path / "corpus"
    (corpus / "phono-v2" / "pcb").mkdir(parents=True)
    for index in range(count):
        target = corpus / ("phono-v2/pcb" if index < 4 else "other") / f"board-{index}.kicad_pcb"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(f"board-{index}".encode())
    superseded = [
        "phono-v2/pcb/superseded-a.kicad_pcb",
        "phono-v2/pcb/superseded-b.kicad_pcb",
    ]
    for name in superseded:
        (corpus / name).write_bytes(b"old")
    return corpus, superseded


def test_selection_applies_history_derived_and_exact_superseded_exclusions(tmp_path: Path) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    for name in superseded:
        path = corpus / name
        path.write_bytes(b"old")
    (corpus / ".history").mkdir()
    (corpus / ".history" / "old.kicad_pcb").write_bytes(b"history")
    (corpus / "other" / "routed-source.kicad_pcb").write_bytes(b"derived")
    selected = census.select_frozen_corpus(corpus, superseded_phono_v2=superseded)
    assert len(selected) == 18
    assert not any(path.name.startswith("superseded") for path in selected)


def test_selection_fails_closed_on_wrong_count_or_superseded_shape(tmp_path: Path) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=19)
    with pytest.raises(ValueError, match="18"):
        census.select_frozen_corpus(corpus, superseded_phono_v2=superseded)
    with pytest.raises(ValueError, match="exactly two"):
        census.select_frozen_corpus(corpus, superseded_phono_v2=superseded[:1])
    with pytest.raises(ValueError, match="phono-v2/pcb"):
        census.select_frozen_corpus(
            corpus, superseded_phono_v2=[*superseded[:1], "other/old.kicad_pcb"]
        )
    missing_corpus, _ = _make_corpus(tmp_path / "missing", count=18)
    with pytest.raises(ValueError, match="must exist"):
        census.select_frozen_corpus(
            missing_corpus,
            superseded_phono_v2=[
                "phono-v2/pcb/missing-a.kicad_pcb",
                "phono-v2/pcb/missing-b.kicad_pcb",
            ],
        )


def test_snapshot_digest_and_aggregate_are_deterministic(tmp_path: Path) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    snapshots = census._snapshot(
        census.select_frozen_corpus(corpus, superseded_phono_v2=superseded),
        corpus,
        max_bytes=1024,
    )
    assert all(
        item.digest == __import__("hashlib").sha256(item.source).hexdigest() for item in snapshots
    )
    first = "".join(f"{item.relative}:{item.digest}\n" for item in snapshots)
    second = "".join(f"{item.relative}:{item.digest}\n" for item in snapshots)
    assert first == second


def test_route_gate_uses_production_identity_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    class Conversion:
        snapshot = object()

    monkeypatch.setattr(census, "_require_native_geometry_identities", lambda _: None)
    assert census._route_gate(Conversion()) == "appliable"


def test_route_gate_preserves_typed_source_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    class Conversion:
        snapshot = object()

    def refuse(_: object) -> None:
        raise census.KiCadRoutePatchError("private geometry")

    monkeypatch.setattr(census, "_require_native_geometry_identities", refuse)
    assert census._route_gate(Conversion()) == "route_identity_refused"


def _placement_conversion() -> SimpleNamespace:
    footprint = SimpleNamespace(id="footprint:one")
    return SimpleNamespace(
        snapshot=SimpleNamespace(content=SimpleNamespace(footprints=[footprint]))
    )


def test_placement_gate_accepts_production_render(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def source_bound(intent, source, relative_path, board_revision, settings, **kwargs):
        captured.update(
            source=source,
            relative_path=relative_path,
            board_revision=board_revision,
            intent=intent,
            settings=settings,
        )
        return SimpleNamespace(status="previewed", candidate=object())

    monkeypatch.setattr(census, "_preview_placement_source", source_bound)
    monkeypatch.setattr(
        census, "render_kicad_placement_candidate_board", lambda *args, **kwargs: None
    )
    monkeypatch.setattr(census, "parse_limits_for", lambda settings: object())
    assert (
        census._placement_gate(
            census.Snapshot(
                Path("b"),
                "b.kicad_pcb",
                b"source",
                __import__("hashlib").sha256(b"source").hexdigest(),
            ),
            _placement_conversion(),
            SimpleNamespace(max_placement_subjects=64, max_placement_rules=256),
        )
        == "appliable"
    )
    assert captured["source"] == b"source"
    assert captured["relative_path"] == "b.kicad_pcb"


def test_placement_gate_classifies_no_candidate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        census,
        "_preview_placement_source",
        lambda *args, **kwargs: SimpleNamespace(status="refused", candidate=None),
    )
    assert (
        census._placement_gate(
            census.Snapshot(Path("b"), "b.kicad_pcb", b"source", ""),
            _placement_conversion(),
            SimpleNamespace(max_placement_subjects=64, max_placement_rules=256),
        )
        == "placement_no_candidate"
    )


def test_placement_gate_redacts_kicad_refusal_and_unexpected_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        census,
        "_preview_placement_source",
        lambda *args, **kwargs: SimpleNamespace(status="previewed", candidate=object()),
    )
    monkeypatch.setattr(
        census,
        "render_kicad_placement_candidate_board",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            census.KiCadPlacementPatchError("secret path")
        ),
    )
    monkeypatch.setattr(census, "parse_limits_for", lambda settings: object())
    snapshot = census.Snapshot(
        Path("b"), "b.kicad_pcb", b"source", __import__("hashlib").sha256(b"source").hexdigest()
    )
    settings = SimpleNamespace(max_placement_subjects=64, max_placement_rules=256)
    assert census._placement_gate(snapshot, _placement_conversion(), settings) == (
        "placement_source_preservation_refused"
    )
    monkeypatch.setattr(
        census,
        "render_kicad_placement_candidate_board",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("secret")),
    )
    assert (
        census._placement_gate(snapshot, _placement_conversion(), settings) == "measurement_error"
    )


def test_measurement_keeps_route_result_when_placement_fails_and_detects_source_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    conversion = SimpleNamespace(snapshot=None)
    monkeypatch.setattr(census, "_convert", lambda *args: conversion)
    mutation_count = 0

    def route_and_mutate(conversion):
        nonlocal mutation_count
        mutation_count += 1
        if mutation_count == 19:
            (corpus / "other" / "board-4.kicad_pcb").write_bytes(b"mutated")
        return "appliable"

    monkeypatch.setattr(census, "_route_gate", route_and_mutate)
    monkeypatch.setattr(census, "_placement_gate", lambda *args: "measurement_error")
    expected = census._fingerprint(
        census._snapshot(
            census.select_frozen_corpus(corpus, superseded_phono_v2=superseded),
            corpus,
            max_bytes=1024,
        )
    )
    result = census.measure_frozen_corpus(
        corpus,
        SimpleNamespace(
            max_board_bytes=1024,
            allow_apply=False,
            allow_live_ipc=False,
            allow_live_apply=False,
        ),
        superseded=superseded,
        expected_fingerprint=expected,
    )
    assert result["route_gate"] == {"appliable": 18}
    assert result["placement_gate"] == {"measurement_error": 18}
    with pytest.raises(RuntimeError, match="changed"):
        census.measure_frozen_corpus(
            corpus,
            SimpleNamespace(
                max_board_bytes=1024,
                allow_apply=False,
                allow_live_ipc=False,
                allow_live_apply=False,
            ),
            superseded=superseded,
            expected_fingerprint=expected,
        )


def test_output_is_aggregate_and_self_digest_is_verifiable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    output = tmp_path / "report.json"
    monkeypatch.setattr(census, "_git_state", lambda root: ("commit", False))
    monkeypatch.setattr(
        census,
        "measure_frozen_corpus",
        lambda *args, **kwargs: {
            "cohort_count": 18,
            "source_hashes_unchanged": True,
            "corpus_fingerprint": "sha256:opaque",
            "route_gate": {"appliable": 18},
            "placement_gate": {"appliable": 18},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--corpus",
            str(corpus),
            "--output",
            str(output),
            "--superseded",
            superseded[0],
            "--superseded",
            superseded[1],
        ],
    )
    assert census.main() == 0
    report = json.loads(output.read_text())
    assert not any(key in report for key in ("boards", "paths", "board_digests"))
    assert all(str(corpus) not in json.dumps(value) for value in report.values())
    payload = dict(report)
    digest = payload.pop("run_id")
    assert (
        digest
        == "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )


def test_alternate_existing_exclusion_pair_cannot_reuse_the_frozen_fingerprint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    expected = census._fingerprint(
        census._snapshot(
            census.select_frozen_corpus(corpus, superseded_phono_v2=superseded),
            corpus,
            max_bytes=1024,
        )
    )
    for name in superseded:
        (corpus / name).unlink()
    (corpus / "phono-v2/pcb/board-0.kicad_pcb").unlink()
    (corpus / "other/replacement.kicad_pcb").write_bytes(b"replacement")
    alternate = [
        "phono-v2/pcb/alternate-a.kicad_pcb",
        "phono-v2/pcb/alternate-b.kicad_pcb",
    ]
    for name in alternate:
        (corpus / name).write_bytes(b"alternate")
    monkeypatch.setattr(census, "_convert", lambda *args: pytest.fail("gates must not run"))
    with pytest.raises(RuntimeError, match="fingerprint"):
        census.measure_frozen_corpus(
            corpus,
            SimpleNamespace(
                max_board_bytes=1024,
                allow_apply=False,
                allow_live_ipc=False,
                allow_live_apply=False,
            ),
            superseded=alternate,
            expected_fingerprint=expected,
        )


def test_output_inside_corpus_is_refused(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "runner",
            "--corpus",
            str(corpus),
            "--output",
            str(corpus / "report.json"),
            "--superseded",
            superseded[0],
            "--superseded",
            superseded[1],
        ],
    )
    with pytest.raises(SystemExit, match="outside"):
        census.main()


def test_committed_result_is_bound_redacted_and_self_digested() -> None:
    root = Path(__file__).resolve().parents[1]
    report = json.loads(
        (
            root / "benchmarks/results/capability/2026-08-24-frozen-appliability-census-v1.json"
        ).read_text()
    )
    assert report["cohort_count"] == 18
    assert report["dirty"] is False
    assert report["commit"] == "dc095c4d4735f9cdd8bf1ddb03fb229fb665ffed"
    assert report["corpus_fingerprint"] == census.PREDECLARED_CORPUS_FINGERPRINT
    assert report["source_hashes_unchanged"] is True
    assert report["route_gate"] == {
        "appliable": 5,
        "conversion_refused": 3,
        "route_identity_refused": 10,
    }
    assert report["placement_gate"] == {
        "conversion_refused": 3,
        "placement_no_candidate": 1,
        "placement_source_preservation_refused": 14,
    }
    assert (
        report["runner_digest"]
        == "sha256:"
        + hashlib.sha256(
            (root / "scripts/benchmark_frozen_appliability_census.py").read_bytes()
        ).hexdigest()
    )
    payload = dict(report)
    run_id = payload.pop("run_id")
    assert (
        run_id
        == "sha256:"
        + hashlib.sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
        ).hexdigest()
    )
    serialized = json.dumps(report, sort_keys=True)
    for forbidden in (
        ".kicad_pcb",
        "phono",
        "footprint",
        "segment",
        "coordinate",
        "apply_token",
        "board_bytes",
        "/Users/",
    ):
        assert forbidden not in serialized


def test_oversized_source_is_refused_before_an_output_can_be_written(tmp_path: Path) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    selected = census.select_frozen_corpus(corpus, superseded_phono_v2=superseded)[0]
    selected.write_bytes(b"x" * 2048)
    output = tmp_path / "not-written.json"
    with pytest.raises(WorkspaceViolationError):
        census.measure_frozen_corpus(
            corpus,
            SimpleNamespace(
                max_board_bytes=1024,
                allow_apply=False,
                allow_live_ipc=False,
                allow_live_apply=False,
            ),
            superseded=superseded,
        )
    assert not output.exists()


def test_symlinked_source_is_refused_without_private_data_or_output(tmp_path: Path) -> None:
    corpus, superseded = _make_corpus(tmp_path, count=18)
    selected = census.select_frozen_corpus(corpus, superseded_phono_v2=superseded)[0]
    private = tmp_path / "private.kicad_pcb"
    private.write_bytes(b"private board bytes")
    try:
        selected.unlink()
        selected.symlink_to(private)
    except OSError:
        pytest.skip("symlinks are not available")
    with pytest.raises(WorkspaceViolationError):
        census.measure_frozen_corpus(
            corpus,
            SimpleNamespace(
                max_board_bytes=1024,
                allow_apply=False,
                allow_live_ipc=False,
                allow_live_apply=False,
            ),
            superseded=superseded,
        )
