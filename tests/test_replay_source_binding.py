"""Production-source changes cannot inherit a historical artifact's reproduction claim."""

import hashlib
import json
import os
import py_compile
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.replay_source_binding import (
    _ARGUMENT_ERROR,
    _ARTIFACTS,
    _MAX_OUTPUT_BYTES,
    _ROOTS,
    ReplayBindingError,
    _InventoriedImports,
    _render_bounded,
    capture_source_binding,
    verify_source_binding,
)


@pytest.fixture
def inventory(tmp_path: Path) -> Path:
    for name in _ROOTS:
        directory = tmp_path / name
        directory.mkdir(parents=True)
        (directory / "input.py").write_bytes(b"immutable input")
    for name in _ARTIFACTS:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"{}")
    return tmp_path


@pytest.mark.parametrize("root", _ROOTS)
def test_each_executable_or_corpus_root_is_bound(inventory: Path, root: str):
    before = capture_source_binding(inventory)
    (inventory / root / "input.py").write_bytes(b"changed implementation")
    with pytest.raises(ReplayBindingError, match="changed"):
        verify_source_binding(before, inventory)


def test_adding_production_modules_invalidates_inventory(inventory: Path):
    before = capture_source_binding(inventory)
    (inventory / "src/copper_mcp/new.py").write_bytes(b"new code")
    with pytest.raises(ReplayBindingError, match="changed"):
        verify_source_binding(before, inventory)


def test_missing_and_symlinked_inputs_refuse(inventory: Path):
    path = inventory / _ARTIFACTS[0]
    path.unlink()
    with pytest.raises(ReplayBindingError):
        capture_source_binding(inventory)
    path.symlink_to(inventory / _ARTIFACTS[1])
    with pytest.raises(ReplayBindingError):
        capture_source_binding(inventory)


def test_identity_is_independent_of_caches_and_host_directory(inventory: Path, tmp_path: Path):
    before = capture_source_binding(inventory)
    cache = inventory / "src/copper_mcp/__pycache__"
    cache.mkdir()
    (cache / "input.pyc").write_bytes(b"irrelevant bytecode")
    assert capture_source_binding(inventory) == before
    assert before.digest.startswith("sha256:")
    verify_source_binding(before, inventory)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="requires POSIX FIFO support")
def test_special_members_cannot_disappear_from_a_complete_inventory(inventory: Path):
    os.mkfifo(inventory / "src/copper_mcp/unreadable.py")
    with pytest.raises(ReplayBindingError, match="special file"):
        capture_source_binding(inventory)


def test_receipt_command_refuses_nonisolated_python():
    script = Path(__file__).resolve().parents[1] / "scripts/replay_source_binding.py"
    result = subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
        [sys.executable, str(script)], capture_output=True, timeout=10, check=False
    )
    assert result.returncode != 0
    assert b"requires an isolated interpreter" in result.stderr
    assert not result.stdout


def test_replay_executes_current_source_despite_timestamp_valid_stale_bytecode(inventory: Path):
    script = Path(__file__).resolve().parents[1] / "scripts/replay_source_binding.py"
    copied = inventory / "scripts/replay_source_binding.py"
    copied.write_bytes(script.read_bytes())
    runner = inventory / "scripts/benchmark_negotiated_multipin_branch_repair.py"
    old = (
        "def load_artifact():\n"
        '    return {"metrics": {"value": 2}, "configuration": {}, "run_id": "test-only"}\n'
        "def build_report(repetitions):\n"
        '    return {"metrics": {"value": 9}, "configuration": {}}\n'
    )
    runner.write_text(old)
    metadata = runner.stat()
    py_compile.compile(str(runner), doraise=True)
    current = old.replace('"value": 9', '"value": 2')
    assert len(current) == len(old)
    runner.write_text(current)
    os.utime(runner, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))
    result = subprocess.run(  # noqa: S603 - fixed interpreter and an owned temporary fixture
        [sys.executable, "-I", str(copied)],
        capture_output=True,
        timeout=15,
        check=False,
    )
    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert set(receipt) == {
        "configuration_digest",
        "metrics_digest",
        "published_run_id",
        "python_version",
        "receipt_digest",
        "repetitions",
        "schema",
        "source_inventory_digest",
        "source_inventory_files",
        "status",
    }
    assert receipt["schema"] == "copper-mcp/current-evidence-replay/v1"
    assert receipt["status"] == "reproduced"
    assert receipt["repetitions"] == 2
    body = {key: value for key, value in receipt.items() if key != "receipt_digest"}
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    assert receipt["receipt_digest"] == "sha256:" + hashlib.sha256(canonical).hexdigest()


def test_census_executes_current_source_despite_timestamp_valid_stale_bytecode(inventory: Path):
    script = Path(__file__).resolve().parents[1] / "scripts/replay_source_binding.py"
    copied = inventory / "scripts/replay_source_binding.py"
    copied.write_bytes(script.read_bytes())
    runner = inventory / "scripts/benchmark_negotiated_corpus_census.py"
    old = "def build_report(repetitions):\n    return {'fresh': 1}\n"
    runner.write_text(old)
    metadata = runner.stat()
    py_compile.compile(str(runner), doraise=True)
    current = old.replace("'fresh': 1", "'fresh': 2")
    assert len(current) == len(old)
    runner.write_text(current)
    os.utime(runner, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

    result = subprocess.run(  # noqa: S603 - fixed interpreter and an owned temporary fixture
        [sys.executable, "-I", str(copied), "--census"],
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode == 0, result.stderr.decode()
    receipt = json.loads(result.stdout)
    assert receipt["schema"] == "copper-mcp/current-census-replay/v1"
    assert receipt["status"] == "measured"
    assert receipt["repetitions"] == 1
    assert receipt["report"] == {"fresh": 2}


@pytest.mark.parametrize(
    "arguments",
    [
        ("--unknown",),
        ("--census=1",),
        ("--census", "extra"),
        ("--census", "--census"),
        ("--parse-memory=1",),
        ("--parse-memory", "extra"),
        ("--parse-memory", "--parse-memory"),
    ],
)
def test_replay_rejects_every_unknown_argument_form_with_one_error(
    inventory: Path, arguments: tuple[str, ...]
):
    script = Path(__file__).resolve().parents[1] / "scripts/replay_source_binding.py"
    copied = inventory / "scripts/replay_source_binding.py"
    copied.write_bytes(script.read_bytes())

    result = subprocess.run(  # noqa: S603 - fixed interpreter and an owned temporary fixture
        [sys.executable, "-I", str(copied), *arguments],
        capture_output=True,
        timeout=15,
        check=False,
    )

    assert result.returncode != 0
    assert _ARGUMENT_ERROR.encode() in result.stderr
    assert not result.stdout


def test_replay_output_is_bounded_before_write():
    within_limit = {"payload": "x" * (_MAX_OUTPUT_BYTES - 40)}
    assert len(_render_bounded(within_limit)) <= _MAX_OUTPUT_BYTES

    oversized = {"payload": "x" * _MAX_OUTPUT_BYTES}
    with pytest.raises(ReplayBindingError, match="output exceeds its byte budget"):
        _render_bounded(oversized)


def test_import_refuses_bytes_changed_after_inventory(inventory: Path):
    module = inventory / "scripts/example.py"
    module.write_text("value = 1\n")
    finder = _InventoriedImports(capture_source_binding(inventory), inventory)
    spec = finder.find_spec("scripts.example", [str(module.parent)])
    assert spec is not None and spec.loader is not None
    module.write_text("value = 2\n")
    with pytest.raises(ReplayBindingError, match="changed after"):
        spec.loader.get_code("scripts.example")


@pytest.mark.parametrize("kind", ["new_source", "extension", "sourceless"])
def test_uninventoried_execution_is_refused_before_loading(inventory: Path, kind: str):
    from importlib.machinery import EXTENSION_SUFFIXES

    finder = _InventoriedImports(capture_source_binding(inventory), inventory)
    directory = inventory / "scripts"
    if kind == "extension":
        (directory / ("hidden" + EXTENSION_SUFFIXES[0])).write_bytes(b"not loaded")
    else:
        source = directory / "hidden.py"
        source.write_text("raise AssertionError('unbound code must not execute')\n")
        if kind == "sourceless":
            py_compile.compile(str(source), cfile=str(directory / "hidden.pyc"), doraise=True)
            source.unlink()
    with pytest.raises(ReplayBindingError, match="not inventoried"):
        finder.find_spec("scripts.hidden", [str(directory)])
