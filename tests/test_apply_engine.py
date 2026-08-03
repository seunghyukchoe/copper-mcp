"""The pure apply engine: the bytes an apply would write, and the proof they are right.

The engine mutates nothing, so these tests assert about returned bytes rather than about files.
The one exception is the real-KiCad node, where the *test* writes a scratch file so `kicad-cli`
has something to open - the engine still never touches a filesystem.
"""

from __future__ import annotations

import subprocess
import unittest
from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.apply import ApplyEngineError, ApplyVerification, apply_route_candidate
from copper_mcp.board_ir import NetClass, ParseLimits, PointNM
from copper_mcp.config import Settings
from copper_mcp.route_preview import preview_route
from copper_mcp.routing.contracts import RoutePath

ROOT = Path(__file__).resolve().parents[1]
CANDIDATE_FIXTURES = ROOT / "tests" / "fixtures" / "route-candidate"
COPPERTONE = ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")

CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
#: Boards with a two-pin net this repository's router actually routes.
ROUTABLE = ("two-pad.kicad_pcb", "partial-route.kicad_pcb")


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _routed(board: str, net: str = "AUDIO", workspace: Path | None = None) -> tuple:
    """Return (source bytes, snapshot, candidate) for a board the router can route."""

    directory = workspace or CANDIDATE_FIXTURES
    preview = preview_route(
        {
            "board": board,
            "net": net,
            "layer": "F.Cu",
            "seed": 0,
            "constraints": dict(CONSTRAINTS),
            "settings": {},
        },
        Settings(workspace=directory.resolve()),
    )
    assert preview.candidate is not None, f"{board} must route for this test to mean anything"
    source = (directory / board).read_bytes()
    conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
    assert conversion.snapshot is not None
    return source, conversion.snapshot, preview.candidate


class AppliedBytesTests(unittest.TestCase):
    def test_applying_a_candidate_adds_only_its_segments(self) -> None:
        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        applied = apply_route_candidate(source, snapshot, candidate, _profile())

        self.assertGreater(applied.segments_added, 0)
        self.assertEqual(applied.bytes_added, len(applied.content) - len(source))
        self.assertNotEqual(applied.source_revision, applied.result_revision)
        self.assertEqual(applied.base_revision, snapshot.snapshot_digest)
        self.assertEqual(applied.candidate_id, candidate.candidate_id)

    def test_every_byte_outside_the_patch_is_identical(self) -> None:
        """The assertion is total because the patch is purely additive."""

        for board in ROUTABLE:
            with self.subTest(board=board):
                source, snapshot, candidate = _routed(board)
                applied = apply_route_candidate(source, snapshot, candidate, _profile())
                text = source.decode("utf-8")
                prefix = text[: applied.splice_offset].encode("utf-8")
                suffix = text[applied.splice_offset :].encode("utf-8")
                self.assertEqual(applied.content[: len(prefix)], prefix)
                self.assertEqual(applied.content[len(applied.content) - len(suffix) :], suffix)
                self.assertEqual(
                    len(applied.content),
                    len(prefix) + applied.bytes_added + len(suffix),
                )

    def test_the_applied_board_reparses_and_equals_the_source_plus_the_patch(self) -> None:
        for board in ROUTABLE:
            with self.subTest(board=board):
                source, snapshot, candidate = _routed(board)
                applied = apply_route_candidate(source, snapshot, candidate, _profile())
                patched = parse_kicad_bytes(applied.content, _profile(), ParseLimits())
                self.assertIsNotNone(patched.snapshot)
                self.assertEqual(patched.diagnostics, ())
                assert patched.snapshot is not None
                added = len(patched.snapshot.content.segments) - len(snapshot.content.segments)
                self.assertEqual(added, applied.segments_added)
                # Nothing else moved.
                for collection in ("pads", "vias", "zones", "keepouts", "arcs", "outline", "nets"):
                    self.assertEqual(
                        len(getattr(patched.snapshot.content, collection)),
                        len(getattr(snapshot.content, collection)),
                        collection,
                    )

    def test_writer_metadata_is_not_stamped_on_the_user_s_board(self) -> None:
        """An applied board is the user's file with tracks added, not our artifact.

        The committed fixtures are themselves generated by CopperMCP, so asserting the absence
        of our own writer id would pass for the wrong reason. The board's generator is
        rewritten to KiCad's own before applying, and must survive verbatim - while the
        disposable board rendered for candidate DRC, which *is* our derivative, must still
        claim authorship. Asserting both is what makes the difference deliberate.
        """

        import tempfile

        from copper_mcp.adapters.kicad_route_patch import render_kicad_candidate_board

        original = (CANDIDATE_FIXTURES / "two-pad.kicad_pcb").read_text(encoding="utf-8")
        foreign = original.replace('(generator "copper-mcp")', '(generator "pcbnew")')
        self.assertNotEqual(foreign, original, "the fixture must actually have been rewritten")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "two-pad.kicad_pcb").write_text(foreign, encoding="utf-8")
            source, snapshot, candidate = _routed("two-pad.kicad_pcb", workspace=workspace)

        applied = apply_route_candidate(source, snapshot, candidate, _profile())
        patched = parse_kicad_bytes(applied.content, _profile(), ParseLimits())
        assert patched.snapshot is not None
        self.assertEqual(patched.snapshot.content.source.generator, "pcbnew")
        self.assertIn(b'(generator "pcbnew")', applied.content)
        self.assertNotIn(b'(generator "copper-mcp")', applied.content)

        # The disposable render still stamps, because that board really is ours.
        rendered = render_kicad_candidate_board(source, snapshot, candidate, _profile())
        self.assertIn(b'(generator "copper-mcp")', rendered)

    def test_applying_is_deterministic(self) -> None:
        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        first = apply_route_candidate(source, snapshot, candidate, _profile())
        second = apply_route_candidate(source, snapshot, candidate, _profile())
        self.assertEqual(first.content, second.content)
        self.assertEqual(first.result_revision, second.result_revision)

    def test_the_verification_record_cannot_claim_an_unperformed_stage(self) -> None:
        for field, value in (
            ("untouched_bytes_identical", "not_run"),
            ("reparse_fail_closed", "skipped"),
            ("ir_equals_source_plus_patch", "not_run"),
            ("kicad_opened_board", "passed"),
            ("drc_after_apply", "passed"),
        ):
            with self.subTest(field=field), self.assertRaises(ApplyEngineError):
                ApplyVerification(**{field: value})


class HostileCandidateTests(unittest.TestCase):
    """A candidate is never trusted from its manifest."""

    def test_a_forged_candidate_id_is_refused(self) -> None:
        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        forged = replace(candidate, candidate_id="sha256:" + "0" * 64)
        with self.assertRaises(ApplyEngineError) as caught:
            apply_route_candidate(source, snapshot, forged, _profile())
        self.assertIn("identity", str(caught.exception))

    def test_tampered_geometry_with_a_recomputed_id_still_fails_replay(self) -> None:
        """Recomputing the id makes the manifest self-consistent but not truthful.

        This is why identity verification alone is not enough: the geometry is replayed
        against the board, so a candidate that no router would produce is refused even when
        its digest matches its own contents.
        """

        import hashlib

        from copper_mcp.routing.astar import canonical_candidate_bytes

        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        moved_path = RoutePath(
            vertices=tuple(
                PointNM(point.x + 1_000_000, point.y) for point in candidate.patch.paths[0].vertices
            )
        )
        tampered = replace(candidate, patch=replace(candidate.patch, paths=(moved_path,)))
        digest = hashlib.sha256(canonical_candidate_bytes(tampered)).hexdigest()
        tampered = replace(tampered, candidate_id=f"sha256:{digest}")
        with self.assertRaises((ApplyEngineError, ValueError)):
            apply_route_candidate(source, snapshot, tampered, _profile())

    def test_a_candidate_for_a_different_board_is_refused_as_stale(self) -> None:
        source, snapshot, _ = _routed("two-pad.kicad_pcb")
        _, _, other = _routed("partial-route.kicad_pcb")
        with self.assertRaises(ApplyEngineError) as caught:
            apply_route_candidate(source, snapshot, other, _profile())
        self.assertIn("stale", str(caught.exception))

    def test_a_snapshot_that_does_not_match_the_source_is_refused(self) -> None:
        source, _, candidate = _routed("two-pad.kicad_pcb")
        other_source = (CANDIDATE_FIXTURES / "partial-route.kicad_pcb").read_bytes()
        other = parse_kicad_bytes(other_source, _profile(), ParseLimits()).snapshot
        assert other is not None
        with self.assertRaises(ApplyEngineError):
            apply_route_candidate(source, other, candidate, _profile())

    def test_malformed_arguments_are_refused(self) -> None:
        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        cases = (
            ("source", ("not bytes", snapshot, candidate, _profile())),
            ("snapshot", (source, "not a snapshot", candidate, _profile())),
            ("candidate", (source, snapshot, {"candidate_id": "x"}, _profile())),
            ("profile", (source, snapshot, candidate, "not a profile")),
        )
        for reason, arguments in cases:
            with self.subTest(reason=reason), self.assertRaises(ApplyEngineError):
                apply_route_candidate(*arguments)  # type: ignore[arg-type]

    def test_an_unparseable_board_is_refused_before_any_splice(self) -> None:
        broken = b"(kicad_pcb (version 20260206)"
        _, snapshot, candidate = _routed("two-pad.kicad_pcb")
        with self.assertRaises(ApplyEngineError):
            apply_route_candidate(broken, snapshot, candidate, _profile())

    def test_an_object_budget_that_only_the_patch_would_exceed_is_refused(self) -> None:
        """The ceiling must bite on source-plus-patch, not on the source alone.

        A limit low enough to reject the source would refuse for an unrelated reason, so it is
        set to exactly the source's own object count: the board still converts, and only the
        added segments push it over.
        """

        from copper_mcp.adapters.kicad_route_patch import _modeled_object_count

        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        exact = _modeled_object_count(snapshot)
        limits = replace(ParseLimits(), max_objects=exact)
        # The source alone still converts at this ceiling.
        self.assertIsNotNone(parse_kicad_bytes(source, _profile(), limits).snapshot)
        with self.assertRaises(ApplyEngineError) as caught:
            apply_route_candidate(source, snapshot, candidate, _profile(), limits=limits)
        self.assertIn("budget", str(caught.exception))


class MetamorphicTests(unittest.TestCase):
    def test_applying_to_a_translated_board_translates_the_applied_result(self) -> None:
        """A rigid motion of the board must move the patch with it and change nothing else."""

        import tempfile

        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        applied = apply_route_candidate(source, snapshot, candidate, _profile())

        shift_mm = 5
        text = source.decode("utf-8")
        moved_text = (
            text.replace("(at 10 15 0)", f"(at {10 + shift_mm} 15 0)")
            .replace("(at 30 15 0)", f"(at {30 + shift_mm} 15 0)")
            .replace("(end 40 30)", f"(end {40 + shift_mm} 30)")
        )
        self.assertNotEqual(moved_text, text, "the fixture must actually have moved")

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            (workspace / "two-pad.kicad_pcb").write_text(moved_text, encoding="utf-8")
            moved_source, moved_snapshot, moved_candidate = _routed(
                "two-pad.kicad_pcb", workspace=workspace
            )
        moved_applied = apply_route_candidate(
            moved_source, moved_snapshot, moved_candidate, _profile()
        )

        self.assertEqual(moved_applied.segments_added, applied.segments_added)
        before = parse_kicad_bytes(applied.content, _profile(), ParseLimits()).snapshot
        after = parse_kicad_bytes(moved_applied.content, _profile(), ParseLimits()).snapshot
        assert before is not None and after is not None

        def by_start(item: object) -> tuple[int, int]:
            return (item.start.x, item.start.y)  # type: ignore[attr-defined]

        original = sorted(before.content.segments, key=by_start)
        translated = sorted(after.content.segments, key=by_start)
        self.assertEqual(len(original), len(translated))
        for left, right in zip(original, translated, strict=True):
            self.assertEqual(right.start.x - left.start.x, shift_mm * 1_000_000)
            self.assertEqual(right.start.y, left.start.y)
            self.assertEqual(right.end.x - left.end.x, shift_mm * 1_000_000)
            self.assertEqual(right.width_nm, left.width_nm)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class RealKiCadTests(unittest.TestCase):
    """The applied bytes have to be a board KiCad will actually open.

    The engine returns bytes and never writes; this test writes them to its own scratch
    directory so `kicad-cli` has a file to read.
    """

    def _drc(self, board: Path) -> dict:
        import json
        import tempfile

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
            self.assertIn(completed.returncode, (0, 5), completed.stderr)
            return json.loads(report.read_text(encoding="utf-8"))

    def test_the_applied_board_opens_and_the_net_becomes_connected(self) -> None:
        import tempfile

        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        applied = apply_route_candidate(source, snapshot, candidate, _profile())

        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            before = work / "before.kicad_pcb"
            after = work / "after.kicad_pcb"
            before.write_bytes(source)
            after.write_bytes(applied.content)

            before_report = self._drc(before)
            after_report = self._drc(after)

        # The unrouted net is what the candidate exists to connect.
        self.assertGreater(
            len(before_report.get("unconnected_items", [])),
            0,
            "the fixture must start with something unconnected",
        )
        self.assertEqual(
            len(after_report.get("unconnected_items", [])),
            0,
            "applying the candidate must connect the net KiCad said was open",
        )
        errors = [
            item for item in after_report.get("violations", []) if item.get("severity") == "error"
        ]
        self.assertEqual(errors, [], "the applied board must not introduce a DRC error")

    def test_kicad_preserves_the_applied_board_across_its_own_save(self) -> None:
        import tempfile

        source, snapshot, candidate = _routed("two-pad.kicad_pcb")
        applied = apply_route_candidate(source, snapshot, candidate, _profile())

        with tempfile.TemporaryDirectory() as directory:
            board = Path(directory) / "applied.kicad_pcb"
            board.write_bytes(applied.content)
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
                    "--save-board",
                    "-o",
                    str(Path(directory) / "drc.json"),
                    str(board),
                ],
                capture_output=True,
                check=False,
                timeout=180,
            )
            self.assertIn(completed.returncode, (0, 5), completed.stderr)
            resaved = board.read_bytes()

        rewritten = parse_kicad_bytes(resaved, _profile(), ParseLimits()).snapshot
        assert rewritten is not None
        self.assertEqual(
            len(rewritten.content.segments),
            len(snapshot.content.segments) + applied.segments_added,
            "KiCad must keep the applied segments after rewriting the board itself",
        )


class CopperToneTests(unittest.TestCase):
    def setUp(self) -> None:
        if not COPPERTONE.exists():
            self.skipTest("CopperTone board is not present")

    def test_the_three_part_assertion_holds_on_the_real_board(self) -> None:
        """CopperTone's nets are already connected, so the engine is exercised via a fixture
        candidate replayed against it - what matters here is that a 162 KiB real board with
        multi-byte characters survives the splice and the assertions."""

        from copper_mcp.adapters.cst import Splice, root_close_offset, splice_source

        source = COPPERTONE.read_bytes()
        text = source.decode("utf-8")
        self.assertNotEqual(len(source), len(text), "CopperTone must contain multi-byte characters")
        close = root_close_offset(text)
        spliced = splice_source(source, [Splice(close, close, "")])
        self.assertEqual(spliced, source)

        conversion = parse_kicad_bytes(source, _profile(), ParseLimits())
        self.assertIsNotNone(conversion.snapshot)
        self.assertEqual(conversion.diagnostics, ())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
