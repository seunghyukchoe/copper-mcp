from __future__ import annotations

import json
import sys
from dataclasses import replace

import pytest

from copper_mcp.routing.policy import PolicyBounds, PolicyNet, RoutingPolicyInput
from copper_mcp.routing.policy_worker import (
    _SAFE_ENV,
    POLICY_WORKER_REJECTED,
    PolicyWorkerError,
    _run_closed_frame,
    _serve_reference_once,
    _spawn_worker,
    _worker_command,
    evaluate_reference_policy_in_worker,
)
from copper_mcp.routing.policy_worker_protocol import (
    MAX_POLICY_WORKER_FRAME_BYTES,
    POLICY_WORKER_REQUEST_SCHEMA,
    POLICY_WORKER_RESPONSE_SCHEMA,
    PolicyWorkerProtocolError,
    PolicyWorkerRequest,
    canonical_policy_worker_request_bytes,
    canonical_policy_worker_response_bytes,
    decode_policy_worker_request,
    decode_policy_worker_response,
    policy_worker_request_digest,
    rejected_policy_worker_response,
    validate_policy_worker_response,
)


def _input() -> RoutingPolicyInput:
    return RoutingPolicyInput(
        board_revision="sha256:" + "a" * 64,
        bounds=PolicyBounds(0, 0, 0, 0),
        nets=(
            PolicyNet("net:audio-left", 3, 8, 1),
            PolicyNet("net:audio-right", 2, 9, 2),
        ),
    )


def _request() -> PolicyWorkerRequest:
    return PolicyWorkerRequest(nonce="b" * 64, policy_input=_input())


def test_single_frame_reference_worker_is_deterministic_and_nonce_bound() -> None:
    request = _request()
    frame = canonical_policy_worker_request_bytes(request)
    first = _serve_reference_once(frame)
    second = _serve_reference_once(frame)
    response = decode_policy_worker_response(first)

    assert first == second
    assert validate_policy_worker_response(request, response).net_order == (
        "net:audio-left",
        "net:audio-right",
    )
    assert response.request_digest == policy_worker_request_digest(request)
    assert canonical_policy_worker_response_bytes(response) == first


@pytest.mark.parametrize(
    "frame",
    (
        b"",
        b"{",
        (
            b'{"schema":"copper-mcp.policy-worker-request.v1","schema":"x",'
            b'"backend":"deterministic-reference-v1","nonce":"'
            + b"a" * 64
            + b'","policy_input":{}}'
        ),
        json.dumps(
            {
                "schema": POLICY_WORKER_REQUEST_SCHEMA,
                "backend": "untrusted-local-model-v9",
                "nonce": "a" * 64,
                "policy_input": _input().as_json(),
            }
        ).encode(),
        b" " * (MAX_POLICY_WORKER_FRAME_BYTES + 1),
    ),
)
def test_hostile_or_oversized_request_frames_fail_closed(frame: bytes) -> None:
    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        decode_policy_worker_request(frame)
    response = decode_policy_worker_response(_serve_reference_once(frame))
    assert response.status == "rejected"
    assert response.error == POLICY_WORKER_REJECTED


def test_request_rejects_windows_and_geometry_bearing_input() -> None:
    unsafe = replace(
        _input(),
        bounds=PolicyBounds(0, 0, 1, 0),
    )
    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        PolicyWorkerRequest(nonce="b" * 64, policy_input=unsafe)


def test_response_must_bind_nonce_request_and_closed_reference_decision() -> None:
    request = _request()
    response_frame = _serve_reference_once(canonical_policy_worker_request_bytes(request))
    response = decode_policy_worker_response(response_frame)
    wrong_nonce = replace(response, nonce="c" * 64)
    wrong_request = replace(response, request_digest="sha256:" + "c" * 64)

    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        validate_policy_worker_response(request, wrong_nonce)
    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        validate_policy_worker_response(request, wrong_request)


def test_response_duplicate_keys_and_oversized_output_fail_closed() -> None:
    request = _request()
    valid = _serve_reference_once(canonical_policy_worker_request_bytes(request))
    duplicate = valid.replace(b'"status":"ok"', b'"status":"ok","status":"ok"')
    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        decode_policy_worker_response(duplicate)
    with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
        decode_policy_worker_response(b" " * (MAX_POLICY_WORKER_FRAME_BYTES + 1))


def test_response_requires_exact_canonical_bytes_after_validation() -> None:
    request = _request()
    canonical = _serve_reference_once(canonical_policy_worker_request_bytes(request))
    decoded_object = json.loads(canonical)
    alternate_spacing = json.dumps(decoded_object).encode("utf-8") + b"\n"
    alternate_order = (
        json.dumps(
            dict(reversed(tuple(decoded_object.items()))),
            separators=(",", ":"),
        ).encode("utf-8")
        + b"\n"
    )

    assert decode_policy_worker_response(canonical).status == "ok"
    for noncanonical in (canonical + b" ", alternate_spacing, alternate_order):
        with pytest.raises(PolicyWorkerProtocolError, match=POLICY_WORKER_REJECTED):
            decode_policy_worker_response(noncanonical)


def test_real_subprocess_emits_exact_canonical_response_bytes() -> None:
    request = _request()
    response_frame = _run_closed_frame(
        canonical_policy_worker_request_bytes(request),
        timeout_seconds=1.0,
        cancelled=None,
    )
    response = decode_policy_worker_response(response_frame)

    assert response_frame == canonical_policy_worker_response_bytes(response)


def test_rejected_response_has_one_fixed_redacted_error() -> None:
    response = rejected_policy_worker_response()
    decoded = decode_policy_worker_response(canonical_policy_worker_response_bytes(response))

    assert decoded.error == POLICY_WORKER_REJECTED
    assert decoded.schema == POLICY_WORKER_RESPONSE_SCHEMA
    assert "traceback" not in canonical_policy_worker_response_bytes(decoded).decode("ascii")


def test_parent_uses_isolated_self_python_and_replacement_environment() -> None:
    command = _worker_command()

    assert command[:3] == (sys.executable, "-I", "-c")
    assert command[-2:] == ("copper_mcp.routing.policy_worker", "--serve-reference-v1")
    assert _SAFE_ENV == {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
    assert "OPENAI_API_KEY" not in _SAFE_ENV
    assert "ANTHROPIC_API_KEY" not in _SAFE_ENV


def test_spawn_does_not_inherit_environment_or_open_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    class FakeProcess:
        pass

    def fake_popen(*args: object, **kwargs: object) -> FakeProcess:
        captured["args"] = args
        captured["kwargs"] = kwargs
        return FakeProcess()

    monkeypatch.setattr("copper_mcp.routing.policy_worker.subprocess.Popen", fake_popen)
    _spawn_worker()

    kwargs = captured["kwargs"]
    assert isinstance(kwargs, dict)
    assert kwargs["env"] == _SAFE_ENV
    assert kwargs["close_fds"] is True
    assert kwargs["shell"] is False
    assert kwargs["start_new_session"] is True
    assert kwargs["stderr"] is not None


def test_actual_subprocess_replay_is_deterministic_and_cannot_emit_copper() -> None:
    first = evaluate_reference_policy_in_worker(_input(), timeout_seconds=1.0)
    second = evaluate_reference_policy_in_worker(_input(), timeout_seconds=1.0)
    decision_fields = set(first.as_json())

    assert first == second
    assert first.corridor_hints == ()
    assert first.repair_windows == ()
    assert {"vertices", "copper", "apply_token", "route_patch"}.isdisjoint(decision_fields)


def test_parent_timeout_and_cancellation_kill_nonresponsive_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "copper_mcp.routing.policy_worker._worker_command",
        lambda: (sys.executable, "-I", "-c", "import time; time.sleep(60)"),
    )
    with pytest.raises(PolicyWorkerError, match=POLICY_WORKER_REJECTED):
        evaluate_reference_policy_in_worker(_input(), timeout_seconds=0.02)

    calls = 0

    def cancelled() -> bool:
        nonlocal calls
        calls += 1
        return calls >= 2

    with pytest.raises(PolicyWorkerError, match=POLICY_WORKER_REJECTED):
        evaluate_reference_policy_in_worker(_input(), timeout_seconds=1.0, cancelled=cancelled)
    assert calls >= 2
