"""The public placement surface: the service, the MCP tool, and the CLI.

The legalizer's own behaviour is covered in ``test_placement``. What matters here is that the
surface does not weaken it: the same verdicts must survive the request boundary, the response
must satisfy its advertised schema, and a hostile request must be refused before any file is
read.
"""

from __future__ import annotations

import asyncio
import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

import copper_mcp.mcp_server as _server
from copper_mcp.cli import main
from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import PlacementPreviewToolResponse
from copper_mcp.placement.contracts import PlacementError
from copper_mcp.placement_preview import preview_live_placement, preview_placement

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "placement-v0.1"
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
SUBJECTS = [
    "footprint:kicad:90000000-0000-0000-0000-000000000001",
    "footprint:kicad:90000000-0000-0000-0000-000000000003",
]
#: What each committed fixture must produce, end to end.
EXPECTED = {
    "placement-legal.kicad_pcb": ("previewed", None),
    "placement-overlap.kicad_pcb": ("refused", "illegal_placement"),
    "placement-keepout.kicad_pcb": ("refused", "illegal_placement"),
    "placement-outside-outline.kicad_pcb": ("refused", "illegal_placement"),
}


def _settings(**overrides: Any) -> Settings:
    return replace(Settings(workspace=FIXTURES.resolve()), **overrides)


def _request(board: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "board": board,
        "constraints": dict(CONSTRAINTS),
        "subjects": list(SUBJECTS),
    }
    payload.update(overrides)
    return payload


def _preview(board: str, **overrides: Any) -> dict[str, Any]:
    document = preview_placement(_request(board, **overrides), _settings()).to_dict()
    # Every response this suite produces must satisfy the advertised machine contract, so a
    # widened response cannot pass by only being checked field by field.
    PlacementPreviewToolResponse.model_validate(document)
    return document


class ServiceTests(unittest.TestCase):
    def test_every_fixture_reaches_its_expected_outcome(self) -> None:
        for board, (status, code) in EXPECTED.items():
            with self.subTest(board=board):
                document = _preview(board)
                self.assertEqual(document["status"], status)
                if code is None:
                    self.assertIsNotNone(document["candidate"])
                    self.assertIsNone(document["diagnostic"])
                else:
                    self.assertIsNone(document["candidate"])
                    assert document["diagnostic"] is not None
                    self.assertEqual(document["diagnostic"]["code"], code)

    def test_a_response_echoes_the_validated_request_and_binds_to_the_board(self) -> None:
        document = _preview("placement-legal.kicad_pcb")
        self.assertEqual(document["board_path"], "placement-legal.kicad_pcb")
        self.assertRegex(document["board_revision"], r"^sha256:[0-9a-f]{64}$")
        self.assertRegex(document["snapshot_digest"], r"^sha256:[0-9a-f]{64}$")
        assert document["request"] is not None
        self.assertEqual(document["request"]["subjects"], SUBJECTS)
        self.assertEqual(document["request"]["constraints"], CONSTRAINTS)
        candidate = document["candidate"]
        assert candidate is not None
        self.assertEqual(candidate["base_revision"], document["snapshot_digest"])
        self.assertEqual(candidate["view_revision"], document["board_revision"])

    def test_a_refusal_still_carries_the_legality_that_condemned_it(self) -> None:
        document = _preview("placement-keepout.kicad_pcb")
        diagnostic = document["diagnostic"]
        assert diagnostic is not None
        self.assertIsNotNone(diagnostic["legality"])
        self.assertEqual(diagnostic["legality"]["keepout_respect"], "violated")

    def test_boards_without_courtyards_are_proven_clear_for_that_check(self) -> None:
        document = _preview("placement-legal.kicad_pcb")
        candidate = document["candidate"]
        assert candidate is not None
        self.assertEqual(candidate["evidence"]["legality"]["courtyard_overlap"], "proven_clear")

    def test_a_board_outside_the_workspace_is_refused(self) -> None:
        for path in ("../board-ir-v0.1/subset.kicad_pcb", "/etc/hosts", "nope.kicad_pcb"):
            with self.subTest(path=path), self.assertRaises(Exception) as caught:
                preview_placement(_request(path), _settings())
            self.assertNotIsInstance(caught.exception, AssertionError)

    def test_an_unsupported_board_reports_counts_and_no_candidate(self) -> None:
        board = ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "malformed-unbalanced.kicad_pcb"
        document = preview_placement(
            _request(board.name), replace(_settings(), workspace=board.parent.resolve())
        ).to_dict()
        PlacementPreviewToolResponse.model_validate(document)
        self.assertEqual(document["status"], "unsupported_board")
        self.assertIsNone(document["candidate"])
        self.assertTrue(document["conversion_diagnostic_counts"])

    def test_budgets_come_from_settings(self) -> None:
        document = preview_placement(
            _request("placement-legal.kicad_pcb"), _settings(max_placement_checks=1)
        ).to_dict()
        PlacementPreviewToolResponse.model_validate(document)
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "budget_exhausted")

    def test_file_backed_revision_preconditions_are_honored_before_work(self) -> None:
        baseline = _preview("placement-legal.kicad_pcb")
        board_revision = baseline["board_revision"]
        snapshot_digest = baseline["snapshot_digest"]
        assert snapshot_digest is not None

        bound = _preview(
            "placement-legal.kicad_pcb",
            expect_board_revision=board_revision,
            expect_snapshot_digest=snapshot_digest,
        )
        self.assertEqual(bound["status"], "previewed")
        self.assertEqual(bound["board_revision"], board_revision)
        self.assertEqual(bound["snapshot_digest"], snapshot_digest)

        stale_board = _preview(
            "placement-legal.kicad_pcb",
            expect_board_revision="sha256:" + "0" * 64,
            expect_snapshot_digest=snapshot_digest,
        )
        self.assertEqual(stale_board["status"], "refused")
        self.assertEqual(stale_board["diagnostic"]["code"], "stale_revision")
        self.assertIsNone(stale_board["snapshot_digest"])

        stale_snapshot = _preview(
            "placement-legal.kicad_pcb",
            expect_board_revision=board_revision,
            expect_snapshot_digest="sha256:" + "1" * 64,
        )
        self.assertEqual(stale_snapshot["status"], "refused")
        self.assertEqual(stale_snapshot["diagnostic"]["code"], "stale_revision")
        self.assertEqual(stale_snapshot["snapshot_digest"], snapshot_digest)
        self.assertIsNone(stale_snapshot["candidate"])

    def test_stale_file_backed_board_revision_skips_conversion(self) -> None:
        """The board CAS must stop before parsing untrusted/stale file-backed work."""

        with patch(
            "copper_mcp.placement_preview.parse_kicad_bytes",
            side_effect=AssertionError("stale board reached Board IR conversion"),
        ) as parse:
            result = _preview(
                "placement-legal.kicad_pcb",
                expect_board_revision="sha256:" + "0" * 64,
                expect_snapshot_digest="sha256:" + "1" * 64,
            )

        self.assertFalse(parse.called)
        self.assertEqual(result["status"], "refused")
        assert result["diagnostic"] is not None
        self.assertEqual(result["diagnostic"]["code"], "stale_revision")
        self.assertIsNone(result["snapshot_digest"])

    def test_stale_snapshot_is_rejected_before_building_placement_view(self) -> None:
        baseline = _preview("placement-legal.kicad_pcb")
        board_revision = baseline["board_revision"]

        # A stale snapshot must be a cheap CAS refusal. In particular, the placement view can
        # be expensive for large boards and must not be constructed before the digest check.
        with (
            patch(
                "copper_mcp.placement_preview.build_placement_view",
                return_value=object(),
            ) as build_view,
            patch(
                "copper_mcp.placement_preview.evaluate_placement",
                side_effect=AssertionError("stale snapshot reached legalizer"),
            ) as legalizer,
        ):
            result = _preview(
                "placement-legal.kicad_pcb",
                expect_board_revision=board_revision,
                expect_snapshot_digest="sha256:" + "1" * 64,
            )

        self.assertFalse(build_view.called)
        self.assertFalse(legalizer.called)
        self.assertEqual(result["status"], "refused")
        assert result["diagnostic"] is not None
        self.assertEqual(result["diagnostic"]["code"], "stale_revision")

    def test_a_subject_ceiling_is_enforced_at_the_boundary(self) -> None:
        with self.assertRaises(PlacementError):
            preview_placement(
                _request("placement-legal.kicad_pcb"), _settings(max_placement_subjects=1)
            )


class HostileRequestTests(unittest.TestCase):
    """Malformed requests must be refused before the board is read."""

    def test_hostile_requests_are_refused(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({"constraints": dict(CONSTRAINTS), "subjects": SUBJECTS}, "missing board"),
            (_request("b.kicad_pcb", surprise=1), "unknown field"),
            (_request("b.kicad_pcb", subjects=[]), "no subjects"),
            (_request("b.kicad_pcb", placement_grid_nm=0), "non-positive grid"),
            (_request("b.kicad_pcb", rules=[{"kind": "nope"}]), "unknown rule kind"),
            (
                _request("b.kicad_pcb", proposals=[{"subject": SUBJECTS[0], "x_nm": 1}]),
                "absolute coordinate smuggled into a proposal",
            ),
            (
                _request(
                    "b.kicad_pcb",
                    rules=[
                        {
                            "kind": "orientation",
                            "subject": SUBJECTS[0],
                            "allowed": [45_000_000],
                        }
                    ],
                ),
                "oblique orientation",
            ),
            (_request("b.kicad_pcb", subjects=[SUBJECTS[0], SUBJECTS[0]]), "duplicate subject"),
            (_request("b.kicad_pcb", constraints={}), "empty constraints"),
            ([], "request is not an object"),
        ]
        for payload, reason in cases:
            with self.subTest(reason=reason), self.assertRaises((PlacementError, ValueError)):
                preview_placement(payload, _settings())

    def test_a_refusal_does_not_echo_the_rejected_value(self) -> None:
        secret = "PRIVATE-PROJECT-FOOTPRINT-NAME"
        with self.assertRaises(PlacementError) as caught:
            preview_placement(
                _request("placement-legal.kicad_pcb", proposals=[{"subject": secret}]),
                _settings(),
            )
        self.assertNotIn(secret, str(caught.exception))


class LivePlacementTests(unittest.TestCase):
    """The live bridge must equal the file oracle without touching the editor."""

    class _Version:
        major = 10
        minor = 0
        patch = 5

    class _Board:
        def __init__(self, source: str) -> None:
            self.source = source
            self.reads = 0

        def get_as_string(self) -> str:
            self.reads += 1
            return self.source

        def __getattr__(self, name: str) -> Any:
            if name in {
                "save",
                "save_as",
                "move",
                "set_position",
                "update_items",
                "begin_commit",
                "push_commit",
                "refill_zones",
            }:
                raise AssertionError(f"live placement called forbidden mutator: {name}")
            raise AttributeError(name)

    class _Client:
        def __init__(self, board: Any) -> None:
            self.board = board

        def get_version(self) -> Any:
            return LivePlacementTests._Version()

        def get_api_version(self) -> Any:
            return LivePlacementTests._Version()

        def check_version(self) -> bool:
            return True

        def get_board(self) -> Any:
            return self.board

    def _live(self, board: str = "placement-legal.kicad_pcb") -> tuple[dict[str, Any], Any]:
        source = (FIXTURES / board).read_text(encoding="utf-8")
        proposal = [
            {
                "subject": SUBJECTS[0],
                "offset_x_nm": 1_234_567,
                "offset_y_nm": 0,
            }
        ]
        file_document = _preview(board, proposals=proposal)
        assert file_document["snapshot_digest"] is not None
        request = _request(
            "live",
            expect_board_revision=file_document["board_revision"],
            expect_snapshot_digest=file_document["snapshot_digest"],
            proposals=proposal,
        )
        board_object = self._Board(source)

        def factory(**_: Any) -> Any:
            return self._Client(board_object)

        return request, (factory, file_document, board_object)

    def test_live_candidate_matches_the_file_oracle_and_is_read_only(self) -> None:
        request, (factory, file_document, board_object) = self._live()
        live = preview_live_placement(request, _settings(), client_factory=factory).to_dict()
        self.assertEqual(live["status"], file_document["status"])
        self.assertEqual(live["candidate"], file_document["candidate"])
        self.assertEqual(live["board_revision"], file_document["board_revision"])
        self.assertEqual(live["snapshot_digest"], file_document["snapshot_digest"])
        self.assertEqual(live["board_path"], "live")
        self.assertEqual(board_object.reads, 2)
        self.assertNotIn("kicad_pcb", repr(live))

    def test_stale_board_revision_refuses_after_capture_before_conversion(self) -> None:
        request, (factory, _, board_object) = self._live()
        request["expect_board_revision"] = "sha256:" + "0" * 64
        result = preview_live_placement(request, _settings(), client_factory=factory).to_dict()
        self.assertEqual(result["diagnostic"]["code"], "stale_revision")
        self.assertIsNone(result["snapshot_digest"])
        self.assertEqual(board_object.reads, 2)

    def test_stale_snapshot_digest_refuses_before_placement_view(self) -> None:
        request, (factory, _, _board_object) = self._live()
        request["expect_snapshot_digest"] = "sha256:" + "1" * 64
        result = preview_live_placement(request, _settings(), client_factory=factory).to_dict()
        self.assertEqual(result["diagnostic"]["code"], "stale_revision")
        self.assertIsNotNone(result["snapshot_digest"])
        self.assertIsNone(result["candidate"])

    def test_unknown_action_field_is_rejected_before_ipc(self) -> None:
        request, (factory, _, board_object) = self._live()
        request["include_apply_token"] = True
        with self.assertRaises(PlacementError):
            preview_live_placement(request, _settings(), client_factory=factory)
        self.assertEqual(board_object.reads, 0)

    def test_live_result_survives_the_actual_mcp_boundary(self) -> None:
        request, (factory, _, _) = self._live()
        service_result = preview_live_placement(request, _settings(), client_factory=factory)
        with patch.object(
            _server, "preview_live_placement_service_raw", return_value=service_result
        ):
            with patch.object(_server, "_SETTINGS", _settings()):
                result = asyncio.run(
                    _server.mcp.call_tool("preview_live_placement", {"request": request})
                )
        self.assertFalse(result.is_error)
        assert result.structured_content is not None
        self.assertEqual(result.structured_content["board_path"], "live")
        PlacementPreviewToolResponse.model_validate(result.structured_content)

    def test_an_unresolved_subject_is_a_typed_refusal_not_an_exception(self) -> None:
        document = _preview(
            "placement-legal.kicad_pcb", subjects=["footprint:kicad:not-on-this-board"]
        )
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "unresolved_ref")


class McpSurfaceTests(unittest.TestCase):
    def _call(self, request: dict[str, Any], settings: Settings | None = None) -> Any:
        target = settings or _settings()
        with patch.object(_server, "_SETTINGS", target):
            return asyncio.run(_server.mcp.call_tool("preview_placement", {"request": request}))

    def test_the_tool_advertises_a_real_output_schema(self) -> None:
        tools = asyncio.run(_server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "preview_placement")
        schema = tool.output_schema
        assert isinstance(schema, dict)
        self.assertIs(schema["additionalProperties"], False)
        self.assertEqual(
            set(schema["properties"]),
            {
                "status",
                "placement_version",
                "board_path",
                "board_revision",
                "snapshot_digest",
                "request",
                "candidate",
                "diagnostic",
                "conversion_diagnostic_counts",
                "apply_token",
            },
        )

    def test_the_tool_is_annotated_read_only(self) -> None:
        tools = asyncio.run(_server.mcp.list_tools())
        tool = next(item for item in tools if item.name == "preview_placement")
        assert tool.annotations is not None
        self.assertIs(tool.annotations.read_only_hint, True)
        self.assertIs(tool.annotations.destructive_hint, False)
        self.assertIs(tool.annotations.open_world_hint, False)

    def test_every_fixture_survives_the_mcp_boundary_unchanged(self) -> None:
        for board, (status, code) in EXPECTED.items():
            with self.subTest(board=board):
                result = self._call(_request(board))
                self.assertFalse(result.is_error)
                structured = result.structured_content
                self.assertEqual(structured["status"], status)
                if code is not None:
                    self.assertEqual(structured["diagnostic"]["code"], code)
                # The surface must not alter the service's own answer.
                self.assertEqual(structured, _preview(board))

    def test_the_tool_is_available_over_both_transports(self) -> None:
        """Transport parity: the response holds no capability handle, so nothing is withheld.

        Unlike a render or a schematic artifact, a placement preview is one self-contained
        document with no process-local bytes to resolve, so a stateless HTTP deployment can
        serve it identically.
        """

        stdio = self._call(_request("placement-legal.kicad_pcb"), _settings(transport="stdio"))
        http = self._call(
            _request("placement-legal.kicad_pcb"), _settings(transport="streamable-http")
        )
        self.assertFalse(stdio.is_error)
        self.assertFalse(http.is_error)
        self.assertEqual(stdio.structured_content, http.structured_content)

    def test_the_tool_is_registered_on_a_stateless_http_server(self) -> None:
        import os
        import subprocess
        import sys

        script = (
            "import asyncio, json, os;"
            "os.environ['COPPER_MCP_TRANSPORT']='streamable-http';"
            "import copper_mcp.mcp_server as m;"
            "print(json.dumps(sorted(t.name for t in asyncio.run(m.mcp.list_tools()))))"
        )
        environment = dict(os.environ)
        environment["COPPER_MCP_WORKSPACE"] = str(ROOT)
        environment["COPPER_MCP_TRANSPORT"] = "streamable-http"
        completed = subprocess.run(  # noqa: S603
            [sys.executable, "-c", script],
            cwd=ROOT,
            env=environment,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertIn("preview_placement", json.loads(completed.stdout))


class CliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def _base(self, board: str) -> list[str]:
        arguments = [
            "--workspace",
            str(FIXTURES),
            "preview-placement",
            board,
            "--clearance-nm",
            "200000",
            "--track-width-nm",
            "250000",
            "--via-diameter-nm",
            "600000",
            "--via-drill-nm",
            "300000",
        ]
        for subject in SUBJECTS:
            arguments += ["--subject", subject]
        return arguments

    def test_every_fixture_produces_the_same_answer_through_the_cli(self) -> None:
        for board, (status, code) in EXPECTED.items():
            with self.subTest(board=board):
                exit_code, out, _ = self._run(*self._base(board))
                self.assertEqual(exit_code, 0)
                document = json.loads(out)
                self.assertEqual(document["status"], status)
                if code is not None:
                    self.assertEqual(document["diagnostic"]["code"], code)
                self.assertEqual(document, _preview(board))

    def test_rules_and_proposals_come_from_a_workspace_confined_document(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            import shutil

            shutil.copy2(FIXTURES / "placement-legal.kicad_pcb", workspace)
            (workspace / "intent.json").write_text(
                json.dumps(
                    {
                        "rules": [
                            {"kind": "alignment", "axis": "y", "members": SUBJECTS},
                            {"kind": "side", "subject": SUBJECTS[0], "side": "front"},
                        ]
                    }
                ),
                encoding="utf-8",
            )
            arguments = self._base("placement-legal.kicad_pcb")
            arguments[1] = str(workspace)
            exit_code, out, _ = self._run(*arguments, "--intent", "intent.json")
            self.assertEqual(exit_code, 0)
            document = json.loads(out)
            self.assertEqual(document["request"]["rule_count"], 2)
            results = document["candidate"]["evidence"]["rule_results"]
            self.assertEqual([item["status"] for item in results], ["satisfied_exactly"] * 2)

    def test_an_intent_document_cannot_redirect_the_request(self) -> None:
        """The board, constraints and subjects always come from the flags."""

        import shutil
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            shutil.copy2(FIXTURES / "placement-legal.kicad_pcb", workspace)
            (workspace / "intent.json").write_text(
                json.dumps({"board": "somewhere-else.kicad_pcb", "rules": []}), encoding="utf-8"
            )
            arguments = self._base("placement-legal.kicad_pcb")
            arguments[1] = str(workspace)
            exit_code, _, err = self._run(*arguments, "--intent", "intent.json")
            self.assertEqual(exit_code, 2)
            self.assertIn("unsupported field", err)

    def test_a_malformed_request_exits_non_zero_without_a_traceback(self) -> None:
        arguments = [*self._base("placement-legal.kicad_pcb"), "--placement-grid-nm", "0"]
        exit_code, out, err = self._run(*arguments)
        self.assertEqual(exit_code, 2)
        self.assertEqual(out, "")
        self.assertTrue(err.startswith("error: "))


class MetamorphicSurfaceTests(unittest.TestCase):
    """A rigid motion of the board must not change what the surface concludes."""

    def test_translating_the_board_preserves_the_preview_verdict(self) -> None:
        import shutil
        import tempfile

        source = (FIXTURES / "placement-legal.kicad_pcb").read_text(encoding="utf-8")
        shifted = (
            source.replace("(at 10 15 0)", "(at 17 15 0)")
            .replace("(at 30 15 0)", "(at 37 15 0)")
            .replace("(end 40 30)", "(end 47 30)")
        )
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            shutil.copy2(FIXTURES / "placement-legal.kicad_pcb", workspace)
            (workspace / "moved.kicad_pcb").write_text(shifted, encoding="utf-8")
            settings = replace(_settings(), workspace=workspace.resolve())
            rules = [{"kind": "alignment", "axis": "y", "members": SUBJECTS}]
            before = preview_placement(
                _request("placement-legal.kicad_pcb", rules=rules), settings
            ).to_dict()
            after = preview_placement(_request("moved.kicad_pcb", rules=rules), settings).to_dict()

        self.assertEqual(before["status"], after["status"])
        assert before["candidate"] is not None and after["candidate"] is not None
        self.assertEqual(
            before["candidate"]["evidence"]["legality"],
            after["candidate"]["evidence"]["legality"],
        )
        self.assertEqual(
            before["candidate"]["evidence"]["rule_results"],
            after["candidate"]["evidence"]["rule_results"],
        )
        # References are invariant; only the coordinates moved, and by exactly the shift.
        for left, right in zip(
            before["candidate"]["placements"], after["candidate"]["placements"], strict=True
        ):
            self.assertEqual(left["ref_id"], right["ref_id"])
            self.assertEqual(right["origin_nm"][0] - left["origin_nm"][0], 7_000_000)
            self.assertEqual(right["origin_nm"][1], left["origin_nm"][1])
        # Guard the guard: the boards really were different.
        self.assertNotEqual(before["board_revision"], after["board_revision"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
