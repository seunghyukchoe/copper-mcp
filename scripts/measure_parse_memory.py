"""Bounded full-scale parser allocation measurements for isolated replay."""

from __future__ import annotations

import hashlib
import tracemalloc
from collections.abc import Callable
from dataclasses import asdict, dataclass, replace

from copper_mcp.adapters.sexpr import SExprError, parse_sexpr
from copper_mcp.board_ir import ParseLimits

MIB = 1024 * 1024


class ParseMemoryMeasurementError(ValueError):
    """A full-scale synthetic parse did not satisfy its bounded refusal contract."""


def adversarial_wide(byte_ceiling: int) -> bytes:
    """One list far wider than any real board's: binds ``max_children_per_list``."""

    atoms = (byte_ceiling - 24) // 2
    return ("(kicad_pcb(gr_poly" + " a" * atoms + "))").encode()


def adversarial_tree(byte_ceiling: int) -> bytes:
    """Maximal node count with every list narrow and shallow: binds ``max_nodes``."""

    chunk = "(g" + " a" * 1000 + ")"
    group = "(p" + chunk * 100 + ")"
    return ("(kicad_pcb" + group * ((byte_ceiling - 64) // len(group)) + ")").encode()


def adversarial_deep(byte_ceiling: int) -> bytes:
    """Repeated maximal-depth nesting: the worst case for retained parser memory."""

    unit = "(" * 126 + "a" + ")" * 126
    return ("(kicad_pcb" + unit * max(1, (byte_ceiling - 16) // len(unit)) + ")").encode()


@dataclass(frozen=True)
class ParseMemoryScenario:
    shape: str
    payload_factory: Callable[[int], bytes]
    limits: ParseLimits
    expected_refusal_code: str
    peak_ceiling_bytes: int


def scenarios() -> tuple[ParseMemoryScenario, ...]:
    """Return the calibrated wide, tree, and deep scenarios without their payloads."""

    defaults = ParseLimits()
    tightened = replace(defaults, max_tokens=200_000)
    return (
        ParseMemoryScenario(
            "wide",
            adversarial_wide,
            defaults,
            "budget.exceeded.children_per_list",
            48 * MIB,
        ),
        ParseMemoryScenario("tree", adversarial_tree, defaults, "budget.exceeded.nodes", 96 * MIB),
        ParseMemoryScenario(
            "deep",
            adversarial_deep,
            tightened,
            "budget.exceeded.tokens",
            200 * tightened.max_tokens,
        ),
    )


def _validate_observation(
    scenario: ParseMemoryScenario, refusal_code: str | None, peak_bytes: int
) -> None:
    if refusal_code is None:
        raise ParseMemoryMeasurementError(f"{scenario.shape} parse did not refuse")
    if refusal_code != scenario.expected_refusal_code:
        raise ParseMemoryMeasurementError(
            f"{scenario.shape} refusal code {refusal_code!r} does not match "
            f"{scenario.expected_refusal_code!r}"
        )
    if type(peak_bytes) is not int or peak_bytes <= 0 or peak_bytes >= scenario.peak_ceiling_bytes:
        raise ParseMemoryMeasurementError(
            f"{scenario.shape} peak allocation exceeds its "
            f"{scenario.peak_ceiling_bytes}-byte ceiling"
        )


def measure_scenario(
    scenario: ParseMemoryScenario,
    *,
    parser: Callable[[bytes, ParseLimits], object] = parse_sexpr,
) -> dict[str, object]:
    """Measure one full input and retain only aggregate, non-payload metadata."""

    if tracemalloc.is_tracing():
        raise ParseMemoryMeasurementError("measurement requires fresh allocation tracing")
    payload = scenario.payload_factory(scenario.limits.max_input_bytes)
    if len(payload) > scenario.limits.max_input_bytes:
        raise ParseMemoryMeasurementError(f"{scenario.shape} payload exceeds its input ceiling")
    refusal_code: str | None = None
    tracemalloc.start()
    try:
        try:
            parser(payload, scenario.limits)
        except SExprError as error:
            refusal_code = error.code
        if not tracemalloc.is_tracing():
            raise ParseMemoryMeasurementError("allocation tracing stopped during parsing")
        _, peak_bytes = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    _validate_observation(scenario, refusal_code, peak_bytes)
    return {
        "shape": scenario.shape,
        "payload_digest": "sha256:" + hashlib.sha256(payload).hexdigest(),
        "payload_bytes": len(payload),
        "limits": asdict(scenario.limits),
        "refusal_code": refusal_code,
        "peak_bytes": peak_bytes,
    }


def measure_all() -> dict[str, object]:
    """Measure all calibrated scenarios, never returning synthetic payload bytes or text."""

    return {"cases": [measure_scenario(scenario) for scenario in scenarios()]}
