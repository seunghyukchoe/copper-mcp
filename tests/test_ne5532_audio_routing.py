from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from scripts import benchmark_ne5532_audio_routing as benchmark


def _drc_report(
    *,
    violations: list[object] | None = None,
    unconnected: list[object] | None = None,
) -> dict[str, object]:
    return {
        "violations": [] if violations is None else violations,
        "unconnected_items": [] if unconnected is None else unconnected,
    }


def test_drc_summary_reads_a_normal_bounded_report(tmp_path) -> None:
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps(_drc_report(violations=[{}], unconnected=[{}, {}])), encoding="utf-8"
    )

    assert benchmark._drc_summary(tmp_path, report, max_report_bytes=1024) == {
        "violations": 1,
        "unconnected_items": 2,
    }


def test_drc_summary_rejects_an_oversized_report_before_json_decode(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    report = tmp_path / "report.json"
    report.write_bytes(b"{" + b" " * 1024 + b"}")
    monkeypatch.setattr(
        benchmark.json,
        "loads",
        lambda *_args, **_kwargs: pytest.fail("oversized report reached JSON decoding"),
    )

    with pytest.raises(benchmark.AudioRoutingBenchmarkError, match="cannot be parsed"):
        benchmark._drc_summary(tmp_path, report, max_report_bytes=64)


@pytest.mark.parametrize(
    "payload",
    [
        b"{",
        b"[" * 65 + b"0" + b"]" * 65,
    ],
)
def test_drc_summary_rejects_malformed_or_overdeep_reports(tmp_path, payload: bytes) -> None:
    report = tmp_path / "report.json"
    report.write_bytes(payload)

    with pytest.raises(benchmark.AudioRoutingBenchmarkError, match="cannot be parsed"):
        benchmark._drc_summary(tmp_path, report, max_report_bytes=1024)


def test_drc_runner_discards_noisy_child_diagnostics_without_changing_counts(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim = tmp_path / "noisy-kicad"
    shim.write_text(
        "\n".join(
            [
                f"#!{sys.executable}",
                "from pathlib import Path",
                "import json",
                "import sys",
                "sys.stdout.write('o' * 1000000)",
                "sys.stderr.write('e' * 1000000)",
                "report = Path(sys.argv[sys.argv.index('--output') + 1])",
                "board = Path(sys.argv[-1])",
                "unconnected = 24 if board.name == 'source-drc.kicad_pcb' else 23",
                "report.write_text(json.dumps({",
                "    'violations': [{}] * 14,",
                "    'unconnected_items': [{}] * unconnected,",
                "}))",
            ]
        ),
        encoding="utf-8",
    )
    shim.chmod(0o700)
    monkeypatch.setattr(benchmark, "KICAD_CLI", shim)
    original_run = subprocess.run
    process_options: list[tuple[object, object, bool]] = []

    def observing_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        process_options.append((kwargs["stdout"], kwargs["stderr"], kwargs["shell"]))
        return original_run(*args, **kwargs)  # type: ignore[arg-type, return-value]

    monkeypatch.setattr(benchmark.subprocess, "run", observing_run)

    metrics = benchmark.run_benchmark(1, include_kicad_drc=True)

    assert process_options == [(subprocess.DEVNULL, subprocess.DEVNULL, False)] * 9
    assert metrics["authoritative_drc"] == {
        "attempted": True,
        "status": "completed-not-clean",
        "authority": "KiCad CLI JSON DRC over independent disposable single-net derivatives",
        "kicad_cli_path": str(shim),
        "source": {"violations": 14, "unconnected_items": 24},
        "candidates": [
            {"net": net, "violations": 14, "unconnected_items": 23}
            for net in ("L_IN", "R_IN", "L_SUM", "R_SUM", "L_OUT", "R_OUT", "VPOS", "VNEG")
        ],
        "combined_candidate_board": False,
        "clean": False,
    }


def test_original_unrouted_ne5532_fixture_has_pinned_public_route_evidence() -> None:
    metrics = benchmark.run_benchmark(2)

    assert metrics["fixture_origin"] == "coppermcp-original"
    assert metrics["fixture_license_spdx"] == "Apache-2.0"
    assert (
        metrics["fixture_source_sha256"]
        == hashlib.sha256(benchmark.FIXTURE.read_bytes()).hexdigest()
    )
    assert metrics["source_is_unrouted"] is True
    assert metrics["source_object_counts"] == {
        "arcs": 0,
        "copper_layers": 2,
        "differential_pair_rules": 0,
        "footprints": 14,
        "keepouts": 0,
        "length_rules": 0,
        "net_class_assignments": 11,
        "net_classes": 1,
        "nets": 11,
        "outline": 1,
        "pads": 35,
        "segments": 0,
        "vias": 0,
        "zones": 0,
    }
    assert metrics["route_request_count"] == 8
    assert metrics["routed_request_count"] == 8
    assert metrics["unrouted_request_count"] == 0
    assert metrics["multi_pin_routed_request_count"] == 4
    assert metrics["source_unchanged"] is True
    assert metrics["candidate_applied"] is False
    assert metrics["authoritative_drc"]["status"] == "not_run"
    # These eight candidate IDs moved once, deliberately, under ADR-0087 (issue #128):
    # `ROUTER_VERSION` advanced to `astar-grid/0.7.0` and the recorded settings changed.
    # Every other column below is unchanged -- same pad counts, same path counts, same wire
    # lengths to the nanometre, same zero vias -- which is the evidence that the geometry did
    # not move and only the address did. The migration note is in CHANGELOG.md.
    assert [
        (
            route["net"],
            route["candidate_id"],
            route["pad_count"],
            route["path_count"],
            route["wire_length_nm"],
            route["via_count"],
        )
        for route in metrics["routes"]
    ] == [
        (
            "L_IN",
            "sha256:4434e033163685b19402d0a698a6b347201253eed702797b86b045a106b41410",
            2,
            1,
            12_000_000,
            0,
        ),
        (
            "R_IN",
            "sha256:a68ae6100d6c93b1c059686f4b916c41234dff7d4d2f6d5f906ee6d566e782b2",
            2,
            1,
            12_000_000,
            0,
        ),
        (
            "L_SUM",
            "sha256:b93092b407d0664bdf51f1fffef25bfc0f7d1a927cd4aa1b154b3864267febe7",
            3,
            2,
            37_250_000,
            0,
        ),
        (
            "R_SUM",
            "sha256:2bb6e905555af967c02709c7f3a95403422976ba187d08dca4d1b1efd7018ad4",
            3,
            2,
            34_750_000,
            0,
        ),
        (
            "L_OUT",
            "sha256:7d9ed7776c0934ca913eb0429a9d4ac5ecd19b1f3eca8af5effbb72a09af26ee",
            2,
            1,
            45_500_000,
            0,
        ),
        (
            "R_OUT",
            "sha256:75bbe2fa3b76e204e2b8d39f36727af43cb58fd123dc4c84dbb45994b7a9c1f7",
            2,
            1,
            48_000_000,
            0,
        ),
        (
            "VPOS",
            "sha256:9d44a484d713101d4af24471dceb0d228bcabf1a10306b2bd938b8e4ae463822",
            4,
            3,
            55_500_000,
            0,
        ),
        (
            "VNEG",
            "sha256:d69d774699327e6ce2ad8370660f1f7171c5e01f5e9e79f56a7f6933fdc65e19",
            4,
            3,
            50_750_000,
            0,
        ),
    ]


def test_ne5532_report_is_content_addressed_for_a_fixed_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(benchmark, "_git_commit", lambda: "test-commit")
    timestamp = datetime(2026, 8, 5, tzinfo=UTC)

    first = benchmark.build_report(1, timestamp=timestamp)
    second = benchmark.build_report(1, timestamp=timestamp)

    assert first == second
    canonical = dict(first)
    run_id = canonical.pop("run_id")
    expected = hashlib.sha256(
        json.dumps(canonical, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    assert run_id == f"sha256:{expected}"
