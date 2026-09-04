"""The bounded placement-solve surface: service, MCP tool, and CLI.

The solver core's own behaviour is covered in ``test_placement_solver`` and the legalizer's
in ``test_placement``. What matters here is that the surface does not weaken either: the
same verdicts and candidate identities must survive the request boundary, every response
must satisfy the advertised machine contract, tokens must never be minted, and a hostile
or exhausted request must refuse with the code the contract promises.
"""

from __future__ import annotations

import io
import json
import unittest
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import replace
from pathlib import Path
from typing import Any

import copper_mcp.mcp_server as _server
from copper_mcp.cli import main
from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import PlacementSolveToolResponse
from copper_mcp.placement.contracts import PlacementError
from copper_mcp.placement_solve import solve_placement_preview

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
PAD_OF_FIRST = "pad:kicad:90000000-0000-0000-0000-000000000002"
WIDE_SOLVER = {"max_ranked": 4, "max_evaluations": 512}


def _settings(**overrides: Any) -> Settings:
    return replace(Settings(workspace=FIXTURES.resolve()), **overrides)


def _request(board: str, **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "board": board,
        "constraints": dict(CONSTRAINTS),
        "subjects": list(SUBJECTS),
        "solver": dict(WIDE_SOLVER),
    }
    payload.update(overrides)
    return payload


def _solve(board: str, **overrides: Any) -> dict[str, Any]:
    document = solve_placement_preview(_request(board, **overrides), _settings()).to_dict()
    # Every response this suite produces must satisfy the advertised machine contract, so a
    # widened response cannot pass by only being checked field by field.
    PlacementSolveToolResponse.model_validate(document)
    return document


class SolveSurfaceTests(unittest.TestCase):
    def test_solved_response_carries_ranked_candidates_and_no_authority(self) -> None:
        document = _solve("placement-legal.kicad_pcb")
        self.assertEqual(document["status"], "solved")
        self.assertEqual(document["placement_solve_version"], "0.1.0")
        self.assertGreaterEqual(len(document["candidates"]), 1)
        self.assertLessEqual(len(document["candidates"]), 4)
        self.assertIsNone(document["apply_token"])
        self.assertEqual(document["apply_token_withheld_reason"], "unsupported_surface")
        self.assertIsNone(document["diagnostic"])
        for candidate in document["candidates"]:
            self.assertTrue(candidate["candidate_id"].startswith("sha256:"))
            # base_revision binds the Board IR base, not the file: every candidate from
            # one search shares it, and it is distinct from the file board_revision.
            self.assertTrue(candidate["base_revision"].startswith("sha256:"))
        self.assertEqual(
            len({candidate["base_revision"] for candidate in document["candidates"]}), 1
        )
        self.assertGreater(document["evaluations"], 0)
        self.assertEqual(document["scoring_policy"], "same-net-manhattan-v1")
        self.assertEqual(
            document["request"]["solver"]["max_evaluations"], WIDE_SOLVER["max_evaluations"]
        )

    def test_solve_is_deterministic_across_runs(self) -> None:
        first = _solve("placement-legal.kicad_pcb")
        second = _solve("placement-legal.kicad_pcb")
        self.assertEqual(
            [item["candidate_id"] for item in first["candidates"]],
            [item["candidate_id"] for item in second["candidates"]],
        )
        self.assertEqual(first["evaluations"], second["evaluations"])

    def test_max_ranked_caps_the_ranking(self) -> None:
        document = _solve("placement-legal.kicad_pcb", solver={**WIDE_SOLVER, "max_ranked": 1})
        self.assertEqual(document["status"], "solved")
        self.assertEqual(len(document["candidates"]), 1)

    def test_exhaustion_is_a_budget_refusal_not_a_verdict(self) -> None:
        document = _solve("placement-legal.kicad_pcb", solver={"max_ranked": 4})
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "budget_exhausted")
        self.assertEqual(document["candidates"], [])
        # Spent work is still reported: the refusal admits ignorance with receipts.
        self.assertEqual(document["evaluations"], 64)
        self.assertIsNone(document["apply_token"])
        self.assertEqual(document["apply_token_withheld_reason"], "unsupported_surface")

    def test_an_illegal_starting_placement_returns_the_legalizer_refusal(self) -> None:
        document = _solve("placement-overlap.kicad_pcb")
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "illegal_placement")
        self.assertIsNotNone(document["diagnostic"]["legality"])

    def test_collapsing_proposals_refuse_as_infeasible(self) -> None:
        document = _solve(
            "placement-legal.kicad_pcb",
            subjects=[SUBJECTS[0], PAD_OF_FIRST, SUBJECTS[1]],
            proposals=[
                {"subject": SUBJECTS[0], "offset_x_nm": 1_000_000},
                {"subject": PAD_OF_FIRST, "offset_x_nm": 2_000_000},
            ],
        )
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "infeasible_constraints")

    def test_capability_and_drc_flags_are_not_fields_of_this_surface(self) -> None:
        for flag in ("include_apply_token", "include_drc"):
            with self.subTest(flag=flag):
                with self.assertRaises(PlacementError):
                    solve_placement_preview(
                        _request("placement-legal.kicad_pcb", **{flag: True}), _settings()
                    )

    def test_unknown_solver_fields_and_out_of_range_budgets_refuse(self) -> None:
        with self.assertRaises(PlacementError):
            solve_placement_preview(
                _request("placement-legal.kicad_pcb", solver={**WIDE_SOLVER, "seed": 7}),
                _settings(),
            )
        # Booleans are never integers at the request boundary.
        with self.assertRaises(PlacementError):
            solve_placement_preview(
                _request(
                    "placement-legal.kicad_pcb",
                    placement_grid_nm=True,
                    solver=dict(WIDE_SOLVER),
                ),
                _settings(),
            )
        with self.assertRaises(PlacementError):
            solve_placement_preview(
                _request("placement-legal.kicad_pcb", solver={**WIDE_SOLVER, "max_ranked": 17}),
                _settings(),
            )
        with self.assertRaises(PlacementError):
            solve_placement_preview(
                _request(
                    "placement-legal.kicad_pcb",
                    solver={**WIDE_SOLVER, "max_evaluations": 1025},
                ),
                _settings(),
            )
        with self.assertRaises(PlacementError):
            solve_placement_preview(
                _request(
                    "placement-legal.kicad_pcb",
                    solver={**WIDE_SOLVER, "scoring_policy": "learned-v99"},
                ),
                _settings(),
            )

    def test_stale_revision_refuses_before_search(self) -> None:
        document = _solve("placement-legal.kicad_pcb", expect_board_revision="sha256:" + "0" * 64)
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "stale_revision")
        self.assertEqual(document["evaluations"], 0)

    def test_an_approximated_outline_refuses_like_preview(self) -> None:
        document = _solve("placement-arc-outline.kicad_pcb")
        self.assertEqual(document["status"], "refused")
        assert document["diagnostic"] is not None
        self.assertEqual(document["diagnostic"]["code"], "unsupported_geometry")

    def test_an_unsupported_board_reports_counts_and_no_candidates(self) -> None:
        board = ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "malformed-unbalanced.kicad_pcb"
        document = solve_placement_preview(
            _request(board.name, solver=dict(WIDE_SOLVER)),
            replace(_settings(), workspace=board.parent.resolve()),
        ).to_dict()
        PlacementSolveToolResponse.model_validate(document)
        self.assertEqual(document["status"], "unsupported_board")
        self.assertEqual(document["candidates"], [])
        self.assertTrue(document["conversion_diagnostic_counts"])

    def test_route_aware_policy_reports_its_probe_accounting(self) -> None:
        document = _solve(
            "placement-legal.kicad_pcb",
            solver={**WIDE_SOLVER, "scoring_policy": "route-aware-astar-v1"},
        )
        self.assertEqual(document["status"], "solved")
        self.assertEqual(document["scoring_policy"], "route-aware-astar-v1")
        self.assertGreater(document["route_probe_limit"], 0)
        self.assertLessEqual(document["route_probes_used"], document["route_probe_limit"])


class SolveMcpTests(unittest.TestCase):
    def test_the_tool_is_registered_on_both_transports(self) -> None:
        import asyncio

        names = {tool.name for tool in asyncio.run(_server.mcp.list_tools())}
        self.assertIn("solve_placement", names)


class SolveCliTests(unittest.TestCase):
    def _run(self, *arguments: str) -> tuple[int, str, str]:
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(list(arguments))
        return code, stdout.getvalue(), stderr.getvalue()

    def _base(self, *extra: str) -> list[str]:
        arguments = [
            "--workspace",
            str(FIXTURES),
            "solve-placement",
            "placement-legal.kicad_pcb",
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
        return arguments + list(extra)

    def test_solve_through_the_cli_matches_the_service(self) -> None:
        exit_code, out, _ = self._run(
            *self._base("--solver-max-ranked", "2", "--solver-max-evaluations", "512")
        )
        self.assertEqual(exit_code, 0)
        document = json.loads(out)
        PlacementSolveToolResponse.model_validate(document)
        self.assertEqual(document["status"], "solved")
        self.assertEqual(len(document["candidates"]), 2)
        self.assertEqual(document["apply_token_withheld_reason"], "unsupported_surface")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
