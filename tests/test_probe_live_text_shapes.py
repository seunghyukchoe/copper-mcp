"""Refusal-path tests for the live-editor probe's three guards.

Every test here runs **without a live editor**, which is the point: the guards exist so that a
run which cannot be measured honestly refuses instead of publishing something that reads as
complete. Each guard is exercised in both directions -- the accepting case is asserted too, so a
guard that refused everything would fail these tests rather than pass them vacuously.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from scripts import probe_live_text_shapes as probe


def _repo(tmp_path: Path) -> Path:
    """Build a throwaway git repository with one tracked board file."""

    root = tmp_path / "repo"
    (root / "hardware").mkdir(parents=True)
    board = root / "hardware" / "board.kicad_pcb"
    board.write_bytes(b"(kicad_pcb (version 20260206))")
    for command in (
        ["git", "init", "-q"],
        ["git", "config", "user.email", "t@example.com"],
        ["git", "config", "user.name", "t"],
        ["git", "add", "-A"],
        ["git", "commit", "-qm", "seed"],
    ):
        subprocess.run(command, cwd=root, check=True, capture_output=True)  # noqa: S603
    return root


# --------------------------------------------------------------------------------------
# Guard 2 -- `--board` is validated before a byte of it is read.
# --------------------------------------------------------------------------------------


def test_a_tracked_board_inside_the_repository_is_accepted(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    board, relative = probe.validate_board_argument(root / "hardware" / "board.kicad_pcb", root)
    assert board == root / "hardware" / "board.kicad_pcb"
    assert relative == Path("hardware/board.kicad_pcb")


def test_a_board_outside_the_repository_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    outside = tmp_path / "elsewhere.kicad_pcb"
    outside.write_bytes(b"(kicad_pcb)")
    with pytest.raises(probe.ProbeRefusal, match="inside the repository"):
        probe.validate_board_argument(outside, root)


def test_a_character_device_is_refused_before_it_is_read(tmp_path: Path) -> None:
    """`/dev/zero` is the case that motivated this guard: reading it never returns."""

    root = _repo(tmp_path)
    link = root / "hardware" / "zero.kicad_pcb"
    link.symlink_to("/dev/zero")
    # Caught as a symlink escape -- the resolved path leaves the tree, so no read is attempted.
    with pytest.raises(probe.ProbeRefusal, match="symbolic link"):
        probe.validate_board_argument(link, root)


def test_a_non_regular_file_inside_the_tree_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    fifo = root / "hardware" / "fifo.kicad_pcb"
    os.mkfifo(fifo)
    with pytest.raises(probe.ProbeRefusal, match="regular file"):
        probe.validate_board_argument(fifo, root)


def test_a_directory_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    with pytest.raises(probe.ProbeRefusal, match="regular file"):
        probe.validate_board_argument(root / "hardware", root)


def test_an_oversized_board_is_refused_without_being_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path)
    monkeypatch.setattr(probe, "MAX_BOARD_FILE_BYTES", 8)
    board = root / "hardware" / "board.kicad_pcb"
    opened: list[Path] = []
    original = Path.read_bytes

    def spy(self: Path) -> bytes:
        opened.append(self)
        return original(self)

    monkeypatch.setattr(Path, "read_bytes", spy)
    with pytest.raises(probe.ProbeRefusal, match="over this instrument"):
        probe.validate_board_argument(board, root)
    assert opened == [], "the oversized board must be refused before any read"


def test_an_empty_board_is_refused(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    empty = root / "hardware" / "empty.kicad_pcb"
    empty.write_bytes(b"")
    # Deliberately not staged: emptiness is checked before the tracked check, and this test
    # asserts that ordering by reaching the "empty" refusal from an untracked path.
    with pytest.raises(probe.ProbeRefusal, match="empty"):
        probe.validate_board_argument(empty, root)


def test_an_untracked_board_is_refused(tmp_path: Path) -> None:
    """The artifact labels this file 'the committed board'; an untracked path cannot carry it."""

    root = _repo(tmp_path)
    untracked = root / "hardware" / "scratch.kicad_pcb"
    untracked.write_bytes(b"(kicad_pcb)")
    with pytest.raises(probe.ProbeRefusal, match="git-tracked"):
        probe.validate_board_argument(untracked, root)


# --------------------------------------------------------------------------------------
# Guard 1 -- every measurement belongs to one board revision and one session.
# --------------------------------------------------------------------------------------


def test_an_unchanged_revision_passes_the_checkpoint() -> None:
    revision = {"board_digest": "sha256:aa", "session_revision": "pbkdf2-hmac-sha256:bb"}
    probe.require_same_revision(revision, dict(revision), checkpoint="after_surfaces")


def test_a_moved_board_digest_refuses_and_names_what_moved() -> None:
    before = {"board_digest": "sha256:aa", "session_revision": "pbkdf2-hmac-sha256:bb"}
    after = {"board_digest": "sha256:cc", "session_revision": "pbkdf2-hmac-sha256:bb"}
    with pytest.raises(probe.ProbeRefusal, match="board_digest") as raised:
        probe.require_same_revision(before, after, checkpoint="after_census")
    message = str(raised.value)
    assert "after_census" in message
    assert "No artifact is published" in message
    assert "session_revision" not in message.split("changed:")[1]


def test_a_restarted_session_refuses_even_when_the_board_is_byte_identical() -> None:
    """A reopened, unmodified board has the same digest. Only the session identity sees it."""

    before = {"board_digest": "sha256:aa", "session_revision": "pbkdf2-hmac-sha256:bb"}
    after = {"board_digest": "sha256:aa", "session_revision": "pbkdf2-hmac-sha256:zz"}
    with pytest.raises(probe.ProbeRefusal, match="session_revision"):
        probe.require_same_revision(before, after, checkpoint="after_surfaces")


# --------------------------------------------------------------------------------------
# Guard 3 -- the census is capped by item count and by a shared wall-clock budget.
# --------------------------------------------------------------------------------------


def test_a_census_within_the_item_cap_is_accepted() -> None:
    probe.check_text_item_budget(probe.MAX_TEXT_ITEMS)


def test_an_oversized_census_refuses_rather_than_truncating() -> None:
    with pytest.raises(probe.ProbeRefusal, match="census cap") as raised:
        probe.check_text_item_budget(probe.MAX_TEXT_ITEMS + 1)
    message = str(raised.value)
    assert str(probe.MAX_TEXT_ITEMS) in message
    assert "truncated" in message


def test_a_budget_with_time_remaining_does_not_refuse() -> None:
    ticks = iter([0.0, 1.0, 2.0, 3.0])
    budget = probe.Budget(seconds=10.0, clock=lambda: next(ticks))
    budget.check("a", "b")
    budget.check("c", "d")
    assert budget.calls == 2


def test_an_exhausted_budget_refuses_and_names_both_sides_of_the_boundary() -> None:
    ticks = iter([0.0, 5.0, 99.0, 99.0])
    budget = probe.Budget(seconds=10.0, clock=lambda: next(ticks))
    budget.check("the surfaces", "the census")
    with pytest.raises(probe.ProbeRefusal, match="wall-clock budget") as raised:
        budget.check("the surfaces", "the census")
    message = str(raised.value)
    assert "Measured before the stop: the surfaces" in message
    assert "NOT measured: the census" in message
    assert "No artifact is published" in message


def test_the_budget_bounds_a_loop_that_no_per_call_timeout_would_bound() -> None:
    """Each call is instant; the *sequence* is what runs long. That is the gap being closed."""

    clock = {"t": 0.0}

    def tick() -> float:
        clock["t"] += 1.0
        return clock["t"]

    budget = probe.Budget(seconds=5.0, clock=tick)
    with pytest.raises(probe.ProbeRefusal):
        for _ in range(100):
            budget.check("some items", "the rest")
    assert budget.calls < 100, "the loop must stop at the budget, not run to completion"
