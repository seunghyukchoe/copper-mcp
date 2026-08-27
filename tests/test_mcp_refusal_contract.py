"""The MCP refusal contract: which failures are answers, and which are crashes.

From `mcp` 2.1.0 the SDK classifies a tool failure. `ToolError`, `ResourceError` and
`MCPError` are *anticipated*: the caller receives the message. Anything else is a crash: the
message is replaced by a bare `Error executing tool <name>` and only the server log keeps the
text. Through 2.0.1 every escaping exception was rewrapped as an anticipated `ToolError`, so
the two were indistinguishable from the outside and this file could not have been written.

The tests here are deliberately written so they hold on **both** dependency lines. Most of
them exercise `mcp_server._refusals_as_tool_errors` directly rather than through
`call_tool`, because a message-only assertion made through the SDK passes on the 2.0 line
whether or not the translation exists — which would make it useless as mutation evidence.
The two end-to-end tests at the bottom cover the seam the direct tests cannot, and the
2.1-only test asserts the classification itself.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError

from copper_mcp import mcp_server
from copper_mcp.apply.contracts import ApplyRequestError
from copper_mcp.apply.service import ApplyServiceError
from copper_mcp.circuit_scene import CircuitSceneError
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import KiCadCliError
from copper_mcp.kicad_file import BoardFormatError
from copper_mcp.kicad_ipc import KicadIpcConnectionError, KicadIpcDisabledError
from copper_mcp.live_apply import LiveApplyError
from copper_mcp.models import ManifestContractError
from copper_mcp.placement.contracts import PlacementError, PlacementPreviewError
from copper_mcp.post_placement_observation import PostPlacementObservationError
from copper_mcp.request_boundary import RequestError
from copper_mcp.route_preview import RoutePreviewError
from copper_mcp.scene_render import SceneRenderError
from copper_mcp.security import WorkspaceStaleError, WorkspaceViolationError
from copper_mcp.tools import compare_candidates
from copper_mcp.zone_fill import ZoneFillError

try:  # pragma: no cover - the branch taken depends on the resolved dependency
    from mcp.server.mcpserver.exceptions import UnexpectedToolError
except ImportError:  # pragma: no cover - `mcp` 2.0.x has no crash class
    UnexpectedToolError = None  # type: ignore[assignment, misc]

#: One instance of every audited refusal type, including the subclasses the `except` clause is
#: meant to sweep up. Each message is a real one from the surface it names.
ANTICIPATED: tuple[tuple[str, Exception], ...] = (
    ("RequestError", RequestError("board must be an object")),
    ("RoutePreviewError", RoutePreviewError("net_ref_id must be content-addressed with sha256")),
    ("CircuitSceneError", CircuitSceneError("region is malformed")),
    ("ManifestContractError", ManifestContractError("candidate metrics are malformed")),
    ("WorkspaceViolationError", WorkspaceViolationError("path must stay inside the workspace")),
    ("WorkspaceStaleError", WorkspaceStaleError("the target changed since it was read")),
    ("ApplyRequestError", ApplyRequestError("an apply status is malformed")),
    ("PlacementError", PlacementError("a placement grid must be positive")),
    ("PlacementPreviewError", PlacementPreviewError("placement DRC evidence is unavailable")),
    ("BoardFormatError", BoardFormatError("file does not begin with the kicad_pcb token")),
    ("PostPlacementObservationError", PostPlacementObservationError("board revision is stale")),
    ("KicadIpcDisabledError", KicadIpcDisabledError("set COPPER_MCP_ALLOW_LIVE_IPC=1")),
)

#: Failures that must keep crashing. Each one is either documented as an internal fault, or is
#: a machinery failure that says nothing a caller asked about. Reporting any of them as a
#: refusal would present an unhandled defect as a deliberate answer, which is the one direction
#: of error this boundary must never take.
CRASHES: tuple[tuple[str, Exception], ...] = (
    ("LiveApplyError", LiveApplyError("caller-side programming fault")),
    ("ApplyServiceError", ApplyServiceError("not expressible as a typed refusal")),
    ("KiCadCliError", KiCadCliError("the DRC adapter produced no valid evidence")),
    ("SceneRenderError", SceneRenderError("a render could not be canonicalized")),
    ("ZoneFillError", ZoneFillError("cached fill geometry is out of bounds")),
    ("KicadIpcConnectionError", KicadIpcConnectionError("KiCad did not answer")),
    ("ValueError", ValueError("an untyped failure of unknown provenance")),
    ("TypeError", TypeError("unsupported operand")),
    ("RuntimeError", RuntimeError("an invariant broke")),
    ("KeyError", KeyError("a stray lookup deep in a parser")),
    ("RecursionError", RecursionError("maximum recursion depth exceeded")),
)


def _raising(error: Exception) -> Any:
    @mcp_server._refusals_as_tool_errors
    def tool() -> None:
        raise error

    return tool


@pytest.mark.parametrize(("name", "error"), ANTICIPATED, ids=[row[0] for row in ANTICIPATED])
def test_an_audited_refusal_is_re_raised_as_an_anticipated_tool_error(
    name: str, error: Exception
) -> None:
    """Every audited type — and its subclasses — reaches MCP as an anticipated failure."""

    with pytest.raises(ToolError) as caught:
        _raising(error)()
    assert str(caught.value) == str(error), name
    assert caught.value.__cause__ is error, name


@pytest.mark.parametrize(("name", "error"), CRASHES, ids=[row[0] for row in CRASHES])
def test_a_crash_is_never_dressed_as_a_refusal(name: str, error: Exception) -> None:
    """The forbidden direction. A widened handler here would make every row below pass as one."""

    with pytest.raises(type(error)) as caught:
        _raising(error)()
    assert caught.value is error, name
    assert not isinstance(caught.value, ToolError), name


def test_the_refusal_message_crosses_the_boundary_unchanged() -> None:
    """Not merely non-empty: the reason is the whole point of translating."""

    message = "constraints are invalid: clearance_nm must be between 0 and 1000000000"
    with pytest.raises(ToolError) as caught:
        _raising(RequestError(message))()
    assert str(caught.value) == message


def test_a_successful_call_passes_through_the_translation_untouched() -> None:
    """Guard the guard: the wrapper must not become a filter on ordinary results."""

    @mcp_server._refusals_as_tool_errors
    def tool(value: int) -> dict[str, int]:
        return {"value": value}

    assert tool(7) == {"value": 7}
    assert tool.__wrapped__.__name__ == "tool"  # type: ignore[attr-defined]


def test_every_registered_tool_carries_the_refusal_translation() -> None:
    """Registration owns the contract, so a tool added later cannot forget to opt in."""

    registered = mcp_server.mcp._tool_manager.list_tools()
    assert registered, "the server registered no tools"
    missing = [tool.name for tool in registered if getattr(tool.fn, "__wrapped__", None) is None]
    assert missing == []


def test_the_audited_list_is_a_closed_enumeration_of_exception_types() -> None:
    """A blanket entry would defeat the classification the whole boundary exists to make."""

    assert mcp_server._ANTICIPATED_REFUSALS
    for entry in mcp_server._ANTICIPATED_REFUSALS:
        assert isinstance(entry, type) and issubclass(entry, Exception)
        assert entry not in {Exception, BaseException, ValueError, RuntimeError, TypeError}


def test_compare_candidates_refuses_an_over_limit_batch_with_a_typed_refusal() -> None:
    """The count gate is a refusal of an untrusted request, and now says so in its type."""

    with pytest.raises(ManifestContractError):
        compare_candidates([])


def test_a_malformed_candidate_manifest_refuses_with_a_typed_refusal() -> None:
    """`models` decoding is all contract checks, so every rejection carries the refusal type."""

    from copper_mcp.models import candidate_from_dict

    with pytest.raises(ManifestContractError):
        candidate_from_dict({"nested": {}})


def test_the_render_transport_guard_refuses_in_the_adapters_own_vocabulary(tmp_path: Any) -> None:
    """Called directly, so the assertion holds on both lines rather than only the stricter one."""

    settings = Settings(workspace=tmp_path, transport="streamable-http")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_SETTINGS", settings)
        with pytest.raises(ToolError) as caught:
            mcp_server.observe_board_scene({"board": "board.kicad_pcb", "include_render": True})
    assert "stdio" in str(caught.value)


def test_the_schematic_transport_guard_refuses_in_the_adapters_own_vocabulary(
    tmp_path: Any,
) -> None:
    """The sibling guard, pinned the same way."""

    settings = Settings(workspace=tmp_path, transport="streamable-http")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_SETTINGS", settings)
        with pytest.raises(ToolError) as caught:
            mcp_server.render_circuit_schematic({})
    assert "stdio" in str(caught.value)


def test_a_route_refusal_reason_survives_the_call_tool_boundary(tmp_path: Any) -> None:
    """End to end, through the SDK: the caller learns *why*, not merely *that*."""

    settings = Settings(workspace=tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_SETTINGS", settings)
        with pytest.raises(ToolError) as caught:
            asyncio.run(
                mcp_server.mcp.call_tool(
                    "preview_route",
                    {
                        "request": {
                            "board": "board.kicad_pcb",
                            "net": "GND",
                            "layer": "F.Cu",
                            "constraints": {
                                "clearance_nm": -1,
                                "track_width_nm": 250_000,
                                "via_diameter_nm": 600_000,
                                "via_drill_nm": 300_000,
                            },
                        }
                    },
                )
            )
    message = str(caught.value)
    # The bare crash text on the 2.1 line is exactly "Error executing tool preview_route", so
    # naming the offending field is what proves the reason survived rather than merely the fact.
    assert "clearance_nm" in message, message
    assert message != "Error executing tool preview_route"


def test_a_workspace_refusal_names_no_path_it_was_given(tmp_path: Any) -> None:
    """The translated message is a reason, never an echo (`SEC-163`)."""

    secret = "proprietary-customer-board-name"
    settings = Settings(workspace=tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_SETTINGS", settings)
        with pytest.raises(ToolError) as caught:
            asyncio.run(
                mcp_server.mcp.call_tool("inspect_board", {"path": f"../{secret}.kicad_pcb"})
            )
    message = str(caught.value)
    assert secret not in message
    assert str(tmp_path) not in message


@pytest.mark.skipif(UnexpectedToolError is None, reason="`mcp` 2.0.x has no crash classification")
def test_the_stricter_line_separates_a_refusal_from_a_crash(tmp_path: Any) -> None:
    """The distinction `R-176` said no artifact could supply, made directly.

    A refusal keeps its own class and its reason; a crash is rewrapped and its text withheld.
    """

    assert UnexpectedToolError is not None
    settings = Settings(workspace=tmp_path)
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(mcp_server, "_SETTINGS", settings)

        with pytest.raises(ToolError) as refused:
            asyncio.run(mcp_server.mcp.call_tool("inspect_board", {"path": "../beyond.kicad_pcb"}))

        def crash() -> dict[str, Any]:
            raise RuntimeError("an internal detail that must never reach a caller")

        patch.setattr(mcp_server, "server_info_service", crash)
        with pytest.raises(ToolError) as crashed:
            asyncio.run(mcp_server.mcp.call_tool("server_info", {}))

    assert not isinstance(refused.value, UnexpectedToolError)
    assert "workspace" in str(refused.value)
    assert isinstance(crashed.value, UnexpectedToolError)
    assert "an internal detail" not in str(crashed.value)
