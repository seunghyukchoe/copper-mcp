from __future__ import annotations

import pytest

from copper_mcp.routing.policy import PolicyBounds, RepairWindowCandidate
from copper_mcp.routing.repair import (
    LocalRepairRequest,
    LocalRepairStatus,
    exact_local_repair,
)


def _window() -> RepairWindowCandidate:
    return RepairWindowCandidate(
        net_id="net:repair",
        bounds=PolicyBounds(0, 0, 4, 4),
        conflict_score=3,
    )


def _detour_request(*, max_expansions: int = 64) -> LocalRepairRequest:
    # Predeclared window-detour-v1 fixture.  The vertical three-cell barrier makes the straight
    # four-step route unavailable; any legal route must detour through y=0 or y=4.
    return LocalRepairRequest(
        repair_window=_window(),
        start=(0, 2),
        end=(4, 2),
        blocked_cells=((2, 1), (2, 2), (2, 3)),
        max_expansions=max_expansions,
    )


def test_exact_local_repair_solves_predeclared_bounded_detour_repeatably() -> None:
    request = _detour_request()
    results = tuple(exact_local_repair(request) for _ in range(10))
    result = results[0]

    # Acceptance criterion predeclared before implementation: 10/10 equal replays, with an
    # 8-step / 2-bend in-window detour.  This is an abstract lattice operator, not PCB DRC.
    assert results == (result,) * 10
    assert result.status is LocalRepairStatus.COMPLETED
    assert result.route == (
        (0, 2),
        (0, 1),
        (0, 0),
        (1, 0),
        (2, 0),
        (3, 0),
        (4, 0),
        (4, 1),
        (4, 2),
    )
    assert len(result.route) - 1 == 8
    assert result.bend_count == 2
    assert result.expanded_states <= request.max_expansions
    assert result.input_digest == request.input_digest
    assert result.route_digest.startswith("sha256:")


def test_exact_local_repair_optimizes_bends_after_unit_length() -> None:
    request = LocalRepairRequest(
        repair_window=_window(),
        start=(0, 0),
        end=(2, 2),
        blocked_cells=(),
    )
    result = exact_local_repair(request)

    assert result.status is LocalRepairStatus.COMPLETED
    assert len(result.route) - 1 == 4
    assert result.bend_count == 1
    assert result.route == ((0, 0), (0, 1), (0, 2), (1, 2), (2, 2))


def test_local_repair_never_escapes_selected_window_or_uses_blocked_cells() -> None:
    request = _detour_request()
    result = exact_local_repair(request)

    assert result.status is LocalRepairStatus.COMPLETED
    assert all(
        request.repair_window.bounds.contains(PolicyBounds(cell[0], cell[1], cell[0], cell[1]))
        for cell in result.route
    )
    assert not set(result.route).intersection(request.blocked_cells)


def test_local_repair_reports_bounded_budget_without_a_partial_route() -> None:
    result = exact_local_repair(_detour_request(max_expansions=1))

    assert result.status is LocalRepairStatus.BUDGET_EXHAUSTED
    assert result.route == ()
    assert result.bend_count == 0
    assert result.expanded_states == 1


@pytest.mark.parametrize(
    "cancelled",
    (
        lambda: True,
        lambda: (_ for _ in ()).throw(RuntimeError("untrusted callback")),
    ),
)
def test_local_repair_cancellation_fails_closed_without_a_route(cancelled: object) -> None:
    result = exact_local_repair(_detour_request(), cancelled=cancelled)

    assert result.status is LocalRepairStatus.CANCELLED
    assert result.route == ()
    assert result.bend_count == 0


def test_local_repair_rejects_untrusted_boundaries_with_fixed_diagnostics() -> None:
    invalid_request = exact_local_repair(object())
    invalid_callback = exact_local_repair(_detour_request(), cancelled=object())

    assert invalid_request.status is LocalRepairStatus.INVALID_REQUEST
    assert invalid_request.input_digest == "sha256:" + "0" * 64
    assert invalid_callback.status is LocalRepairStatus.INVALID_REQUEST
    assert invalid_callback.route == ()


def test_local_repair_request_rejects_out_of_window_uncanonical_and_oversized_input() -> None:
    with pytest.raises(ValueError, match="inside the repair window"):
        LocalRepairRequest(_window(), start=(-1, 2), end=(4, 2))
    with pytest.raises(ValueError, match="canonical"):
        LocalRepairRequest(_window(), start=(0, 2), end=(4, 2), blocked_cells=((2, 2), (1, 2)))
    with pytest.raises(ValueError, match="endpoints cannot be blocked"):
        LocalRepairRequest(_window(), start=(0, 2), end=(4, 2), blocked_cells=((0, 2),))
    with pytest.raises(ValueError, match="cell budget"):
        LocalRepairRequest(
            RepairWindowCandidate("net:repair", PolicyBounds(0, 0, 4, 4), 3),
            start=(0, 0),
            end=(4, 4),
            max_window_cells=24,
        )


def test_local_repair_route_digest_changes_when_the_coordinator_input_changes() -> None:
    first = exact_local_repair(_detour_request())
    second = exact_local_repair(
        LocalRepairRequest(
            repair_window=_window(),
            start=(0, 2),
            end=(4, 2),
            blocked_cells=((2, 2),),
        )
    )

    assert first.status is LocalRepairStatus.COMPLETED
    assert second.status is LocalRepairStatus.COMPLETED
    assert first.input_digest != second.input_digest
    assert first.route_digest != second.route_digest
