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
import stat
import subprocess
import sys
import time
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
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
MAX_JSON_ARTIFACT_BYTES = 64 * 1024
# The manifest and licence are read under the descriptor ceiling; every declared sample size is
# preflighted against the separate sample ceiling before any sample descriptor is opened.
MAX_CORPUS_DESCRIPTOR_BYTES = 64 * 1024
MAX_CORPUS_SAMPLE_BYTES = 256 * 1024
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
B141_METRICS_SHA256 = "sha256:f7e38d6744feed63b852e10811f34205bb822a1e2e7ca9759a8cea80a326d4b2"
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
_DATE_UTC = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_EVIDENCE_DATE_ERROR = "the B-141 evidence date is not its recorded commit's UTC date"

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

# Public prose is part of this small JSON contract, rather than an open-ended annotation field.
# Keeping the canonical values in one place makes a self-consistent rewrite unable to smuggle in a
# new claim, path, or identity while still passing the report self-digest.
REPAIR_WORK_DEFINITION = (
    "Inherited search and proximity fields are read from a published repaired candidate's "
    "unchanged candidate metrics and cost. Local expansions, Board-IR projection checks, "
    "validator checks, and repair responsibility/final physical work are separate. "
    "Refused transactions publish no repair evidence; their consumed physical work remains "
    "in total_physical_checks and is not fabricated into success-only fields."
)
DIFFERENTIAL_DEFINITION = (
    "Treatment minus control over the same immutable B-140 snapshots and request tuples. "
    "A positive completion delta is a measured differential only; it is not a "
    "routing-quality, electrical, DRC, fabrication, or generalisation claim."
)
NOT_CLAIMED: tuple[str, ...] = (
    "that repair was successful when repair evidence was not published",
    "that a zero or negative differential is evidence that the repair contract is ineffective",
    "that control and treatment answer a like-for-like quality question against B-088's "
    "independent per-net routes",
    "KiCad DRC, electrical correctness, signal integrity, thermal behaviour, DFM, fabrication, "
    "apply, editor, hardware, or network behaviour",
    "any board, net, revision, candidate, path, geometry, or private corpus identity",
    "generalisation beyond the exact committed 20-board B-088 subset",
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


def _read_bounded_bytes(path: Path, *, label: str) -> bytes:
    """Read one regular JSON artifact, retaining at most the ceiling plus one byte."""

    unreadable = f"the {label} artifact is unreadable"
    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    supports_dir_fd = getattr(os, "supports_dir_fd", ())
    if not nofollow or not directory or not nonblock or os.open not in supports_dir_fd:
        raise NegotiatedDifferentialError(unreadable)
    if any(not callable(getattr(os, primitive, None)) for primitive in ("open", "fstat", "fdopen")):
        raise NegotiatedDifferentialError(unreadable)

    try:
        # Resolve only lexical ``.``/``..`` components.  Calling ``resolve`` here would follow
        # symlinks before the descriptor-relative walk had a chance to reject them.
        absolute = Path(os.path.abspath(os.fspath(path)))  # noqa: PTH100
    except (OSError, TypeError, ValueError) as error:
        raise NegotiatedDifferentialError(unreadable) from error
    if absolute.anchor != os.sep or not absolute.name or len(absolute.parts) < 2:
        raise NegotiatedDifferentialError(unreadable)

    directory_flags = os.O_RDONLY | nofollow | directory | nonblock | getattr(os, "O_CLOEXEC", 0)
    parent_fd = -1
    descriptor = -1
    try:
        parent_fd = os.open(absolute.anchor, directory_flags)
        for component in absolute.parts[1:-1]:
            child_fd = os.open(component, directory_flags, dir_fd=parent_fd)
            os.close(parent_fd)
            parent_fd = child_fd
        descriptor = os.open(
            absolute.name,
            os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0),
            dir_fd=parent_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(MAX_JSON_ARTIFACT_BYTES + 1)
    except OSError as error:
        raise NegotiatedDifferentialError(unreadable) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        if parent_fd >= 0:
            os.close(parent_fd)
    if len(payload) > MAX_JSON_ARTIFACT_BYTES:
        raise NegotiatedDifferentialError(f"the {label} artifact exceeds 64 KiB")
    return payload


def _bounded_file_digest(path: Path, *, label: str) -> str:
    return "sha256:" + hashlib.sha256(_read_bounded_bytes(path, label=label)).hexdigest()


def _open_corpus_directory(path: Path) -> int:
    """Open every corpus path component as a no-follow directory."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if not nofollow or not directory or not nonblock or os.open not in os.supports_dir_fd:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    try:
        # `resolve()` would follow precisely the symlink components this walk must reject.
        absolute = Path(os.path.abspath(os.fspath(path)))  # noqa: PTH100
    except (OSError, TypeError, ValueError) as error:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error
    parts = absolute.parts
    if absolute.anchor != os.sep or not parts:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    flags = os.O_RDONLY | nofollow | directory | nonblock | getattr(os, "O_CLOEXEC", 0)
    current = -1
    try:
        current = os.open(absolute.anchor, flags)
        for component in parts[1:]:
            child = os.open(component, flags, dir_fd=current)
            os.close(current)
            current = child
        return current
    except (OSError, TypeError, ValueError) as error:
        if current >= 0:
            os.close(current)
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error


def _open_corpus_child_directory(directory_fd: int, name: str) -> int:
    """Open one fixed child directory without following its final component."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    directory = getattr(os, "O_DIRECTORY", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if (
        not nofollow
        or not directory
        or not nonblock
        or os.open not in os.supports_dir_fd
        or not name
        or Path(name).name != name
    ):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | directory | nonblock | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        if not stat.S_ISDIR(os.fstat(descriptor).st_mode):
            raise OSError
        return descriptor
    except (OSError, TypeError, ValueError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error


def _read_bounded_at(directory_fd: int, name: str, *, max_bytes: int) -> bytes:
    """Read one fixed child regular file without following its final component."""

    nofollow = getattr(os, "O_NOFOLLOW", 0)
    nonblock = getattr(os, "O_NONBLOCK", 0)
    if (
        not nofollow
        or not nonblock
        or os.open not in os.supports_dir_fd
        or type(max_bytes) is not int
        or max_bytes < 0
        or not name
        or Path(name).name != name
        or "/" in name
        or "\\" in name
        or "\x00" in name
    ):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    descriptor = -1
    try:
        descriptor = os.open(
            name,
            os.O_RDONLY | nofollow | nonblock | getattr(os, "O_CLOEXEC", 0),
            dir_fd=directory_fd,
        )
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise OSError
        with os.fdopen(descriptor, "rb", closefd=True) as stream:
            descriptor = -1
            payload = stream.read(max_bytes + 1)
    except (OSError, TypeError, ValueError) as error:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if len(payload) > max_bytes:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    return payload


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
            old_path, new_path = path.split(" -> ", 1)
            paths.extend((old_path, new_path))
        else:
            paths.append(path)
    if _GIT_COMMIT.fullmatch(commit) is None:
        return "unknown", tuple(paths) or ("<malformed-git-commit>",)
    return commit, tuple(paths)


def _git_path_is_tracked(path: str) -> bool:
    """Return whether a status path is tracked, including a tracked deletion."""

    git = shutil.which("git")
    if git is None:
        raise NegotiatedDifferentialError("the source Git state could not be read")
    candidate = Path(path)
    try:
        relative = (
            candidate.resolve().relative_to(ROOT.resolve())
            if candidate.is_absolute()
            else candidate
        )
    except ValueError:
        return False
    try:
        result = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "ls-files", "--error-unmatch", "--", relative.as_posix()],
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise NegotiatedDifferentialError("the source Git state could not be read") from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise NegotiatedDifferentialError("the source Git state could not be read")


def _require_publishable_source(
    expected_commit: str | None = None,
    *,
    allowed_dirty: frozenset[str] = frozenset(),
) -> str:
    """Fail closed on source drift; only this invocation's new untracked outputs may be dirty."""

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
    if not isinstance(dirty_paths, bool) and any(
        _git_path_is_tracked(path) for path in dirty_paths if path in allowed_dirty
    ):
        raise NegotiatedDifferentialError("artifact publication requires an unchanged source tree")
    if expected_commit is not None and commit != expected_commit:
        raise NegotiatedDifferentialError("the Git revision changed during benchmark measurement")
    return commit


def _validate_source_commit_runner_binding(document: dict[str, Any]) -> None:
    """Require the report runner digest to come from its declared Git revision."""

    source_commit = document.get("source_commit")
    configuration = document.get("configuration")
    if (
        not isinstance(source_commit, str)
        or _GIT_COMMIT.fullmatch(source_commit) is None
        or not isinstance(configuration, dict)
        or not isinstance(configuration.get("runner_sha256"), str)
        or _SHA256.fullmatch(configuration["runner_sha256"]) is None
    ):
        raise NegotiatedDifferentialError(
            "the B-141 source commit/runner binding could not be verified"
        )
    git = shutil.which("git")
    if git is None:
        raise NegotiatedDifferentialError(
            "the B-141 source commit/runner binding could not be verified"
        )
    try:
        resolved = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "rev-parse", "--verify", "--quiet", f"{source_commit}^{{commit}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        if resolved != source_commit or _GIT_COMMIT.fullmatch(resolved) is None:
            raise ValueError
        runner_bytes = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "show", f"{resolved}:{SCRIPT_PATH}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            timeout=5,
        ).stdout
    except (OSError, subprocess.SubprocessError, ValueError):
        raise NegotiatedDifferentialError(
            "the B-141 source commit/runner binding could not be verified"
        ) from None
    if "sha256:" + hashlib.sha256(runner_bytes).hexdigest() != configuration["runner_sha256"]:
        raise NegotiatedDifferentialError(
            "the B-141 source commit/runner binding could not be verified"
        )


def _commit_utc_date(commit: str) -> str:
    """Return the UTC calendar date the recorded commit was committed on.

    The evidence date is *derived*, never written by hand.  A hand-written label can name a day the
    run did not happen on -- and did, in the first version of this artifact, which claimed a UTC
    date later than the commit it recorded as its own source.  Reading the committer date of the
    recorded revision makes the chronology of the audit record checkable rather than asserted.
    """

    if not isinstance(commit, str) or _GIT_COMMIT.fullmatch(commit) is None:
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR)
    git = shutil.which("git")
    if git is None:
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR)
    try:
        committed = subprocess.run(  # noqa: S603 - fixed local Git executable and argv
            [git, "show", "--no-patch", "--format=%cI", f"{commit}^{{commit}}"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
        moment = datetime.fromisoformat(committed)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR) from error
    if moment.tzinfo is None:
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR)
    return moment.astimezone(UTC).strftime("%Y-%m-%d")


def _validate_evidence_date_binding(document: dict[str, Any]) -> None:
    """Require the published evidence date to be the recorded commit's own UTC date."""

    recorded = document.get("date_utc")
    source_commit = document.get("source_commit")
    if (
        not isinstance(recorded, str)
        or _DATE_UTC.fullmatch(recorded) is None
        or not isinstance(source_commit, str)
    ):
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR)
    if recorded != _commit_utc_date(source_commit):
        raise NegotiatedDifferentialError(_EVIDENCE_DATE_ERROR)


def _load_object(path: Path, *, label: str) -> dict[str, Any]:
    try:
        value = json.loads(
            _read_bounded_bytes(path, label=label).decode("utf-8"),
            parse_constant=_reject_json_constant,
        )
    except (RecursionError, UnicodeError, ValueError) as error:
        raise NegotiatedDifferentialError(f"the {label} artifact is unreadable") from error
    if not isinstance(value, dict):
        raise NegotiatedDifferentialError(f"the {label} artifact is not one JSON object")
    try:
        _assert_finite_json_numbers(value)
    except (RecursionError, ValueError) as error:
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
    if type(files) is not list or len(files) != CORPUS_UPSTREAM_SAMPLE_COUNT:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    entries: list[dict[str, Any]] = []
    names: set[str] = set()
    for entry in files:
        if type(entry) is not dict:
            raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
        name = entry.get("name")
        digest = entry.get("sha256")
        byte_count = entry.get("bytes")
        committed = entry.get("committed")
        if (
            set(entry) != {"bytes", "committed", "name", "sha256"}
            or not isinstance(name, str)
            or not name
            or Path(name).name != name
            or "/" in name
            or "\\" in name
            or "\x00" in name
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
        "copyright_holder": "Zach Dwiel",
        "format": "tscircuit-simple-route-json",
        "license_file": "LICENSE",
        "license_url": "https://github.com/dwiel/tscircuit-benchmark/blob/master/LICENSE",
        "reviewed_on": "2026-08-06",
        "upstream_default_branch": "master",
        "upstream_path": "samples",
    }
    if set(manifest) != {*expected, "files"} or any(
        manifest.get(key) != value for key, value in expected.items()
    ):
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    entries = _manifest_entries(manifest)
    committed = tuple(entry for entry in entries if entry["committed"])
    if len(committed) != CORPUS_COMMITTED_COUNT:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR)
    if any(entry["bytes"] > MAX_CORPUS_SAMPLE_BYTES for entry in entries):
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


def _load_exact_corpus(
    corpus: Path, authority_by_board: dict[str, b140.ReferenceBoardAuthority]
) -> tuple[dict[str, Any], tuple[tuple[str, bytes], ...], str]:
    """Load one corpus and retain the digest of the exact manifest bytes that were validated."""

    root_fd = -1
    samples_fd = -1
    try:
        root_fd = _open_corpus_directory(corpus)
        manifest_bytes = _read_bounded_at(
            root_fd,
            "manifest.json",
            max_bytes=MAX_CORPUS_DESCRIPTOR_BYTES,
        )
        manifest = json.loads(manifest_bytes.decode("utf-8"), parse_constant=_reject_json_constant)
        if not isinstance(manifest, dict):
            raise ValueError
        _assert_finite_json_numbers(manifest)
        entries = _assert_manifest_metadata(manifest)
        license_bytes = _read_bounded_at(root_fd, "LICENSE", max_bytes=MAX_CORPUS_DESCRIPTOR_BYTES)
        if hashlib.sha256(license_bytes).hexdigest() != CORPUS_LICENSE_SHA256:
            raise ValueError
        committed_entries = tuple(
            sorted(
                (entry for entry in entries if entry["committed"]),
                key=lambda entry: entry["name"],
            )
        )
        samples_fd = _open_corpus_child_directory(root_fd, "samples")
        loaded: list[tuple[str, bytes]] = []
        for entry in committed_entries:
            sample = _read_bounded_at(
                samples_fd,
                entry["name"],
                max_bytes=MAX_CORPUS_SAMPLE_BYTES,
            )
            if (
                len(sample) != entry["bytes"]
                or hashlib.sha256(sample).hexdigest() != entry["sha256"]
            ):
                raise ValueError
            loaded.append((entry["name"], sample))
        samples = tuple(loaded)
        _assert_manifest_matches_samples(entries, samples)
        _assert_exact_corpus_membership(samples, authority_by_board)
        manifest_sha256 = "sha256:" + hashlib.sha256(manifest_bytes).hexdigest()
    except (
        NegotiatedDifferentialError,
        OSError,
        RecursionError,
        UnicodeError,
        ValueError,
        TypeError,
    ) as error:
        raise NegotiatedDifferentialError(CORPUS_MANIFEST_ERROR) from error
    finally:
        if samples_fd >= 0:
            os.close(samples_fd)
        if root_fd >= 0:
            os.close(root_fd)
    return manifest, samples, manifest_sha256


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
    _manifest, samples, manifest_sha256 = _load_exact_corpus(corpus, authority_by_board)
    return {
        "corpus_manifest_count": len(samples),
        "corpus_manifest_sha256": manifest_sha256,
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
    manifest, samples, _manifest_sha256 = _load_exact_corpus(b140.CORPUS, authority)
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
    manifest, samples, _manifest_sha256 = _load_exact_corpus(b140.CORPUS, authority)
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
    manifest, samples, _manifest_sha256 = _load_exact_corpus(corpus, authority)
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
        if result.iterations < 1:
            raise NegotiatedDifferentialError("the B-141 admitted board returned zero iterations")
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


def _environment_projection() -> dict[str, Any]:
    """Project only bounded host-independent environment facts into the public report."""

    os_family = platform.system()
    architecture = platform.machine()
    if (
        type(os_family) is not str
        or os_family not in SUPPORTED_OS_FAMILIES
        or type(architecture) is not str
        or architecture not in SUPPORTED_ARCHITECTURES
    ):
        raise NegotiatedDifferentialError("the benchmark environment is outside its closed set")
    version = sys.version_info
    version_values = (version.major, version.minor, version.micro)
    if any(type(value) is not int or not 0 <= value <= 99 for value in version_values):
        raise NegotiatedDifferentialError("the benchmark Python version is outside its closed set")
    return {
        "os_family": os_family,
        "architecture": architecture,
        "python_version": {
            "major": version_values[0],
            "minor": version_values[1],
            "micro": version_values[2],
        },
    }


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
    # Derived from the revision this run actually read, never typed in by whoever published it.
    captured_date_utc = _commit_utc_date(captured_commit)
    metrics, timing = run_differential(repetitions, corpus)
    b140_root = load_b140_artifact()
    b088_root = load_reference_artifact()
    authority = _reference_authority(b088_root)
    _manifest, samples, corpus_manifest_sha256 = _load_exact_corpus(corpus, authority)
    report: dict[str, Any] = {
        "schema": REPORT_SCHEMA,
        "benchmark": "B-141",
        "date_utc": captured_date_utc,
        "source_commit": captured_commit,
        "environment": _environment_projection(),
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
        "repair_work_definition": REPAIR_WORK_DEFINITION,
        "differential_definition": DIFFERENTIAL_DEFINITION,
        "not_claimed": list(NOT_CLAIMED),
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
_CONTROL_REPAIR_CLAIMS_ERROR = "the B-141 control arm contains repair claims"
_STATUS_OUTCOME_RECONCILIATION_ERROR = (
    "the B-141 status counts do not reconcile with the outcome taxonomy"
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
_PARTITION_DECLARATION_ERROR = "the B-141 declared partitions do not cover their taxonomy exactly"
# One fixed, named refusal per partitioned breakdown.  A single shared string would tell an auditor
# that something failed to reconcile but not which count/breakdown pair contradicted the other.
_PARTITION_RECONCILIATION_ERRORS: dict[str, str] = {
    "outcome_breakdown": "the B-141 outcome breakdown does not partition its own taxonomy",
    "refusal_breakdown": "the B-141 refusal and outcome taxonomies disagree",
    "repair_outcome_breakdown": (
        "the B-141 repair outcome breakdown does not partition the run outcomes"
    ),
    "status_breakdown": _STATUS_OUTCOME_RECONCILIATION_ERROR,
}


@dataclass(frozen=True, slots=True)
class _DeclaredPartition:
    """One published breakdown, declared as a partition of the run-outcome taxonomy."""

    buckets: tuple[tuple[str, tuple[str, ...]], ...]
    total: bool


# The #226 discipline -- a total partition plus a sum-reconciliation guard that fails the run on
# mismatch -- applied to every count/breakdown pair this benchmark publishes, in the report and in
# its companion.  Each declared bucket names the run-outcome codes it stands for.  Reconciling only
# a grand total accepts a report that moved one item between buckets and left the total alone;
# reconciling every bucket against the codes it partitions does not, and reconciling the
# *declaration* against the taxonomy means a code added later cannot land nowhere and be published
# as a quiet zero.  `_assert_declared_partitions` checks the declaration, `_reconcile_partitions`
# checks a published document against it.
_STATUS_OUTCOME_PARTITION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("cancelled", ("cancelled",)),
    ("completed", ("completed_without_repair", "completed_with_repair")),
    ("invalid_request", ("invalid_request",)),
    (
        "no_path",
        ("no_path_physical_clearance", "no_path_budget", "no_path_search", "no_path_other"),
    ),
    ("not_run", ("envelope_construction",)),
    ("partial", ("partial_budget",)),
)
_REPAIR_OUTCOME_PARTITION: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("not_applicable_envelope_refused", ("envelope_construction",)),
    ("repair_published", ("completed_with_repair",)),
    (
        "repair_not_published",
        (
            "partial_budget",
            "no_path_physical_clearance",
            "no_path_budget",
            "no_path_search",
            "no_path_other",
            "invalid_request",
            "cancelled",
            "completed_without_repair",
        ),
    ),
)
# A refusal reason is its own bucket: the refusal breakdown must agree with the outcome breakdown
# reason by reason, not merely in sum.  It is a partial partition -- it deliberately does not cover
# the two completion codes -- so its buckets are reconciled without a population total.
_REFUSAL_OUTCOME_PARTITION: tuple[tuple[str, tuple[str, ...]], ...] = tuple(
    (code, (code,)) for code in REFUSAL_TAXONOMY
)
_ARM_BREAKDOWN_PARTITIONS: dict[str, _DeclaredPartition] = {
    "outcome_breakdown": _DeclaredPartition(
        buckets=tuple((code, (code,)) for code in RUN_OUTCOME_TAXONOMY), total=True
    ),
    "refusal_breakdown": _DeclaredPartition(buckets=_REFUSAL_OUTCOME_PARTITION, total=False),
    "repair_outcome_breakdown": _DeclaredPartition(buckets=_REPAIR_OUTCOME_PARTITION, total=True),
    "status_breakdown": _DeclaredPartition(buckets=_STATUS_OUTCOME_PARTITION, total=True),
}
_PARTITION_TAXONOMIES: dict[str, frozenset[str]] = {
    "outcome_breakdown": frozenset(RUN_OUTCOME_TAXONOMY),
    "refusal_breakdown": frozenset(REFUSAL_TAXONOMY),
    "repair_outcome_breakdown": frozenset(REPAIR_OUTCOME_TAXONOMY),
    "status_breakdown": _STATUS_TAXONOMY,
}


def _assert_declared_partitions() -> None:
    """Fail the run when a declared breakdown stops partitioning its taxonomy exactly once."""

    if (
        set(_ARM_BREAKDOWN_PARTITIONS) != set(_PARTITION_TAXONOMIES)
        or set(_ARM_BREAKDOWN_PARTITIONS) != set(_PARTITION_RECONCILIATION_ERRORS)
        or len(set(_PARTITION_RECONCILIATION_ERRORS.values()))
        != len(_PARTITION_RECONCILIATION_ERRORS)
    ):
        raise NegotiatedDifferentialError(_PARTITION_DECLARATION_ERROR)
    for name, partition in _ARM_BREAKDOWN_PARTITIONS.items():
        buckets = [bucket for bucket, _codes in partition.buckets]
        if len(buckets) != len(set(buckets)) or set(buckets) != _PARTITION_TAXONOMIES[name]:
            raise NegotiatedDifferentialError(_PARTITION_DECLARATION_ERROR)
        assigned = [code for _bucket, codes in partition.buckets for code in codes]
        if len(assigned) != len(set(assigned)) or not set(assigned).issubset(
            set(RUN_OUTCOME_TAXONOMY)
        ):
            raise NegotiatedDifferentialError(_PARTITION_DECLARATION_ERROR)
        if partition.total and set(assigned) != set(RUN_OUTCOME_TAXONOMY):
            raise NegotiatedDifferentialError(_PARTITION_DECLARATION_ERROR)


def _reconcile_partitions(outcomes: dict[str, int], document: dict[str, Any]) -> None:
    """Require every published bucket to equal the outcome codes its declaration partitions.

    A total partition's buckets sum to the outcome total by construction, so no separate
    population check is repeated here; the sum guard below is what refuses a bucket the
    declaration does not name -- the case where a breakdown's own key set is not otherwise closed,
    as in the companion.
    """

    _assert_declared_partitions()
    for name, partition in _ARM_BREAKDOWN_PARTITIONS.items():
        message = _PARTITION_RECONCILIATION_ERRORS[name]
        published = document.get(name)
        if type(published) is not dict:
            raise NegotiatedDifferentialError(message)
        declared_total = 0
        for bucket, codes in partition.buckets:
            expected = sum(outcomes[code] for code in codes)
            declared_total += expected
            if _require_nonnegative_int(published.get(bucket, 0), message) != expected:
                raise NegotiatedDifferentialError(message)
        if sum(published.values()) != declared_total:
            raise NegotiatedDifferentialError(message)


_B141_POPULATION_EXPECTED: dict[str, int] = {
    **_B140_PRIMARY_EXPECTED,
    "boards_unable_to_form_a_two_request_envelope": 4,
}
_DEFAULT_REPAIR_SETTINGS = asdict(RepairTransactionSettings())

_ROOT_KEYS = frozenset(
    {
        "schema",
        "benchmark",
        "date_utc",
        "source_commit",
        "environment",
        "population_binding",
        "configuration",
        "metrics",
        "timing",
        "repair_work_definition",
        "differential_definition",
        "not_claimed",
        "run_id",
    }
)
_METRICS_KEYS = frozenset(
    {
        "population",
        "deterministic_replays",
        "control",
        "treatment",
        "differential",
        "reference_baseline",
    }
)
_POPULATION_KEYS = frozenset(_B141_POPULATION_EXPECTED)
_AGGREGATE_KEYS = frozenset(
    {
        *_POPULATION_KEYS,
        "repair_enabled",
        "repair_settings",
        "boards_completed",
        "negotiated_nets_completed",
        "total_wire_length_nm",
        "total_overflow_units",
        "total_physical_checks",
        "total_iterations",
        "total_ripups",
        "status_breakdown",
        "outcome_breakdown",
        "refusal_breakdown",
        "repair_outcome_breakdown",
        "repair_work",
        "repair_work_accounting",
    }
)
_REPAIR_SETTINGS_KEYS = frozenset(_DEFAULT_REPAIR_SETTINGS)
_REPAIR_WORK_ACCOUNTING_KEYS = frozenset(
    {
        "successful_repair_evidence_only",
        "refusal_work_in_total_physical_checks",
        "unpublished_local_projection_and_validator_work",
    }
)
_DIFFERENTIAL_KEYS = frozenset(
    {
        "boards_completed_delta",
        "negotiated_nets_completed_delta",
        "total_wire_length_nm_delta",
        "total_overflow_units_delta",
        "total_physical_checks_delta",
        "positive_completion_delta",
        "verdict",
    }
)
_REFERENCE_BASELINE_KEYS = frozenset(
    {"benchmark", "artifact", "artifact_run_id", "grid_policy", "nets_routed", "nets_attempted"}
)
_TIMING_KEYS = frozenset({"repetitions", "mean_wall_seconds"})
_TIMING_MEAN_KEYS = frozenset({"control", "treatment"})
ENVIRONMENT_KEYS = frozenset({"os_family", "architecture", "python_version"})
PYTHON_VERSION_KEYS = frozenset({"major", "minor", "micro"})
SUPPORTED_OS_FAMILIES = frozenset({"Darwin", "Linux", "Windows"})
SUPPORTED_ARCHITECTURES = frozenset({"arm64", "aarch64", "x86_64", "AMD64"})
_POPULATION_BINDING_KEYS = frozenset(
    {
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
)
_ADMISSION_PARTITION_KEYS = frozenset(
    {"boards_admitted_by_the_coordinator", "boards_unable_to_form_a_two_request_envelope"}
)
_AGGREGATE_TOTAL_KEYS = frozenset(
    {
        "boards_completed",
        "negotiated_nets_completed",
        "total_wire_length_nm",
        "total_overflow_units",
        "total_physical_checks",
        "total_iterations",
        "total_ripups",
    }
)


def _upper_bounds() -> dict[str, dict[str, int]]:
    """Derive closed aggregate ceilings from the pinned population and declared budgets.

    Each ceiling is derived from the population that actually applies to the quantity it bounds.
    The failure this guards against is multiplying two population-wide aggregates together --
    ``nets_submitted * boards_admitted`` -- when the 70 submitted nets are *already distributed
    across* the 16 admitted boards.  That product is not a bound on anything: it is 16 copies of a
    population that only exists once.  Each derivation below therefore states which denominator it
    uses and why.
    """

    admitted = _B141_POPULATION_EXPECTED["boards_admitted_by_the_coordinator"]
    submitted = _B141_POPULATION_EXPECTED["nets_submitted"]
    iterations = b140.ENVELOPE_BUDGETS["max_iterations"]
    # Per net, not per net-board: a submitted request may be ripped up once per iteration after the
    # first, and each request belongs to exactly one board's envelope.
    max_ripups = submitted * (iterations - 1)
    max_grid_path_length_nm = b140.ROUTER_LIMITS["max_grid_nodes"] * b140.FIXED_GRID_STEP_NM
    # ``overflow_units`` is ``sum(usage - 1)`` over one board's *best* iteration's overflowed
    # resources, so the iteration count does not multiply it and neither does the board count.
    # Per board the summed resource usage cannot exceed that board's own nets times the grid each
    # may occupy; summing over boards collapses the per-board net counts back to the 70 submitted
    # nets.  The previous ``admitted * iterations * submitted**2`` had no such reading -- it
    # multiplied two population-wide aggregates by a per-board repeat count -- and at 627,200 it
    # was also small enough to refuse a truthful measurement from one dense board, which is the
    # dangerous direction for a ceiling.
    max_overflow_units = submitted * b140.ROUTER_LIMITS["max_grid_nodes"]
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
            # Per admitted board: one board can complete at most once.
            "boards_completed": admitted,
            # Per submitted net: a net completes on the one board that submitted it.
            "negotiated_nets_completed": submitted,
            # Per admitted board: the iteration budget is a per-board envelope budget.
            "total_iterations": admitted * iterations,
            "total_ripups": max_ripups,
            # Per submitted net: each returned candidate is capped at one grid path length.
            "total_wire_length_nm": submitted * max_grid_path_length_nm,
            "total_overflow_units": max_overflow_units,
            # Per admitted board: the physical-check budget is a per-board envelope budget.
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


def _require_exact_dict(value: Any, expected_keys: frozenset[str], message: str) -> dict[str, Any]:
    """Require a plain JSON object with the complete contract key set."""

    if type(value) is not dict or set(value) != expected_keys:
        raise NegotiatedDifferentialError(message)
    return value


def _validate_exact_string_list(value: Any, expected: tuple[str, ...], message: str) -> list[str]:
    if (
        type(value) is not list
        or value != list(expected)
        or any(type(item) is not str for item in value)
    ):
        raise NegotiatedDifferentialError(message)
    return value


def _validate_nonnegative_integer_object(
    value: Any, expected_keys: frozenset[str], message: str
) -> dict[str, int]:
    record = _require_exact_dict(value, expected_keys, message)
    return {key: _require_nonnegative_int(record[key], message) for key in expected_keys}


def _validate_counter(value: Any, expected_keys: tuple[str, ...], message: str) -> dict[str, int]:
    if type(value) is not dict or set(value) != set(expected_keys):
        raise NegotiatedDifferentialError(message)
    result: dict[str, int] = {}
    for key in expected_keys:
        result[key] = _require_nonnegative_int(value[key], message)
    return result


def _validate_repair_work(aggregate: dict[str, Any]) -> dict[str, int]:
    work = aggregate.get("repair_work")
    if type(work) is not dict or set(work) != _REPAIR_WORK_KEYS:
        raise NegotiatedDifferentialError("the B-141 repair work accounting is malformed")
    return {
        key: _require_nonnegative_int(work[key], "the B-141 repair work accounting is malformed")
        for key in _REPAIR_WORK_KEYS
    }


def _validate_timing(timing: Any, *, require_exact_repetitions: bool) -> None:
    if type(timing) is not dict or set(timing) != _TIMING_KEYS:
        raise NegotiatedDifferentialError("the B-141 timing record is malformed")
    repetitions = timing.get("repetitions")
    if type(repetitions) is not int or repetitions < 1:
        raise NegotiatedDifferentialError("the B-141 timing repetition count is malformed")
    if require_exact_repetitions and repetitions != BENCHMARK_REPETITIONS:
        raise NegotiatedDifferentialError("B-141 requires exactly two repetitions")
    means = timing.get("mean_wall_seconds")
    if type(means) is not dict or set(means) != _TIMING_MEAN_KEYS:
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
    if type(value) is not dict:
        raise NegotiatedDifferentialError(message)
    for child in value.values():
        if isinstance(child, dict):
            _validate_nonnegative_integer_tree(child, message)
        elif type(child) is not int or child < 0:
            raise NegotiatedDifferentialError(message)


def _status_keys_for_outcomes(outcomes: dict[str, int]) -> frozenset[str]:
    """Derive the exact sparse status shape emitted by the aggregate builder."""

    statuses: set[str] = set()
    if outcomes["envelope_construction"]:
        statuses.add("not_run")
    if outcomes["completed_without_repair"] or outcomes["completed_with_repair"]:
        statuses.add("completed")
    if outcomes["partial_budget"]:
        statuses.add("partial")
    if outcomes["invalid_request"]:
        statuses.add("invalid_request")
    if outcomes["cancelled"]:
        statuses.add("cancelled")
    if any(outcomes[code] for code in REFUSAL_TAXONOMY if code.startswith("no_path_")):
        statuses.add("no_path")
    return frozenset(statuses)


def _validate_aggregate_shape(aggregate: Any, *, treatment: bool) -> dict[str, Any]:
    """Validate every serialized arm object before applying its semantic bounds."""

    record = _require_exact_dict(
        aggregate,
        _AGGREGATE_KEYS,
        "the B-141 control/treatment aggregate is malformed",
    )
    for key in _POPULATION_KEYS:
        _require_nonnegative_int(record[key], "the B-141 arm population projection is malformed")
    if type(record["repair_enabled"]) is not bool or record["repair_enabled"] is not treatment:
        raise NegotiatedDifferentialError("the B-141 arm repair boundary is malformed")
    settings = record["repair_settings"]
    if treatment:
        _validate_nonnegative_integer_object(
            settings, _REPAIR_SETTINGS_KEYS, "the B-141 arm repair settings are malformed"
        )
    elif settings is not None:
        raise NegotiatedDifferentialError("the B-141 arm repair settings are malformed")
    for key in _AGGREGATE_TOTAL_KEYS:
        _require_nonnegative_int(record[key], "the B-141 arm totals are malformed")
    outcomes = _validate_counter(
        record["outcome_breakdown"],
        RUN_OUTCOME_TAXONOMY,
        "the B-141 outcome taxonomy is not closed",
    )
    _validate_counter(
        record["refusal_breakdown"],
        REFUSAL_TAXONOMY,
        "the B-141 refusal taxonomy is not closed",
    )
    _validate_counter(
        record["repair_outcome_breakdown"],
        REPAIR_OUTCOME_TAXONOMY,
        "the B-141 repair taxonomy is not closed",
    )
    statuses = record["status_breakdown"]
    expected_status_keys = _status_keys_for_outcomes(outcomes)
    actual_status_keys = set(statuses) if type(statuses) is dict else None
    if type(statuses) is not dict or (
        actual_status_keys != expected_status_keys
        and not (
            "completed" not in expected_status_keys
            and actual_status_keys == expected_status_keys | {"completed"}
            and statuses["completed"] == 0
        )
    ):
        raise NegotiatedDifferentialError("the B-141 status taxonomy is malformed")
    for value in statuses.values():
        _require_nonnegative_int(value, "the B-141 status taxonomy is malformed")
    _validate_repair_work(record)
    accounting = record["repair_work_accounting"]
    if type(accounting) is not dict or set(accounting) != _REPAIR_WORK_ACCOUNTING_KEYS:
        raise NegotiatedDifferentialError("the B-141 repair work boundary is malformed")
    if accounting != {
        "refusal_work_in_total_physical_checks": True,
        "successful_repair_evidence_only": True,
        "unpublished_local_projection_and_validator_work": (
            "not exposed by the closed result on refusal"
        ),
    }:
        raise NegotiatedDifferentialError("the B-141 repair work boundary is malformed")
    return record


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
    if type(configuration) is not dict or set(configuration) != required_keys:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if (
        type(configuration.get("adapter_version")) is not str
        or type(configuration.get("router_version")) is not str
        or type(configuration.get("routing_policy")) is not str
        or type(configuration.get("negotiated_routing_policy")) is not str
        or type(configuration.get("fixed_grid_step_nm")) is not int
        or configuration["fixed_grid_step_nm"] < 0
        or type(configuration.get("seed")) is not int
        or configuration["seed"] < 0
        or configuration.get("request_local_grid_origins") is not True
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if (
        type(configuration.get("runner_sha256")) is not str
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
        if type(configuration.get(key)) is not str or _SHA256.fullmatch(configuration[key]) is None:
            raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    for key in ("b140_source_commit", "reference_source_commit"):
        if (
            type(configuration.get(key)) is not str
            or _GIT_COMMIT.fullmatch(configuration[key]) is None
        ):
            raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    if (
        type(configuration.get("configuration_sha256")) is not str
        or _SHA256.fullmatch(configuration["configuration_sha256"]) is None
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
        _validate_nonnegative_integer_object(
            configuration[key], frozenset(expected), "the B-141 configuration is malformed"
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
    control = _require_exact_dict(
        configuration["control"],
        frozenset({"repair_settings"}),
        "the B-141 configuration is malformed",
    )
    if control["repair_settings"] is not None:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    treatment = configuration.get("treatment")
    treatment = _require_exact_dict(
        treatment,
        frozenset({"repair_settings", "repair_settings_profile"}),
        "the B-141 configuration is malformed",
    )
    if (
        type(treatment["repair_settings_profile"]) is not str
        or treatment["repair_settings_profile"] != "RepairTransactionSettings() defaults"
    ):
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    _validate_nonnegative_integer_object(
        treatment["repair_settings"], _REPAIR_SETTINGS_KEYS, "the B-141 configuration is malformed"
    )
    if treatment["repair_settings"] != _DEFAULT_REPAIR_SETTINGS:
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")
    _validate_exact_string_list(
        configuration["refusal_taxonomy"], REFUSAL_TAXONOMY, "the B-141 configuration is malformed"
    )
    _validate_exact_string_list(
        configuration["run_outcome_taxonomy"],
        RUN_OUTCOME_TAXONOMY,
        "the B-141 configuration is malformed",
    )
    _validate_exact_string_list(
        configuration["repair_outcome_taxonomy"],
        REPAIR_OUTCOME_TAXONOMY,
        "the B-141 configuration is malformed",
    )
    bounds = configuration.get("upper_bounds")
    _require_exact_dict(
        bounds,
        frozenset(
            {"population", "completion_totals", "repair_work", "repair_work_per_published_repair"}
        ),
        "the B-141 configuration is malformed",
    )
    _validate_nonnegative_integer_object(
        bounds["population"], _POPULATION_KEYS, "the B-141 configuration is malformed"
    )
    _validate_nonnegative_integer_object(
        bounds["completion_totals"],
        frozenset(_AGGREGATE_TOTAL_KEYS),
        "the B-141 configuration is malformed",
    )
    _validate_nonnegative_integer_object(
        bounds["repair_work"], _REPAIR_WORK_KEYS, "the B-141 configuration is malformed"
    )
    _validate_nonnegative_integer_object(
        bounds["repair_work_per_published_repair"],
        _REPAIR_WORK_KEYS - {"published_repairs"},
        "the B-141 configuration is malformed",
    )
    if bounds != _upper_bounds():
        raise NegotiatedDifferentialError("the B-141 configuration is malformed")


def _validate_aggregate(
    aggregate: Any,
    *,
    treatment: bool,
    population: dict[str, int],
    bounds: dict[str, dict[str, int]],
) -> dict[str, Any]:
    aggregate = _validate_aggregate_shape(aggregate, treatment=treatment)
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
    if aggregate["total_iterations"] < aggregate["boards_admitted_by_the_coordinator"]:
        raise NegotiatedDifferentialError("the B-141 arm iteration floor is not met")
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
    work = _validate_repair_work(aggregate)
    if not treatment and (
        outcomes["completed_with_repair"]
        or repair_outcomes["repair_published"]
        or any(work[key] for key in _REPAIR_WORK_KEYS)
    ):
        raise NegotiatedDifferentialError(_CONTROL_REPAIR_CLAIMS_ERROR)
    if (
        outcomes["envelope_construction"]
        != population["boards_unable_to_form_a_two_request_envelope"]
    ):
        raise NegotiatedDifferentialError(
            "the B-141 envelope refusal count drifted from the fixed population"
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
    ):
        raise NegotiatedDifferentialError("the B-141 arm totals do not reconcile")
    statuses = aggregate.get("status_breakdown")
    if not isinstance(statuses, dict) or set(statuses).difference(_STATUS_TAXONOMY):
        raise NegotiatedDifferentialError("the B-141 status taxonomy is malformed")
    for value in statuses.values():
        _require_nonnegative_int(value, "the B-141 status taxonomy is malformed")
    # Every count/breakdown pair, reconciled bucket by bucket against the outcome codes each
    # bucket is declared to partition.  A reason moved between buckets with the totals patched up
    # fails here even though every grand total still adds up.
    _reconcile_partitions(outcomes, aggregate)
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


def _validate_environment_shape(environment: Any) -> None:
    record = _require_exact_dict(
        environment, ENVIRONMENT_KEYS, "the B-141 environment record is malformed"
    )
    os_family = record["os_family"]
    architecture = record["architecture"]
    if (
        type(os_family) is not str
        or os_family not in SUPPORTED_OS_FAMILIES
        or type(architecture) is not str
        or architecture not in SUPPORTED_ARCHITECTURES
    ):
        raise NegotiatedDifferentialError("the B-141 environment record is malformed")
    version = _validate_nonnegative_integer_object(
        record["python_version"], PYTHON_VERSION_KEYS, "the B-141 environment record is malformed"
    )
    if any(value > 99 for value in version.values()):
        raise NegotiatedDifferentialError("the B-141 environment record is malformed")


def _validate_reference_baseline_shape(reference: Any) -> None:
    record = _require_exact_dict(
        reference, _REFERENCE_BASELINE_KEYS, "the B-141 reference baseline is malformed"
    )
    if (
        type(record["benchmark"]) is not str
        or record["benchmark"] != "B-088"
        or type(record["artifact"]) is not str
        or record["artifact"] != REFERENCE_ARTIFACT.relative_to(ROOT).as_posix()
        or type(record["artifact_run_id"]) is not str
        or _SHA256.fullmatch(record["artifact_run_id"]) is None
        or record["artifact_run_id"] != B088_RUN_ID
        or type(record["grid_policy"]) is not str
        or record["grid_policy"] != "fixed"
        or type(record["nets_routed"]) is not int
        or record["nets_routed"] < 0
        or type(record["nets_attempted"]) is not int
        or record["nets_attempted"] < 0
        or record["nets_routed"] != 70
        or record["nets_attempted"] != 117
    ):
        raise NegotiatedDifferentialError("the B-141 reference baseline is malformed")


def _validate_differential_shape(differential: Any) -> None:
    record = _require_exact_dict(
        differential, _DIFFERENTIAL_KEYS, "the B-141 differential is malformed"
    )
    for key in (
        "boards_completed_delta",
        "negotiated_nets_completed_delta",
        "total_wire_length_nm_delta",
        "total_overflow_units_delta",
        "total_physical_checks_delta",
    ):
        if type(record[key]) is not int:
            raise NegotiatedDifferentialError("the B-141 differential is malformed")
    if (
        type(record["positive_completion_delta"]) is not bool
        or type(record["verdict"]) is not str
        or record["verdict"]
        not in {"positive_completion_delta", "zero_or_negative_completion_delta"}
    ):
        raise NegotiatedDifferentialError("the B-141 differential is malformed")


def _validate_population_shape(population: Any) -> None:
    record = _require_exact_dict(
        population, _POPULATION_KEYS, "the B-141 population projection is malformed"
    )
    for value in record.values():
        _require_nonnegative_int(value, "the B-141 population projection is malformed")
    if record != _B141_POPULATION_EXPECTED:
        raise NegotiatedDifferentialError("the B-141 population projection is malformed")


def _validate_metrics_shape(metrics: Any) -> None:
    record = _require_exact_dict(metrics, _METRICS_KEYS, "the B-141 metrics record is malformed")
    if type(record["deterministic_replays"]) is not bool or not record["deterministic_replays"]:
        raise NegotiatedDifferentialError("the B-141 deterministic replay claim is malformed")
    _validate_population_shape(record["population"])
    _validate_aggregate_shape(record["control"], treatment=False)
    _validate_aggregate_shape(record["treatment"], treatment=True)
    _validate_differential_shape(record["differential"])
    _validate_reference_baseline_shape(record["reference_baseline"])


def _validate_report_shape(document: Any) -> dict[str, Any]:
    """Validate the complete public JSON shape before any semantic or authority checks."""

    record = _require_exact_dict(document, _ROOT_KEYS, "the B-141 report keys are malformed")
    if (
        type(record["schema"]) is not str
        or record["schema"] != REPORT_SCHEMA
        or type(record["benchmark"]) is not str
        or record["benchmark"] != "B-141"
        or type(record["date_utc"]) is not str
        or _DATE_UTC.fullmatch(record["date_utc"]) is None
        or type(record["source_commit"]) is not str
        or _GIT_COMMIT.fullmatch(record["source_commit"]) is None
        or type(record["repair_work_definition"]) is not str
        or record["repair_work_definition"] != REPAIR_WORK_DEFINITION
        or type(record["differential_definition"]) is not str
        or record["differential_definition"] != DIFFERENTIAL_DEFINITION
    ):
        raise NegotiatedDifferentialError("the B-141 report shape is malformed")
    # The shape check no longer pins a literal date: the value is bound to the recorded commit by
    # `_validate_evidence_date_binding`.  It must still be a real calendar day, so `9999-99-99`
    # cannot pass the regex and then be excused as merely unverified.
    try:
        date.fromisoformat(record["date_utc"])
    except ValueError as error:
        raise NegotiatedDifferentialError("the B-141 report shape is malformed") from error
    _validate_environment_shape(record["environment"])
    _validate_population_binding_shape(record["population_binding"])
    _validate_configuration_shape(record["configuration"])
    _validate_metrics_shape(record["metrics"])
    _validate_timing(record["timing"], require_exact_repetitions=True)
    _validate_exact_string_list(
        record["not_claimed"], NOT_CLAIMED, "the B-141 report claims are malformed"
    )
    if type(record["run_id"]) is not str or _SHA256.fullmatch(record["run_id"]) is None:
        raise NegotiatedDifferentialError("the B-141 report run ID is malformed")
    return record


_COMMITMENT_KEYS = frozenset(
    {
        "schema",
        "artifact_path",
        "artifact_sha256",
        "artifact_run_id",
        "source_commit",
        "runner_sha256",
        "configuration_sha256",
        "metrics_sha256",
        "repetitions",
        "population",
        "control",
        "treatment",
        "differential",
        "run_id",
    }
)
# The arm keys that describe how the arm was *configured* rather than what it measured.  Everything
# else in the arm schema is a claim, and every claim is signed.
_ARM_CONFIGURATION_KEYS = frozenset(
    {
        *_POPULATION_KEYS,
        "repair_enabled",
        "repair_settings",
        "repair_work_accounting",
    }
)
# Derived from the report's own arm schema rather than listed by hand.  The previous list pinned
# only the completion counts, so re-signing both files could move `total_physical_checks` or
# `total_wire_length_nm` -- the two headline numbers this benchmark exists to publish -- while the
# "exact result" commitment still validated.  Deriving the key set means adding a claimed aggregate
# to `_AGGREGATE_KEYS` without pinning it fails `_assert_commitment_covers_claims` at build and at
# validation, instead of quietly publishing an unsigned number.
_COMMITMENT_ARM_KEYS = frozenset(_AGGREGATE_KEYS) - _ARM_CONFIGURATION_KEYS
_COMMITMENT_CONTROL_EXPECTED: dict[str, Any] = {
    "boards_completed": 0,
    "negotiated_nets_completed": 0,
    "total_wire_length_nm": 0,
    "total_overflow_units": 0,
    "total_physical_checks": 11326,
    "total_iterations": 128,
    "total_ripups": 0,
    "outcome_breakdown": {
        "cancelled": 0,
        "completed_with_repair": 0,
        "completed_without_repair": 0,
        "envelope_construction": 4,
        "invalid_request": 0,
        "no_path_budget": 0,
        "no_path_other": 0,
        "no_path_physical_clearance": 16,
        "no_path_search": 0,
        "partial_budget": 0,
    },
    "refusal_breakdown": {
        "cancelled": 0,
        "envelope_construction": 4,
        "invalid_request": 0,
        "no_path_budget": 0,
        "no_path_other": 0,
        "no_path_physical_clearance": 16,
        "no_path_search": 0,
        "partial_budget": 0,
    },
    "repair_outcome_breakdown": {
        "not_applicable_envelope_refused": 4,
        "repair_not_published": 16,
        "repair_published": 0,
    },
    "status_breakdown": {"no_path": 16, "not_run": 4},
    "repair_work": {
        "inherited_proximity_cost_nm": 0,
        "inherited_proximity_steps": 0,
        "inherited_search_expansions": 0,
        "inherited_search_obstacle_checks": 0,
        "published_repairs": 0,
        "repair_local_expanded_states": 0,
        "repair_projection_obstacle_checks": 0,
        "repair_validator_edge_checks": 0,
        "repair_validator_obstacle_checks": 0,
    },
}
_COMMITMENT_TREATMENT_EXPECTED: dict[str, Any] = {
    "boards_completed": 1,
    "negotiated_nets_completed": 2,
    "total_wire_length_nm": 43750000,
    "total_overflow_units": 0,
    "total_physical_checks": 18758,
    "total_iterations": 121,
    "total_ripups": 0,
    "outcome_breakdown": {
        "cancelled": 0,
        "completed_with_repair": 1,
        "completed_without_repair": 0,
        "envelope_construction": 4,
        "invalid_request": 0,
        "no_path_budget": 0,
        "no_path_other": 0,
        "no_path_physical_clearance": 15,
        "no_path_search": 0,
        "partial_budget": 0,
    },
    "refusal_breakdown": {
        "cancelled": 0,
        "envelope_construction": 4,
        "invalid_request": 0,
        "no_path_budget": 0,
        "no_path_other": 0,
        "no_path_physical_clearance": 15,
        "no_path_search": 0,
        "partial_budget": 0,
    },
    "repair_outcome_breakdown": {
        "not_applicable_envelope_refused": 4,
        "repair_not_published": 15,
        "repair_published": 1,
    },
    "status_breakdown": {"completed": 1, "no_path": 15, "not_run": 4},
    "repair_work": {
        "inherited_proximity_cost_nm": 800000,
        "inherited_proximity_steps": 16,
        "inherited_search_expansions": 8276,
        "inherited_search_obstacle_checks": 14269,
        "published_repairs": 1,
        "repair_local_expanded_states": 3526,
        "repair_projection_obstacle_checks": 2070,
        "repair_validator_edge_checks": 303,
        "repair_validator_obstacle_checks": 3217,
    },
}
_COMMITMENT_DIFFERENTIAL_KEYS = frozenset(_DIFFERENTIAL_KEYS)
_COMMITMENT_DIFFERENTIAL_EXPECTED: dict[str, Any] = {
    "boards_completed_delta": 1,
    "negotiated_nets_completed_delta": 2,
    "total_wire_length_nm_delta": 43750000,
    "total_overflow_units_delta": 0,
    "total_physical_checks_delta": 7432,
    "positive_completion_delta": True,
    "verdict": "positive_completion_delta",
}


_COMMITMENT_ARM_PIN_ERROR = "the B-141 commitment arm pin is malformed"
_COMMITMENT_COVERAGE_ERROR = "the B-141 commitment does not pin every claimed arm aggregate"


def _assert_commitment_covers_claims() -> None:
    """Fail the run when a claimed arm aggregate is not covered by the commitment's pins.

    Enumerated from the report schema, not from memory: the configuration keys and the pinned keys
    must partition the arm schema exactly, and both hard-coded arm pins must carry the whole pinned
    key set.  If an aggregate is claimed, it is signed.
    """

    if (
        _ARM_CONFIGURATION_KEYS & _COMMITMENT_ARM_KEYS
        or (_ARM_CONFIGURATION_KEYS | _COMMITMENT_ARM_KEYS) != _AGGREGATE_KEYS
        or set(_COMMITMENT_CONTROL_EXPECTED) != _COMMITMENT_ARM_KEYS
        or set(_COMMITMENT_TREATMENT_EXPECTED) != _COMMITMENT_ARM_KEYS
        or not _AGGREGATE_TOTAL_KEYS.issubset(_COMMITMENT_ARM_KEYS)
        or not set(_ARM_BREAKDOWN_PARTITIONS).issubset(_COMMITMENT_ARM_KEYS)
        or "repair_work" not in _COMMITMENT_ARM_KEYS
    ):
        raise NegotiatedDifferentialError(_COMMITMENT_COVERAGE_ERROR)


def _validate_commitment_arm(value: Any, expected: dict[str, Any]) -> None:
    _assert_commitment_covers_claims()
    if not isinstance(value, dict) or set(value) != _COMMITMENT_ARM_KEYS:
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)
    for key in _AGGREGATE_TOTAL_KEYS:
        _require_nonnegative_int(value[key], _COMMITMENT_ARM_PIN_ERROR)
    for key in (*sorted(_ARM_BREAKDOWN_PARTITIONS), "repair_work"):
        _validate_nonnegative_integer_tree(value[key], _COMMITMENT_ARM_PIN_ERROR)
    if value != expected:
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)
    # The companion carries breakdowns, so the companion is reconciled too: a commitment whose
    # buckets contradict its own outcome taxonomy is refused before it can authenticate anything.
    outcomes = value["outcome_breakdown"]
    _reconcile_partitions(outcomes, value)
    if (
        value["repair_work"]["published_repairs"]
        != value["repair_outcome_breakdown"]["repair_published"]
    ):
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)
    if value["boards_completed"] != (
        outcomes["completed_without_repair"] + outcomes["completed_with_repair"]
    ):
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)


def _measurement_arm_pin(aggregate: Any) -> dict[str, Any]:
    """Project the claimed aggregates of one published arm, exactly as the commitment pins them."""

    if not isinstance(aggregate, dict):
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)
    _assert_commitment_covers_claims()
    if not _COMMITMENT_ARM_KEYS.issubset(aggregate):
        raise NegotiatedDifferentialError(_COMMITMENT_ARM_PIN_ERROR)
    return {key: aggregate[key] for key in _COMMITMENT_ARM_KEYS}


def _validate_commitment_differential(value: Any) -> None:
    _validate_differential_shape(value)
    if value != _COMMITMENT_DIFFERENTIAL_EXPECTED:
        raise NegotiatedDifferentialError("the B-141 commitment differential pin is malformed")


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
    _validate_commitment_differential(metrics.get("differential"))
    if _digest(metrics) != B141_METRICS_SHA256:
        raise NegotiatedDifferentialError("the B-141 metrics digest pin is malformed")


def validate_commitment(document: dict[str, Any]) -> None:
    """Validate the closed, self-digest companion commitment without reading live authorities."""

    if not isinstance(document, dict) or set(document) != _COMMITMENT_KEYS:
        raise NegotiatedDifferentialError("the B-141 commitment keys are malformed")
    if document.get("schema") != COMMITMENT_SCHEMA:
        raise NegotiatedDifferentialError("the B-141 commitment schema is malformed")
    if document.get("artifact_path") != DEFAULT_OUTPUT.relative_to(ROOT).as_posix():
        raise NegotiatedDifferentialError("the B-141 commitment artifact path is malformed")
    for key in (
        "artifact_sha256",
        "artifact_run_id",
        "runner_sha256",
        "configuration_sha256",
        "metrics_sha256",
    ):
        value = document.get(key)
        if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
            raise NegotiatedDifferentialError("the B-141 commitment digest pin is malformed")
    if document["metrics_sha256"] != B141_METRICS_SHA256:
        raise NegotiatedDifferentialError("the B-141 commitment metrics digest pin is malformed")
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
    _validate_commitment_differential(document.get("differential"))
    recorded = document.get("run_id")
    if not isinstance(recorded, str) or _SHA256.fullmatch(recorded) is None:
        raise NegotiatedDifferentialError("the B-141 commitment run ID is malformed")
    body = {key: value for key, value in document.items() if key != "run_id"}
    if recorded != _digest(body):
        raise NegotiatedDifferentialError("the B-141 commitment fails its own self-digest")


def _build_commitment_from_bytes(
    document: dict[str, Any], artifact_path: Path, artifact_bytes: bytes
) -> dict[str, Any]:
    if len(artifact_bytes) > MAX_JSON_ARTIFACT_BYTES:
        raise NegotiatedDifferentialError("the B-141 artifact exceeds 64 KiB")
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
    metrics_digest = _digest(document["metrics"])
    commitment: dict[str, Any] = {
        "schema": COMMITMENT_SCHEMA,
        "artifact_path": DEFAULT_OUTPUT.relative_to(ROOT).as_posix(),
        "artifact_sha256": "sha256:" + hashlib.sha256(artifact_bytes).hexdigest(),
        "artifact_run_id": document["run_id"],
        "source_commit": document["source_commit"],
        "runner_sha256": configuration["runner_sha256"],
        "configuration_sha256": configuration["configuration_sha256"],
        "metrics_sha256": metrics_digest,
        "repetitions": BENCHMARK_REPETITIONS,
        "population": dict(_B141_POPULATION_EXPECTED),
        "control": dict(_COMMITMENT_CONTROL_EXPECTED),
        "treatment": dict(_COMMITMENT_TREATMENT_EXPECTED),
        "differential": dict(_COMMITMENT_DIFFERENTIAL_EXPECTED),
    }
    commitment["run_id"] = _digest(commitment)
    validate_commitment(commitment)
    return commitment


def build_commitment(
    document: dict[str, Any], artifact_path: Path = DEFAULT_OUTPUT
) -> dict[str, Any]:
    """Build the exact-result commitment after the report has been written."""

    candidate = Path(artifact_path).expanduser()
    artifact_bytes = _read_bounded_bytes(candidate, label="B-141")
    return _build_commitment_from_bytes(document, candidate, artifact_bytes)


def load_commitment(path: Path | None = None) -> dict[str, Any]:
    """Load and validate the closed companion commitment."""

    candidate = COMMITMENT_PATH if path is None else Path(path)
    document = _load_object(candidate, label="B-141 commitment")
    validate_commitment(document)
    return document


def _validate_population_binding_shape(population_binding: Any) -> None:
    population_binding = _require_exact_dict(
        population_binding, _POPULATION_BINDING_KEYS, "the B-141 population binding is malformed"
    )
    if (
        type(population_binding["benchmark"]) is not str
        or population_binding["benchmark"] != "B-140"
        or type(population_binding["artifact"]) is not str
        or population_binding["artifact"] != B140_ARTIFACT.relative_to(ROOT).as_posix()
        or type(population_binding["artifact_run_id"]) is not str
        or population_binding["artifact_run_id"] != B140_RUN_ID
        or type(population_binding["configuration"]) is not str
        or population_binding["configuration"] != "b088-routable"
        or type(population_binding["boards_offered"]) is not int
        or population_binding["boards_offered"] != 20
        or type(population_binding["nets_submitted"]) is not int
        or population_binding["nets_submitted"] != 70
        or type(population_binding["corpus_manifest_count"]) is not int
        or population_binding["corpus_manifest_count"] != CORPUS_COMMITTED_COUNT
        or type(population_binding["corpus_manifest_sha256"]) is not str
        or _SHA256.fullmatch(population_binding["corpus_manifest_sha256"]) is None
    ):
        raise NegotiatedDifferentialError("the B-141 population binding is malformed")
    partition = population_binding.get("admission_partition")
    partition = _validate_nonnegative_integer_object(
        partition, _ADMISSION_PARTITION_KEYS, "the B-141 population binding is malformed"
    )
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

    _validate_source_commit_runner_binding(document)
    _validate_evidence_date_binding(document)
    configuration = document.get("configuration")
    if not isinstance(configuration, dict) or configuration != _configuration():
        raise NegotiatedDifferentialError("the B-141 source/configuration binding drifted")
    load_b140_artifact()
    reference = load_reference_artifact()
    authority = _reference_authority(reference)
    _manifest, samples, corpus_manifest_sha256 = _load_exact_corpus(corpus, authority)
    expected_population_binding = {
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
        or commitment["artifact_sha256"] != _bounded_file_digest(candidate, label="B-141")
        or commitment["artifact_run_id"] != document.get("run_id")
        or commitment["source_commit"] != document.get("source_commit")
        or commitment["runner_sha256"] != configuration.get("runner_sha256")
        or commitment["configuration_sha256"] != configuration.get("configuration_sha256")
        or commitment["metrics_sha256"] != _digest(document.get("metrics"))
        or commitment["repetitions"] != BENCHMARK_REPETITIONS
        or commitment["population"] != _B141_POPULATION_EXPECTED
    ):
        raise NegotiatedDifferentialError("the B-141 commitment binding drifted")
    _validate_exact_measurement_pins(document)
    if _measurement_arm_pin(document["metrics"]["control"]) != commitment["control"]:
        raise NegotiatedDifferentialError("the B-141 control commitment pin drifted")
    if _measurement_arm_pin(document["metrics"]["treatment"]) != commitment["treatment"]:
        raise NegotiatedDifferentialError("the B-141 treatment commitment pin drifted")
    if document["metrics"]["differential"] != commitment["differential"]:
        raise NegotiatedDifferentialError("the B-141 differential commitment pin drifted")


def validate_report(
    document: dict[str, Any],
    *,
    corpus: Path = b140.CORPUS,
    verify_live_bindings: bool = False,
    require_semantics: bool = True,
) -> None:
    """Validate generic report structure/semantics; live authorities are opt-in."""

    document = _validate_report_shape(document)
    recorded = document["run_id"]
    body = {key: value for key, value in document.items() if key != "run_id"}
    if recorded != _digest(body):
        raise NegotiatedDifferentialError("the B-141 report fails its own self-digest")
    configuration = document["configuration"]
    _validate_configuration_shape(configuration)
    config_digest = configuration["configuration_sha256"]
    without_digest = {
        key: value for key, value in configuration.items() if key != "configuration_sha256"
    }
    if config_digest != _digest(without_digest):
        raise NegotiatedDifferentialError("the B-141 configuration digest is malformed")
    if _nested_keys(document).intersection(FORBIDDEN_PUBLIC_KEYS):
        raise NegotiatedDifferentialError(
            "the B-141 public report contains private identity fields"
        )
    metrics = document["metrics"]
    if require_semantics:
        population = metrics["population"]
        bounds = _upper_bounds()
        control = _validate_aggregate(
            metrics["control"], treatment=False, population=population, bounds=bounds
        )
        treatment = _validate_aggregate(
            metrics["treatment"], treatment=True, population=population, bounds=bounds
        )
        _validate_differential(metrics, control, treatment)
        baseline = metrics["reference_baseline"]
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
    allowed_dirty: set[str] = set()
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
