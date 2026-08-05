"""Safe primitives for a future MCP Tasks adapter.

This module deliberately does *not* register ``io.modelcontextprotocol/tasks``.
The current CopperMCP authorization boundary requires a caller context for every
lookup, whereas the draft ``tasks/get`` and ``tasks/cancel`` requests carry
only a task ID.  The broker keeps the missing boundary explicit and gives a
future, session-authenticated adapter one small component to reuse.
"""

from __future__ import annotations

import hmac
import importlib
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError, version
from typing import Final, TypeVar

TASKS_EXTENSION_IDENTIFIER: Final = "io.modelcontextprotocol/tasks"
DEFAULT_TASK_HANDLE_RETENTION_SECONDS: Final = 900
MAX_TASK_HANDLE_RETENTION_SECONDS: Final = 86_400
DEFAULT_MAX_TASK_HANDLES: Final = 1_024
_TASK_ID_RE: Final = re.compile(r"^[A-Za-z0-9_-]{43}$")
_SHA256_RE: Final = re.compile(r"^sha256:[0-9a-f]{64}$")
_Result = TypeVar("_Result")


class TaskHandleUnavailableError(ValueError):
    """A non-echoing refusal for absent, expired, or unauthorized task handles."""


@dataclass(frozen=True, slots=True)
class RoutingTaskHandle:
    """An opaque task handle bound to one durable routing job and caller context."""

    task_id: str
    job_id: str
    expires_at_monotonic: float


@dataclass(frozen=True, slots=True)
class MCPTasksCompatibility:
    """Pinned runtime facts needed before advertising the draft Tasks extension."""

    mcp_version: str | None
    has_extension_api: bool
    has_task_wire_types: bool
    has_task_dispatcher: bool
    has_owner_bound_task_lookup: bool
    has_durable_task_handle_store: bool

    @property
    def supports_safe_wire_contract(self) -> bool:
        """Whether every required runtime and CopperMCP security seam is present."""

        return (
            self.has_extension_api
            and self.has_task_wire_types
            and self.has_task_dispatcher
            and self.has_owner_bound_task_lookup
            and self.has_durable_task_handle_store
        )

    @property
    def gaps(self) -> tuple[str, ...]:
        """Stable, non-sensitive reasons that prevent Tasks advertisement."""

        gaps: list[str] = []
        if not self.has_extension_api:
            gaps.append("extension_api_unavailable")
        if not self.has_task_wire_types:
            gaps.append("task_wire_types_unavailable")
        if not self.has_task_dispatcher:
            gaps.append("task_dispatcher_unavailable")
        if not self.has_owner_bound_task_lookup:
            gaps.append("owner_bound_lookup_unavailable")
        if not self.has_durable_task_handle_store:
            gaps.append("durable_task_handle_store_unavailable")
        return tuple(gaps)


def assess_mcp_tasks_compatibility(
    *,
    mcp_version: str | None,
    has_extension_api: bool,
    has_task_wire_types: bool,
    has_task_dispatcher: bool,
    has_owner_bound_task_lookup: bool,
    has_durable_task_handle_store: bool,
) -> MCPTasksCompatibility:
    """Build a compatibility result from explicit, independently testable facts."""

    return MCPTasksCompatibility(
        mcp_version=mcp_version,
        has_extension_api=has_extension_api,
        has_task_wire_types=has_task_wire_types,
        has_task_dispatcher=has_task_dispatcher,
        has_owner_bound_task_lookup=has_owner_bound_task_lookup,
        has_durable_task_handle_store=has_durable_task_handle_store,
    )


def probe_installed_mcp_tasks_runtime() -> MCPTasksCompatibility:
    """Inspect the installed SDK without claiming support from generic hooks alone.

    The probe checks only public importable symbols.  A generic extension API is
    insufficient: a Tasks server also needs the draft request/result wire types,
    a dispatcher that validates their negotiated lifecycle, durable task state,
    and an authenticated caller identity for every deferred-result lookup.
    """

    try:
        installed_version = version("mcp")
    except PackageNotFoundError:
        installed_version = None

    extension_api = _has_symbols(
        "mcp.server.extension", ("Extension", "MethodBinding")
    ) and _has_symbols("mcp.server.mcpserver.server", ("require_client_extension",))
    task_wire_types = _has_current_task_wire_types()
    task_dispatcher = _has_symbols("mcp.server.tasks", ("Tasks",))

    # CopperMCP currently has neither a session-authenticated principal exposed
    # to task handlers nor permitted durable storage for a distinct task handle.
    # Do not infer either guarantee merely from a client-declared capability.
    return assess_mcp_tasks_compatibility(
        mcp_version=installed_version,
        has_extension_api=extension_api,
        has_task_wire_types=task_wire_types,
        has_task_dispatcher=task_dispatcher,
        has_owner_bound_task_lookup=False,
        has_durable_task_handle_store=False,
    )


def _has_symbols(module_name: str, names: tuple[str, ...]) -> bool:
    try:
        module = importlib.import_module(module_name)
    except ImportError:
        return False
    return all(hasattr(module, name) for name in names)


def _has_current_task_wire_types() -> bool:
    """Distinguish current draft shapes from the SDK's legacy nested-task types."""

    try:
        types_module = importlib.import_module("mcp.types")
        task_symbols = (
            "CreateTaskResult",
            "GetTaskRequestParams",
            "CancelTaskRequestParams",
        )
        create_task_result, get_task_params, cancel_task_params = (
            getattr(types_module, name) for name in task_symbols
        )
        create_fields = set(create_task_result.model_fields)
        get_fields = set(get_task_params.model_fields)
        cancel_fields = set(cancel_task_params.model_fields)
    except (AttributeError, ImportError):
        return False
    # The current draft's CreateTaskResult is a Task itself: its fields are
    # direct (taskId/status/timestamps), rather than the 2025-11-25 SDK's
    # incompatible ``{task: ...}`` wrapper.
    return (
        {"task_id", "status", "created_at", "last_updated_at", "ttl_ms"} <= create_fields
        and "task_id" in get_fields
        and "task_id" in cancel_fields
    )


class RoutingTaskHandleBroker:
    """In-memory, bounded map from unguessable handles to owner-bound jobs.

    It is intentionally not a draft Tasks implementation: memory-only retention
    cannot satisfy the specification's durable ``tasks/get`` guarantee after a
    process restart.  The broker nevertheless prevents a future adapter from
    accidentally turning deterministic routing job IDs into bearer handles.
    """

    def __init__(
        self,
        *,
        retention_seconds: int = DEFAULT_TASK_HANDLE_RETENTION_SECONDS,
        max_handles: int = DEFAULT_MAX_TASK_HANDLES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if not 1 <= retention_seconds <= MAX_TASK_HANDLE_RETENTION_SECONDS:
            raise ValueError("task handle retention is outside the supported bound")
        if max_handles < 1:
            raise ValueError("task handle capacity must be positive")
        self._retention_seconds = retention_seconds
        self._max_handles = max_handles
        self._clock = clock
        self._entries: dict[str, tuple[RoutingTaskHandle, str]] = {}
        self._lock = threading.RLock()

    @property
    def retention_seconds(self) -> int:
        """Return the fixed maximum lifetime for handles minted by this broker."""

        return self._retention_seconds

    def mint(self, *, job_id: str, authorization_digest: str) -> RoutingTaskHandle:
        """Mint a fresh cryptographic handle after the durable job already exists."""

        self._validate_binding(job_id, authorization_digest)
        with self._lock:
            now = self._clock()
            self._purge_locked(now)
            if len(self._entries) >= self._max_handles:
                raise TaskHandleUnavailableError("routing task handle is unavailable")
            task_id = secrets.token_urlsafe(32)
            # ``token_urlsafe(32)`` is currently 43 URL-safe characters. Keep a
            # defensive loop so a future Python implementation change cannot
            # weaken the advertised opaque-handle grammar.
            while task_id in self._entries or _TASK_ID_RE.fullmatch(task_id) is None:
                task_id = secrets.token_urlsafe(32)
            handle = RoutingTaskHandle(
                task_id=task_id,
                job_id=job_id,
                expires_at_monotonic=now + self._retention_seconds,
            )
            self._entries[task_id] = (handle, authorization_digest)
            return handle

    def resolve(self, *, task_id: str, authorization_digest: str) -> RoutingTaskHandle:
        """Resolve a live task only for its original caller context.

        Absent, malformed, expired, and unauthorized handles intentionally use
        one fixed error so callers cannot enumerate jobs or owners.
        """

        with self._lock:
            self._purge_locked(self._clock())
            entry = self._entries.get(task_id)
            if (
                entry is None
                or _TASK_ID_RE.fullmatch(task_id) is None
                or _SHA256_RE.fullmatch(authorization_digest) is None
                or not hmac.compare_digest(entry[1], authorization_digest)
            ):
                raise TaskHandleUnavailableError("routing task handle is unavailable")
            return entry[0]

    def cancel(
        self,
        *,
        task_id: str,
        authorization_digest: str,
        cancel_job: Callable[[str, str], _Result],
    ) -> _Result:
        """Authorize cancellation before invoking the existing durable-job operation."""

        handle = self.resolve(task_id=task_id, authorization_digest=authorization_digest)
        return cancel_job(handle.job_id, authorization_digest)

    def active_handle_count(self) -> int:
        """Return the live count for bounded-capacity tests and local diagnostics."""

        with self._lock:
            self._purge_locked(self._clock())
            return len(self._entries)

    def _purge_locked(self, now: float) -> None:
        expired = [
            task_id
            for task_id, (handle, _) in self._entries.items()
            if handle.expires_at_monotonic <= now
        ]
        for task_id in expired:
            del self._entries[task_id]

    @staticmethod
    def _validate_binding(job_id: str, authorization_digest: str) -> None:
        if (
            _SHA256_RE.fullmatch(job_id) is None
            or _SHA256_RE.fullmatch(authorization_digest) is None
        ):
            raise TaskHandleUnavailableError("routing task handle is unavailable")
