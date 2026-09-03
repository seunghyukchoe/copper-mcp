"""The link checker must judge the label as well as the target.

Every case here builds a miniature repository under `tmp_path` and points the
checker at it, so nothing depends on the real tree's current contents. The
mismatch cases all use links whose *target resolves*: that is the whole point of
the class, and the reason target-only checking let three of them through.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from scripts import check_doc_links

OUTLINE_ADR = "0076-segment-assembled-edge-cuts-outline.md"


def _repository(root: Path) -> None:
    """Create the record files a label can legitimately point at."""
    adr = root / "docs" / "adr"
    adr.mkdir(parents=True)
    (adr / OUTLINE_ADR).write_text("# ADR-0076\n", encoding="utf-8")
    (adr / "0093-actionable-off-grid-refusals.md").write_text("# ADR-0093\n", encoding="utf-8")
    (adr / "README.md").write_text("# ADRs\n", encoding="utf-8")
    ledgers = root / "docs" / "ledgers"
    ledgers.mkdir(parents=True)
    for name in ("decision-ledger.md", "risk-register.md", "benchmark-ledger.md"):
        (ledgers / name).write_text("# Ledger\n", encoding="utf-8")


def _check(root: Path, relative: str, body: str, monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(check_doc_links, "ROOT", root)
    monkeypatch.setattr(check_doc_links, "EXEMPT_TARGETS", {})
    monkeypatch.setattr(check_doc_links, "EXEMPT_LABEL_RECORDS", {})
    document = root / relative
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(body, encoding="utf-8")
    failures: list[str] = []
    check_doc_links._check_document(document, failures, set(), set())
    return failures


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    resolved = tmp_path.resolve()
    _repository(resolved)
    return resolved


def test_a_label_naming_the_wrong_adr_fails_even_though_the_target_resolves(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = _check(
        root,
        "docs/architecture/board-ir.md",
        f"See [ADR-0077](../adr/{OUTLINE_ADR}).\n",
        monkeypatch,
    )
    assert len(failures) == 1
    assert "ADR-0077" in failures[0]
    assert "is ADR-0076" in failures[0]


def test_a_label_naming_the_right_adr_passes(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _check(
            root,
            "docs/architecture/board-ir.md",
            f"See [ADR-0076](../adr/{OUTLINE_ADR}).\n",
            monkeypatch,
        )
        == []
    )


def test_a_label_listing_several_adrs_passes_when_one_of_them_is_the_target(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _check(
            root,
            "docs/architecture/board-ir.md",
            f"See [ADR-0076, ADR-0087](../adr/{OUTLINE_ADR}).\n",
            monkeypatch,
        )
        == []
    )


def test_a_prose_label_naming_no_record_is_never_judged(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _check(
            root,
            "docs/architecture/board-ir.md",
            f"See [the assembled outline decision](../adr/{OUTLINE_ADR}).\n",
            monkeypatch,
        )
        == []
    )


def test_a_ledger_identifier_pointed_at_the_wrong_ledger_fails(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = _check(
        root,
        "docs/ledgers/decision-ledger.md",
        "Recorded in [R-117](decision-ledger.md).\n",
        monkeypatch,
    )
    assert len(failures) == 1
    assert "R-117" in failures[0]
    assert "belongs to the D- record space" in failures[0]


def test_a_ledger_identifier_pointed_at_its_own_ledger_passes(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _check(
            root,
            "docs/ledgers/decision-ledger.md",
            "Recorded in [R-117](risk-register.md).\n",
            monkeypatch,
        )
        == []
    )


def test_a_ledger_identifier_pointed_at_an_adr_fails(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = _check(
        root,
        "docs/ledgers/decision-ledger.md",
        f"Recorded in [B-100](../adr/{OUTLINE_ADR}).\n",
        monkeypatch,
    )
    assert len(failures) == 1
    assert "belongs to the ADR- record space" in failures[0]


def test_a_path_that_names_no_record_is_never_judged(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert (
        _check(
            root,
            "docs/ledgers/decision-ledger.md",
            "Indexed in [ADR-0076](../adr/README.md).\n",
            monkeypatch,
        )
        == []
    )


def test_a_reference_definition_label_is_judged_the_same_way(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    failures = _check(
        root,
        "docs/architecture/board-ir.md",
        f"Text using [ADR-0077].\n\n[ADR-0077]: ../adr/{OUTLINE_ADR}\n",
        monkeypatch,
    )
    # The inline reference `[ADR-0077]` carries no target; only the definition
    # is a link, so exactly one failure is expected.
    assert len(failures) == 1
    assert "is ADR-0076" in failures[0]


def test_a_fenced_example_is_not_judged(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert (
        _check(
            root,
            "docs/architecture/board-ir.md",
            f"```\n[ADR-0077](../adr/{OUTLINE_ADR})\n```\n",
            monkeypatch,
        )
        == []
    )


def test_a_recorded_label_exemption_suppresses_exactly_its_own_link(
    root: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(check_doc_links, "ROOT", root)
    monkeypatch.setattr(check_doc_links, "EXEMPT_TARGETS", {})
    monkeypatch.setattr(
        check_doc_links,
        "EXEMPT_LABEL_RECORDS",
        {
            (
                "docs/ledgers/decision-ledger.md",
                "ADR-0079",
                f"../adr/{OUTLINE_ADR}",
            ): "D-155, corrected reference recorded in D-182",
        },
    )
    document = root / "docs" / "ledgers" / "decision-ledger.md"
    document.write_text(
        f"| D-155 | [ADR-0079](../adr/{OUTLINE_ADR}) |\n"
        f"| D-900 | [ADR-0077](../adr/{OUTLINE_ADR}) |\n",
        encoding="utf-8",
    )
    failures: list[str] = []
    used: set[tuple[str, str, str]] = set()
    check_doc_links._check_document(document, failures, set(), used)
    assert used == {("docs/ledgers/decision-ledger.md", "ADR-0079", f"../adr/{OUTLINE_ADR}")}
    assert len(failures) == 1
    assert "ADR-0077" in failures[0]


def test_the_repository_link_check_passes_with_its_real_exemption_lists() -> None:
    assert check_doc_links.main() == 0


def test_an_untracked_markdown_file_fails_the_run_by_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A document Git does not know yet must not be silently excluded (#244)."""

    monkeypatch.setattr(check_doc_links, "_tracked_markdown", lambda: [])
    monkeypatch.setattr(check_doc_links, "_untracked_markdown", lambda: ["docs/new-note.md"])
    monkeypatch.setattr(check_doc_links, "EXEMPT_TARGETS", {})
    monkeypatch.setattr(check_doc_links, "EXEMPT_LABEL_RECORDS", {})
    with pytest.raises(SystemExit) as error:
        check_doc_links.main()
    assert "docs/new-note.md" in str(error.value)
    assert "untracked" in str(error.value)


def test_no_untracked_markdown_keeps_a_clean_run_green(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(check_doc_links, "_tracked_markdown", lambda: [])
    monkeypatch.setattr(check_doc_links, "_untracked_markdown", lambda: [])
    monkeypatch.setattr(check_doc_links, "EXEMPT_TARGETS", {})
    monkeypatch.setattr(check_doc_links, "EXEMPT_LABEL_RECORDS", {})
    assert check_doc_links.main() == 0


def test_untracked_enumeration_lists_only_files_git_does_not_track(
    tmp_path: Path,
) -> None:
    """The guard reads the working tree, not the track list, or it is vacuous."""

    git = shutil.which("git")
    if git is None:
        pytest.skip("git is required to enumerate untracked Markdown files")
    subprocess.run([git, "init", "-q"], cwd=tmp_path, check=True)  # noqa: S603
    (tmp_path / "tracked.md").write_text("# Tracked\n", encoding="utf-8")
    (tmp_path / "fresh-note.md").write_text("# Fresh\n", encoding="utf-8")
    (tmp_path / "notes.txt").write_text("not markdown\n", encoding="utf-8")
    subprocess.run([git, "add", "tracked.md"], cwd=tmp_path, check=True)  # noqa: S603
    assert check_doc_links._untracked_markdown(tmp_path) == ["fresh-note.md"]
