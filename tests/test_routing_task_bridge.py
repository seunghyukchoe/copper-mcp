from __future__ import annotations

import hashlib

import pytest

from copper_mcp.routing.task_bridge import (
    DEFAULT_TASK_HANDLE_RETENTION_SECONDS,
    MAX_TASK_HANDLES,
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


class UnhashableText(str):
    __hash__ = None  # type: ignore[assignment]


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


@pytest.mark.parametrize(
    ("task_id", "authorization_digest"),
    (
        ([], _digest(b"owner")),
        ({"unhashable": "task"}, _digest(b"owner")),
        ("x" * 43, []),
        ("x" * 43, {"unhashable": "owner"}),
        (UnhashableText("x" * 43), _digest(b"owner")),
        ("x" * 43, UnhashableText(_digest(b"owner"))),
    ),
)
def test_broker_rejects_hostile_non_string_handles_before_dictionary_lookup(
    task_id: object,
    authorization_digest: object,
) -> None:
    broker = RoutingTaskHandleBroker()

    with pytest.raises(TaskHandleUnavailableError, match=r"^routing task handle is unavailable$"):
        broker.resolve(task_id=task_id, authorization_digest=authorization_digest)  # type: ignore[arg-type]


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


@pytest.mark.parametrize("retention_seconds", (True, False, 1.5, "5"))
def test_broker_rejects_non_integer_retention_limits(retention_seconds: object) -> None:
    with pytest.raises(ValueError):
        RoutingTaskHandleBroker(retention_seconds=retention_seconds)  # type: ignore[arg-type]


@pytest.mark.parametrize("max_handles", (True, False, 1.5, "5", MAX_TASK_HANDLES + 1))
def test_broker_rejects_boolean_non_integer_and_oversize_capacity(max_handles: object) -> None:
    with pytest.raises(ValueError):
        RoutingTaskHandleBroker(max_handles=max_handles)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_clock", (None, 3, "clock"))
def test_broker_rejects_non_callable_clock(bad_clock: object) -> None:
    with pytest.raises(TypeError):
        RoutingTaskHandleBroker(clock=bad_clock)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad_time", (float("nan"), float("inf"), float("-inf"), True, "now"))
def test_broker_fails_closed_for_non_finite_or_non_numeric_clock_values(bad_time: object) -> None:
    broker = RoutingTaskHandleBroker(clock=lambda: bad_time)  # type: ignore[arg-type]

    with pytest.raises(TaskHandleUnavailableError, match=r"^routing task handle is unavailable$"):
        broker.mint(job_id=_digest(b"job"), authorization_digest=_digest(b"owner"))


def test_broker_fails_closed_when_clock_moves_backwards() -> None:
    clock = Clock()
    broker = RoutingTaskHandleBroker(clock=clock)
    broker.mint(job_id=_digest(b"first"), authorization_digest=_digest(b"owner"))
    clock.now -= 1

    with pytest.raises(TaskHandleUnavailableError, match=r"^routing task handle is unavailable$"):
        broker.mint(job_id=_digest(b"second"), authorization_digest=_digest(b"owner"))


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


@pytest.mark.parametrize(
    "field",
    (
        "has_extension_api",
        "has_task_wire_types",
        "has_task_dispatcher",
        "has_owner_bound_task_lookup",
        "has_durable_task_handle_store",
    ),
)
def test_compatibility_gate_rejects_truthy_non_boolean_flags(field: str) -> None:
    values: dict[str, object] = {
        "mcp_version": "9.9.9",
        "has_extension_api": True,
        "has_task_wire_types": True,
        "has_task_dispatcher": True,
        "has_owner_bound_task_lookup": True,
        "has_durable_task_handle_store": True,
    }
    values[field] = "yes"

    with pytest.raises(TypeError, match="flags must be booleans"):
        assess_mcp_tasks_compatibility(**values)  # type: ignore[arg-type]


def test_compatibility_gate_rejects_non_text_version() -> None:
    with pytest.raises(TypeError, match="version must be text or absent"):
        assess_mcp_tasks_compatibility(
            mcp_version=1,  # type: ignore[arg-type]
            has_extension_api=True,
            has_task_wire_types=True,
            has_task_dispatcher=True,
            has_owner_bound_task_lookup=True,
            has_durable_task_handle_store=True,
        )


def test_runtime_probe_records_injected_current_mcp_2_0_facts() -> None:
    def symbol_probe(module_name: str, names: tuple[str, ...]) -> bool:
        return (module_name, names) in {
            ("mcp.server.extension", ("Extension", "MethodBinding")),
            ("mcp.server.mcpserver.server", ("require_client_extension",)),
        }

    result = probe_installed_mcp_tasks_runtime(
        version_lookup=lambda package: "2.0.0" if package == "mcp" else "",
        symbol_probe=symbol_probe,
        task_wire_probe=lambda: False,
    )

    assert isinstance(result, MCPTasksCompatibility)
    assert result.mcp_version == "2.0.0"
    assert result.has_extension_api is True
    assert result.has_task_wire_types is False
    assert result.has_task_dispatcher is False
    assert result.has_owner_bound_task_lookup is False
    assert result.has_durable_task_handle_store is False
    assert result.supports_safe_wire_contract is False


def test_installed_runtime_probe_stays_fail_closed_across_dependency_updates() -> None:
    result = probe_installed_mcp_tasks_runtime()

    assert isinstance(result, MCPTasksCompatibility)
    assert result.has_owner_bound_task_lookup is False
    assert result.has_durable_task_handle_store is False
    assert result.supports_safe_wire_contract is False
