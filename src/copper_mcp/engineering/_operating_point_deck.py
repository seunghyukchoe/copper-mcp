"""Bounded generated nominal-DC decks over internally verified source/model inputs."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from copper_mcp.engineering._operating_point_cases import OperatingPointCase
from copper_mcp.engineering._operating_point_topology import validate_dc_topology
from copper_mcp.engineering.project_spice_export import _RetainedProjectSpiceExport

TITLE = "copper-mcp operating point"
_MAX_BYTES = 16 * 1024 * 1024
_MAX_VECTORS = 128  # The existing raw-result profile's independently validated ceiling.


class OperatingPointDeckError(ValueError):
    """Fixed refusal without source, model or node contents."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise OperatingPointDeckError("operating-point deck deadline expired")


def _scaled(value: int, divisor: int, digits: int) -> str:
    whole, fraction = divmod(abs(value), divisor)
    return ("-" if value < 0 else "") + f"{whole}.{fraction:0{digits}d}"


def _node(value: str) -> str:
    value = value.casefold()
    return "0" if value == "gnd" else value


@dataclass(frozen=True, slots=True, repr=False)
class OperatingPointDeck:
    payload: bytes
    vectors: tuple[tuple[str, str], ...]
    node_map: tuple[tuple[str, str], ...]

    def __repr__(self) -> str:
        return "<OperatingPointDeck redacted>"


def compile_operating_point_deck(
    retained: _RetainedProjectSpiceExport,
    case: OperatingPointCase,
    *,
    deadline: float,
) -> OperatingPointDeck:
    """Compile only internally obtained records; topology is checked before any execution."""
    _check(deadline)
    rows = retained.report.observation.rows
    context = dict(retained.prepared.prepared.files)
    model_sources = tuple(context[path] for _artifact, path in retained.prepared.model_paths)
    sources = tuple(rail for rail in case.rails if rail.energized)
    validate_dc_topology(
        rows,
        model_sources,
        tuple((rail.positive_node, rail.negative_node) for rail in sources),
        tuple((load.from_node, load.to_node) for load in case.loads),
        deadline=deadline,
    )
    # This bijection changes labels only. Captured libraries forbid cross-instance expressions;
    # their internal nodes and global0/GND retain their native semantics.
    names = sorted({_node(name) for row in rows for name in row[1:-1]} - {"0"})
    if not names or len(names) + len(sources) > _MAX_VECTORS:
        raise OperatingPointDeckError("operating-point output scope exceeds its profile")
    mapping = {name: f"n{index:05d}" for index, name in enumerate(names)}
    mapping["0"] = "0"
    parts: list[bytes] = []
    size = 0

    def append(value: bytes) -> None:
        nonlocal size
        _check(deadline)
        if len(value) > _MAX_BYTES - size:
            raise OperatingPointDeckError("operating-point deck exceeds its byte ceiling")
        parts.append(value)
        size += len(value)

    append((TITLE + "\n").encode())
    for model in model_sources:
        append(model)
        if not model.endswith(b"\n"):
            append(b"\n")
    for index, row in enumerate(rows):
        _check(deadline)
        rendered = (
            f"{row[0][0]}copper{index}",
            *(mapping[_node(node)] for node in row[1:-1]),
            row[-1],
        )
        append((" ".join(rendered) + "\n").encode("ascii"))
    for index, rail in enumerate(sources):
        append(
            (
                f"Vcopper{index} {mapping[rail.positive_node]} {mapping[rail.negative_node]} DC "
                + _scaled(rail.signed_voltage_uv, 1_000_000, 6)
                + "\n"
            ).encode("ascii")
        )
    for index, load in enumerate(case.loads):
        append(
            (
                f"Icopper{index} {mapping[load.from_node]} {mapping[load.to_node]} DC "
                + _scaled(load.current_ua, 1_000_000, 6)
                + "\n"
            ).encode("ascii")
        )
    vectors = tuple((f"v({mapping[name]})", "voltage") for name in names) + tuple(
        (f"i(vcopper{index})", "current") for index in range(len(sources))
    )
    append((".temp " + _scaled(case.temperature_millic, 1000, 3) + "\n").encode("ascii"))
    append((".save " + " ".join(name for name, _kind in vectors) + "\n.op\n.end\n").encode("ascii"))
    payload = b"".join(parts)
    _check(deadline)
    return OperatingPointDeck(payload, vectors, tuple((name, mapping[name]) for name in names))


_NUMBER = r"[0-9]+(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?"


def validate_native_diagnostics(
    payload: bytes, case: OperatingPointCase, vector_count: int, *, deadline: float
) -> None:
    """Accept the complete fixed45.2 nominal-OP log shape, not a keyword-only success test."""
    _check(deadline)
    if type(payload) is not bytes or len(payload) > 64 * 1024:
        raise OperatingPointDeckError("operating-point diagnostics are unavailable")
    lines = tuple(line.strip() for line in payload.decode("ascii").splitlines() if line.strip())
    prefix = (
        "Note: No compatibility mode selected!",
        f"Circuit: {TITLE}",
        'ASCII raw file "/work/result.raw"',
        "Doing analysis at TEMP = "
        + _scaled(case.temperature_millic, 1000, 3)
        + "000 and TNOM = 27.000000",
        "Using SPARSE 1.3 as Direct Linear Solver",
        f"No. of Data Columns : {vector_count}",
        "No. of Data Rows : 1",
    )
    suffix = (
        r"Total analysis time \(seconds\) = " + _NUMBER,
        r"Total elapsed time \(seconds\) = " + _NUMBER,
        *(
            re.escape(label.rstrip()) + r"[ \t]+" + _NUMBER + r" MB\."
            for label in (
                "Total DRAM available = ",
                "DRAM currently available = ",
                "Maximum ngspice program size = ",
                "Current ngspice program size = ",
                "Shared ngspice pages = ",
                "Text (code) pages = ",
            )
        ),
        r"Stack = " + _NUMBER + r" bytes\.",
        r"Library pages =[ \t]+" + _NUMBER + r" MB\.",
    )
    if lines[: len(prefix)] != prefix or len(lines) != len(prefix) + len(suffix):
        raise OperatingPointDeckError("operating-point diagnostics disagree with the profile")
    for line, pattern in zip(lines[len(prefix) :], suffix, strict=True):
        _check(deadline)
        if re.fullmatch(pattern, line) is None:
            raise OperatingPointDeckError("operating-point diagnostics disagree with the profile")
    _check(deadline)
