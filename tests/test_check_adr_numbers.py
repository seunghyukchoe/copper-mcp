from __future__ import annotations

from pathlib import Path

import pytest

from scripts import check_adr_numbers


def _adr(root: Path, filename: str, heading_number: str | None = None) -> None:
    number = heading_number if heading_number is not None else filename[:4]
    path = root / check_adr_numbers.ADR_DIR / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"# ADR-{number}: A decision\n\n- Status: Accepted\n\n## Context\n\nSome context.\n",
        encoding="utf-8",
    )


def _index(root: Path, rows: str, next_unused: str = "0002") -> None:
    path = root / check_adr_numbers.ADR_INDEX
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "# Architecture Decision Records\n\n"
        "## Adding an ADR\n\n"
        "1. Copy `template.md` and assign the next unused number — "
        f"currently **{next_unused}**.\n\n"
        "## Index\n\n"
        "| ADR | Title | Status |\n|---|---|---|\n"
        f"{rows}\n"
        "## Reading order\n\nNothing else.\n",
        encoding="utf-8",
    )


def _row(number: str, filename: str) -> str:
    return f"| [{number}]({filename}) | A decision | Accepted |\n"


def _run(root: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    monkeypatch.setattr(check_adr_numbers, "ROOT", root)
    failures: list[str] = []
    notes: list[str] = []
    adrs = check_adr_numbers._discover_adrs(failures)
    by_number = check_adr_numbers._check_unique_numbers(adrs, failures)
    check_adr_numbers._check_headings(adrs, failures)
    text = (root / check_adr_numbers.ADR_INDEX).read_text(encoding="utf-8")
    check_adr_numbers._check_index(by_number, text, failures, notes)
    check_adr_numbers._check_next_unused(by_number, text, failures)
    return failures, notes


def test_clean_tree_passes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0002-second.md")
    _index(tmp_path, _row("0001", "0001-first.md") + _row("0002", "0002-second.md"), "0003")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == []


def test_duplicate_adr_number_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The ADR-0066 incident: two branches, two slugs, one number, no merge conflict."""
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0001-also-first.md")
    _index(tmp_path, _row("0001", "0001-also-first.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert any("reuses ADR number 0001" in failure for failure in failures)


def test_heading_filename_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md", heading_number="0066")
    _index(tmp_path, _row("0001", "0001-first.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == [
        "docs/adr/0001-first.md is headed ADR-0066 but its filename allocates ADR-0001; "
        "a renumbered ADR must have both changed"
    ]


def test_adr_missing_from_index_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0002-second.md")
    _index(tmp_path, _row("0001", "0001-first.md"), "0003")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == [
        "docs/adr/README.md does not index docs/adr/0002-second.md; "
        "every ADR appears in the index exactly once"
    ]


def test_index_entry_without_a_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _index(tmp_path, _row("0001", "0001-first.md") + _row("0002", "0002-second.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert any("indexes ADR-0002, but no such file exists" in failure for failure in failures)


def test_index_entry_pointing_at_the_wrong_file_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _adr(tmp_path, "0001-first.md")
    _index(tmp_path, _row("0001", "0001-renamed.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == [
        "docs/adr/README.md:11 links ADR-0001 to '0001-renamed.md', but that ADR is 0001-first.md"
    ]


def test_adr_indexed_twice_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _index(tmp_path, _row("0001", "0001-first.md") + _row("0001", "0001-first.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert any("indexes ADR-0001 again" in failure for failure in failures)


def test_index_out_of_order_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0002-second.md")
    _index(tmp_path, _row("0002", "0002-second.md") + _row("0001", "0001-first.md"), "0003")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == [
        "docs/adr/README.md lists ADR-0001 after ADR-0002; the index is ordered by number"
    ]


def test_unallocated_number_passes_as_information(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """ADR-0027 and ADR-0067 are spent numbers, not defects."""
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0003-third.md")
    _index(tmp_path, _row("0001", "0001-first.md") + _row("0003", "0003-third.md"), "0004")

    failures, notes = _run(tmp_path, monkeypatch)

    assert failures == []
    assert any("ADR-0002" in note for note in notes)


def test_stale_next_unused_number_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _adr(tmp_path, "0001-first.md")
    _adr(tmp_path, "0002-second.md")
    _index(tmp_path, _row("0001", "0001-first.md") + _row("0002", "0002-second.md"), "0002")

    failures, _ = _run(tmp_path, monkeypatch)

    assert failures == [
        "docs/adr/README.md advertises 0002 as the next unused ADR number, but the highest "
        "allocated is ADR-0002, so it is 0003"
    ]


def test_repository_adrs_allocate_numbers_cleanly() -> None:
    """The committed tree must satisfy every rule above."""
    failures: list[str] = []
    notes: list[str] = []
    adrs = check_adr_numbers._discover_adrs(failures)
    by_number = check_adr_numbers._check_unique_numbers(adrs, failures)
    check_adr_numbers._check_headings(adrs, failures)
    text = (check_adr_numbers.ROOT / check_adr_numbers.ADR_INDEX).read_text(encoding="utf-8")
    check_adr_numbers._check_index(by_number, text, failures, notes)
    check_adr_numbers._check_next_unused(by_number, text, failures)

    assert failures == []
    assert len(by_number) == len(adrs)
