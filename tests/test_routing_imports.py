"""Lightweight leaf imports preserve the existing fully typed routing facade."""

import os
import subprocess
import sys
from pathlib import Path


def test_policy_leaf_does_not_load_unrelated_routing_backends():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; import copper_mcp.routing.policy_worker; "
                "assert 'copper_mcp.routing.astar' not in sys.modules; "
                "assert 'copper_mcp.routing.job_repository' not in sys.modules; "
                "assert 'copper_mcp.routing.congestion' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        timeout=15,
    )
    assert result.returncode == 0, result.stderr.decode()


def test_facade_retains_every_public_export_and_identity():
    import copper_mcp.routing as facade
    from copper_mcp.routing import _exports

    assert facade.__all__ == _exports.__all__
    assert set(_exports.__all__) <= set(dir(facade))
    for name in _exports.__all__:
        assert getattr(facade, name) is getattr(_exports, name)


def test_mcp_registration_does_not_import_optimization_execution_stack(tmp_path, monkeypatch):
    # Other tests or an operator environment may name an expired temporary workspace.
    # The fresh-process import test owns its configuration instead of inheriting that state.
    monkeypatch.setenv("COPPER_MCP_WORKSPACE", str(tmp_path / "does-not-exist"))
    monkeypatch.setenv("COPPER_MCP_ALLOW_APPLY", "invalid-parent-value")
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            (
                "import sys; from copper_mcp.mcp_server import mcp; "
                "assert 'copper_mcp.optimization.service' not in sys.modules; "
                "assert 'copper_mcp.optimization.repository' not in sys.modules; "
                "assert 'copper_mcp.optimization.isolated' not in sys.modules"
            ),
        ],
        check=False,
        capture_output=True,
        timeout=15,
        cwd=Path(__file__).resolve().parents[1],
        env={
            "PATH": os.defpath,
            "LANG": "C.UTF-8",
            "COPPER_MCP_WORKSPACE": str(tmp_path),
            "COPPER_MCP_TRANSPORT": "streamable-http",
        },
    )
    assert result.returncode == 0, result.stderr.decode()
