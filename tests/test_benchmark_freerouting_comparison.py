from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_freerouting_comparison.py"
SPEC = importlib.util.spec_from_file_location("benchmark_freerouting_comparison", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)


def test_freerouting_command_uses_documented_dsn_ses_boundary(tmp_path: Path) -> None:
    command = benchmark.freerouting_argv(
        tmp_path / "java",
        tmp_path / "freerouting.jar",
        tmp_path / "input.dsn",
        tmp_path / "output.ses",
    )
    assert command[-6:] == (
        "-de",
        str(tmp_path / "input.dsn"),
        "-do",
        str(tmp_path / "output.ses"),
        "-l",
        "en",
    )
    assert "shell" not in " ".join(command)


def test_copper_template_has_only_explicit_placeholders(tmp_path: Path) -> None:
    command = benchmark.copper_argv(
        ("runner", "{source}", "{output}", "{seed}"), tmp_path / "in", tmp_path / "out", 9
    )
    assert command == ("runner", str(tmp_path / "in"), str(tmp_path / "out"), "9")
    try:
        benchmark.copper_argv(("runner", "{unknown}"), tmp_path / "in", tmp_path / "out", 9)
    except ValueError as error:
        assert "unknown" in str(error)
    else:
        raise AssertionError("unknown template placeholder must fail")


def test_process_timeout_and_redaction_are_truthful(tmp_path: Path) -> None:
    result = benchmark.run_process(
        (sys.executable, "-u", "-c", "import time; print('token=super-secret'); time.sleep(2)"),
        1,
        tmp_path,
    )
    assert result.status == "timeout"
    assert "super-secret" not in result.stdout
    assert "[redacted]" in result.stdout


def test_metric_priority_prefers_connectivity_and_drc_before_quality() -> None:
    clean = {
        "status": "ok",
        "drc": {"status": "ok", "unconnected": 0, "hard_violations": 0},
        "vias": 99,
        "length_nm": 99,
        "elapsed_ns": 99,
    }
    broken = {
        "status": "ok",
        "drc": {"status": "ok", "unconnected": 1, "hard_violations": 0},
        "vias": 0,
        "length_nm": 0,
        "elapsed_ns": 0,
    }
    assert benchmark.metric_priority(clean) < benchmark.metric_priority(broken)


def test_report_is_content_addressed_and_records_unavailable_preflight(tmp_path: Path) -> None:
    source = tmp_path / "fixture.kicad_pcb"
    source.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    provenance = tmp_path / "fixture.provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "origin": "coppermcp-original",
                "license_spdx": "Apache-2.0",
                "derivation_statement": "Authored for benchmark.",
            }
        ),
        encoding="utf-8",
    )
    report = benchmark.build_report(
        source=source,
        dsn=None,
        java=None,
        jar=None,
        kicad_cli=None,
        provenance=provenance,
        copper_board=None,
        freerouting_board=None,
        copper_command=None,
        seed=23,
        timeout_seconds=1,
        timestamp=datetime(2026, 8, 5, tzinfo=UTC),
    )
    assert report["status"] == "unavailable_or_incomplete"
    assert report["source_preserved"] is True
    assert report["run_id"].startswith("sha256:")
    assert "DSN is unavailable" in report["preflight"]["reasons"]
