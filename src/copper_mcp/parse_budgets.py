"""The single seam between process configuration and the Board IR parser's budgets.

Before this module every board-reading service built its own ``ParseLimits`` and derived exactly
one field from settings::

    default_limits = ParseLimits()
    limits = replace(
        default_limits,
        max_input_bytes=min(default_limits.max_input_bytes, settings.max_board_bytes),
    )

That block was copied to thirteen call sites, so the six structural budgets were hardcoded
thirteen times over and an operator could move only the byte ceiling — which, at the defaults
that shipped, could never be the binding constraint (issue #112). Deriving the limits in one place
means a new budget becomes operator-settable everywhere at once, or nowhere, and never in a subset
of the services that read the same board.

``ParseLimits`` itself stays free of any dependency on process configuration: it is the pure
contract, and this module is the adapter that binds it to one deployment's settings.
"""

from __future__ import annotations

from dataclasses import replace

from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.config import Settings

#: ``ParseLimits`` field name -> ``Settings`` field name, for every budget an operator may move.
#: The fields deliberately absent are the ones no environment variable exposes: ``max_depth``,
#: ``max_atom_chars``, ``max_vertices_per_ring``, and ``max_diagnostics`` bound the *shape* of one
#: construct rather than the scale of a document, and calibration found no measured board within
#: two orders of magnitude of any of them.
_SETTINGS_BY_LIMIT: dict[str, str] = {
    "max_tokens": "max_parse_tokens",
    "max_nodes": "max_parse_nodes",
    "max_children_per_list": "max_parse_children_per_list",
    "max_objects": "max_parse_objects",
    "max_total_vertices": "max_parse_total_vertices",
    "max_intersection_tests": "max_parse_intersection_tests",
}


def parse_limits_for(settings: Settings) -> ParseLimits:
    """Return the structural parse budgets one deployment's settings authorize.

    The six structural budgets are taken **as configured**. Their whole point is that an operator
    can move them, and ``Settings.from_env`` has already bounded each one to a range that ends
    where the budget stops being reachable, so there is nothing left here to clamp against.

    ``max_input_bytes`` is the deliberate exception and keeps its long-standing ``min`` semantics.
    ``COPPER_MCP_MAX_BOARD_BYTES`` is not a parser setting: it also bounds workspace file reads,
    DRC captures, and live-editor serializations, and it defaults four times higher than the
    parser's own input ceiling. Letting it raise the parser ceiling as a side effect would widen
    the parser's exposure for every deployment that raised it for an unrelated reason.
    """

    if not isinstance(settings, Settings):
        raise TypeError("parse budgets require typed settings")
    defaults = ParseLimits()
    configured = {
        limit_field: getattr(settings, settings_field)
        for limit_field, settings_field in _SETTINGS_BY_LIMIT.items()
    }
    return replace(
        defaults,
        max_input_bytes=min(defaults.max_input_bytes, settings.max_board_bytes),
        **configured,
    )
