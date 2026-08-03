from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from copper_mcp.circuit_ir import (
    CircuitIntentValidationError,
    CircuitParseLimits,
    decode_snapshot_json,
    encode_snapshot,
    make_snapshot,
    normalize_content,
    snapshot_from_content,
)

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
SCHEMA = ROOT / "schemas" / "circuit-intent" / "0.1.0.schema.json"


def _document() -> dict[str, Any]:
    document = json.loads(FIXTURE.read_text(encoding="utf-8"))
    assert isinstance(document, dict)
    return document


def _payload(document: dict[str, Any]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        separators=(",", ":"),
        allow_nan=True,
    ).encode("utf-8")


def _assert_decode_error(
    document: dict[str, Any],
    code: str,
    *,
    limits: CircuitParseLimits | None = None,
) -> CircuitIntentValidationError:
    with pytest.raises(CircuitIntentValidationError) as raised:
        decode_snapshot_json(_payload(document), limits)
    assert raised.value.code == code
    return raised.value


def test_fixture_strictly_decodes_encodes_and_matches_schema() -> None:
    payload = FIXTURE.read_bytes()
    document = _document()
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    validator = Draft202012Validator(schema)

    assert list(validator.iter_errors(document)) == []
    snapshot = decode_snapshot_json(payload)
    encoded = encode_snapshot(snapshot)

    assert encoded == payload
    assert json.loads(encoded) == document
    assert list(validator.iter_errors(json.loads(encoded))) == []
    assert snapshot.snapshot_digest == document["snapshot_digest"]


def test_decode_normalizes_collection_order_without_changing_digest() -> None:
    document = _document()
    content = document["content"]
    content["components"].reverse()
    content["nets"].reverse()
    content["ports"].reverse()
    for net in content["nets"]:
        net["connections"].reverse()

    snapshot = decode_snapshot_json(_payload(document))

    assert encode_snapshot(snapshot) == FIXTURE.read_bytes()
    assert snapshot.content == normalize_content(snapshot.content)


def test_semantic_change_produces_a_new_deterministic_digest() -> None:
    original = decode_snapshot_json(FIXTURE.read_bytes())
    changed_content = replace(original.content, title="Original RC audio intent, revised")

    first = make_snapshot(changed_content)
    second = make_snapshot(changed_content)

    assert first == second
    assert first.snapshot_digest != original.snapshot_digest
    assert encode_snapshot(first) == encode_snapshot(second)


def test_tampered_content_is_not_accepted_under_the_old_digest() -> None:
    document = _document()
    document["content"]["title"] = "Tampered intent"

    _assert_decode_error(document, "digest.mismatch")


def test_duplicate_json_keys_are_rejected() -> None:
    payload = FIXTURE.read_bytes()
    duplicate = b'{"schema":"copper.circuit-intent",' + payload[1:]

    with pytest.raises(CircuitIntentValidationError) as raised:
        decode_snapshot_json(duplicate)

    assert raised.value.code == "schema.invalid"


@pytest.mark.parametrize("value", [1.5, float("nan"), float("inf")])
def test_numeric_json_scalars_are_rejected(value: float) -> None:
    document = _document()
    document["content"]["title"] = value

    _assert_decode_error(document, "schema.invalid")


def test_unknown_fields_are_rejected_by_schema_and_runtime() -> None:
    document = _document()
    document["content"]["internal_instruction"] = "do not disclose"
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))

    assert not Draft202012Validator(schema).is_valid(document)
    error = _assert_decode_error(document, "schema.invalid")
    assert "internal_instruction" not in str(error)


def test_control_characters_are_rejected_without_echoing_content() -> None:
    document = _document()
    document["content"]["components"][0]["value"] = "100n\nSECRET_INTERNAL_VALUE"

    error = _assert_decode_error(document, "schema.invalid")

    assert "SECRET_INTERNAL_VALUE" not in str(error)


def test_input_depth_and_value_budgets_fail_before_full_decode() -> None:
    payload = FIXTURE.read_bytes()

    for limits in (
        CircuitParseLimits(max_input_bytes=len(payload) - 1),
        CircuitParseLimits(max_json_depth=2),
        CircuitParseLimits(max_json_values=16),
    ):
        with pytest.raises(CircuitIntentValidationError) as raised:
            decode_snapshot_json(payload, limits)
        assert raised.value.code == "budget.exceeded"


def test_oversized_array_is_refused_before_the_decoder_expands_it() -> None:
    class _UnexpandableList(list[Any]):
        def __iter__(self) -> Any:
            raise AssertionError("array was expanded before the structure budget was checked")

    oversized = _UnexpandableList(["x"] * 9_000)

    with pytest.raises(CircuitIntentValidationError) as raised:
        snapshot_from_content(oversized)

    assert raised.value.code == "budget.exceeded"


def test_multi_million_element_array_is_refused_without_materializing_it() -> None:
    with pytest.raises(CircuitIntentValidationError) as raised:
        snapshot_from_content(["x"] * 4_000_000)

    assert raised.value.code == "budget.exceeded"


def _exception_chain(error: BaseException) -> list[BaseException]:
    chain: list[BaseException] = []
    pending: list[BaseException | None] = [error]
    while pending:
        item = pending.pop()
        if item is None or any(item is seen for seen in chain):
            continue
        chain.append(item)
        pending.extend((item.__cause__, item.__context__))
    return chain


def test_rejected_enum_values_never_reach_the_exception_chain() -> None:
    document = _document()
    document["content"]["components"][0]["kind"] = "SECRET_PRIVATE_KIND"
    content = _document()["content"]
    content["ports"][0]["direction"] = "SECRET_PRIVATE_KIND"

    with pytest.raises(CircuitIntentValidationError) as decoded:
        decode_snapshot_json(_payload(document))
    with pytest.raises(CircuitIntentValidationError) as structured:
        snapshot_from_content(content)

    for raised in (decoded, structured):
        assert raised.value.code == "schema.invalid"
        for error in _exception_chain(raised.value):
            assert "SECRET_PRIVATE_KIND" not in repr(error)


@pytest.mark.parametrize(
    ("field", "limit"),
    [
        ("components", CircuitParseLimits(max_components=1)),
        ("nets", CircuitParseLimits(max_nets=2)),
        ("ports", CircuitParseLimits(max_ports=2)),
        ("connections", CircuitParseLimits(max_connections=3)),
    ],
)
def test_topology_collection_budgets_are_enforced(
    field: str,
    limit: CircuitParseLimits,
) -> None:
    with pytest.raises(CircuitIntentValidationError) as raised:
        decode_snapshot_json(FIXTURE.read_bytes(), limit)

    assert raised.value.code == "budget.exceeded", field


@pytest.mark.parametrize("value", [True, 0, -1, 1.5])
def test_parse_limits_require_positive_integers(value: object) -> None:
    with pytest.raises(ValueError, match="positive integers"):
        CircuitParseLimits(max_components=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_input_bytes", 256_001),
        ("max_json_depth", 33),
        ("max_json_values", 8_193),
        ("max_components", 65),
        ("max_nets", 129),
        ("max_ports", 33),
        ("max_connections", 129),
    ],
)
def test_parse_limits_cannot_weaken_v0_1_ceilings(field: str, value: int) -> None:
    with pytest.raises(ValueError, match="cannot exceed"):
        CircuitParseLimits(**{field: value})  # type: ignore[arg-type]


def _semantic_case(case: str) -> dict[str, Any]:
    document = copy.deepcopy(_document())
    content = document["content"]
    components = content["components"]
    nets = content["nets"]
    ports = content["ports"]

    if case == "duplicate-component-id":
        components[1]["id"] = components[0]["id"]
    elif case == "duplicate-reference":
        components[1]["kind"] = "capacitor_unpolarized"
        components[1]["reference"] = components[0]["reference"]
    elif case == "duplicate-net-id":
        nets[1]["id"] = nets[0]["id"]
    elif case == "unknown-component":
        nets[0]["connections"][0]["component_id"] = "component:missing"
    elif case == "unknown-port-net":
        ports[0]["net_id"] = "net:missing"
    elif case == "pin-multiple-nets":
        nets[2]["connections"][0]["pin"] = "1"
    elif case == "incomplete-pins":
        content["nets"] = nets[:2]
        content["ports"] = ports[:2]
    elif case == "dangling-without-port":
        content["ports"] = ports[:2]
    elif case == "component-self-short":
        nets[1]["connections"].append({"component_id": "component:c-filter", "pin": "2"})
    elif case == "duplicate-pin":
        nets[1]["connections"].append(copy.deepcopy(nets[1]["connections"][0]))
    else:  # pragma: no cover - protects the test table itself
        raise AssertionError(f"unknown semantic case: {case}")
    return document


@pytest.mark.parametrize(
    ("case", "code"),
    [
        ("duplicate-component-id", "identity.duplicate"),
        ("duplicate-reference", "identity.duplicate"),
        ("duplicate-net-id", "identity.duplicate"),
        ("unknown-component", "reference.unknown"),
        ("unknown-port-net", "reference.unknown"),
        ("pin-multiple-nets", "topology.multiple_nets"),
        ("incomplete-pins", "topology.incomplete"),
        ("dangling-without-port", "topology.dangling"),
        ("component-self-short", "topology.self_short"),
        ("duplicate-pin", "topology.duplicate_pin"),
    ],
)
def test_semantic_topology_failures_are_typed(case: str, code: str) -> None:
    _assert_decode_error(_semantic_case(case), code)
