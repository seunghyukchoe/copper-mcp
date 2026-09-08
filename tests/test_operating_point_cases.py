from __future__ import annotations

import hashlib
import json
import time
from dataclasses import replace

import pytest

from copper_mcp.engineering._operating_point_cases import (
    OperatingPointCasesError,
    resolve_operating_point_cases,
)
from copper_mcp.engineering._pin_net_source import SourcePinAlias
from copper_mcp.engineering._spice_terminal_binding import BoundSpicePin, BoundSpiceReference
from copper_mcp.engineering.inputs import parse_electrical_inputs
from copper_mcp.engineering.native_pin_net_map import NativePinNet
from copper_mcp.engineering.project_spice_model_binding import ProjectSpiceModelBinding
from copper_mcp.engineering.spice_model_library import SpiceDefinition


def _sha(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _declaration() -> bytes:
    return json.dumps(
        {
            "schema_version": "electrical-inputs/v1",
            "board_revision": _sha(b"board"),
            "snapshot_digest": _sha(b"snapshot"),
            "project_context_digest": _sha(b"project"),
            "profile_id": "analog-audio-v1",
            "source_artifacts": [
                {"artifact_id": "models", "role": "model-library", "artifact_digest": _sha(b"m")}
            ],
            "rails": [
                {"rail_id": "genrail", "nominal_voltage_uv": -1200000},
                {"rail_id": "vinrail", "nominal_voltage_uv": 5000000},
            ],
            "load_cases": [
                {"case_id": "loadgen", "rail_id": "genrail", "current_ua": 12000, "duration_ms": 7}
            ],
            "operating_limits": {
                "min_temperature_millic": 0,
                "max_temperature_millic": 80000,
                "max_input_voltage_uv": 10000000,
            },
        },
        separators=(",", ":"),
    ).encode()


def _node(reference: str, number: str, net: str, port: str, index: int) -> BoundSpicePin:
    symbol_uuid = f"00000000-0000-4000-8000-{index:012d}"
    alias = SourcePinAlias(f"/{symbol_uuid}", symbol_uuid, f"10000000-0000-4000-8000-{index:012d}")
    code = {"/GND": "0", "/VGEN": "1", "/VIN": "2"}[net]
    return BoundSpicePin(
        NativePinNet(
            reference,
            number,
            "Device:D",
            code,
            net,
            "Default",
            None,
            "passive",
            False,
            (alias,),
        ),
        port,
    )


def _binding(declaration: bytes) -> ProjectSpiceModelBinding:
    parsed = parse_electrical_inputs(declaration)
    definition = SpiceDefinition("DMOD", "diode", ("A", "K"), b".model DMOD D(IS=1e-12)\n", ())
    first = BoundSpiceReference(
        "model1",
        "models",
        "R1",
        definition,
        (_node("R1", "1", "/VIN", "A", 1), _node("R1", "2", "/GND", "K", 2)),
    )
    second = BoundSpiceReference(
        "model2",
        "models",
        "R2",
        definition,
        (_node("R2", "1", "/VGEN", "A", 3), _node("R2", "2", "/GND", "K", 4)),
    )
    return ProjectSpiceModelBinding(
        *(_sha(str(index).encode()) for index in range(1)),
        parsed.digest,
        *(_sha(str(index).encode()) for index in range(1, 5)),
        (first, second),
    )


def _case(
    declaration: bytes, *, energized: tuple[str, ...] = ("vinrail",), loads: bool = True
) -> bytes:
    document: dict[str, object] = {
        "schema_version": "project-spice-operating-point-cases/v1",
        "declaration_digest": parse_electrical_inputs(declaration).digest,
        "project_capture_digest": _binding(declaration).project_capture_digest,
        "cases": [
            {
                "case_id": "case1",
                "temperature_millic": 25000,
                "ground": {"reference": "R1", "pin_number": "2"},
                "rail_nodes": [
                    {
                        "rail_id": "genrail",
                        "positive": {"reference": "R2", "pin_number": "1"},
                        "negative": {"reference": "R2", "pin_number": "2"},
                    },
                    {
                        "rail_id": "vinrail",
                        "positive": {"reference": "R1", "pin_number": "1"},
                        "negative": {"reference": "R1", "pin_number": "2"},
                    },
                ],
                "energized_rails": list(energized),
                "current_loads": (
                    [
                        {
                            "load_case_id": "loadgen",
                            "from": {"reference": "R2", "pin_number": "1"},
                            "to": {"reference": "R2", "pin_number": "2"},
                        }
                    ]
                    if loads
                    else []
                ),
            }
        ],
    }
    return json.dumps(document, separators=(",", ":")).encode()


def _resolve(payload: bytes | None = None):
    declaration = _declaration()
    return resolve_operating_point_cases(
        payload or _case(declaration),
        declaration,
        _binding(declaration),
        deadline=time.monotonic() + 10,
    )


def test_resolves_powered_generated_negative_and_zero_excitation_cases() -> None:
    result = _resolve()
    case = result.cases[0]
    assert case.ground_node == "0"
    assert [(rail.rail_id, rail.signed_voltage_uv, rail.energized) for rail in case.rails] == [
        ("genrail", -1200000, False),
        ("vinrail", 5000000, True),
    ]
    assert (
        case.loads[0].rail_id,
        case.loads[0].current_ua,
        case.loads[0].duration_ms,
        case.loads[0].from_node,
        case.loads[0].to_node,
    ) == ("genrail", 12000, 7, "/vgen", "0")
    zero = _resolve(_case(_declaration(), energized=(), loads=False))
    assert not any(rail.energized for rail in zero.cases[0].rails) and not zero.cases[0].loads
    assert repr(result) == "<OperatingPointCases redacted>" and "ngspice" not in repr(result)


@pytest.mark.parametrize(
    "mutate",
    (
        lambda document: document["cases"][0].pop("ground"),
        lambda document: document["cases"][0]["rail_nodes"].pop(),
        lambda document: document["cases"][0]["rail_nodes"][0].update(
            negative={"reference": "R1", "pin_number": "1"}
        ),
        lambda document: document["cases"][0].update(temperature_millic=90000),
        lambda document: document.update(declaration_digest=_sha(b"stale")),
        lambda document: document["cases"].append(document["cases"][0]),
    ),
)
def test_refuses_missing_wrong_pair_temperature_stale_and_duplicate(mutate) -> None:
    declaration = _declaration()
    document = json.loads(_case(declaration))
    mutate(document)
    with pytest.raises(OperatingPointCasesError) as error:
        _resolve(json.dumps(document, separators=(",", ":")).encode())
    assert error.value.__context__ is None


def test_refuses_nc_missing_pin_size_and_deadline() -> None:
    declaration = _declaration()
    binding = _binding(declaration)
    broken = replace(binding.references[0].pins[0].node, no_connect=True)
    binding = replace(
        binding,
        references=(
            replace(
                binding.references[0],
                pins=(
                    replace(binding.references[0].pins[0], node=broken),
                    binding.references[0].pins[1],
                ),
            ),
            binding.references[1],
        ),
    )
    with pytest.raises(OperatingPointCasesError):
        resolve_operating_point_cases(
            _case(declaration), declaration, binding, deadline=time.monotonic() + 10
        )
    missing = json.loads(_case(declaration))
    missing["cases"][0]["ground"]["pin_number"] = "99"
    with pytest.raises(OperatingPointCasesError):
        _resolve(json.dumps(missing, separators=(",", ":")).encode())
    wrong_pair = json.loads(_case(declaration))
    load = wrong_pair["cases"][0]["current_loads"][0]
    load["from"] = {"reference": "R1", "pin_number": "1"}
    load["to"] = {"reference": "R1", "pin_number": "2"}
    with pytest.raises(OperatingPointCasesError):
        _resolve(json.dumps(wrong_pair, separators=(",", ":")).encode())
    with pytest.raises(OperatingPointCasesError):
        _resolve(b"x" * (128 * 1024 + 1))
    with pytest.raises(OperatingPointCasesError) as error:
        resolve_operating_point_cases(
            _case(declaration), declaration, _binding(declaration), deadline=True
        )  # type: ignore[arg-type]
    assert error.value.__cause__ is None and error.value.__context__ is None


def test_negative_rail_reversed_load_is_retained_and_case_identity_changes() -> None:
    declaration = _declaration()
    reversed_load = json.loads(_case(declaration))
    load = reversed_load["cases"][0]["current_loads"][0]
    load["from"], load["to"] = load["to"], load["from"]
    reversed_result = _resolve(json.dumps(reversed_load, separators=(",", ":")).encode())
    baseline = _resolve()
    assert (
        reversed_result.cases[0].loads[0].from_node,
        reversed_result.cases[0].loads[0].to_node,
    ) == ("0", "/vgen")
    changed = json.loads(_case(declaration))
    changed["cases"][0]["temperature_millic"] = 26000
    changed["cases"][0]["energized_rails"] = []
    changed_result = _resolve(json.dumps(changed, separators=(",", ":")).encode())
    assert baseline.case_document_digest != reversed_result.case_document_digest
    assert baseline.case_document_digest != changed_result.case_document_digest


@pytest.mark.parametrize("field", ("energized_rails", "current_loads"))
def test_required_explicit_arrays_and_private_parser_redaction(field: str) -> None:
    from copper_mcp.engineering import _operating_point_cases as module

    declaration = _declaration()
    document = json.loads(_case(declaration))
    document["cases"][0].pop(field)
    with pytest.raises(OperatingPointCasesError) as error:
        _resolve(json.dumps(document, separators=(",", ":")).encode())
    assert error.value.__cause__ is None and error.value.__context__ is None
    parsed = module._parse(_case(declaration), time.monotonic() + 10)
    assert repr(parsed) == str(parsed) == "<_CasesDocument redacted>"


def test_document_hash_rechecks_deadline(monkeypatch: pytest.MonkeyPatch) -> None:
    result = _resolve()
    from copper_mcp.engineering import _operating_point_cases as module

    parsed = module._parse(_case(_declaration()), time.monotonic() + 10)
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 1.0))
    with pytest.raises(OperatingPointCasesError) as error:
        module._document_digest(parsed, 1.0)
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert result.case_document_digest
