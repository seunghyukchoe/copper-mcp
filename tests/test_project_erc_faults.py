"""Fault-injection coverage for the private, bound project ERC orchestration."""

import dataclasses
import json
import subprocess
import time
from pathlib import Path

import pytest
from test_project_erc import build_project

from copper_mcp.engineering.erc_profile import BACKEND_VERSION, OUTSIDE_CONNECTIVITY_SCOPE
from copper_mcp.engineering.project_erc import _ERC_FLAGS, ProjectErcError, run_project_erc
from copper_mcp.kicad_cli import KICAD_ERC_SCHEMA


@pytest.fixture(autouse=True)
def synthetic_backend_authority(monkeypatch):
    """These are orchestration faults, never native/signature acceptance evidence."""
    from copper_mcp.engineering import project_erc

    monkeypatch.setattr(project_erc, "_authenticate_backend", lambda *_args: "sha256:" + "a" * 64)


def _fake_executable(tmp_path: Path) -> Path:
    executable = tmp_path / "fake-kicad-cli"
    executable.write_bytes(b"test double")
    executable.chmod(0o700)
    return executable


def _report(
    source: str,
    uuid_paths: set[str],
    *,
    finding: str | None = None,
    severity="error",
    description="test finding",
    extra_ignore=False,
):
    ignored = [
        {"key": key, "description": "outside profile"} for key in sorted(OUTSIDE_CONNECTIVITY_SCOPE)
    ]
    if extra_ignore:
        ignored.append({"key": "extra_check", "description": "unexpected"})
    violations = []
    if finding:
        violations.append(
            {
                "type": finding,
                "description": description,
                "severity": severity,
                "items": [],
                "excluded": False,
            }
        )
    return {
        "$schema": KICAD_ERC_SCHEMA,
        "coordinate_units": "mm",
        "source": source,
        "date": "2026-09-06T00:00:00Z",
        "kicad_version": BACKEND_VERSION,
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": ignored,
        "sheets": [
            {"path": f"sheet-{index}", "uuid_path": path, "violations": violations}
            for index, path in enumerate(sorted(uuid_paths))
        ],
    }


def _install_invoke(monkeypatch, project_erc, uuid_paths, *, mode="pass"):
    calls = []

    def fake_invoke(command, *, settings, environment, deadline, stdout=subprocess.DEVNULL):
        calls.append((list(command), dict(environment)))
        assert "--define-var" not in command
        assert "--save" not in command
        assert command[:2] == [command[0], "-I"]
        assert Path(environment["HOME"]).is_absolute()
        assert Path(environment["KICAD_CONFIG_HOME"]).is_absolute()
        assert Path(environment["HOME"]) != Path(environment["KICAD_CONFIG_HOME"])
        assert ".copper-erc-libraries" in environment["COPPER_MCP_ERC_LIBDIR"]
        if "--version" in command:
            assert stdout is not subprocess.DEVNULL
            stdout.write(BACKEND_VERSION.encode())
            if mode == "modified-table":
                table = Path(environment["KICAD_CONFIG_HOME"]) / "sym-lib-table"
                table.write_bytes(b"changed")
            elif mode == "extra-table":
                (Path(environment["KICAD_CONFIG_HOME"]) / "extra-lib-table").write_bytes(b"extra")
            elif mode == "modified-fonts":
                Path(environment["FONTCONFIG_FILE"]).write_bytes(b"changed")
            return 0
        if "upgrade" in command:
            assert tuple(command[5:8]) == ("sch", "upgrade", "--force")
            target = Path(command[8])
            assert "syntax-" in str(target)
            if mode == "syntax-refusal":
                return 3
            if mode == "syntax-unrelated-change":
                other = target.with_suffix(".kicad_pro")
                other.chmod(0o600)
                other.write_bytes(b"changed")
                other.chmod(0o400)
            target.write_bytes(b"SYNTAX_ONLY_DERIVATIVE")
            return 0
        assert command[3] == str(settings.max_drc_report_bytes)
        assert tuple(command[5:13]) == _ERC_FLAGS
        assert command[13] == "--output"
        output = Path(command[14])
        snapshot = Path(command[15])
        assert "/input/" in str(snapshot)
        assert snapshot.read_bytes() != b"SYNTAX_ONLY_DERIVATIVE"
        source = snapshot.name
        if mode == "modified-snapshot":
            snapshot.chmod(0o700)
            snapshot.write_bytes(b"tampered")
        if mode == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if mode == "oversize":
            output.write_bytes(b"{" + b"x" * settings.max_drc_report_bytes)
            return 0
        finding = None
        extra_ignore = mode == "extra-ignore"
        erc_call_count = len([call for call in calls if "--output" in call[0]])
        if mode in {"divergent", "hard-error-divergent"} and erc_call_count == 1:
            finding = "pin_not_connected"
        elif mode in {"divergent", "hard-error-divergent"}:
            finding = "pin_to_pin"
        elif mode in {"findings", "unknown-finding"}:
            finding = "pin_not_connected"
        if mode == "unknown-finding":
            finding = "invented-finding"
        if mode == "divergent":
            finding = "unconnected_wire_endpoint" if erc_call_count == 1 else "label_multiple_wires"
        if mode == "downgraded-error":
            finding = "pin_not_connected"
        severity = "warning" if mode in {"divergent", "downgraded-error"} else "error"
        output.write_text(
            json.dumps(
                _report(
                    source,
                    uuid_paths,
                    finding=finding,
                    severity=severity,
                    description=(
                        f"finding {erc_call_count}"
                        if mode in {"divergent", "hard-error-divergent"}
                        else "test finding"
                    ),
                    extra_ignore=extra_ignore,
                )
            )
        )
        if mode == "modified-executable" and erc_call_count == 1:
            Path(command[4]).write_bytes(b"changed executable")
        return 5 if finding else 0

    monkeypatch.setattr(project_erc, "_invoke", fake_invoke)
    return calls


def _run(tmp_path, monkeypatch, mode="pass", **settings_changes):
    from copper_mcp.engineering import project_erc

    capture, libraries, context = build_project(tmp_path)
    before = {name: (data, (tmp_path / name).stat().st_mtime_ns) for name, data in context.items()}
    settings = dataclasses.replace(
        project_erc.Settings(workspace=tmp_path, kicad_cli=_fake_executable(tmp_path)),
        **settings_changes,
    )
    uuid_paths = {item.uuid_path for item in capture.hierarchy.instance_paths}
    calls = _install_invoke(monkeypatch, project_erc, uuid_paths, mode=mode)
    error = None
    try:
        result = run_project_erc(capture, libraries, settings)
    except ProjectErcError as caught:
        result = None
        error = caught
    after = {
        name: ((tmp_path / name).read_bytes(), (tmp_path / name).stat().st_mtime_ns)
        for name in before
    }
    assert after == before
    return result, calls, error


def test_fake_baseline_is_pass_only_with_scope_and_no_apply(tmp_path, monkeypatch):
    report, calls, _ = _run(tmp_path, monkeypatch)
    assert report is not None and report.status == "pass"
    assert report.document()["apply_authority"] == "none"
    assert len(calls) == 5
    assert len([command for command, _ in calls if "upgrade" in command]) == 2
    assert report.native_syntax_file_count == 2


@pytest.mark.parametrize("phase", ("before", "during"))
@pytest.mark.parametrize("source", ("root", "project", "child"))
def test_workspace_source_changes_refuse_before_execution_or_delivery(
    tmp_path, monkeypatch, phase, source
):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    name = {"root": capture.root_path, "project": capture.project_path, "child": "child.kicad_sch"}[
        source
    ]
    original = (tmp_path / name).read_bytes()
    settings = project_erc.Settings(workspace=tmp_path, kicad_cli=_fake_executable(tmp_path))
    _install_invoke(
        monkeypatch, project_erc, {item.uuid_path for item in capture.hierarchy.instance_paths}
    )
    execute = project_erc._execute
    executions = []

    def observed_execution(*args):
        executions.append(True)
        report = execute(*args)
        assert report.status == "pass"
        if phase == "during":
            (tmp_path / name).write_bytes(b"changed source")
        return report

    monkeypatch.setattr(project_erc, "_execute", observed_execution)
    if phase == "before":
        (tmp_path / name).write_bytes(b"changed source")
    with pytest.raises(ProjectErcError) as caught:
        run_project_erc(capture, libraries, settings)
    assert len(executions) == (0 if phase == "before" else 1)
    assert caught.value.__cause__ is None and caught.value.__context__ is None
    assert next(item.content for item in capture._files if item.path == name) == original


@pytest.mark.parametrize("fault", ("missing", "symlink"))
def test_workspace_freshness_requires_regular_confined_source(tmp_path, monkeypatch, fault):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    source = tmp_path / "child.kicad_sch"
    original = source.read_bytes()
    source.unlink()
    if fault == "symlink":
        other = tmp_path / "other.kicad_sch"
        other.write_bytes(original)
        source.symlink_to(other)
    monkeypatch.setattr(project_erc, "_execute", lambda *_args: pytest.fail("must not execute"))
    with pytest.raises(ProjectErcError):
        run_project_erc(capture, libraries, project_erc.Settings(workspace=tmp_path))


def test_workspace_resolution_loop_has_a_fixed_error(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    loop = tmp_path / "loop"
    loop.symlink_to("loop")
    monkeypatch.setattr(project_erc, "_execute", lambda *_args: pytest.fail("must not execute"))
    with pytest.raises(ProjectErcError) as caught:
        run_project_erc(capture, libraries, project_erc.Settings(workspace=loop))
    assert str(tmp_path) not in str(caught.value)
    assert caught.value.__cause__ is None and caught.value.__context__ is None


@pytest.mark.parametrize("workspace", (None, "not-a-path", 42))
def test_malformed_workspace_type_refuses_before_execution(tmp_path, monkeypatch, workspace):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    settings = dataclasses.replace(project_erc.Settings(workspace=tmp_path), workspace=workspace)
    monkeypatch.setattr(project_erc, "_execute", lambda *_args: pytest.fail("must not execute"))
    with pytest.raises(ProjectErcError):
        run_project_erc(capture, libraries, settings)


def test_final_workspace_read_respects_original_size_and_shared_deadline(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    expected = {item.path: item.content for item in capture._files}
    settings = project_erc.Settings(workspace=tmp_path, kicad_cli=_fake_executable(tmp_path))
    _install_invoke(
        monkeypatch, project_erc, {item.uuid_path for item in capture.hierarchy.instance_paths}
    )
    reads = []
    clock = [100.0]
    read = project_erc.read_workspace_file

    def expiring_read(workspace, path, **kwargs):
        observed = read(workspace, path, **kwargs)
        if workspace == tmp_path and path in expected:
            assert kwargs["max_bytes"] == len(expected[path])
            assert kwargs["allowed_suffixes"] == (".kicad_sch", ".kicad_pro")
            reads.append(path)
            if len(reads) == 2 * len(expected):
                clock[0] += settings.kicad_timeout_seconds + 1
        return observed

    monkeypatch.setattr(project_erc.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(project_erc, "read_workspace_file", expiring_read)
    with pytest.raises(ProjectErcError):
        run_project_erc(capture, libraries, settings)
    assert reads == list(expected) * 2


@pytest.mark.parametrize(
    "mode",
    (
        "modified-snapshot",
        "modified-table",
        "extra-table",
        "timeout",
        "oversize",
        "modified-executable",
        "unknown-finding",
        "downgraded-error",
        "syntax-refusal",
        "syntax-unrelated-change",
        "modified-fonts",
    ),
)
def test_private_or_report_faults_refuse(tmp_path, monkeypatch, mode):
    report, _, error = _run(tmp_path, monkeypatch, mode, max_drc_report_bytes=16_384)
    assert report is None
    if mode == "timeout":
        assert error is not None
        assert str(error) == "project ERC could not produce bound connectivity evidence"
        assert str(tmp_path) not in str(error)


@pytest.mark.parametrize(
    "mode", ("wrong-source", "wrong-version", "malformed", "missing-uuid", "duplicate-uuid")
)
def test_report_identity_faults_refuse(tmp_path, monkeypatch, mode):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    executable = _fake_executable(tmp_path)

    def fake_invoke(command, *, settings, environment, deadline, stdout=subprocess.DEVNULL):
        if "--version" in command:
            stdout.write(BACKEND_VERSION.encode())
            return 0
        if "upgrade" in command:
            return 0
        output = Path(command[14])
        if mode == "malformed":
            output.write_bytes(b"not json")
            return 0
        uuid_paths = [item.uuid_path for item in capture.hierarchy.instance_paths]
        if mode not in {"missing-uuid", "duplicate-uuid"}:
            pass
        else:
            uuid_paths = uuid_paths[:1]
        report = _report(Path(command[15]).name, set(uuid_paths))
        if mode == "wrong-source":
            report["source"] = "other.kicad_sch"
        if mode == "wrong-version":
            report["kicad_version"] = "9.0.0"
        if mode == "duplicate-uuid":
            report["sheets"].append(dict(report["sheets"][0]))
        output.write_text(json.dumps(report))
        return 0

    monkeypatch.setattr(project_erc, "_invoke", fake_invoke)
    with pytest.raises(ProjectErcError):
        run_project_erc(
            capture,
            libraries,
            dataclasses.replace(project_erc.Settings(workspace=tmp_path, kicad_cli=executable)),
        )


@pytest.mark.parametrize("mode", ("extra-ignore", "divergent"))
def test_non_equivalent_repetitions_are_inconclusive(tmp_path, monkeypatch, mode):
    report, _, _ = _run(tmp_path, monkeypatch, mode)
    assert report is not None and report.status == "inconclusive"


def test_hard_error_takes_precedence_over_divergence(tmp_path, monkeypatch):
    report, _, _ = _run(tmp_path, monkeypatch, "hard-error-divergent")
    assert report is not None and report.status == "fail"


def test_findings_and_exit_code_are_bound(tmp_path, monkeypatch):
    report, _, _ = _run(tmp_path, monkeypatch, "findings")
    assert report is not None and report.status == "fail"


@pytest.mark.parametrize(
    "deadline", (time.monotonic() - 1, float("inf")), ids=("expired", "nonfinite")
)
def test_budget_guard_refuses_before_operator(tmp_path, monkeypatch, deadline):
    from copper_mcp.engineering import project_erc

    capture, libraries, _ = build_project(tmp_path)
    monkeypatch.setattr(project_erc, "_invoke", lambda **_: pytest.fail("operator must not run"))
    with pytest.raises(ProjectErcError):
        run_project_erc(
            capture, libraries, project_erc.Settings(workspace=tmp_path), deadline=deadline
        )
