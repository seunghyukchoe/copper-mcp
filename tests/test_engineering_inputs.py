"""Electrical-input declarations are private metadata, never a simulation or signoff result."""

from __future__ import annotations

import json

import pytest

from copper_mcp.engineering.inputs import (
    ElectricalInputs,
    OptimizationError,
    assess_completeness,
    parse_electrical_inputs,
    redacted_projection,
)


def digest(character: str) -> str:
    return "sha256:" + character * 64


def document() -> dict[str, object]:
    return {
        "schema_version": "electrical-inputs/v1",
        "board_revision": digest("a"),
        "snapshot_digest": digest("b"),
        "project_context_digest": digest("c"),
        "profile_id": "mcu-sensor-v1",
        "source_artifacts": [
            {"artifact_id": "bom-main", "role": "bom", "artifact_digest": digest("d")},
            {"artifact_id": "models-main", "role": "model-library", "artifact_digest": digest("0")},
            {"artifact_id": "netlist-main", "role": "netlist", "artifact_digest": digest("e")},
            {"artifact_id": "schematic-main", "role": "schematic", "artifact_digest": digest("f")},
        ],
        "bom_bindings": [
            {"item_id": "u1", "artifact_id": "bom-main", "quantity": 1, "power_dissipation_uw": 100}
        ],
        "model_bindings": [
            {
                "model_id": "u1-ibis",
                "bom_item_id": "u1",
                "artifact_id": "models-main",
                "model_kind": "ibis",
                "model_digest": digest("1"),
            },
            {
                "model_id": "u1-spice",
                "bom_item_id": "u1",
                "artifact_id": "models-main",
                "model_kind": "spice",
                "model_digest": digest("2"),
            },
            {
                "model_id": "u1-thermal",
                "bom_item_id": "u1",
                "artifact_id": "models-main",
                "model_kind": "thermal",
                "model_digest": digest("3"),
            },
        ],
        "stackup": [
            {
                "physical_index": 0,
                "kind": "copper",
                "material_id": "fr4",
                "thickness_nm": 35_000,
                "copper_thickness_nm": 35_000,
                "relative_permittivity_ppm": 4_200_000,
                "thermal_conductivity_uw_per_mk": 300_000,
                "loss_tangent_ppm": 20_000,
            },
            {
                "physical_index": 1,
                "kind": "dielectric",
                "material_id": "fr4",
                "thickness_nm": 1_500_000,
                "copper_thickness_nm": 0,
                "relative_permittivity_ppm": 4_200_000,
                "thermal_conductivity_uw_per_mk": 300_000,
                "loss_tangent_ppm": 20_000,
            },
            {
                "physical_index": 2,
                "kind": "copper",
                "material_id": "fr4",
                "thickness_nm": 35_000,
                "copper_thickness_nm": 35_000,
                "relative_permittivity_ppm": 4_200_000,
                "thermal_conductivity_uw_per_mk": 300_000,
                "loss_tangent_ppm": 20_000,
            },
        ],
        "rails": [{"rail_id": "vdd", "nominal_voltage_uv": 3_300_000}],
        "load_cases": [
            {"case_id": "active", "rail_id": "vdd", "current_ua": 20_000, "duration_ms": 100}
        ],
        "edge_rates": [
            {
                "signal_id": "spi-sck",
                "rail_id": "vdd",
                "model_id": "u1-ibis",
                "rise_time_ps": 1_000,
                "fall_time_ps": 1_000,
            }
        ],
        "operating_limits": {
            "min_temperature_millic": -40_000,
            "max_temperature_millic": 85_000,
            "max_input_voltage_uv": 5_000_000,
        },
        "thermal_boundary": {
            "ambient_temperature_millic": 25_000,
            "convection_uw_per_k": 1_000,
            "enclosure_to_ambient_uk_per_w": 10_000,
        },
    }


def payload(value: dict[str, object]) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode("ascii")


def test_canonical_digest_and_complete_inputs_are_inputs_only():
    first = parse_electrical_inputs(payload(document()))
    second = ElectricalInputs.model_validate_json(payload(document()))
    assert first.digest == second.digest
    assessment = assess_completeness(first)
    assert all(row.complete for row in assessment.domains)
    assert assessment.assessment_state == "inputs_only"
    assert "pass" not in assessment.document().values()


def test_one_valid_edge_model_cannot_cover_an_unrelated_invalid_edge_model():
    value_document = document()
    value_document["edge_rates"].append(
        {
            **value_document["edge_rates"][0],
            "signal_id": "spi-sck2",
            "model_id": "u1-thermal",
        }
    )
    rows = {
        row.domain: row
        for row in assess_completeness(parse_electrical_inputs(payload(value_document))).domains
    }
    assert not rows["SI"].complete and not rows["EMC"].complete
    assert "missing_suitable_model" in rows["SI"].missing_reasons


def test_scaled_inputs_can_express_negative_rails_and_conductive_metal_layers():
    value_document = document()
    value_document["rails"][0]["nominal_voltage_uv"] = -15_000_000
    value_document["stackup"][0]["thermal_conductivity_uw_per_mk"] = 400_000_000
    parsed = parse_electrical_inputs(payload(value_document))
    assert parsed.rails[0].nominal_voltage_uv == -15_000_000
    assert parsed.stackup[0].thermal_conductivity_uw_per_mk == 400_000_000


def test_two_disagreeing_copper_thicknesses_are_refused():
    value_document = document()
    value_document["stackup"][0]["copper_thickness_nm"] = 70_000
    with pytest.raises(OptimizationError):
        parse_electrical_inputs(payload(value_document))


@pytest.mark.parametrize(
    "raw",
    [
        b'{"schema_version":"electrical-inputs/v1","schema_version":"electrical-inputs/v1"}',
        b'{"value":NaN}',
        b'{"value":Infinity}',
    ],
)
def test_malformed_or_nonfinite_json_is_rejected(raw):
    with pytest.raises(OptimizationError, match="malformed"):
        parse_electrical_inputs(raw)


@pytest.mark.parametrize(
    "path,value",
    [
        (("bom_bindings", 0, "quantity"), True),
        (("stackup", 0, "thickness_nm"), True),
        (("edge_rates", 0, "rise_time_ps"), 1.5),
    ],
)
def test_scaled_units_reject_bool_and_float(path, value):
    value_document = document()
    target = value_document
    for part in path[:-1]:
        target = target[part]  # type: ignore[index]
    target[path[-1]] = value  # type: ignore[index]
    with pytest.raises(OptimizationError, match="malformed"):
        parse_electrical_inputs(payload(value_document))


def test_duplicate_identifiers_and_reference_mismatches_are_rejected():
    duplicate = document()
    duplicate["rails"] = [
        {"rail_id": "vdd", "nominal_voltage_uv": 3_300_000},
        {"rail_id": "vdd", "nominal_voltage_uv": 1_800_000},
    ]
    mismatch = document()
    mismatch["model_bindings"] = [{**mismatch["model_bindings"][0], "bom_item_id": "missing"}]  # type: ignore[index]
    for value_document in (duplicate, mismatch):
        with pytest.raises(OptimizationError, match="malformed"):
            parse_electrical_inputs(payload(value_document))


def test_incomplete_inputs_report_fixed_missing_reasons_without_authority():
    value_document = document()
    for key in ("bom_bindings", "model_bindings", "stackup", "rails", "load_cases", "edge_rates"):
        value_document[key] = []
    value_document["operating_limits"] = None
    value_document["thermal_boundary"] = None
    assessment = assess_completeness(parse_electrical_inputs(payload(value_document)))
    rows = {row.domain: row for row in assessment.domains}
    assert rows["PI"].missing_reasons == (
        "missing_operating_limit",
        "missing_rail_load_case",
        "missing_stackup",
        "missing_suitable_model",
    )
    assert not any(row.complete for row in assessment.domains)
    assert assessment.assessment_state == "inputs_only"


def test_projection_redacts_all_identifiers_and_authority_claims():
    projection = redacted_projection(parse_electrical_inputs(payload(document())))
    rendered = projection.document()
    assert rendered["package_digest"] == parse_electrical_inputs(payload(document())).digest
    assert rendered["source_artifact_count"] == 4
    assert "u1" not in json.dumps(rendered)
    assert "models" not in json.dumps(rendered)
    assert "pass" not in json.dumps(rendered)


def test_absent_erc_artifacts_and_wrong_models_are_incomplete_not_evidence():
    value_document = document()
    value_document["source_artifacts"] = [
        artifact
        for artifact in value_document["source_artifacts"]  # type: ignore[index]
        if artifact["role"] != "schematic"  # type: ignore[index]
    ]
    value_document["model_bindings"] = [
        {
            "model_id": "u1-thermal",
            "bom_item_id": "u1",
            "artifact_id": "models-main",
            "model_kind": "thermal",
            "model_digest": digest("3"),
        }
    ]
    value_document["edge_rates"][0]["model_id"] = "u1-thermal"  # type: ignore[index]
    rows = {
        row.domain: row
        for row in assess_completeness(parse_electrical_inputs(payload(value_document))).domains
    }
    assert "missing_schematic_artifact" in rows["ERC"].missing_reasons
    assert "missing_suitable_model" in rows["SI"].missing_reasons
    assert "missing_suitable_model" in rows["PI"].missing_reasons
    assert "missing_suitable_model" in rows["EMC"].missing_reasons


def test_partial_rail_load_and_material_properties_remain_incomplete():
    value_document = document()
    value_document["rails"].append({"rail_id": "vref", "nominal_voltage_uv": 1_800_000})  # type: ignore[index]
    value_document["stackup"][1]["thermal_conductivity_uw_per_mk"] = None  # type: ignore[index]
    value_document["stackup"][1]["loss_tangent_ppm"] = None  # type: ignore[index]
    rows = {
        row.domain: row
        for row in assess_completeness(parse_electrical_inputs(payload(value_document))).domains
    }
    assert "missing_rail_load_case" in rows["PI"].missing_reasons
    assert "missing_thermal_conductivity" in rows["thermal"].missing_reasons
    assert "missing_loss_tangent" in rows["EMC"].missing_reasons


def test_invalid_stackup_order_is_rejected():
    value_document = document()
    value_document["stackup"][1]["physical_index"] = 2  # type: ignore[index]
    with pytest.raises(OptimizationError, match="malformed"):
        parse_electrical_inputs(payload(value_document))
