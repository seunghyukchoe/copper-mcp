"""Production-source changes cannot inherit a historical artifact's reproduction claim."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

from scripts.replay_source_binding import (
    _ARTIFACTS,
    _ROOTS,
    ReplayBindingError,
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
