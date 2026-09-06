"""Closed backend-authentication gates; command doubles earn no engine credit."""

import subprocess
import time
from types import SimpleNamespace

import pytest
from test_project_erc import build_project

from copper_mcp.config import Settings
from copper_mcp.engineering import project_erc
from copper_mcp.kicad_cli import _private_kicad_environment


def _bundle(tmp_path):
    bundle = tmp_path / "KiCad.app"
    for relative in (
        "Contents/_CodeSignature/CodeResources",
        "Contents/Info.plist",
        "Contents/MacOS/kicad",
        "Contents/MacOS/kicad-cli",
    ):
        target = bundle / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(relative.encode())
    return bundle, bundle / "Contents/MacOS/kicad-cli"


def test_configured_forged_executable_cannot_invoke_itself(tmp_path, monkeypatch):
    capture, libraries, _ = build_project(tmp_path)
    forged = tmp_path / "kicad-cli"
    forged.write_bytes(b"unreviewed")
    forged.chmod(0o700)
    monkeypatch.setattr(
        project_erc, "_invoke", lambda *_a, **_k: pytest.fail("unapproved executable must not run")
    )
    with pytest.raises(project_erc.ProjectErcError):
        project_erc.run_project_erc(
            capture, libraries, Settings(workspace=tmp_path, kicad_cli=forged)
        )


def test_platform_authority_is_fixed_and_binds_the_sealed_closure(tmp_path, monkeypatch):
    bundle, executable = _bundle(tmp_path)
    monkeypatch.setattr(project_erc.sys, "platform", "darwin")
    commands = []

    def verifier(command, **kwargs):
        commands.append(command)
        assert command[0] == "/usr/bin/codesign"
        assert command[1:3] == ["--verify", "--strict"]
        assert "9FQDHNY6U2" in command[-2]
        assert kwargs["shell"] is False
        assert kwargs["stdout"] == kwargs["stderr"] == subprocess.DEVNULL
        assert kwargs["timeout"] > 0
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(project_erc.subprocess, "run", verifier)
    environment = _private_kicad_environment(tmp_path / "state")
    settings = Settings(workspace=tmp_path)
    first = project_erc._authenticate_backend(
        executable, settings, environment, time.monotonic() + 5
    )
    assert len(commands) == 2 and "--deep" in commands[0]
    assert 'identifier "org.kicad.kicad"' in commands[0][-2]
    assert 'identifier "kicad-cli"' in commands[1][-2]
    (bundle / "Contents/_CodeSignature/CodeResources").write_bytes(b"other hypothetical sealed set")
    second = project_erc._authenticate_backend(
        executable, settings, environment, time.monotonic() + 5
    )
    assert first != second


@pytest.mark.parametrize("fault", ("signature", "timeout", "platform"))
def test_unverified_unavailable_or_late_platform_authority_refuses(tmp_path, monkeypatch, fault):
    _, executable = _bundle(tmp_path)
    monkeypatch.setattr(project_erc.sys, "platform", "linux" if fault == "platform" else "darwin")

    def verifier(command, **_kwargs):
        if fault == "platform":
            pytest.fail("unsupported platform must not invoke a substitute verifier")
        if fault == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        return SimpleNamespace(returncode=1)

    monkeypatch.setattr(project_erc.subprocess, "run", verifier)
    with pytest.raises((project_erc.ProjectErcError, subprocess.TimeoutExpired)):
        project_erc._authenticate_backend(
            executable,
            Settings(workspace=tmp_path),
            _private_kicad_environment(tmp_path / "state"),
            time.monotonic() + 5,
        )
