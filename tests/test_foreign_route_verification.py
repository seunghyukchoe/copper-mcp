"""Foreign SimpleRouteJson route verification: trust boundary, direction of error, budgets."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

from copper_mcp import cli
from copper_mcp.benchmarks.simple_route_json import ImportPolicy, import_simple_route_json
from copper_mcp.foreign_route_verification import (
    ACCEPTANCE_CLAIM,
    NON_CLAIMS,
    ForeignRouteCheck,
    ForeignRouteCheckStatus,
    ForeignRouteRefusalCode,
    ForeignRouteVerificationLimits,
    ForeignRouteVerificationResult,
    verify_foreign_simple_route_json,
)

# One policy for the whole module: 0.2 mm clearance, 0.6/0.3 mm vias — the import defaults.
POLICY = ImportPolicy()


def problem_document(
    *,
    layer_count: int = 1,
    obstacles: list[dict[str, Any]] | None = None,
    connections: list[dict[str, Any]] | None = None,
) -> bytes:
    """A 10x10 mm board with two 1x1 mm pads on net A at (-4, 0) and (4, 0) by default."""

    if obstacles is None:
        obstacles = [
            _pad_obstacle(-4, 0, "A.1"),
            _pad_obstacle(4, 0, "A.2"),
        ]
    if connections is None:
        connections = [
            {
                "name": "A",
                "pointsToConnect": [
                    {"x": -4, "y": 0, "layer": "top", "pointId": "A.1"},
                    {"x": 4, "y": 0, "layer": "top", "pointId": "A.2"},
                ],
            }
        ]
    return json.dumps(
        {
            "bounds": {"minX": -5, "maxX": 5, "minY": -5, "maxY": 5},
            "obstacles": obstacles,
            "connections": connections,
            "layerCount": layer_count,
            "minTraceWidth": 0.15,
        }
    ).encode()


def _pad_obstacle(x: float, y: float, point_id: str) -> dict[str, Any]:
    return {
        "type": "rect",
        "layers": ["top"],
        "center": {"x": x, "y": y},
        "width": 1,
        "height": 1,
        "connectedTo": [point_id],
    }


def _keepout_obstacle(x: float, y: float, width: float = 1, height: float = 1) -> dict[str, Any]:
    return {
        "type": "rect",
        "layers": ["top"],
        "center": {"x": x, "y": y},
        "width": width,
        "height": height,
        "connectedTo": [],
    }


def straight_trace(
    *,
    connection_name: str = "A",
    y: float = 0,
    width: float = 0.15,
    layer: str = "top",
    x_from: float = -4,
    x_to: float = 4,
) -> dict[str, Any]:
    return {
        "type": "pcb_trace",
        "pcb_trace_id": "trace_0",
        "connection_name": connection_name,
        "route": [
            {"route_type": "wire", "x": x_from, "y": y, "width": width, "layer": layer},
            {"route_type": "wire", "x": x_to, "y": y, "width": width, "layer": layer},
        ],
    }


def solution_document(traces: list[dict[str, Any]], **extra: Any) -> bytes:
    return json.dumps({"traces": traces, **extra}).encode()


def sha256_of(document: bytes) -> str:
    return hashlib.sha256(document).hexdigest()


def verify(
    problem: bytes,
    solution: bytes,
    *,
    expected: str | None = None,
    limits: ForeignRouteVerificationLimits | None = None,
) -> ForeignRouteVerificationResult:
    return verify_foreign_simple_route_json(
        problem,
        solution,
        expected_problem_sha256=expected if expected is not None else sha256_of(problem),
        policy=POLICY,
        limits=limits,
    )


def check_status(result: ForeignRouteVerificationResult, check: ForeignRouteCheck) -> str:
    return next(evidence.status.value for evidence in result.checks if evidence.check is check)


class TestAcceptance:
    def test_a_valid_foreign_route_verifies_with_full_evidence(self) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()])

        result = verify(problem, solution)

        assert result.refusal is None
        assert result.verified
        assert result.verdict == "clearance_and_connectivity_verified"
        assert all(evidence.status is ForeignRouteCheckStatus.PASSED for evidence in result.checks)
        assert result.trace_count == 1
        assert result.segment_count == 1
        assert result.wire_point_count == 2
        assert result.via_count == 0
        assert result.pair_checks > 0
        assert result.rounding_slack_doubled_nm == 0

    def test_the_result_binds_computed_content_addresses_and_the_import_digest(self) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()])

        result = verify(problem, solution)
        imported = import_simple_route_json("fixture", problem, policy=POLICY)

        assert result.problem_sha256 == f"sha256:{sha256_of(problem)}"
        assert result.solution_sha256 == f"sha256:{sha256_of(solution)}"
        assert result.snapshot_digest == imported.snapshot.snapshot_digest

    def test_a_two_layer_route_with_a_via_verifies(self) -> None:
        problem = problem_document(layer_count=2)
        trace = {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": 0, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "via", "x": 0, "y": 0, "from_layer": "top", "to_layer": "bottom"},
                {"route_type": "wire", "x": 0, "y": 0, "width": 0.2, "layer": "bottom"},
                {"route_type": "wire", "x": 3, "y": 0, "width": 0.2, "layer": "bottom"},
                {"route_type": "via", "x": 3, "y": 0, "from_layer": "bottom", "to_layer": "top"},
                {"route_type": "wire", "x": 3, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "top"},
            ],
        }
        result = verify(problem, solution_document([trace]))

        assert result.refusal is None, result.refusal
        assert result.via_count == 2

    def test_the_response_never_carries_native_identity_or_authority_fields(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([straight_trace()]))

        document = result.to_dict()
        rendered = json.dumps(document)
        for forbidden in ("candidate_id", "base_revision", "apply_token"):
            assert forbidden not in rendered
        assert document["origin"] == "foreign_untrusted"
        assert document["apply_authority"] == "none"
        assert document["kicad_drc"] == "not_run"
        assert document["repair"] == "not_attempted"
        assert document["claim"] == ACCEPTANCE_CLAIM
        assert list(document["non_claims"]) == list(NON_CLAIMS)

    def test_a_refused_result_carries_no_claim(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([]), expected="0" * 64)

        assert result.to_dict()["claim"] is None


class TestRevisionBinding:
    def test_a_solution_bound_to_the_wrong_problem_revision_is_refused(self) -> None:
        problem = problem_document()
        other = problem_document(layer_count=2)
        solution = solution_document([straight_trace()])

        result = verify(problem, solution, expected=sha256_of(other))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.WRONG_REVISION
        assert check_status(result, ForeignRouteCheck.REVISION_BINDING) == "failed"
        # Nothing beyond the binding ran: the board was never imported.
        assert check_status(result, ForeignRouteCheck.PROBLEM_IMPORT) == "not_run"
        assert check_status(result, ForeignRouteCheck.CLEARANCE) == "not_run"
        assert result.snapshot_digest is None

    def test_a_malformed_expected_digest_is_refused_not_defaulted(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([straight_trace()]), expected="not-a-digest")

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.WRONG_REVISION

    def test_the_sha256_prefix_form_is_accepted_for_the_same_binding(self) -> None:
        problem = problem_document()
        result = verify(
            problem,
            solution_document([straight_trace()]),
            expected=f"sha256:{sha256_of(problem)}",
        )

        assert result.refusal is None


class TestLaundering:
    @pytest.mark.parametrize(
        "key",
        [
            "candidate_id",
            "base_revision",
            "board_revision",
            "snapshot_digest",
            "apply_token",
            "authorization_digest",
            "router_version",
            "origin",
            "copper_mcp",
        ],
    )
    def test_a_solution_asserting_a_reserved_identity_key_is_refused(self, key: str) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()], **{key: "sha256:" + "0" * 64})

        result = verify(problem, solution)

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.FORGED_IDENTITY
        assert check_status(result, ForeignRouteCheck.IDENTITY_HYGIENE) == "failed"

    def test_a_trace_asserting_a_reserved_identity_key_is_refused(self) -> None:
        problem = problem_document()
        trace = straight_trace()
        trace["candidate_id"] = "sha256:" + "f" * 64

        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.FORGED_IDENTITY

    def test_reserved_key_refusal_happens_even_when_geometry_would_verify(self) -> None:
        # The same solution verifies without the key: the refusal is about identity, not shape.
        problem = problem_document()
        assert verify(problem, solution_document([straight_trace()])).refusal is None

        forged = solution_document([straight_trace()], candidate_id="sha256:" + "a" * 64)
        result = verify(problem, forged)

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.FORGED_IDENTITY

    def test_an_unknown_root_key_is_refused_by_the_document_contract(self) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()], vendor_extension=True)

        result = verify(problem, solution)

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.MALFORMED_DOCUMENT


class TestClearanceRefusals:
    def test_a_route_that_violates_keepout_clearance_is_refused(self) -> None:
        # Keepout copper at (0, 0.5): a trace along y=0.5 runs straight through it.
        problem = problem_document(
            obstacles=[
                _pad_obstacle(-4, 0, "A.1"),
                _pad_obstacle(4, 0, "A.2"),
                _keepout_obstacle(0, 0.5),
            ]
        )
        # The trace dodges nothing: straight through the keepout band.
        result = verify(problem, solution_document([straight_trace(y=0.5)]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.CLEARANCE_VIOLATION
        assert check_status(result, ForeignRouteCheck.CLEARANCE) == "failed"
        assert check_status(result, ForeignRouteCheck.CONNECTIVITY) == "not_run"

    def test_a_route_too_close_to_another_nets_pad_is_refused(self) -> None:
        # Net B pad at (0, 1): 1x1 mm, so its copper spans y in [0.5, 1.5].  A 0.15 mm trace at
        # y = 0.75 has its top edge at 0.825 — 0.325 mm gap > 0.2 clearance would pass, but at
        # y = 0.4 the gap to B's copper is 0.025 mm and must refuse.
        problem = problem_document(
            obstacles=[
                _pad_obstacle(-4, 0, "A.1"),
                _pad_obstacle(4, 0, "A.2"),
                _pad_obstacle(0, 1, "B.1"),
            ],
            connections=[
                {
                    "name": "A",
                    "pointsToConnect": [
                        {"x": -4, "y": 0, "layer": "top", "pointId": "A.1"},
                        {"x": 4, "y": 0, "layer": "top", "pointId": "A.2"},
                    ],
                },
                {
                    "name": "B",
                    "pointsToConnect": [{"x": 0, "y": 1, "layer": "top", "pointId": "B.1"}],
                },
            ],
        )
        result = verify(problem, solution_document([straight_trace(y=0.4)]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.CLEARANCE_VIOLATION

    def test_two_foreign_traces_on_different_nets_must_clear_each_other(self) -> None:
        problem = problem_document(
            obstacles=[
                _pad_obstacle(-4, 0, "A.1"),
                _pad_obstacle(4, 0, "A.2"),
                _pad_obstacle(-4, 0.2, "B.1"),
                _pad_obstacle(4, 0.2, "B.2"),
            ],
            connections=[
                {
                    "name": "A",
                    "pointsToConnect": [
                        {"x": -4, "y": 0, "layer": "top", "pointId": "A.1"},
                        {"x": 4, "y": 0, "layer": "top", "pointId": "A.2"},
                    ],
                },
                {
                    "name": "B",
                    "pointsToConnect": [
                        {"x": -4, "y": 0.2, "layer": "top", "pointId": "B.1"},
                        {"x": 4, "y": 0.2, "layer": "top", "pointId": "B.2"},
                    ],
                },
            ],
        )
        # Two 0.15 mm traces 0.2 mm apart centreline-to-centreline: the copper gap is
        # 0.05 mm, far below the 0.2 mm clearance.
        result = verify(
            problem,
            solution_document([straight_trace(y=0), straight_trace(connection_name="B", y=0.2)]),
        )

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.CLEARANCE_VIOLATION

    def test_a_via_too_close_to_foreign_copper_is_refused(self) -> None:
        problem = problem_document(
            layer_count=2,
            obstacles=[
                _pad_obstacle(-4, 0, "A.1"),
                _pad_obstacle(4, 0, "A.2"),
                _keepout_obstacle(0, 1),
            ],
        )
        trace = {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                # Via at (0, 0.4): barrel radius 0.3, keepout copper starts at y=0.5.
                # Gap 0.1 mm - 0.3 mm radius: the barrel edge is 0.2 - 0.2 = clearance exactly?
                # 0.5 - 0.4 = 0.1 < 0.3 + 0.2: violation.
                {"route_type": "wire", "x": 0, "y": 0.4, "width": 0.2, "layer": "top"},
                {
                    "route_type": "via",
                    "x": 0,
                    "y": 0.4,
                    "from_layer": "top",
                    "to_layer": "bottom",
                },
                {"route_type": "wire", "x": 0, "y": 0.4, "width": 0.2, "layer": "bottom"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "bottom"},
                {"route_type": "via", "x": 4, "y": 0, "from_layer": "bottom", "to_layer": "top"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "top"},
            ],
        }
        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.CLEARANCE_VIOLATION

    def test_clearance_is_layer_scoped(self) -> None:
        # The same keepout collision as above, but the trace runs on the bottom layer of a
        # two-layer board while the keepout names only the top layer.
        problem = problem_document(
            layer_count=2,
            obstacles=[
                {**_pad_obstacle(-4, 0, "A.1"), "layers": ["bottom"]},
                {**_pad_obstacle(4, 0, "A.2"), "layers": ["bottom"]},
                _keepout_obstacle(0, 0.5),
            ],
            connections=[
                {
                    "name": "A",
                    "pointsToConnect": [
                        {"x": -4, "y": 0, "layer": "bottom", "pointId": "A.1"},
                        {"x": 4, "y": 0, "layer": "bottom", "pointId": "A.2"},
                    ],
                }
            ],
        )
        result = verify(problem, solution_document([straight_trace(y=0.5, layer="bottom")]))

        assert result.refusal is None, result.refusal


class TestDirectionOfError:
    @staticmethod
    def _tight_problem() -> bytes:
        # Keepout copper spans y in [1.0, 2.0] around x = 0.
        return problem_document(
            obstacles=[
                _pad_obstacle(-4, 0, "A.1"),
                _pad_obstacle(4, 0, "A.2"),
                _keepout_obstacle(0, 1.5),
            ]
        )

    @staticmethod
    def _detour_trace(crossing_y: float) -> dict[str, Any]:
        # Starts and ends on the pads, crossing the keepout band at ``crossing_y``.
        return {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": -4, "y": crossing_y, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": 4, "y": crossing_y, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "top"},
            ],
        }

    def test_an_exactly_legal_separation_verifies_when_tokens_are_exact(self) -> None:
        # A 0.2 mm trace crossing at y = 0.7 has its copper edge at y = 0.8, leaving exactly
        # the 0.2 mm clearance to the keepout copper at y = 1.0.  Equality is legal.
        result = verify(self._tight_problem(), solution_document([self._detour_trace(0.7)]))

        assert result.refusal is None, result.refusal
        assert result.rounding_slack_doubled_nm == 0

    def test_the_same_separation_with_an_inexact_token_is_refused_by_slack(self) -> None:
        # 0.7000000000000001 mm rounds to the same nanometre, but the residue means the true
        # geometry may sit anywhere within the rounding bound — so the exactly-tight case must
        # now refuse rather than pass on doubt.
        result = verify(
            self._tight_problem(),
            solution_document([self._detour_trace(0.7000000000000001)]),
        )

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.CLEARANCE_VIOLATION
        assert result.rounding_slack_doubled_nm == 3

    def test_an_inexact_token_alone_does_not_refuse_a_route_with_margin(self) -> None:
        problem = problem_document()
        trace = straight_trace()
        trace["route"][0]["y"] = 0.0000000000000001

        result = verify(problem, solution_document([trace]))

        assert result.refusal is None, result.refusal
        assert result.rounding_slack_doubled_nm == 3

    def test_a_width_below_the_stated_minimum_is_refused_exactly(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([straight_trace(width=0.1499999)]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.WIDTH_BELOW_MINIMUM
        assert check_status(result, ForeignRouteCheck.TRACE_WIDTH) == "failed"

    def test_route_copper_leaving_the_board_outline_is_refused(self) -> None:
        problem = problem_document()
        # The centreline reaches x = 4.95; with a 0.15 mm width the copper edge crosses 5.0.
        result = verify(problem, solution_document([straight_trace(x_to=4.95)]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.OUTSIDE_BOARD_BOUNDS


class TestStructuralRefusals:
    def test_a_layer_change_without_a_via_is_refused(self) -> None:
        problem = problem_document(layer_count=2)
        trace = {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "bottom"},
            ],
        }
        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.TRACE_DISCONTINUITY

    def test_a_via_not_coincident_with_its_wire_points_is_refused(self) -> None:
        problem = problem_document(layer_count=2)
        trace = {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "via", "x": 0, "y": 0, "from_layer": "top", "to_layer": "bottom"},
                {"route_type": "wire", "x": 0, "y": 0, "width": 0.2, "layer": "bottom"},
                {"route_type": "wire", "x": 4, "y": 0, "width": 0.2, "layer": "bottom"},
            ],
        }
        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.TRACE_DISCONTINUITY

    def test_a_trace_ending_on_a_via_is_refused(self) -> None:
        problem = problem_document(layer_count=2)
        trace = {
            "type": "pcb_trace",
            "connection_name": "A",
            "route": [
                {"route_type": "wire", "x": -4, "y": 0, "width": 0.2, "layer": "top"},
                {"route_type": "via", "x": -4, "y": 0, "from_layer": "top", "to_layer": "bottom"},
            ],
        }
        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.TRACE_DISCONTINUITY

    def test_an_unattributed_trace_is_refused(self) -> None:
        problem = problem_document()
        trace = straight_trace()
        del trace["connection_name"]

        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.UNATTRIBUTED_TRACE

    def test_a_trace_naming_an_unknown_connection_is_refused(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([straight_trace(connection_name="GHOST")]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.UNATTRIBUTED_TRACE

    def test_a_wire_point_on_an_undeclared_layer_is_refused(self) -> None:
        problem = problem_document()  # one-layer stack: "bottom" does not exist
        result = verify(problem, solution_document([straight_trace(layer="bottom")]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT

    def test_an_unknown_route_type_is_refused(self) -> None:
        problem = problem_document()
        trace = straight_trace()
        trace["route"].append({"route_type": "arc", "x": 0, "y": 0})

        result = verify(problem, solution_document([trace]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.UNSUPPORTED_TRACE_ELEMENT

    def test_nan_and_infinity_coordinates_are_refused(self) -> None:
        problem = problem_document()
        text = json.dumps({"traces": [straight_trace()]}).replace("-4", "NaN", 1)

        result = verify(problem, text.encode())

        assert result.refusal is not None
        assert result.refusal.code in {
            ForeignRouteRefusalCode.MALFORMED_DOCUMENT,
            ForeignRouteRefusalCode.UNSUPPORTED_UNIT,
        }


class TestConnectivity:
    def test_a_solution_missing_the_route_entirely_is_refused(self) -> None:
        problem = problem_document()
        result = verify(problem, solution_document([]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.NOT_CONNECTED
        assert check_status(result, ForeignRouteCheck.CONNECTIVITY) == "failed"

    def test_a_route_that_stops_short_of_a_pad_is_refused(self) -> None:
        problem = problem_document()
        # Ends at x = 2.9: pad copper starts at 3.5, so the under-approximated copper
        # never reaches the second pad.
        result = verify(problem, solution_document([straight_trace(x_to=2.9)]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.NOT_CONNECTED

    def test_two_touching_traces_of_one_net_count_as_one_component(self) -> None:
        problem = problem_document()
        left = straight_trace(x_from=-4, x_to=0)
        right = straight_trace(x_from=0, x_to=4)

        result = verify(problem, solution_document([left, right]))

        assert result.refusal is None, result.refusal


class TestBudgets:
    def test_too_many_traces_are_refused(self) -> None:
        problem = problem_document()
        limits = ForeignRouteVerificationLimits(max_traces=1)
        result = verify(
            problem,
            solution_document([straight_trace(), straight_trace()]),
            limits=limits,
        )

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.BUDGET_EXCEEDED

    def test_an_exhausted_pair_check_budget_refuses_instead_of_passing(self) -> None:
        problem = problem_document()
        limits = ForeignRouteVerificationLimits(max_pair_checks=1)
        result = verify(problem, solution_document([straight_trace()]), limits=limits)

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.BUDGET_EXCEEDED

    def test_an_oversized_solution_document_is_refused(self) -> None:
        problem = problem_document()
        limits = ForeignRouteVerificationLimits(max_document_bytes=len(problem) + 100)
        oversized = straight_trace()
        oversized["pcb_trace_id"] = "x" * 10_000
        result = verify(problem, solution_document([oversized]), limits=limits)

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.BUDGET_EXCEEDED
        assert check_status(result, ForeignRouteCheck.DOCUMENT_CONTRACT) == "failed"

    def test_a_refused_problem_document_reports_the_import_refusal(self) -> None:
        result = verify(b"{}", solution_document([straight_trace()]))

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.PROBLEM_REFUSED
        assert check_status(result, ForeignRouteCheck.PROBLEM_IMPORT) == "failed"

    def test_malformed_solution_json_is_refused(self) -> None:
        problem = problem_document()
        result = verify(problem, b"not json")

        assert result.refusal is not None
        assert result.refusal.code is ForeignRouteRefusalCode.MALFORMED_DOCUMENT


class TestCli:
    def test_the_cli_verifies_and_reports_the_typed_verdict(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()])
        (tmp_path / "problem.json").write_bytes(problem)
        (tmp_path / "solution.json").write_bytes(solution)

        exit_code = cli.main(
            [
                "--workspace",
                str(tmp_path),
                "verify-foreign-route",
                "problem.json",
                "solution.json",
                "--expect-problem-sha256",
                sha256_of(problem),
            ]
        )

        assert exit_code == 0
        document = json.loads(capsys.readouterr().out)
        assert document["verdict"] == "clearance_and_connectivity_verified"
        assert document["refusal"] is None
        assert "candidate_id" not in json.dumps(document)

    def test_the_cli_reports_a_refusal_as_data_not_as_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        problem = problem_document()
        solution = solution_document([straight_trace()])
        (tmp_path / "problem.json").write_bytes(problem)
        (tmp_path / "solution.json").write_bytes(solution)

        exit_code = cli.main(
            [
                "--workspace",
                str(tmp_path),
                "verify-foreign-route",
                "problem.json",
                "solution.json",
                "--expect-problem-sha256",
                "0" * 64,
            ]
        )

        assert exit_code == 0
        document = json.loads(capsys.readouterr().out)
        assert document["verdict"] == "refused"
        assert document["refusal"]["code"] == "wrong_revision"
