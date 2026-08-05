"""The mutating apply path.

This is the only code in the project that changes a user's file, so the tests are mostly about
the cases where it must refuse. Anything that reaches the filesystem does so in a temporary
directory the test owns.
"""

from __future__ import annotations

import hashlib
import json
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.apply import ApplyTokenAuthority, apply_candidate, lockfile_for
from copper_mcp.apply.contracts import ApplyRequestError, parse_apply_request
from copper_mcp.apply.tokens import MAX_CONSUMED_TOKENS, ApplyBinding, ApplyTokenError
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
        # The token is always issued by a preview taken with apply enabled - that is the real
        # workflow: the operator previews with the capability on. ``self.settings`` is what the
        # apply itself runs under, which some tests deliberately leave disabled.
        enabled = replace(self.settings, allow_apply=True)
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
            enabled,
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

    def test_a_writer_landing_before_the_locked_rename_is_refused(self) -> None:
        """A writer that lands between the first read and the locked swap is caught.

        The compare-and-swap that matters happens *under the lock*, inside
        ``replace_workspace_file``, immediately before the rename. Simulating a concurrent
        write just before that call must make the swap fail rather than clobber it.
        """

        import copper_mcp.apply.service as service

        real_replace = service.replace_workspace_file

        def writer_then_replace(*args: Any, **kwargs: Any) -> Any:
            # A third party writes after the service's first check but before the swap.
            self.fixture.board.write_bytes(self.fixture.original + b"\n")
            return real_replace(*args, **kwargs)

        with patch.object(service, "replace_workspace_file", writer_then_replace):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "stale_candidate")
        # The third party's write survived; our applied bytes were never published.
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original + b"\n")

    def test_two_concurrent_applies_from_the_same_base_do_not_both_win(self) -> None:
        """The confirmed exploit: two applies from one base, only one may change the board.

        The outcome is decided by the real ``flock`` in ``replace_workspace_file``, not by any
        wall-clock timeout. The interleaving is driven through the precheck hook, which runs
        under the lock after the compare-and-swap and just before the rename:

        * Thread A's apply is given a precheck that, still holding the lock and having passed
          its swap, parks on an event. A is now committed to renaming but has not released the
          lock.
        * Thread B is started only once A is parked. B does all of its own work and then blocks
          in ``replace_workspace_file`` trying to acquire the lock A holds. While A is parked, B
          therefore cannot have finished - which is what proves the lock is real: without it, B
          would read the still-unchanged board, pass its swap, and win a second write.
        * A is released. It renames and returns ``applied`` and drops the lock; B acquires it,
          re-reads the now-changed board under its swap, and refuses as ``stale_candidate``.

        The only timeouts are generous liveness guards for a genuinely stuck thread; none of
        them decides ``applied`` versus ``refused``.
        """

        import threading

        second_authority = ApplyTokenAuthority()
        enabled = replace(self.fixture.settings, allow_apply=True)
        second_preview = preview_route(
            {
                "board": "two-pad.kicad_pcb",
                "net": "AUDIO",
                "layer": "F.Cu",
                "seed": 0,
                "constraints": dict(CONSTRAINTS),
                "settings": {},
                "include_apply_token": True,
            },
            enabled,
            second_authority,
        ).to_dict()

        import copper_mcp.apply.service as service

        real_replace = service.replace_workspace_file
        a_holds_lock = threading.Event()
        release_a = threading.Event()
        # If any assertion below fails, releasing A and joining both threads in cleanup stops a
        # parked worker from lingering - a failure must report quickly, never hang on the lock.
        self.addCleanup(release_a.set)

        def dispatch_replace(*args: Any, **kwargs: Any) -> Any:
            # Only the first apply (thread "apply-A") is parked under the lock; the second runs
            # the replacement untouched and blocks naturally on the lock A is holding.
            if threading.current_thread().name != "apply-A":
                return real_replace(*args, **kwargs)
            service_precheck = kwargs.get("precheck")

            def parked_precheck() -> None:
                if service_precheck is not None:
                    service_precheck()
                # A now holds the lock and has passed its compare-and-swap.
                a_holds_lock.set()
                if not release_a.wait(timeout=30):  # pragma: no cover - liveness guard
                    raise RuntimeError("thread A was never released")

            kwargs["precheck"] = parked_precheck
            return real_replace(*args, **kwargs)

        results: dict[str, dict[str, Any]] = {}

        def run(name: str, authority: ApplyTokenAuthority, token: str, revision: str) -> None:
            request = {
                "board": "two-pad.kicad_pcb",
                "candidate": self.fixture.preview["candidate"],
                "apply_token": token,
                "expect_board_revision": revision,
                "constraints": dict(CONSTRAINTS),
            }
            results[name] = apply_candidate(request, self.fixture.settings, authority).to_dict()

        thread_a = threading.Thread(
            name="apply-A",
            target=run,
            args=(
                "a",
                self.fixture.authority,
                str(self.fixture.preview["apply_token"]),
                str(self.fixture.preview["board_revision"]),
            ),
        )
        thread_b = threading.Thread(
            name="apply-B",
            target=run,
            args=(
                "b",
                second_authority,
                str(second_preview["apply_token"]),
                str(second_preview["board_revision"]),
            ),
        )
        self.addCleanup(lambda: thread_a.join(timeout=5))
        self.addCleanup(lambda: thread_b.join(timeout=5))

        with patch.object(service, "replace_workspace_file", dispatch_replace):
            thread_a.start()
            self.assertTrue(
                a_holds_lock.wait(timeout=30), "thread A never reached its parked precheck"
            )
            # A is holding the lock. Start B; it must not be able to complete while A holds it.
            thread_b.start()
            thread_b.join(timeout=2.0)
            self.assertTrue(
                thread_b.is_alive(),
                "thread B completed while A held the lock - the lock is not serialising applies",
            )
            # Release A; both threads must now finish, deterministically, one applied one stale.
            release_a.set()
            thread_a.join(timeout=30)
            thread_b.join(timeout=30)
            self.assertFalse(thread_a.is_alive(), "thread A did not finish")
            self.assertFalse(thread_b.is_alive(), "thread B did not finish")

        self.assertEqual(sorted(results), ["a", "b"], results)
        statuses = sorted(document["status"] for document in results.values())
        self.assertEqual(statuses, ["applied", "refused"], results)
        loser = next(d for d in results.values() if d["status"] == "refused")
        assert loser["diagnostic"] is not None
        self.assertEqual(loser["diagnostic"]["code"], "stale_candidate")
        # Exactly one write landed, and the board on disk is precisely the winner's output.
        import hashlib

        self.assertNotEqual(self.fixture.board.read_bytes(), self.fixture.original)
        winner = next(d for d in results.values() if d["status"] == "applied")
        on_disk = f"sha256:{hashlib.sha256(self.fixture.board.read_bytes()).hexdigest()}"
        self.assertEqual(on_disk, winner["board_revision_after"])

    def test_a_rewrite_before_final_observation_is_unverified_and_spends_token(
        self,
    ) -> None:
        """A writer visible after publication must not be reported as our verified result."""

        import copper_mcp.apply.service as service

        third_party = self.fixture.original + b"\n(comment final-observation writer)\n"
        real_observed_revision = service._final_observed_revision

        def rewrite_then_observe(*args: Any, **kwargs: Any) -> str | None:
            self.fixture.board.write_bytes(third_party)
            return real_observed_revision(*args, **kwargs)

        with patch.object(service, "_final_observed_revision", rewrite_then_observe):
            document = self.fixture.apply()

        expected_revision = f"sha256:{hashlib.sha256(third_party).hexdigest()}"
        self.assertEqual(document["status"], "applied_but_unverified")
        self.assertEqual(document["board_revision_after"], expected_revision)
        self.assertEqual(self.fixture.board.read_bytes(), third_party)
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "apply_verification_failed")

        replay = self.fixture.apply()
        assert replay["diagnostic"] is not None
        self.assertEqual(replay["diagnostic"]["code"], "token_already_used")

    def test_an_unreadable_final_board_is_not_reported_as_applied(self) -> None:
        """A missing final board must not inherit the expected published digest."""

        import copper_mcp.apply.service as service

        real_observed_revision = service._final_observed_revision

        def remove_then_observe(*args: Any, **kwargs: Any) -> str | None:
            self.fixture.board.unlink()
            return real_observed_revision(*args, **kwargs)

        with patch.object(service, "_final_observed_revision", remove_then_observe):
            document = self.fixture.apply()

        self.assertEqual(document["status"], "applied_but_unverified")
        self.assertIsNone(document["board_revision_after"])
        assert document["diagnostic"] is not None
        self.assertIn("could not be observed", document["diagnostic"]["message"])

        self.fixture.board.write_bytes(self.fixture.original)
        replay = self.fixture.apply()
        assert replay["diagnostic"] is not None
        self.assertEqual(replay["diagnostic"]["code"], "token_already_used")

    def test_a_stale_board_is_never_auto_refreshed(self) -> None:
        self.fixture.board.write_bytes(self.fixture.original + b"\n")
        document = self.fixture.apply()
        self.assertEqual(document["status"], "refused")
        self.assertEqual(document["segments_added"], 0)
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original + b"\n")


class DurabilityTests(_Case):
    def test_a_pre_rename_failure_leaves_the_board_untouched(self) -> None:
        """Crash injection before the rename: the board is the original, and says so."""

        import copper_mcp.security as security

        applied_marker = b"1d4206c7"
        # These calls all happen before the rename inside replace_workspace_file. The first
        # write, and the fsync of the temporary, are pre-rename steps.
        for step in ("write",):
            with self.subTest(step=step):
                import tempfile

                with tempfile.TemporaryDirectory() as directory:
                    fixture = _Fixture(Path(directory))

                    def exploding(*args: Any, _step: str = step, **kwargs: Any) -> Any:
                        raise OSError(f"simulated {_step} failure")

                    def failing_replace(
                        *args: Any,
                        _target: str = step,
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
                    self.assertIsNone(document["board_revision_after"])
                    content = fixture.board.read_bytes()
                    self.assertEqual(content, fixture.original, "the board must not be torn")
                    self.assertNotIn(applied_marker, content)
                    leftovers = [
                        item.name
                        for item in Path(directory).iterdir()
                        if item.name.startswith(".copper-mcp-") and item.name.endswith(".tmp")
                    ]
                    self.assertEqual(leftovers, [], "temporary files must be cleaned up")

    def test_a_post_rename_failure_is_reported_as_a_change_not_as_untouched(self) -> None:
        """Finding 3: once the rename happened the board changed, and must be reported so.

        Failing the directory fsync - a step that runs *after* the rename - previously mapped
        to a refusal claiming nothing changed, while the board already held the applied bytes.
        The stale ``board_revision_before`` was then a lie. It must now be
        ``applied_but_unverified`` with the real after-revision.
        """

        import copper_mcp.apply.service as service
        import copper_mcp.security as security

        real_replace = security.replace_workspace_file
        real_fsync = security.os.fsync

        def replace_with_failing_dir_fsync(*args: Any, **kwargs: Any) -> Any:
            # Scope the injection to this one replacement so the backup's own fsyncs do not
            # count. Inside a replacement the first fsync is the temporary (pre-rename, must
            # succeed) and the second is the directory (post-rename, where we inject).
            calls = {"n": 0}

            def fsync(descriptor: int) -> None:
                calls["n"] += 1
                if calls["n"] >= 2:
                    raise OSError("simulated directory fsync failure")
                real_fsync(descriptor)

            with patch.object(security.os, "fsync", fsync):
                return real_replace(*args, **kwargs)

        with patch.object(service, "replace_workspace_file", replace_with_failing_dir_fsync):
            document = self.fixture.apply()

        self.assertEqual(document["status"], "applied_but_unverified")
        self.assertIsNotNone(document["board_revision_after"])
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "apply_verification_failed")
        self.assertIsNotNone(document["backup_path"])
        # The board is genuinely one of the two known states, never torn.
        on_disk = self.fixture.board.read_bytes()
        self.assertIn(on_disk, (self.fixture.original,), "guarded rollback returns the original")
        on_disk_revision = f"sha256:{hashlib.sha256(on_disk).hexdigest()}"
        self.assertEqual(document["board_revision_after"], on_disk_revision)
        self.assertEqual(document["board_revision_after"], document["board_revision_before"])
        replay = self.fixture.apply()
        assert replay["diagnostic"] is not None
        self.assertEqual(replay["diagnostic"]["code"], "token_already_used")

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

    def test_backups_go_in_a_subdirectory_not_beside_the_board(self) -> None:
        """Finding 8: a backup beside the board is itself a valid apply target.

        Cascading ``pre-apply.pre-apply`` names appear if backups sit next to the board, so
        they go in ``.copper-mcp-backups/`` where they are not selectable as ``.kicad_pcb``
        apply targets in the board's own directory.
        """

        document = self.fixture.apply()
        backup_path = str(document["backup_path"])
        self.assertIn(".copper-mcp-backups/", backup_path)
        siblings = [
            item.name
            for item in self.fixture.workspace.iterdir()
            if item.name.endswith(".kicad_pcb")
        ]
        self.assertEqual(siblings, ["two-pad.kicad_pcb"], "no backup beside the board")
        self.assertEqual((self.fixture.workspace / backup_path).read_bytes(), self.fixture.original)

    def test_backups_are_pruned_to_a_bounded_count(self) -> None:
        """Finding 8: a preview→apply loop must not accumulate backups without bound."""

        from copper_mcp.apply.service import (
            MAX_BACKUPS_PER_BOARD,
            _read_board,
            _write_backup,
        )

        board = _read_board(self.fixture.settings, "two-pad.kicad_pcb")
        seconds = iter(range(1, MAX_BACKUPS_PER_BOARD + 6))
        for _ in range(MAX_BACKUPS_PER_BOARD + 5):
            second = next(seconds)
            _write_backup(self.fixture.settings, board, lambda _s=second: float(_s))
        backups_dir = self.fixture.workspace / ".copper-mcp-backups"
        copies = [item for item in backups_dir.iterdir() if item.name.endswith(".kicad_pcb")]
        self.assertLessEqual(len(copies), MAX_BACKUPS_PER_BOARD)

    def test_a_backup_preserves_the_board_permission_bits(self) -> None:
        """Finding 7: a backup must not collapse to 0600."""

        import stat as stat_module

        self.fixture.board.chmod(0o644)
        document = self.fixture.apply()
        backup = self.fixture.workspace / str(document["backup_path"])
        self.assertEqual(stat_module.S_IMODE(backup.stat().st_mode), 0o644)

    def test_an_unconditional_restore_would_destroy_a_third_partys_write(self) -> None:
        """Finding 2: the guarded restore must not clobber a concurrent writer's newer bytes.

        The likeliest cause of a post-publish verification failure is a concurrent writer, so a
        restore that always wrote the pre-apply bytes back would be the data loss it exists to
        prevent. Here a third party's bytes are on disk when the failure is reported; they must
        survive.
        """

        import hashlib

        import copper_mcp.apply.service as service
        from copper_mcp.security import WorkspacePostRenameError

        third_party = self.fixture.original + b"\n(comment third party)\n"
        real_replace = service.replace_workspace_file
        state = {"first": True}

        def replace_then_race_once(*args: Any, **kwargs: Any) -> Any:
            if not state["first"]:
                # Any later call - the guarded restore - runs the real replacement cleanly, so
                # this test isolates the guard itself rather than the restore's own swap.
                return real_replace(*args, **kwargs)
            state["first"] = False
            published = f"sha256:{hashlib.sha256(args[2]).hexdigest()}"
            real_replace(*args, **kwargs)  # our bytes are genuinely published
            # A third party overwrites the applied board immediately after publication, then the
            # post-publish verification is reported as failed.
            self.fixture.board.write_bytes(third_party)
            raise WorkspacePostRenameError("simulated post-publish verification failure", published)

        with patch.object(service, "replace_workspace_file", replace_then_race_once):
            document = self.fixture.apply()

        self.assertEqual(document["status"], "applied_but_unverified")
        # The third party's write is intact - the guarded restore refused to clobber it.
        self.assertEqual(self.fixture.board.read_bytes(), third_party)
        assert document["diagnostic"] is not None
        self.assertIn("did not write", document["diagnostic"]["message"])

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
    def test_a_board_outside_the_workspace_is_a_typed_refusal(self) -> None:
        for path in ("../escape.kicad_pcb", "/etc/hosts"):
            with self.subTest(path=path):
                document = apply_candidate(
                    self.fixture.request(board=path),
                    self.fixture.settings,
                    self.fixture.authority,
                ).to_dict()
                self.assertEqual(document["status"], "refused")
                assert document["diagnostic"] is not None
                self.assertEqual(document["diagnostic"]["code"], "invalid_request")
                self.assertIsNone(document["board_revision_before"])


class LockfileRecheckTests(_Case):
    def test_a_lockfile_appearing_before_the_locked_write_still_refuses(self) -> None:
        """Finding 4: the lockfile is re-checked under the lock, right before the rename.

        The up-front check happens seconds before the write; a GUI opened in between would
        otherwise land its later save on top of ours. Creating the lockfile just before the
        replacement runs must still refuse and leave the board untouched.
        """

        import copper_mcp.apply.service as service

        real_replace = service.replace_workspace_file

        def lock_then_replace(*args: Any, **kwargs: Any) -> Any:
            lockfile_for(self.fixture.board).write_text("late@host", encoding="utf-8")
            return real_replace(*args, **kwargs)

        with patch.object(service, "replace_workspace_file", lock_then_replace):
            document = self.fixture.apply()

        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "kicad_open")
        self.assertEqual(self.fixture.board.read_bytes(), self.fixture.original)


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


class PreviewGatingTests(unittest.TestCase):
    """Finding 5 (preview half) and the flag-gated issuance defence in depth."""

    def _preview(self, board_bytes: bytes, *, allow_apply: bool) -> dict[str, Any]:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "board.kicad_pcb").write_bytes(board_bytes)
            settings = replace(Settings(workspace=workspace.resolve()), allow_apply=allow_apply)
            return preview_route(
                {
                    "board": "board.kicad_pcb",
                    "net": "AUDIO",
                    "layer": "F.Cu",
                    "seed": 0,
                    "constraints": dict(CONSTRAINTS),
                    "settings": {},
                    "include_apply_token": True,
                },
                settings,
                ApplyTokenAuthority(),
            ).to_dict()

    def test_no_token_is_issued_when_apply_is_disabled(self) -> None:
        """A library embedder with apply off must not be handed apply tokens."""

        document = self._preview(FIXTURE.read_bytes(), allow_apply=False)
        self.assertEqual(document["status"], "routed")
        self.assertIsNone(document["apply_token"])

    def test_no_token_is_issued_for_a_board_that_could_never_be_applied(self) -> None:
        """Finding 5: a derived-identity board is unappliable, so it gets no token.

        The append-only apply engine rejects a board whose modeled geometry carries derived
        (content-hash) rather than native identities. Minting a token for such a board used to
        end in an uncaught crash from the destructive tool; now the preview declines the token.
        """

        source = FIXTURE.read_text(encoding="utf-8")
        # Drop the outline's uuid so its Board IR identity becomes ``:derived:``.
        derived = source.replace('\n    (uuid "20000000-0000-0000-0000-000000000005")', "")
        self.assertNotEqual(derived, source)
        document = self._preview(derived.encode("utf-8"), allow_apply=True)
        self.assertEqual(document["status"], "routed", "the board still routes")
        self.assertIsNone(document["apply_token"], "but it must not be handed an apply token")


class DerivedIdentityApplyTests(_Case):
    """Finding 5 (apply half): a derived-identity board must refuse, not crash."""

    def test_a_derived_identity_board_is_a_typed_refusal(self) -> None:
        import tempfile

        source = FIXTURE.read_text(encoding="utf-8")
        derived = source.replace('\n    (uuid "20000000-0000-0000-0000-000000000005")', "")
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "board.kicad_pcb").write_bytes(derived.encode("utf-8"))
            settings = replace(Settings(workspace=workspace.resolve()), allow_apply=True)
            authority = ApplyTokenAuthority()
            # The preview declines a token, so mint one directly for the binding to reach the
            # engine and prove the apply path itself no longer lets KiCadRoutePatchError escape.
            board_revision = (
                "sha256:"
                + __import__("hashlib")
                .sha256((workspace / "board.kicad_pcb").read_bytes())
                .hexdigest()
            )
            preview = preview_route(
                {
                    "board": "board.kicad_pcb",
                    "net": "AUDIO",
                    "layer": "F.Cu",
                    "seed": 0,
                    "constraints": dict(CONSTRAINTS),
                    "settings": {},
                },
                settings,
                authority,
            ).to_dict()
            manifest = preview["candidate"]
            token = authority.issue(
                ApplyBinding(
                    candidate_id=manifest["candidate_id"],
                    base_revision=manifest["base_revision"],
                    board_revision=board_revision,
                    relative_path="board.kicad_pcb",
                )
            )
            document = apply_candidate(
                {
                    "board": "board.kicad_pcb",
                    "candidate": manifest,
                    "apply_token": token,
                    "expect_board_revision": board_revision,
                    "constraints": dict(CONSTRAINTS),
                },
                settings,
                authority,
            ).to_dict()

            self.assertEqual(document["status"], "refused")
            assert document["diagnostic"] is not None
            self.assertEqual(document["diagnostic"]["code"], "splice_assertion_failed")
            self.assertEqual((workspace / "board.kicad_pcb").read_bytes(), derived.encode("utf-8"))


class ManifestBoundsTests(unittest.TestCase):
    """Finding 6: the candidate geometry is bounded before anything is materialised."""

    def _base(self) -> dict[str, Any]:
        return {
            "board": "b.kicad_pcb",
            "candidate": {"candidate_id": "x", "base_revision": "y"},
            "apply_token": "t",
            "expect_board_revision": "sha256:" + "a" * 64,
            "constraints": dict(CONSTRAINTS),
        }

    def test_too_many_vertices_are_refused_at_the_boundary(self) -> None:
        from copper_mcp.apply.contracts import MAX_MANIFEST_VERTICES

        request = self._base()
        request["candidate"]["patch"] = {
            "paths": [{"vertices_nm": [[0, 0]] * (MAX_MANIFEST_VERTICES + 1)}]
        }
        with self.assertRaises(ApplyRequestError):
            parse_apply_request(request)

    def test_too_many_paths_are_refused_at_the_boundary(self) -> None:
        from copper_mcp.apply.contracts import MAX_MANIFEST_PATHS

        request = self._base()
        request["candidate"]["patch"] = {
            "paths": [{"vertices_nm": [[0, 0]]}] * (MAX_MANIFEST_PATHS + 1)
        }
        with self.assertRaises(ApplyRequestError):
            parse_apply_request(request)

    def test_a_pathological_identity_field_is_refused(self) -> None:
        from copper_mcp.apply.contracts import MAX_MANIFEST_FIELD_CHARACTERS

        request = self._base()
        request["candidate"]["candidate_id"] = "z" * (MAX_MANIFEST_FIELD_CHARACTERS + 1)
        with self.assertRaises(ApplyRequestError):
            parse_apply_request(request)


class TokenLifetimeTests(unittest.TestCase):
    """Finding 9: consumed nonces are swept by expiry rather than kept forever or FIFO-evicted.

    The pre-fix store mapped each nonce to ``None`` and evicted only when a count cap was hit,
    so it had no notion of expiry: a consumed nonce lived until enough newer ones pushed it out.
    """

    def test_a_consumed_nonce_is_proactively_removed_once_its_token_expires(self) -> None:
        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(ttl_seconds=60, clock=lambda: clock[0])
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")

        first = authority.issue(binding)
        authority.consume(authority.verify(first, binding))
        self.assertEqual(len(authority._consumed), 1)

        # Past the first token's expiry, any later operation sweeps it - it is not retained
        # indefinitely the way a count-only store would retain it.
        clock[0] += 61
        second = authority.issue(binding)
        authority.consume(authority.verify(second, binding))
        self.assertEqual(len(authority._consumed), 1, "the expired nonce must have been swept")

    def test_sweep_removes_an_expired_nonce_consumed_after_a_newer_live_nonce(self) -> None:
        """Consumption order cannot make an older expiry unreachable behind a live nonce."""

        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(ttl_seconds=60, clock=lambda: clock[0])
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")

        earlier = authority.issue(binding)
        clock[0] += 30
        later = authority.issue(binding)

        later_verified = authority.verify(later, binding)
        authority.consume(later_verified)
        earlier_verified = authority.verify(earlier, binding)
        authority.consume(earlier_verified)

        # The earlier token expires first, but it was inserted second. Sweeping must remove it
        # without deleting the later token, which remains a live replay refusal.
        clock[0] += 31
        trigger = authority.issue(binding)
        authority.consume(authority.verify(trigger, binding))

        self.assertNotIn(earlier_verified.identifier, authority._consumed)
        self.assertIn(later_verified.identifier, authority._consumed)
        with self.assertRaises(ApplyTokenError) as caught:
            authority.verify(later, binding)
        self.assertEqual(caught.exception.code, "token_already_used")

    def test_a_live_consumed_nonce_is_not_evicted_by_newer_arrivals(self) -> None:
        """A still-valid consumed nonce keeps rejecting replays while other tokens come and go."""

        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(ttl_seconds=600, clock=lambda: clock[0])
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")

        guarded = authority.issue(binding)
        authority.consume(authority.verify(guarded, binding))

        # Many other applies happen, none of which advance the clock past the guarded token's
        # 10-minute life. It must still be recognised as already used.
        for _ in range(50):
            clock[0] += 1
            other = authority.issue(binding)
            authority.consume(authority.verify(other, binding))

        with self.assertRaises(ApplyTokenError) as caught:
            authority.verify(guarded, binding)
        self.assertEqual(caught.exception.code, "token_already_used")

    def test_a_tiny_capacity_hint_cannot_evict_a_live_consumed_nonce(self) -> None:
        """Count pressure must never reopen a confirmation after a guarded board restore."""

        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(
            ttl_seconds=600,
            max_consumed=1,
            clock=lambda: clock[0],
        )
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")

        guarded = authority.issue(binding)
        authority.consume(authority.verify(guarded, binding))
        for _ in range(3):
            other = authority.issue(binding)
            authority.consume(authority.verify(other, binding))

        self.assertGreater(len(authority._consumed), authority._max_consumed)
        with self.assertRaises(ApplyTokenError) as caught:
            authority.verify(guarded, binding)
        self.assertEqual(caught.exception.code, "token_already_used")

    def test_consumed_capacity_hint_must_be_a_positive_bounded_integer(self) -> None:
        for hint in (False, 0, -1, MAX_CONSUMED_TOKENS + 1):
            with self.subTest(hint=hint):
                with self.assertRaises(ApplyTokenError) as caught:
                    ApplyTokenAuthority(max_consumed=hint)
                self.assertEqual(caught.exception.code, "invalid_token")

    def test_the_store_does_not_grow_without_bound(self) -> None:
        clock = [1_000_000.0]
        authority = ApplyTokenAuthority(ttl_seconds=60, clock=lambda: clock[0])
        binding = ApplyBinding("c", "sha256:" + "a" * 64, "sha256:" + "b" * 64, "b.kicad_pcb")
        # A long run of applies, each a minute apart, keeps the store bounded because expired
        # nonces are swept as newer ones arrive.
        for _ in range(200):
            clock[0] += 61
            token = authority.issue(binding)
            authority.consume(authority.verify(token, binding))
        self.assertLessEqual(len(authority._consumed), 2)


class DisabledRefusalTests(_Case):
    """Finding 10: the disabled refusal names the real path and fabricates no digest."""

    def test_apply_disabled_uses_the_canonical_path_and_no_synthetic_digest(self) -> None:
        disabled = replace(self.fixture.settings, allow_apply=False)
        document = apply_candidate(
            self.fixture.request(board="./two-pad.kicad_pcb"), disabled, self.fixture.authority
        ).to_dict()
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "apply_disabled")
        # The canonical relative path, not the raw "./"-prefixed request value.
        self.assertEqual(document["board_path"], "two-pad.kicad_pcb")
        # No digest is synthesised for a board that was never read.
        self.assertIsNone(document["board_revision_before"])


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
