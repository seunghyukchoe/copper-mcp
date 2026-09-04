"""The sdist ships an allowlist, and the gate reads the artifact the allowlist produces.

A default hatchling sdist swept twenty-eight agent worktrees into the 0.7.0 artifact — 20,452
files and 124 MB against a 544 KB wheel — because hatchling honours ``.gitignore`` but not
``.git/info/exclude``, which is where this repository keeps ``.claude/worktrees/``.  An exclusion
list can only ever be as complete as the last person to edit it; an allowlist cannot regress when
something new lands in the tree.  The first block of tests pins that it stays an allowlist, that
every entry names something real, and that nothing shipping today falls out of it silently.

The second block guards `scripts/check_sdist_tracked.py`, and the reason it builds real tarballs
is the defect that gate shipped with.  Its first version enumerated untracked files with ``git
ls-files --others --exclude-standard`` and called that "exactly the set the build would
over-include".  It was not: that enumeration honours ``core.excludesFile`` and ``.git/info/
exclude`` as well as ``.gitignore``, hatchling honours only the last, and review packed a file
hidden by ``$GIT_DIR/info/exclude`` into the sdist while the gate printed "passed".  A model of
the build passes by omission exactly where it disagrees with the build, so the tests below run
the build and read its members.  `test_the_built_sdist_names_every_untracked_file_...` is the
case the old gate could not report, and is therefore the proof that this one can.
"""

from __future__ import annotations

import importlib.util
import os
import shutil
import subprocess
import tomllib
from pathlib import Path
from textwrap import dedent

import pytest

from scripts import check_sdist_tracked

ROOT = Path(__file__).resolve().parents[1]
_SDIST = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["hatch"][
    "build"
]["targets"]["sdist"]

# Tracked top-level entries the sdist deliberately does not ship: local tooling and CI wiring that
# a consumer of the source distribution has no use for.
_DELIBERATELY_UNSHIPPED = frozenset(
    {
        ".dockerignore",
        ".editorconfig",
        ".env.example",
        ".gitattributes",
        ".gitignore",
        ".pre-commit-config.yaml",
        ".python-version",
    }
)


def test_the_sdist_is_configured_as_an_allowlist() -> None:
    assert "include" in _SDIST, "the sdist must name what it ships, not what it omits"
    assert "exclude" not in _SDIST, (
        "an exclusion list is only as complete as its last edit; keep the allowlist"
    )


def test_every_allowlist_entry_names_something_that_exists() -> None:
    missing = [entry for entry in _SDIST["include"] if not (ROOT / entry.lstrip("/")).exists()]
    assert not missing, f"allowlist names paths that do not exist: {missing}"


def test_no_tracked_top_level_entry_falls_out_of_the_sdist_unnoticed() -> None:
    tracked = {
        line.split("/", 1)[0]
        for line in subprocess.run(  # noqa: S603 - fixed git argv, no external input
            [shutil.which("git") or "/usr/bin/git", "ls-files"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.splitlines()
        if line
    }
    allowed = {entry.lstrip("/") for entry in _SDIST["include"]}
    unaccounted = tracked - allowed - _DELIBERATELY_UNSHIPPED - {".github"}
    assert not unaccounted, (
        "new tracked top-level entries are neither shipped nor recorded as deliberately "
        f"unshipped: {sorted(unaccounted)}"
    )


# --- `untracked_members`, the pure comparison ---------------------------------------------


def test_a_member_the_repository_never_recorded_is_named() -> None:
    assert check_sdist_tracked.untracked_members(
        ["PKG-INFO", "docs/SCRATCH-NOTE.md", "src/kept.py"], {"src/kept.py"}
    ) == ["docs/SCRATCH-NOTE.md"]


def test_the_backend_generated_pkg_info_is_not_a_finding_on_its_own() -> None:
    """The one exception, and the only one: a tarball of nothing else must pass."""

    assert check_sdist_tracked.untracked_members(["PKG-INFO"], set()) == []


def test_a_tracked_member_is_not_named() -> None:
    assert check_sdist_tracked.untracked_members(["src/kept.py"], {"src/kept.py"}) == []


def test_the_generated_exception_may_not_widen_into_a_blanket_pass() -> None:
    """Widening `generated` to every member is a silent pass, so pin both halves.

    The comparison must still report an untracked file while `PKG-INFO` sits beside
    it, and the exception itself must stay the single name the evidence supports —
    the 0.12.0 audit found exactly one untracked member in 937.
    """

    assert check_sdist_tracked.untracked_members(["PKG-INFO", "docs/SCRATCH-NOTE.md"], set()) == [
        "docs/SCRATCH-NOTE.md"
    ]
    assert check_sdist_tracked.GENERATED_MEMBERS == frozenset({"PKG-INFO"})


# --- `main()`, the refusal branch -----------------------------------------------------------
#
# A mutant that emptied the finding list (`extras = []`) survived every test this file
# carried before, because nothing here ever reached the refusal. These two do.


def _stub_build(monkeypatch: pytest.MonkeyPatch, members: list[str], tracked: set[str]) -> None:
    monkeypatch.setattr(
        check_sdist_tracked, "build_sdist", lambda root, outdir: outdir / "probe-0.0.1.tar.gz"
    )
    monkeypatch.setattr(check_sdist_tracked, "sdist_members", lambda tarball: members)
    monkeypatch.setattr(check_sdist_tracked, "_tracked_files", lambda root: tracked)


def test_main_refuses_and_names_the_untracked_member(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_build(
        monkeypatch,
        ["PKG-INFO", "docs/SCRATCH-NOTE.md", "src/kept.py"],
        {"src/kept.py"},
    )

    with pytest.raises(SystemExit) as raised:
        check_sdist_tracked.main(tmp_path)

    message = str(raised.value)
    assert "docs/SCRATCH-NOTE.md" in message
    assert "src/kept.py" not in message


def test_main_returns_zero_when_every_member_is_tracked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_build(monkeypatch, ["PKG-INFO", "src/kept.py"], {"src/kept.py"})

    assert check_sdist_tracked.main(tmp_path) == 0


# --- The real build, including the case the modelled gate could not see ---------------------


def _git(root: Path, *arguments: str) -> None:
    subprocess.run(  # noqa: S603 - fixed local executable, argv from this test
        [shutil.which("git") or "/usr/bin/git", *arguments],
        cwd=root,
        check=True,
        capture_output=True,
    )


def _require_build() -> None:
    """`build` is a declared development dependency, so a miss is a CI failure."""

    if importlib.util.find_spec("build") is None:  # pragma: no cover - environment-dependent
        if os.environ.get("CI"):
            pytest.fail("the `build` package is a development dependency and is missing")
        pytest.skip("the `build` package is not installed; `pip install -e '.[dev]'`")


@pytest.fixture()
def hatchling_project(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A miniature hatchling project: one tracked file and two untracked ones.

    The second untracked file is hidden only by ``$GIT_DIR/info/exclude``. Git's
    ``--exclude-standard`` enumeration does not report it and hatchling packs it
    anyway, which is precisely the disagreement that made the modelled gate pass
    over a file it was supposed to catch.
    """

    root = (tmp_path / "project").resolve()
    root.mkdir()
    # Hermetic: a developer's global excludes must not decide what this test observes.
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(tmp_path / "no-such-global-config"))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(tmp_path / "no-such-system-config"))
    (root / "pyproject.toml").write_text(
        dedent("""
            [build-system]
            requires = ["hatchling"]
            build-backend = "hatchling.build"

            [project]
            name = "miniature-probe"
            version = "0.0.1"

            [tool.hatch.build.targets.sdist]
            include = ["/src", "/docs"]
            """).lstrip(),
        encoding="utf-8",
    )
    (root / "src").mkdir()
    (root / "docs").mkdir()
    (root / "src" / "kept.py").write_text("# kept\n", encoding="utf-8")
    (root / "docs" / "SCRATCH-NOTE.md").write_text("# scratch\n", encoding="utf-8")
    (root / "docs" / "hidden-by-info-exclude.md").write_text("# hidden\n", encoding="utf-8")
    _git(root, "init", "--quiet")
    (root / ".git" / "info").mkdir(exist_ok=True)
    (root / ".git" / "info" / "exclude").write_text("hidden-by-info-exclude.md\n", encoding="utf-8")
    _git(root, "add", "pyproject.toml", "src/kept.py")
    return root


def test_the_built_sdist_names_every_untracked_file_including_one_hidden_by_info_exclude(
    hatchling_project: Path, tmp_path: Path
) -> None:
    """The case #256's first gate reported as "passed"."""

    _require_build()
    outdir = tmp_path / "dist"
    outdir.mkdir()

    tarball = check_sdist_tracked.build_sdist(hatchling_project, outdir)
    members = check_sdist_tracked.sdist_members(tarball)
    tracked = check_sdist_tracked._tracked_files(hatchling_project)
    extras = check_sdist_tracked.untracked_members(members, tracked)

    # The premise, asserted rather than assumed: hatchling packs the info/exclude file.
    assert "docs/hidden-by-info-exclude.md" in members
    assert "docs/hidden-by-info-exclude.md" not in tracked

    assert extras == ["docs/SCRATCH-NOTE.md", "docs/hidden-by-info-exclude.md"]


def test_a_fully_tracked_project_produces_no_findings(
    hatchling_project: Path, tmp_path: Path
) -> None:
    """The green direction, and the guard on the top-level prefix.

    Every member here is tracked, so anything that leaves the `<name>-<version>/`
    prefix on the member names turns all of them into findings and this fails.
    """

    _require_build()
    _git(hatchling_project, "add", "-f", "docs")
    outdir = tmp_path / "dist"
    outdir.mkdir()

    tarball = check_sdist_tracked.build_sdist(hatchling_project, outdir)
    members = check_sdist_tracked.sdist_members(tarball)
    tracked = check_sdist_tracked._tracked_files(hatchling_project)

    assert "src/kept.py" in members, "the prefix must be stripped from member names"
    assert check_sdist_tracked.untracked_members(members, tracked) == []
