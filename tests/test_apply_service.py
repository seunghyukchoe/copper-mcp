"""The mutating apply path.

This is the only code in the project that changes a user's file, so the tests are mostly about
the cases where it must refuse. Anything that reaches the filesystem does so in a temporary
directory the test owns.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.apply import ApplyTokenAuthority, apply_candidate, lockfile_for
from copper_mcp.apply.contracts import ApplyRequestError, parse_apply_request
from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenError
from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.mcp_contracts import ApplyCandidateToolResponse
from copper_mcp.route_preview import preview_route

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}


class _Fixture:
    """A workspace with one routable board, a preview, and a matching apply request."""

    def __init__(self, directory: Path, *, allow_apply: bool = True) -> None:
        self.workspace = directory
        self.board = directory / "two-pad.kicad_pcb"
        self.board.write_bytes(FIXTURE.read_bytes())
        self.settings = replace(Settings(workspace=directory.resolve()), allow_apply=allow_apply)
        self.authority = ApplyTokenAuthority()
        self.preview = preview_route(
            {
                "board": "two-pad.kicad_pcb",
                "net": "AUDIO",
                "layer": "F.Cu",
                "seed": 0,
                "constraints": dict(CONSTRAINTS),
                "settings": {},
                "include_apply_token": True,
            },
            self.settings,
            self.authority,
        ).to_dict()
        assert self.preview["status"] == "routed"
        assert self.preview["apply_token"] is not None
        self.original = self.board.read_bytes()

    def request(self, **overrides: Any) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "board": "two-pad.kicad_pcb",
            "candidate": self.preview["candidate"],
            "apply_token": self.preview["apply_token"],
            "expect_board_revision": self.preview["board_revision"],
            "constraints": dict(CONSTRAINTS),
        }
        payload.update(overrides)
        return payload

    def apply(self, **overrides: Any) -> dict[str, Any]:
        document = apply_candidate(
            self.request(**overrides), self.settings, self.authority
        ).to_dict()
        ApplyCandidateToolResponse.model_validate(document)
        return document


class _Case(unittest.TestCase):
    def setUp(self) -> None:
        import tempfile

        self._directory = tempfile.TemporaryDirectory()
        self.addCleanup(self._directory.cleanup)
        self.fixture = _Fixture(Path(self._directory.name))


class HappyPathTests(_Case):
    def test_an_authorized_apply_changes_the_board_and_reports_its_evidence(self) -> None:
        document = self.fixture.apply()

        self.assertEqual(document["status"], "applied")
        self.assertGreater(document["segments_added"], 0)
        self.assertNotEqual(document["board_revision_before"], document["board_revision_after"])
        self.assertEqual(
            document["verification"],
            {
                "untouched_bytes_identical": "passed",
                "reparse_fail_closed": "passed",
                "ir_equals_source_plus_patch": "passed",
                "kicad_opened_board": "not_run",
                "drc_after_apply": "not_run",
            },
        )
        self.assertNotEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_the_pre_apply_copy_is_the_board_exactly_as_it_was(self) -> None:
        """The copy is the undo, so it has to be byte-exact."""

        document = self.fixture.apply()
        backup = self.fixture.workspace / str(document["backup_path"])
        self.assertTrue(backup.is_file())
        self.assertEqual(backup.read_bytes(), self.fixture.original)
        self.assertIn("pre-apply", backup.name)

    def test_restoring_the_pre_apply_copy_returns_the_original_board(self) -> None:
        """The documented undo, performed the way a user would perform it."""

        document = self.fixture.apply()
        backup = self.fixture.workspace / str(document["backup_path"])
        self.fixture.board.write_bytes(backup.read_bytes())
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_kicad_s_own_backup_files_are_never_touched(self) -> None:
        sibling = self.fixture.workspace / "two-pad.kicad_pcb-bak"
        sibling.write_bytes(b"kicad's own backup")
        self.fixture.apply()
        self.assertEqual(sibling.read_bytes(), b"kicad's own backup")

    def test_the_response_never_echoes_the_apply_token(self) -> None:
        document = self.fixture.apply()
        self.assertNotIn(str(self.fixture.preview["apply_token"]), json.dumps(document))


class AuthorizationTests(_Case):
    def test_the_operator_flag_is_off_by_default(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            fixture = _Fixture(Path(directory), allow_apply=False)
            document = fixture.apply()
            self.assertEqual(document["status"], "refused")
            assert document["diagnostic"] is not None
            self.assertEqual(document["diagnostic"]["code"], "apply_disabled")
            self.assertEqual(fixture.board.read_bytes(), fixture.original)

    def test_the_flag_requires_an_exact_spelling(self) -> None:
        """Truthiness would let "false" or "no" enable board mutation."""

        import os

        for raw in ("true", "yes", "", "TRUE", "2", " 1"):
            with self.subTest(value=raw):
                with patch.dict(
                    os.environ,
                    {"COPPER_MCP_ALLOW_APPLY": raw, "COPPER_MCP_WORKSPACE": str(ROOT)},
                ):
                    with self.assertRaises(ConfigurationError):
                        Settings.from_env()

    def test_hostile_tokens_are_all_refused(self) -> None:
        token = str(self.fixture.preview["apply_token"])
        cases = {
            "empty": "",
            "not base64": "!!!!not-a-token!!!!",
            "truncated": token[:-8],
            "bit flipped": ("A" if token[0] != "A" else "B") + token[1:],
            "padded with junk": token + "AAAA",
        }
        for reason, value in cases.items():
            with self.subTest(reason=reason):
                # A malformed token may be rejected at the request boundary or as a typed
                # refusal; what must hold in every case is that the board did not change.
                try:
                    document = self.fixture.apply(apply_token=value)
                except (ApplyRequestError, ValueError):
                    pass
                else:
                    assert document["diagnostic"] is not None
                    self.assertIn(
                        document["diagnostic"]["code"], {"invalid_token", "invalid_request"}
                    )
                self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_a_token_from_another_process_key_is_refused(self) -> None:
        """The signing key exists only in the issuing process."""

        other = ApplyTokenAuthority()
        foreign = other.issue(
            ApplyBinding(
                candidate_id=str(self.fixture.preview["candidate"]["candidate_id"]),
                base_revision=str(self.fixture.preview["candidate"]["base_revision"]),
                board_revision=str(self.fixture.preview["board_revision"]),
                relative_path="two-pad.kicad_pcb",
            )
        )
        document = self.fixture.apply(apply_token=foreign)
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "invalid_token")
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_a_token_for_a_different_board_or_candidate_is_refused(self) -> None:
        base = ApplyBinding(
            candidate_id=str(self.fixture.preview["candidate"]["candidate_id"]),
            base_revision=str(self.fixture.preview["candidate"]["base_revision"]),
            board_revision=str(self.fixture.preview["board_revision"]),
            relative_path="two-pad.kicad_pcb",
        )
        for field, value in (
            ("candidate_id", "sha256:" + "0" * 64),
            ("base_revision", "sha256:" + "1" * 64),
            ("board_revision", "sha256:" + "2" * 64),
            ("relative_path", "other.kicad_pcb"),
        ):
            with self.subTest(field=field):
                token = self.fixture.authority.issue(replace(base, **{field: value}))
                document = self.fixture.apply(apply_token=token)
                assert document["diagnostic"] is not None
                self.assertIn(document["diagnostic"]["code"], {"invalid_token", "stale_candidate"})
                self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_an_expired_token_is_refused(self) -> None:
        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(ttl_seconds=60, clock=lambda: clock[0])
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")
        token = authority.issue(binding)
        self.assertTrue(authority.verify(token, binding))
        clock[0] += 61
        with self.assertRaises(ApplyTokenError) as caught:
            authority.verify(token, binding)
        self.assertEqual(caught.exception.code, "token_expired")

    def test_a_token_is_single_use(self) -> None:
        first = self.fixture.apply()
        self.assertEqual(first["status"], "applied")
        second = self.fixture.apply()
        assert second["diagnostic"] is not None
        self.assertEqual(second["diagnostic"]["code"], "token_already_used")

    def test_a_token_survives_a_refusal_so_a_legitimate_retry_still_works(self) -> None:
        """Consumption happens on success, not on presentation."""

        lock = lockfile_for(self.fixture.board)
        lock.write_text("someone@host", encoding="utf-8")
        blocked = self.fixture.apply()
        assert blocked["diagnostic"] is not None
        self.assertEqual(blocked["diagnostic"]["code"], "kicad_open")
        lock.unlink()
        self.assertEqual(self.fixture.apply()["status"], "applied")


class LockfileTests(_Case):
    def test_a_kicad_lockfile_is_a_hard_refusal_that_names_the_file(self) -> None:
        lock = lockfile_for(self.fixture.board)
        lock.write_text("someone@host", encoding="utf-8")
        document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "kicad_open")
        self.assertIn(lock.name, document["diagnostic"]["message"])
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_the_lockfile_is_never_removed(self) -> None:
        """Stale locks are a known KiCad bug, but deleting one is the operator's call."""

        lock = lockfile_for(self.fixture.board)
        lock.write_text("someone@host", encoding="utf-8")
        self.fixture.apply()
        self.assertTrue(lock.is_file())
        self.assertEqual(lock.read_text(encoding="utf-8"), "someone@host")


class StalenessTests(_Case):
    def test_a_board_that_moved_before_the_apply_is_refused(self) -> None:
        self.fixture.board.write_bytes(self.fixture.original + b"\n")
        document = self.fixture.apply()
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "stale_candidate")

    def test_a_wrong_expected_revision_is_refused(self) -> None:
        document = self.fixture.apply(expect_board_revision="sha256:" + "0" * 64)
        assert document["diagnostic"] is not None
        self.assertIn(document["diagnostic"]["code"], {"invalid_token", "stale_candidate"})
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_a_board_that_moves_in_the_second_window_is_refused_before_publishing(self) -> None:
        """The window between the first check and the rename is where a GUI save lands."""

        import copper_mcp.apply.service as service

        original_read = service._read_board
        calls = {"n": 0}

        def racing_read(settings: Any, requested: str) -> Any:
            calls["n"] += 1
            if calls["n"] == 2:
                # Simulate another writer landing after the first compare-and-swap.
                self.fixture.board.write_bytes(self.fixture.original + b"\n")
            return original_read(settings, requested)

        with patch.object(service, "_read_board", racing_read):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "stale_candidate")
        self.assertGreaterEqual(calls["n"], 2, "the second read must actually have happened")
        # Refused, so the applied bytes were never published.
        self.assertNotIn(b"1d4206c7", self.fixture.board.read_bytes())

    def test_a_stale_board_is_never_auto_refreshed(self) -> None:
        self.fixture.board.write_bytes(self.fixture.original + b"\n")
        document = self.fixture.apply()
        self.assertEqual(document["status"], "refused")
        self.assertEqual(document["segments_added"], 0)
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original + b"\n")


class DurabilityTests(_Case):
    def test_a_failure_at_each_durability_step_leaves_the_board_intact(self) -> None:
        """Crash injection: the board must be the original or the applied one, never torn."""

        import copper_mcp.security as security

        applied_marker = b"1d4206c7"
        for step in ("write", "fsync", "rename"):
            with self.subTest(step=step):
                import tempfile

                with tempfile.TemporaryDirectory() as directory:
                    fixture = _Fixture(Path(directory))
                    target = {"write": "write", "fsync": "fsync", "rename": "rename"}[step]

                    def exploding(*args: Any, _step: str = target, **kwargs: Any) -> Any:
                        raise OSError(f"simulated {_step} failure")

                    # Scoped to the replacement itself. Injecting globally would also break
                    # the pre-apply copy, which is a different failure with its own test.
                    def failing_replace(
                        *args: Any,
                        _target: str = target,
                        _real: Any = security.replace_workspace_file,
                        **kwargs: Any,
                    ) -> Any:
                        with patch.object(security.os, _target, exploding):
                            return _real(*args, **kwargs)

                    import copper_mcp.apply.service as service_module

                    with patch.object(service_module, "replace_workspace_file", failing_replace):
                        document = apply_candidate(
                            fixture.request(), fixture.settings, fixture.authority
                        ).to_dict()

                    self.assertEqual(document["status"], "refused")
                    content = fixture.board.read_bytes()
                    self.assertEqual(content, fixture.original, "the board must not be left torn")
                    self.assertNotIn(applied_marker, content)
                    leftovers = [
                        item.name
                        for item in Path(directory).iterdir()
                        if item.name.startswith(".copper-mcp-")
                    ]
                    self.assertEqual(leftovers, [], "temporary files must be cleaned up")

    def test_a_backup_that_cannot_be_written_stops_the_apply(self) -> None:
        """No pre-apply copy means no way back, so the apply must not proceed."""

        import copper_mcp.apply.service as service

        def exploding(*args: Any, **kwargs: Any) -> Any:
            raise OSError("simulated backup failure")

        with patch.object(service, "_write_backup", exploding):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "backup_failed")
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_an_existing_identical_backup_is_reused_rather_than_failing(self) -> None:
        """A retry inside the same second finds its own copy already there.

        The name carries the source digest, so an existing file under it is the same board
        state; refusing would fail an apply over a backup that is already correct.
        """

        lock = lockfile_for(self.fixture.board)
        lock.write_text("someone@host", encoding="utf-8")
        blocked = self.fixture.apply()
        assert blocked["diagnostic"] is not None
        self.assertEqual(blocked["diagnostic"]["code"], "kicad_open")
        lock.unlink()

        first = self.fixture.apply()
        self.assertEqual(first["status"], "applied")
        copies = sorted(
            item.name for item in self.fixture.workspace.iterdir() if "pre-apply" in item.name
        )
        self.assertEqual(len(copies), 1, copies)

    def test_a_colliding_backup_with_different_content_is_not_overwritten(self) -> None:
        """Guard the guard: reuse must verify, not assume."""

        import copper_mcp.apply.service as service

        planted: dict[str, Path] = {}

        real_backup = service._write_backup

        def plant_then_backup(settings: Any, board: Any, now: Any) -> str:
            if "path" not in planted:
                from datetime import UTC, datetime

                stamp = datetime.fromtimestamp(now(), tz=UTC).strftime("%Y%m%dT%H%M%SZ")
                digest = board.revision.removeprefix("sha256:")[:16]
                name = f"{board.absolute_path.name}.{stamp}.{digest}.pre-apply.kicad_pcb"
                target = self.fixture.workspace / name
                target.write_bytes(b"(kicad_pcb (version 20260206))")
                planted["path"] = target
            return real_backup(settings, board, now)

        with patch.object(service, "_write_backup", plant_then_backup):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "backup_failed")
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)
        self.assertEqual(
            planted["path"].read_bytes(),
            b"(kicad_pcb (version 20260206))",
            "a colliding backup with different content must not be overwritten",
        )

    def test_a_verification_failure_after_publication_restores_the_board(self) -> None:
        import copper_mcp.apply.service as service

        with patch.object(service, "_verify_after_publish", lambda *args: False):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "apply_verification_failed")
        self.assertIn("restored", document["diagnostic"]["message"])
        self.assertEqual(
            self.fixture.board.read_bytes(),
            self.fixture.original,
            "a board that failed verification must be put back",
        )

    def test_an_unsafe_filesystem_is_refused_rather_than_silently_degraded(self) -> None:
        import copper_mcp.apply.service as service

        with patch.object(service, "unsafe_filesystem", lambda path: "nfs"):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "unsafe_filesystem")
        self.assertIn("nfs", document["diagnostic"]["message"])
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_local_storage_is_not_reported_as_unsafe(self) -> None:
        """Guard the guard: the detector must not refuse ordinary local filesystems."""

        from copper_mcp.apply.service import unsafe_filesystem

        self.assertIsNone(unsafe_filesystem(self.fixture.workspace))


class ReplacePrimitiveTests(_Case):
    """The only clobbering primitive in the project."""

    def test_a_symlink_at_the_target_is_refused(self) -> None:
        from copper_mcp.security import WorkspaceViolationError, replace_workspace_file

        elsewhere = self.fixture.workspace / "elsewhere.kicad_pcb"
        elsewhere.write_bytes(b"(kicad_pcb)")
        link = self.fixture.workspace / "link.kicad_pcb"
        link.symlink_to(elsewhere)
        with self.assertRaises(WorkspaceViolationError):
            replace_workspace_file(
                self.fixture.workspace,
                "link.kicad_pcb",
                b"(kicad_pcb (version 20260206))",
                allowed_suffixes={".kicad_pcb"},
                max_bytes=1_000_000,
            )
        self.assertEqual(elsewhere.read_bytes(), b"(kicad_pcb)")

    def test_a_missing_target_is_refused_rather_than_created(self) -> None:
        """Replacement is not creation; a typo must not quietly make a new board."""

        from copper_mcp.security import WorkspaceViolationError, replace_workspace_file

        with self.assertRaises(WorkspaceViolationError):
            replace_workspace_file(
                self.fixture.workspace,
                "does-not-exist.kicad_pcb",
                b"(kicad_pcb (version 20260206))",
                allowed_suffixes={".kicad_pcb"},
                max_bytes=1_000_000,
            )
        self.assertFalse((self.fixture.workspace / "does-not-exist.kicad_pcb").exists())

    def test_a_path_outside_the_workspace_is_refused(self) -> None:
        from copper_mcp.security import WorkspaceViolationError, replace_workspace_file

        for path in ("../outside.kicad_pcb", "/etc/hosts"):
            with self.subTest(path=path), self.assertRaises(WorkspaceViolationError):
                replace_workspace_file(
                    self.fixture.workspace,
                    path,
                    b"(kicad_pcb)",
                    allowed_suffixes={".kicad_pcb"},
                    max_bytes=1_000_000,
                )

    def test_a_disallowed_suffix_is_refused(self) -> None:
        from copper_mcp.security import WorkspaceViolationError, replace_workspace_file

        with self.assertRaises(WorkspaceViolationError):
            replace_workspace_file(
                self.fixture.workspace,
                "two-pad.kicad_pcb",
                b"(kicad_pcb)",
                allowed_suffixes={".svg"},
                max_bytes=1_000_000,
            )
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)


class RequestBoundaryTests(unittest.TestCase):
    def test_hostile_requests_are_refused_at_the_boundary(self) -> None:
        base = {
            "board": "b.kicad_pcb",
            "candidate": {"candidate_id": "x"},
            "apply_token": "t",
            "expect_board_revision": "sha256:" + "a" * 64,
            "constraints": dict(CONSTRAINTS),
        }
        cases = [
            ({k: v for k, v in base.items() if k != "board"}, "missing board"),
            ({k: v for k, v in base.items() if k != "apply_token"}, "missing token"),
            ({**base, "surprise": 1}, "unknown field"),
            ({**base, "expect_board_revision": "not-a-digest"}, "bad revision"),
            ({**base, "candidate": []}, "candidate is not an object"),
            ({**base, "constraints": {}}, "empty constraints"),
            ([], "request is not an object"),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason), self.assertRaises((ApplyRequestError, ValueError)):
                parse_apply_request(payload)

    def test_a_refusal_does_not_echo_the_rejected_value(self) -> None:
        secret = "PRIVATE-BOARD-NAME-abc"
        with self.assertRaises((ApplyRequestError, ValueError)) as caught:
            parse_apply_request(
                {
                    "board": "b.kicad_pcb",
                    "candidate": {},
                    "apply_token": "t",
                    "expect_board_revision": secret,
                    "constraints": dict(CONSTRAINTS),
                }
            )
        self.assertNotIn(secret, str(caught.exception))


class WorkspaceTests(_Case):
    def test_a_board_outside_the_workspace_is_refused(self) -> None:
        for path in ("../escape.kicad_pcb", "/etc/hosts"):
            with self.subTest(path=path), self.assertRaises(Exception) as caught:
                apply_candidate(
                    self.fixture.request(board=path),
                    self.fixture.settings,
                    self.fixture.authority,
                )
            self.assertNotIsInstance(caught.exception, AssertionError)


class McpSurfaceTests(_Case):
    def test_the_tool_is_listed_even_when_applying_is_disabled(self) -> None:
        """Hiding it would make the capability undiscoverable and invite retry loops.

        A client needs to be able to describe the tool and explain why it refused; a tool that
        vanishes when a flag is off looks like a broken server instead of a locked door.
        """

        import asyncio

        import copper_mcp.mcp_server as server

        disabled = replace(self.fixture.settings, allow_apply=False)
        with patch.object(server, "_SETTINGS", disabled):
            tools = asyncio.run(server.mcp.list_tools())
            names = {tool.name for tool in tools}
            self.assertIn("apply_candidate", names)

            result = asyncio.run(
                server.mcp.call_tool("apply_candidate", {"request": self.fixture.request()})
            )
        self.assertFalse(result.is_error)
        self.assertEqual(result.structured_content["status"], "refused")
        self.assertEqual(result.structured_content["diagnostic"]["code"], "apply_disabled")
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)

    def test_the_tool_declares_itself_destructive_and_not_read_only(self) -> None:
        import asyncio

        import copper_mcp.mcp_server as server

        tools = asyncio.run(server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "apply_candidate")
        assert tool.annotations is not None
        self.assertIs(tool.annotations.read_only_hint, False)
        self.assertIs(tool.annotations.destructive_hint, True)
        self.assertIs(tool.annotations.idempotent_hint, False)
        self.assertIs(tool.annotations.open_world_hint, False)
        assert isinstance(tool.output_schema, dict)
        self.assertIs(tool.output_schema["additionalProperties"], False)

    def test_a_preview_only_issues_a_token_when_asked(self) -> None:
        plain = preview_route(
            {
                "board": "two-pad.kicad_pcb",
                "net": "AUDIO",
                "layer": "F.Cu",
                "seed": 0,
                "constraints": dict(CONSTRAINTS),
                "settings": {},
            },
            self.fixture.settings,
            self.fixture.authority,
        ).to_dict()
        self.assertEqual(plain["status"], "routed")
        self.assertIsNone(plain["apply_token"])


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class RealKiCadTests(_Case):
    def test_the_applied_board_opens_and_the_net_becomes_connected(self) -> None:
        import subprocess
        import tempfile

        def drc(board: Path) -> dict:
            with tempfile.TemporaryDirectory() as directory:
                report = Path(directory) / "drc.json"
                completed = subprocess.run(  # noqa: S603
                    [
                        str(REAL_KICAD_CLI),
                        "pcb",
                        "drc",
                        "--format",
                        "json",
                        "--units",
                        "mm",
                        "--severity-all",
                        "-o",
                        str(report),
                        str(board),
                    ],
                    capture_output=True,
                    check=False,
                    timeout=180,
                )
                assert completed.returncode in (0, 5), completed.stderr
                return json.loads(report.read_text(encoding="utf-8"))

        before = drc(self.fixture.board)
        self.assertGreater(len(before.get("unconnected_items", [])), 0)

        self.assertEqual(self.fixture.apply()["status"], "applied")

        after = drc(self.fixture.board)
        self.assertEqual(len(after.get("unconnected_items", [])), 0)
        errors = [item for item in after.get("violations", []) if item.get("severity") == "error"]
        self.assertEqual(errors, [])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
