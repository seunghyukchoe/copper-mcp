from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest
from kicad_drc_mock import make_fake_kicad_cli, rule_liveness_report
from test_kicad_drc_rule_liveness import _context, _summary

from copper_mcp import kicad_cli
from copper_mcp.config import Settings


def _settings(tmp_path: Path, executable: Path) -> Settings:
    return Settings(workspace=tmp_path, kicad_cli=executable, kicad_timeout_seconds=10)


def _fixed_error(call) -> None:
    with pytest.raises(kicad_cli.KiCadCliError, match="KiCad CLI executable") as error:
        call()
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_two_passes_share_one_discovery_and_resolved_target_after_alias_retarget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = make_fake_kicad_cli(tmp_path, "first-cli")
    second = make_fake_kicad_cli(tmp_path, "second-cli")
    alias = tmp_path / "selected-cli"
    alias.symlink_to(first)
    settings = _settings(tmp_path, alias)
    discovered = 0
    actual_discover = kicad_cli.discover_kicad_cli
    targets: list[Path] = []

    def discover(current: Settings) -> Path:
        nonlocal discovered
        discovered += 1
        return actual_discover(current)

    def run(command: list[str], **_kwargs):
        targets.append(Path(command[command.index("pcb") - 1]))
        board = Path(command[-1])
        report_path = Path(command[command.index("--output") + 1])
        report = rule_liveness_report(board)
        if report is None:
            report = {
                "$schema": "https://schemas.kicad.org/drc.v1.json",
                "source": board.name,
                "date": "2026-08-03T12:00:00+09:00",
                "coordinate_units": "mm",
                "kicad_version": "10.0.5",
                "violations": [],
                "unconnected_items": [],
                "schematic_parity": [],
                "included_severities": ["error", "warning", "exclusion"],
                "ignored_checks": [],
            }
        report_path.write_text(json.dumps(report), encoding="utf-8")
        if len(targets) == 1:
            alias.unlink()
            alias.symlink_to(second)
        return subprocess.CompletedProcess(command, 5 if rule_liveness_report(board) else 0)

    monkeypatch.setattr(kicad_cli, "discover_kicad_cli", discover)
    monkeypatch.setattr(kicad_cli.subprocess, "run", run)
    kicad_cli._run_captured_drc(_context(), board_relative="board.kicad_pcb", settings=settings)
    assert discovered == 1
    assert targets == [first, first]


def test_replacement_before_witness_pass_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    replacement = make_fake_kicad_cli(tmp_path, "replacement")
    actual_build = kicad_cli.build_rule_liveness_context

    def build(*args, **kwargs):
        result = actual_build(*args, **kwargs)
        replacement.replace(executable)
        return result

    monkeypatch.setattr(kicad_cli, "build_rule_liveness_context", build)
    monkeypatch.setattr(
        kicad_cli,
        "_run_captured_drc_pass",
        lambda *_args, **_kwargs: pytest.fail("changed executable reached witness pass"),
    )
    _fixed_error(
        lambda: kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path, executable),
        )
    )


def test_replacement_between_passes_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    replacement = make_fake_kicad_cli(tmp_path, "replacement")
    calls = 0

    def run_pass(payload, **_kwargs):
        nonlocal calls
        calls += 1
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        replacement.replace(executable)
        return _summary(revision)

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    _fixed_error(
        lambda: kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path, executable),
        )
    )
    assert calls == 1


def test_replacement_during_final_cleanup_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    replacement = make_fake_kicad_cli(tmp_path, "replacement")
    actual_temporary = kicad_cli.tempfile.TemporaryDirectory

    class ReplacingCleanup:
        def __init__(self, *args, **kwargs):
            self._temporary = actual_temporary(*args, **kwargs)
            self.name = self._temporary.name

        def cleanup(self):
            self._temporary.cleanup()
            replacement.replace(executable)

    def run_pass(payload, **_kwargs):
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        return _summary(revision)

    monkeypatch.setattr(kicad_cli.tempfile, "TemporaryDirectory", ReplacingCleanup)
    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    _fixed_error(
        lambda: kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path, executable),
        )
    )


def test_no_rule_pass_cannot_return_after_executable_drift(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    replacement = make_fake_kicad_cli(tmp_path, "replacement")
    context = _context()
    del context["board.kicad_dru"]

    def run_pass(payload, **_kwargs):
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        replacement.replace(executable)
        return _summary(revision)

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    _fixed_error(
        lambda: kicad_cli._run_captured_drc(
            context,
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path, executable),
        )
    )


@pytest.mark.parametrize("change", ["bytes", "mode", "timestamp"])
def test_in_place_executable_identity_drift_is_refused(tmp_path: Path, change: str) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    settings = _settings(tmp_path, executable)
    deadline = time.monotonic() + 10
    identity = kicad_cli._capture_kicad_cli_executable(settings, deadline)
    if change == "bytes":
        payload = executable.read_bytes()
        executable.write_bytes(payload[:-1] + bytes([payload[-1] ^ 1]))
    elif change == "mode":
        executable.chmod(0o600)
    else:
        current = executable.stat()
        os.utime(executable, ns=(current.st_atime_ns, current.st_mtime_ns + 1_000_000_000))
    _fixed_error(lambda: kicad_cli._verify_kicad_cli_executable(identity, settings, deadline))


@pytest.mark.parametrize("invalid", ["oversized", "nonregular", "unreadable"])
def test_invalid_executable_is_refused_before_a_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, invalid: str
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    settings = _settings(tmp_path, executable)
    if invalid == "oversized":
        monkeypatch.setattr(kicad_cli, "_MAX_KICAD_EXECUTABLE_BYTES", 4)
    elif invalid == "nonregular":
        monkeypatch.setattr(kicad_cli, "discover_kicad_cli", lambda _settings: tmp_path)
    else:
        actual_open = kicad_cli.os.open

        def refuse(path, flags, *args, **kwargs):
            if Path(path) == executable:
                raise OSError("PRIVATE executable path")
            return actual_open(path, flags, *args, **kwargs)

        monkeypatch.setattr(kicad_cli.os, "open", refuse)
    _fixed_error(lambda: kicad_cli._capture_kicad_cli_executable(settings, time.monotonic() + 10))


def test_executable_identity_hashing_obeys_the_shared_deadline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    executable = make_fake_kicad_cli(tmp_path)
    clock = iter((0.0, 0.0, 11.0))
    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: next(clock, 11.0))
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline"):
        kicad_cli._capture_kicad_cli_executable(_settings(tmp_path, executable), 10.0)


def test_no_rule_native_timeout_uses_time_remaining_after_identity(tmp_path, monkeypatch):
    from test_kicad_cli import drc_report

    executable = make_fake_kicad_cli(tmp_path)
    settings = _settings(tmp_path, executable)
    context = _context()
    del context["board.kicad_dru"]
    clock = [0.0]
    actual_capture = kicad_cli._capture_kicad_cli_executable
    timeouts = []

    def slow_capture(current, deadline):
        identity = actual_capture(current, deadline)
        clock[0] = 7.0
        return identity

    def run(command, **kwargs):
        timeouts.append(kwargs["timeout"])
        assert kwargs["timeout"] == 3
        report = drc_report()
        report["source"] = Path(command[-1]).name
        Path(command[command.index("--output") + 1]).write_text(
            json.dumps(report), encoding="utf-8"
        )
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli, "_capture_kicad_cli_executable", slow_capture)
    monkeypatch.setattr(kicad_cli.subprocess, "run", run)
    kicad_cli._run_captured_drc(context, board_relative="board.kicad_pcb", settings=settings)
    assert timeouts == [3]
