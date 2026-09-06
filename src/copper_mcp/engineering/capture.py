"""Private electrical artifact capture; byte verification is not engineering authority."""

from __future__ import annotations

import hashlib
import math
import time
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import Field, StringConstraints, TypeAdapter, ValidationError, model_validator

from copper_mcp.engineering.inputs import Identifier, parse_electrical_inputs
from copper_mcp.optimization.contracts import (
    ClosedModel,
    Digest,
    OptimizationError,
    bounded_json,
    digest_document,
)
from copper_mcp.security import WorkspaceViolationError, read_workspace_file

_DEFAULT_FILE_BYTES = 8 * 1024 * 1024
_DEFAULT_TOTAL_BYTES = 32 * 1024 * 1024
_DEFAULT_SECONDS = 5
_MAX_FILE_BYTES = 32 * 1024 * 1024
_MAX_TOTAL_BYTES = 128 * 1024 * 1024
_MAX_SECONDS = 30
_SUFFIXES: dict[str, frozenset[str]] = {
    "bom": frozenset({".csv", ".json"}),
    "schematic": frozenset({".kicad_sch"}),
    "netlist": frozenset({".xml", ".net"}),
    "model-library": frozenset({".lib", ".mod", ".cir", ".sp", ".spice", ".ibs", ".json"}),
}


class ElectricalCaptureError(OptimizationError):
    """A fixed, redacted refusal from the electrical artifact capture boundary."""


@dataclass(frozen=True, slots=True)
class CaptureLimits:
    """Operator-owned ceilings; they may relax defaults only within ADR-0136 caps."""

    max_file_bytes: int = _DEFAULT_FILE_BYTES
    max_total_bytes: int = _DEFAULT_TOTAL_BYTES
    max_capture_seconds: int = _DEFAULT_SECONDS

    def __post_init__(self) -> None:
        values = (self.max_file_bytes, self.max_total_bytes, self.max_capture_seconds)
        if any(type(value) is not int for value in values) or not (
            1 <= self.max_file_bytes <= _MAX_FILE_BYTES
            and 1 <= self.max_total_bytes <= _MAX_TOTAL_BYTES
            and 1 <= self.max_capture_seconds <= _MAX_SECONDS
        ):
            raise ElectricalCaptureError("electrical artifact capture limits are malformed")


class _ArtifactPath(ClosedModel):
    artifact_id: Identifier
    path: Annotated[str, StringConstraints(max_length=4096)]


class _ArtifactPaths(ClosedModel):
    schema_version: Literal["electrical-artifact-paths/v1"]
    artifacts: Annotated[tuple[_ArtifactPath, ...], Field(min_length=1, max_length=16)]

    @model_validator(mode="after")
    def canonical_bindings(self) -> _ArtifactPaths:
        if len({item.artifact_id for item in self.artifacts}) != len(self.artifacts):
            raise ValueError("artifact bindings must be unique")
        return self


class PublicElectricalCaptureProjection(ClosedModel):
    declaration_digest: Digest
    capture_digest: Digest
    artifact_count: Annotated[int, Field(ge=0, le=16)]
    total_bytes: Annotated[int, Field(ge=0, le=_MAX_TOTAL_BYTES)]
    binding_scope: Literal["declared_artifact_bytes_only"] = "declared_artifact_bytes_only"
    project_capture_complete: Literal[False] = False
    semantic_validation: Literal["not_run"] = "not_run"
    model_execution: Literal["not_run"] = "not_run"
    apply_authority: Literal["none"] = "none"


@dataclass(frozen=True, slots=True, repr=False)
class _CapturedArtifact:
    artifact_id: str
    role: str
    digest: str
    content: bytes

    def __repr__(self) -> str:
        return "<CapturedElectricalArtifact redacted>"


@dataclass(frozen=True, slots=True, repr=False)
class ElectricalArtifactCapture:
    """Frozen private payloads plus the sole safe-to-disclose projection."""

    _artifacts: tuple[_CapturedArtifact, ...] = field(repr=False)
    declaration_digest: str
    digest: str
    redacted_projection: PublicElectricalCaptureProjection

    def __repr__(self) -> str:
        return "<ElectricalArtifactCapture redacted>"


def _parse_paths(payload: bytes) -> _ArtifactPaths:
    try:
        bounded_json(payload)
        return TypeAdapter(_ArtifactPaths).validate_json(payload)
    except (OptimizationError, ValidationError, ValueError, TypeError, RecursionError):
        raise ElectricalCaptureError("electrical artifact paths are malformed") from None


def _canonical_path(path: str) -> str:
    if (
        not path
        or path.startswith("~")
        or "\\" in path
        or any(unicodedata.category(character) in {"Cc", "Cs"} for character in path)
    ):
        raise ElectricalCaptureError("electrical artifact paths are malformed")
    parsed = PurePosixPath(path)
    if parsed.is_absolute() or any(part in {"", ".", ".."} for part in parsed.parts):
        raise ElectricalCaptureError("electrical artifact paths are malformed")
    canonical = parsed.as_posix()
    if canonical != path:
        raise ElectricalCaptureError("electrical artifact paths are malformed")
    return canonical


def _deadline(started_at: float, limits: CaptureLimits, caller_deadline: float | None) -> float:
    configured_deadline = started_at + limits.max_capture_seconds
    if caller_deadline is None:
        return configured_deadline
    if type(caller_deadline) not in (int, float):
        raise ElectricalCaptureError("electrical artifact capture deadline is malformed")
    try:
        caller_value = float(caller_deadline)
    except OverflowError:
        raise ElectricalCaptureError("electrical artifact capture deadline is malformed") from None
    if not math.isfinite(caller_value):
        raise ElectricalCaptureError("electrical artifact capture deadline is malformed")
    return min(configured_deadline, caller_value)


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ElectricalCaptureError("electrical artifact capture deadline expired")


def _read(workspace: Path, path: str, role: str, max_bytes: int, deadline: float) -> bytes:
    _check_deadline(deadline)
    try:
        payload = read_workspace_file(
            workspace, path, allowed_suffixes=_SUFFIXES[role], max_bytes=max_bytes
        ).content
    except (WorkspaceViolationError, OSError, ValueError):
        raise ElectricalCaptureError("electrical artifact capture refused") from None
    _check_deadline(deadline)
    return payload


def _sha256(payload: bytes, deadline: float) -> str:
    _check_deadline(deadline)
    digest = "sha256:" + hashlib.sha256(payload).hexdigest()
    _check_deadline(deadline)
    return digest


def capture_electrical_artifacts(
    declaration_json: bytes,
    paths_json: bytes,
    workspace: Path,
    *,
    limits: CaptureLimits | None = None,
    deadline: float | None = None,
) -> ElectricalArtifactCapture:
    """Capture declared bytes twice without interpreting them or granting engineering authority."""

    started_at = time.monotonic()
    if limits is None:
        limits = CaptureLimits()
    if type(limits) is not CaptureLimits:
        raise ElectricalCaptureError("electrical artifact capture limits are malformed")
    try:
        # Freeze validated scalar values for this call, not a caller-owned mutable reference.
        limits = CaptureLimits(
            limits.max_file_bytes, limits.max_total_bytes, limits.max_capture_seconds
        )
    except AttributeError:
        raise ElectricalCaptureError("electrical artifact capture limits are malformed") from None
    capture_deadline = _deadline(started_at, limits, deadline)
    _check_deadline(capture_deadline)
    try:
        declaration = parse_electrical_inputs(declaration_json)
    except OptimizationError:
        raise ElectricalCaptureError("electrical artifact declaration is malformed") from None
    paths = _parse_paths(paths_json)
    declared = {artifact.artifact_id: artifact for artifact in declaration.source_artifacts}
    bindings = {binding.artifact_id: _canonical_path(binding.path) for binding in paths.artifacts}
    portable_paths = {unicodedata.normalize("NFC", path.casefold()) for path in bindings.values()}
    if set(bindings) != set(declared) or len(portable_paths) != len(bindings):
        raise ElectricalCaptureError("electrical artifact bindings are malformed")

    captured: list[_CapturedArtifact] = []
    total_bytes = 0
    for artifact in declaration.source_artifacts:
        path = bindings[artifact.artifact_id]
        remaining = limits.max_total_bytes - total_bytes
        if remaining < 1:
            raise ElectricalCaptureError("electrical artifact capture refused")
        first = _read(
            workspace,
            path,
            artifact.role,
            min(limits.max_file_bytes, remaining),
            capture_deadline,
        )
        if not first:
            raise ElectricalCaptureError("electrical artifact capture refused")
        total_bytes += len(first)
        if total_bytes > limits.max_total_bytes:
            raise ElectricalCaptureError("electrical artifact capture refused")
        first_digest = _sha256(first, capture_deadline)
        if first_digest != artifact.artifact_digest:
            raise ElectricalCaptureError("electrical artifact digest does not match declaration")
        captured.append(_CapturedArtifact(artifact.artifact_id, artifact.role, first_digest, first))

    for captured_artifact in captured:
        second = _read(
            workspace,
            bindings[captured_artifact.artifact_id],
            captured_artifact.role,
            len(captured_artifact.content),
            capture_deadline,
        )
        if second != captured_artifact.content:
            raise ElectricalCaptureError("electrical artifact changed during capture")
        _sha256(second, capture_deadline)

    _check_deadline(capture_deadline)
    declaration_digest = declaration.digest
    capture_digest = digest_document(
        "copper-mcp/electrical-artifact-capture/v1",
        {
            "declaration_digest": declaration_digest,
            "artifacts": [
                {
                    "artifact_id": item.artifact_id,
                    "role": item.role,
                    "digest": item.digest,
                    "size": len(item.content),
                }
                for item in captured
            ],
        },
    )
    projection = PublicElectricalCaptureProjection(
        declaration_digest=declaration_digest,
        capture_digest=capture_digest,
        artifact_count=len(captured),
        total_bytes=total_bytes,
    )
    _check_deadline(capture_deadline)
    return ElectricalArtifactCapture(
        tuple(captured), declaration_digest, capture_digest, projection
    )
