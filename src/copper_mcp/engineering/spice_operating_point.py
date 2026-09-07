"""Bounded private interpreter for ngspice 45.2 ASCII operating-point rawfiles."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass
from typing import NoReturn, cast

_BACKEND = "ngspice45.2"
_MAX_BYTES = 256 * 1024
_MAX_LINE_BYTES = 4096
_MAX_LINES = 512
_MAX_VECTORS = 128
_MAX_DECIMAL_BYTES = 128
_TEXT = re.compile(r"[ -~]{1,4096}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_().:+\-/]{1,128}\Z")
_DECIMAL = re.compile(r"[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][+-]?[0-9]{1,3})?\Z")
_DATE = re.compile(
    r"(?:Mon|Tue|Wed|Thu|Fri|Sat|Sun) (?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r" (?: [1-9]|[12][0-9]|3[01]) (?:[01][0-9]|2[0-3]):[0-5][0-9]:[0-5][0-9] {1,2}[0-9]{4}\Z"
)


class SpiceOperatingPointError(ValueError):
    """Fixed refusal without raw output, simulation, or physics authority."""


def _fail(message: str = "SPICE operating-point output is malformed") -> NoReturn:
    raise SpiceOperatingPointError(message)


def _deadline(value: object) -> float:
    if type(value) not in (int, float) or isinstance(value, bool):
        _fail()
    failed = False
    result = 0.0
    try:
        result = float(cast(int | float, value))
    except (OverflowError, ValueError):
        failed = True
    if failed or not math.isfinite(result):
        _fail()
    if time.monotonic() >= result:
        _fail("SPICE operating-point deadline expired")
    return result


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _fail("SPICE operating-point deadline expired")


def _lines(payload: bytes, deadline: float) -> tuple[str, ...]:
    if type(payload) is not bytes or not payload or len(payload) > _MAX_BYTES:
        _fail()
    lines: list[str] = []
    start = 0
    while start < len(payload):
        _check(deadline)
        end = payload.find(b"\n", start)
        if end < 0:
            _fail()
        if len(lines) >= _MAX_LINES or end - start > _MAX_LINE_BYTES:
            _fail()
        raw = payload[start:end]
        if raw.endswith(b"\r") or not raw:
            _fail()
        lines.append(raw.decode("ascii", errors="strict"))
        start = end + 1
    _check(deadline)
    return tuple(lines)


def _controls(
    title: object, command: object, vectors: object, max_bytes: object, deadline: float
) -> tuple[str, str, tuple[tuple[str, str], ...]]:
    if (
        type(max_bytes) is not int
        or isinstance(max_bytes, bool)
        or not 1 <= max_bytes <= _MAX_BYTES
        or type(title) is not str
        or type(command) is not str
        or _TEXT.fullmatch(title) is None
        or _TEXT.fullmatch(command) is None
        or type(vectors) is not tuple
        or not 1 <= len(vectors) <= _MAX_VECTORS
    ):
        _fail()
    schema: list[tuple[str, str]] = []
    for vector in vectors:
        _check(deadline)
        if (
            type(vector) is not tuple
            or len(vector) != 2
            or any(type(value) is not str or _TOKEN.fullmatch(value) is None for value in vector)
        ):
            _fail()
        name, kind = cast(tuple[str, str], vector)
        if (
            kind not in {"voltage", "current"}
            or (kind == "voltage" and re.fullmatch(r"v\([A-Za-z0-9_./:+-]{1,120}\)", name) is None)
            or (kind == "current" and re.fullmatch(r"i\([A-Za-z0-9_./:+-]{1,120}\)", name) is None)
        ):
            _fail()
        schema.append((name, kind))
    if len({name for name, _ in schema}) != len(schema):
        _fail()
    return title, command, tuple(schema)


def _decimal(value: str) -> str:
    if len(value) > _MAX_DECIMAL_BYTES or _DECIMAL.fullmatch(value) is None:
        _fail()
    exponent = value.lower().partition("e")[2]
    if exponent and abs(int(exponent)) > 308:
        _fail()
    try:
        numeric = float(value)
    except ValueError:
        _fail()
    if not math.isfinite(numeric):
        _fail()
    return value


@dataclass(frozen=True, slots=True, repr=False)
class SpiceOperatingPoint:
    """Private raw observation; decimal text is retained without a precision-changing conversion."""

    title: str
    command: str
    vectors: tuple[tuple[str, str, str], ...]

    def __repr__(self) -> str:
        return "<SpiceOperatingPoint redacted>"

    @property
    def digest(self) -> str:
        return self._digest(math.inf)

    def _digest(self, deadline: float) -> str:
        digest = hashlib.sha256(b"copper-mcp/ngspice45.2-operating-point/v1\x00")
        encoder = json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )
        for token in encoder.iterencode(
            {
                "backend": _BACKEND,
                "command": self.command,
                "title": self.title,
                "vectors": self.vectors,
            }
        ):
            _check(deadline)
            digest.update(token.encode("ascii"))
        _check(deadline)
        return "sha256:" + digest.hexdigest()


def parse_spice_operating_point(
    payload: bytes,
    *,
    expected_title: str,
    expected_command: str,
    expected_vectors: tuple[tuple[str, str], ...],
    deadline: float,
    max_bytes: int,
) -> SpiceOperatingPoint:
    """Consume exactly one supplied ngspice 45.2 real operating-point plot; never execute it."""

    active = _deadline(deadline)
    result: SpiceOperatingPoint | None = None
    try:
        title, command, schema = _controls(
            expected_title, expected_command, expected_vectors, max_bytes, active
        )
        if len(payload) > max_bytes:
            _fail()
        lines = _lines(payload, active)
        count = len(schema)
        if len(lines) != 9 + 2 * count:
            _fail()
        if (
            lines[0] != f"Title: {title}"
            or not lines[1].startswith("Date: ")
            or _DATE.fullmatch(lines[1][6:]) is None
            or lines[2] != f"Command: {command}"
            or lines[3] != "Plotname: Operating Point"
            or lines[4] != "Flags: real"
            or lines[5] != f"No. Variables: {count}"
            or lines[6] != "No. Points: 1       "
            or lines[7] != "Variables:"
        ):
            _fail()
        actual_schema: list[tuple[str, str]] = []
        for index in range(count):
            _check(active)
            tokens = lines[8 + index].split("\t")
            if len(tokens) != 4 or tokens[0] != "" or tokens[1] != str(index):
                _fail()
            actual_schema.append((tokens[2], tokens[3]))
        if tuple(actual_schema) != schema or len(set(actual_schema)) != count:
            _fail()
        values_at = 8 + count
        if lines[values_at] != "Values:":
            _fail()
        values: list[str] = []
        first = lines[values_at + 1].split("\t")
        if len(first) != 3 or first[0] != "0" or first[1] != "":
            _fail()
        values.append(_decimal(first[2]))
        for index in range(1, count):
            _check(active)
            tokens = lines[values_at + 1 + index].split("\t")
            if len(tokens) != 2 or tokens[0] != "":
                _fail()
            values.append(_decimal(tokens[1]))
        observation = SpiceOperatingPoint(
            title,
            command,
            tuple((name, kind, value) for (name, kind), value in zip(schema, values, strict=True)),
        )
        _ = observation._digest(active)
        _check(active)
        result = observation
    except (SpiceOperatingPointError, UnicodeError, ValueError, TypeError, OverflowError):
        pass
    if result is None:
        _fail()
    return result
