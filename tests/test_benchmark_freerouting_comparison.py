from __future__ import annotations

import hashlib
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


def test_process_kills_on_bounded_output_and_never_buffers_the_full_stream(tmp_path: Path) -> None:
    result = benchmark.run_process(
        (sys.executable, "-u", "-c", "import sys; sys.stdout.write('x' * 20000)"),
        3,
        tmp_path,
    )
    assert result.status == "output_limit"
    assert len(result.stdout) <= benchmark.MAX_PROCESS_OUTPUT_BYTES


def test_untrusted_file_reads_have_explicit_byte_ceiling(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized"
    oversized.write_bytes(b"x" * 17)
    try:
        benchmark.read_bounded_bytes(oversized, 16)
    except ValueError as error:
        assert "byte limit" in str(error)
    else:
        raise AssertionError("oversized untrusted input must fail closed")


def test_malformed_drc_report_fails_closed_without_report_diagnostics(
    tmp_path: Path, monkeypatch: object
) -> None:
    board = tmp_path / "result.kicad_pcb"
    board.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")

    def fake_run(argv: tuple[str, ...], _timeout: int, _cwd: Path) -> object:
        report = Path(argv[argv.index("--output") + 1])
        report.write_text("{not JSON", encoding="utf-8")
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", fake_run)
    result = benchmark.drc_metrics(tmp_path / "kicad-cli", board, 1, tmp_path)
    assert result["status"] == "failed"
    assert "parse_error" not in result


def test_report_process_evidence_never_includes_private_argv_or_child_output() -> None:
    result = benchmark.ProcessResult(
        ("/private/customer/token=never",),
        1,
        1,
        "failed",
        "password=never /private/customer/board.kicad_pcb",
        "",
    )
    evidence = json.dumps(benchmark.process_record(result, "freerouting_dsn_ses"))
    assert "/private" not in evidence
    assert "never" not in evidence
    assert "argv" not in evidence


def test_minimal_child_environment_does_not_inherit_provider_tokens(
    tmp_path: Path, monkeypatch: object
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "never-inherit")
    environment = benchmark.minimal_environment(tmp_path)
    assert "OPENAI_API_KEY" not in environment
    assert set(environment) == {"HOME", "LANG", "LC_ALL", "PATH", "TMPDIR"}


def _sha(payload: bytes) -> str:
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _comparison_inputs(tmp_path: Path) -> dict[str, Path]:
    source = tmp_path / "source.kicad_pcb"
    board = tmp_path / "clean-but-unrelated.kicad_pcb"
    dsn = tmp_path / "source.dsn"
    provenance = tmp_path / "provenance.json"
    for executable in ("java", "router.jar", "kicad-cli"):
        (tmp_path / executable).write_bytes(b"tool")
    source.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    board.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    dsn.write_text("(pcb test)\n", encoding="utf-8")
    provenance.write_text(
        json.dumps(
            {
                "origin": "independently-authored",
                "license_spdx": "Apache-2.0",
                "derivation_statement": "Authored for hostile harness tests.",
            }
        ),
        encoding="utf-8",
    )
    return {
        "source": source,
        "board": board,
        "dsn": dsn,
        "provenance": provenance,
        "java": tmp_path / "java",
        "jar": tmp_path / "router.jar",
        "kicad": tmp_path / "kicad-cli",
    }


def _clean_drc(*_args: object, **_kwargs: object) -> dict[str, object]:
    return {"status": "ok", "hard_violations": 0, "unconnected": 0}


def _build_kwargs(paths: dict[str, Path]) -> dict[str, Path]:
    return {
        "source": paths["source"],
        "dsn": paths["dsn"],
        "java": paths["java"],
        "jar": paths["jar"],
        "kicad_cli": paths["kicad"],
        "provenance": paths["provenance"],
    }


def test_failed_freerouting_with_ses_and_clean_boards_cannot_close(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    monkeypatch.setattr(
        benchmark, "preflight", lambda **_kwargs: {"available": True, "reasons": [], "probes": {}}
    )
    monkeypatch.setattr(benchmark, "drc_metrics", _clean_drc)

    def failed_router(argv: tuple[str, ...], *_args: object) -> object:
        if "-do" in argv:
            Path(argv[argv.index("-do") + 1]).write_text("(session)\n", encoding="utf-8")
            return benchmark.ProcessResult(argv, 1, 1, "failed", "", "")
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", failed_router)
    report = benchmark.build_report(
        **_build_kwargs(paths),
        copper_board=paths["board"],
        freerouting_board=paths["board"],
        copper_receipt=None,
        freerouting_receipt=None,
        copper_command=None,
        seed=1,
        timeout_seconds=1,
    )
    assert report["freerouting_process"]["status"] == "failed"
    assert report["status"] == "unavailable_or_incomplete"


def test_successful_router_without_valid_ses_cannot_close(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    monkeypatch.setattr(
        benchmark, "preflight", lambda **_kwargs: {"available": True, "reasons": [], "probes": {}}
    )
    monkeypatch.setattr(benchmark, "drc_metrics", _clean_drc)
    monkeypatch.setattr(
        benchmark,
        "run_process",
        lambda argv, *_args: benchmark.ProcessResult(argv, 1, 0, "ok", "", ""),
    )
    report = benchmark.build_report(
        **_build_kwargs(paths),
        copper_board=paths["board"],
        freerouting_board=paths["board"],
        copper_receipt=None,
        freerouting_receipt=None,
        copper_command=None,
        seed=1,
        timeout_seconds=1,
    )
    assert report["freerouting_process"]["ses_status"] == "missing_or_invalid"
    assert report["status"] == "unavailable_or_incomplete"


def test_unrelated_drc_clean_board_fails_ses_receipt_binding(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    ses = b"(session)\n"
    source_sha = _sha(paths["source"].read_bytes())
    receipt = tmp_path / "freerouting-receipt.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": benchmark.FREEROUTING_RECEIPT_SCHEMA,
                "workflow": "kicad-specctra-ses-import",
                "source_sha256": source_sha,
                "ses_sha256": _sha(ses),
                "result_board_sha256": _sha(b"not-the-evaluated-board"),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark, "preflight", lambda **_kwargs: {"available": True, "reasons": [], "probes": {}}
    )
    monkeypatch.setattr(benchmark, "drc_metrics", _clean_drc)

    def successful_router(argv: tuple[str, ...], *_args: object) -> object:
        Path(argv[argv.index("-do") + 1]).write_bytes(ses)
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", successful_router)
    report = benchmark.build_report(
        **_build_kwargs(paths),
        copper_board=paths["board"],
        freerouting_board=paths["board"],
        copper_receipt=None,
        freerouting_receipt=receipt,
        copper_command=None,
        seed=1,
        timeout_seconds=1,
    )
    assert report["freerouting_import_binding"]["status"] == "mismatch"
    assert report["status"] == "unavailable_or_incomplete"


def test_self_attested_bound_receipts_and_clean_drc_cannot_close(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    paths["board"].write_text(
        "(kicad_pcb (version 20240108) (general (thickness 1.6)))\n", encoding="utf-8"
    )
    ses = b"(session)\n"
    source_sha = _sha(paths["source"].read_bytes())
    board_sha = _sha(paths["board"].read_bytes())
    freerouting_receipt = tmp_path / "freerouting-receipt.json"
    freerouting_receipt.write_text(
        json.dumps(
            {
                "schema": benchmark.FREEROUTING_RECEIPT_SCHEMA,
                "workflow": "kicad-specctra-ses-import",
                "source_sha256": source_sha,
                "ses_sha256": _sha(ses),
                "result_board_sha256": board_sha,
            }
        ),
        encoding="utf-8",
    )
    copper_receipt = tmp_path / "copper-receipt.json"
    copper_receipt.write_text(
        json.dumps(
            {
                "schema": benchmark.COPPER_RECEIPT_SCHEMA,
                "workflow": "coppermcp-candidate-runner",
                "source_sha256": source_sha,
                "runner_output_sha256": board_sha,
                "result_board_sha256": board_sha,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark, "preflight", lambda **_kwargs: {"available": True, "reasons": [], "probes": {}}
    )
    monkeypatch.setattr(benchmark, "drc_metrics", _clean_drc)

    def self_attesting_tools(argv: tuple[str, ...], *_args: object) -> object:
        if "-do" in argv:
            Path(argv[argv.index("-do") + 1]).write_bytes(ses)
        elif argv[0] == "not-coppermcp":
            Path(argv[2]).write_bytes(paths["board"].read_bytes())
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", self_attesting_tools)
    report = benchmark.build_report(
        **_build_kwargs(paths),
        copper_board=paths["board"],
        freerouting_board=paths["board"],
        copper_receipt=copper_receipt,
        freerouting_receipt=freerouting_receipt,
        copper_command=("not-coppermcp", "{source}", "{output}", "{seed}"),
        seed=1,
        timeout_seconds=1,
    )

    assert report["freerouting_import_binding"]["status"] == "bound"
    assert report["copper_runner_binding"]["status"] == "bound"
    assert report["comparison_closed"] is False
    assert report["incomplete_reason"] == "self_attested_unverified"
    assert report["status"] == "unavailable_or_incomplete"


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
        copper_receipt=None,
        freerouting_receipt=None,
        copper_command=None,
        seed=23,
        timeout_seconds=1,
        timestamp=datetime(2026, 8, 5, tzinfo=UTC),
    )
    assert report["status"] == "unavailable_or_incomplete"
    assert report["source_preserved"] is True
    assert report["run_id"].startswith("sha256:")
    assert "DSN is unavailable" in report["preflight"]["reasons"]
