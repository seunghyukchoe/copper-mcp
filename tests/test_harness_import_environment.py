"""Importing offline harnesses must not reconfigure their caller or disable real smokes."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HARNESS_MODULES = (
    "scripts.evaluate_excessive_agency",
    "scripts.evaluate_mcp_agency_safety",
    "scripts.benchmark_scene_action_closure",
)


@pytest.mark.parametrize("module", HARNESS_MODULES)
@pytest.mark.parametrize("mode", ("fresh", "preloaded", "import_failure", "caller_after"))
def test_harness_import_restores_caller_environment(module: str, mode: str, tmp_path: Path) -> None:
    code = """
import importlib
import importlib.machinery
import os
import sys
from pathlib import Path
from unittest.mock import patch

root, module, mode, workspace = sys.argv[1:]
sys.path.insert(0, str(Path(root) / 'scripts'))
sys.path.insert(0, root)
existing = None
if mode == 'preloaded':
    with patch.dict(os.environ, {
        'COPPER_MCP_WORKSPACE': workspace,
        'COPPER_MCP_TRANSPORT': 'streamable-http',
        'COPPER_MCP_ALLOW_APPLY': '1',
    }, clear=True):
        existing = importlib.import_module('copper_mcp.mcp_server')
    settings = existing._SETTINGS

before = dict(os.environ)
real_exec = importlib.machinery.SourceFileLoader.exec_module
def failing_exec(loader, loaded):
    if loaded.__name__ == '_copper_mcp_offline_harness_server':
        raise RuntimeError('synthetic MCP initialization failure')
    return real_exec(loader, loaded)

if mode == 'import_failure':
    with patch.object(importlib.machinery.SourceFileLoader, 'exec_module', failing_exec):
        try:
            importlib.import_module(module)
        except RuntimeError as error:
            assert str(error) == 'synthetic MCP initialization failure'
        else:
            raise AssertionError('expected initialization refusal')
    assert dict(os.environ) == before
    assert '_copper_mcp_offline_harness_server' not in sys.modules

# Also prove failed construction can be retried without reusing a partial module.
harness = importlib.import_module(module)
server = harness.mcp_server
assert server._SETTINGS.workspace == Path(root)
assert server._SETTINGS.transport == 'stdio'
assert not server._SETTINGS.allow_apply
assert not server._SETTINGS.allow_live_ipc
assert not server._SETTINGS.allow_live_apply
if existing is not None:
    assert server is not existing and existing._SETTINGS is settings
    assert existing._SETTINGS.workspace == Path(workspace)
    assert existing._SETTINGS.transport == 'streamable-http'
    assert existing._SETTINGS.allow_apply
else:
    assert 'copper_mcp.mcp_server' not in sys.modules

if mode == 'caller_after':
    normal = importlib.import_module('copper_mcp.mcp_server')
    assert normal is not server
    assert normal._SETTINGS.workspace == Path(workspace)
    assert normal._SETTINGS.transport == 'streamable-http'
    assert normal._SETTINGS.allow_apply
    from copper_mcp.cli import main
    with patch.object(normal.mcp, 'run') as run:
        assert main(['serve', '--transport', 'streamable-http']) == 0
    run.assert_called_once_with(
        'streamable-http', host='127.0.0.1', port=8765, stateless_http=True, json_response=True
    )

assert dict(os.environ) == before, 'harness changed the caller environment'
"""
    environment = {
        "PATH": os.defpath,
        "PYTHONPATH": str(ROOT / "src"),
        "COPPER_MCP_WORKSPACE": str(tmp_path / "absent-workspace"),
        "COPPER_MCP_TRANSPORT": "invalid-inherited-transport",
        "COPPER_MCP_ALLOW_APPLY": "1",
        "COPPER_MCP_ALLOW_LIVE_IPC": "1",
        "COPPER_MCP_ALLOW_LIVE_APPLY": "1",
        "COPPER_MCP_TEST_FREEROUTING_IMAGE": "synthetic-smoke-setting",
        "COPPER_MCP_TEST_SRJ_IMAGE": "synthetic-other-smoke-setting",
        "COPPER_MCP_UNRECOGNIZED_OPTION": "synthetic-caller-setting",
        "HARNESS_UNRELATED_SETTING": "unchanged",
    }
    if mode == "caller_after":
        environment["COPPER_MCP_WORKSPACE"] = str(tmp_path)
        environment["COPPER_MCP_TRANSPORT"] = "streamable-http"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and synthetic environment
        [sys.executable, "-c", code, str(ROOT), module, mode, str(tmp_path)],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
