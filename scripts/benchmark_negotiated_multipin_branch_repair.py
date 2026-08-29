#!/usr/bin/env python3
"""Measure the opt-in multi-pin repair transaction against B-140's exact population.

B-140 is the immutable admission and population authority for this run.  It offers exactly the
70 nets routed by B-088's fixed-grid reference pass, as one whole-board envelope per imported
board.  This successor does not change that set or replay B-140 as if it were a quality result:
it runs two deliberately separate configurations over the same immutable imported snapshots.

``control`` calls the negotiated coordinator with ``repair_settings=None``.  ``treatment`` calls
the same production path with a fresh default :class:`RepairTransactionSettings` for every board.
The complete immutable coordinator results are compared across two repetitions before any public
aggregate is emitted.  Repair evidence is success-only in the production contract; a refusal
therefore publishes no candidate, path, or partial repair accounting.  The aggregate records
that boundary explicitly and keeps the physical-check total (which includes charged repair
responsibility and final-pass work) separate from successful repair evidence.

This is a candidate-only benchmark.  It reads the committed redistributable corpus and immutable
B-088/B-140 artifacts, never writes a board, invokes no KiCad process, and publishes no board,
net, revision, candidate, path, or geometry identity.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import shutil
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from copper_mcp.routing import AStarRouter
from copper_mcp.routing.congestion import (
    NegotiatedRoutingRequest,
    NegotiatedRoutingResult,
    NegotiatedRoutingStatus,
    negotiate_routes,
)
from copper_mcp.routing.repair import RepairTransactionSettings
from scripts import benchmark_negotiated_corpus_census as b140

ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = "scripts/benchmark_negotiated_multipin_branch_repair.py"
B140_RUNNER_PATH = ROOT / "scripts/benchmark_negotiated_corpus_census.py"
B140_ARTIFACT = (
    ROOT / "benchmarks/results/routing/2026-08-29-negotiated-multipin-corpus-census-v1.json"
)
B088_RUNNER_PATH = ROOT / "scripts/benchmark_simple_route_json_corpus.py"
B088_ARTIFACT = ROOT / "benchmarks/results/routing/2026-08-06-simple-route-json-corpus-v1.json"
REFERENCE_RUNNER_PATH = B088_RUNNER_PATH
REFERENCE_ARTIFACT = B088_ARTIFACT
DEFAULT_OUTPUT = (
    ROOT / "benchmarks/results/routing/2026-08-30-negotiated-multipin-branch-repair-v1.json"
)
COMMITMENT_PATH = ROOT / (
    "benchmarks/results/routing/2026-08-30-negotiated-multipin-branch-repair-v1.commitment.json"
)
COMMITMENT_RELATIVE_PATH = COMMITMENT_PATH.relative_to(ROOT).as_posix()
COMMITMENT_SCHEMA = "copper-mcp/benchmark-commitment/negotiated-multipin-branch-repair/v1"
BENCHMARK_REPETITIONS = 2
REPORT_SCHEMA = "copper-mcp/benchmark/negotiated-multipin-branch-repair-differential/v1"
B140_REPORT_SCHEMA = "copper-mcp/benchmark/negotiated-multipin-corpus-census/v1"
B140_RUN_ID = "sha256:ef3724e6a58ba94df8a7e392a4e407029fb2720844fc5adcc4654cac8bbc3a31"
B088_RUN_ID = "sha256:facf95ee9770ffab8c1bc403a32a403e55ca79f2c56d1eabc6679eb6ec4dfca3"
REFERENCE_RUN_ID = B088_RUN_ID
B140_SOURCE_COMMIT = "30692df496e0dc250d3b09bae5ad9b7b11a3d827"
B088_SOURCE_COMMIT = "7a868623d4f88e51bda3621c3acf0594d41f813e"
B140_RUNNER_SHA256 = "sha256:eb4339e5e2264c62a1971958af6a6d5d037d5e5703a3609561c7f5f607279774"
B088_RUNNER_SHA256 = "sha256:8fb5d05fb60a75b66e4720b3aa3ba9e0b28dbd8c3377ac159a239adbc4795fed"
B088_ADAPTER_PATH = ROOT / "src/copper_mcp/benchmarks/simple_route_json.py"
B088_ADAPTER_SHA256 = "sha256:fb64b59f5727d792de30d82b1e9c0b7eab606d071569a29c8c8bc3ff8db5ec66"
CORPUS_UPSTREAM_COMMIT = "be36518b5bf51755dae92c230061ab3cf4e3e063"
CORPUS_COMMITTED_COUNT = 20
CORPUS_UPSTREAM_SAMPLE_COUNT = 36
CORPUS_ID = "tscircuit-benchmark"
CORPUS_UPSTREAM_REPOSITORY = "https://github.com/dwiel/tscircuit-benchmark"
CORPUS_SUBSET_RULE = "the first 20 sample filenames in upstream lexical order"
CORPUS_LICENSE_SHA256 = "5e1e61463320a61ebac4a326a3c6ea5608280c75e5b95c6931497e3a14ebb632"
CORPUS_MANIFEST_ERROR = "the B-088 corpus membership is not the exact pinned file-set"

_GIT_COMMIT = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")
_SHA256 = re.compile(r"sha256:[0-9a-f]{64}")

# These codes are the only refusal values the public differential may contain.  A raw production
# diagnostic is intentionally used only by the private classifier, never by the artifact.
REFUSAL_TAXONOMY: tuple[str, ...] = (
    "envelope_construction",
    "partial_budget",
    "no_path_physical_clearance",
    "no_path_budget",
    "no_path_search",
    "no_path_other",
    "invalid_request",
    "cancelled",
)
RUN_OUTCOME_TAXONOMY: tuple[str, ...] = (
    *REFUSAL_TAXONOMY,
    "completed_without_repair",
    "completed_with_repair",
)
REPAIR_OUTCOME_TAXONOMY: tuple[str, ...] = (
    "not_applicable_envelope_refused",
    "repair_not_published",
    "repair_published",
)

_B140_PRIMARY_EXPECTED: dict[str, int] = {
    "boards_offered": 20,
    "boards_imported": 20,
    "boards_with_a_constructible_envelope": 16,
    "boards_admitted_by_the_coordinator": 16,
    "nets_submitted": 70,
    "submitted_nets_the_reference_routed": 70,
    "reference_per_net_nets_routed": 70,
}


class NegotiatedDifferentialError(RuntimeError):
    """Raised when source binding, measurement, or redaction cannot be trusted."""


@dataclass(frozen=True, slots=True)
class PreparedBoard:
    """An immutable in-memory projection of one exact B-140 submission."""

    problem: b140.ImportedProblem
    submitted: tuple[b140.SubmittedNet, ...]
    envelope: NegotiatedRoutingRequest | None
    reference_nets_routed: int


@dataclass(frozen=True, slots=True)
class RunRecord:
    """Private per-board evidence; it never enters the public report."""

    result: NegotiatedRoutingResult | None
    projection: dict[str, Any]


@dataclass(frozen=True, slots=True)
class ConfigurationMeasurement:
    aggregate: dict[str, Any]
    records: tuple[RunRecord, ...]


@dataclass(frozen=True, slots=True)
class DifferentialMeasurement:
    control: ConfigurationMeasurement
    treatment: ConfigurationMeasurement


def _canonical_bytes(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            allow_nan=False,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("ascii")
    except (TypeError, ValueError, OverflowError) as error:
        raise NegotiatedDifferentialError("the B-141 JSON value is unsafe to digest") from error


def _digest(value: object) -> str:
    return "sha256:" + hashlib.sha256(_canonical_bytes(value)).hexdigest()


def _file_digest(path: Path) -> str:
    try:
        return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as error:
        raise NegotiatedDifferentialError(
            f"required authority is unreadable: {path.name}"
        ) from error


def _git_state() -> tuple[str, tuple[str, ...]]:
    """Return HEAD and status paths without exposing repository paths in the artifact."""

    git = shutil.which("git")
    if git is None:
        return "unknown", ("<git-unavailable>",)
    try:
        commit = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "rev-parse", "HEAD"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        status = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "status", "--porcelain", "--untracked-files=normal"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.splitlines()
    except (OSError, subprocess.SubprocessError) as error:
        raise NegotiatedDifferentialError("the source Git state could not be read") from error
    paths: list[str] = []
    for line in status:
        if len(line) < 4:
            paths.append("<malformed-git-status>")
            continue
        path = line[3:]
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        paths.append(path)
    if _GIT_COMMIT.fullmatch(commit) is None:
        return "unknown", tuple(paths) or ("<malformed-git-commit>",)
    return commit, tuple(paths)


def _require_publishable_source(
    expected_commit: str | None = None,
    *,
    allowed_dirty: frozenset[str] = frozenset(),
) -> str:
    """Fail closed on source drift while allowing this new untracked writer during first run."""

    commit, dirty_paths = _git_state()
    if commit == "unknown":
        raise NegotiatedDifferentialError("artifact publication requires a known Git revision")
    # Keep the guard compatible with lightweight callers that monkeypatch the Git probe with
    # ``(commit, dirty)`` while the real probe returns status paths.  A boolean ``True`` means
    # that the source is dirty but does not identify an allowed path, so it must fail closed.
    if isinstance(dirty_paths, bool):
        unexpected = ("<dirty>",) if dirty_paths else ()
    else:
        unexpected = tuple(path for path in dirty_paths if path not in allowed_dirty)
    if unexpected:
        raise NegotiatedDifferentialError("artifact publication requires an unchanged source tree")
    if expected_commit is not None and commit != expected_commit:
        raise NegotiatedDifferentialError("the Git revision changed during benchmark measurement")
    return commit


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            path.read_text(encoding="utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (OSError, ValueError) as error:
        raise NegotiatedDifferentialError(f"the {label} artifact is unreadable") from error
    if not isinstance(value, dict):
        raise NegotiatedDifferentialError(f"the {label} artifact is not one JSON object")
    try:
        _assert_finite_json_numbers(value)
    except ValueError as error:
        raise NegotiatedDifferentialError(
            f"the {label} artifact contains unsafe numbers"
        ) from error
    return value


def _reject_json_constant(value: str) -> None:
    """Reject JSON extensions that would make numeric validation non-portable."""

    raise ValueError(f"JSON constant {value!r} is not permitted")


def _assert_finite_json_numbers(value: Any) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("non-finite JSON number")
    if isinstance(value, dict):
        for child in value.values():
            _assert_finite_json_numbers(child)
    elif isinstance(value, list):
        for child in value:
            _assert_finite_json_numbers(child)


def _manifest_entries(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Return every manifest file entry after enforcing one canonical, closed file list."""

    files = manifest.get("files")
    if not isinstance(files, list):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    entries: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in files:
        if not isinstance(entry, dict):
            raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
        name = entry.get("name")
        digest = entry.get("sha256")
        byte_count = entry.get("bytes")
        committed = entry.get("committed")
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or Path(name).suffix != ".json"
            or name in names
            or not isinstance(digest, str)
            or re.fullmatch(r"[0-9a-f]{64}", digest) is None
            or type(byte_count) is not int
            or byte_count < 0
            or type(committed) is not bool
        ):
            raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
        names.add(name)
        entries.append(
            {
                "bytes": byte_count,
                "committed": committed,
                "name": name,
                "sha256": digest,
            }
        )
    return tuple(entries)


def _assert_manifest_metadata(manifest: dict[str, Any]) -> tuple[dict[str, Any], ...]:
    """Check the corpus identity and return its canonical, structurally checked file entries."""

    expected = {
        "attribution_required": True,
        "commercial_use_allowed": True,
        "committed_subset_rule": CORPUS_SUBSET_RULE,
        "committed_subset_size": CORPUS_COMMITTED_COUNT,
        "corpus_id": CORPUS_ID,
        "license_sha256": CORPUS_LICENSE_SHA256,
        "license_spdx": "MIT",
        "redistribution_allowed": True,
        "schema": "copper-mcp/benchmark-corpus/v1",
        "upstream_commit": CORPUS_UPSTREAM_COMMIT,
        "upstream_repository": CORPUS_UPSTREAM_REPOSITORY,
        "upstream_sample_count": CORPUS_UPSTREAM_SAMPLE_COUNT,
    }
    if any(manifest.get(key) != value for key, value in expected.items()):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    entries = _manifest_entries(manifest)
    committed = tuple(entry for entry in entries if entry["committed"])
    if len(committed) != CORPUS_COMMITTED_COUNT:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    return entries


def _sample_records(samples: tuple[tuple[str, bytes], ...]) -> tuple[dict[str, Any], ...]:
    """Project loaded sample bytes into canonical records without retaining their names publicly."""

    records: list[dict[str, Any]] = []
    names: set[str] = set()
    for item in samples:
        if not isinstance(item, tuple) or len(item) != 2:
            raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
        name, payload = item
        if (
            not isinstance(name, str)
            or not name
            or Path(name).name != name
            or Path(name).suffix != ".json"
            or name in names
            or not isinstance(payload, bytes)
        ):
            raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
        names.add(name)
        records.append(
            {
                "bytes": len(payload),
                "name": name,
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    return tuple(sorted(records, key=lambda entry: entry["name"]))


def _assert_exact_corpus_membership(
    samples: tuple[tuple[str, bytes], ...],
    authority_by_board: dict[str, b140.ReferenceBoardAuthority],
) -> None:
    """Reject duplicate, omitted, or substituted samples before any aggregate is measured."""

    actual = _sample_records(samples)
    expected: set[tuple[str, str]] = set()
    try:
        for board_name, authority in authority_by_board.items():
            document_sha256 = authority.document_sha256
            if (
                not isinstance(board_name, str)
                or not board_name
                or not isinstance(document_sha256, str)
                or re.fullmatch(r"[0-9a-f]{64}", document_sha256) is None
            ):
                raise ValueError
            expected.add((board_name, document_sha256))
    except (AttributeError, TypeError, ValueError) as error:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error
    actual_membership = {(Path(entry["name"]).stem, entry["sha256"]) for entry in actual}
    if (
        len(actual) != len(expected)
        or len(actual_membership) != len(actual)
        or actual_membership != expected
    ):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)


def _assert_manifest_matches_samples(
    entries: tuple[dict[str, Any], ...], samples: tuple[tuple[str, bytes], ...]
) -> None:
    committed = tuple(
        sorted(
            (
                {
                    "bytes": entry["bytes"],
                    "name": entry["name"],
                    "sha256": entry["sha256"],
                }
                for entry in entries
                if entry["committed"]
            ),
            key=lambda entry: entry["name"],
        )
    )
    if committed != _sample_records(samples):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)


def _corpus_manifest_sha256(
    corpus: Path, manifest: dict[str, Any], samples: tuple[tuple[str, bytes], ...]
) -> str:
    """Return the committed manifest digest after verifying its canonical file-set projection."""

    entries = _assert_manifest_metadata(manifest)
    _assert_manifest_matches_samples(entries, samples)
    return _file_digest(corpus / "manifest.json")


def _load_exact_corpus(
    corpus: Path, authority_by_board: dict[str, b140.ReferenceBoardAuthority]
) -> tuple[dict[str, Any], tuple[tuple[str, bytes], ...]]:
    """Load one corpus path and enforce the immutable B-088 manifest/file-set authority."""

    try:
        manifest, samples = b140.reference.load_corpus(corpus)
    except Exception as error:  # loader diagnostics are intentionally collapsed at this boundary
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error
    if not isinstance(manifest, dict) or not isinstance(samples, tuple):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    entries = _assert_manifest_metadata(manifest)
    _assert_manifest_matches_samples(entries, samples)
    _assert_exact_corpus_membership(samples, authority_by_board)
    return manifest, samples


def _corpus_summary(manifest: dict[str, Any], sample_count: int) -> dict[str, Any]:
    return {
        "committed_boards": sample_count,
        "committed_subset_rule": CORPUS_SUBSET_RULE,
        "corpus_id": CORPUS_ID,
        "license_sha256": CORPUS_LICENSE_SHA256,
        "license_spdx": "MIT",
        "upstream_commit": CORPUS_UPSTREAM_COMMIT,
        "upstream_repository": CORPUS_UPSTREAM_REPOSITORY,
        "upstream_sample_count": CORPUS_UPSTREAM_SAMPLE_COUNT,
    }


def _assert_corpus_summary(
    recorded: Any, manifest: dict[str, Any], sample_count: int, *, include_subset_rule: bool
) -> None:
    if not isinstance(recorded, dict):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    expected = _corpus_summary(manifest, sample_count)
    if not include_subset_rule:
        expected.pop("committed_subset_rule")
    if any(recorded.get(key) != value for key, value in expected.items()):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)


def _current_corpus_binding(
    corpus: Path = b140.CORPUS,
    *,
    authority_by_board: dict[str, b140.ReferenceBoardAuthority] | None = None,
) -> dict[str, Any]:
    if authority_by_board is None:
        authority_by_board = _reference_authority(load_reference_artifact())
    manifest, samples = _load_exact_corpus(corpus, authority_by_board)
    return {
        "corpus_manifest_count": len(samples),
        "corpus_manifest_sha256": _corpus_manifest_sha256(corpus, manifest, samples),
    }


def _verify_self_digest(document: dict[str, Any], *, expected: str, label: str) -> None:
    recorded = document.get("run_id")
    body = {key: value for key, value in document.items() if key != "run_id"}
    if recorded != _digest(body):
        raise NegotiatedDifferentialError(f"the {label} artifact fails its own self-digest")
    if recorded != expected:
        raise NegotiatedDifferentialError(f"the {label} artifact is not the pinned authority")


def load_reference_artifact(path: Path = REFERENCE_ARTIFACT) -> dict[str, Any]:
    """Load and pin B-088's immutable root used for the submitted net population."""

    document = _load_object(path, label="B-088 reference")
    _verify_self_digest(document, expected=B088_RUN_ID, label="B-088 reference")
    if document.get("schema") != b140.reference.REPORT_SCHEMA:
        raise NegotiatedDifferentialError("the B-088 reference schema is unexpected")
    if document.get("source_commit") != B088_SOURCE_COMMIT:
        raise NegotiatedDifferentialError("the B-088 reference source commit is unexpected")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        raise NegotiatedDifferentialError("the B-088 reference configuration is malformed")
    expected_configuration = {
        "adapter_sha256": B088_ADAPTER_SHA256,
        "adapter_version": b140.SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
        "fixed_grid_step_nm": b140.FIXED_GRID_STEP_NM,
        "router_limits": dict(b140.ROUTER_LIMITS),
        "router_version": b140.ROUTER_VERSION,
        "routing_policy": b140.ROUTING_POLICY,
        "runner_sha256": B088_RUNNER_SHA256,
        "seed": b140.SEED,
    }
    if any(configuration.get(key) != value for key, value in expected_configuration.items()):
        raise NegotiatedDifferentialError("the B-088 reference configuration drifted")
    if _file_digest(B088_RUNNER_PATH) != B088_RUNNER_SHA256:
        raise NegotiatedDifferentialError("the B-088 runner bytes do not match its artifact")
    if _file_digest(B088_ADAPTER_PATH) != B088_ADAPTER_SHA256:
        raise NegotiatedDifferentialError("the B-088 adapter bytes do not match its artifact")
    authority = _reference_authority(document)
    manifest, samples = _load_exact_corpus(b140.CORPUS, authority)
    metrics = document.get("metrics")
    if not isinstance(metrics, dict):
        raise NegotiatedDifferentialError("the B-088 reference metrics are malformed")
    _assert_corpus_summary(metrics.get("corpus"), manifest, len(samples), include_subset_rule=True)
    fixed = metrics.get("configurations", {}).get("fixed")
    if not isinstance(fixed, dict):
        raise NegotiatedDifferentialError("the B-088 fixed configuration is malformed")
    if any(
        fixed.get(key) != value
        for key, value in {
            "boards_offered": CORPUS_COMMITTED_COUNT,
            "boards_imported": CORPUS_COMMITTED_COUNT,
            "nets_attempted": 117,
            "nets_routed": 70,
        }.items()
    ):
        raise NegotiatedDifferentialError("the B-088 fixed population is malformed")
    return document


def load_b140_artifact(path: Path = B140_ARTIFACT) -> dict[str, Any]:
    """Load and pin B-140, including its runner and B-088 authority digests."""

    document = _load_object(path, label="B-140")
    _verify_self_digest(document, expected=B140_RUN_ID, label="B-140")
    if document.get("schema") != B140_REPORT_SCHEMA:
        raise NegotiatedDifferentialError("the B-140 schema is unexpected")
    if document.get("source_commit") != B140_SOURCE_COMMIT:
        raise NegotiatedDifferentialError("the B-140 source commit is unexpected")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        raise NegotiatedDifferentialError("the B-140 configuration is malformed")
    if configuration.get("runner_sha256") != B140_RUNNER_SHA256:
        raise NegotiatedDifferentialError("the B-140 runner authority is unexpected")
    if configuration.get("reference_runner_sha256") != B088_RUNNER_SHA256:
        raise NegotiatedDifferentialError("the B-088 runner authority is unexpected")
    if _file_digest(B140_RUNNER_PATH) != B140_RUNNER_SHA256:
        raise NegotiatedDifferentialError("the B-140 runner bytes do not match its artifact")
    if _file_digest(B088_RUNNER_PATH) != B088_RUNNER_SHA256:
        raise NegotiatedDifferentialError("the B-088 runner bytes do not match its artifact")
    expected_configuration = {
        "adapter_version": b140.SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
        "admission_conjuncts": [
            {"name": name, "stage": stage, "description": description}
            for name, stage, description in b140.ADMISSION_CONJUNCTS
        ],
        "blocking_stages": list(b140.BLOCKING_STAGES),
        "envelope_budgets": dict(b140.ENVELOPE_BUDGETS),
        "fixed_grid_step_nm": b140.FIXED_GRID_STEP_NM,
        "negotiated_routing_policy": b140.NEGOTIATED_ROUTING_POLICY,
        "reference_runner_sha256": B088_RUNNER_SHA256,
        "repair_settings": None,
        "request_local_grid_origins": True,
        "router_limits": dict(b140.ROUTER_LIMITS),
        "router_version": b140.ROUTER_VERSION,
        "routing_policy": b140.ROUTING_POLICY,
        "runner_sha256": B140_RUNNER_SHA256,
        "seed": b140.SEED,
        "selected_layer_pad_count": {"minimum": 2, "maximum": 32},
    }
    if any(configuration.get(key) != value for key, value in expected_configuration.items()):
        raise NegotiatedDifferentialError("the B-140 configuration drifted")
    b088_root = load_reference_artifact()
    authority = _reference_authority(b088_root)
    manifest, samples = _load_exact_corpus(b140.CORPUS, authority)
    metrics = document.get("metrics")
    if not isinstance(metrics, dict):
        raise NegotiatedDifferentialError("the B-140 metrics are malformed")
    _assert_corpus_summary(metrics.get("corpus"), manifest, len(samples), include_subset_rule=False)
    baseline = metrics.get("reference_baseline")
    fixed = b088_root.get("metrics", {}).get("configurations", {}).get("fixed")
    if not isinstance(baseline, dict) or not isinstance(fixed, dict):
        raise NegotiatedDifferentialError("B-140 does not bind the pinned B-088 root")
    if baseline != {
        "artifact": REFERENCE_ARTIFACT.relative_to(ROOT).as_posix(),
        "artifact_run_id": B088_RUN_ID,
        "benchmark": "B-088",
        "grid_policy": "fixed",
        "nets_attempted": fixed.get("nets_attempted"),
        "nets_routed": fixed.get("nets_routed"),
    }:
        raise NegotiatedDifferentialError("B-140 does not bind the pinned B-088 root")
    recorded_primary = metrics.get("configurations", {}).get("b088-routable")
    if not isinstance(recorded_primary, dict) or any(
        recorded_primary.get(key) != value for key, value in _B140_PRIMARY_EXPECTED.items()
    ):
        raise NegotiatedDifferentialError("the pinned B-140 primary aggregate is malformed")
    return document


def _reference_authority(document: dict[str, Any]) -> dict[str, b140.ReferenceBoardAuthority]:
    try:
        return b140._reference_authority_by_board(document)
    except b140.NegotiatedCensusError as error:
        raise NegotiatedDifferentialError("the B-088 per-board authority is malformed") from error


def _build_envelope(
    problem: b140.ImportedProblem, submitted: tuple[b140.SubmittedNet, ...]
) -> NegotiatedRoutingRequest | None:
    if b140._first_unmet(b140._admission(problem, submitted)) is not None:
        return None
    requests = tuple(
        b140._request_for(problem, item.net_id, item.layer_id)
        for item in sorted(submitted, key=lambda entry: entry.net_id)
    )
    try:
        return NegotiatedRoutingRequest(
            board_revision=problem.snapshot.snapshot_digest,
            requests=requests,
            **b140.ENVELOPE_BUDGETS,
        )
    except ValueError as error:
        raise NegotiatedDifferentialError(
            "the independently admitted B-140 envelope refused construction"
        ) from error


def prepare_population(
    corpus: Path = b140.CORPUS,
    *,
    b140_document: dict[str, Any] | None = None,
    b088_document: dict[str, Any] | None = None,
) -> tuple[tuple[PreparedBoard, ...], dict[str, Any]]:
    """Re-derive B-140's exact 20-board/70-net population before either treatment runs."""

    b140_root = b140_document or load_b140_artifact()
    b088_root = b088_document or load_reference_artifact()
    authority = _reference_authority(b088_root)
    manifest, samples = _load_exact_corpus(corpus, authority)
    _assert_corpus_summary(
        b140_root.get("metrics", {}).get("corpus"),
        manifest,
        len(samples),
        include_subset_rule=False,
    )
    router = AStarRouter()
    prepared: list[PreparedBoard] = []
    admitted = 0
    envelope_refusals = 0
    other_refusals = 0
    submitted_total = 0
    routed_total = 0
    for name, payload in samples:
        board_name = Path(name).stem
        try:
            problem = b140.import_simple_route_json(board_name, payload)
        except b140.SimpleRouteJsonImportError as error:
            raise NegotiatedDifferentialError("a B-140 board no longer imports cleanly") from error
        solo = b140._solo_reference(problem, router)
        submitted = b140.PRIMARY.select(solo)
        try:
            routed = b140._assert_reference_authority(
                problem,
                solo,
                authority.get(problem.name),
            )
        except b140.NegotiatedCensusError as error:
            raise NegotiatedDifferentialError("the re-derived B-088 population drifted") from error
        # The projection is immutable after import and the authority check consumed this exact
        # tuple, so no mutable per-board identity is carried into either configuration.
        checked_submitted = b140.PRIMARY.select(solo)
        if checked_submitted != submitted:
            raise NegotiatedDifferentialError("the immutable B-088 projection changed in memory")
        held = b140._admission(problem, submitted)
        unmet = b140._first_unmet(held)
        if unmet is None:
            admitted += 1
        elif unmet == ("at_least_two_requests", "envelope_construction"):
            envelope_refusals += 1
        else:
            other_refusals += 1
        submitted_total += len(submitted)
        routed_total += routed
        prepared.append(
            PreparedBoard(
                problem=problem,
                submitted=submitted,
                envelope=_build_envelope(problem, submitted),
                reference_nets_routed=routed,
            )
        )
    actual = {
        "boards_offered": len(prepared),
        "boards_imported": len(prepared),
        "boards_with_a_constructible_envelope": admitted,
        "boards_admitted_by_the_coordinator": admitted,
        "boards_unable_to_form_a_two_request_envelope": envelope_refusals,
        "other_admission_refusals": other_refusals,
        "nets_submitted": submitted_total,
        "submitted_nets_the_reference_routed": submitted_total,
        "reference_per_net_nets_routed": routed_total,
    }
    if actual != {
        **_B140_PRIMARY_EXPECTED,
        "boards_unable_to_form_a_two_request_envelope": 4,
        "other_admission_refusals": 0,
    }:
        raise NegotiatedDifferentialError(
            "B-140 exact admission/population parity was not reproduced"
        )
    recorded_primary = b140_root.get("metrics", {}).get("configurations", {}).get("b088-routable")
    if not isinstance(recorded_primary, dict) or any(
        recorded_primary.get(key) != value for key, value in _B140_PRIMARY_EXPECTED.items()
    ):
        raise NegotiatedDifferentialError("the pinned B-140 primary aggregate is malformed")
    if len(prepared) != 20 or sum(board.envelope is not None for board in prepared) != 16:
        raise NegotiatedDifferentialError("B-140 envelope admission parity was not reproduced")
    return tuple(prepared), {
        "boards_offered": len(prepared),
        "boards_imported": len(prepared),
        "boards_with_a_constructible_envelope": admitted,
        "boards_admitted_by_the_coordinator": admitted,
        "boards_unable_to_form_a_two_request_envelope": envelope_refusals,
        "nets_submitted": submitted_total,
        "submitted_nets_the_reference_routed": submitted_total,
        "reference_per_net_nets_routed": routed_total,
    }


def _refusal_code(result: NegotiatedRoutingResult) -> str:
    """Map current result diagnostics to a fixed, non-echoing refusal vocabulary."""

    if result.status is NegotiatedRoutingStatus.COMPLETED:
        return ""
    if result.status is NegotiatedRoutingStatus.PARTIAL:
        return "partial_budget"
    if result.status is NegotiatedRoutingStatus.INVALID_REQUEST:
        return "invalid_request"
    if result.status is NegotiatedRoutingStatus.CANCELLED:
        return "cancelled"
    if result.status is not NegotiatedRoutingStatus.NO_PATH:
        raise NegotiatedDifferentialError("the coordinator returned an unknown terminal status")
    diagnostic = (result.diagnostic or "").lower()
    if "physical clearance" in diagnostic or "pairwise physical clearance" in diagnostic:
        return "no_path_physical_clearance"
    if "budget" in diagnostic or "iteration" in diagnostic:
        return "no_path_budget"
    if "no path" in diagnostic or "produced a candidate" in diagnostic:
        return "no_path_search"
    return "no_path_other"


def _repair_evidence(result: NegotiatedRoutingResult) -> Any:
    return getattr(result, "repair_evidence", None)


def _published_repair_evidence(result: NegotiatedRoutingResult) -> Any:
    """Expose repair evidence only for a completed transaction that actually published it."""

    evidence = _repair_evidence(result)
    if result.status is NegotiatedRoutingStatus.COMPLETED and evidence is not None:
        return evidence
    return None


def _projection(result: NegotiatedRoutingResult) -> dict[str, Any]:
    """Return a closed immutable-result projection with no identities or geometry."""

    refusal = _refusal_code(result)
    evidence = _published_repair_evidence(result)
    return {
        "status": result.status.value,
        "refusal_code": refusal or None,
        "iterations": result.iterations,
        "ripups": result.ripups,
        "candidate_count": len(result.candidates),
        "connection_count": len(result.connections),
        "unrouted_count": len(result.unrouted_nets),
        "overflow_units": result.overflow_units,
        "total_wire_length_nm": result.total_wire_length_nm,
        "total_physical_checks": result.total_physical_checks,
        "repair_evidence_published": evidence is not None,
    }


def _repair_work(result: NegotiatedRoutingResult) -> dict[str, int]:
    """Project success-only repair evidence and inherited candidate work into counters."""

    evidence = _published_repair_evidence(result)
    if evidence is None:
        return {
            "published_repairs": 0,
            "inherited_search_expansions": 0,
            "inherited_search_obstacle_checks": 0,
            "inherited_proximity_steps": 0,
            "inherited_proximity_cost_nm": 0,
            "repair_local_expanded_states": 0,
            "repair_projection_obstacle_checks": 0,
            "repair_validator_edge_checks": 0,
            "repair_validator_obstacle_checks": 0,
        }
    return {
        "published_repairs": 1,
        "inherited_search_expansions": sum(
            item.metrics.expanded_states for item in result.candidates
        ),
        "inherited_search_obstacle_checks": sum(
            item.metrics.obstacle_checks for item in result.candidates
        ),
        "inherited_proximity_steps": sum(item.cost.proximity_steps for item in result.candidates),
        "inherited_proximity_cost_nm": sum(
            item.cost.proximity_cost_nm for item in result.candidates
        ),
        "repair_local_expanded_states": evidence.local_expanded_states,
        "repair_projection_obstacle_checks": evidence.projection_obstacle_checks,
        "repair_validator_edge_checks": evidence.validator_edge_checks,
        "repair_validator_obstacle_checks": evidence.validator_obstacle_checks,
    }


def _empty_counts(codes: tuple[str, ...]) -> dict[str, int]:
    return dict.fromkeys(codes, 0)


def _measure_configuration(
    prepared: tuple[PreparedBoard, ...],
    *,
    treatment: bool,
) -> ConfigurationMeasurement:
    """Measure one configuration over the already-frozen population."""

    router = AStarRouter()
    outcomes = _empty_counts(RUN_OUTCOME_TAXONOMY)
    refusals = _empty_counts(REFUSAL_TAXONOMY)
    repair_outcomes = _empty_counts(REPAIR_OUTCOME_TAXONOMY)
    statuses: Counter[str] = Counter()
    records: list[RunRecord] = []
    completed_boards = 0
    completed_nets = 0
    total_wire = 0
    total_overflow = 0
    total_physical = 0
    total_iterations = 0
    total_ripups = 0
    repair_work = _repair_work(
        NegotiatedRoutingResult(
            status=NegotiatedRoutingStatus.CANCELLED,
            board_revision="sha256:" + "0" * 64,
        )
    )
    repair_work = dict.fromkeys(repair_work, 0)
    for board in prepared:
        if board.envelope is None:
            outcomes["envelope_construction"] += 1
            refusals["envelope_construction"] += 1
            repair_outcomes["not_applicable_envelope_refused"] += 1
            statuses["not_run"] += 1
            records.append(
                RunRecord(
                    result=None,
                    projection={
                        "status": "not_run",
                        "outcome": "envelope_construction",
                        "repair_outcome": "not_applicable_envelope_refused",
                    },
                )
            )
            continue
        settings: RepairTransactionSettings | None
        if treatment:
            settings = RepairTransactionSettings()
        else:
            settings = None
        result = negotiate_routes(
            board.problem.snapshot,
            board.envelope,
            router=router,
            repair_settings=settings,
        )
        evidence = _published_repair_evidence(result)
        if result.status is NegotiatedRoutingStatus.COMPLETED:
            outcome = (
                "completed_with_repair" if evidence is not None else "completed_without_repair"
            )
            outcomes[outcome] += 1
            if evidence is not None:
                repair_outcomes["repair_published"] += 1
            else:
                repair_outcomes["repair_not_published"] += 1
            completed_boards += 1
            completed_nets += len(result.candidates) + len(result.connections)
        else:
            refusal = _refusal_code(result)
            outcomes[refusal] += 1
            refusals[refusal] += 1
            repair_outcomes["repair_not_published"] += 1
        statuses[result.status.value] += 1
        total_wire += result.total_wire_length_nm
        total_overflow += result.overflow_units
        total_physical += result.total_physical_checks
        total_iterations += result.iterations
        total_ripups += result.ripups
        contribution = _repair_work(result)
        repair_work = {key: repair_work[key] + contribution[key] for key in repair_work}
        records.append(RunRecord(result=result, projection=_projection(result)))
    aggregate = {
        "repair_enabled": treatment,
        "repair_settings": None if not treatment else asdict(RepairTransactionSettings()),
        "boards_offered": len(prepared),
        "boards_imported": len(prepared),
        "boards_with_a_constructible_envelope": sum(
            board.envelope is not None for board in prepared
        ),
        "boards_admitted_by_the_coordinator": sum(board.envelope is not None for board in prepared),
        "boards_unable_to_form_a_two_request_envelope": sum(
            board.envelope is None for board in prepared
        ),
        "nets_submitted": sum(len(board.submitted) for board in prepared),
        "submitted_nets_the_reference_routed": sum(
            sum(item.reference_outcome == "routed" for item in board.submitted)
            for board in prepared
        ),
        "reference_per_net_nets_routed": sum(board.reference_nets_routed for board in prepared),
        "boards_completed": completed_boards,
        "negotiated_nets_completed": completed_nets,
        "total_wire_length_nm": total_wire,
        "total_overflow_units": total_overflow,
        "total_physical_checks": total_physical,
        "total_iterations": total_iterations,
        "total_ripups": total_ripups,
        "status_breakdown": dict(sorted(statuses.items())),
        "outcome_breakdown": outcomes,
        "refusal_breakdown": refusals,
        "repair_outcome_breakdown": repair_outcomes,
        "repair_work": repair_work,
        "repair_work_accounting": {
            "successful_repair_evidence_only": True,
            "refusal_work_in_total_physical_checks": True,
            "unpublished_local_projection_and_validator_work": (
                "not exposed by the closed result on refusal"
            ),
        },
    }
    return ConfigurationMeasurement(aggregate=aggregate, records=tuple(records))


def _population_projection(aggregate: dict[str, Any]) -> dict[str, int]:
    keys = (
        "boards_offered",
        "boards_imported",
        "boards_with_a_constructible_envelope",
        "boards_admitted_by_the_coordinator",
        "boards_unable_to_form_a_two_request_envelope",
        "nets_submitted",
        "submitted_nets_the_reference_routed",
        "reference_per_net_nets_routed",
    )
    return {key: aggregate[key] for key in keys if key in aggregate and type(aggregate[key]) is int}


def run_differential(
    repetitions: int = 2,
    corpus: Path = b140.CORPUS,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Run control and treatment repeatedly, comparing complete immutable results."""

    if repetitions != BENCHMARK_REPETITIONS:
        raise ValueError("B-141 requires exactly two repetitions")
    prepared, population = prepare_population(corpus)
    first: DifferentialMeasurement | None = None
    elapsed = {"control": 0.0, "treatment": 0.0}
    for _ in range(repetitions):
        started = time.perf_counter()
        control = _measure_configuration(prepared, treatment=False)
        elapsed["control"] += time.perf_counter() - started
        started = time.perf_counter()
        treatment = _measure_configuration(prepared, treatment=True)
        elapsed["treatment"] += time.perf_counter() - started
        measurement = DifferentialMeasurement(control=control, treatment=treatment)
        if first is None:
            first = measurement
        elif measurement != first:
            raise NegotiatedDifferentialError(
                "control or treatment immutable result projection diverged on replay"
            )
    assert first is not None
    control = first.control.aggregate
    treatment = first.treatment.aggregate
    expected_population = population
    if (
        _population_projection(control) != expected_population
        or _population_projection(treatment) != expected_population
    ):
        raise NegotiatedDifferentialError(
            "control/treatment changed the B-140 population projection"
        )
    control_complete = int(control["negotiated_nets_completed"])
    treatment_complete = int(treatment["negotiated_nets_completed"])
    differential = {
        "boards_completed_delta": int(treatment["boards_completed"])
        - int(control["boards_completed"]),
        "negotiated_nets_completed_delta": treatment_complete - control_complete,
        "total_wire_length_nm_delta": int(treatment["total_wire_length_nm"])
        - int(control["total_wire_length_nm"]),
        "total_overflow_units_delta": int(treatment["total_overflow_units"])
        - int(control["total_overflow_units"]),
        "total_physical_checks_delta": int(treatment["total_physical_checks"])
        - int(control["total_physical_checks"]),
        "positive_completion_delta": treatment_complete > control_complete,
        "verdict": (
            "positive_completion_delta"
            if treatment_complete > control_complete
            else "zero_or_negative_completion_delta"
        ),
    }
    metrics = {
        "population": population,
        "deterministic_replays": True,
        "control": control,
        "treatment": treatment,
        "differential": differential,
        "reference_baseline": {
            "benchmark": "B-088",
            "artifact": REFERENCE_ARTIFACT.relative_to(ROOT).as_posix(),
            "artifact_run_id": B088_RUN_ID,
            "grid_policy": "fixed",
            "nets_routed": 70,
            "nets_attempted": 117,
        },
    }
    timing = {
        "repetitions": repetitions,
        "mean_wall_seconds": {
            name: round(duration / repetitions, 3) for name, duration in elapsed.items()
        },
    }
    return metrics, timing


def _configuration() -> dict[str, Any]:
    repair_settings = asdict(RepairTransactionSettings())
    configuration: dict[str, Any] = {
        "adapter_version": b140.SIMPLE_ROUTE_JSON_ADAPTER_VERSION,
        "router_version": b140.ROUTER_VERSION,
        "routing_policy": b140.ROUTING_POLICY,
        "negotiated_routing_policy": b140.NEGOTIATED_ROUTING_POLICY,
        "fixed_grid_step_nm": b140.FIXED_GRID_STEP_NM,
        "router_limits": dict(b140.ROUTER_LIMITS),
        "envelope_budgets": dict(b140.ENVELOPE_BUDGETS),
        "seed": b140.SEED,
        "request_local_grid_origins": True,
        "selected_layer_pad_count": {"minimum": 2, "maximum": 32},
        "control": {"repair_settings": None},
        "treatment": {
            "repair_settings": repair_settings,
            "repair_settings_profile": "RepairTransactionSettings() defaults",
        },
        "refusal_taxonomy": list(REFUSAL_TAXONOMY),
        "run_outcome_taxonomy": list(RUN_OUTCOME_TAXONOMY),
        "repair_outcome_taxonomy": list(REPAIR_OUTCOME_TAXONOMY),
        "upper_bounds": _upper_bounds(),
        "runner_sha256": _file_digest(ROOT / SCRIPT_PATH),
        "b140_source_commit": B140_SOURCE_COMMIT,
        "b140_runner_sha256": _file_digest(B140_RUNNER_PATH),
        "b140_artifact_sha256": _file_digest(B140_ARTIFACT),
        "b140_artifact_run_id": B140_RUN_ID,
        "reference_source_commit": B088_SOURCE_COMMIT,
        "reference_runner_sha256": _file_digest(REFERENCE_RUNNER_PATH),
        "reference_adapter_sha256": _file_digest(B088_ADAPTER_PATH),
        "reference_artifact_sha256": _file_digest(REFERENCE_ARTIFACT),
        "reference_artifact_run_id": B088_RUN_ID,
    }
    configuration["configuration_sha256"] = _digest(configuration)
    return configuration


def build_report(
    repetitions: int = 2,
    corpus: Path = b140.CORPUS,
    *,
    source_commit: str | None = None,
) -> dict[str, Any]:
    """Build a canonical self-digesting report without writing it."""

    if repetitions != BENCHMARK_REPETITIONS:
        raise ValueError("B-141 requires exactly two repetitions")
    captured_commit = _git_state()[0] if source_commit is None else source_commit
    if _GIT_COMMIT.fullmatch(captured_commit) is None:
        raise NegotiatedDifferentialError("the captured source commit is malformed")
    metrics, timing = run_differential(repetitions, corpus)
    b140_root = load_b140_artifact()
    b088_root = load_reference_artifact()
    authority = _reference_authority(b088_root)
    manifest, samples = _load_exact_corpus(corpus, authority)
    corpus_manifest_sha256 = _corpus_manifest_sha256(corpus, manifest, samples)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "benchmark": "B-141",
        "date_utc": "2026-08-30",
        "source_commit": captured_commit,
        "environment": {"platform": platform.platform(), "python": sys.version.split()[0]},
        "population_binding": {
            "benchmark": "B-140",
            "artifact": B140_ARTIFACT.relative_to(ROOT).as_posix(),
            "artifact_run_id": B140_RUN_ID,
            "configuration": "b088-routable",
            "boards_offered": 20,
            "nets_submitted": 70,
            "corpus_manifest_count": len(samples),
            "corpus_manifest_sha256": corpus_manifest_sha256,
            "admission_partition": {
                "boards_admitted_by_the_coordinator": 16,
                "boards_unable_to_form_a_two_request_envelope": 4,
            },
        },
        "configuration": _configuration(),
        "metrics": metrics,
        "timing": timing,
        "repair_work_definition": (
            "Inherited search and proximity fields are read from a published repaired candidate's "
            "unchanged candidate metrics and cost. Local expansions, Board-IR projection checks, "
            "validator checks, and repair responsibility/final physical work are separate. "
            "Refused transactions publish no repair evidence; their consumed physical work remains "
            "in total_physical_checks and is not fabricated into success-only fields."
        ),
        "differential_definition": (
            "Treatment minus control over the same immutable B-140 snapshots and request tuples. "
            "A positive completion delta is a measured differential only; it is not a "
            "routing-quality, "
            "electrical, DRC, fabrication, or generalisation claim."
        ),
        "not_claimed": [
            "that repair was successful when repair evidence was not published",
            "that a zero or negative differential is evidence that the repair contract is "
            "ineffective",
            "that control and treatment answer a like-for-like quality question against "
            "B-088's independent per-net routes",
            "KiCad DRC, electrical correctness, signal integrity, thermal behaviour, DFM, "
            "fabrication, apply, editor, hardware, or network behaviour",
            "any board, net, revision, candidate, path, geometry, or private corpus identity",
            "generalisation beyond the exact committed 20-board B-088 subset",
        ],
    }
    # Loading the root after the run protects against an authority changing while measurement was
    # in progress; its pinned run ID and digest are included in configuration above.
    if b140_root.get("run_id") != B140_RUN_ID:
        raise NegotiatedDifferentialError("the B-140 authority changed during report construction")
    report["run_id"] = _digest(report)
    validate_report(report)
    return report


def _nested_keys(value: Any) -> set[str]:
    if isinstance(value, dict):
        return set(value) | {item for child in value.values() for item in _nested_keys(child)}
    if isinstance(value, list):
        return {item for child in value for item in _nested_keys(child)}
    return set()


FORBIDDEN_PUBLIC_KEYS = frozenset(
    {
        "board",
        "board_id",
        "board_revision",
        "boards",
        "candidate",
        "candidate_id",
        "candidate_ids",
        "coordinates",
        "document_sha256",
        "geometry",
        "net",
        "net_id",
        "net_ids",
        "paths",
        "revision",
        "segments",
        "submitted_net_ids",
        "vertices",
    }
)


_STATUS_TAXONOMY = frozenset(
    {"completed", "no_path", "partial", "invalid_request", "cancelled", "not_run"}
)
_REPAIR_WORK_KEYS = frozenset(
    {
        "published_repairs",
        "inherited_search_expansions",
        "inherited_search_obstacle_checks",
        "inherited_proximity_steps",
        "inherited_proximity_cost_nm",
        "repair_local_expanded_states",
        "repair_projection_obstacle_checks",
        "repair_validator_edge_checks",
        "repair_validator_obstacle_checks",
    }
)
_B141_POPULATION_EXPECTED: dict[str, int] = {
    **_B140_PRIMARY_EXPECTED,
    "boards_unable_to_form_a_two_request_envelope": 4,
}
_DEFAULT_REPAIR_SETTINGS = asdict(RepairTransactionSettings())


def _upper_bounds() -> dict[str, dict[str, int]]:
    """Derive closed aggregate ceilings from the pinned population and declared budgets."""

    admitted = _B141_POPULATION_EXPECTED["boards_admitted_by_the_coordinator"]
    submitted = _B141_POPULATION_EXPECTED["nets_submitted"]
    iterations = b140.ENVELOPE_BUDGETS["max_iterations"]
    route_calls = admitted * submitted * iterations
    max_grid_path_length_nm = b140.ROUTER_LIMITS["max_grid_nodes"] * b140.FIXED_GRID_STEP_NM
    max_final_candidates_per_repaired_board = 32
    per_repair_work = {
        "inherited_search_expansions": (
            max_final_candidates_per_repaired_board * b140.ROUTER_LIMITS["max_expansions"]
        ),
        "inherited_search_obstacle_checks": (
            max_final_candidates_per_repaired_board * b140.ROUTER_LIMITS["max_obstacle_checks"]
        ),
        "inherited_proximity_steps": (
            max_final_candidates_per_repaired_board * b140.ROUTER_LIMITS["max_grid_nodes"]
        ),
        "inherited_proximity_cost_nm": (
            max_final_candidates_per_repaired_board * max_grid_path_length_nm
        ),
        "repair_local_expanded_states": (
            _DEFAULT_REPAIR_SETTINGS["max_attempts"]
            * _DEFAULT_REPAIR_SETTINGS["max_local_expansions"]
        ),
        "repair_projection_obstacle_checks": (
            _DEFAULT_REPAIR_SETTINGS["max_attempts"]
            * (
                _DEFAULT_REPAIR_SETTINGS["max_projection_cells"]
                + _DEFAULT_REPAIR_SETTINGS["max_validator_obstacle_checks"]
            )
        ),
        "repair_validator_edge_checks": (
            _DEFAULT_REPAIR_SETTINGS["max_attempts"]
            * _DEFAULT_REPAIR_SETTINGS["max_validator_path_edges"]
        ),
        "repair_validator_obstacle_checks": (
            _DEFAULT_REPAIR_SETTINGS["max_attempts"]
            * _DEFAULT_REPAIR_SETTINGS["max_validator_obstacle_checks"]
        ),
    }
    return {
        "population": {
            "boards_offered": _B141_POPULATION_EXPECTED["boards_offered"],
            "boards_imported": _B141_POPULATION_EXPECTED["boards_imported"],
            "boards_admitted_by_the_coordinator": admitted,
            "boards_with_a_constructible_envelope": admitted,
            "boards_unable_to_form_a_two_request_envelope": (
                _B141_POPULATION_EXPECTED["boards_unable_to_form_a_two_request_envelope"]
            ),
            "nets_submitted": submitted,
            "submitted_nets_the_reference_routed": submitted,
            "reference_per_net_nets_routed": submitted,
        },
        "completion_totals": {
            "boards_completed": admitted,
            "negotiated_nets_completed": submitted,
            "total_iterations": admitted * iterations,
            "total_ripups": route_calls,
            "total_wire_length_nm": admitted * submitted * max_grid_path_length_nm,
            "total_overflow_units": admitted * iterations * submitted * submitted,
            "total_physical_checks": admitted * b140.ENVELOPE_BUDGETS["max_total_physical_checks"],
        },
        "repair_work": {
            "published_repairs": admitted,
            **{key: admitted * value for key, value in per_repair_work.items()},
        },
        "repair_work_per_published_repair": per_repair_work,
    }


def _require_nonnegative_int(value: Any, message: str) -> int:
    if type(value) is not int or value < 0:
        raise NegotiatedDifferentialError(message)
    return value


def _validate_counter(value: Any, expected_keys: tuple[str, ...], message: str) -> dict[str, int]:
    if not isinstance(value, dict) or set(value) != set(expected_keys):
        raise NegotiatedDifferentialError(message)
    result: dict[str, int] = {}
    for key in expected_keys:
        result[key] = _require_nonnegative_int(value[key], message)
    return result


def _validate_repair_work(aggregate: dict[str, Any]) -> dict[str, int]:
    work = aggregate.get("repair_work")
    if not isinstance(work, dict) or set(work) != _REPAIR_WORK_KEYS:
        raise NegotiatedDifferentialError("the B-141 repair work accounting is malformed")
    return {
        key: _require_nonnegative_int(work[key], "the B-141 repair work accounting is malformed")
        for key in _REPAIR_WORK_KEYS
    }


def _validate_timing(timing: Any, *, require_exact_repetitions: bool) -> None:
    if not isinstance(timing, dict) or set(timing) != {"mean_wall_seconds", "repetitions"}:
        raise NegotiatedDifferentialError("the B-141 timing record is malformed")
    repetitions = timing.get("repetitions")
    if type(repetitions) is not int or repetitions < 1:
        raise NegotiatedDifferentialError("the B-141 timing repetition count is malformed")
    if require_exact_repetitions and repetitions != BENCHMARK_REPETITIONS:
        raise NegotiatedDifferentialError("B-141 requires exactly two repetitions")
    means = timing.get("mean_wall_seconds")
    if not isinstance(means, dict) or set(means) != {"control", "treatment"}:
        raise NegotiatedDifferentialError("the B-141 timing means are malformed")
    for value in means.values():
        if isinstance(value, bool) or not isinstance(value, int | float):
            raise NegotiatedDifferentialError("the B-141 timing means are malformed")
        try:
            finite = math.isfinite(float(value))
        except (OverflowError, ValueError) as error:
            raise NegotiatedDifferentialError("the B-141 timing means are malformed") from error
        if not finite or value < 0:
            raise NegotiatedDifferentialError("the B-141 timing means are malformed")


def _validate_nonnegative_integer_tree(value: Any, message: str) -> None:
    if not isinstance(value, dict):
        raise NegotiatedDifferentialError(message)
    for child in value.values():
        if isinstance(child, dict):
            _validate_nonnegative_integer_tree(child, message)
        elif type(child) is not int or child < 0:
            raise NegotiatedDifferentialError(message)


def _validate_configuration_shape(configuration: Any) -> None:
    required_keys = {
        "adapter_version",
        "router_version",
        "routing_policy",
        "negotiated_routing_policy",
        "fixed_grid_step_nm",
        "router_limits",
        "envelope_budgets",
        "seed",
        "request_local_grid_origins",
        "selected_layer_pad_count",
        "control",
        "treatment",
        "refusal_taxonomy",
        "run_outcome_taxonomy",
        "repair_outcome_taxonomy",
        "upper_bounds",
        "runner_sha256",
        "b140_source_commit",
        "b140_runner_sha256",
        "b140_artifact_sha256",
        "b140_artifact_run_id",
        "reference_source_commit",
        "reference_runner_sha256",
        "reference_adapter_sha256",
        "reference_artifact_sha256",
        "reference_artifact_run_id",
        "configuration_sha256",
    }
    if not isinstance(configuration, dict) or set(configuration) != required_keys:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if (
        not isinstance(configuration.get("adapter_version"), str)
        or not isinstance(configuration.get("router_version"), str)
        or not isinstance(configuration.get("routing_policy"), str)
        or not isinstance(configuration.get("negotiated_routing_policy"), str)
        or type(configuration.get("fixed_grid_step_nm")) is not int
        or configuration["fixed_grid_step_nm"] < 0
        or type(configuration.get("seed")) is not int
        or configuration["seed"] < 0
        or configuration.get("request_local_grid_origins") is not True
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if (
        not isinstance(configuration.get("runner_sha256"), str)
        or _SHA256.fullmatch(configuration["runner_sha256"]) is None
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    for key in (
        "b140_runner_sha256",
        "b140_artifact_sha256",
        "b140_artifact_run_id",
        "reference_runner_sha256",
        "reference_adapter_sha256",
        "reference_artifact_sha256",
        "reference_artifact_run_id",
    ):
        if (
            not isinstance(configuration.get(key), str)
            or _SHA256.fullmatch(configuration[key]) is None
        ):
            raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    for key in ("b140_source_commit", "reference_source_commit"):
        if (
            not isinstance(configuration.get(key), str)
            or _GIT_COMMIT.fullmatch(configuration[key]) is None
        ):
            raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    nested_keys = {
        "router_limits": {
            "max_grid_nodes",
            "max_expansions",
            "max_obstacles",
            "max_obstacle_checks",
        },
        "envelope_budgets": {
            "max_iterations",
            "present_penalty_nm",
            "history_penalty_nm",
            "max_total_expansions",
            "max_total_obstacle_checks",
            "max_total_physical_checks",
        },
        "selected_layer_pad_count": {"minimum", "maximum"},
    }
    for key, expected in nested_keys.items():
        if not isinstance(configuration.get(key), dict) or set(configuration[key]) != expected:
            raise NegotiatedDifferentialError("the B-141 configuration is malformed")
        _validate_nonnegative_integer_tree(
            configuration[key], "the B-141 configuration is malformed"
        )
    if (
        configuration["adapter_version"] != b140.SIMPLE_ROUTE_JSON_ADAPTER_VERSION
        or configuration["router_version"] != b140.ROUTER_VERSION
        or configuration["routing_policy"] != b140.ROUTING_POLICY
        or configuration["negotiated_routing_policy"] != b140.NEGOTIATED_ROUTING_POLICY
        or configuration["fixed_grid_step_nm"] != b140.FIXED_GRID_STEP_NM
        or configuration["router_limits"] != dict(b140.ROUTER_LIMITS)
        or configuration["envelope_budgets"] != dict(b140.ENVELOPE_BUDGETS)
        or configuration["seed"] != b140.SEED
        or configuration["selected_layer_pad_count"] != {"minimum": 2, "maximum": 32}
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if configuration["control"] != {"repair_settings": None}:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    treatment = configuration.get("treatment")
    if (
        not isinstance(treatment, dict)
        or set(treatment) != {"repair_settings", "repair_settings_profile"}
        or treatment["repair_settings_profile"] != "RepairTransactionSettings() defaults"
        or not isinstance(treatment["repair_settings"], dict)
        or set(treatment["repair_settings"]) != set(_DEFAULT_REPAIR_SETTINGS)
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    _validate_nonnegative_integer_tree(
        treatment["repair_settings"], "the B-141 configuration is malformed"
    )
    if treatment["repair_settings"] != _DEFAULT_REPAIR_SETTINGS:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if configuration["refusal_taxonomy"] != list(REFUSAL_TAXONOMY):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if configuration["run_outcome_taxonomy"] != list(RUN_OUTCOME_TAXONOMY):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if configuration["repair_outcome_taxonomy"] != list(REPAIR_OUTCOME_TAXONOMY):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    bounds = configuration.get("upper_bounds")
    if not isinstance(bounds, dict) or set(bounds) != {
        "population",
        "completion_totals",
        "repair_work",
        "repair_work_per_published_repair",
    }:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    for key in (
        "population",
        "completion_totals",
        "repair_work",
        "repair_work_per_published_repair",
    ):
        _validate_nonnegative_integer_tree(bounds[key], "the B-141 configuration is malformed")
    if bounds != _upper_bounds():
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if set(bounds["population"]) != set(_B141_POPULATION_EXPECTED):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if set(bounds["completion_totals"]) != {
        "boards_completed",
        "negotiated_nets_completed",
        "total_iterations",
        "total_ripups",
        "total_wire_length_nm",
        "total_overflow_units",
        "total_physical_checks",
    }:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if set(bounds["repair_work"]) != _REPAIR_WORK_KEYS:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if set(bounds["repair_work_per_published_repair"]) != _REPAIR_WORK_KEYS - {"published_repairs"}:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")


def _validate_aggregate(
    aggregate: Any,
    *,
    treatment: bool,
    population: dict[str, int],
    bounds: dict[str, dict[str, int]],
) -> dict[str, Any]:
    if not isinstance(aggregate, dict):
        raise NegotiatedDifferentialError("the B-141 control/treatment aggregate is malformed")
    try:
        projected_population = _population_projection(aggregate)
    except (KeyError, TypeError, ValueError) as error:
        raise NegotiatedDifferentialError(
            "the B-141 arm population projection is malformed"
        ) from error
    if projected_population != population:
        raise NegotiatedDifferentialError("the B-141 arm population projection drifted")
    if aggregate.get("repair_enabled") is not treatment:
        raise NegotiatedDifferentialError("the B-141 arm repair boundary is malformed")
    expected_settings = _DEFAULT_REPAIR_SETTINGS if treatment else None
    recorded_settings = aggregate.get("repair_settings")
    if treatment:
        _validate_nonnegative_integer_tree(
            recorded_settings, "the B-141 arm repair settings are malformed"
        )
    if recorded_settings != expected_settings:
        raise NegotiatedDifferentialError("the B-141 arm repair settings are malformed")
    for key in (
        "boards_completed",
        "negotiated_nets_completed",
        "total_wire_length_nm",
        "total_overflow_units",
        "total_physical_checks",
        "total_iterations",
        "total_ripups",
    ):
        value = _require_nonnegative_int(aggregate.get(key), "the B-141 arm totals are malformed")
        if value > bounds["completion_totals"][key]:
            raise NegotiatedDifferentialError("the B-141 arm total exceeds its closed bound")
    outcomes = _validate_counter(
        aggregate.get("outcome_breakdown"),
        RUN_OUTCOME_TAXONOMY,
        "the B-141 outcome taxonomy is not closed",
    )
    refusals = _validate_counter(
        aggregate.get("refusal_breakdown"),
        REFUSAL_TAXONOMY,
        "the B-141 refusal taxonomy is not closed",
    )
    repair_outcomes = _validate_counter(
        aggregate.get("repair_outcome_breakdown"),
        REPAIR_OUTCOME_TAXONOMY,
        "the B-141 repair taxonomy is not closed",
    )
    offered = population["boards_offered"]
    refusal_total = sum(outcomes[key] for key in REFUSAL_TAXONOMY)
    completed_without_repair = outcomes["completed_without_repair"]
    completed_with_repair = outcomes["completed_with_repair"]
    completed_total = completed_without_repair + completed_with_repair
    completed_nets = aggregate["negotiated_nets_completed"]
    if completed_total == 0:
        valid_completion_net_range = completed_nets == 0
    else:
        valid_completion_net_range = 2 * completed_total <= completed_nets <= 32 * completed_total
    if (
        sum(outcomes.values()) != offered
        or refusal_total + completed_total != offered
        or sum(refusals.values()) != refusal_total
        or aggregate["boards_completed"] != completed_total
        or not valid_completion_net_range
        or repair_outcomes["not_applicable_envelope_refused"] != outcomes["envelope_construction"]
        or repair_outcomes["repair_published"] != completed_with_repair
        or repair_outcomes["repair_not_published"]
        != refusal_total - outcomes["envelope_construction"] + completed_without_repair
        or sum(repair_outcomes.values()) != offered
    ):
        raise NegotiatedDifferentialError("the B-141 arm totals do not reconcile")
    statuses = aggregate.get("status_breakdown")
    if not isinstance(statuses, dict) or set(statuses).difference(_STATUS_TAXONOMY):
        raise NegotiatedDifferentialError("the B-141 status taxonomy is malformed")
    for value in statuses.values():
        _require_nonnegative_int(value, "the B-141 status taxonomy is malformed")
    if (
        sum(statuses.values()) != offered
        or statuses.get("not_run", 0) != outcomes["envelope_construction"]
        or statuses.get("completed", 0) != completed_total
    ):
        raise NegotiatedDifferentialError("the B-141 status totals do not reconcile")
    work = _validate_repair_work(aggregate)
    if any(work[key] > bounds["repair_work"][key] for key in _REPAIR_WORK_KEYS):
        raise NegotiatedDifferentialError("the B-141 repair work exceeds its closed bound")
    if work["published_repairs"] != repair_outcomes["repair_published"]:
        raise NegotiatedDifferentialError("the B-141 published repair count drifted")
    per_repair_bounds = bounds["repair_work_per_published_repair"]
    published_repairs = work["published_repairs"]
    if any(
        work[key] > published_repairs * per_repair_bounds[key]
        for key in _REPAIR_WORK_KEYS
        if key != "published_repairs"
    ):
        raise NegotiatedDifferentialError("the B-141 repair work exceeds its per-repair bound")
    if work["published_repairs"] == 0 and any(
        value for key, value in work.items() if key != "published_repairs"
    ):
        raise NegotiatedDifferentialError("the B-141 unpublished repair work is malformed")
    accounting = aggregate.get("repair_work_accounting")
    if accounting != {
        "refusal_work_in_total_physical_checks": True,
        "successful_repair_evidence_only": True,
        "unpublished_local_projection_and_validator_work": (
            "not exposed by the closed result on refusal"
        ),
    }:
        raise NegotiatedDifferentialError("the B-141 repair work boundary is malformed")
    return aggregate


def _validate_differential(
    metrics: dict[str, Any], control: dict[str, Any], treatment: dict[str, Any]
) -> None:
    differential = metrics.get("differential")
    if not isinstance(differential, dict):
        raise NegotiatedDifferentialError("the B-141 differential is malformed")
    expected = {
        "boards_completed_delta": treatment["boards_completed"] - control["boards_completed"],
        "negotiated_nets_completed_delta": treatment["negotiated_nets_completed"]
        - control["negotiated_nets_completed"],
        "total_wire_length_nm_delta": treatment["total_wire_length_nm"]
        - control["total_wire_length_nm"],
        "total_overflow_units_delta": treatment["total_overflow_units"]
        - control["total_overflow_units"],
        "total_physical_checks_delta": treatment["total_physical_checks"]
        - control["total_physical_checks"],
        "positive_completion_delta": treatment["negotiated_nets_completed"]
        > control["negotiated_nets_completed"],
        "verdict": (
            "positive_completion_delta"
            if treatment["negotiated_nets_completed"] > control["negotiated_nets_completed"]
            else "zero_or_negative_completion_delta"
        ),
    }
    if differential != expected:
        raise NegotiatedDifferentialError("the B-141 differential totals do not reconcile")


_COMMITMENT_KEYS = frozenset(
    {
        "schema",
        "artifact_path",
        "artifact_sha256",
        "artifact_run_id",
        "source_commit",
        "runner_sha256",
        "configuration_sha256",
        "repetitions",
        "population",
        "control",
        "treatment",
        "run_id",
    }
)
_COMMITMENT_ARM_KEYS = frozenset(
    {"boards_completed", "negotiated_nets_completed", "repair_published", "completed_with_repair"}
)
_COMMITMENT_CONTROL_EXPECTED = {
    "boards_completed": 0,
    "negotiated_nets_completed": 0,
    "repair_published": 0,
    "completed_with_repair": 0,
}
_COMMITMENT_TREATMENT_EXPECTED = {
    "boards_completed": 1,
    "negotiated_nets_completed": 2,
    "repair_published": 1,
    "completed_with_repair": 1,
}


def _validate_commitment_arm(value: Any, expected: dict[str, int]) -> None:
    if (
        not isinstance(value, dict)
        or set(value) != _COMMITMENT_ARM_KEYS
        or any(type(item) is not int for item in value.values())
        or value != expected
    ):
        raise NegotiatedDifferentialError("the B-141 commitment arm pin is malformed")


def _measurement_arm_pin(aggregate: Any) -> dict[str, Any]:
    if not isinstance(aggregate, dict):
        raise NegotiatedDifferentialError("the B-141 commitment arm pin is malformed")
    repairs = aggregate.get("repair_outcome_breakdown")
    outcomes = aggregate.get("outcome_breakdown")
    if not isinstance(repairs, dict) or not isinstance(outcomes, dict):
        raise NegotiatedDifferentialError("the B-141 commitment arm pin is malformed")
    return {
        "boards_completed": aggregate.get("boards_completed"),
        "negotiated_nets_completed": aggregate.get("negotiated_nets_completed"),
        "repair_published": repairs.get("repair_published"),
        "completed_with_repair": outcomes.get("completed_with_repair"),
    }


def _validate_exact_measurement_pins(document: dict[str, Any]) -> None:
    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("population") != _B141_POPULATION_EXPECTED:
        raise NegotiatedDifferentialError("the B-141 commitment population pin is malformed")
    _validate_commitment_arm(
        _measurement_arm_pin(metrics.get("control")), _COMMITMENT_CONTROL_EXPECTED
    )
    _validate_commitment_arm(
        _measurement_arm_pin(metrics.get("treatment")), _COMMITMENT_TREATMENT_EXPECTED
    )


def validate_commitment(document: dict[str, Any]) -> None:
    """Validate the closed, self-digest companion commitment without reading live authorities."""

    if not isinstance(document, dict) or set(document) != _COMMITMENT_KEYS:
        raise NegotiatedDifferentialError("the B-141 commitment keys are malformed")
    if document.get("schema") != COMMITMENT_SCHEMA:
        raise NegotiatedDifferentialError("the B-141 commitment schema is malformed")
    if document.get("artifact_path") != DEFAULT_OUTPUT.relative_to(ROOT).as_posix():
        raise NegotiatedDifferentialError("the B-141 commitment artifact path is malformed")
    for key in ("artifact_sha256", "artifact_run_id", "runner_sha256", "configuration_sha256"):
        value = document.get(key)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise NegotiatedDifferentialError("the B-141 commitment digest pin is malformed")
    source_commit = document.get("source_commit")
    if not isinstance(source_commit, str) or _GIT_COMMIT.fullmatch(source_commit) is None:
        raise NegotiatedDifferentialError("the B-141 commitment source revision is malformed")
    if type(document.get("repetitions")) is not int:
        raise NegotiatedDifferentialError("B-141 requires exactly two repetitions")
    if document["repetitions"] != BENCHMARK_REPETITIONS:
        raise NegotiatedDifferentialError("B-141 requires exactly two repetitions")
    population = document.get("population")
    if (
        not isinstance(population, dict)
        or set(population) != set(_B141_POPULATION_EXPECTED)
        or any(type(value) is not int for value in population.values())
        or population != _B141_POPULATION_EXPECTED
    ):
        raise NegotiatedDifferentialError("the B-141 commitment population pin is malformed")
    _validate_commitment_arm(document.get("control"), _COMMITMENT_CONTROL_EXPECTED)
    _validate_commitment_arm(document.get("treatment"), _COMMITMENT_TREATMENT_EXPECTED)
    recorded = document.get("run_id")
    if not isinstance(recorded, str) or _SHA256.fullmatch(recorded) is None:
        raise NegotiatedDifferentialError("the B-141 commitment run ID is malformed")
    body = {key: value for key, value in document.items() if key != "run_id"}
    if recorded != _digest(body):
        raise NegotiatedDifferentialError("the B-141 commitment fails its own self-digest")


def _build_commitment_from_bytes(
    document: dict[str, Any], artifact_path: Path, artifact_bytes: bytes
) -> dict[str, Any]:
    validate_report(document)
    _validate_exact_measurement_pins(document)
    candidate = Path(artifact_path).expanduser()
    if candidate.is_symlink() or candidate.resolve(strict=False) != DEFAULT_OUTPUT.resolve():
        raise NegotiatedDifferentialError("the B-141 commitment artifact path is malformed")
    try:
        decoded = json.loads(
            artifact_bytes.decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (UnicodeError, ValueError) as error:
        raise NegotiatedDifferentialError("the B-141 artifact is unreadable") from error
    if decoded != document:
        raise NegotiatedDifferentialError("the B-141 artifact bytes do not match its report")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    commitment: dict[str, Any] = {
        "schema": COMMITMENT_SCHEMA,
        "artifact_path": DEFAULT_OUTPUT.relative_to(ROOT).as_posix(),
        "artifact_sha256": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
        "artifact_run_id": document["run_id"],
        "source_commit": document["source_commit"],
        "runner_sha256": configuration["runner_sha256"],
        "configuration_sha256": configuration["configuration_sha256"],
        "repetitions": BENCHMARK_REPETITIONS,
        "population": dict(_B141_POPULATION_EXPECTED),
        "control": dict(_COMMITMENT_CONTROL_EXPECTED),
        "treatment": dict(_COMMITMENT_TREATMENT_EXPECTED),
    }
    commitment["run_id"] = _digest(commitment)
    validate_commitment(commitment)
    return commitment


def build_commitment(
    document: dict[str, Any], artifact_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    """Build the exact-result commitment after the report has been written."""

    candidate = Path(artifact_path).expanduser()
    try:
        artifact_bytes = candidate.read_bytes()
    except OSError as error:
        raise NegotiatedDifferentialError("the B-141 artifact is unreadable") from error
    return _build_commitment_from_bytes(document, candidate, artifact_bytes)


def load_commitment(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the closed companion commitment."""

    candidate = COMMITMENT_PATH if path is None else Path(path)
    document = _load_object(candidate, label="B-141 commitment")
    validate_commitment(document)
    return document


def _validate_population_binding_shape(population_binding: Any) -> None:
    expected_keys = {
        "benchmark",
        "artifact",
        "artifact_run_id",
        "configuration",
        "boards_offered",
        "nets_submitted",
        "corpus_manifest_count",
        "corpus_manifest_sha256",
        "admission_partition",
    }
    if not isinstance(population_binding, dict) or set(population_binding) != expected_keys:
        raise NegotiatedDifferentialError("the B-141 population binding is malformed")
    if (
        population_binding.get("benchmark") != "B-140"
        or population_binding.get("artifact") != B140_ARTIFACT.relative_to(ROOT).as_posix()
        or population_binding.get("artifact_run_id") != B140_RUN_ID
        or population_binding.get("configuration") != "b088-routable"
        or type(population_binding.get("boards_offered")) is not int
        or population_binding.get("boards_offered") != 20
        or type(population_binding.get("nets_submitted")) is not int
        or population_binding.get("nets_submitted") != 70
        or type(population_binding.get("corpus_manifest_count")) is not int
        or population_binding.get("corpus_manifest_count") != CORPUS_COMMITTED_COUNT
        or not isinstance(population_binding.get("corpus_manifest_sha256"), str)
        or _SHA256.fullmatch(population_binding["corpus_manifest_sha256"]) is None
    ):
        raise NegotiatedDifferentialError("the B-141 population binding is malformed")
    partition = population_binding.get("admission_partition")
    if partition != {
        "boards_admitted_by_the_coordinator": 16,
        "boards_unable_to_form_a_two_request_envelope": 4,
    }:
        raise NegotiatedDifferentialError("the B-141 population binding is malformed")


def _validate_authoritative_bindings(
    document: dict[str, Any],
    *,
    corpus: Path = b140.CORPUS,
    artifact_path: Path | None = None,
    require_commitment: bool = False,
) -> None:
    """Bind a generic report to the live runner, historical roots, corpus, and optional sidecar."""

    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or configuration != _configuration():
        raise NegotiatedDifferentialError("the B-141 source/configuration binding drifted")
    load_b140_artifact()
    reference = load_reference_artifact()
    authority = _reference_authority(reference)
    manifest, samples = _load_exact_corpus(corpus, authority)
    expected_population_binding = {
        "benchmark": "B-140",
        "artifact": B140_ARTIFACT.relative_to(ROOT).as_posix(),
        "artifact_run_id": B140_RUN_ID,
        "configuration": "b088-routable",
        "boards_offered": 20,
        "nets_submitted": 70,
        "corpus_manifest_count": len(samples),
        "corpus_manifest_sha256": _corpus_manifest_sha256(corpus, manifest, samples),
        "admission_partition": {
            "boards_admitted_by_the_coordinator": 16,
            "boards_unable_to_form_a_two_request_envelope": 4,
        },
    }
    population_binding = document.get("population_binding")
    _validate_population_binding_shape(population_binding)
    if population_binding != expected_population_binding:
        raise NegotiatedDifferentialError("the B-141 corpus manifest binding drifted")
    if not require_commitment:
        return
    candidate = DEFAULT_OUTPUT if artifact_path is None else Path(artifact_path).expanduser()
    if candidate.is_symlink() or candidate.resolve(strict=False) != DEFAULT_OUTPUT.resolve():
        raise NegotiatedDifferentialError("the B-141 commitment artifact path is malformed")
    commitment = load_commitment()
    if (
        commitment["artifact_path"] != DEFAULT_OUTPUT.relative_to(ROOT).as_posix()
        or commitment["artifact_sha256"] != _file_digest(candidate)
        or commitment["artifact_run_id"] != document.get("run_id")
        or commitment["source_commit"] != document.get("source_commit")
        or commitment["runner_sha256"] != configuration.get("runner_sha256")
        or commitment["configuration_sha256"] != configuration.get("configuration_sha256")
        or commitment["repetitions"] != BENCHMARK_REPETITIONS
        or commitment["population"] != _B141_POPULATION_EXPECTED
    ):
        raise NegotiatedDifferentialError("the B-141 commitment binding drifted")
    _validate_exact_measurement_pins(document)
    if _measurement_arm_pin(document["metrics"]["control"]) != commitment["control"]:
        raise NegotiatedDifferentialError("the B-141 control commitment pin drifted")
    if _measurement_arm_pin(document["metrics"]["treatment"]) != commitment["treatment"]:
        raise NegotiatedDifferentialError("the B-141 treatment commitment pin drifted")


def validate_report(
    document: dict[str, Any],
    *,
    corpus: Path = b140.CORPUS,
    verify_live_bindings: bool = False,
    require_semantics: bool = True,
) -> None:
    """Validate generic report structure/semantics; live authorities are opt-in."""

    if not isinstance(document, dict) or document.get("schema") != REPORT_SCHEMA:
        raise NegotiatedDifferentialError("the B-141 report schema is malformed")
    if _GIT_COMMIT.fullmatch(document.get("source_commit", "")) is None:
        raise NegotiatedDifferentialError("the B-141 source revision provenance is malformed")
    recorded = document.get("run_id")
    if not isinstance(recorded, str) or not _SHA256.fullmatch(recorded):
        raise NegotiatedDifferentialError("the B-141 report run ID is malformed")
    body = {key: value for key, value in document.items() if key != "run_id"}
    if recorded != _digest(body):
        raise NegotiatedDifferentialError("the B-141 report fails its own self-digest")
    configuration = document.get("configuration")
    if not isinstance(configuration, dict):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    _validate_configuration_shape(configuration)
    config_digest = configuration.get("configuration_sha256")
    without_digest = {
        key: value for key, value in configuration.items() if key != "configuration_sha256"
    }
    if config_digest != _digest(without_digest):
        raise NegotiatedDifferentialError("the B-141 configuration digest is malformed")
    if _nested_keys(document).intersection(FORBIDDEN_PUBLIC_KEYS):
        raise NegotiatedDifferentialError(
            "the B-141 public report contains private identity fields"
        )
    metrics = document.get("metrics")
    if not isinstance(metrics, dict) or metrics.get("deterministic_replays") is not True:
        raise NegotiatedDifferentialError("the B-141 deterministic replay claim is malformed")
    # Repetition count and timing safety are part of the outer report contract even when a caller
    # intentionally skips aggregate reconciliation for a lightweight source-provenance seam.
    _validate_timing(document.get("timing"), require_exact_repetitions=True)
    population = metrics.get("population")
    if (
        not isinstance(population, dict)
        or set(population) != set(_B141_POPULATION_EXPECTED)
        or any(type(value) is not int for value in population.values())
        or population != _B141_POPULATION_EXPECTED
    ):
        raise NegotiatedDifferentialError("the B-141 population projection is malformed")
    if require_semantics:
        _validate_population_binding_shape(document.get("population_binding"))
        bounds = _upper_bounds()
        control = _validate_aggregate(
            metrics.get("control"), treatment=False, population=population, bounds=bounds
        )
        treatment = _validate_aggregate(
            metrics.get("treatment"), treatment=True, population=population, bounds=bounds
        )
        _validate_differential(metrics, control, treatment)
        baseline = metrics.get("reference_baseline")
        if baseline != {
            "artifact": REFERENCE_ARTIFACT.relative_to(ROOT).as_posix(),
            "artifact_run_id": B088_RUN_ID,
            "benchmark": "B-088",
            "grid_policy": "fixed",
            "nets_routed": 70,
            "nets_attempted": 117,
        }:
            raise NegotiatedDifferentialError("the B-141 reference baseline binding drifted")
    if verify_live_bindings:
        _validate_authoritative_bindings(document, corpus=corpus)


def load_artifact(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Load a B-141 artifact with generic checks plus authoritative companion binding."""

    document = _load_object(path, label="B-141")
    validate_report(document)
    _validate_authoritative_bindings(
        document,
        artifact_path=path,
        require_commitment=True,
    )
    return document


def validate_artifact(path: Path = DEFAULT_OUTPUT) -> dict[str, Any]:
    """Alias suitable for checkers that want the validated document back."""

    return load_artifact(path)


def _new_output_target(output: Path) -> Path:
    candidate_input = output.expanduser()
    if candidate_input.is_symlink():
        raise NegotiatedDifferentialError("artifact output must be a new regular path")
    try:
        parent = candidate_input.parent.resolve(strict=True)
    except OSError as error:
        raise NegotiatedDifferentialError("artifact output parent must already exist") from error
    if not parent.is_dir():
        raise NegotiatedDifferentialError("artifact output parent must be a directory")
    candidate = parent / candidate_input.name
    protected = {B140_ARTIFACT.resolve(), B088_ARTIFACT.resolve()}
    if candidate.resolve(strict=False) in protected:
        raise NegotiatedDifferentialError("artifact output is a protected historical authority")
    if candidate.exists() or candidate.is_symlink():
        raise NegotiatedDifferentialError("artifact output must be a new path")
    return candidate


def _write_exclusive(output: Path, rendered: str) -> tuple[int, int]:
    signature: tuple[int, int] | None = None
    try:
        with output.open("x", encoding="utf-8") as stream:
            stat = os.fstat(stream.fileno())
            signature = stat.st_dev, stat.st_ino
            stream.write(rendered)
        return stat.st_dev, stat.st_ino
    except FileExistsError as error:
        raise NegotiatedDifferentialError("artifact output must remain a new path") from error
    except Exception:
        if signature is not None:
            _remove_created_file(output, signature)
        raise


def _remove_created_file(output: Path, signature: tuple[int, int]) -> None:
    """Rollback only the regular file created by this invocation after sidecar failure."""

    try:
        stat = output.stat()
        if not output.is_symlink() and (stat.st_dev, stat.st_ino) == signature:
            output.unlink()
    except OSError:
        # The primary publication error is more useful than a best-effort rollback failure.
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--repetitions", type=int, choices=(BENCHMARK_REPETITIONS,), default=BENCHMARK_REPETITIONS
    )
    parser.add_argument("--corpus", type=Path, default=b140.CORPUS)
    parser.add_argument(
        "--write", action="store_true", help="write the artifact and its default commitment"
    )
    arguments = parser.parse_args()
    output: Path | None = None
    commitment_output: Path | None = None
    captured_commit: str | None = None
    runner_digest: str | None = None
    allowed_dirty = {SCRIPT_PATH}
    if arguments.write:
        output = _new_output_target(arguments.output)
        try:
            allowed_dirty.add(output.relative_to(ROOT).as_posix())
        except ValueError:
            pass
        if output.resolve(strict=False) == DEFAULT_OUTPUT.resolve():
            commitment_output = _new_output_target(COMMITMENT_PATH)
            allowed_dirty.add(COMMITMENT_RELATIVE_PATH)
        captured_commit = _require_publishable_source(allowed_dirty=frozenset(allowed_dirty))
        runner_digest = _file_digest(ROOT / SCRIPT_PATH)
    report = build_report(
        arguments.repetitions,
        arguments.corpus,
        source_commit=captured_commit,
    )
    rendered = json.dumps(report, allow_nan=False, indent=2, sort_keys=True) + "\n"
    commitment_rendered: str | None = None
    if commitment_output is not None:
        commitment = _build_commitment_from_bytes(
            report, output or DEFAULT_OUTPUT, rendered.encode()
        )
        commitment_rendered = (
            json.dumps(commitment, allow_nan=False, indent=2, sort_keys=True) + "\n"
        )
    if arguments.write:
        assert output is not None and captured_commit is not None and runner_digest is not None
        _require_publishable_source(
            expected_commit=captured_commit,
            allowed_dirty=frozenset(allowed_dirty),
        )
        if _file_digest(ROOT / SCRIPT_PATH) != runner_digest:
            raise NegotiatedDifferentialError("the benchmark runner changed during measurement")
        output_signature = _write_exclusive(output, rendered)
        if commitment_output is not None:
            assert commitment_rendered is not None
            try:
                _write_exclusive(commitment_output, commitment_rendered)
            except Exception:
                _remove_created_file(output, output_signature)
                raise
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
