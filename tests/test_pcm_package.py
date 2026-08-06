"""The KiCad Plugin and Content Manager package: schema, reproducibility, and boundary.

These tests answer four questions that a hand-checked package cannot answer once and stay
answered:

1. Does `metadata.json` satisfy KiCad's *actual* schema? The vendored copies under
   `schemas/kicad-pcm/` are the authority, not the prose on dev-docs.kicad.org, which contradicts
   the schema on six points (see `docs/research/kicad-pcm-distribution-v1.md`). Where the prose is
   *stricter* than the schema -- the 150-character description and the 50-character identifier --
   the prose is asserted separately, because a reviewer reads the prose.
2. Is the archive byte-reproducible? Asserted by building it twice in this process and once in a
   fresh subprocess with a different timezone and hash seed, and by inspecting every ZIP entry for
   a field that could have recorded a clock or a host.
3. Do the declared hashes and sizes describe the artifact that was actually built?
4. Does widening the install base change what the plugin discloses? Checked against the *parsed
   tree* of every shipped Python member rather than its bytes -- for the live-IPC token, for a
   literal that could name the default-off flag, for a mutation call, and for an import outside a
   closed three-name allowlist. The token name appears twice in the archive as deliberate prose,
   so a byte grep would have had to be relaxed to tolerate it.

Where a test reimplements a rule, it reimplements the rule KiCad's addons-metadata CI enforces
(`ci/validate/package.py`, `ci/validate/image.py`), not a paraphrase of it.
"""

from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import struct
import subprocess
import sys
import tempfile
import tomllib
import unittest
import unittest.mock
import zipfile
from pathlib import Path, PurePath
from typing import Any

import jsonschema

from scripts import build_pcm_package as builder

ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "kicad-pcm"
PLUGIN_DIR = ROOT / "hardware" / "kicad-ipc-plugin"

# `ci/validate/package.py` in gitlab.com/kicad/addons/metadata, verbatim for the two entries that
# apply to a `plugin` package. Every archive entry must match one of these or submission fails
# with `package contains extra file`.
ALLOWED_FILES = {
    "all": ["/metadata.json", "/resources/icon.png"],
    "plugin": ["/plugins/*", "/plugins/**/*"],
}

# `ci/validate/image.py` defaults.
MAX_ICON_DIMENSION = 64
MAX_ICON_BYTES = 20480

PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _schema(version: str) -> dict[str, Any]:
    document: Any = json.loads(
        (SCHEMA_DIR / f"pcm.{version}.schema.json").read_text(encoding="utf-8")
    )
    assert isinstance(document, dict)
    return document


def _packaged_metadata() -> dict[str, Any]:
    return builder.load_package_metadata()


def _built() -> tuple[bytes, dict[str, Any]]:
    archive = builder.build_archive()
    submission = builder.submission_metadata(
        _packaged_metadata(),
        archive=archive,
        download_url=builder.DOWNLOAD_URL_TEMPLATE.format(
            version=_packaged_metadata()["versions"][0]["version"],
            archive="coppermcp-live-observer.zip",
        ),
    )
    return archive, submission


class PackageMetadataSchemaTests(unittest.TestCase):
    """The metadata validates against the schema KiCad actually publishes."""

    def test_packaged_metadata_validates_against_both_published_schemas(self) -> None:
        """v2 is what a new package targets; v1 is what KiCad 6.0-9.x is served.

        A `plugin`-typed package appears in both the v2 and the down-converted v1 package lists,
        so both have to hold. v1 is the stricter document -- it closes `type` to three members and
        `license` to a fixed list -- and passing it is the claim worth making.
        """

        metadata = _packaged_metadata()
        for version in ("v1", "v2"):
            with self.subTest(schema=version):
                jsonschema.validate(metadata, _schema(version))

    def test_submission_metadata_validates_against_both_published_schemas(self) -> None:
        """Adding the four download fields must not invalidate the document."""

        _, submission = _built()
        for version in ("v1", "v2"):
            with self.subTest(schema=version):
                jsonschema.validate(submission, _schema(version))

    def test_vendored_schemas_are_the_documents_they_claim_to_be(self) -> None:
        """A silently replaced schema would make every validation above vacuous."""

        for version, expected_id in (
            ("v1", "https://go.kicad.org/pcm/schemas/v1"),
            ("v2", "https://go.kicad.org/pcm/schemas/v2"),
        ):
            with self.subTest(schema=version):
                document = _schema(version)
                self.assertEqual(document["$id"], expected_id)
                self.assertEqual(document["$ref"], "#/definitions/Package")

    def test_the_v1_schema_would_reject_a_type_the_official_repository_does_not_serve(
        self,
    ) -> None:
        """Guard the guard: prove v1 is discriminating rather than trivially satisfied."""

        metadata = dict(_packaged_metadata())
        metadata["type"] = "datasource"
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(metadata, _schema("v1"))

    def test_a_missing_required_field_is_actually_caught(self) -> None:
        metadata = dict(_packaged_metadata())
        del metadata["resources"]
        for version in ("v1", "v2"):
            with self.subTest(schema=version), self.assertRaises(jsonschema.ValidationError):
                jsonschema.validate(metadata, _schema(version))

    def test_the_prose_limits_the_schema_does_not_encode_are_respected(self) -> None:
        """dev-docs is stricter than the schema on two fields, and a reviewer reads dev-docs.

        The schema allows a 500-character description and a 100-character identifier; the
        published guidance says 150 and 50. Meeting the tighter number costs nothing and removes
        an argument from the merge request.
        """

        metadata = _packaged_metadata()
        self.assertLessEqual(len(metadata["description"]), 150)
        self.assertGreaterEqual(len(metadata["identifier"]), 2)
        self.assertLessEqual(len(metadata["identifier"]), 50)

    def test_the_identifier_is_reverse_dns_under_a_namespace_we_demonstrably_control(self) -> None:
        """The official repository requires a namespace tied to the hosting service."""

        self.assertEqual(
            _packaged_metadata()["identifier"],
            "com.github.seunghyukchoe.coppermcp-live-observer",
        )

    def test_the_declared_license_is_a_member_of_the_v1_closed_enum(self) -> None:
        """v1's `license` is a Debian-style closed list, not free-form SPDX.

        `Apache-2.0` is in it; `Apache-2.0-only` and similar SPDX-only spellings are not.
        """

        licenses = _schema("v1")["definitions"]["License"]["enum"]
        self.assertIn(_packaged_metadata()["license"], licenses)
        self.assertEqual(_packaged_metadata()["license"], "Apache-2.0")

    def test_the_package_version_tracks_the_single_source_project_version(self) -> None:
        project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(
            _packaged_metadata()["versions"][0]["version"], project["project"]["version"]
        )

    def test_the_plugin_declares_the_ipc_runtime_and_the_release_that_understands_it(self) -> None:
        """`runtime` exists only since KiCad 9.0.1; an older client assumes SWIG and misloads."""

        version = _packaged_metadata()["versions"][0]
        self.assertEqual(version["runtime"], "ipc")
        self.assertEqual(version["kicad_version"], "9.0.1")
        self.assertEqual(_packaged_metadata()["type"], "plugin")

    def test_the_declared_status_is_one_that_can_still_be_walked_back(self) -> None:
        """The addons repository lets `stable` move only to `deprecated`. Do not start there."""

        self.assertEqual(_packaged_metadata()["versions"][0]["status"], "testing")


class PackagedMetadataCrossCheckTests(unittest.TestCase):
    """`validate_packaged_metadata` compares the in-archive copy against the submitted one."""

    def test_the_packaged_copy_declares_exactly_one_version(self) -> None:
        self.assertEqual(len(_packaged_metadata()["versions"]), 1)

    def test_the_packaged_copy_carries_no_download_fields(self) -> None:
        """`download_sha256` in the archive is unsatisfiable: it would hash the file it is in."""

        version = _packaged_metadata()["versions"][0]
        for field in ("download_sha256", "download_url", "download_size", "install_size"):
            with self.subTest(field=field):
                self.assertNotIn(field, version)

    def test_the_builder_refuses_a_packaged_copy_that_carries_one(self) -> None:
        """The rule is enforced at build time, not only asserted about today's file.

        The rejected document is written to a temporary directory rather than over the tracked
        one. A test that poisons a source file and restores it in a `finally` leaves the tree
        corrupted if the run is interrupted, which is too high a price for one assertion.
        """

        poisoned = _packaged_metadata()
        poisoned["versions"][0]["download_sha256"] = "0" * 64
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(poisoned), encoding="utf-8")
            with self.assertRaises(builder.BuildError):
                builder.load_package_metadata(path)

    def test_the_builder_refuses_a_packaged_copy_declaring_more_than_one_version(self) -> None:
        rejected = _packaged_metadata()
        rejected["versions"] = rejected["versions"] * 2
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(rejected), encoding="utf-8")
            with self.assertRaises(builder.BuildError):
                builder.load_package_metadata(path)

    def test_an_unmodified_document_written_elsewhere_still_loads(self) -> None:
        """Guard the guard: the two refusals above must not be rejecting the temporary path."""

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "metadata.json"
            path.write_text(json.dumps(_packaged_metadata()), encoding="utf-8")
            self.assertEqual(builder.load_package_metadata(path), _packaged_metadata())

    def test_the_fields_the_repository_compares_are_all_present_to_compare(self) -> None:
        """`status`, `kicad_version`, `kicad_version_max`, and `platforms` are equality-checked.

        A field present on one side and absent on the other fails submission, so the two documents
        must agree on absence too. The builder derives the submitted copy from the packaged one,
        which is what makes that true by construction rather than by review.
        """

        packaged = _packaged_metadata()["versions"][0]
        _, submission = _built()
        submitted = submission["versions"][0]
        for field in ("status", "kicad_version", "kicad_version_max", "platforms", "runtime"):
            with self.subTest(field=field):
                self.assertEqual(field in packaged, field in submitted)
                if field in packaged:
                    self.assertEqual(packaged[field], submitted[field])


class ArchiveLayoutTests(unittest.TestCase):
    """The archive matches the whitelist KiCad's submission CI applies to every entry."""

    def _entries(self) -> list[zipfile.ZipInfo]:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            return handle.infolist()

    def test_every_entry_is_inside_the_official_whitelist(self) -> None:
        for entry in self._entries():
            with self.subTest(name=entry.filename):
                path = PurePath("/" + entry.filename)
                allowed = ALLOWED_FILES["all"] + ALLOWED_FILES["plugin"]
                self.assertTrue(
                    any(path.match(pattern) for pattern in allowed),
                    f'package would be rejected: extra file "{entry.filename}"',
                )

    def test_the_archive_holds_exactly_the_declared_members(self) -> None:
        """`PurePath.match` semantics have moved between Python releases; this does not."""

        self.assertEqual(
            [entry.filename for entry in self._entries()],
            [
                "metadata.json",
                "plugins/LICENSE",
                "plugins/coppermcp_ipc_plugin.py",
                "plugins/plugin.json",
                "plugins/requirements.txt",
                "resources/icon.png",
            ],
        )

    def test_no_directory_entries_are_written(self) -> None:
        """Each one omitted is a stored mode and timestamp that cannot drift."""

        self.assertEqual([e.filename for e in self._entries() if e.is_dir()], [])

    def test_every_member_is_byte_identical_to_its_source_in_the_tree(self) -> None:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            for name, source in builder.MEMBERS:
                with self.subTest(member=name):
                    self.assertEqual(handle.read(name), source.read_bytes())

    def test_the_archived_entrypoint_is_the_manifest_entrypoint(self) -> None:
        """A manifest naming a file the archive does not contain installs a dead action."""

        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            manifest = json.loads(handle.read("plugins/plugin.json"))
            names = set(handle.namelist())
        entrypoint = manifest["actions"][0]["entrypoint"]
        self.assertIn(f"plugins/{entrypoint}", names)

    def test_the_archive_ships_the_license_the_metadata_declares(self) -> None:
        """Apache-2.0 section 4(a) wants the licence with the distribution.

        A root-level `LICENSE` is not in the whitelist, but `/plugins/*` is, so the obligation is
        met without an entry that would fail submission.
        """

        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            licence = handle.read("plugins/LICENSE").decode("utf-8")
        self.assertIn("Apache License", licence)
        self.assertIn("Version 2.0", licence)

    def test_a_requirements_file_is_present_and_installs_nothing(self) -> None:
        """Both halves are load-bearing, and they pull in opposite directions.

        Without the file KiCad reports `requirements.txt could not be read` and never adds the
        plugin to its ready set, so the action never appears. With a line in it, KiCad runs pip
        against PyPI under `--only-binary :all:`, and `copper-mcp` is not published there, so pip
        exits non-zero and the plugin is equally unreachable.
        """

        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            text = handle.read("plugins/requirements.txt").decode("utf-8")
        self.assertTrue(text.strip())
        directives = [
            line for line in text.splitlines() if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertEqual(directives, [])


class ArchiveReproducibilityTests(unittest.TestCase):
    """The archive is a pure function of its members, their names, and their order."""

    def test_two_builds_in_one_process_are_byte_identical(self) -> None:
        self.assertEqual(builder.build_archive(), builder.build_archive())

    def test_a_fresh_process_with_a_different_clock_and_seed_agrees(self) -> None:
        """The interesting failure is a build that records *where* and *when* it ran."""

        environment = dict(os.environ)
        environment["TZ"] = "Pacific/Kiritimati"
        environment["PYTHONHASHSEED"] = "12345"
        environment["SOURCE_DATE_EPOCH"] = "1"
        completed = subprocess.run(  # noqa: S603
            [sys.executable, str(ROOT / "scripts" / "build_pcm_package.py"), "--no-write"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            env=environment,
            stdin=subprocess.DEVNULL,
        )
        reported = json.loads(completed.stdout)
        self.assertEqual(
            reported["download_sha256"], hashlib.sha256(builder.build_archive()).hexdigest()
        )
        self.assertEqual(reported["download_size"], len(builder.build_archive()))

    def test_no_entry_records_a_clock_a_host_or_a_local_file_mode(self) -> None:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            entries = handle.infolist()
        self.assertTrue(entries)
        for entry in entries:
            with self.subTest(name=entry.filename):
                self.assertEqual(entry.date_time, builder.ZIP_EPOCH)
                self.assertEqual(entry.create_system, builder.UNIX_CREATE_SYSTEM)
                self.assertEqual(entry.external_attr, builder.REGULAR_FILE_0644)
                self.assertEqual(entry.internal_attr, 0)
                self.assertEqual(entry.comment, b"")
                self.assertEqual(entry.extra, b"")

    def test_every_member_is_stored_rather_than_compressed(self) -> None:
        """Stored bytes remove the compressor from the reproducibility argument entirely.

        A deflated archive is reproducible only for a fixed zlib; a stored one is reproducible for
        any implementation that honours the fields set above. The archive is small enough that the
        difference costs nothing worth having.
        """

        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            for entry in handle.infolist():
                with self.subTest(name=entry.filename):
                    self.assertEqual(entry.compress_type, zipfile.ZIP_STORED)
                    self.assertEqual(entry.compress_size, entry.file_size)

    def test_the_archive_is_a_readable_zip_with_no_corrupt_member(self) -> None:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            self.assertIsNone(handle.testzip())

    def test_the_member_order_is_declared_and_sorted(self) -> None:
        names = [name for name, _ in builder.MEMBERS]
        self.assertEqual(names, sorted(names))
        self.assertEqual(len(set(names)), len(names))


class DeclaredMeasurementTests(unittest.TestCase):
    """The numbers in the submitted metadata describe the artifact, not a previous one."""

    def test_the_declared_hash_is_the_hash_of_the_built_archive(self) -> None:
        archive, submission = _built()
        self.assertEqual(
            submission["versions"][0]["download_sha256"], hashlib.sha256(archive).hexdigest()
        )

    def test_the_declared_download_size_is_the_archive_length(self) -> None:
        archive, submission = _built()
        self.assertEqual(submission["versions"][0]["download_size"], len(archive))

    def test_the_declared_install_size_is_the_sum_of_uncompressed_entry_sizes(self) -> None:
        """Computed exactly as `validate_version` computes it: `instsize += entry.file_size`."""

        archive, submission = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            total = sum(e.file_size for e in handle.infolist() if not e.is_dir())
        self.assertEqual(submission["versions"][0]["install_size"], total)

    def test_the_declared_sizes_are_inside_the_repository_tolerance(self) -> None:
        """The validator allows a 1024-byte deviation. Ours is exact; assert it stays exact."""

        archive, submission = _built()
        version = submission["versions"][0]
        self.assertLess(abs(version["download_size"] - len(archive)), 1024)
        self.assertEqual(version["download_size"], len(archive))

    def test_the_submitted_copy_carries_all_four_fields_the_repository_requires(self) -> None:
        _, submission = _built()
        for version in submission["versions"]:
            for field in ("download_url", "download_sha256", "download_size", "install_size"):
                with self.subTest(field=field):
                    self.assertIn(field, version)

    def test_the_download_url_points_at_the_release_asset_the_workflow_uploads(self) -> None:
        """A URL nobody can fetch fails submission at the download step, not at review."""

        _, submission = _built()
        url = submission["versions"][0]["download_url"]
        self.assertTrue(url.startswith("https://github.com/seunghyukchoe/copper-mcp/releases/"))

    def test_the_archive_is_far_inside_the_hundred_megabyte_download_ceiling(self) -> None:
        archive, _ = _built()
        self.assertLess(len(archive), 100 * 1024 * 1024)

    def test_a_stale_hash_would_be_caught(self) -> None:
        """Guard the guard: the equality above must not be comparing a value to itself."""

        archive, submission = _built()
        self.assertNotEqual(
            submission["versions"][0]["download_sha256"],
            hashlib.sha256(archive + b"\x00").hexdigest(),
        )

    def test_the_builder_refuses_a_tag_that_disagrees_before_writing_anything(self) -> None:
        """The release workflow passes the tag; a mismatch must not leave a mislabelled archive."""

        with self.assertRaises(builder.BuildError):
            builder.build(output_dir=ROOT / "dist", expect_version="v9999.0.0", write=False)
        builder.build(
            output_dir=ROOT / "dist", expect_version=f"v{builder.project_version()}", write=False
        )

    def test_the_builder_refuses_a_metadata_version_that_drifted_from_pyproject(self) -> None:
        """`pyproject.toml` is the single source of truth; the package must not be a second one.

        Modelled by moving the project version rather than the package's, which is the same
        comparison from the other side and leaves no tracked file to restore.
        """

        with unittest.mock.patch.object(builder, "project_version", return_value="9999.0.0"):
            with self.assertRaises(builder.BuildError):
                builder.build(output_dir=ROOT / "dist", write=False)


class IconTests(unittest.TestCase):
    """`ci/validate/image.py` opens the icon as PNG and bounds its dimensions and size."""

    def _icon(self) -> bytes:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            return handle.read("resources/icon.png")

    def test_the_icon_is_a_png(self) -> None:
        """The validator passes `formats=["PNG"]`, so a renamed JPEG fails."""

        self.assertTrue(self._icon().startswith(PNG_SIGNATURE))

    def test_the_icon_is_within_the_sixty_four_pixel_bound(self) -> None:
        icon = self._icon()
        width, height = struct.unpack(">II", icon[16:24])
        self.assertEqual(icon[12:16], b"IHDR")
        self.assertLessEqual(width, MAX_ICON_DIMENSION)
        self.assertLessEqual(height, MAX_ICON_DIMENSION)
        self.assertEqual((width, height), (64, 64))

    def test_the_icon_is_within_the_twenty_kilobyte_bound(self) -> None:
        self.assertLessEqual(len(self._icon()), MAX_ICON_BYTES)

    def test_every_icon_chunk_passes_its_own_crc(self) -> None:
        """A truncated or corrupted icon fails the submission download, so check it here."""

        import zlib

        icon = self._icon()
        offset = len(PNG_SIGNATURE)
        tags = []
        while offset < len(icon):
            (length,) = struct.unpack(">I", icon[offset : offset + 4])
            tag = icon[offset + 4 : offset + 8]
            payload = icon[offset + 8 : offset + 8 + length]
            (declared,) = struct.unpack(">I", icon[offset + 8 + length : offset + 12 + length])
            self.assertEqual(declared, zlib.crc32(tag + payload) & 0xFFFFFFFF)
            tags.append(tag)
            offset += 12 + length
        self.assertEqual(tags[0], b"IHDR")
        self.assertEqual(tags[-1], b"IEND")


class DistributionBoundaryTests(unittest.TestCase):
    """What a wider install base can and cannot reach. See SEC-121."""

    def _archived_python(self) -> list[tuple[str, str]]:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            return [
                (name, handle.read(name).decode("utf-8"))
                for name in handle.namelist()
                if name.endswith(".py")
            ]

    def _archived_trees(self) -> list[tuple[str, ast.AST]]:
        return [
            (name, ast.parse(source, filename=name)) for name, source in self._archived_python()
        ]

    def _archived_literals(self) -> list[tuple[str, str]]:
        found = []
        for name, tree in self._archived_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    found.append((name, node.value))
        return found

    def test_the_shipped_code_contains_no_reference_to_the_ipc_token(self) -> None:
        """KiCad hands every launched plugin `KICAD_API_TOKEN`. Nothing here reads or moves it.

        Compared against the parsed tree rather than the file bytes, on purpose. The name appears
        twice in the archive as *prose* -- once in a source comment and once in the PCM
        description that tells an installer the token stays put -- and a byte grep would have to
        be relaxed to tolerate that, which would relax it for a real reference too. Comments and
        the metadata document are not code; the AST is exactly the part that can act.
        """

        for name, tree in self._archived_trees():
            for node in ast.walk(tree):
                with self.subTest(module=name, node=type(node).__name__):
                    if isinstance(node, ast.Constant) and isinstance(node.value, str):
                        self.assertNotIn("KICAD_API_TOKEN", node.value)
                    if isinstance(node, ast.Name):
                        self.assertNotIn("KICAD_API_TOKEN", node.id)
                    if isinstance(node, ast.Attribute):
                        self.assertNotIn("KICAD_API_TOKEN", node.attr)

    def test_the_shipped_code_imports_only_from_a_closed_list(self) -> None:
        """One allowlist forecloses the environment, the filesystem, and every transport at once.

        An import the plugin cannot make is a capability it cannot have, so this replaces a
        marker-by-marker grep for `os.environ`, `socket`, `urllib`, `subprocess`, and friends --
        each of which would have to be extended for the next name someone thought of.
        """

        allowed = {"__future__", "json", "copper_mcp.kicad_ipc"}
        for name, tree in self._archived_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        with self.subTest(module=name, imported=alias.name):
                            self.assertIn(alias.name, allowed)
                elif isinstance(node, ast.ImportFrom):
                    with self.subTest(module=name, imported=node.module):
                        self.assertEqual(node.level, 0)
                        self.assertIn(node.module, allowed)

    def test_the_shipped_code_cannot_enable_the_default_off_live_ipc_flag(self) -> None:
        """The packaged half must not be able to grant what the server half withholds.

        The consent flags are read by `copper_mcp.config` from the process environment. With no
        `os` import the plugin cannot write that environment, and with no literal naming a flag it
        cannot name one to a helper either.
        """

        for name, literal in self._archived_literals():
            for flag in ("COPPER_MCP_ALLOW_LIVE_IPC", "COPPER_MCP_ALLOW_LIVE_APPLY"):
                with self.subTest(module=name, flag=flag):
                    self.assertNotIn(flag, literal)

    def test_the_shipped_code_calls_nothing_that_could_mutate_the_document(self) -> None:
        """ADR-0069 makes this the observation half of the boundary. Assert it stayed that."""

        mutations = {
            "begin_commit",
            "push_commit",
            "create_items",
            "update_items",
            "remove_items",
            "save",
        }
        for name, tree in self._archived_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Attribute):
                    with self.subTest(module=name, attribute=node.attr):
                        self.assertNotIn(node.attr, mutations)
                elif isinstance(node, ast.Name):
                    with self.subTest(module=name, symbol=node.id):
                        self.assertNotIn(node.id, mutations)

    def test_the_only_call_reaching_kicad_is_the_gated_capture_chokepoint(self) -> None:
        """Guard the guard: prove the tree actually contains the observation call it disclaims."""

        called = set()
        for _, tree in self._archived_trees():
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                    called.add(node.func.id)
        self.assertIn("inspect_live_board", called)

    def test_the_non_python_members_carry_no_token_reference_either(self) -> None:
        """The manifest and the requirements file are read by KiCad, not by Python."""

        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            for name in ("plugins/plugin.json", "plugins/requirements.txt"):
                with self.subTest(member=name):
                    self.assertNotIn(b"KICAD_API_TOKEN", handle.read(name))

    def test_the_manifest_registers_one_read_only_pcb_action_and_no_more(self) -> None:
        archive, _ = _built()
        with zipfile.ZipFile(io.BytesIO(archive)) as handle:
            manifest = json.loads(handle.read("plugins/plugin.json"))
        self.assertEqual(len(manifest["actions"]), 1)
        self.assertEqual(manifest["actions"][0]["scopes"], ["pcb"])

    def test_the_pcm_description_tells_an_installer_the_server_half_is_off(self) -> None:
        """A user who finds this in the PCM must not be surprised by a dead button.

        The PCM dialog shows `description` in the list and `description_full` on selection, and
        these are the only text a package can put in front of someone before they install it.
        """

        metadata = _packaged_metadata()
        self.assertIn("COPPER_MCP_ALLOW_LIVE_IPC", metadata["description"])
        self.assertIn("COPPER_MCP_ALLOW_LIVE_IPC=1", metadata["description_full"])
        self.assertIn("KICAD_API_TOKEN", metadata["description_full"])
        for claim in ("never mutates", "off by default"):
            with self.subTest(claim=claim):
                self.assertIn(claim, metadata["description_full"].lower())


class EntrypointFailClosedTests(unittest.TestCase):
    """The entrypoint's exits, one of which only a PCM install can reach."""

    def _module(self) -> Any:
        path = PLUGIN_DIR / "coppermcp_ipc_plugin.py"
        spec = importlib.util.spec_from_file_location("coppermcp_ipc_plugin_under_test", path)
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def _run_without_copper_mcp(self) -> tuple[int, str]:
        """Invoke the entrypoint in the state a fresh PCM install leaves behind.

        `sys.modules[name] = None` is how CPython records "known absent": the import machinery
        raises `ImportError` for such an entry, which is exactly what a KiCad plugin environment
        without CopperMCP produces.
        """

        module = self._module()
        absent = object()
        saved: Any = sys.modules.get("copper_mcp.kicad_ipc", absent)
        buffer = io.StringIO()
        try:
            sys.modules["copper_mcp.kicad_ipc"] = None  # type: ignore[assignment]
            with contextlib.redirect_stdout(buffer):
                status = module.main()
        finally:
            if saved is absent:
                sys.modules.pop("copper_mcp.kicad_ipc", None)
            else:
                sys.modules["copper_mcp.kicad_ipc"] = saved
        return status, buffer.getvalue()

    def test_a_missing_copper_mcp_install_refuses_with_an_actionable_sentence(self) -> None:
        """The PCM installs this file; it cannot install CopperMCP. That gap must not traceback."""

        status, printed = self._run_without_copper_mcp()
        self.assertEqual(status, 1)
        self.assertIn("CopperMCP IPC observer unavailable", printed)
        self.assertIn("pip install 'copper-mcp[kicad]'", printed)

    def test_the_refusal_discloses_no_path_and_no_traceback(self) -> None:
        """An unhandled ImportError would have put a filesystem path in KiCad's warning bar."""

        _, printed = self._run_without_copper_mcp()
        self.assertNotIn(str(ROOT), printed)
        self.assertNotIn("Traceback", printed)
        self.assertEqual(len(printed.splitlines()), 1)

    def test_the_entrypoint_is_importable_without_running_anything(self) -> None:
        """Guard the guard: module scope must stay side-effect free for the above to mean much."""

        self.assertTrue(callable(self._module().main))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
