from __future__ import annotations

from typing import get_args

import copper_mcp.routing.jobs as routing_jobs
from copper_mcp.mcp_contracts import LayeredRouteDiagnosticContract, RoutingJobToolResponse
from copper_mcp.routing.jobs import RoutingJobFailureCode
from copper_mcp.routing.layered_contracts import LayeredRouteFailureCode
from copper_mcp.routing_job_service import _failure_code


def _literal_values(annotation: object) -> set[object]:
    values: set[object] = set()
    pending = [annotation]
    while pending:
        current = pending.pop()
        args = get_args(current)
        if args:
            pending.extend(args)
        else:
            values.add(current)
    return values


def test_layered_obstacle_check_budget_code_is_public_and_durable() -> None:
    code = "obstacle_check_budget_exceeded"

    assert code in _literal_values(LayeredRouteDiagnosticContract.model_fields["code"].annotation)
    assert code in _literal_values(
        RoutingJobToolResponse.model_fields["diagnostic_code"].annotation
    )
    assert (
        _failure_code(LayeredRouteFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED)
        is RoutingJobFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED
    )


def test_durable_obstacle_check_message_is_fixed_and_non_echoing() -> None:
    secret = "SECRET_CALLER_GEOMETRY"

    message = routing_jobs._FIXED_FAILURE_MESSAGES[
        RoutingJobFailureCode.OBSTACLE_CHECK_BUDGET_EXCEEDED
    ]

    assert message == "routing obstacle-check budget was exceeded"
    assert secret not in message
