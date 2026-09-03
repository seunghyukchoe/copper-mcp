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


# --- The population, and the vacuous pass that made it a problem (#244) -------------------
#
# These cases build a *real* Git repository under `tmp_path` rather than faking the
# enumeration, because the defect lived in the enumeration itself: a checker whose
# population came from `git ls-files` alone could answer "no broken links" over a set that
# excluded the document its author had just written. Each case therefore plants an
# untracked-but-present Markdown file and asks what the checker does with it.


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed local executable, argv from this test
        [shutil.which("git") or "/usr/bin/git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


@pytest.fixture()
def git_root(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A miniature repository with one tracked, link-clean Markdown document."""
    root = tmp_path.resolve()
    # Hermetic: a developer's global excludes must not decide what this test observes.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(root / "no-such-global-config"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(root / "no-such-system-config"))
    _repository(root)
    (root / "README.md").write_text(f"See [ADR-0076](docs/adr/{OUTLINE_ADR}).\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    _git(root, "add", "-A")
    monkeypatch.setattr(check_doc_links, "ROOT", root)
    monkeypatch.setattr(check_doc_links, "EXEMPT_TARGETS", {})
    monkeypatch.setattr(check_doc_links, "EXEMPT_LABEL_RECORDS", {})
    return root


def _plant_untracked(root: Path, relative: str, body: str) -> Path:
    document = root / relative
    document.parent.mkdir(parents=True, exist_ok=True)
    document.write_text(body, encoding="utf-8")
    return document


def test_the_tracked_only_population_is_the_one_that_could_pass_vacuously(
    git_root: Path,
) -> None:
    """The defect itself, exercised: the old population cannot see the new note.

    Without this case the fix would only ever be tested through its loud side, and a
    regression to `git ls-files` alone would show up as a checker that still passes --
    which is exactly what #244 reports.
    """
    _plant_untracked(git_root, "docs/migration-note.md", "See [ADR-0001](../adr/0001-none.md).\n")

    tracked_only = check_doc_links._list_markdown()
    population = check_doc_links._repository_markdown()

    assert "docs/migration-note.md" not in tracked_only
    assert "docs/migration-note.md" in population.untracked
    assert git_root / "docs/migration-note.md" in population.checked


def test_an_untracked_markdown_file_with_a_broken_link_cannot_produce_a_green_run(
    git_root: Path,
) -> None:
    _plant_untracked(git_root, "docs/migration-note.md", "See [ADR-0001](../adr/0001-none.md).\n")

    with pytest.raises(SystemExit) as raised:
        check_doc_links.main()

    message = str(raised.value)
    assert "docs/migration-note.md" in message
    assert "does not resolve" in message


def test_an_untracked_markdown_file_whose_label_names_the_wrong_record_also_fails(
    git_root: Path,
) -> None:
    """The label rule reaches the widened population too, not only the target rule."""
    _plant_untracked(git_root, "docs/note.md", f"See [ADR-0077](adr/{OUTLINE_ADR}).\n")

    with pytest.raises(SystemExit) as raised:
        check_doc_links.main()

    assert "docs/note.md" in str(raised.value)
    assert "ADR-0077" in str(raised.value)


def test_reading_an_untracked_file_is_announced_by_name_rather_than_widened_silently(
    git_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _plant_untracked(git_root, "docs/note.md", f"See [ADR-0076](adr/{OUTLINE_ADR}).\n")

    assert check_doc_links.main() == 0

    output = capsys.readouterr().out
    assert "docs/note.md" in output
    assert "1 untracked Markdown file(s) present in the working tree were checked" in output


def test_the_printed_count_reconciles_with_the_markdown_that_exists_on_disk(
    git_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The count is what was read, not what Git tracks."""
    _plant_untracked(git_root, "docs/note.md", f"See [ADR-0076](adr/{OUTLINE_ADR}).\n")
    on_disk = sorted(path for path in git_root.rglob("*.md") if ".git/" not in path.as_posix())

    assert check_doc_links.main() == 0

    population = check_doc_links._repository_markdown()
    assert sorted(population.checked) == on_disk
    assert len(population.tracked_present) + len(population.untracked) == len(on_disk)
    assert f"{len(on_disk)} Markdown files read: " in capsys.readouterr().out


def test_an_ignored_markdown_file_is_not_swept_into_the_population(git_root: Path) -> None:
    """Scoping, the other direction of error: scratch content is still not repository content."""
    (git_root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
    _git(git_root, "add", ".gitignore")
    _plant_untracked(git_root, "scratch/draft.md", "See [nothing](../nowhere/absent.md).\n")

    population = check_doc_links._repository_markdown()

    assert not any("scratch" in name for name in population.untracked)
    assert check_doc_links.main() == 0


def test_a_scratch_file_of_another_kind_is_never_read(git_root: Path) -> None:
    """The pathspec, not the ignore rules, is what keeps an editor swap file out."""
    (git_root / "note.md.swp").write_text("[broken](nowhere.md)\n", encoding="utf-8")

    population = check_doc_links._repository_markdown()

    assert population.untracked == ()
    assert check_doc_links.main() == 0


def test_a_tracked_path_absent_from_the_working_tree_is_named_rather_than_dropped(
    git_root: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reconciliation runs both ways: a count must not include a file nobody read."""
    (git_root / "README.md").unlink()

    assert check_doc_links.main() == 0

    output = capsys.readouterr().out
    population = check_doc_links._repository_markdown()
    assert population.absent == ("README.md",)
    assert "README.md" in output
    assert "could not be read" in output


def test_a_markdown_file_that_cannot_be_read_is_a_failure_not_a_skip(git_root: Path) -> None:
    """An unreadable document is one whose links were not checked; silence is not available."""
    (git_root / "docs" / "binary-note.md").write_bytes(b"\xff\xfe not utf-8 \x00")

    with pytest.raises(SystemExit) as raised:
        check_doc_links.main()

    assert "docs/binary-note.md" in str(raised.value)
    assert "cannot be read" in str(raised.value)
