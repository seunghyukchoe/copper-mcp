from __future__ import annotations

import hashlib

import pytest

from copper_mcp.routing.task_bridge import (
    DEFAULT_TASK_HANDLE_RETENTION_SECONDS,
    MCPTasksCompatibility,
    RoutingTaskHandleBroker,
    TaskHandleUnavailableError,
    assess_mcp_tasks_compatibility,
    probe_installed_mcp_tasks_runtime,
)


def _digest(value: bytes) -> str:
    return f"sha256:{hashlib.sha256(value).hexdigest()}"


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def test_broker_mints_unguessable_handle_distinct_from_deterministic_job_id() -> None:
    broker = RoutingTaskHandleBroker()
    job_id = _digest(b"deterministic-job")
    authorization = _digest(b"owner")

    first = broker.mint(job_id=job_id, authorization_digest=authorization)
    second = broker.mint(job_id=job_id, authorization_digest=authorization)

    assert first.task_id != job_id
    assert first.task_id != second.task_id
    assert len(first.task_id) == 43
    assert broker.retention_seconds == DEFAULT_TASK_HANDLE_RETENTION_SECONDS


def test_broker_requires_original_caller_context_without_handle_or_owner_oracle() -> None:
    broker = RoutingTaskHandleBroker()
    handle = broker.mint(job_id=_digest(b"job"), authorization_digest=_digest(b"owner"))

    messages: set[str] = set()
    for task_id, authorization in (
        (handle.task_id, _digest(b"other")),
        ("x" * 43, _digest(b"owner")),
        ("not-a-task", "not-a-digest"),
    ):
        with pytest.raises(TaskHandleUnavailableError) as caught:
            broker.resolve(task_id=task_id, authorization_digest=authorization)
        messages.add(str(caught.value))

    assert messages == {"routing task handle is unavailable"}
    assert broker.resolve(task_id=handle.task_id, authorization_digest=_digest(b"owner")) == handle


def test_broker_purges_expired_handles_before_lookup_and_capacity_check() -> None:
    clock = Clock()
    broker = RoutingTaskHandleBroker(retention_seconds=5, max_handles=1, clock=clock)
    authorization = _digest(b"owner")
    first = broker.mint(job_id=_digest(b"first"), authorization_digest=authorization)

    clock.now += 5
    with pytest.raises(TaskHandleUnavailableError, match=r"^routing task handle is unavailable$"):
        broker.resolve(task_id=first.task_id, authorization_digest=authorization)
    second = broker.mint(job_id=_digest(b"second"), authorization_digest=authorization)

    assert second.task_id != first.task_id
    assert broker.active_handle_count() == 1


def test_broker_cancellation_never_calls_delegate_before_authorization() -> None:
    broker = RoutingTaskHandleBroker()
    authorization = _digest(b"owner")
    handle = broker.mint(job_id=_digest(b"job"), authorization_digest=authorization)
    calls: list[tuple[str, str]] = []

    def cancel(job_id: str, owner: str) -> str:
        calls.append((job_id, owner))
        return "cancelled"

    with pytest.raises(TaskHandleUnavailableError):
        broker.cancel(
            task_id=handle.task_id,
            authorization_digest=_digest(b"other"),
            cancel_job=cancel,
        )
    assert calls == []
    assert (
        broker.cancel(
            task_id=handle.task_id,
            authorization_digest=authorization,
            cancel_job=cancel,
        )
        == "cancelled"
    )
    assert calls == [(handle.job_id, authorization)]


def test_explicit_compatibility_gate_requires_every_security_and_runtime_seam() -> None:
    compatible = assess_mcp_tasks_compatibility(
        mcp_version="9.9.9",
        has_extension_api=True,
        has_task_wire_types=True,
        has_task_dispatcher=True,
        has_owner_bound_task_lookup=True,
        has_durable_task_handle_store=True,
    )
    assert compatible.supports_safe_wire_contract is True
    assert compatible.gaps == ()

    incompatible = assess_mcp_tasks_compatibility(
        mcp_version="9.9.9",
        has_extension_api=True,
        has_task_wire_types=False,
        has_task_dispatcher=False,
        has_owner_bound_task_lookup=False,
        has_durable_task_handle_store=False,
    )
    assert incompatible.supports_safe_wire_contract is False
    assert incompatible.gaps == (
        "task_wire_types_unavailable",
        "task_dispatcher_unavailable",
        "owner_bound_lookup_unavailable",
        "durable_task_handle_store_unavailable",
    )


def test_installed_runtime_probe_refuses_to_overclaim_task_support() -> None:
    result = probe_installed_mcp_tasks_runtime()

    assert isinstance(result, MCPTasksCompatibility)
    assert result.mcp_version == "2.0.0"
    assert result.has_extension_api is True
    assert result.has_task_wire_types is False
    assert result.has_task_dispatcher is False
    assert result.has_owner_bound_task_lookup is False
    assert result.has_durable_task_handle_store is False
    assert result.supports_safe_wire_contract is False
