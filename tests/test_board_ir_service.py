from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from copper_mcp.board_ir_service import (
    BoardIrError,
    BoardIrSummary,
    parse_board_ir_request,
    summarize_board_ir,
)
from copper_mcp.config import Settings

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"


def _request(**overrides: Any) -> dict[str, Any]:
    request: dict[str, Any] = {
        "board": "two-pad.kicad_pcb",
        "constraints": {
            "clearance_nm": 250_000,
            "track_width_nm": 250_000,
            "via_diameter_nm": 800_000,
            "via_drill_nm": 400_000,
        },
    }
    request.update(overrides)
    return request


def _workspace(tmp_path: Path, *, source: bytes | None = None) -> tuple[Path, Settings]:
    board = tmp_path / "two-pad.kicad_pcb"
    board.write_bytes(source if source is not None else FIXTURE.read_bytes())
    return board, Settings(workspace=tmp_path)


def _entries(root: Path) -> dict[str, tuple[int, int, bytes]]:
    return {
        str(path.relative_to(root)): (
            path.stat().st_ino,
            path.stat().st_mtime_ns,
            path.read_bytes(),
        )
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def test_describes_a_supported_board_without_disclosing_content(tmp_path: Path) -> None:
    board, settings = _workspace(tmp_path)
    before = _entries(tmp_path)

    summary = summarize_board_ir(_request(), settings)

    assert summary.supported is True
    assert summary.board_revision == f"sha256:{hashlib.sha256(board.read_bytes()).hexdigest()}"
    assert summary.snapshot_digest is not None
    assert summary.snapshot_digest != summary.board_revision
    assert summary.constraint_digest is not None
    # A one-value literal, not `BOARD_IR_SCHEMA_VERSION`: this asserts what the MCP surface
    # publishes, so importing the constant would make the assertion agree with itself.
    assert summary.ir_schema_version == "0.4.0"
    assert summary.distance_unit == "nm"
    assert summary.angle_unit == "udeg"
    assert summary.copper_layer_ids == ("layer:B.Cu", "layer:F.Cu")
    assert summary.object_counts["pads"] == 2
    assert summary.object_counts["nets"] == 1
    assert summary.object_counts["segments"] == 0
    assert dict(summary.conversion_diagnostic_counts) == {}
    assert _entries(tmp_path) == before


def test_summary_never_returns_names_coordinates_or_identities(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    document = summarize_board_ir(_request(), settings).to_dict()

    flattened = repr(document)
    assert "AUDIO" not in flattened
    assert "20000000-0000-0000-0000" not in flattened
    assert "pad:" not in flattened
    assert "net:" not in flattened


def test_summary_is_deterministic_and_detached(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    summary = summarize_board_ir(_request(), settings)
    document = summary.to_dict()
    document["supported"] = False
    document["object_counts"]["pads"] = 99

    assert summarize_board_ir(_request(), settings).to_dict() == summary.to_dict()
    assert summary.to_dict()["supported"] is True
    assert summary.to_dict()["object_counts"]["pads"] == 2
    with pytest.raises(TypeError):
        summary.object_counts["injected"] = 1  # type: ignore[index]


def test_reports_an_unsupported_board_as_diagnostic_codes(tmp_path: Path) -> None:
    unsupported = FIXTURE.read_bytes().replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    _, settings = _workspace(tmp_path, source=unsupported)

    summary = summarize_board_ir(_request(), settings)

    assert summary.supported is False
    assert summary.snapshot_digest is None
    assert summary.constraint_digest is None
    assert summary.copper_layer_ids == ()
    assert dict(summary.object_counts) == {}
    assert dict(summary.conversion_diagnostic_counts) == {"geometry.missing": 1}


def test_constraints_change_the_snapshot_digest(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    default = summarize_board_ir(_request(), settings)
    widened = summarize_board_ir(
        _request(
            constraints={
                "clearance_nm": 300_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            }
        ),
        settings,
    )

    assert default.board_revision == widened.board_revision
    assert default.constraint_digest != widened.constraint_digest
    assert default.snapshot_digest != widened.snapshot_digest


def test_rejects_boards_outside_the_workspace(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)

    with pytest.raises(ValueError, match="workspace"):
        summarize_board_ir(_request(board="../two-pad.kicad_pcb"), settings)


@pytest.mark.parametrize(
    "payload",
    [
        [],
        "not-an-object",
        {"constraints": {}},
        {"board": "two-pad.kicad_pcb"},
        _request(net="AUDIO"),
        _request(board=""),
        _request(board="two-pad\x00.kicad_pcb"),
        _request(constraints={"clearance_nm": 1}),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": True,
                "via_diameter_nm": 800_000,
                "via_drill_nm": 400_000,
            }
        ),
        _request(
            constraints={
                "clearance_nm": 250_000,
                "track_width_nm": 250_000,
                "via_diameter_nm": 400_000,
                "via_drill_nm": 800_000,
            }
        ),
    ],
)
def test_rejects_malformed_requests(payload: Any) -> None:
    with pytest.raises(BoardIrError):
        parse_board_ir_request(payload)


def test_errors_never_echo_caller_supplied_field_names() -> None:
    secret = "y" * 5000 + "-internal-project-codename"

    with pytest.raises(BoardIrError) as raised:
        parse_board_ir_request(_request(**{secret: 1}))

    message = str(raised.value)
    assert secret not in message
    assert "internal-project-codename" not in message
    assert len(message) < 500
    assert "1 unsupported field" in message


def test_summary_record_rejects_inconsistent_states(tmp_path: Path) -> None:
    _, settings = _workspace(tmp_path)
    summary = summarize_board_ir(_request(), settings)

    with pytest.raises(BoardIrError, match="must describe its Board IR snapshot"):
        BoardIrSummary(
            board_path=summary.board_path,
            board_revision=summary.board_revision,
            supported=True,
            request=summary.request,
            object_counts={"pads": 2},
            copper_layer_ids=("layer:F.Cu",),
        )
    with pytest.raises(BoardIrError, match="cannot describe a Board IR snapshot"):
        BoardIrSummary(
            board_path=summary.board_path,
            board_revision=summary.board_revision,
            supported=False,
            request=summary.request,
            snapshot_digest=summary.snapshot_digest,
            conversion_diagnostic_counts={"geometry.missing": 1},
        )
    with pytest.raises(BoardIrError, match="must report conversion diagnostics"):
        BoardIrSummary(
            board_path=summary.board_path,
            board_revision=summary.board_revision,
            supported=False,
            request=summary.request,
        )
    with pytest.raises(BoardIrError, match="must not report conversion diagnostics"):
        BoardIrSummary(
            board_path=summary.board_path,
            board_revision=summary.board_revision,
            supported=True,
            request=summary.request,
            snapshot_digest=summary.snapshot_digest,
            constraint_digest=summary.constraint_digest,
            ir_schema=summary.ir_schema,
            ir_schema_version=summary.ir_schema_version,
            distance_unit=summary.distance_unit,
            angle_unit=summary.angle_unit,
            copper_layer_ids=summary.copper_layer_ids,
            object_counts=dict(summary.object_counts),
            conversion_diagnostic_counts={"geometry.missing": 1},
        )


# ---------------------------------------------------------------------------
# `unmodelled_counts`: the disclosure four risk rows depend on (P3.6)
#
# `ConversionResult` carries five measured fields, and `BoardIrSummary` carried
# none of them, so from an MCP client every accepted-but-unmodelled construct was
# discarded silently. `R-134`, `R-139`, `R-141` and `R-144` each say in their own
# words that the count *is* the disclosure; four mitigations therefore rested on
# a channel no published surface had. The direction of error is under-disclosure.
#
# The fix is one map rather than five fields, and the test below is what makes
# that shape hold: it reflects over the dataclass, so a sixth counter fails here
# until it is mapped. That is deliberate. The reason this grew from two to five
# unnoticed is that each addition was invisible; the reflection makes the next
# one loud without making it a contract change.
# ---------------------------------------------------------------------------


def _measured_fields_by_reflection() -> set[str]:
    """Every `ConversionResult` field that is a measured quantity rather than a result."""

    import dataclasses

    from copper_mcp.board_ir.diagnostics import ConversionResult

    return {
        item.name
        for item in dataclasses.fields(ConversionResult)
        if item.name not in {"snapshot", "diagnostics"}
    }


def test_every_measured_conversion_field_appears_in_the_summary_map(tmp_path: Path) -> None:
    """The contract test P3.6 exists for, and the one a sixth counter has to pass.

    Reflection over `ConversionResult` rather than a written list, because a
    written list on both sides would agree with itself. Adding a counter to the
    dataclass and not to `_MEASURED_COUNT_FIELDS` fails here.
    """

    from copper_mcp.board_ir_service import _MEASURED_COUNT_FIELDS

    measured = _measured_fields_by_reflection()

    assert measured == set(_MEASURED_COUNT_FIELDS)
    assert len(_MEASURED_COUNT_FIELDS) == len(set(_MEASURED_COUNT_FIELDS))

    _, settings = _workspace(tmp_path)
    document = summarize_board_ir(_request(), settings).to_dict()

    assert set(document["unmodelled_counts"]) == measured


def test_a_supported_board_publishes_its_conversion_counts(tmp_path: Path) -> None:
    """Zeros included: an empty map and an absent one read the same to a client."""

    _, settings = _workspace(tmp_path)

    summary = summarize_board_ir(_request(), settings)

    assert dict(summary.unmodelled_counts) == {
        "edge_connector_pad_count": 0,
        "max_roundrect_rounding_nm": 0,
        "unmodelled_board_property_count": 0,
        "unmodelled_group_count": 0,
        "unmodelled_pad_property_count": 0,
        "unmodelled_thermal_bridge_angle_pad_count": 0,
        "unmodelled_setup_field_count": 0,
        "unmodelled_stackup_layer_count": 0,
        "unmodelled_footprint_field_count": 0,
        "outline_inward_deviation_nm": 0,
    }


def test_a_board_carrying_an_unmodelled_construct_discloses_it(tmp_path: Path) -> None:
    """The end-to-end proof: a root board property is accepted, erased, and now visible.

    `R-139` records that the conversion keeps the board and drops the property
    map, and that the count is the disclosure. Before this change the count
    stopped at `ConversionResult` and the client saw a clean conversion with no
    hint that anything had been dropped.
    """

    source = FIXTURE.read_bytes().replace(
        b"(generator ", b'(property "REVISION" "rev-a")\n  (generator ', 1
    )
    _, settings = _workspace(tmp_path, source=source)

    summary = summarize_board_ir(_request(), settings)

    assert summary.supported is True
    assert summary.unmodelled_counts["unmodelled_board_property_count"] == 1
    assert summary.to_dict()["unmodelled_counts"]["unmodelled_board_property_count"] == 1


def test_a_thermal_bridge_angle_nonclaim_reaches_the_mcp_summary(tmp_path: Path) -> None:
    """Issue #186's typed non-claim is visible at the public inspection boundary."""

    source = FIXTURE.read_bytes().replace(
        b'      (net "AUDIO")',
        b'      (thermal_bridge_angle 45)\n      (net "AUDIO")',
        1,
    )
    _, settings = _workspace(tmp_path, source=source)

    summary = summarize_board_ir(_request(), settings)

    assert summary.supported is True
    assert summary.unmodelled_counts["unmodelled_thermal_bridge_angle_pad_count"] == 1
    assert summary.to_dict()["unmodelled_counts"]["unmodelled_thermal_bridge_angle_pad_count"] == 1


def test_an_unsupported_board_reports_no_conversion_counts(tmp_path: Path) -> None:
    """A refused conversion measured nothing, and zeros would claim it measured zero."""

    unsupported = FIXTURE.read_bytes().replace(b'(layer "Edge.Cuts")', b'(layer "F.SilkS")')
    _, settings = _workspace(tmp_path, source=unsupported)

    summary = summarize_board_ir(_request(), settings)

    assert summary.supported is False
    assert dict(summary.unmodelled_counts) == {}
    assert summary.to_dict()["unmodelled_counts"] == {}


def test_a_supported_summary_missing_a_measured_count_is_refused() -> None:
    """The invariant is enforced by the type, not only by the one builder that fills it."""

    request = parse_board_ir_request(_request())
    complete = dict.fromkeys(_measured_fields_by_reflection(), 0)

    with pytest.raises(BoardIrError, match="every measured conversion count"):
        BoardIrSummary(
            board_path="two-pad.kicad_pcb",
            board_revision="sha256:" + "0" * 64,
            supported=True,
            request=request,
            snapshot_digest="sha256:" + "1" * 64,
            constraint_digest="sha256:" + "2" * 64,
            ir_schema="board-ir",
            ir_schema_version="0.4.0",
            distance_unit="nm",
            angle_unit="udeg",
            copper_layer_ids=("layer:F.Cu",),
            object_counts={"pads": 2},
            unmodelled_counts=dict.fromkeys(list(complete)[:-1], 0),
        )


def test_the_conversion_counts_are_detached_and_immutable(tmp_path: Path) -> None:
    """Same detachment contract as the other two maps; a caller cannot reach back in."""

    _, settings = _workspace(tmp_path)

    summary = summarize_board_ir(_request(), settings)
    document = summary.to_dict()
    document["unmodelled_counts"]["unmodelled_group_count"] = 99

    assert summary.to_dict()["unmodelled_counts"]["unmodelled_group_count"] == 0
    with pytest.raises(TypeError):
        summary.unmodelled_counts["injected"] = 1  # type: ignore[index]


def test_the_counts_disclose_a_quantity_and_never_an_identity(tmp_path: Path) -> None:
    """Widening the surface must not widen what it discloses.

    The map's values are integers and its keys are field names from this
    repository's own source, so nothing in it can carry a net name, a pad
    identity, a coordinate or a board byte -- which is why this is an additive
    output field rather than a security review (see D-202).
    """

    source = FIXTURE.read_bytes().replace(
        b"(generator ", b'(property "SECRET_KEY" "do-not-disclose")\n  (generator ', 1
    )
    _, settings = _workspace(tmp_path, source=source)

    document = summarize_board_ir(_request(), settings).to_dict()

    assert document["unmodelled_counts"]["unmodelled_board_property_count"] == 1
    assert "SECRET_KEY" not in repr(document)
    assert "do-not-disclose" not in repr(document)
    assert all(isinstance(value, int) for value in document["unmodelled_counts"].values())


def test_the_conversion_docstring_counts_the_fields_that_actually_precede_it() -> None:
    """The drift the audit found, mechanised rather than merely corrected once.

    `unmodelled_board_property_count`'s paragraph said "the *two* fields above"
    while three counters preceded it. The sentence was true when it was written;
    `edge_connector_pad_count`'s paragraph (ADR-0096) was later inserted *above*
    it and the cross-reference was not updated. Nothing could have noticed --
    it is a hand-maintained cross-reference, which is exactly the class section 4
    of the post-0.8.0 audit is about.

    So this checks the class rather than the instance: every "the N fields above"
    in the docstring must equal the number of measured-field paragraphs that
    really precede it. Inserting a sixth counter above an existing paragraph now
    fails here.
    """

    import re
    from textwrap import dedent

    from copper_mcp.board_ir.diagnostics import ConversionResult
    from copper_mcp.board_ir_service import _MEASURED_COUNT_FIELDS

    ordinals = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
    docstring = dedent(ConversionResult.__doc__ or "")
    paragraphs = docstring.split("\n\n")
    reference = re.compile(r"\bthe (one|two|three|four|five|six)(?: fields?)? above\b")

    preceding = 0
    checked = 0
    for paragraph in paragraphs:
        opens_a_field = any(
            paragraph.lstrip().startswith(f"``{name}``") for name in _MEASURED_COUNT_FIELDS
        )
        for match in reference.finditer(paragraph):
            claimed = ordinals[match.group(1)]
            assert claimed == preceding, (
                f"the docstring says {match.group(0)!r} where {preceding} measured field(s) "
                f"precede it: {paragraph.strip()[:80]!r}"
            )
            checked += 1
        if opens_a_field:
            preceding += 1

    # The scan is only evidence if it could have reported a presence: three of the
    # five paragraphs carry such a cross-reference today.
    assert preceding == len(_MEASURED_COUNT_FIELDS)
    assert checked == 3


def test_an_unsupported_summary_carrying_counts_is_refused() -> None:
    """The type refuses it, not merely the one builder that never fills it.

    Found by mutation: deleting this invariant left every test green, because the
    only path that constructs an unsupported summary happens not to pass the
    field. An invariant nothing exercises is a comment.
    """

    request = parse_board_ir_request(_request())

    with pytest.raises(BoardIrError, match="cannot report conversion counts"):
        BoardIrSummary(
            board_path="two-pad.kicad_pcb",
            board_revision="sha256:" + "0" * 64,
            supported=False,
            request=request,
            conversion_diagnostic_counts={"geometry.missing": 1},
            unmodelled_counts={"unmodelled_group_count": 0},
        )
