#!/usr/bin/env python3
"""Build the KiCad Plugin and Content Manager archive for the CopperMCP live observer.

The archive is byte-reproducible by construction. Every field a ZIP entry carries that could
record *when* or *where* a build ran is set to a constant here: the modification time is the
1980-01-01 ZIP epoch, the mode is a fixed 0644, the host system is fixed to Unix, and members are
written in one declared order rather than in directory-walk order. Members are **stored**, not
deflated, so the archive's bytes do not depend on a compressor version either -- the file is a
pure function of the member names, the member contents, and that order. Nothing is copied to a
temporary directory first, so no path leaks in.

That property is what makes the metadata mechanical rather than hand-written. `download_sha256`,
`download_size`, and `install_size` are measured from the artifact this script just produced and
written into a second file -- the repository-side `metadata.json` a human submits to KiCad's
addons-metadata repository -- so no one ever transcribes a hash.

The two metadata files are deliberately different documents, because KiCad's submission CI
requires them to be. The copy *inside* the archive must carry exactly one version and must not
carry `download_sha256`; the copy *submitted* must carry `download_sha256`, `download_url`,
`download_size`, and `install_size` on every version. See
`docs/research/kicad-pcm-distribution-v1.md`.
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import tomllib
import zipfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Final

ROOT: Final = Path(__file__).resolve().parents[1]
PLUGIN_DIR: Final = ROOT / "hardware" / "kicad-ipc-plugin"
PCM_DIR: Final = PLUGIN_DIR / "pcm"

PACKAGE_STEM: Final = "coppermcp-live-observer"
DOWNLOAD_URL_TEMPLATE: Final = (
    "https://github.com/seunghyukchoe/copper-mcp/releases/download/v{version}/{archive}"
)

# The ZIP epoch. `zipfile` cannot represent anything earlier, and every DOS timestamp field is
# derived from this tuple, so no clock reading reaches the archive.
ZIP_EPOCH: Final = (1980, 1, 1, 0, 0, 0)
UNIX_CREATE_SYSTEM: Final = 3
REGULAR_FILE_0644: Final = 0o100644 << 16

# (archive member name, source file). The order is the write order and is asserted to be sorted,
# so appending a member out of place is a build failure rather than a silent digest change.
#
# Every member here is inside KiCad's addons-metadata whitelist for a `plugin` package:
# `/metadata.json`, `/resources/icon.png`, and `/plugins/**`. Anything else fails submission with
# `package contains extra file`. `plugins/LICENSE` is inside `/plugins/*` and is how the archive
# satisfies Apache-2.0 section 4(a) without a root-level file the whitelist would reject.
MEMBERS: Final[tuple[tuple[str, Path], ...]] = (
    ("metadata.json", PCM_DIR / "metadata.json"),
    ("plugins/LICENSE", ROOT / "LICENSE"),
    ("plugins/coppermcp_ipc_plugin.py", PLUGIN_DIR / "coppermcp_ipc_plugin.py"),
    ("plugins/plugin.json", PLUGIN_DIR / "plugin.json"),
    ("plugins/requirements.txt", PCM_DIR / "requirements.txt"),
    ("resources/icon.png", PCM_DIR / "icon.png"),
)


class BuildError(RuntimeError):
    """A precondition of a releasable package archive does not hold."""


def project_version() -> str:
    """Return the single-source project version from `pyproject.toml`."""

    document = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = document["project"]["version"]
    if not isinstance(version, str):  # pragma: no cover - malformed pyproject
        raise BuildError("project version is not a string")
    return version


def load_package_metadata(path: Path | None = None) -> dict[str, Any]:
    """Return the in-archive metadata document, refusing a shape the PCM would reject.

    `path` exists so that a test can hand this function a rejected document without writing one
    into the tree and relying on a `finally` to undo it. An interrupted run must not be able to
    leave a poisoned `metadata.json` behind.
    """

    source = PCM_DIR / "metadata.json" if path is None else path
    metadata: Any = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise BuildError("package metadata is not a JSON object")
    versions = metadata.get("versions")
    # KiCad's submission CI requires the packaged copy to declare exactly one version and to omit
    # download_sha256; a build that produced anything else would fail review, not merge.
    if not isinstance(versions, list) or len(versions) != 1:
        raise BuildError("package metadata must declare exactly one version")
    if not isinstance(versions[0], dict):
        raise BuildError("package metadata version is not a JSON object")
    for field in ("download_sha256", "download_url", "download_size", "install_size"):
        if field in versions[0]:
            raise BuildError(f"packaged metadata must not carry {field}")
    return metadata


def _member_bytes() -> tuple[tuple[str, bytes], ...]:
    """Read every declared member, in the declared order."""

    names = [name for name, _ in MEMBERS]
    if names != sorted(names):
        raise BuildError("archive members are not in sorted order")
    if len(set(names)) != len(names):
        raise BuildError("archive members are not unique")
    contents = []
    for name, source in MEMBERS:
        if not source.is_file():
            raise BuildError(f"missing package source file: {source.relative_to(ROOT)}")
        contents.append((name, source.read_bytes()))
    return tuple(contents)


def build_archive() -> bytes:
    """Return the package archive bytes. Equal inputs always give equal output."""

    buffer = io.BytesIO()
    # No directory entries are written. KiCad's validator skips them, and every one we omit is a
    # name whose stored mode and timestamp cannot drift.
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, payload in _member_bytes():
            info = zipfile.ZipInfo(filename=name, date_time=ZIP_EPOCH)
            info.compress_type = zipfile.ZIP_STORED
            info.create_system = UNIX_CREATE_SYSTEM
            info.external_attr = REGULAR_FILE_0644
            info.internal_attr = 0
            archive.writestr(info, payload)
    return buffer.getvalue()


def install_size() -> int:
    """Return the uncompressed byte total, computed the way KiCad's validator computes it."""

    return sum(len(payload) for _, payload in _member_bytes())


def submission_metadata(
    metadata: Mapping[str, Any],
    *,
    archive: bytes,
    download_url: str,
) -> dict[str, Any]:
    """Return the repository-side metadata document, measured from `archive`."""

    document = json.loads(json.dumps(metadata))
    version = document["versions"][0]
    version["download_url"] = download_url
    version["download_sha256"] = hashlib.sha256(archive).hexdigest()
    version["download_size"] = len(archive)
    version["install_size"] = install_size()
    return document


def build(
    *,
    output_dir: Path,
    download_url: str | None = None,
    expect_version: str | None = None,
    write: bool = True,
) -> dict[str, Any]:
    """Build the archive and its submission metadata, returning a summary."""

    metadata = load_package_metadata()
    declared = metadata["versions"][0]["version"]
    expected = project_version()
    if declared != expected:
        raise BuildError(
            f"package metadata declares version {declared}, but pyproject.toml says {expected}"
        )
    # Checked before anything is written, so a tag that disagrees with the tree leaves no
    # mislabelled archive behind in dist/ for a later step to pick up.
    if expect_version is not None and declared != expect_version.removeprefix("v"):
        raise BuildError(
            f"package version {declared} does not match the requested {expect_version}"
        )

    archive_name = f"{PACKAGE_STEM}-{declared}.zip"
    archive = build_archive()
    url = download_url or DOWNLOAD_URL_TEMPLATE.format(version=declared, archive=archive_name)
    submission = submission_metadata(metadata, archive=archive, download_url=url)

    archive_path = output_dir / archive_name
    submission_path = output_dir / f"{PACKAGE_STEM}-{declared}.metadata.json"
    if write:
        output_dir.mkdir(parents=True, exist_ok=True)
        archive_path.write_bytes(archive)
        submission_path.write_text(
            json.dumps(submission, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )

    version = submission["versions"][0]
    return {
        "identifier": submission["identifier"],
        "version": declared,
        "archive": str(archive_path),
        "metadata": str(submission_path),
        "download_sha256": version["download_sha256"],
        "download_size": version["download_size"],
        "install_size": version["install_size"],
        "download_url": version["download_url"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=ROOT / "dist",
        help="directory to write the archive and submission metadata into (default: dist/)",
    )
    parser.add_argument(
        "--download-url",
        default=None,
        help="override the release download URL recorded in the submission metadata",
    )
    parser.add_argument(
        "--expect-version",
        default=None,
        help="fail unless the built package carries this version (a leading 'v' is accepted)",
    )
    parser.add_argument(
        "--no-write",
        action="store_true",
        help="compute and report without writing any file",
    )
    arguments = parser.parse_args()

    try:
        summary = build(
            output_dir=arguments.output_dir,
            download_url=arguments.download_url,
            expect_version=arguments.expect_version,
            write=not arguments.no_write,
        )
    except BuildError as error:
        print(f"PCM package build failed: {error}")
        return 1

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
