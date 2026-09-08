"""Private bounded interpretation of KiCad's fixed SPICE export profile."""

from __future__ import annotations

import hashlib
import json
import math
import re
import time
from dataclasses import dataclass

from copper_mcp.engineering.project_spice_model_binding import ProjectSpiceModelBinding

_TOKEN = re.compile(r"[A-Za-z0-9_./:+-]{1,4096}\Z")
_REFERENCE = re.compile(r"[A-Za-z][A-Za-z0-9_.:-]{0,255}\Z")
_REPLACEMENTS = str.maketrans(dict.fromkeys("%(),[]<>~ ", "_"))
_MAX_ROWS = 10_000
_MAX_BYTES = 16 * 1024 * 1024
_MAX_LINE_BYTES = 66 * (4096 + 1)


class SpiceExportError(ValueError):
    """Fixed refusal, without private model names, paths, nodes or native output."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise SpiceExportError("SPICE export deadline expired")


def _deadline(value: float) -> float:
    result = None
    if type(value) in (int, float):
        try:
            result = float(value)
        except OverflowError:
            pass
    if result is None or not math.isfinite(result):
        raise SpiceExportError("SPICE export controls are malformed")
    _check(result)
    return result


def _net_name(name: str) -> str:
    # KiCad 10.0.5 NETLIST_EXPORTER_SPICE::ConvertToSpiceMarkup. Markup/escaped
    # or non-ASCII names require a separate validated profile, never guessed stripping.
    if type(name) is not str or not name.isascii() or not 1 <= len(name) <= 4096:
        raise SpiceExportError("SPICE export net names are unsupported")
    name = name.translate(_REPLACEMENTS)
    if _TOKEN.fullmatch(name) is None:
        raise SpiceExportError("SPICE export net names are unsupported")
    if name.endswith("/0") and not name.endswith("//0"):
        name = "0"
    if name.lower() == "/gnd":
        name = name[1:]
    if name.startswith("//"):
        name = "/root/" + name[2:]
    return name


def expected_spice_rows(
    binding: ProjectSpiceModelBinding, *, deadline: float
) -> tuple[tuple[str, ...], ...]:
    """Derive exact D/X rows from the internally obtained complete terminal binding."""
    active = _deadline(deadline)
    result = None
    try:
        if (
            type(binding) is not ProjectSpiceModelBinding
            or not 1 <= len(binding.references) <= _MAX_ROWS
        ):
            raise ValueError
        rows = []
        instances: set[str] = set()
        net_codes: dict[str, str] = {}
        for reference in binding.references:
            _check(active)
            if _REFERENCE.fullmatch(reference.reference) is None:
                raise ValueError
            prefix = {"diode": "D", "subcircuit": "X"}[reference.definition.kind]
            instance = (
                reference.reference
                if reference.reference.startswith(prefix)
                else prefix + reference.reference
            )
            if instance.casefold() in instances:
                raise ValueError
            instances.add(instance.casefold())
            ports = {}
            for pin in reference.pins:
                _check(active)
                if pin.node.reference != reference.reference:
                    raise ValueError
                if pin.port is None:
                    continue
                if pin.port in ports or pin.node.no_connect:
                    raise ValueError
                name = _net_name(pin.node.net_name)
                key = "0" if name.casefold() == "gnd" else name.casefold()
                if key in net_codes and net_codes[key] != pin.node.net_code:
                    raise ValueError
                net_codes[key] = pin.node.net_code
                ports[pin.port] = name
            if set(ports) != set(reference.definition.ports):
                raise ValueError
            rows.append(
                (
                    instance,
                    *(ports[port] for port in reference.definition.ports),
                    reference.definition.name,
                )
            )
        _check(active)
        completed = tuple(sorted(rows))
        _check(active)
        result = completed
    except (ValueError, TypeError, AttributeError, KeyError):
        pass
    if result is None:
        raise SpiceExportError("SPICE export expected connectivity is unsupported")
    return result


@dataclass(frozen=True, slots=True, repr=False)
class SpiceExportObservation:
    rows: tuple[tuple[str, ...], ...]
    model_paths: tuple[str, ...]

    def __repr__(self) -> str:
        return "<SpiceExportObservation redacted>"

    def digest(self, *, deadline: float) -> str:
        active = _deadline(deadline)
        digest = hashlib.sha256(b"copper-mcp/native-spice-export/v1\x00")
        encoder = json.JSONEncoder(
            sort_keys=True, ensure_ascii=True, allow_nan=False, separators=(",", ":")
        )
        for token in encoder.iterencode({"rows": self.rows, "model_paths": self.model_paths}):
            _check(active)
            digest.update(token.encode("ascii"))
        _check(active)
        return "sha256:" + digest.hexdigest()


def parse_spice_export(
    payload: bytes,
    *,
    expected_rows: tuple[tuple[str, ...], ...],
    expected_includes: tuple[tuple[str, str], ...],
    deadline: float,
    max_bytes: int,
) -> SpiceExportObservation:
    """Consume every line; map exact temporary include paths to stable internal paths only."""
    active = _deadline(deadline)
    result = None
    try:
        if (
            type(max_bytes) is not int
            or not 1 <= max_bytes <= _MAX_BYTES
            or type(payload) is not bytes
            or not 1 <= len(payload) <= max_bytes
        ):
            raise ValueError
        if type(expected_rows) is not tuple or not 1 <= len(expected_rows) <= _MAX_ROWS:
            raise ValueError
        if type(expected_includes) is not tuple or not 1 <= len(expected_includes) <= 16:
            raise ValueError
        includes: dict[str, str] = {}
        for absolute, relative in expected_includes:
            _check(active)
            if any(
                type(path) is not str
                or not 1 <= len(path) <= 4096
                or '"' in path
                or any(ord(char) < 32 for char in path)
                for path in (absolute, relative)
            ):
                raise ValueError
            line = f'.include "{absolute}"'
            if line in includes or relative in includes.values():
                raise ValueError
            includes[line] = relative
        expected_bytes = 0
        for row in expected_rows:
            _check(active)
            if type(row) is not tuple or not 3 <= len(row) <= 66:
                raise ValueError
            for token in row:
                _check(active)
                if type(token) is not str or _TOKEN.fullmatch(token) is None:
                    raise ValueError
                expected_bytes += len(token) + 1
                if expected_bytes > max_bytes:
                    raise ValueError
            if (
                re.fullmatch(r"[DX][A-Za-z0-9_.:-]{0,256}", row[0]) is None
                or (row[0].startswith("D") and len(row) != 4)
                or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", row[-1]) is None
            ):
                raise ValueError
        expected = set(expected_rows)
        if len(expected) != len(expected_rows) or len(
            {row[0].casefold() for row in expected_rows}
        ) != len(expected_rows):
            raise ValueError
        # Admit each native byte span before allocating its decoded line. Neither a
        # newline flood nor an oversized row can create an unrestricted split array.
        lines: list[str] = []
        start = 0
        while start < len(payload):
            _check(active)
            end = payload.find(b"\n", start)
            if end < 0:
                end = len(payload)
            _check(active)
            if len(lines) >= _MAX_ROWS + 18 or end - start > _MAX_LINE_BYTES:
                raise ValueError
            raw = payload[start:end]
            if raw.endswith(b"\r"):
                raw = raw[:-1]
            lines.append(raw.decode("utf-8", errors="strict"))
            start = end + 1
        if len(lines) < 4:
            raise ValueError
        if not lines or lines[0] != ".title KiCad schematic" or lines[-1] != ".end":
            raise ValueError
        rows: set[tuple[str, ...]] = set()
        seen_includes: set[str] = set()
        for line in lines[1:-1]:
            _check(active)
            if line in includes:
                if line in seen_includes or rows:
                    raise ValueError
                seen_includes.add(line)
                continue
            if not line.isascii():
                raise ValueError
            for offset in range(0, len(line), 4096):
                _check(active)
                if any(
                    ord(character) < 32 and character != "\t"
                    for character in line[offset : offset + 4096]
                ):
                    raise ValueError
            tokens = line.split(maxsplit=66)
            if not 3 <= len(tokens) <= 66 or any(len(token) > 4096 for token in tokens):
                raise ValueError
            row = tuple(tokens)
            if row not in expected or row in rows:
                raise ValueError
            rows.add(row)
        if rows != expected or seen_includes != set(includes):
            raise ValueError
        completed = SpiceExportObservation(tuple(sorted(rows)), tuple(sorted(includes.values())))
        _ = completed.digest(deadline=active)
        _check(active)
        result = completed
    except (ValueError, TypeError, IndexError, AttributeError):
        pass
    if result is None:
        raise SpiceExportError("SPICE export does not match complete expected inputs")
    return result
