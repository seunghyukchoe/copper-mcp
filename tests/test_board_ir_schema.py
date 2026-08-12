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
ACTIVE_FIXTURE_ROOT = TEST_ROOT / "fixtures" / "board-ir-v0.2"
LEGACY_FIXTURE_ROOT = TEST_ROOT / "fixtures" / "board-ir-v0.1"
SCHEMA_ROOT = TEST_ROOT.parent / "schemas" / "board-ir"
SCHEMA_PATH = SCHEMA_ROOT / "0.2.0.schema.json"
LEGACY_SCHEMA_PATH = SCHEMA_ROOT / "0.1.0.schema.json"
VALID_FIXTURE = ACTIVE_FIXTURE_ROOT / "schema-valid.json"
INVALID_FIXTURE = ACTIVE_FIXTURE_ROOT / "schema-invalid.json"
LEGACY_VALID_FIXTURE = LEGACY_FIXTURE_ROOT / "schema-valid.json"
SUBSET_BOARD = LEGACY_FIXTURE_ROOT / "subset.kicad_pcb"
FAR_SIDE_BOARD = ACTIVE_FIXTURE_ROOT / "courtyard-far-side.kicad_pcb"


def _load_json(path: Path) -> dict[str, Any]:
    return cast(dict[str, Any], json.loads(path.read_text(encoding="utf-8")))


def _validator() -> Draft202012Validator:
    return Draft202012Validator(_load_json(SCHEMA_PATH))


def _legacy_validator() -> Draft202012Validator:
    return Draft202012Validator(_load_json(LEGACY_SCHEMA_PATH))


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


def test_active_board_ir_schema_is_valid_draft_2020_12() -> None:
    Draft202012Validator.check_schema(_load_json(SCHEMA_PATH))


def test_legacy_v0_1_schema_remains_valid_and_accepts_its_golden_snapshot() -> None:
    schema = _load_json(LEGACY_SCHEMA_PATH)
    payload = _load_json(LEGACY_VALID_FIXTURE)

    Draft202012Validator.check_schema(schema)
    assert schema["properties"]["schema_version"]["const"] == "0.1.0"
    assert "footprints" not in schema["$defs"]["content"]["properties"]["items"]["properties"]
    assert list(_legacy_validator().iter_errors(payload)) == []


def test_active_schema_and_decoder_reject_legacy_v0_1_snapshot() -> None:
    encoded = LEGACY_VALID_FIXTURE.read_bytes()
    payload = json.loads(encoded)

    assert list(_legacy_validator().iter_errors(payload)) == []
    assert list(_validator().iter_errors(payload))
    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(encoded)

    assert caught.value.code == "schema.invalid"


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


@pytest.mark.parametrize(
    "mutate",
    [
        lambda footprint: footprint.update({"id": "pad:not-a-footprint"}),
        lambda footprint: footprint["origin"].update({"x_nm": 0.5}),
        lambda footprint: footprint.update({"rotation_udeg": 360_000_000}),
        lambda footprint: footprint.update({"side": "inner"}),
        lambda footprint: footprint.update({"locked": 1}),
        lambda footprint: footprint.update({"unexpected": True}),
    ],
)
def test_schema_closes_footprints_and_enforces_pose_types(
    mutate: Callable[[dict[str, Any]], None],
) -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    footprint = payload["content"]["items"]["footprints"][0]
    mutate(footprint)

    assert list(_validator().iter_errors(payload))


def test_schema_requires_footprints_as_a_total_items_collection() -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    del payload["content"]["items"]["footprints"]

    errors = list(_validator().iter_errors(payload))

    assert any(error.validator == "required" and "footprints" in error.message for error in errors)


def test_schema_enforces_unique_pad_ids_and_at_most_64_courtyards() -> None:
    duplicate_pad = deepcopy(_load_json(VALID_FIXTURE))
    footprint = duplicate_pad["content"]["items"]["footprints"][0]
    assert footprint["pad_ids"]
    footprint["pad_ids"].append(footprint["pad_ids"][0])
    assert any(
        error.validator == "uniqueItems" for error in _validator().iter_errors(duplicate_pad)
    )

    too_many_courtyards = deepcopy(_load_json(VALID_FIXTURE))
    footprint = too_many_courtyards["content"]["items"]["footprints"][0]
    courtyard = deepcopy(too_many_courtyards["content"]["outline"]["contours"][0]["outer"])
    footprint["courtyards"] = [deepcopy(courtyard) for _ in range(65)]
    assert any(
        error.validator == "maxItems" for error in _validator().iter_errors(too_many_courtyards)
    )


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


def test_schema_accepts_an_emitted_far_side_courtyard_payload_and_closes_it() -> None:
    """The far-side keys are proved by a payload the adapter really emits, not by a fixture.

    A schema that omits a field the code emits makes a true payload fail; a schema that declares
    it without its constraints lets a false one pass (R-137). Both directions are checked here:
    the emitted payload validates, an empty array does not - the encoder omits the key rather
    than emitting `[]`, so an empty one is not a payload this project produces - and the object
    stays closed against a near-miss key name.
    """

    default = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=250_000,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    profile = KiCadConstraintProfile(net_classes=(default,), default_net_class_id=default.id)
    result = parse_kicad_bytes(FAR_SIDE_BOARD.read_bytes(), profile)
    assert result.diagnostics == ()
    assert result.snapshot is not None
    payload = json.loads(encode_snapshot(result.snapshot))

    assert list(_validator().iter_errors(payload)) == []
    carrier = next(
        footprint
        for footprint in payload["content"]["items"]["footprints"]
        if "far_side_courtyards" in footprint
    )
    assert len(carrier["far_side_courtyards"]) == 1

    emptied = deepcopy(payload)
    next(
        footprint
        for footprint in emptied["content"]["items"]["footprints"]
        if "far_side_courtyards" in footprint
    )["far_side_courtyards"] = []
    assert any(error.validator == "minItems" for error in _validator().iter_errors(emptied))

    misspelled = deepcopy(payload)
    footprint = next(
        item
        for item in misspelled["content"]["items"]["footprints"]
        if "far_side_courtyards" in item
    )
    footprint["far_side_courtyard"] = footprint.pop("far_side_courtyards")
    assert any(
        error.validator == "additionalProperties" for error in _validator().iter_errors(misspelled)
    )
