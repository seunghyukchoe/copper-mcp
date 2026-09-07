"""Orchestration doubles prove refusals, never native-engine or physics authority."""

import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from project_parity_fixtures import assigned_project, digest
from test_source_to_board_parity import finding, parity_report

from copper_mcp.config import Settings
from copper_mcp.engineering import project_board_parity as parity


def run_case(tmp_path, monkeypatch, mode):
    capture, libraries, _files, boards = assigned_project(tmp_path)
    runtime = tmp_path / "runtime"
    runtime.mkdir()
    checks = []
    calls = []

    @contextmanager
    def context(prepared, settings, deadline):
        yield SimpleNamespace(
            temporary=runtime,
            environment={"TMPDIR": str(runtime)},
            executable=Path("/fake/kicad-cli"),
            executable_digest="sha256:" + "1" * 64,
            authentication_digest="sha256:" + "2" * 64,
            version="10.0.5",
            base_command=("python", "-I", "bounded.py"),
            native_syntax_digest="sha256:" + "3" * 64,
            verify=lambda: checks.append(True),
        )

    def invoke(command, *, stdout, **kwargs):
        calls.append(command)
        assert "--save-board" not in command and "--refill-zones" not in command
        assert "--exit-code-violations" not in command and "--define-var" not in command
        output = Path(command[command.index("--output") + 1])
        board = Path(command[-1])
        findings = [finding("net_conflict")] if mode == "hard-divergent" else []
        if mode == "unknown-type":
            findings = [finding("unreviewed-parity")]
        report = parity_report(source=board.name, schematic_parity=findings)
        if mode == "ignored-check":
            report["ignored_checks"].append({"key": "net_conflict", "description": "ignored"})
        if mode in {"divergent", "hard-divergent"}:
            detail = finding("test-companion")
            detail["description"] = f"different observation {len(calls)}"
            report["violations"] = [detail]
        output.write_text(json.dumps(report))
        text = (
            f"Found {len(report['violations'])} violations\nFound 0 unconnected items\n"
            f"Found {len(findings)} schematic parity issues\nSaved DRC Report to {output}\n"
        )
        if mode == "missing-marker":
            text = text.replace("Found 0 schematic parity issues\n", "")
        elif mode == "wrong-count":
            text = text.replace("Found 0 schematic parity", "Found 1 schematic parity")
        elif mode == "extra-diagnostic":
            text += "unexpected native warning\n"
        elif mode == "oversized-log":
            text += "x" * (64 * 1024)
        stdout.write(text.encode())
        if mode in {"changed-board", "changed-project"}:
            target = board if mode == "changed-board" else board.with_suffix(".kicad_pro")
            target.chmod(0o600)
            target.write_bytes(b"changed input")
            target.chmod(0o400)
        if mode == "source-drift" and len(calls) == 2:
            (tmp_path / "child.kicad_sch").write_bytes(b"changed live source")
        return 5 if mode == "nonzero-exit" else 0

    monkeypatch.setattr(parity.execution, "open_project_execution_context", context)
    monkeypatch.setattr(parity.execution, "_invoke", invoke)
    result = parity.run_project_board_parity(
        capture,
        libraries,
        boards["matching"],
        digest(boards["matching"]),
        Settings(workspace=tmp_path),
    )
    return result, calls, checks


def test_valid_simulated_liveness_is_still_only_parity_evidence(tmp_path, monkeypatch):
    report, calls, checks = run_case(tmp_path, monkeypatch, "pass")
    assert report.status == "pass" and len(calls) == len(checks) == 2
    assert report.document()["apply_authority"] == "none"
    assert report.document()["drc_validation"] == "inconclusive"


@pytest.mark.parametrize(
    "mode",
    (
        "missing-marker",
        "wrong-count",
        "extra-diagnostic",
        "oversized-log",
        "ignored-check",
        "unknown-type",
        "changed-board",
        "changed-project",
        "source-drift",
        "nonzero-exit",
    ),
)
def test_unbound_or_incomplete_execution_cannot_return_evidence(tmp_path, monkeypatch, mode):
    with pytest.raises(parity.ProjectBoardParityError) as caught:
        run_case(tmp_path, monkeypatch, mode)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize("mode,status", (("divergent", "inconclusive"), ("hard-divergent", "fail")))
def test_inconsistent_reports_never_produce_a_pass(tmp_path, monkeypatch, mode, status):
    report, _calls, _checks = run_case(tmp_path, monkeypatch, mode)
    assert report.status == status
