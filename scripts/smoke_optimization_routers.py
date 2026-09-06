"""Run two public synthetic router smokes; print redacted metadata, never candidate authority."""

from __future__ import annotations

import argparse
import json
import tempfile
from dataclasses import asdict
from pathlib import Path

from copper_mcp.adapters.sexpr import SExpr, parse_sexpr
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.optimization.container_runner import (
    ContainerRouterLimits,
    ContainerRouterRunner,
    ContainerRunRequest,
    ContainerRunStatus,
    EngineKind,
    OperatorContainerRuntime,
    OperatorRouterImages,
)
from copper_mcp.optimization.contracts import bounded_json, digest_document

ROOT = Path(__file__).resolve().parents[1]


def format_observation(engine: EngineKind, output: bytes) -> dict[str, object]:
    """Only a smoke shape check, not net connectivity, geometry, or DRC validation."""
    if engine is EngineKind.FREEROUTING:
        root = parse_sexpr(output, ParseLimits())
        if root.head != "session":
            raise ValueError("smoke did not return a session")
        nodes = [root]
        wires = 0
        while nodes:
            node = nodes.pop()
            wires += node.head == "wire"
            nodes.extend(item for item in node.items if isinstance(item, SExpr))
        if not wires:
            raise ValueError("smoke did not return a routed wire")
        return {"format": "specctra-session", "wire_count": wires}
    document = bounded_json(output)
    if not isinstance(document, dict) or not isinstance(document.get("traces"), list):
        raise ValueError("smoke did not return routed SimpleRouteJson")
    traces = document["traces"]
    if not traces or any(
        not isinstance(trace, dict)
        or trace.get("type") != "pcb_trace"
        or not isinstance(trace.get("route"), list)
        or len(trace["route"]) < 2
        for trace in traces
    ):
        raise ValueError("smoke did not return a routed trace")
    return {"format": "simple-route-json", "trace_count": len(traces)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker", required=True, type=Path)
    parser.add_argument("--socket", required=True, type=Path)
    parser.add_argument("--freerouting-image", required=True)
    parser.add_argument("--simpleroutejson-image", required=True)
    args = parser.parse_args()
    images = OperatorRouterImages(args.freerouting_image, args.simpleroutejson_image)
    inputs = (
        (
            EngineKind.FREEROUTING,
            ROOT / "benchmarks/routing/fixtures/freerouting-common-two-pad-v1.dsn",
        ),
        (
            EngineKind.SIMPLE_ROUTE_JSON,
            ROOT / "hardware/optimization-router/simpleroutejson/smoke-input.json",
        ),
    )
    observations = []
    with tempfile.TemporaryDirectory(prefix="copper-router-smoke-") as private:
        runner = ContainerRouterRunner(
            OperatorContainerRuntime(args.docker, args.socket, Path(private)),
            images,
            ContainerRouterLimits(
                max_runtime_ms=180_000,
                max_input_bytes=1_048_576,
                max_output_bytes=1_048_576,
                memory_bytes=1024 * 1024 * 1024,
                cpu_count=1,
                pids_limit=128,
                work_tmpfs_bytes=64 * 1024 * 1024,
            ),
        )
        for engine, fixture in inputs:
            source = fixture.read_bytes()
            result = runner.run(ContainerRunRequest(engine, source))
            observation: dict[str, object] = {"execution": asdict(result.record)}
            if result.record.status is ContainerRunStatus.SUCCESS and result.output is not None:
                try:
                    observation["output_shape"] = format_observation(engine, result.output)
                except (ValueError, UnicodeError):
                    observation["output_shape"] = "refused"
            observations.append(observation)
    report = {
        "schema_version": "optimization-router-smoke/v1",
        "observations": observations,
        "private_board_input": False,
        "geometry_authority": False,
        "kicad_drc_run": False,
        "apply_authority": "none",
    }
    print(
        json.dumps(
            {**report, "digest": digest_document("optimization-router-smoke/v1", report)}, indent=2
        )
    )
    return 0 if all(isinstance(item.get("output_shape"), dict) for item in observations) else 1


if __name__ == "__main__":
    raise SystemExit(main())
