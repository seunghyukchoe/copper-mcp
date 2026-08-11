"""The real-board capability runner must never write board geometry into its artifact.

The runner emits a JSON report that is committed to this public repository, while the corpus it
reads is a private, non-redistributable design tree. Its module docstring states the invariant:
"Nothing in the emitted artifact carries board content -- no net names, no component references,
no coordinates."

ADR-0093 put geometry into one refusal message for the first time, which is exactly the kind of
change that breaks such an invariant silently: the runner records one message per verdict, so an
`off_grid` refusal would have carried a pad's miss distance straight into the committed file.
"""

from __future__ import annotations

from copper_mcp.routing.astar import OFF_GRID_MESSAGE_LEAD
from scripts import benchmark_real_board_capability as benchmark


def test_a_refusal_without_geometry_is_recorded_verbatim() -> None:
    diagnostic = {
        "code": "invalid_two_pin_net",
        "message": "the selected net must resolve to exactly two pads on the selected layer",
        "off_grid": None,
    }

    assert benchmark._redacted_message(diagnostic) == diagnostic["message"]


def test_an_off_grid_refusal_is_truncated_to_its_board_independent_lead() -> None:
    """Truncation is keyed to the evidence object, not to the code string or to a substring.

    A caller of the *service* is entitled to these numbers; this artifact is not, and the two
    audiences are distinguished by whether the payload is durable rather than by which code it
    carries.
    """

    diagnostic = {
        "code": "off_grid",
        "message": (
            f"{OFF_GRID_MESSAGE_LEAD}: it misses the nearest lattice point by "
            "(-100000 nm, 0 nm) at grid_step_nm=300000; the largest step that represents "
            "this pad pair is 20000000 nm"
        ),
        "off_grid": {
            "pad_id": "pad:kicad:20000000-0000-0000-0000-000000000004",
            "anchor_pad_id": "pad:kicad:20000000-0000-0000-0000-000000000002",
            "grid_step_nm": 300_000,
            "miss_x_nm": -100_000,
            "miss_y_nm": 0,
            "largest_representable_step_nm": 20_000_000,
        },
    }

    recorded = benchmark._redacted_message(diagnostic)

    assert recorded == OFF_GRID_MESSAGE_LEAD
    assert not any(character.isdigit() for character in recorded)
