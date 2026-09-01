"""Read-only capability oracle for one live KiCad IPC snapshot.

The normal live adapters intentionally expose only the operation they need.  This diagnostic
joins the existing redacted capture, Board IR conversion, and Circuit Scene projection so an
operator can establish whether one running editor can support the full observation path.  It is
not an MCP tool, never exposes source bytes, and never invokes any KiCad mutator.
"""

from __future__ import annotations

import hashlib
import os
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Literal

from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.circuit_scene import _observe_board_scene, parse_circuit_scene_request
from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.kicad_ipc import (
    ACCEPTED_API_COMPATIBILITY,
    KicadIpcConfigurationError,
    KicadIpcConnectionError,
    KicadIpcDeadlineError,
    KicadIpcError,
    KicadIpcPayloadError,
    KicadIpcUnavailableError,
    KicadIpcVersionError,
    capture_live_board,
)
from copper_mcp.parse_budgets import parse_limits_for

LIVE_IPC_ORACLE_SCHEMA_VERSION = "0.1.0"

# A fixed, intentionally broad region and conservative ordinary-net constraints make the scene
# digest reproducible.  These are diagnostic conversion inputs, not KiCad design-rule authority.
_SCENE_PAYLOAD: dict[str, Any] = {
    "board": "live",
    "constraints": {
        "clearance_nm": 200_000,
        "track_width_nm": 250_000,
        "via_diameter_nm": 600_000,
        "via_drill_nm": 300_000,
    },
    "region": {
        "min_x_nm": -1_000_000_000,
        "min_y_nm": -1_000_000_000,
        "max_x_nm": 1_000_000_000,
        "max_y_nm": 1_000_000_000,
    },
}

OracleStatus = Literal["ready", "skipped", "refused"]


@dataclass(frozen=True, slots=True)
class LiveIpcOracleResult:
    """Redacted result of a bounded, non-mutating live-editor capability probe."""

    status: OracleStatus
    capability: str
    socket_configured: bool
    token_configured: bool
    board_digest: str | None = None
    board_ir_snapshot_digest: str | None = None
    scene_snapshot_digest: str | None = None
    exact_source_digest: str | None = None
    digest_matches: Mapping[str, bool] | None = None
    compatibility: str | None = None
    read_only: bool = True
    schema_version: str = LIVE_IPC_ORACLE_SCHEMA_VERSION

    def __post_init__(self) -> None:
        if self.status not in {"ready", "skipped", "refused"}:
            raise ValueError("live IPC oracle status is invalid")
        if not self.capability or len(self.capability) > 96:
            raise ValueError("live IPC oracle capability is invalid")
        if not self.read_only or self.schema_version != LIVE_IPC_ORACLE_SCHEMA_VERSION:
            raise ValueError("live IPC oracle must remain read-only")
        # The oracle republishes the observation's verdict, so it must be one the observation
        # boundary could actually have produced. Without this, a widened verdict vocabulary
        # would reach callers through the oracle without passing any acceptance check.
        if self.compatibility is not None and self.compatibility not in ACCEPTED_API_COMPATIBILITY:
            raise ValueError("live IPC oracle compatibility is invalid")
        digests = (
            self.board_digest,
            self.board_ir_snapshot_digest,
            self.scene_snapshot_digest,
            self.exact_source_digest,
        )
        if any(
            value is not None and (not value.startswith("sha256:") or len(value) != 71)
            for value in digests
        ):
            raise ValueError("live IPC oracle digest is invalid")
        if self.status == "ready":
            if any(value is None for value in digests) or self.digest_matches is None:
                raise ValueError("ready live IPC oracle result is incomplete")
            if set(self.digest_matches) != {
                "source_matches_observation",
                "board_ir_source_matches_observation",
                "scene_source_matches_observation",
                "scene_snapshot_matches_board_ir",
            } or not all(type(value) is bool for value in self.digest_matches.values()):
                raise ValueError("live IPC oracle digest evidence is invalid")
        elif any(value is not None for value in digests) or self.digest_matches is not None:
            raise ValueError("non-ready live IPC oracle result must not include partial evidence")

    def to_dict(self) -> dict[str, Any]:
        """Return a detached record that deliberately omits source, socket, and token values."""

        return {
            "schema_version": self.schema_version,
            "status": self.status,
            "capability": self.capability,
            "read_only": self.read_only,
            "socket_configured": self.socket_configured,
            "token_configured": self.token_configured,
            "compatibility": self.compatibility,
            "board_digest": self.board_digest,
            "exact_source_digest": self.exact_source_digest,
            "board_ir_snapshot_digest": self.board_ir_snapshot_digest,
            "scene_snapshot_digest": self.scene_snapshot_digest,
            "digest_matches": None if self.digest_matches is None else dict(self.digest_matches),
        }


def _environment_capability() -> tuple[bool, bool, str | None]:
    """Identify missing KiCad plugin credentials without attempting a default connection."""

    socket_configured = bool(os.environ.get("KICAD_API_SOCKET", "").strip())
    token_configured = bool(os.environ.get("KICAD_API_TOKEN", ""))
    if not socket_configured and not token_configured:
        # KiCad documents that standalone clients do not receive either variable, so this must
        # not be reported as proof that the editor's API server was disabled.
        return socket_configured, token_configured, "kicad_plugin_environment_absent"
    if not socket_configured:
        return socket_configured, token_configured, "kicad_api_socket_missing"
    if not token_configured:
        return socket_configured, token_configured, "kicad_api_token_missing"
    return socket_configured, token_configured, None


def _cause_class_names(error: BaseException) -> frozenset[str]:
    """Classify public exception types only; never examine potentially secret error text."""

    result: set[str] = set()
    seen: set[int] = set()
    current: BaseException | None = error
    for _ in range(8):
        if current is None or id(current) in seen:
            break
        seen.add(id(current))
        result.add(type(current).__name__.lower())
        next_error = current.__cause__ or current.__context__
        if next_error is current:
            break
        current = next_error
    return frozenset(result)


def _connection_capability(error: KicadIpcConnectionError) -> str:
    """Map known typed/session failures to a stable redacted capability code."""

    # These two strings are CopperMCP's own fixed messages, not any text from kicad-python.
    if str(error) == "KiCad IPC session changed during observation":
        return "kicad_session_changed"
    if str(error) == "KiCad board changed during observation":
        return "kicad_board_changed_during_capture"
    class_names = _cause_class_names(error)
    if any(
        marker in class_name
        for class_name in class_names
        for marker in ("authentication", "unauthenticated", "permissiondenied", "token")
    ):
        return "kicad_token_or_session_rejected"
    if any(
        marker in class_name
        for class_name in class_names
        for marker in ("sessionexpired", "sessionclosed")
    ):
        return "kicad_session_rejected"
    # KiCad's API is synchronous; this includes an API server that is disabled, a stale socket,
    # and an editor that is busy.  A read-only client cannot distinguish those safely.
    return "kicad_api_server_unreachable_or_busy"


def _configuration_capability(error: KicadIpcConfigurationError) -> str:
    """Map CopperMCP's own fixed configuration errors without exposing environment text."""

    # ``kicad_ipc`` constructs these messages itself; no binding/server exception text is read.
    message = str(error)
    if message.startswith("KICAD_API_SOCKET"):
        return "kicad_endpoint_configuration_invalid"
    if message.startswith("KICAD_API_TOKEN"):
        return "kicad_token_configuration_invalid"
    if message.startswith("IPC timeout") or message.startswith("IPC deadline"):
        return "kicad_timeout_or_budget_configuration_invalid"
    return "kicad_configuration_invalid"


def _operation_deadline(timeout_ms: int) -> float | None:
    """Return the one cooperative deadline shared by capture and both conversions."""

    if type(timeout_ms) is not int or not 1 <= timeout_ms <= 10_000:
        return None
    return time.monotonic() + timeout_ms / 1_000


def _deadline_exhausted(deadline: float) -> bool:
    return time.monotonic() >= deadline


def _refused(
    capability: str,
    socket_configured: bool,
    token_configured: bool,
) -> LiveIpcOracleResult:
    return LiveIpcOracleResult(
        status="refused",
        capability=capability,
        socket_configured=socket_configured,
        token_configured=token_configured,
    )


def probe_live_kicad_ipc(
    settings: Settings | None = None,
    *,
    client_factory: Callable[..., Any] | None = None,
    timeout_ms: int = 2_000,
) -> LiveIpcOracleResult:
    """Prove, or safely decline to claim, exact live source → Board IR → scene fidelity.

    The function is useful in a KiCad-launched plugin action because that process has the
    per-instance credentials.  CI and ordinary shells produce deterministic ``skipped`` results
    when those credentials are absent.  It does not write KiCad, run DRC, refill zones, create
    candidates, or retain/return serialized board text.
    """

    socket_configured, token_configured, environment_problem = _environment_capability()
    if environment_problem is not None:
        return LiveIpcOracleResult(
            status="skipped",
            capability=environment_problem,
            socket_configured=socket_configured,
            token_configured=token_configured,
        )
    deadline = _operation_deadline(timeout_ms)
    if deadline is None:
        return _refused(
            "kicad_timeout_or_budget_configuration_invalid",
            socket_configured,
            token_configured,
        )
    try:
        active_settings = settings or Settings.from_env()
    except ConfigurationError:
        return _refused(
            "coppermcp_settings_configuration_invalid",
            socket_configured,
            token_configured,
        )
    if _deadline_exhausted(deadline):
        return _refused("live_ipc_oracle_deadline_exhausted", socket_configured, token_configured)
    try:
        captured = capture_live_board(
            active_settings,
            client_factory=client_factory,
            timeout_ms=timeout_ms,
            deadline=deadline,
        )
    except KicadIpcUnavailableError:
        return _refused("kicad_python_binding_unavailable", socket_configured, token_configured)
    except KicadIpcConfigurationError as error:
        return _refused(
            _configuration_capability(error),
            socket_configured,
            token_configured,
        )
    except KicadIpcVersionError:
        return _refused("kicad_version_mismatch", socket_configured, token_configured)
    except KicadIpcPayloadError:
        return _refused("kicad_snapshot_refused_by_budget", socket_configured, token_configured)
    except KicadIpcDeadlineError:
        return _refused("live_ipc_oracle_deadline_exhausted", socket_configured, token_configured)
    except KicadIpcConnectionError as error:
        return _refused(_connection_capability(error), socket_configured, token_configured)
    except KicadIpcError:
        return _refused("kicad_ipc_observation_refused", socket_configured, token_configured)

    if _deadline_exhausted(deadline):
        return _refused("live_ipc_oracle_deadline_exhausted", socket_configured, token_configured)
    request = parse_circuit_scene_request(_SCENE_PAYLOAD)
    limits = parse_limits_for(active_settings)
    conversion = parse_kicad_bytes(captured.source, request.profile(), limits)
    if conversion.snapshot is None or conversion.diagnostics:
        return _refused("board_ir_conversion_unsupported", socket_configured, token_configured)
    if _deadline_exhausted(deadline):
        return _refused("live_ipc_oracle_deadline_exhausted", socket_configured, token_configured)
    scene = _observe_board_scene(
        _SCENE_PAYLOAD,
        active_settings,
        source=captured.source,
        board_path_override="live",
    )
    if not scene.supported or scene.snapshot_digest is None:
        return _refused("circuit_scene_projection_unsupported", socket_configured, token_configured)
    if _deadline_exhausted(deadline):
        return _refused("live_ipc_oracle_deadline_exhausted", socket_configured, token_configured)

    source_digest = f"sha256:{hashlib.sha256(captured.source).hexdigest()}"
    board_digest = captured.observation.board_digest
    snapshot = conversion.snapshot
    digest_matches = {
        "source_matches_observation": source_digest == board_digest,
        "board_ir_source_matches_observation": snapshot.content.source.revision == board_digest,
        "scene_source_matches_observation": scene.board_revision == board_digest,
        "scene_snapshot_matches_board_ir": scene.snapshot_digest == snapshot.snapshot_digest,
    }
    if not all(digest_matches.values()):
        return _refused("live_snapshot_digest_mismatch", socket_configured, token_configured)
    return LiveIpcOracleResult(
        status="ready",
        capability="live_source_board_ir_scene_fidelity_confirmed",
        socket_configured=socket_configured,
        token_configured=token_configured,
        board_digest=board_digest,
        exact_source_digest=source_digest,
        board_ir_snapshot_digest=snapshot.snapshot_digest,
        scene_snapshot_digest=scene.snapshot_digest,
        digest_matches=digest_matches,
        compatibility=captured.observation.compatibility,
    )


__all__ = ["LIVE_IPC_ORACLE_SCHEMA_VERSION", "LiveIpcOracleResult", "probe_live_kicad_ipc"]
