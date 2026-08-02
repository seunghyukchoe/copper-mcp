from __future__ import annotations

import json
from collections.abc import Callable
from copy import deepcopy
from pathlib import Path
from typing import Any, cast

import pytest
from jsonschema import Draft202012Validator  # type: ignore[import-untyped]

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import (
    BoardIRValidationError,
    NetClass,
    decode_snapshot_json,
    encode_snapshot,
)

TEST_ROOT = Path(__file__).parent
FIXTURE_ROOT = TEST_ROOT / "fixtures" / "board-ir-v0.1"
SCHEMA_PATH = TEST_ROOT.parent / "schemas" / "board-ir" / "0.1.0.schema.json"
VALID_FIXTURE = FIXTURE_ROOT / "schema-valid.json"
INVALID_FIXTURE = FIXTURE_ROOT / "schema-invalid.json"
SUBSET_BOARD = FIXTURE_ROOT / "subset.kicad_pcb"


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_load_json(SCHEMA_PATH))


def _fixture_profile() -> KiCadConstraintProfile:
    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    audio = NetClass(
        id="class:audio",
        name="Audio",
        clearance_nm=300_000,
        track_width_nm=300_000,
        via_diameter_nm=900_000,
        via_drill_nm=450_000,
    )
    return KiCadConstraintProfile(
        net_classes=(default, audio),
        default_net_class_id=default.id,
        net_class_by_name=(("SIG_µ", audio.id),),
    )


def test_board_ir_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_load_json(SCHEMA_PATH))


def test_valid_golden_snapshot_satisfies_schema_and_codec() -> None:
    encoded = VALID_FIXTURE.read_bytes()
    payload = json.loads(encoded)

    assert list(_validator().iter_errors(payload)) == []
    assert encode_snapshot(decode_snapshot_json(encoded)) == encoded


def test_adapter_output_matches_golden_fixture_and_schema() -> None:
    result = parse_kicad_bytes(SUBSET_BOARD.read_bytes(), _fixture_profile())

    assert result.diagnostics == ()
    assert result.snapshot is not None
    encoded = encode_snapshot(result.snapshot)
    assert encoded == VALID_FIXTURE.read_bytes()
    assert list(_validator().iter_errors(json.loads(encoded))) == []


def test_invalid_fixture_is_rejected_by_schema_and_runtime_decoder() -> None:
    payload = _load_json(INVALID_FIXTURE)
    errors = list(_validator().iter_errors(payload))

    assert errors
    assert any(
        error.validator == "additionalProperties" and "unexpected" in error.message
        for error in errors
    )
    with pytest.raises(BoardIRValidationError):
        decode_snapshot_json(INVALID_FIXTURE.read_bytes())


@pytest.mark.parametrize(
    "mutate",
    [
        lambda payload: payload["content"]["items"]["segments"][0].update({"unknown": True}),
        lambda payload: payload["content"]["items"]["segments"][0]["start"].update({"x_nm": 0.5}),
    ],
)
def test_schema_closes_nested_objects_and_requires_integer_nanometres(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    mutate(payload)

    assert list(_validator().iter_errors(payload))


def test_schema_enforces_pad_kind_drill_and_npth_net_rules() -> None:
    without_drill = deepcopy(_load_json(VALID_FIXTURE))
    smd_pad = next(pad for pad in without_drill["content"]["items"]["pads"] if pad["kind"] == "smd")
    smd_pad["kind"] = "through_hole"
    assert list(_validator().iter_errors(without_drill))

    connected_npth = deepcopy(_load_json(VALID_FIXTURE))
    through_pad = next(
        pad for pad in connected_npth["content"]["items"]["pads"] if pad["kind"] == "through_hole"
    )
    through_pad["kind"] = "np_through_hole"
    assert through_pad["net_id"] is not None
    assert list(_validator().iter_errors(connected_npth))


def test_schema_requires_positive_dimensions_for_thermal_zone_connections() -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    zone = payload["content"]["items"]["zones"][0]
    assert zone["pad_connection"] == "thermal"
    zone["thermal_gap_nm"] = 0
    zone["thermal_bridge_width_nm"] = 0

    assert list(_validator().iter_errors(payload))


def test_schema_and_runtime_both_reject_more_than_64_copper_layers() -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    layers = payload["content"]["copper_layers"]
    for index in range(2, 65):
        layers.append(
            {
                "id": f"layer:L{index}.Cu",
                "name": f"L{index}.Cu",
                "index": index,
                "kind": "signal",
            }
        )

    assert list(_validator().iter_errors(payload))
    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(json.dumps(payload).encode())

    assert caught.value.code == "schema.limit"
