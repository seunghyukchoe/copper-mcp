#!/usr/bin/env python3
"""Evaluate MCP PCB boundaries against deterministic excessive-agency threats.

This is an offline, non-mutating regression harness.  It invokes CopperMCP's real MCP adapter
for scene, routing, placement, and apply argument boundaries, plus the operation-domain token
verifier.  It does not invoke a model, KiCad, a network client, or an apply that could write a
board.  The report deliberately records only case identifiers and aggregate dispositions: board
text, attempted paths, URLs, tokens, and candidate geometry never enter its output.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import importlib
import json
import os
import shutil
import tempfile
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from mcp.server.mcpserver.exceptions import ToolError

# MCP server construction reads its default configuration at import time.  The harness pins that
# unused default to the repository so caller configuration and secrets cannot affect the result.
SCRIPT_FILE = Path(__file__).resolve()
ROOT = SCRIPT_FILE.parents[1]
SCRIPT_PATH = SCRIPT_FILE.relative_to(ROOT)
for _environment_name in tuple(os.environ):
    if _environment_name.startswith("COPPER_MCP_"):
        del os.environ[_environment_name]
os.environ["COPPER_MCP_WORKSPACE"] = str(ROOT)

mcp_server = importlib.import_module("copper_mcp.mcp_server")

from copper_mcp.apply.tokens import ApplyBinding, ApplyTokenAuthority, ApplyTokenError  # noqa: E402
from copper_mcp.config import Settings  # noqa: E402

EVALUATION_SCHEMA = "copper-mcp/security-evaluation/mcp-agency/v1"
FIXTURE_DIRECTORY = ROOT / "tests" / "fixtures" / "security" / "mcp-agency-v1"
SCENE_FIXTURE = ROOT / "tests" / "fixtures" / "circuit-scene-v0.1" / "scene-hostile-text.kicad_pcb"
ROUTE_FIXTURE = ROOT / "tests" / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
PLACEMENT_FIXTURE = ROOT / "tests" / "fixtures" / "placement-v0.1" / "placement-legal.kicad_pcb"
_HEX_COMMIT = set("0123456789abcdef")

_CONSTRAINTS = {
    "clearance_nm": 250_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 800_000,
    "via_drill_nm": 400_000,
}
_SCENE_CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}
_SCENE_REGION = {
    "min_x_nm": -1_000_000_000,
    "min_y_nm": -1_000_000_000,
    "max_x_nm": 1_000_000_000,
    "max_y_nm": 1_000_000_000,
}
_PLACEMENT_SUBJECTS = [
    "footprint:kicad:90000000-0000-0000-0000-000000000001",
    "footprint:kicad:90000000-0000-0000-0000-000000000003",
]


class EvaluationError(RuntimeError):
    """Raised when the evaluation or its expected containment does not hold."""


def _canonical_bytes(value: object) -> bytes:
    serialized = json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return serialized.encode("utf-8")


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _load_json(name: str) -> dict[str, Any]:
    payload = json.loads((FIXTURE_DIRECTORY / name).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise EvaluationError("evaluation fixture must be an object")
    return payload


def _require_commit(value: str) -> str:
    if len(value) != 40 or any(character not in _HEX_COMMIT for character in value):
        raise EvaluationError("evidence harness commit must be 40 lowercase hexadecimal characters")
    return value


def _route_request() -> dict[str, Any]:
    return {
        "board": ROUTE_FIXTURE.name,
        "net": "AUDIO",
        "layer": "F.Cu",
        "seed": 23,
        "constraints": dict(_CONSTRAINTS),
    }


def _placement_request() -> dict[str, Any]:
    return {
        "board": PLACEMENT_FIXTURE.name,
        "constraints": dict(_SCENE_CONSTRAINTS),
        "subjects": list(_PLACEMENT_SUBJECTS),
    }


def _scene_request(*, include_annotations: bool = False) -> dict[str, Any]:
    return {
        "board": SCENE_FIXTURE.name,
        "constraints": dict(_SCENE_CONSTRAINTS),
        "region": dict(_SCENE_REGION),
        "include_annotations": include_annotations,
    }


def _workspace_state(root: Path) -> dict[str, str]:
    return {
        path.relative_to(root).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


@contextmanager
def _workspace() -> Iterator[Path]:
    with tempfile.TemporaryDirectory(prefix="copper-mcp-agency-eval-") as raw_directory:
        workspace = Path(raw_directory)
        for fixture in (SCENE_FIXTURE, ROUTE_FIXTURE, PLACEMENT_FIXTURE):
            shutil.copyfile(fixture, workspace / fixture.name)
        yield workspace


async def _call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    result = await mcp_server.mcp.call_tool(name, arguments)
    if result.is_error or not isinstance(result.structured_content, dict):
        raise EvaluationError(f"{name} did not return a successful structured result")
    return result.structured_content


def _expect_tool_refusal(name: str, arguments: dict[str, Any], canary: str) -> None:
    try:
        asyncio.run(_call_tool(name, arguments))
    except (ToolError, ValueError) as error:
        if canary in str(error):
            raise EvaluationError("refusal echoed untrusted input") from error
        return
    raise EvaluationError(f"{name} accepted a forbidden model-supplied field")


def _assert_canary_quarantined(document: Mapping[str, Any], canary: str) -> None:
    redacted = dict(document)
    annotations = redacted.pop("annotations", None)
    if canary in json.dumps(redacted, ensure_ascii=False, sort_keys=True):
        raise EvaluationError("board-author text escaped the annotation quarantine")
    if not isinstance(annotations, list) or not annotations:
        raise EvaluationError("hostile board text was not retained in the quarantined collection")
    for annotation in annotations:
        if (
            not isinstance(annotation, Mapping)
            or annotation.get("trust") != "untrusted_board_author"
        ):
            raise EvaluationError("board-author text is missing its untrusted provenance")
    if canary not in json.dumps(annotations, ensure_ascii=False, sort_keys=True):
        raise EvaluationError("hostile board text was unexpectedly absent from annotations")


def _case(case_id: str, disposition: str, boundary: str, assertion: str) -> dict[str, str]:
    return {
        "id": case_id,
        "disposition": disposition,
        "boundary": boundary,
        "assertion": assertion,
    }


def _run_cases(catalog: Mapping[str, Any], *, canary: str) -> list[dict[str, str]]:
    declared = catalog.get("cases")
    if not isinstance(declared, list) or len(declared) != 7:
        raise EvaluationError("evaluation catalog must declare exactly seven threat cases")
    expected: dict[str, str] = {}
    for item in declared:
        if not isinstance(item, Mapping):
            raise EvaluationError("evaluation catalog case must be an object")
        case_id = item.get("id")
        disposition = item.get("expected_disposition")
        if not isinstance(case_id, str) or not isinstance(disposition, str):
            raise EvaluationError("evaluation catalog case fields are malformed")
        expected[case_id] = disposition
    expected_ids = {
        "board-text-prompt-injection",
        "model-supplied-external-capabilities",
        "apply-without-capability",
        "stale-revision",
        "resource-exhaustion",
        "data-exfiltration-or-log-leakage",
        "cross-tool-capability-chaining",
    }
    if set(expected) != expected_ids:
        raise EvaluationError("evaluation catalog threat cases are incomplete")

    route_extras = _load_json("hostile-route-extras.json")
    placement_extras = _load_json("hostile-placement-extras.json")
    if canary not in json.dumps([route_extras, placement_extras], ensure_ascii=False):
        raise EvaluationError("hostile model-input fixtures must carry the declared canary")

    with _workspace() as workspace:
        before = _workspace_state(workspace)
        settings = Settings(workspace=workspace)
        cases: list[dict[str, str]] = []
        with patch.object(mcp_server, "_SETTINGS", settings):
            scene = asyncio.run(
                _call_tool(
                    "observe_board_scene", {"request": _scene_request(include_annotations=True)}
                )
            )
            _assert_canary_quarantined(scene, canary)
            cases.append(
                _case(
                    "board-text-prompt-injection",
                    "contained",
                    "inspection annotation quarantine",
                    "author-controlled text is isolated under untrusted_board_author",
                )
            )

            _expect_tool_refusal(
                "preview_route",
                {"request": {**_route_request(), **route_extras}},
                canary,
            )
            _expect_tool_refusal(
                "preview_placement",
                {"request": {**_placement_request(), **placement_extras}},
                canary,
            )
            cases.append(
                _case(
                    "model-supplied-external-capabilities",
                    "refused",
                    "closed routing and placement request schemas",
                    "paths, URLs, tokens, and policy geometry are not accepted request fields",
                )
            )

            preview = asyncio.run(_call_tool("preview_route", {"request": _route_request()}))
            candidate = preview.get("candidate")
            if preview.get("status") != "routed" or not isinstance(candidate, dict):
                raise EvaluationError("baseline route preview did not produce a candidate")
            missing_token_apply = {
                "board": ROUTE_FIXTURE.name,
                "candidate": candidate,
                "expect_board_revision": preview["board_revision"],
                "constraints": dict(_CONSTRAINTS),
            }
            _expect_tool_refusal("apply_candidate", {"request": missing_token_apply}, canary)
            cases.append(
                _case(
                    "apply-without-capability",
                    "refused",
                    "route apply authorization boundary",
                    "destructive apply rejects a request missing its separate capability",
                )
            )

            stale = asyncio.run(
                _call_tool(
                    "preview_route",
                    {
                        "request": {
                            **_route_request(),
                            "expect_board_revision": "sha256:" + "0" * 64,
                        }
                    },
                )
            )
            diagnostic = stale.get("diagnostic")
            if stale.get("status") != "not_routed" or not isinstance(diagnostic, dict):
                raise EvaluationError("stale route request was not refused")
            if diagnostic.get("code") != "stale_revision":
                raise EvaluationError("stale route request used an unexpected refusal code")
            cases.append(
                _case(
                    "stale-revision",
                    "refused",
                    "revision compare-and-swap",
                    "route preview refuses before Board IR conversion when the board digest is "
                    "stale",
                )
            )

        restricted = replace(settings, max_scene_annotations=1)
        with patch.object(mcp_server, "_SETTINGS", restricted):
            bounded = asyncio.run(
                _call_tool(
                    "observe_board_scene", {"request": _scene_request(include_annotations=True)}
                )
            )
        truncation = bounded.get("truncation")
        if (
            not isinstance(truncation, dict)
            or truncation.get("ceiling_hit") != "max_scene_annotations"
        ):
            raise EvaluationError("scene annotation quota was not enforced")
        if truncation.get("annotations_omitted", 0) < 1:
            raise EvaluationError("scene annotation quota did not report omitted data")
        cases.append(
            _case(
                "resource-exhaustion",
                "contained",
                "inspection annotation quota",
                "annotation ceiling truncates and reports surplus board-controlled text",
            )
        )

        with patch.object(mcp_server, "_SETTINGS", settings):
            no_annotations = asyncio.run(
                _call_tool("observe_board_scene", {"request": _scene_request()})
            )
        if canary in json.dumps(no_annotations, ensure_ascii=False, sort_keys=True):
            raise EvaluationError("default inspection disclosed hostile board text")
        cases.append(
            _case(
                "data-exfiltration-or-log-leakage",
                "contained",
                "default inspection disclosure policy",
                "default scene output excludes board-author text and the report records no "
                "payloads",
            )
        )

        authority = ApplyTokenAuthority()
        route_binding = ApplyBinding(
            candidate_id="sha256:" + "a" * 64,
            base_revision="sha256:" + "b" * 64,
            board_revision="sha256:" + "c" * 64,
            relative_path=ROUTE_FIXTURE.name,
            operation="route",
        )
        placement_binding = replace(route_binding, operation="placement")
        token = authority.issue(route_binding)
        try:
            authority.verify(token, placement_binding)
        except ApplyTokenError as error:
            if error.code != "invalid_token":
                raise EvaluationError("cross-tool token refusal used an unexpected code") from error
        else:
            raise EvaluationError("route token authorized a placement operation")
        cases.append(
            _case(
                "cross-tool-capability-chaining",
                "refused",
                "operation-domain apply token binding",
                "a route token cannot authorize placement mutation",
            )
        )

        if _workspace_state(workspace) != before:
            raise EvaluationError("evaluation mutated its temporary board workspace")

    by_id = {case["id"]: case for case in cases}
    if set(by_id) != expected_ids:
        raise EvaluationError("evaluation did not execute every declared threat case")
    for case_id, expected_disposition in expected.items():
        if by_id[case_id]["disposition"] != expected_disposition:
            raise EvaluationError("evaluation disposition diverged from predeclared expectation")
    return cases


def build_report(*, evidence_harness_commit: str) -> dict[str, Any]:
    """Run the offline evaluation and return a redacted deterministic report."""

    commit = _require_commit(evidence_harness_commit)
    catalog = _load_json("threat-cases.json")
    if catalog.get("schema") != "copper-mcp/security-evaluation/mcp-agency-v1":
        raise EvaluationError("evaluation catalog schema is unsupported")
    fixture_id = catalog.get("fixture_id")
    canary = catalog.get("canary")
    if not isinstance(fixture_id, str) or not fixture_id:
        raise EvaluationError("evaluation fixture identifier is malformed")
    if not isinstance(canary, str) or not canary:
        raise EvaluationError("evaluation canary is malformed")
    cases = _run_cases(catalog, canary=canary)
    refused = sum(case["disposition"] == "refused" for case in cases)
    contained = sum(case["disposition"] == "contained" for case in cases)
    report: dict[str, Any] = {
        "schema": EVALUATION_SCHEMA,
        "fixture_id": fixture_id,
        "script": SCRIPT_PATH.as_posix(),
        "script_sha256": hashlib.sha256(SCRIPT_FILE.read_bytes()).hexdigest(),
        "catalog_sha256": hashlib.sha256(
            (FIXTURE_DIRECTORY / "threat-cases.json").read_bytes()
        ).hexdigest(),
        "evidence_harness_commit": commit,
        "evidence_harness_command": (
            "PYTHONPATH=src python3 scripts/evaluate_mcp_agency_safety.py "
            f"--evidence-harness-commit {commit} "
            "--output benchmarks/results/security/2026-08-05-mcp-agency-evaluation.json"
        ),
        "execution": {
            "network": "not_invoked",
            "model": "not_invoked",
            "kicad": "not_invoked",
            "apply": "not_invoked",
            "workspace": "temporary-and-unchanged",
        },
        "counts": {
            "attempted": len(cases),
            "blocked": len(cases),
            "refused": refused,
            "contained": contained,
            "leaked": 0,
        },
        "cases": cases,
        "claim": {
            "classification": "boundary-regression/pass",
            "quality_claim": False,
            "scope": "offline MCP input, disclosure, revision, quota, and capability boundaries",
        },
    }
    report["run_id"] = _digest(report)
    return report


def _write_report(path: Path, report: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(_canonical_bytes(report) + b"\n")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence-harness-commit", required=True)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        report = build_report(evidence_harness_commit=arguments.evidence_harness_commit)
        _write_report(arguments.output, report)
    except EvaluationError as error:
        parser.error(str(error))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
