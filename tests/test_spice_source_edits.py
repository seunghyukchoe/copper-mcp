"""Direct controls for the standalone confined SPICE source edit kernel."""

from __future__ import annotations

import time
from pathlib import PurePosixPath

import pytest

from copper_mcp.adapters.sexpr import SExprError
from copper_mcp.engineering import _project_spice_source_edits as edits
from copper_mcp.engineering.capture import CaptureLimits

_PATH = "root.kicad_sch"


def _source(*items: bytes) -> bytes:
    return b"(kicad_sch (lib_symbols)" + b"".join(items) + b")"


def _symbol(*properties: bytes) -> bytes:
    return b"(symbol" + b"".join(properties) + b'(uuid "symbol-1"))'


def _property(name: str, value: str) -> bytes:
    return f'(property "{name}" "{value}")'.encode()


def _desired() -> dict[tuple[str, str], edits._DesiredFields]:
    return {
        (_PATH, "symbol-1"): edits._DesiredFields(
            "models/original.lib",
            ".copper-spice-models/model-000.lib",
            "MODEL",
            (("1", "A"), ("2", "K")),
        )
    }


def test_literal_escape_and_directive_controls_are_standalone():
    deadline = time.monotonic() + 30
    assert edits._unescape_literal_text("A{space}B", deadline) == "A B"
    with pytest.raises(edits.ProjectSpiceSourceError):
        edits._source_splices(
            _PATH,
            _source(b'(text "Header{return}.include outside.lib")'),
            {},
            CaptureLimits(),
            deadline,
        )


def test_partial_existing_pin_map_refuses_in_the_kernel():
    source = _source(
        _symbol(
            _property("Sim.Library", "models/original.lib"),
            _property("Sim.Name", "MODEL"),
            _property("Sim.Pins", "1=A"),
            _property("Reference", "R1"),
        )
    )
    with pytest.raises(edits.ProjectSpiceSourceError):
        edits._source_splices(_PATH, source, _desired(), CaptureLimits(), time.monotonic() + 30)


def test_measurement_and_splice_preserve_unicode_bytes():
    source = _source(
        '(text "µ safe")'.encode(),
        _symbol(_property("Reference", "R1")),
    )
    deadline = time.monotonic() + 30
    measured, projected_size = edits._source_edit_plan(
        _PATH,
        source,
        _desired(),
        CaptureLimits(),
        deadline,
        materialize=False,
    )
    splices, materialized_size = edits._source_edit_plan(
        _PATH,
        source,
        _desired(),
        CaptureLimits(),
        deadline,
        materialize=True,
    )
    output = edits._splice(source, splices, deadline)
    assert measured == ()
    assert materialized_size == projected_size == len(output)
    assert '(text "µ safe")'.encode() in output
    assert b'(property "Sim.Pins" "1=A 2=K"' in output


def test_kernel_path_bounds_and_deadline_are_fixed_refusals():
    source = _source(_symbol(_property("Reference", "R1")))
    assert edits._project_relative("models/original.lib", PurePosixPath(".")) == (
        "models/original.lib"
    )
    with pytest.raises(edits.ProjectSpiceSourceError):
        edits._project_relative("../outside.lib", PurePosixPath("."))
    try:
        edits._source_splices(
            _PATH,
            source,
            {},
            CaptureLimits(max_file_bytes=len(source) - 1),
            time.monotonic() + 30,
        )
    except SExprError as error:
        assert error.code == "budget.exceeded.input_bytes"
    else:
        pytest.fail("kernel parser bound must refuse")
    with pytest.raises(edits.ProjectSpiceSourceError):
        edits._source_splices(_PATH, source, {}, CaptureLimits(), 0)
