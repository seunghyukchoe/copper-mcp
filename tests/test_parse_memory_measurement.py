"""Full-scale parser allocation checks run once outside the coverage interpreter."""

from __future__ import annotations

import hashlib
import json
import os
import platform
import subprocess
import sys
import tracemalloc
from dataclasses import asdict
from pathlib import Path
from typing import Any

import pytest

from scripts import measure_parse_memory as measurement
from scripts.replay_source_binding import (
    _MAX_OUTPUT_BYTES,
    SourceBinding,
    capture_source_binding,
    verify_source_binding,
)

PARSE_MEMORY_SCHEMA = "copper-mcp/parse-memory-replay/v1"
PARSE_MEMORY_KEYS = frozenset(
    {
        "python_version",
        "receipt_digest",
        "report",
        "schema",
        "source_inventory_digest",
        "source_inventory_files",
        "status",
    }
)
CASE_KEYS = frozenset(
    {"shape", "payload_digest", "payload_bytes", "limits", "refusal_code", "peak_bytes"}
)
CHILD_TIMEOUT_SECONDS = 1_200
# Bind the original full-size generators, not merely a digest-shaped child assertion.
EXPECTED_PAYLOADS = {
    "wide": (16_777_212, "dae4064a98a2c0a55388161efdcb40bbf0c5f76fc0ac9775f8b404cd1e0d4078"),
    "tree": (16_625_160, "0133f13bc94523ee38e923585c953f83f28f668d667cc8c3606739688cfa3e5f"),
    "deep": (16_777_200, "1f2042cf18f9c9713c79c53b530c6632690ce7daa443d01f132ea6f5fad89743"),
}


def _canonical_digest(value: object) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _closed_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _reject_nonfinite_json_number(value: str) -> Any:
    raise ValueError(f"non-finite JSON number: {value}")


def _render_test_envelope(binding: SourceBinding, report: object) -> bytes:
    receipt = {
        "schema": PARSE_MEMORY_SCHEMA,
        "source_inventory_digest": binding.digest,
        "source_inventory_files": len(binding.entries),
        "python_version": platform.python_version(),
        "status": "measured",
        "report": report,
    }
    return json.dumps(
        {**receipt, "receipt_digest": _canonical_digest(receipt)}, sort_keys=True
    ).encode()


def _synthetic_valid_report() -> dict[str, object]:
    return {
        "cases": [
            {
                "shape": scenario.shape,
                "payload_digest": "sha256:" + EXPECTED_PAYLOADS[scenario.shape][1],
                "payload_bytes": EXPECTED_PAYLOADS[scenario.shape][0],
                "limits": asdict(scenario.limits),
                "refusal_code": scenario.expected_refusal_code,
                "peak_bytes": 1,
            }
            for scenario in measurement.scenarios()
        ]
    }


def _validate_parse_memory_envelope(payload: bytes, binding: SourceBinding) -> dict[str, Any]:
    if len(payload) > _MAX_OUTPUT_BYTES:
        raise AssertionError("parse-memory replay output exceeds its byte budget")
    try:
        envelope = json.loads(
            payload,
            object_pairs_hook=_closed_json_object,
            parse_constant=_reject_nonfinite_json_number,
        )
    except (UnicodeDecodeError, ValueError) as error:
        raise AssertionError("parse-memory replay output is not one JSON document") from error
    if not isinstance(envelope, dict) or set(envelope) != PARSE_MEMORY_KEYS:
        raise AssertionError("parse-memory replay envelope is not closed")
    body = {key: value for key, value in envelope.items() if key != "receipt_digest"}
    if type(envelope["receipt_digest"]) is not str or envelope[
        "receipt_digest"
    ] != _canonical_digest(body):
        raise AssertionError("parse-memory replay self-digest does not match")
    if (
        type(envelope["schema"]) is not str
        or envelope["schema"] != PARSE_MEMORY_SCHEMA
        or type(envelope["status"]) is not str
        or envelope["status"] != "measured"
    ):
        raise AssertionError("parse-memory replay identity does not match")
    if (
        type(envelope["python_version"]) is not str
        or envelope["python_version"] != platform.python_version()
    ):
        raise AssertionError("parse-memory replay interpreter does not match")
    if (
        type(envelope["source_inventory_digest"]) is not str
        or envelope["source_inventory_digest"] != binding.digest
        or type(envelope["source_inventory_files"]) is not int
        or envelope["source_inventory_files"] != len(binding.entries)
    ):
        raise AssertionError("parse-memory replay source binding does not match")
    report = envelope["report"]
    if (
        not isinstance(report, dict)
        or set(report) != {"cases"}
        or not isinstance(report["cases"], list)
    ):
        raise AssertionError("parse-memory replay report is not closed")
    scenarios = measurement.scenarios()
    if len(report["cases"]) != len(scenarios):
        raise AssertionError("parse-memory replay case count does not match")
    for case, scenario in zip(report["cases"], scenarios, strict=True):
        if not isinstance(case, dict) or set(case) != CASE_KEYS:
            raise AssertionError("parse-memory replay case is not closed")
        expected_bytes, expected_digest = EXPECTED_PAYLOADS[scenario.shape]
        limits = case["limits"]
        if (
            type(case["shape"]) is not str
            or case["shape"] != scenario.shape
            or case["payload_digest"] != "sha256:" + expected_digest
            or type(case["payload_bytes"]) is not int
            or case["payload_bytes"] != expected_bytes
            or not isinstance(limits, dict)
            or any(type(value) is not int for value in limits.values())
            or limits != asdict(scenario.limits)
            or type(case["refusal_code"]) is not str
            or case["refusal_code"] != scenario.expected_refusal_code
            or type(case["peak_bytes"]) is not int
            or not 0 < case["peak_bytes"] < scenario.peak_ceiling_bytes
        ):
            raise AssertionError("parse-memory replay case does not match")
    return report


def _current_parse_memory_report() -> dict[str, Any]:
    before = capture_source_binding()
    script = Path(__file__).resolve().parents[1] / "scripts" / "replay_source_binding.py"
    environment = {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"}
    try:
        completed = subprocess.run(  # noqa: S603 - fixed interpreter and repository-owned script
            [sys.executable, "-I", str(script), "--parse-memory"],
            check=True,
            capture_output=True,
            timeout=CHILD_TIMEOUT_SECONDS,
            env=environment,
        )
    finally:
        verify_source_binding(before)
    return _validate_parse_memory_envelope(completed.stdout, before)


@pytest.fixture(scope="module")
def parse_memory_report() -> dict[str, Any]:
    """One fresh, source-bound child serves every full-scale assertion in this module."""

    return _current_parse_memory_report()


def test_parse_memory_subprocess_is_fixed_isolated_and_has_a_minimal_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    binding = capture_source_binding()
    payload = _render_test_envelope(binding, _synthetic_valid_report())
    captured: dict[str, Any] = {}

    def completed(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[bytes]:
        captured["command"] = command
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(command, 0, stdout=payload, stderr=b"")

    monkeypatch.setenv("COV_CORE_SOURCE", "must-not-reach-child")
    monkeypatch.setenv("PYTEST_ADDOPTS", "must-not-reach-child")
    monkeypatch.setattr(subprocess, "run", completed)

    assert _current_parse_memory_report() == _synthetic_valid_report()
    assert captured["command"] == [
        sys.executable,
        "-I",
        str(Path(__file__).resolve().parents[1] / "scripts" / "replay_source_binding.py"),
        "--parse-memory",
    ]
    assert captured["kwargs"] == {
        "check": True,
        "capture_output": True,
        "timeout": CHILD_TIMEOUT_SECONDS,
        "env": {"PATH": os.environ.get("PATH", os.defpath), "LANG": "C.UTF-8"},
    }


def test_full_scale_cases_are_bound_and_redacted(parse_memory_report: dict[str, Any]) -> None:
    cases = parse_memory_report["cases"]
    assert isinstance(cases, list)
    assert [case["shape"] for case in cases] == ["wide", "tree", "deep"]
    for case, scenario in zip(cases, measurement.scenarios(), strict=True):
        assert isinstance(case, dict) and set(case) == CASE_KEYS
        assert case["payload_digest"].startswith("sha256:")
        assert type(case["payload_bytes"]) is int
        assert case["payload_bytes"] <= scenario.limits.max_input_bytes
        assert case["limits"] == asdict(scenario.limits)
        assert case["refusal_code"] == scenario.expected_refusal_code
        assert type(case["peak_bytes"]) is int
        assert case["peak_bytes"] < scenario.peak_ceiling_bytes
        assert "payload" not in case
    # Preserve the original retained-memory law at the shipped token default too.
    assert measurement.ParseLimits().max_tokens * 200 < 1024 * measurement.MIB


@pytest.mark.parametrize("shape", ("wide", "tree", "deep"))
def test_generators_preserve_the_original_full_scale_payloads(shape: str) -> None:
    scenario = next(item for item in measurement.scenarios() if item.shape == shape)
    payload = scenario.payload_factory(scenario.limits.max_input_bytes)
    assert (len(payload), hashlib.sha256(payload).hexdigest()) == EXPECTED_PAYLOADS[shape]


@pytest.mark.parametrize(
    "field,value", (("payload_bytes", 1), ("payload_digest", "sha256:" + "0" * 64))
)
def test_parent_refuses_resigned_small_or_different_payloads(field: str, value: object) -> None:
    binding = SourceBinding((("synthetic.py", "0" * 64),))
    report = _synthetic_valid_report()
    report["cases"][0][field] = value
    with pytest.raises(AssertionError, match="case does not match"):
        _validate_parse_memory_envelope(_render_test_envelope(binding, report), binding)


def test_parent_refuses_float_limits_even_when_numerically_equal() -> None:
    binding = SourceBinding((("synthetic.py", "0" * 64),))
    report = _synthetic_valid_report()
    limits = report["cases"][0]["limits"]
    limits["max_nodes"] = float(limits["max_nodes"])
    with pytest.raises(AssertionError, match="case does not match"):
        _validate_parse_memory_envelope(_render_test_envelope(binding, report), binding)


@pytest.mark.parametrize(
    ("refusal_code", "peak_bytes", "message"),
    [
        ("budget.exceeded.nodes", 1, "refusal code"),
        (None, 1, "did not refuse"),
        ("budget.exceeded.children_per_list", 48 * measurement.MIB, "peak allocation"),
        ("budget.exceeded.children_per_list", 0, "peak allocation"),
    ],
)
def test_measurement_refuses_wrong_code_no_refusal_or_over_ceiling(
    refusal_code: str | None, peak_bytes: int, message: str
) -> None:
    scenario = measurement.scenarios()[0]
    with pytest.raises(measurement.ParseMemoryMeasurementError, match=message):
        measurement._validate_observation(scenario, refusal_code, peak_bytes)


def test_parser_disabling_tracing_cannot_produce_successful_measurement() -> None:
    scenario = measurement.scenarios()[0]

    def disable_tracing(_payload, _limits):
        tracemalloc.stop()
        raise measurement.SExprError(
            code=scenario.expected_refusal_code, message="synthetic refusal", offset=0
        )

    with pytest.raises(measurement.ParseMemoryMeasurementError, match="tracing"):
        measurement.measure_scenario(scenario, parser=disable_tracing)


def test_preexisting_tracing_is_refused_without_stopping_its_owner() -> None:
    tracemalloc.start()
    try:
        with pytest.raises(
            measurement.ParseMemoryMeasurementError, match="fresh allocation tracing"
        ):
            measurement.measure_scenario(measurement.scenarios()[0])
        assert tracemalloc.is_tracing()
    finally:
        tracemalloc.stop()


def test_parent_refuses_resigned_zero_peak_measurement() -> None:
    binding = SourceBinding((("synthetic.py", "0" * 64),))
    report = _synthetic_valid_report()
    report["cases"][0]["peak_bytes"] = 0
    with pytest.raises(AssertionError, match="case does not match"):
        _validate_parse_memory_envelope(_render_test_envelope(binding, report), binding)


def test_parent_refuses_receipt_tampering_and_source_or_interpreter_mismatch() -> None:
    binding = SourceBinding((("synthetic.py", "0" * 64),))
    valid = _render_test_envelope(binding, _synthetic_valid_report())

    tampered = json.loads(valid)
    tampered["report"]["cases"] = ["changed"]
    with pytest.raises(AssertionError, match="self-digest"):
        _validate_parse_memory_envelope(json.dumps(tampered).encode(), binding)

    source_mismatch = json.loads(valid)
    source_mismatch["source_inventory_digest"] = "sha256:" + "1" * 64
    source_body = {key: value for key, value in source_mismatch.items() if key != "receipt_digest"}
    source_mismatch["receipt_digest"] = _canonical_digest(source_body)
    with pytest.raises(AssertionError, match="source binding"):
        _validate_parse_memory_envelope(json.dumps(source_mismatch).encode(), binding)

    interpreter_mismatch = json.loads(valid)
    interpreter_mismatch["python_version"] = "0.0.0"
    interpreter_body = {
        key: value for key, value in interpreter_mismatch.items() if key != "receipt_digest"
    }
    interpreter_mismatch["receipt_digest"] = _canonical_digest(interpreter_body)
    with pytest.raises(AssertionError, match="interpreter"):
        _validate_parse_memory_envelope(json.dumps(interpreter_mismatch).encode(), binding)
