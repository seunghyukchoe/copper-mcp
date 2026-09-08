"""Private v2 promotion of exactly the five native-default suppressed DRC checks."""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import PurePosixPath
from typing import Annotated, Any, Literal, cast

from pydantic import Field, StringConstraints, model_validator

from copper_mcp.config import Settings
from copper_mcp.engineering.project_settings import parse_project_document
from copper_mcp.kicad_drc_rule_liveness import admit_rule_liveness_context
from copper_mcp.optimization.contracts import ClosedModel, Digest, OptimizationError

DEFAULT_IGNORED_CHECKS = (
    "footprint_filters_mismatch",
    "footprint_type_mismatch",
    "missing_courtyard",
    "track_not_centered_on_via",
    "tuning_profile_track_geometries",
)
_CheckName = Annotated[str, StringConstraints(min_length=1, max_length=256)]
_Severity = Literal["warning", "error"]


class DrcProfileBinding(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v2/drc-profile"
    method: Literal["complete-native-default-drc/v1"] = "complete-native-default-drc/v1"
    original_context_digest: Digest
    effective_context_digest: Digest
    original_project_digest: Digest | None
    effective_project_digest: Digest
    promoted_checks: Annotated[tuple[_CheckName, ...], Field(max_length=5)]
    enabled_inventory: Annotated[
        tuple[tuple[_CheckName, _Severity], ...], Field(min_length=5, max_length=4096)
    ]

    @model_validator(mode="after")
    def fixed_profile(self) -> DrcProfileBinding:
        names = tuple(key for key, _ in self.enabled_inventory)
        if tuple(sorted(set(names))) != names or not set(DEFAULT_IGNORED_CHECKS).issubset(names):
            raise ValueError("DRC profile inventory is inconsistent")
        if tuple(sorted(set(self.promoted_checks))) != self.promoted_checks or not set(
            self.promoted_checks
        ).issubset(DEFAULT_IGNORED_CHECKS):
            raise ValueError("DRC profile promotions are inconsistent")
        return self


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise OptimizationError("optimization DRC profile deadline expired")


def _sha(payload: bytes, deadline: float) -> str:
    result = hashlib.sha256()
    for offset in range(0, len(payload), 65536):
        _check(deadline)
        result.update(memoryview(payload)[offset : offset + 65536])
    _check(deadline)
    return "sha256:" + result.hexdigest()


def _context_digest(context: dict[str, bytes], deadline: float) -> str:
    result = hashlib.sha256()
    for name, payload in sorted(context.items()):
        _check(deadline)
        result.update(name.encode("utf-8"))
        result.update(b"\0")
        result.update(len(payload).to_bytes(8, "big"))
        for offset in range(0, len(payload), 65536):
            _check(deadline)
            result.update(memoryview(payload)[offset : offset + 65536])
    _check(deadline)
    return "sha256:" + result.hexdigest()


def prepare_drc_profile(
    context: dict[str, bytes], board_relative: str, settings: Settings, deadline: float
) -> tuple[dict[str, bytes], DrcProfileBinding]:
    """Preserve source bytes; add or replace only the private project companion."""

    admit_rule_liveness_context(
        context,
        board_relative,
        max_file_bytes=settings.max_board_bytes,
        max_context_files=settings.max_drc_context_files,
        max_context_bytes=settings.max_drc_context_bytes,
        deadline=deadline,
    )
    project_path = PurePosixPath(board_relative).with_suffix(".kicad_pro").as_posix()
    original = context.get(project_path)
    if original is None and len(context) >= settings.max_drc_context_files:
        raise OptimizationError("optimization DRC profile exceeds its context budget")
    project: dict[str, Any] = (
        parse_project_document(original, deadline=deadline)
        if original is not None
        else {"meta": {"version": 1}}
    )
    board = project.setdefault("board", {})
    if type(board) is not dict:
        raise OptimizationError("optimization DRC project is malformed")
    design = board.setdefault("design_settings", {})
    if type(design) is not dict:
        raise OptimizationError("optimization DRC project is malformed")
    severities = design.setdefault("rule_severities", {})
    if type(severities) is not dict or len(severities) > 4096:
        raise OptimizationError("optimization DRC project is malformed")
    promoted = []
    for key in DEFAULT_IGNORED_CHECKS:
        _check(deadline)
        if key not in severities or severities[key] == "ignore":
            severities[key] = "warning"
            promoted.append(key)
        elif type(severities[key]) is not str or severities[key] not in {"warning", "error"}:
            raise OptimizationError("optimization DRC project is malformed")
    inventory = []
    for key, value in severities.items():
        _check(deadline)
        if type(value) is str and value in {"warning", "error"}:
            if len(key) > 256:
                raise OptimizationError("optimization DRC inventory exceeds its bounds")
            inventory.append((key, cast(_Severity, value)))
    remaining = min(
        settings.max_board_bytes,
        settings.max_drc_context_bytes
        - sum(len(value) for key, value in context.items() if key != project_path),
    )
    chunks = []
    size = 0
    for token in json.JSONEncoder(
        sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
    ).iterencode(project):
        _check(deadline)
        size += len(token)
        if size > remaining:
            raise OptimizationError("optimization DRC profile exceeds its context budget")
        chunks.append(token)
    payload = "".join(chunks).encode("ascii")
    effective = {**context, project_path: payload}
    admit_rule_liveness_context(
        effective,
        board_relative,
        max_file_bytes=settings.max_board_bytes,
        max_context_files=settings.max_drc_context_files,
        max_context_bytes=settings.max_drc_context_bytes,
        deadline=deadline,
    )
    profile = DrcProfileBinding(
        original_context_digest=_context_digest(context, deadline),
        effective_context_digest=_context_digest(effective, deadline),
        original_project_digest=None if original is None else _sha(original, deadline),
        effective_project_digest=_sha(payload, deadline),
        promoted_checks=tuple(promoted),
        enabled_inventory=tuple(sorted(inventory)),
    )
    _check(deadline)
    return effective, profile
