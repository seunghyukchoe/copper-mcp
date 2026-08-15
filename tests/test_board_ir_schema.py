from __future__ import annotations

import hashlib
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
# The directory names the era the boards under it were authored in, not the envelope version its
# golden carries.  `board-ir-v0.1/subset.kicad_pcb` has been the source board for the active
# golden since `0.2.0`, and the active golden in `board-ir-v0.2/` is now a `0.4.0`
# envelope.  Renaming would move a dozen `.kicad_pcb` paths that no version bump touches.
ACTIVE_FIXTURE_ROOT = TEST_ROOT / "fixtures" / "board-ir-v0.2"
LEGACY_FIXTURE_ROOT = TEST_ROOT / "fixtures" / "board-ir-v0.1"
SCHEMA_ROOT = TEST_ROOT.parent / "schemas" / "board-ir"
SCHEMA_PATH = SCHEMA_ROOT / "0.4.0.schema.json"
LEGACY_SCHEMA_PATH = SCHEMA_ROOT / "0.1.0.schema.json"
# `0.2.0` is byte-frozen by ADR-0105 and no longer the accepted set for a new document.  It is
# kept, and checked below, as the copy a consumer of a v0.5.0-v0.8.0 release is holding.
FROZEN_V0_2_0_SCHEMA_PATH = SCHEMA_ROOT / "0.2.0.schema.json"
FROZEN_V0_3_0_SCHEMA_PATH = SCHEMA_ROOT / "0.3.0.schema.json"
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

    # Moved from `schema.invalid` by ADR-0105, and for the same reason a persisted `0.2.0`
    # envelope moved: a `0.1` document is well-formed Board IR at a superseded version, not
    # malformed bytes. The remedy the message states -- re-convert from the source board -- is
    # exactly what `docs/migrations/board-ir-0.2.md` has always required.
    assert caught.value.code == "schema.version"


# The `0.2.0`-as-published envelope for the subset board, digested at `v0.8.0`.  ADR-0105 froze
# `0.2.0` rather than correcting it, so this artifact is not committed a second time: it is
# recovered from the active golden by substituting the version string, which is the whole of what
# the bump moved.  `git show v0.8.0:tests/fixtures/board-ir-v0.2/schema-valid.json | shasum -a 256`
# reproduces it.
PUBLISHED_V0_2_0_ENVELOPE_SHA256 = (
    "3a4edf3732624c836860a112d3d060778b6e6b28f3ee6fc5f8863eeacba0efe6"
)
PUBLISHED_V0_2_0_ENVELOPE_BYTES = 4_280


def test_the_version_bump_moved_the_envelope_by_its_version_string_and_nothing_else() -> None:
    """ADR-0105, proved by construction rather than by a second committed fixture.

    Substituting `0.2.0` back into the active golden must reproduce the `0.2.0`-as-published
    bytes exactly.  If any other byte had moved the substitution would not reach the recorded
    digest, so this is the whole claim and not a sample of it.  `"0.2.0"` and `"0.3.0"` are the
    same width, which is why the byte count is unchanged as well.
    """

    active = VALID_FIXTURE.read_bytes()

    assert active.count(b'"0.4.0"') == 1
    published = active.replace(b'"0.4.0"', b'"0.2.0"')

    assert len(published) == len(active) == PUBLISHED_V0_2_0_ENVELOPE_BYTES
    assert hashlib.sha256(published).hexdigest() == PUBLISHED_V0_2_0_ENVELOPE_SHA256


# The exact bytes of the two frozen published schemas, as shipped at `v0.8.0`.
#
# **The accepted-set gate cannot enforce a byte freeze and this pin exists because it was proved
# it cannot.** Adversarial review of PR #181 rewrote all 2,046 lines of `0.2.0.schema.json` --
# reindented, every multi-member `enum` reversed -- and the gate plus all 107 schema tests stayed
# green, correctly, because the *accepted set* had not moved. ADR-0105 claims byte permanence, and
# byte permanence needs a byte pin. It also closes the two other silent routes the ADR's first
# draft missed: an accepted-set-neutral rewrite, and an edit to a keyword the gate does not watch
# (`pattern`, `maximum`, `$ref`).
#
# `git show v0.8.0:schemas/board-ir/<name> | shasum -a 256` reproduces each.
FROZEN_SCHEMA_DIGESTS = {
    "0.1.0.schema.json": (
        "4f8652ce217129749e1ea968c9672ce728c4755311779c95904a28f848edf6da",
        16_773,
    ),
    "0.2.0.schema.json": (
        "7653de48a5b289bf671b44770f32d6bc7b2df7d5d653c74f38bc407168029c3c",
        24_040,
    ),
    "0.3.0.schema.json": (
        "37d395c7491824c9568b06ab91e01bc2062204305720d77ef18a135ff432a486",
        24_040,
    ),
}


@pytest.mark.parametrize("name", sorted(FROZEN_SCHEMA_DIGESTS))
def test_a_frozen_published_schema_keeps_the_exact_bytes_it_shipped_with(name: str) -> None:
    """Byte permanence, pinned rather than described.

    Both digests and byte counts, for the reason `tests/test_golden_identities.py` gives: two
    payloads differing only inside one fixed-width field have the same length, so a length
    assertion alone misses it -- but a reindent or a reordered member moves the length, so the
    pair says *which* kind of drift happened rather than only that a hex string moved.

    Updating a value here to make a red test green is the failure this pin exists to prevent.
    `0.2.0` is frozen by ADR-0105 and `0.1.0` by the `0.1` -> `0.2` migration; neither may move
    again for any reason, including a purely cosmetic one.
    """

    digest, byte_count = FROZEN_SCHEMA_DIGESTS[name]
    raw = (SCHEMA_ROOT / name).read_bytes()

    assert len(raw) == byte_count
    assert hashlib.sha256(raw).hexdigest() == digest


def test_the_active_schema_is_not_pinned_by_byte_and_says_why() -> None:
    """The pin above is deliberately not extended to `0.4.0`, and that is a decision.

    A frozen schema may not change at all, so its bytes are the contract. The **active** schema
    is expected to change -- that is what a version bump is for -- and pinning its bytes would
    turn every legitimate edit into a pin update, which is how a pin becomes a rubber stamp.
    What guards the active schema is `scripts/check_schema_sets.py`: it may change only with its
    declared version.
    """

    assert SCHEMA_PATH.name not in FROZEN_SCHEMA_DIGESTS
    assert FROZEN_V0_2_0_SCHEMA_PATH.name in FROZEN_SCHEMA_DIGESTS
    assert FROZEN_V0_3_0_SCHEMA_PATH.name in FROZEN_SCHEMA_DIGESTS


def test_the_frozen_v0_2_0_schema_still_accepts_the_envelope_it_was_published_beside() -> None:
    """The freeze is a promise to a consumer holding the `0.5.0`-`0.8.0` copy, so it is checked.

    ADR-0105 declined to correct `0.2.0` retroactively.  That is only worth anything if the
    frozen copy still accepts the documents it accepted when it shipped.
    """

    published = VALID_FIXTURE.read_bytes().replace(b'"0.4.0"', b'"0.2.0"')
    frozen = Draft202012Validator(_load_json(FROZEN_V0_2_0_SCHEMA_PATH))

    assert list(frozen.iter_errors(json.loads(published))) == []
    # And the active schema rejects it, on the version and only on the version.
    errors = list(_validator().iter_errors(json.loads(published)))
    assert [(error.validator, list(error.absolute_path)) for error in errors] == [
        ("const", ["schema_version"])
    ]


def test_the_frozen_v0_3_0_schema_still_accepts_the_envelope_it_was_published_beside() -> None:
    """The prior active schema and its ordinary-pad envelope are immutable after 0.4.0."""

    published = VALID_FIXTURE.read_bytes().replace(b'"0.4.0"', b'"0.3.0"')
    frozen = Draft202012Validator(_load_json(FROZEN_V0_3_0_SCHEMA_PATH))

    assert list(frozen.iter_errors(json.loads(published))) == []
    errors = list(_validator().iter_errors(json.loads(published)))
    assert [(error.validator, list(error.absolute_path)) for error in errors] == [
        ("const", ["schema_version"])
    ]


def test_the_codec_refuses_a_persisted_v0_2_0_envelope_with_a_discriminated_code() -> None:
    """The largest real cost of ADR-0105's bump, pinned rather than described.

    Anyone storing `0.2.0` snapshots must re-convert from the source board; the decoder will not
    read them.  The refusal is `schema.version`, **not** `schema.invalid`, and the distinction is
    the point: this document conforms to `0.2.0`-as-published exactly and is refused *because*
    that version is superseded.  Reporting it as malformed -- which the first draft of ADR-0105
    did, under a message reading "JSON does not conform to Board IR v0.2" -- states the opposite
    of the truth on the one path the version bump created.

    The message is asserted, not just the code: a caller reading it must be told to re-convert,
    and must be told which version this build accepts.
    """

    persisted = VALID_FIXTURE.read_bytes().replace(b'"0.4.0"', b'"0.2.0"')

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(persisted)

    assert caught.value.code == "schema.version"
    assert caught.value.source_locator == "snapshot.schema_version"
    assert caught.value.message == (
        "Board IR envelope declares a superseded or unknown schema version; this build accepts "
        "0.4.0 only, and an envelope at any other version must be re-converted from its source "
        "board"
    )


def test_the_version_refusal_never_echoes_the_version_the_document_declared() -> None:
    """A declared version is caller-controlled, so no diagnostic may repeat it.

    Naming the *found* version would be the friendlier message and is exactly the thing the
    adapter's own rule forbids: a refusal names the field, never the bytes in it.
    """

    secret = "9." + "7" * 200 + ".0"
    forged = VALID_FIXTURE.read_bytes().replace(b'"0.4.0"', f'"{secret}"'.encode())

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(forged)

    assert caught.value.code == "schema.version"
    assert secret not in caught.value.message
    assert "7777" not in str(caught.value)


def test_the_version_code_separates_a_stale_version_from_bytes_that_are_not_board_ir() -> None:
    """Both halves of the discrimination, because one half alone proves nothing.

    A code that fires on everything is not a discriminated code.
    """

    not_board_ir = b'{"schema":"something.else","schema_version":"0.4.0",'
    not_board_ir += b'"snapshot_digest":"sha256:' + b"0" * 64 + b'","content":{}}'

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(not_board_ir)
    assert caught.value.code == "schema.invalid"

    with pytest.raises(BoardIRValidationError) as caught:
        decode_snapshot_json(b"not json at all")
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


def test_schema_accepts_and_closes_the_custom_pad_copper_envelope() -> None:
    payload = deepcopy(_load_json(VALID_FIXTURE))
    pad = payload["content"]["items"]["pads"][0]
    pad["copper_envelope"] = {
        "min_x_nm": -1_000_000,
        "min_y_nm": -500_000,
        "max_x_nm": 4_000_000,
        "max_y_nm": 500_000,
    }

    assert list(_validator().iter_errors(payload)) == []

    pad["copper_envelope"]["unexpected"] = True
    assert any(
        error.validator == "additionalProperties" for error in _validator().iter_errors(payload)
    )


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
