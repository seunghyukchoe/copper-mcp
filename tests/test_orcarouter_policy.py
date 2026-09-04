from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.routing.orcarouter_policy as orca_module
from copper_mcp.routing.orcarouter_policy import (
    ORCAROUTER_DEFAULT_MODEL,
    ORCAROUTER_POLICY_ID,
    OrcaRouterPolicy,
    OrcaRouterPolicyError,
)
from copper_mcp.routing.policy import (
    decode_policy_input_json,
    evaluate_policy,
    policy_input_digest,
)

_FIXTURE = Path(__file__).parent / "fixtures" / "routing-policy" / "reference-input.json"
_TEST_KEY = "sk-" + "orca-" + "fixture"
_OTHER_KEY = "sk-" + "orca-" + "secret"


def _input():
    return decode_policy_input_json(_FIXTURE.read_bytes())


def _response(arguments: dict[str, Any], *, tool_name: str = "select_routing_policy") -> bytes:
    return _response_with_arguments(
        json.dumps(arguments, ensure_ascii=True, separators=(",", ":")),
        tool_name=tool_name,
    )


def _response_with_arguments(
    arguments: str,
    *,
    tool_name: str = "select_routing_policy",
) -> bytes:
    return json.dumps(
        {
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "type": "function",
                                "function": {
                                    "name": tool_name,
                                    "arguments": arguments,
                                },
                            }
                        ],
                    }
                }
            ]
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _arguments() -> dict[str, Any]:
    return {
        "net_order": ["n1", "n0"],
        "corridor_hints": ["c2", "c0"],
        "repair_windows": ["r1"],
    }


def test_orcarouter_selection_is_redacted_and_rebound_to_local_options(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    def fake_post(
        url: str,
        headers: dict[str, str],
        body: bytes,
        timeout: float,
    ) -> bytes:
        captured.update(url=url, headers=headers, body=body, timeout=timeout)
        return _response(_arguments())

    monkeypatch.setattr(orca_module, "_post_json", fake_post)
    policy_input = _input()
    policy = OrcaRouterPolicy(api_key=_TEST_KEY, model="openai/gpt-5.5")
    decision = evaluate_policy(policy, policy_input)

    assert decision.policy_id == ORCAROUTER_POLICY_ID
    assert decision.input_digest == policy_input_digest(policy_input)
    assert decision.net_order == ("net:data", "net:clock")
    assert decision.corridor_hints == (
        policy_input.corridor_candidates[0],
        policy_input.corridor_candidates[2],
    )
    assert decision.repair_windows == (policy_input.repair_candidates[1],)

    assert captured["url"] == "https://api.orcarouter.ai/v1/chat/completions"
    assert captured["headers"]["Authorization"] == "Bearer " + _TEST_KEY
    assert captured["timeout"] == 10.0
    request = json.loads(captured["body"])
    assert request["model"] == "openai/gpt-5.5"
    assert request["stream"] is False
    assert "response_format" not in request
    assert request["tool_choice"] == {
        "function": {"name": "select_routing_policy"},
        "type": "function",
    }
    serialized = captured["body"].decode("utf-8")
    for sensitive_value in (
        "net:clock",
        "net:data",
        policy_input.board_revision,
        "min_x",
        "max_x",
        "vertices",
        "apply_token",
    ):
        assert sensitive_value not in serialized
    assert request["tools"][0]["function"]["parameters"]["properties"]["net_order"]["items"] == {
        "enum": ["n0", "n1"],
        "type": "string",
    }


def test_orcarouter_policy_repr_does_not_include_the_api_key() -> None:
    policy = OrcaRouterPolicy(api_key=_OTHER_KEY)

    assert _OTHER_KEY not in repr(policy)
    assert policy.model == ORCAROUTER_DEFAULT_MODEL


def test_from_env_uses_explicit_orcarouter_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ORCA_KEY", _TEST_KEY)
    monkeypatch.setenv("ORCAROUTER_MODEL", "anthropic/claude-sonnet")

    policy = OrcaRouterPolicy.from_env()

    assert policy.model == "anthropic/claude-sonnet"
    assert _TEST_KEY not in repr(policy)


def test_from_env_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ORCA_KEY", raising=False)

    with pytest.raises(ValueError, match="ORCA_KEY is not set"):
        OrcaRouterPolicy.from_env()


@pytest.mark.parametrize(
    "kwargs",
    [
        {"api_key": "not-a-key"},
        {"api_key": _TEST_KEY, "model": ""},
        {"api_key": _TEST_KEY, "base_url": "http://api.orcarouter.ai/v1"},
        {"api_key": _TEST_KEY, "base_url": "https://example.test/v1"},
        {"api_key": _TEST_KEY, "base_url": "https://api.orcarouter.ai/v1?x=1"},
        {"api_key": _TEST_KEY, "timeout_seconds": 0.0},
        {"api_key": _TEST_KEY, "timeout_seconds": 60.1},
    ],
)
def test_orcarouter_configuration_is_bounded(kwargs: dict[str, Any]) -> None:
    with pytest.raises(ValueError):
        OrcaRouterPolicy(**kwargs)


def test_upstream_failures_are_redacted(monkeypatch: pytest.MonkeyPatch) -> None:
    def failing_post(*args: Any, **kwargs: Any) -> bytes:
        del args, kwargs
        raise RuntimeError("provider body contains a secret")

    monkeypatch.setattr(orca_module, "_post_json", failing_post)

    with pytest.raises(OrcaRouterPolicyError) as error:
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())

    assert str(error.value) == "orcarouter_policy_rejected"
    assert "secret" not in str(error.value)


def test_post_json_uses_the_fixed_endpoint_and_bounded_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, Any] = {}

    class FakeResponse:
        status = 200

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            del args

        def read(self, limit: int) -> bytes:
            captured["read_limit"] = limit
            return b"{}"

    class FakeOpener:
        def open(self, request: Any, *, timeout: float) -> FakeResponse:
            captured.update(
                url=request.full_url,
                method=request.get_method(),
                timeout=timeout,
            )
            return FakeResponse()

    monkeypatch.setattr(orca_module, "build_opener", lambda handler: FakeOpener())

    assert (
        orca_module._post_json(
            "https://api.orcarouter.ai/v1/chat/completions",
            {"Accept": "application/json"},
            b"{}",
            1.0,
        )
        == b"{}"
    )
    assert captured == {
        "url": "https://api.orcarouter.ai/v1/chat/completions",
        "method": "POST",
        "timeout": 1.0,
        "read_limit": 64_001,
    }


def test_post_json_rejects_an_endpoint_override(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orca_module, "build_opener", lambda handler: pytest.fail("opened"))

    with pytest.raises(OrcaRouterPolicyError):
        orca_module._post_json("https://example.test/v1/chat/completions", {}, b"{}", 1.0)


def test_post_json_refuses_non_success_without_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    opens = 0

    class FakeResponse:
        status = 429

        def __enter__(self) -> FakeResponse:
            return self

        def __exit__(self, *args: Any) -> None:
            del args

    class FakeOpener:
        def open(self, request: Any, *, timeout: float) -> FakeResponse:
            nonlocal opens
            del request, timeout
            opens += 1
            return FakeResponse()

    monkeypatch.setattr(orca_module, "build_opener", lambda handler: FakeOpener())

    with pytest.raises(OrcaRouterPolicyError):
        orca_module._post_json(
            "https://api.orcarouter.ai/v1/chat/completions",
            {},
            b"{}",
            1.0,
        )

    assert opens == 1


def test_redirects_are_rejected() -> None:
    with pytest.raises(OrcaRouterPolicyError):
        orca_module._NoRedirectHandler().redirect_request(None, None, 302, "", {})


@pytest.mark.parametrize(
    "response",
    [
        _response(_arguments(), tool_name="other_tool"),
        _response({"net_order": ["n0"], "corridor_hints": [], "repair_windows": []}),
        _response({"net_order": ["n0", "n9"], "corridor_hints": [], "repair_windows": []}),
        _response_with_arguments(
            '{"net_order":["n0","n1"],'
            '"net_order":["n1","n0"],'
            '"corridor_hints":[],"repair_windows":[]}'
        ),
    ],
)
def test_malformed_or_unbound_provider_decisions_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    response: bytes,
) -> None:
    monkeypatch.setattr(orca_module, "_post_json", lambda *args, **kwargs: response)

    with pytest.raises(OrcaRouterPolicyError):
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())


def test_oversized_provider_response_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(orca_module, "_post_json", lambda *args, **kwargs: b"{" + b" " * 64_000)

    with pytest.raises(OrcaRouterPolicyError):
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())


def test_non_finite_provider_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _response_with_arguments(
        '{"net_order":["n0","n1"],"corridor_hints":[NaN],"repair_windows":[]}'
    )
    monkeypatch.setattr(orca_module, "_post_json", lambda *args, **kwargs: response)

    with pytest.raises(OrcaRouterPolicyError):
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())


def test_deeply_nested_provider_value_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    nested = "null"
    for _ in range(orca_module._MAX_JSON_DEPTH + 1):
        nested = '{"nested":' + nested + "}"
    response = _response_with_arguments(nested)
    monkeypatch.setattr(orca_module, "_post_json", lambda *args, **kwargs: response)

    with pytest.raises(OrcaRouterPolicyError):
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())


def test_multiple_choices_fail_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    response = _response(_arguments()).decode("utf-8")
    decoded = json.loads(response)
    decoded["choices"].append(decoded["choices"][0])
    monkeypatch.setattr(
        orca_module,
        "_post_json",
        lambda *args, **kwargs: json.dumps(decoded).encode("utf-8"),
    )

    with pytest.raises(OrcaRouterPolicyError):
        OrcaRouterPolicy(api_key=_TEST_KEY).propose(_input())
