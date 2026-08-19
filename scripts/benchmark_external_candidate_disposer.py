#!/usr/bin/env python3
"""Replay B-088's routed patches through the production external-candidate disposer."""

from __future__ import annotations

import argparse
import hashlib
import json
from copy import deepcopy
from itertools import pairwise
from pathlib import Path
from typing import Any

from copper_mcp.benchmarks.simple_route_json import (
    ImportedNet,
    ImportedProblem,
    import_simple_route_json,
)
from copper_mcp.routing import (
    EXTERNAL_ROUTE_PATCH_SCHEMA,
    AStarRouter,
    ExternalCandidateVerificationResult,
    verify_external_route_candidate,
)
from copper_mcp.routing.contracts import AStarSettings, RouteCandidate, RouteRequest
from scripts.benchmark_simple_route_json_corpus import (
    FIXED_POLICY,
    ROUTER_LIMITS,
    SEED,
    load_corpus,
)

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ARTIFACT = ROOT / "benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json"
DEFAULT_OUTPUT = (
    ROOT / "benchmarks/results/routing/2026-08-17-external-candidate-disposer-corpus-v1.json"
)
REPORT_SCHEMA = "copper-mcp/benchmark/external-candidate-disposer-corpus/v1"
MAX_PATH_EDGES = 4_096
OBSTACLE_CASE = ("ts19_adc_breakout.json", "net:n8", 0, 2, "y_nm", 1)
DISCONTINUITY_CASE = ("ts06_push_pull.json", "net:n4", 0, 1)
BOUND_FILES = (
    "scripts/benchmark_external_candidate_disposer.py",
    "src/copper_mcp/benchmarks/simple_route_json.py",
    "src/copper_mcp/routing/external_candidate_verifier.py",
    "src/copper_mcp/routing/candidate_path_validator.py",
)


class DisposerBenchmarkError(RuntimeError):
    """Raised when the evidence protocol itself diverges from its predeclaration."""


def _canonical_digest(value: object) -> str:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _file_digest(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _document(candidate: RouteCandidate) -> dict[str, Any]:
    return {
        "schema": EXTERNAL_ROUTE_PATCH_SCHEMA,
        "problem_revision": candidate.base_revision,
        "start_pad_id": candidate.start_pad_id,
        "end_pad_id": candidate.end_pad_id,
        "paths": [
            {
                "segments": [
                    {
                        "layer_id": candidate.patch.layer_id,
                        "width_nm": candidate.patch.width_nm,
                        "start": {"x_nm": start.x, "y_nm": start.y},
                        "end": {"x_nm": end.x, "y_nm": end.y},
                    }
                    for start, end in pairwise(path.vertices)
                ]
            }
            for path in candidate.patch.paths
        ],
        "vias": [],
    }


def _request(problem: ImportedProblem, net: ImportedNet) -> RouteRequest:
    return RouteRequest(
        board_revision=problem.snapshot.snapshot_digest,
        net_id=net.net_id,
        layer_id=net.layer_id,
        seed=SEED,
        settings=AStarSettings(
            grid_step_nm=FIXED_POLICY.step_for(problem, net.pad_ids),
            **ROUTER_LIMITS,
        ),
    )


def _verify(
    problem: ImportedProblem,
    request: RouteRequest,
    candidate: RouteCandidate,
    document: object,
) -> ExternalCandidateVerificationResult:
    return verify_external_route_candidate(
        problem.snapshot,
        request,
        document,
        start_pad_id=candidate.start_pad_id,
        end_pad_id=candidate.end_pad_id,
        max_obstacle_checks=request.settings.max_obstacle_checks,
        max_path_edges=MAX_PATH_EDGES,
    )


def _perturbations(
    cases: dict[
        tuple[str, str], tuple[ImportedProblem, RouteRequest, RouteCandidate, dict[str, Any]]
    ],
) -> list[dict[str, Any]]:
    results: list[dict[str, object]] = []

    board, net_id, path_index, segment_index, axis, delta = OBSTACLE_CASE
    problem, request, candidate, original = cases[(board, net_id)]
    incursion = deepcopy(original)
    segments = incursion["paths"][path_index]["segments"]
    segments[segment_index]["start"][axis] += delta
    segments[segment_index]["end"][axis] += delta
    segments[segment_index - 1]["end"][axis] += delta
    segments[segment_index + 1]["start"][axis] += delta
    results.append(
        {
            "class": "one_nm_obstacle_incursion",
            "source": {"board": board, "net_id": net_id},
            "document_digest": _canonical_digest(incursion),
            "result": _verify(problem, request, candidate, incursion).to_dict(),
        }
    )

    board, net_id, path_index, segment_index = DISCONTINUITY_CASE
    problem, request, candidate, original = cases[(board, net_id)]
    discontinuous = deepcopy(original)
    del discontinuous["paths"][path_index]["segments"][segment_index]
    results.append(
        {
            "class": "dropped_middle_segment",
            "source": {"board": board, "net_id": net_id},
            "document_digest": _canonical_digest(discontinuous),
            "result": _verify(problem, request, candidate, discontinuous).to_dict(),
        }
    )

    board, net_id = sorted(cases)[0]
    problem, request, candidate, original = cases[(board, net_id)]
    wrong_endpoint = deepcopy(original)
    wrong_endpoint["start_pad_id"] = candidate.end_pad_id
    results.append(
        {
            "class": "wrong_pad_endpoint",
            "source": {"board": board, "net_id": net_id},
            "document_digest": _canonical_digest(wrong_endpoint),
            "result": _verify(problem, request, candidate, wrong_endpoint).to_dict(),
        }
    )

    undeclared_via = deepcopy(original)
    undeclared_via["vias"] = [
        {
            "start_layer_id": candidate.patch.layer_id,
            "end_layer_id": "layer:undeclared",
            "at": {"x_nm": 0, "y_nm": 0},
        }
    ]
    results.append(
        {
            "class": "undeclared_layer_via",
            "source": {"board": board, "net_id": net_id},
            "document_digest": _canonical_digest(undeclared_via),
            "result": _verify(problem, request, candidate, undeclared_via).to_dict(),
        }
    )
    return results


def run_benchmark() -> dict[str, object]:
    manifest, samples = load_corpus()
    router = AStarRouter()
    cases: dict[
        tuple[str, str], tuple[ImportedProblem, RouteRequest, RouteCandidate, dict[str, Any]]
    ] = {}
    accepted: list[dict[str, Any]] = []
    input_digests: list[str] = []
    result_digests: list[str] = []
    for name, payload in samples:
        problem = import_simple_route_json(Path(name).stem, payload)
        for net in problem.nets:
            if net.pad_count < 2:
                continue
            request = _request(problem, net)
            proposal = router.propose(problem.snapshot, request)
            if proposal.candidate is None:
                continue
            candidate = proposal.candidate
            document = _document(candidate)
            result = _verify(problem, request, candidate, document)
            record: dict[str, Any] = {
                "board": name,
                "net_id": net.net_id,
                "source_candidate_id": candidate.candidate_id,
                "document_digest": _canonical_digest(document),
                "result": result.to_dict(),
            }
            accepted.append(record)
            input_digests.append(record["document_digest"])
            result_digests.append(_canonical_digest(record["result"]))
            cases[(name, net.net_id)] = (problem, request, candidate, document)

    perturbations = _perturbations(cases)
    expected_codes = {
        "one_nm_obstacle_incursion": "obstacle_violation",
        "dropped_middle_segment": "discontinuous_path",
        "wrong_pad_endpoint": "endpoint_mismatch",
        "undeclared_layer_via": "undeclared_layer",
    }
    observed_codes = {item["class"]: item["result"].get("code") for item in perturbations}
    if len(accepted) != 70 or any(item["result"]["status"] != "accepted" for item in accepted):
        raise DisposerBenchmarkError("the predeclared 70/70 unperturbed acceptance gate failed")
    if observed_codes != expected_codes:
        raise DisposerBenchmarkError("the predeclared perturbation taxonomy diverged")

    return {
        "corpus": {
            "corpus_id": manifest["corpus_id"],
            "upstream_commit": manifest["upstream_commit"],
            "license_spdx": manifest["license_spdx"],
            "committed_boards": len(samples),
        },
        "predeclared_criterion": {
            "unperturbed_accepted": "70/70",
            "perturbation_codes": expected_codes,
        },
        "unperturbed": {
            "offered": len(accepted),
            "accepted": sum(item["result"]["status"] == "accepted" for item in accepted),
            "input_set_digest": _canonical_digest(input_digests),
            "result_set_digest": _canonical_digest(result_digests),
            "cases": accepted,
        },
        "perturbations": perturbations,
        "physical_validation": "not_run",
    }


def build_report() -> dict[str, object]:
    source_report = json.loads(SOURCE_ARTIFACT.read_text(encoding="utf-8"))
    report: dict[str, object] = {
        "schema": REPORT_SCHEMA,
        "date_utc": "2026-08-17",
        "configuration": {
            "source_benchmark_run_id": source_report["run_id"],
            "source_artifact_sha256": _file_digest(SOURCE_ARTIFACT),
            "bound_file_sha256": {name: _file_digest(ROOT / name) for name in BOUND_FILES},
        },
        "metrics": run_benchmark(),
        "not_claimed": [
            "KiCad DRC, electrical correctness, fabrication readiness, or physical validation",
            "whole-board compatibility between candidates routed independently",
            "MCP, CLI, apply, persistence, repair, or mutation authority",
        ],
    }
    report["run_id"] = _canonical_digest(report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    arguments = parser.parse_args()
    report = build_report()
    rendered = json.dumps(report, indent=2, sort_keys=True) + "\n"
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
