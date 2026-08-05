from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
import types
from datetime import UTC, datetime
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "benchmark_freerouting_comparison.py"
SPEC = importlib.util.spec_from_file_location("benchmark_freerouting_comparison", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
benchmark = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = benchmark
SPEC.loader.exec_module(benchmark)

RUNNER = Path(__file__).parents[1] / "scripts" / "run_copper_two_pad_fixture.py"
RUNNER_SPEC = importlib.util.spec_from_file_location("run_copper_two_pad_fixture", RUNNER)
assert RUNNER_SPEC is not None and RUNNER_SPEC.loader is not None
runner = importlib.util.module_from_spec(RUNNER_SPEC)
sys.modules[RUNNER_SPEC.name] = runner
RUNNER_SPEC.loader.exec_module(runner)

ADAPTER = Path(__file__).parents[1] / "scripts" / "kicad_specctra_transaction.py"
ADAPTER_SPEC = importlib.util.spec_from_file_location("kicad_specctra_transaction", ADAPTER)
assert ADAPTER_SPEC is not None and ADAPTER_SPEC.loader is not None
adapter = importlib.util.module_from_spec(ADAPTER_SPEC)
sys.modules[ADAPTER_SPEC.name] = adapter
ADAPTER_SPEC.loader.exec_module(adapter)


def _workspace_capability(tmp_path: Path) -> object:
    root = tmp_path / "quota-backed-workspace"
    root.mkdir(mode=0o700)
    root.chmod(0o700)
    return benchmark.PrivateWorkspaceCapability(
        root=root,
        quota_bytes=benchmark.MIN_PRIVATE_WORKSPACE_QUOTA_BYTES,
    )


def _gui_drc_report(board_name: str, unconnected: int = 1) -> str:
    """Return the exact KiCad 10.0.5 GUI-report grammar accepted by this benchmark."""

    unconnected_detail = ()
    if unconnected == 1:
        unconnected_detail = (
            "[unconnected_items]: Missing connection between items",
            "    Local override; error",
            "    @(10.0000 mm, 15.0000 mm): Pad 1 [AUDIO] of J1 on F.Cu",
            "    @(30.0000 mm, 15.0000 mm): Pad 1 [AUDIO] of J2 on F.Cu",
        )
    return "\n".join(
        (
            f"** Drc report for {board_name} **",
            "** Created on 2026-08-05T16:04:34 **",
            "** Report includes: Errors, Warnings **",
            "",
            "** Found 0 DRC violations **",
            "",
            f"** Found {unconnected} unconnected pads **",
            *unconnected_detail,
            "",
            "** Found 0 Footprint errors **",
            "",
            "** Ignored checks **",
            "    - Footprint has no courtyard defined",
            "    - Track endpoint not centered on via",
            "    - Tuning profile track geometries",
            "    - Footprint doesn't match symbol's footprint filters",
            "    - Footprint component type doesn't match footprint pads",
            "",
            "** End of Report **",
            "",
        )
    )


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
    assert "-Djava.awt.headless=true" in command
    assert "shell" not in " ".join(command)


def test_harness_owned_kicad_transaction_argv_has_no_template_or_shell(tmp_path: Path) -> None:
    export = benchmark.kicad_specctra_argv(
        tmp_path / "kicad-python",
        "export-dsn",
        tmp_path / "source.kicad_pcb",
        tmp_path / "source.dsn",
    )
    imported = benchmark.kicad_specctra_argv(
        tmp_path / "kicad-python",
        "import-ses",
        tmp_path / "source.kicad_pcb",
        tmp_path / "result.kicad_pcb",
        ses=tmp_path / "result.ses",
    )

    assert export[1] == str(benchmark.KICAD_SPECCTRA_TRANSACTION)
    assert export[2:] == (
        "export-dsn",
        "--source",
        str(tmp_path / "source.kicad_pcb"),
        "--output",
        str(tmp_path / "source.dsn"),
    )
    assert imported[2] == "import-ses"
    assert "shell" not in " ".join(imported)


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


def test_kicad_adapter_refuses_false_save_result(tmp_path: Path, monkeypatch: object) -> None:
    fake_pcbnew = types.SimpleNamespace(
        LoadBoard=lambda _path: object(),
        ImportSpecctraSES=lambda _board, _ses: True,
        SaveBoard=lambda _output, _board: False,
    )
    monkeypatch.setitem(sys.modules, "pcbnew", fake_pcbnew)

    try:
        adapter.import_ses(
            tmp_path / "source.kicad_pcb", tmp_path / "route.ses", tmp_path / "out.kicad_pcb"
        )
    except ValueError as error:
        assert "save" in str(error).lower()
    else:
        raise AssertionError("false SaveBoard result must fail closed")


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


def test_process_prevents_an_oversized_child_file_before_it_can_complete(tmp_path: Path) -> None:
    result = benchmark.run_process(
        (
            sys.executable,
            "-c",
            "from pathlib import Path; Path('oversized.bin').write_bytes(b'x' * (2 * 1024 * 1024))",
        ),
        3,
        tmp_path,
        file_limit_bytes=1024 * 1024,
    )

    assert result.status == "failed"
    assert (tmp_path / "oversized.bin").stat().st_size <= 1024 * 1024


def test_process_uses_private_cwd_home_and_tmpdir(tmp_path: Path) -> None:
    result = benchmark.run_process(
        (
            sys.executable,
            "-c",
            "import json, os; from pathlib import Path; "
            "Path('environment.json').write_text(json.dumps({'cwd': os.getcwd(), "
            "'home': os.environ['HOME'], 'tmpdir': os.environ['TMPDIR']}))",
        ),
        3,
        tmp_path,
    )

    assert result.status == "ok"
    environment = json.loads((tmp_path / "environment.json").read_text(encoding="utf-8"))
    assert environment == {"cwd": str(tmp_path), "home": str(tmp_path), "tmpdir": str(tmp_path)}


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

    def fake_run(argv: tuple[str, ...], _timeout: int, _cwd: Path, **_kwargs: object) -> object:
        report = Path(argv[argv.index("--output") + 1])
        report.write_text("{not JSON", encoding="utf-8")
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", fake_run)
    result = benchmark.drc_metrics(tmp_path / "kicad-cli", board, 1, tmp_path)
    assert result["status"] == "failed"
    assert "parse_error" not in result


def test_drc_report_and_child_are_confined_to_given_workspace(
    tmp_path: Path, monkeypatch: object
) -> None:
    board = tmp_path / "result.kicad_pcb"
    board.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    observed: dict[str, Path] = {}

    def fake_run(argv: tuple[str, ...], _timeout: int, cwd: Path, **_kwargs: object) -> object:
        observed["cwd"] = cwd
        report = Path(argv[argv.index("--output") + 1])
        assert report.parent.parent == tmp_path
        report.write_text("{}", encoding="utf-8")
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", fake_run)
    monkeypatch.setattr(
        benchmark,
        "_parse_drc_report",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(benchmark.KiCadCliError("stop")),
    )
    assert benchmark.drc_metrics(tmp_path / "kicad-cli", board, 1, tmp_path)["status"] == "failed"
    assert observed == {"cwd": tmp_path}


def test_drc_metrics_accepts_kicad_cli_v10_basename_source_field(
    tmp_path: Path, monkeypatch: object
) -> None:
    board = tmp_path / "result.kicad_pcb"
    board.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    observed: dict[str, str] = {}

    def fake_run(argv: tuple[str, ...], _timeout: int, _cwd: Path, **_kwargs: object) -> object:
        report = Path(argv[argv.index("--output") + 1])
        report.write_text("{}", encoding="utf-8")
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    def fake_parse(payload: bytes, **kwargs: object) -> object:
        observed["source"] = str(kwargs["expected_source"])
        raise benchmark.KiCadCliError("stop after inspecting the source contract")

    monkeypatch.setattr(benchmark, "run_process", fake_run)
    monkeypatch.setattr(benchmark, "_parse_drc_report", fake_parse)
    result = benchmark.drc_metrics(tmp_path / "kicad-cli", board, 1, tmp_path)

    assert result["status"] == "failed"
    assert observed == {"source": "result.kicad_pcb"}


def test_source_drc_binding_requires_declared_exact_baseline_and_source_hash() -> None:
    source_sha = "sha256:" + "a" * 64
    expectation = {"hard_violations": 0, "intentional_unconnected_items": 1}
    drc = {
        "status": "ok",
        "board_sha256": source_sha,
        "hard_violations": 0,
        "unconnected": 1,
        "report_sha256": "sha256:" + "b" * 64,
        "workflow": "kicad-gui-drc-report",
    }

    assert (
        benchmark.source_drc_binding(source_sha, drc, expectation)["status"]
        == "self_attested_unverified"
    )
    assert benchmark.source_drc_binding(source_sha, drc, None)["status"] == "unavailable"
    assert (
        benchmark.source_drc_binding(
            source_sha,
            {**drc, "unconnected": 0},
            expectation,
        )["status"]
        == "mismatch"
    )
    assert (
        benchmark.source_drc_binding(
            source_sha,
            {**drc, "board_sha256": "sha256:" + "c" * 64},
            expectation,
        )["status"]
        == "mismatch"
    )


def test_gui_source_drc_requires_unambiguous_expected_report_structure(tmp_path: Path) -> None:
    source = tmp_path / "source.kicad_pcb"
    source.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    report = tmp_path / "source.rpt"
    report.write_text(_gui_drc_report(source.name), encoding="utf-8")

    evidence = benchmark.gui_source_drc_metrics(source, report)

    assert evidence["status"] == "ok"
    assert evidence["board_sha256"] == _sha(source.read_bytes())
    assert evidence["report_sha256"] == _sha(report.read_bytes())
    assert evidence["hard_violations"] == 0
    assert evidence["unconnected"] == 1
    assert evidence["footprint_errors"] == 0
    assert benchmark.gui_source_drc_metrics(source, None)["status"] == "unavailable"
    report.write_text("** Drc report for another.kicad_pcb **", encoding="utf-8")
    assert benchmark.gui_source_drc_metrics(source, report)["status"] == "failed"


def test_gui_source_drc_rejects_duplicate_or_conflicting_counts(tmp_path: Path) -> None:
    source = tmp_path / "source.kicad_pcb"
    source.write_text("(kicad_pcb (version 20240108))\n", encoding="utf-8")
    report = tmp_path / "source.rpt"
    valid = _gui_drc_report(source.name)
    for malformed in (
        valid.replace(
            "** Found 0 DRC violations **\n",
            "** Found 7 DRC violations **\n** Found 0 DRC violations **\n",
        ),
        valid.replace(
            "** End of Report **", "** Drc report for source.kicad_pcb **\n** End of Report **"
        ),
        valid.replace("** End of Report **", "** Found 0 Other errors **\n** End of Report **"),
        valid.replace("** End of Report **", ""),
        "UNEXPECTED LINE\n" + valid,
        valid.replace(
            "** Found 0 DRC violations **\n", "** Found 0 DRC violations **\nUNEXPECTED LINE\n"
        ),
        valid + "POSTSCRIPT\n",
        valid.replace(
            "** Found 0 DRC violations **\n\n** Found 1 unconnected pads **",
            "** Found 1 unconnected pads **\n\n** Found 0 DRC violations **",
        ),
    ):
        report.write_text(malformed, encoding="utf-8")
        assert benchmark.gui_source_drc_metrics(source, report)["status"] == "failed"


def test_dsn_export_relationship_is_never_inferred_from_separate_hashes(tmp_path: Path) -> None:
    provenance = tmp_path / "provenance.json"
    provenance.write_text(
        json.dumps(
            {
                "origin": "coppermcp-original",
                "license_spdx": "Apache-2.0",
                "derivation_statement": "Authored for this test.",
            }
        ),
        encoding="utf-8",
    )

    assert benchmark.dsn_source_export_binding(provenance) == {"status": "unavailable"}


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


def test_official_release_provenance_binds_the_exact_jar(tmp_path: Path) -> None:
    jar = tmp_path / "freerouting-2.2.2.jar"
    jar.write_bytes(b"official-release-bytes")
    provenance = tmp_path / "freerouting-release.json"
    provenance.write_text(
        json.dumps(
            {
                "schema": benchmark.FREEROUTING_RELEASE_SCHEMA,
                "release_tag": "v2.2.2",
                "asset_name": "freerouting-2.2.2.jar",
                "asset_sha256": _sha(jar.read_bytes()),
                "source_url": (
                    "https://github.com/freerouting/freerouting/releases/download/"
                    "v2.2.2/freerouting-2.2.2.jar"
                ),
                "license_spdx": "GPL-3.0-only",
            }
        ),
        encoding="utf-8",
    )

    record, status = benchmark.freerouting_release_provenance(provenance, jar)

    assert status == "verified"
    assert record == {
        "asset_name": "freerouting-2.2.2.jar",
        "asset_sha256": _sha(jar.read_bytes()),
        "license_spdx": "GPL-3.0-only",
        "release_tag": "v2.2.2",
        "source_url": (
            "https://github.com/freerouting/freerouting/releases/download/"
            "v2.2.2/freerouting-2.2.2.jar"
        ),
    }


def test_official_release_provenance_rejects_a_jar_hash_mismatch(tmp_path: Path) -> None:
    jar = tmp_path / "freerouting-2.2.2.jar"
    jar.write_bytes(b"different-binary")
    provenance = tmp_path / "freerouting-release.json"
    provenance.write_text(
        json.dumps(
            {
                "schema": benchmark.FREEROUTING_RELEASE_SCHEMA,
                "release_tag": "v2.2.2",
                "asset_name": "freerouting-2.2.2.jar",
                "asset_sha256": _sha(b"official-release-bytes"),
                "source_url": (
                    "https://github.com/freerouting/freerouting/releases/download/"
                    "v2.2.2/freerouting-2.2.2.jar"
                ),
                "license_spdx": "GPL-3.0-only",
            }
        ),
        encoding="utf-8",
    )

    record, status = benchmark.freerouting_release_provenance(provenance, jar)

    assert record is None
    assert status == "mismatch"


def test_public_two_pad_runner_uses_preview_then_pure_apply_without_mutating_source(
    tmp_path: Path,
) -> None:
    source = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "routing"
        / "fixtures"
        / "freerouting-common-two-pad-v1.kicad_pcb"
    )
    copied = tmp_path / "source.kicad_pcb"
    copied.write_bytes(source.read_bytes())
    before = copied.read_bytes()

    result = runner.route_fixture(copied, tmp_path / "result.kicad_pcb", seed=0)

    assert copied.read_bytes() == before
    assert result.count(b"(segment") >= 1


def test_committed_real_run_remains_explicitly_incomplete_evidence() -> None:
    artifact = (
        Path(__file__).parents[1]
        / "benchmarks"
        / "results"
        / "routing"
        / "2026-08-05-freerouting-common-two-pad.json"
    )
    report = json.loads(artifact.read_text(encoding="utf-8"))

    assert report["schema"] == benchmark.SCHEMA
    assert report["comparison_closed"] is False
    assert report["status"] == "unavailable_or_incomplete"
    assert report["incomplete_reason"] == "self_attested_unverified"
    assert report["toolchain"]["freerouting_release_provenance_status"] == "verified"
    assert report["source_drc"]["status"] == "ok"
    assert report["source_drc"]["hard_violations"] == 0
    assert report["source_drc"]["unconnected"] == 1
    assert report["source_drc_binding"]["status"] == "self_attested_unverified"
    assert report["fixture"]["dsn_source_export_binding"]["status"] == "self_attested_unverified"
    assert all(item["drc"]["status"] == "ok" for item in report["results"])


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


def test_harness_owned_transaction_binds_export_router_import_and_result_drc(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    kicad_python = tmp_path / "kicad-python"
    kicad_python.write_bytes(b"tool")
    source_sha = _sha(paths["source"].read_bytes())
    board_bytes = b"(kicad_pcb (version 20240108) (segment (start 1 1) (end 2 1)))\n"
    copper_receipt = tmp_path / "copper-receipt.json"
    copper_receipt.write_text(
        json.dumps(
            {
                "schema": benchmark.COPPER_RECEIPT_SCHEMA,
                "workflow": "coppermcp-candidate-runner",
                "source_sha256": source_sha,
                "runner_output_sha256": _sha(paths["board"].read_bytes()),
                "result_board_sha256": _sha(paths["board"].read_bytes()),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        benchmark, "preflight", lambda **_kwargs: {"available": True, "reasons": [], "probes": {}}
    )
    capability = _workspace_capability(tmp_path)
    monkeypatch.setattr(benchmark, "private_workspace_capability", lambda: capability)
    drc_cwds: list[Path] = []

    def clean_private_drc(*args: object, **kwargs: object) -> dict[str, object]:
        cwd = args[3] if len(args) > 3 else kwargs["cwd"]
        assert isinstance(cwd, Path)
        drc_cwds.append(cwd)
        return _clean_drc(*args, **kwargs)

    monkeypatch.setattr(benchmark, "drc_metrics", clean_private_drc)
    process_cwds: list[Path] = []

    def transaction_tools(argv: tuple[str, ...], *_args: object, **_kwargs: object) -> object:
        assert len(_args) >= 2 and isinstance(_args[1], Path)
        process_cwds.append(_args[1])
        if "export-dsn" in argv:
            Path(argv[argv.index("--output") + 1]).write_text("(pcb exported)\n", encoding="utf-8")
        elif "-do" in argv:
            Path(argv[argv.index("-do") + 1]).write_text("(session)\n", encoding="utf-8")
        elif "import-ses" in argv:
            Path(argv[argv.index("--output") + 1]).write_bytes(board_bytes)
        elif argv[0] == "not-coppermcp":
            Path(argv[2]).write_bytes(paths["board"].read_bytes())
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", transaction_tools)
    kwargs = _build_kwargs(paths)
    kwargs["dsn"] = None
    report = benchmark.build_report(
        **kwargs,
        kicad_python=kicad_python,
        copper_board=paths["board"],
        freerouting_board=None,
        copper_receipt=copper_receipt,
        freerouting_receipt=None,
        copper_command=("not-coppermcp", "{source}", "{output}", "{seed}"),
        seed=1,
        timeout_seconds=1,
    )

    assert report["freerouting_transaction"]["status"] == "bound"
    assert report["fixture"]["dsn_source_export_binding"]["status"] == "harness_bound"
    assert report["freerouting_import_binding"] == {"status": "harness_bound"}
    freerouting_result = next(item for item in report["results"] if item["name"] == "freerouting")
    assert freerouting_result["board_sha256"] == _sha(board_bytes)
    assert report["comparison_closed"] is False
    assert report["incomplete_reason"] == "copper_runner_self_attested_unverified"
    assert process_cwds and drc_cwds
    assert all(path.is_relative_to(capability.root) for path in process_cwds + drc_cwds)


def test_harness_transaction_fails_closed_without_aggregate_workspace_quota(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    kicad_python = tmp_path / "kicad-python"
    kicad_python.write_bytes(b"tool")
    monkeypatch.setattr(
        benchmark,
        "aggregate_workspace_containment",
        lambda: {"status": "unavailable", "reason": "no aggregate quota"},
    )
    monkeypatch.setattr(
        benchmark,
        "run_process",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("must not execute")),
    )

    transaction, result = benchmark._harness_freerouting_transaction(
        source=paths["source"],
        java=paths["java"],
        jar=paths["jar"],
        kicad_python=kicad_python,
        kicad_cli=paths["kicad"],
        timeout_seconds=1,
        source_sha256=_sha(paths["source"].read_bytes()),
        cwd=tmp_path,
    )

    assert transaction["status"] == "unavailable"
    assert transaction["containment"]["status"] == "unavailable"
    assert result is None


def test_unavailable_private_workspace_preflight_launches_no_external_seam(
    tmp_path: Path, monkeypatch: object
) -> None:
    """Containment refusal happens before version probes, DRC, routing, import, or runner work."""

    paths = _comparison_inputs(tmp_path)
    kicad_python = tmp_path / "kicad-python"
    kicad_python.write_bytes(b"tool")
    launched: list[tuple[str, ...]] = []

    def record_launch(argv: tuple[str, ...], *_args: object, **_kwargs: object) -> object:
        launched.append(argv)
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "private_workspace_capability", lambda: None)
    monkeypatch.setattr(benchmark, "run_process", record_launch)
    report = benchmark.build_report(
        **_build_kwargs(paths),
        kicad_python=kicad_python,
        copper_board=paths["board"],
        freerouting_board=paths["board"],
        copper_receipt=None,
        freerouting_receipt=None,
        copper_command=("candidate-runner", "{source}", "{output}", "{seed}"),
        seed=1,
        timeout_seconds=1,
    )

    assert launched == []
    assert report["preflight"]["available"] is False
    assert report["source_drc"]["status"] == "unavailable"
    assert report["freerouting_process"]["status"] == "unavailable"
    assert report["copper_process"]["status"] == "unavailable"
    assert report["comparison_closed"] is False


def test_workspace_capability_rejects_shared_and_symlink_roots(
    tmp_path: Path, monkeypatch: object
) -> None:
    shared = tmp_path / "shared"
    shared.mkdir(mode=0o755)
    monkeypatch.setattr(
        benchmark,
        "private_workspace_capability",
        lambda: benchmark.PrivateWorkspaceCapability(
            root=shared, quota_bytes=benchmark.MIN_PRIVATE_WORKSPACE_QUOTA_BYTES
        ),
    )
    capability, containment = benchmark.verified_private_workspace_capability()
    assert capability is None
    assert containment["status"] == "unavailable"

    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(target, target_is_directory=True)
    monkeypatch.setattr(
        benchmark,
        "private_workspace_capability",
        lambda: benchmark.PrivateWorkspaceCapability(
            root=alias, quota_bytes=benchmark.MIN_PRIVATE_WORKSPACE_QUOTA_BYTES
        ),
    )
    capability, containment = benchmark.verified_private_workspace_capability()
    assert capability is None
    assert containment["status"] == "unavailable"


def test_harness_transaction_refuses_an_importer_that_mutates_its_source_copy(
    tmp_path: Path, monkeypatch: object
) -> None:
    paths = _comparison_inputs(tmp_path)
    kicad_python = tmp_path / "kicad-python"
    kicad_python.write_bytes(b"tool")
    monkeypatch.setattr(
        benchmark, "private_workspace_capability", lambda: _workspace_capability(tmp_path)
    )

    def mutating_importer(argv: tuple[str, ...], *_args: object, **_kwargs: object) -> object:
        if "export-dsn" in argv:
            Path(argv[argv.index("--output") + 1]).write_text("(pcb exported)\n", encoding="utf-8")
        elif "-do" in argv:
            Path(argv[argv.index("-do") + 1]).write_text("(session)\n", encoding="utf-8")
        elif "import-ses" in argv:
            Path(argv[argv.index("--source") + 1]).write_text("tampered", encoding="utf-8")
            Path(argv[argv.index("--output") + 1]).write_text(
                "(kicad_pcb (version 20240108))\n", encoding="utf-8"
            )
        return benchmark.ProcessResult(argv, 1, 0, "ok", "", "")

    monkeypatch.setattr(benchmark, "run_process", mutating_importer)
    transaction, result = benchmark._harness_freerouting_transaction(
        source=paths["source"],
        java=paths["java"],
        jar=paths["jar"],
        kicad_python=kicad_python,
        kicad_cli=paths["kicad"],
        timeout_seconds=1,
        source_sha256=_sha(paths["source"].read_bytes()),
        cwd=tmp_path,
    )

    assert transaction["status"] == "failed"
    assert transaction["kicad_import"]["source_copy_preserved"] is False
    assert result is None


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
