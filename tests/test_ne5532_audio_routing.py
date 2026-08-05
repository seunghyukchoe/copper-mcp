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
            "sha256:c7dccb5a913b36ced1f157f5054d78d7bcb78d830a32ba361de3c14643a22b57",
            2,
            1,
            12_000_000,
            0,
        ),
        (
            "R_IN",
            "sha256:cb3448a60d70541df5461016ccf9e3605a52b5069a9619b722ccb28b1034974d",
            2,
            1,
            12_000_000,
            0,
        ),
        (
            "L_SUM",
            "sha256:0dc0e8e4b8429c11afe756e7900dbce557d8dbfbd70c7afb0f05d3c887355043",
            3,
            2,
            37_250_000,
            0,
        ),
        (
            "R_SUM",
            "sha256:c79975c6b32e7f2a06cc4eb0c438c45dfda24735f21cf36ebd5ff8c39dd28883",
            3,
            2,
            34_750_000,
            0,
        ),
        (
            "L_OUT",
            "sha256:1ab41c2dab8293a99f7bc72fc757897fcf759885b3d0d6011f8d3130bb812555",
            2,
            1,
            45_500_000,
            0,
        ),
        (
            "R_OUT",
            "sha256:37773b6c823180314bdefea9c2af3bbda78ba1237bc68cebaee15e120232956e",
            2,
            1,
            48_000_000,
            0,
        ),
        (
            "VPOS",
            "sha256:28d59543d41ac83756b5d9a8f23eb6cf82c581f5971611ddc82f938ef1ef7d5a",
            4,
            3,
            55_500_000,
            0,
        ),
        (
            "VNEG",
            "sha256:e602da5551f899a2202bcca07ac86ea2f5e02f3a82eab8ea08b83db1c2296044",
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
