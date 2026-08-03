"""Deterministic board render: canonicalization, evidence binding, and text exclusion.

The layer-exclusion test is the one that matters most, and it is worth saying why it is
written the way it is. Measured against KiCad 10.0.5, an SVG export that includes F.SilkS or
F.Fab embeds each board string *twice in literal form* - once in a ``<desc>`` beside the
stroked paths and once in an invisible ``<text opacity="0">``. The strings are therefore
greppable, so a raw byte assertion is a real control rather than a gesture, and the companion
guard proves it by showing that including those layers does leak them.
"""

from __future__ import annotations

import shutil
import time
import unittest
import unittest.mock
from dataclasses import replace
from pathlib import Path

import pytest

from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError, run_scene_render
from copper_mcp.scene_render import (
    MAX_SCENE_RENDERS,
    RENDER_LAYERS,
    SVG_CANONICALIZATION,
    SceneRenderError,
    SceneRenderEvidence,
    SceneRenderStore,
    SceneRenderUnavailableError,
    canonicalize_svg,
    render_digest,
)

ROOT = Path(__file__).resolve().parents[1]
SCENE_FIXTURES = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
DIGEST = "sha256:" + "0" * 64

_EXPORT_HEAD = b'<?xml version="1.0" standalone="no"?>\n<svg>\n'
_EXPORT_TAIL = b'\n<path d="M 0 0"/>\n</svg>\n'


def _fake_export(name: str = "scene.svg", when: str = "2026-08-04T01:02:03") -> bytes:
    """One synthetic export in KiCad 10.0.5's exact shape, for the pure tests."""

    return (
        _EXPORT_HEAD
        + f"<title>SVG Image created as {name} date {when} </title>".encode()
        + (_EXPORT_TAIL)
    )


def _evidence(**overrides: object) -> SceneRenderEvidence:
    base: dict[str, object] = {
        "normalized_digest": DIGEST,
        "source_revision": DIGEST,
        "context_revision": DIGEST,
        "kicad_version": "10.0.5",
        "layers": RENDER_LAYERS,
        "side": "top",
        "canonicalization": SVG_CANONICALIZATION,
        "byte_count": 10,
    }
    base.update(overrides)
    return SceneRenderEvidence(**base)  # type: ignore[arg-type]


class CanonicalizationTests(unittest.TestCase):
    def test_two_exports_differing_only_in_time_and_name_canonicalize_equal(self) -> None:
        first = _fake_export("a.svg", "2026-08-04T01:02:03")
        second = _fake_export("b.svg", "2026-08-04T09:59:59")
        self.assertNotEqual(first, second)
        self.assertEqual(canonicalize_svg(first), canonicalize_svg(second))

    def test_canonicalization_is_idempotent(self) -> None:
        once = canonicalize_svg(_fake_export())
        self.assertEqual(canonicalize_svg(once), once)
        self.assertEqual(canonicalize_svg(canonicalize_svg(once)), once)

    def test_canonicalization_changes_only_the_title_line(self) -> None:
        source = _fake_export()
        canonical = canonicalize_svg(source)
        before, after = source.split(b"\n"), canonical.split(b"\n")
        self.assertEqual(len(before), len(after), "line count must not change")
        pairs = enumerate(zip(before, after, strict=True))
        differing = [index for index, (a, b) in pairs if a != b]
        self.assertEqual(len(differing), 1)
        self.assertIn(b"<title>", before[differing[0]])

    def test_canonicalization_fails_closed_on_an_unexpected_shape(self) -> None:
        for payload, reason in (
            (b"<svg></svg>", "no title at all"),
            (_fake_export() + _fake_export(), "two titles"),
            (_EXPORT_HEAD + b"<title>something else</title>" + _EXPORT_TAIL, "foreign title"),
            (_fake_export()[: len(_fake_export()) - 12], "truncated document"),
            (b"<svg>" + _fake_export()[5:], "missing xml declaration"),
        ):
            with self.subTest(reason=reason), self.assertRaises(SceneRenderError):
                canonicalize_svg(payload)

    def test_a_non_bytes_render_is_refused(self) -> None:
        with self.assertRaises(SceneRenderError):
            canonicalize_svg("not bytes")  # type: ignore[arg-type]

    def test_the_digest_is_taken_over_canonical_bytes(self) -> None:
        canonical = canonicalize_svg(_fake_export())
        self.assertEqual(render_digest(canonical), render_digest(canonicalize_svg(canonical)))
        self.assertNotEqual(render_digest(canonical), render_digest(_fake_export()))


class EvidenceTests(unittest.TestCase):
    def test_evidence_requires_digests_and_a_known_canonicalization(self) -> None:
        for overrides, reason in (
            ({"normalized_digest": "nope"}, "digest shape"),
            ({"source_revision": "sha256:zz"}, "source digest shape"),
            ({"canonicalization": "other-v1"}, "unknown canonicalization"),
            ({"layers": ()}, "no layers"),
            ({"side": "sideways"}, "bad side"),
            ({"byte_count": 0}, "empty render"),
            ({"kicad_version": ""}, "missing kicad version"),
        ):
            with self.subTest(reason=reason), self.assertRaises(SceneRenderError):
                _evidence(**overrides)

    def test_evidence_serializes_every_field_a_comparison_needs(self) -> None:
        document = _evidence().to_dict()
        self.assertEqual(
            set(document),
            {
                "normalized_digest",
                "source_revision",
                "context_revision",
                "kicad_version",
                "layers",
                "side",
                "canonicalization",
                "byte_count",
            },
        )


class RenderStoreTests(unittest.TestCase):
    def _put(self, store: SceneRenderStore, payload: bytes) -> str:
        canonical = canonicalize_svg(payload)
        return store.put(
            canonical,
            _evidence(normalized_digest=render_digest(canonical), byte_count=len(canonical)),
        )

    def test_stored_bytes_are_returned_exactly(self) -> None:
        store = SceneRenderStore()
        canonical = canonicalize_svg(_fake_export())
        uri = self._put(store, _fake_export())
        self.assertEqual(store.read(uri.split("/")[-2]), canonical)

    def test_bytes_that_do_not_match_their_evidence_are_refused(self) -> None:
        """A capability URI must never name bytes its evidence does not describe."""

        store = SceneRenderStore()
        canonical = canonicalize_svg(_fake_export())
        with self.assertRaises(SceneRenderError):
            store.put(canonical, _evidence(normalized_digest=DIGEST))

    def test_an_unknown_or_malformed_capability_is_uniformly_unavailable(self) -> None:
        store = SceneRenderStore()
        for token, reason in (("short", "malformed"), ("A" * 43, "unknown")):
            with self.subTest(reason=reason), self.assertRaises(SceneRenderUnavailableError):
                store.read(token)

    def test_capacity_evicts_least_recently_used(self) -> None:
        store = SceneRenderStore()
        tokens = [
            self._put(store, _fake_export(f"r{index}.svg")).split("/")[-2]
            for index in range(MAX_SCENE_RENDERS + 1)
        ]
        with self.assertRaises(SceneRenderUnavailableError):
            store.read(tokens[0])
        self.assertTrue(store.read(tokens[-1]))

    def test_expiry_is_absolute_and_a_read_does_not_renew_it(self) -> None:
        now = [1000.0]
        store = SceneRenderStore(ttl_seconds=60, clock=lambda: now[0])
        token = self._put(store, _fake_export()).split("/")[-2]
        now[0] += 59
        self.assertTrue(store.read(token))
        now[0] += 1
        with self.assertRaises(SceneRenderUnavailableError):
            store.read(token)

    def test_store_limits_are_tighten_only(self) -> None:
        with self.assertRaises(ValueError):
            SceneRenderStore(max_artifacts=MAX_SCENE_RENDERS + 1)
        with self.assertRaises(ValueError):
            SceneRenderStore(ttl_seconds=10**9)

    def test_an_oversized_render_is_refused_rather_than_truncated(self) -> None:
        store = SceneRenderStore(max_total_bytes=2048)
        payload = (
            _EXPORT_HEAD
            + b"<title>SVG Image created as scene.svg date 2026-08-04T01:02:03 </title>"
            + b'\n<path d="'
            + b"M 0 0 " * 2000
            + b'"/>\n</svg>\n'
        )
        canonical = canonicalize_svg(payload)
        with self.assertRaises(SceneRenderError):
            store.put(
                canonical,
                _evidence(normalized_digest=render_digest(canonical), byte_count=len(canonical)),
            )


def _workspace_entries(root: Path) -> set[str]:
    return {path.relative_to(root).as_posix() for path in root.rglob("*")}


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class RealKiCadRenderTests(unittest.TestCase):
    """These run the actual exporter. Nothing else can establish determinism."""

    def setUp(self) -> None:
        self.workspace = Path(
            __import__("tempfile").mkdtemp(prefix="copper-mcp-render-test-")
        ).resolve()
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.board = self.workspace / "scene-region.kicad_pcb"
        shutil.copy2(SCENE_FIXTURES / "scene-region.kicad_pcb", self.board)
        self.settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)

    def test_two_real_exports_seconds_apart_have_equal_normalized_digests(self) -> None:
        """The regression that proves canonicalization does its job on real output.

        The delay is not incidental: KiCad stamps the export with a wall-clock time to the
        second, so a test that rendered twice in the same second would pass without the
        canonicalization doing anything at all.
        """

        first_evidence, first_bytes = run_scene_render(self.board.name, self.settings)
        time.sleep(2.1)
        second_evidence, second_bytes = run_scene_render(self.board.name, self.settings)

        self.assertEqual(first_evidence.normalized_digest, second_evidence.normalized_digest)
        self.assertEqual(first_bytes, second_bytes)
        self.assertEqual(first_evidence.to_dict(), second_evidence.to_dict())

    def test_evidence_binds_the_digest_to_the_bytes_and_the_board(self) -> None:
        evidence, canonical = run_scene_render(self.board.name, self.settings)
        self.assertEqual(evidence.normalized_digest, render_digest(canonical))
        self.assertEqual(evidence.byte_count, len(canonical))
        self.assertEqual(evidence.canonicalization, SVG_CANONICALIZATION)
        self.assertEqual(evidence.layers, RENDER_LAYERS)
        self.assertEqual(evidence.side, "top")
        self.assertTrue(evidence.kicad_version.startswith("10."))
        self.assertRegex(evidence.source_revision, r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(evidence.context_revision, r"^sha256:[0-9a-f]{64}$")

    def test_editing_the_board_changes_the_render_digest(self) -> None:
        """Guard the guard: a digest that never moves would pass every test above."""

        before, _ = run_scene_render(self.board.name, self.settings)
        source = self.board.read_text(encoding="utf-8")
        self.board.write_text(source.replace("(width 0.25)", "(width 0.4)"), encoding="utf-8")
        after, _ = run_scene_render(self.board.name, self.settings)
        self.assertNotEqual(before.normalized_digest, after.normalized_digest)
        self.assertNotEqual(before.source_revision, after.source_revision)

    def test_the_render_never_writes_anything_into_the_workspace(self) -> None:
        """KiCad drops a .kicad_prl beside a writable input; the read-only snapshot stops it."""

        before_entries = _workspace_entries(self.workspace)
        before_stat = self.board.stat()
        source = self.board.read_bytes()

        run_scene_render(self.board.name, self.settings)

        self.assertEqual(_workspace_entries(self.workspace), before_entries)
        self.assertEqual(self.board.read_bytes(), source)
        self.assertEqual(self.board.stat().st_ino, before_stat.st_ino)
        self.assertEqual(self.board.stat().st_mtime_ns, before_stat.st_mtime_ns)

    def test_a_size_ceiling_refuses_rather_than_returning_a_partial_render(self) -> None:
        """A truncated render must be a refusal, never a digest over partial bytes.

        Measured on KiCad 10.0.5: reaching the RLIMIT_FSIZE ceiling does not kill the
        process. It exits 0 having written a truncated file, and the volatile title line is
        near the top of the document so it survives - which means the exit code, the title
        check and the digest would all have been satisfied by half an SVG.
        """

        with self.assertRaises((SceneRenderError, KiCadCliError)) as caught:
            run_scene_render(self.board.name, replace(self.settings, max_render_bytes=1024))
        self.assertIn("complete", str(caught.exception))

    def test_a_complete_render_under_the_ceiling_is_still_returned(self) -> None:
        """Guard the guard: the ceiling test must not pass by refusing everything."""

        evidence, canonical = run_scene_render(
            self.board.name, replace(self.settings, max_render_bytes=64 * 1024)
        )
        self.assertTrue(canonical.rstrip().endswith(b"</svg>"))
        self.assertEqual(evidence.byte_count, len(canonical))

    def test_a_context_change_during_the_render_is_refused(self) -> None:
        """Evidence must describe a board that still exists.

        The render is bound to the project context around the board, not just the board, so
        a rule or layer edited while KiCad was running would make the recorded
        context_revision describe inputs the render was not taken under.
        """

        import copper_mcp.kicad_cli as module

        real_context = module._drc_context
        calls = {"n": 0}

        def shifting_context(*args: object, **kwargs: object) -> dict[str, bytes]:
            calls["n"] += 1
            captured = real_context(*args, **kwargs)  # type: ignore[arg-type]
            if calls["n"] > 1:
                # Simulate a rule file edited while KiCad was running.
                return {**captured, "copper.kicad_dru": b"(version 1)"}
            return captured

        with unittest.mock.patch.object(module, "_drc_context", shifting_context):
            with self.assertRaises(KiCadCliError) as caught:
                run_scene_render(self.board.name, self.settings)
        self.assertIn("changed", str(caught.exception))
        self.assertGreaterEqual(calls["n"], 2, "the recapture must actually have happened")

    def test_the_bottom_side_renders_mirrored_and_is_a_distinct_artifact(self) -> None:
        """Reachable from the Python API only today, so it is tested rather than assumed."""

        top_evidence, top_bytes = run_scene_render(self.board.name, self.settings, side="top")
        bottom_evidence, bottom_bytes = run_scene_render(
            self.board.name, self.settings, side="bottom"
        )
        self.assertEqual(bottom_evidence.side, "bottom")
        self.assertTrue(bottom_bytes.rstrip().endswith(b"</svg>"))
        # Same copper, viewed from the other side: the bytes must not be interchangeable, or
        # the evidence's `side` field would be recording a distinction that does not exist.
        self.assertNotEqual(top_evidence.normalized_digest, bottom_evidence.normalized_digest)
        self.assertNotEqual(top_bytes, bottom_bytes)
        self.assertEqual(bottom_evidence.source_revision, top_evidence.source_revision)

    def test_a_bad_side_is_refused_before_kicad_runs(self) -> None:
        with self.assertRaises(SceneRenderError):
            run_scene_render(self.board.name, self.settings, side="sideways")


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class RealKiCadTextExclusionTests(unittest.TestCase):
    """The hostile fixture's strings must not be in the bytes handed to a model."""

    def setUp(self) -> None:
        self.workspace = Path(
            __import__("tempfile").mkdtemp(prefix="copper-mcp-render-text-")
        ).resolve()
        self.addCleanup(shutil.rmtree, self.workspace, True)
        self.board = self.workspace / "scene-hostile-text.kicad_pcb"
        shutil.copy2(SCENE_FIXTURES / "scene-hostile-text.kicad_pcb", self.board)
        self.settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)

    def test_the_rendered_layer_set_excludes_every_text_bearing_layer(self) -> None:
        evidence, _ = run_scene_render(self.board.name, self.settings)
        self.assertEqual(evidence.layers, ("F.Cu", "B.Cu", "Edge.Cuts"))
        for layer in evidence.layers:
            self.assertNotIn("SilkS", layer)
            self.assertNotIn("Fab", layer)
            self.assertNotIn("Cmts", layer)

    def test_no_board_author_string_appears_in_the_render_bytes(self) -> None:
        _, canonical = run_scene_render(self.board.name, self.settings)
        for marker in (
            b"CANARY_SILK_GR",
            b"CANARY_FAB_GR",
            b"CANARY_FP_TEXT",
            b"CANARY_REFERENCE_U1",
            b"CANARY_PROPERTY_NAME",
            b"CANARY_PROPERTY_VALUE",
            b"CANARY_NET_NAME",
        ):
            with self.subTest(marker=marker.decode()):
                self.assertNotIn(marker, canonical)
        self.assertNotIn(b"CANARY", canonical)

    def test_including_the_text_layers_would_leak_them(self) -> None:
        """Guard the guard.

        If a copper-only export happened to contain no text for an unrelated reason, the
        assertion above would pass while proving nothing. This renders the same board *with*
        the silkscreen and fabrication layers and requires the markers to appear, which is
        what establishes that excluding those layers is the control doing the work.
        """

        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "leak.svg"
            completed = subprocess.run(  # noqa: S603
                [
                    str(REAL_KICAD_CLI),
                    "pcb",
                    "export",
                    "svg",
                    "--mode-single",
                    "--exclude-drawing-sheet",
                    "--black-and-white",
                    "--page-size-mode",
                    "2",
                    "--layers",
                    "F.Cu,B.Cu,Edge.Cuts,F.SilkS,F.Fab",
                    "--output",
                    str(output),
                    str(self.board),
                ],
                capture_output=True,
                check=False,
                timeout=120,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            leaked = output.read_bytes()

        self.assertIn(b"CANARY_SILK_GR", leaked)
        self.assertIn(b"CANARY_REFERENCE_U1", leaked)
        # And specifically: the strings are literal, not merely drawn as paths. Both copies
        # are present, which is why filtering <text> after the fact would not be enough.
        self.assertIn(b"<desc>CANARY_SILK_GR", leaked)
        self.assertIn(b"<text", leaked)


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
class SceneIntegrationTests(unittest.TestCase):
    """include_render as a caller actually reaches it, through the scene service."""

    def setUp(self) -> None:
        self.workspace = Path(
            __import__("tempfile").mkdtemp(prefix="copper-mcp-scene-render-")
        ).resolve()
        self.addCleanup(shutil.rmtree, self.workspace, True)
        shutil.copy2(SCENE_FIXTURES / "scene-region.kicad_pcb", self.workspace)
        self.settings = Settings(workspace=self.workspace, kicad_cli=REAL_KICAD_CLI)

    def _request(self, **overrides: object) -> dict[str, object]:
        payload: dict[str, object] = {
            "board": "scene-region.kicad_pcb",
            "constraints": {
                "clearance_nm": 200_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            },
            "region": {
                "min_x_nm": 0,
                "min_y_nm": 0,
                "max_x_nm": 30_000_000,
                "max_y_nm": 30_000_000,
            },
        }
        payload.update(overrides)
        return payload

    def test_a_scene_carries_no_render_unless_it_is_asked_for(self) -> None:
        from copper_mcp.circuit_scene import observe_board_scene

        scene = observe_board_scene(self._request(), self.settings)
        self.assertIsNone(scene.render)
        self.assertIsNone(scene.render_bytes)
        self.assertIsNone(scene.to_dict()["render"])

    def test_a_requested_render_is_evidence_in_the_document_and_bytes_beside_it(self) -> None:
        from copper_mcp.circuit_scene import observe_board_scene
        from copper_mcp.mcp_contracts import CircuitSceneToolResponse

        scene = observe_board_scene(self._request(include_render=True), self.settings)
        document = scene.to_dict()
        CircuitSceneToolResponse.model_validate(document)

        self.assertIsNotNone(scene.render_bytes)
        assert scene.render_bytes is not None
        assert scene.render is not None
        self.assertEqual(document["render"], scene.render.to_dict())
        # The bytes are never inlined into the JSON response.
        self.assertNotIn("board.svg", str(document))
        self.assertNotIn(b"<svg", str(document).encode())
        self.assertEqual(scene.render.source_revision, document["board_revision"])

    def test_the_render_is_whole_board_even_when_the_scene_is_a_small_window(self) -> None:
        """Stated rather than implied: region scoping applies to semantics, not the picture."""

        from copper_mcp.circuit_scene import observe_board_scene

        narrow = observe_board_scene(
            self._request(
                include_render=True,
                region={
                    "min_x_nm": 0,
                    "min_y_nm": 0,
                    "max_x_nm": 15_000_000,
                    "max_y_nm": 15_000_000,
                },
            ),
            self.settings,
        )
        wide = observe_board_scene(
            self._request(
                include_render=True,
                region={
                    "min_x_nm": 0,
                    "min_y_nm": 0,
                    "max_x_nm": 100_000_000,
                    "max_y_nm": 30_000_000,
                },
            ),
            self.settings,
        )
        assert narrow.render is not None and wide.render is not None
        self.assertLess(
            narrow.to_dict()["truncation"]["objects_returned"],
            wide.to_dict()["truncation"]["objects_returned"],
        )
        self.assertEqual(narrow.render.normalized_digest, wide.render.normalized_digest)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
