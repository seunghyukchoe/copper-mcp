"""Bounded reader for the fixed KiCad BOM CSV export profile."""

from __future__ import annotations

import csv
import io
import math
import re
import time
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import NoReturn

_MAX_BYTES = 8 * 1024 * 1024
_MAX_ROWS = 100_000
_MAX_COLUMNS = 64
_MAX_FIELD_BYTES = 4096
_REQUIRED_HEADERS = ("Refs", "Value", "Footprint", "Qty", "DNP")
_RANGE = re.compile(r"(?P<prefix>[^0-9,\-]+)(?P<start>[0-9]+)-(?P=prefix)(?P<end>[0-9]+)")


class BomCsvError(ValueError):
    """A fixed, redacted refusal from the BOM CSV boundary."""


@dataclass(frozen=True, slots=True, repr=False)
class BomCsvRow:
    references: tuple[str, ...]
    value: str
    footprint: str
    quantity: int
    dnp: bool
    extra_fields: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "<BomCsvRow redacted>"


def _malformed() -> NoReturn:
    raise BomCsvError("BOM CSV is malformed")


def _limited() -> NoReturn:
    raise BomCsvError("BOM CSV exceeds limits")


def _expired() -> NoReturn:
    raise BomCsvError("BOM CSV deadline expired")


def _check_deadline(deadline: float) -> None:
    if time.monotonic() >= deadline:
        _expired()


def _normalize_deadline(deadline: float) -> float:
    if type(deadline) not in (int, float):
        _malformed()
    value = math.nan
    try:
        value = float(deadline)
    except OverflowError:
        pass
    if not math.isfinite(value):
        _malformed()
    return value


def _validate_text(value: object) -> str:
    if type(value) is not str:
        _malformed()
    if len(value) > _MAX_FIELD_BYTES:
        _limited()
    encoded = None
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        pass
    if encoded is None:
        _malformed()
    if len(encoded) > _MAX_FIELD_BYTES:
        _limited()
    if any(unicodedata.category(character) in {"Cc", "Cs"} for character in value):
        _malformed()
    return value


def _validate_known_references(known_references: frozenset[str], deadline: float) -> None:
    if type(known_references) is not frozenset or len(known_references) > _MAX_ROWS:
        _malformed()
    for reference in known_references:
        _check_deadline(deadline)
        if not reference:
            _malformed()
        _validate_text(reference)


def _range_references(token: str, deadline: float, remaining: int) -> tuple[str, ...] | None:
    match = _RANGE.fullmatch(token)
    if match is None:
        return None
    start_text = match["start"]
    end_text = match["end"]
    padded = start_text.startswith("0") or end_text.startswith("0")
    if padded and len(start_text) != len(end_text):
        return None
    # Exact conversions bypass Python's configurable decimal int/string limit.
    # Inputs are already bounded ASCII digits; arithmetic remains integral.
    # https://docs.python.org/3.12/library/decimal.html#decimal.Decimal
    start = int(Decimal(start_text))
    end = int(Decimal(end_text))
    count = end - start + 1
    if count <= 0:
        _malformed()
    if count > remaining:
        _limited()
    prefix = match["prefix"]
    width = len(start_text)
    result: list[str] = []
    for number in range(start, end + 1):
        _check_deadline(deadline)
        result.append(prefix + format(Decimal(number), "f").zfill(width))
    return tuple(result)


def _parse_references(
    value: str,
    known_references: frozenset[str],
    deadline: float,
    remaining: int,
) -> tuple[str, ...]:
    tokens = tuple(token.strip() for token in value.split(","))
    if not tokens or any(not token for token in tokens):
        _malformed()
    references: list[str] = []
    for token in tokens:
        _check_deadline(deadline)
        expanded: tuple[str, ...]
        if token in known_references:
            expanded = (token,)
        else:
            range_references = _range_references(token, deadline, remaining - len(references))
            expanded = (token,) if range_references is None else range_references
        if len(expanded) > remaining - len(references):
            _limited()
        references.extend(expanded)
    return tuple(references)


def _quantity(value: str) -> int:
    if not value or not value.isascii() or not value.isdecimal():
        _malformed()
    quantity = int(Decimal(value))
    if not 1 <= quantity <= _MAX_ROWS:
        _malformed()
    return quantity


def parse_bom_csv(
    payload: bytes,
    *,
    known_references: frozenset[str],
    deadline: float,
    max_bytes: int = _MAX_BYTES,
) -> tuple[BomCsvRow, ...]:
    """Parse one fixed-profile BOM CSV without assigning reconciliation meaning."""

    active_deadline = _normalize_deadline(deadline)
    _check_deadline(active_deadline)
    if type(max_bytes) is not int or not 1 <= max_bytes <= _MAX_BYTES:
        _malformed()
    if type(payload) is not bytes:
        _malformed()
    if len(payload) > max_bytes:
        _limited()
    _validate_known_references(known_references, active_deadline)
    _check_deadline(active_deadline)
    text = None
    try:
        text = payload.decode("utf-8-sig", errors="strict")
    except UnicodeDecodeError:
        pass
    if text is None:
        _malformed()
    reader = csv.reader(io.StringIO(text, newline=""), strict=True)
    header = None
    try:
        header = next(reader, None)
    except csv.Error:
        pass
    if header is None:
        _malformed()
    _check_deadline(active_deadline)
    if not header or len(header) > _MAX_COLUMNS:
        _malformed()
    headers = tuple(_validate_text(item) for item in header)
    aliases = {item.casefold() for item in headers}
    if any(not item for item in headers) or len(aliases) != len(headers):
        _malformed()
    if not set(_REQUIRED_HEADERS).issubset(headers):
        _malformed()
    indices = {header: index for index, header in enumerate(headers)}
    extras = tuple(index for index, header in enumerate(headers) if header not in _REQUIRED_HEADERS)
    rows: list[BomCsvRow] = []
    seen_references: set[str] = set()
    reference_count = 0
    malformed_csv = False
    try:
        for record in reader:
            _check_deadline(active_deadline)
            if len(rows) >= _MAX_ROWS:
                _limited()
            if len(record) != len(headers):
                _malformed()
            fields = tuple(_validate_text(item) for item in record)
            quantity = _quantity(fields[indices["Qty"]])
            dnp_value = fields[indices["DNP"]]
            if dnp_value not in {"", "DNP"}:
                _malformed()
            remaining = _MAX_ROWS - reference_count
            references = _parse_references(
                fields[indices["Refs"]], known_references, active_deadline, remaining
            )
            if quantity != len(references):
                _malformed()
            for reference in references:
                _check_deadline(active_deadline)
                if reference in seen_references:
                    _malformed()
                seen_references.add(reference)
            reference_count += len(references)
            rows.append(
                BomCsvRow(
                    references=tuple(sorted(references)),
                    value=fields[indices["Value"]],
                    footprint=fields[indices["Footprint"]],
                    quantity=quantity,
                    dnp=dnp_value == "DNP",
                    extra_fields=tuple(sorted((headers[index], fields[index]) for index in extras)),
                )
            )
    except csv.Error:
        malformed_csv = True
    if malformed_csv:
        _malformed()
    _check_deadline(active_deadline)
    result = tuple(sorted(rows, key=lambda row: row.references))
    _check_deadline(active_deadline)
    return result
