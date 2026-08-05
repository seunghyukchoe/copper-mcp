"""One read-only, revision-bound post-placement scene and DRC observation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from copper_mcp.circuit_scene import (
    CircuitScene,
    CircuitSceneError,
    _observe_board_scene,
    parse_circuit_scene_request,
)
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    _context_revision,
    _drc_context,
    _revision,
    _run_captured_drc,
)
from copper_mcp.models import SCHEMA_VERSION, DrcSummary
from copper_mcp.security import read_workspace_file

_SHA256 = re.compile(r"^sha256:[0-9a-f]{64}$")
_SCENE_FIELDS = frozenset(
    {"board", "constraints", "region", "layers", "include_annotations", "include_render"}
)


class PostPlacementObservationError(ValueError):
    """A fixed, non-echoing refusal for post-placement evidence."""


@dataclass(frozen=True, slots=True)
class PostPlacementObservation:
    """DRC and semantic scene evidence derived from one captured board/context state."""

    board_path: str
    board_revision: str
    snapshot_digest: str
    scene: CircuitScene
    drc_summary: DrcSummary
    schema_version: str = SCHEMA_VERSION
    observation_version: str = "0.1.0"

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "observation_version": self.observation_version,
            "board_path": self.board_path,
            "board_revision": self.board_revision,
            "snapshot_digest": self.snapshot_digest,
            "scene": self.scene.to_dict(),
            "drc_summary": self.drc_summary.to_dict(),
        }


def _request(payload: Any) -> tuple[dict[str, Any], str]:
    if not isinstance(payload, Mapping):
        raise PostPlacementObservationError("post-placement observation request is malformed")
    fields = dict(payload)
    allowed = _SCENE_FIELDS | {"expect_board_revision"}
    if set(fields) - allowed or not {
        "board",
        "constraints",
        "region",
        "expect_board_revision",
    } <= set(fields):
        raise PostPlacementObservationError("post-placement observation request is malformed")
    expected = fields.pop("expect_board_revision")
    if not isinstance(expected, str) or _SHA256.fullmatch(expected) is None:
        raise PostPlacementObservationError("post-placement expected board revision is malformed")
    try:
        validated = parse_circuit_scene_request(fields)
    except CircuitSceneError as error:
        raise PostPlacementObservationError(
            "post-placement observation request is malformed"
        ) from error
    # A render independently opens the workspace and would no longer be bound to this capture.
    if validated.include_render:
        raise PostPlacementObservationError("post-placement observation does not render boards")
    return validated.to_dict(), expected


def observe_post_placement(payload: Any, settings: Settings) -> PostPlacementObservation:
    """Observe one exact workspace revision through Circuit Scene and private KiCad DRC.

    Every public result is built from the single byte/context capture.  A changed board, project,
    rules, or local library at either boundary discards the whole result rather than returning a
    scene and DRC that might describe different states.
    """

    if not isinstance(settings, Settings):
        raise PostPlacementObservationError("post-placement observation settings are malformed")
    scene_payload, expected_revision = _request(payload)
    try:
        board = read_workspace_file(
            settings.workspace,
            scene_payload["board"],
            allowed_suffixes={".kicad_pcb"},
            max_bytes=settings.max_board_bytes,
        )
        board_path = board.path
        relative_path = board_path.relative_to(settings.workspace.resolve(strict=True)).as_posix()
        # The board bytes alone are sufficient to reject a stale request. Do this before reading
        # project/rule/library sidecars for the DRC context, which keeps stale work proportional to
        # the one descriptor-confined board capture rather than the whole project.
        board_revision = _revision(board.content)
        if board_revision != expected_revision:
            raise PostPlacementObservationError("post-placement board revision is stale")
        context = _drc_context(board_path, settings, board)
        source = context[relative_path]
        if _revision(source) != board_revision:
            raise PostPlacementObservationError("post-placement board changed during observation")
        context_revision = _context_revision(context)
        scene = _observe_board_scene(
            scene_payload,
            settings,
            source=source,
            board_path_override=relative_path,
        )
        if not scene.supported or scene.snapshot_digest is None:
            raise PostPlacementObservationError(
                "post-placement board is outside the supported subset"
            )
        if scene.board_revision != board_revision:
            raise PostPlacementObservationError("post-placement scene revision is inconsistent")
        summary = _run_captured_drc(context, board_relative=relative_path, settings=settings)
        if (
            summary.base_revision != board_revision
            or summary.drc_context_revision != context_revision
        ):
            raise PostPlacementObservationError("post-placement DRC evidence is inconsistent")
        if _context_revision(_drc_context(board_path, settings)) != context_revision:
            raise PostPlacementObservationError(
                "post-placement board context changed during observation"
            )
    except PostPlacementObservationError:
        raise
    except (CircuitSceneError, KiCadCliError, OSError, KeyError) as error:
        raise PostPlacementObservationError(
            "post-placement authoritative evidence is unavailable"
        ) from error
    return PostPlacementObservation(
        board_path=relative_path,
        board_revision=board_revision,
        snapshot_digest=scene.snapshot_digest,
        scene=scene,
        drc_summary=summary,
    )


__all__ = ["PostPlacementObservation", "PostPlacementObservationError", "observe_post_placement"]
