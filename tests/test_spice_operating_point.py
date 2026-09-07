from __future__ import annotations

import time
from collections.abc import Callable
from typing import cast

import pytest

from copper_mcp.engineering.spice_operating_point import (
    SpiceOperatingPoint,
    SpiceOperatingPointError,
    parse_spice_operating_point,
)

TITLE = "coppermcp owned op control"
COMMAND = "ngspice-45.2, Build Sun Sep  7 00:00:00 UTC 2025"
SCHEMA = (("v(in)", "voltage"), ("v(out)", "voltage"), ("i(vdrive)", "current"))
RAW = b"""Title: coppermcp owned op control
Date: Mon Sep  7 18:06:18  2026
Command: ngspice-45.2, Build Sun Sep  7 00:00:00 UTC 2025
Plotname: Operating Point
Flags: real
No. Variables: 3
No. Points: 1\x20\x20\x20\x20\x20\x20\x20
Variables:
\t0\tv(in)\tvoltage
\t1\tv(out)\tvoltage
\t2\ti(vdrive)\tcurrent
Values:
0\t\t1.000000000000000e+00
\t5.000000000000000e-01
\t-5.000000000000000e-04
"""


def _parse(payload: bytes) -> SpiceOperatingPoint:
    return parse_spice_operating_point(
        payload,
        expected_title=TITLE,
        expected_command=COMMAND,
        expected_vectors=SCHEMA,
        deadline=time.monotonic() + 10,
        max_bytes=64 * 1024,
    )


def _fixed(error: SpiceOperatingPointError) -> None:
    assert error.__cause__ is None and error.__context__ is None


def test_parses_actual_resistor_divider_shape_and_identity_excludes_date() -> None:
    first = _parse(RAW)
    second = _parse(RAW.replace(b"Mon Sep  7 18:06:18  2026", b"Tue Sep  8 18:06:18  2026"))
    changed = _parse(RAW.replace(b"-5.000000000000000e-04", b"-5.000001000000000e-04"))
    assert first.vectors == (
        (*SCHEMA[0], "1.000000000000000e+00"),
        (*SCHEMA[1], "5.000000000000000e-01"),
        (*SCHEMA[2], "-5.000000000000000e-04"),
    )
    assert first.digest == second.digest
    assert first.digest != changed.digest
    assert first.command == COMMAND
    assert repr(first) == "<SpiceOperatingPoint redacted>"


@pytest.mark.parametrize(
    "mutate",
    (
        lambda data: data.replace(b"Plotname: Operating Point", b"Plotname: Transient Analysis"),
        lambda data: data.replace(b"No. Variables: 3", b"No. Variables: 2"),
        lambda data: data.replace(b"Date: Mon Sep  7 18:06:18  2026", b"Date: arbitrary"),
        lambda data: data.replace(b"\t1\tv(out)", b"\t2\tv(out)"),
        lambda data: data.replace(b"\t2\ti(vdrive)", b"\t1\ti(vdrive)"),
        lambda data: data + b"late\n",
        lambda data: data.replace(b"1.000000000000000e+00", b"NaN"),
        lambda data: data.replace(b"1.000000000000000e+00", b"-inf"),
        lambda data: data.replace(b"1.000000000000000e+00", b"1e9999"),
        lambda data: data.replace(b"1.000000000000000e+00", b"1" * 129),
    ),
)
def test_refuses_malformed_headers_counts_indexes_extra_and_nonfinite(
    mutate: Callable[[bytes], bytes],
) -> None:
    with pytest.raises(SpiceOperatingPointError) as error:
        _parse(mutate(RAW))
    _fixed(error.value)


def test_refuses_duplicate_vector_and_schema_identity_mismatch() -> None:
    duplicate = RAW.replace(b"\t2\ti(vdrive)", b"\t2\tv(out)")
    with pytest.raises(SpiceOperatingPointError):
        _parse(duplicate)
    with pytest.raises(SpiceOperatingPointError):
        parse_spice_operating_point(
            RAW,
            expected_title=TITLE,
            expected_command="other",
            expected_vectors=SCHEMA,
            deadline=time.monotonic() + 10,
            max_bytes=64 * 1024,
        )


@pytest.mark.parametrize(
    "schema",
    (
        (("v(in)", "current"),),
        (("i(vdrive)", "voltage"),),
        (("node", "voltage"),),
        (("v(in)", "power"),),
    ),
)
def test_refuses_invalid_vector_schema_controls(schema: tuple[tuple[str, str], ...]) -> None:
    with pytest.raises(SpiceOperatingPointError) as error:
        parse_spice_operating_point(
            RAW,
            expected_title=TITLE,
            expected_command=COMMAND,
            expected_vectors=schema,
            deadline=time.monotonic() + 10,
            max_bytes=64 * 1024,
        )
    _fixed(error.value)


@pytest.mark.parametrize("deadline", (None, True, float("inf"), -1.0))
def test_refuses_invalid_deadlines_and_bool_controls(deadline: object) -> None:
    with pytest.raises(SpiceOperatingPointError) as error:
        parse_spice_operating_point(
            RAW,
            expected_title=TITLE,
            expected_command=COMMAND,
            expected_vectors=SCHEMA,
            deadline=cast(float, deadline),
            max_bytes=True,
        )
    _fixed(error.value)
    with pytest.raises(SpiceOperatingPointError):
        parse_spice_operating_point(
            RAW,
            expected_title=TITLE,
            expected_command=COMMAND,
            expected_vectors=SCHEMA,
            deadline=time.monotonic() + 10,
            max_bytes=True,
        )


def test_refuses_line_and_output_caps_before_split() -> None:
    with pytest.raises(SpiceOperatingPointError):
        _parse(b"x" * (4097 + 1))
    with pytest.raises(SpiceOperatingPointError):
        parse_spice_operating_point(
            RAW,
            expected_title=TITLE,
            expected_command=COMMAND,
            expected_vectors=SCHEMA,
            deadline=time.monotonic() + 10,
            max_bytes=1,
        )


def test_digest_rechecks_deadline_while_streaming(monkeypatch: pytest.MonkeyPatch) -> None:
    observation = _parse(RAW)
    ticks = iter((0.0, 0.0, 1.0))
    monkeypatch.setattr(time, "monotonic", lambda: next(ticks, 1.0))
    with pytest.raises(SpiceOperatingPointError) as error:
        observation._digest(1.0)
    _fixed(error.value)
