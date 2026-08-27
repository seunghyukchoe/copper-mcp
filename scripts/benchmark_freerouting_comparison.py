#!/usr/bin/env python3
"""Reproducibly compare CopperMCP and FreeRouting at their file/process boundaries.

This program deliberately contains no FreeRouting code.  It launches a released JAR as a
separate process using its documented Specctra DSN/SES CLI.  A KiCad board produced by
importing that SES is supplied back to the harness for the identical KiCad-CLI DRC gate used
for a CopperMCP-produced disposable board.  A missing tool is evidence, not a pass.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import re
import selectors
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

try:
    import resource
except ImportError:  # pragma: no cover - Windows has no POSIX resource module.
    resource = None  # type: ignore[assignment]

from copper_mcp.benchmarks.drc_comparability import (
    LITERAL_KEY,
    comparability_of,
    require_qualified,
)
from copper_mcp.kicad_cli import (
    _BOUNDED_EXEC,
    KiCadCliError,
    _drc_object_pairs,
    _finite_json_float,
    _parse_drc_report,
    _preflight_drc_json,
    _reject_json_constant,
    _validate_drc_json_tree,
)

ROOT = Path(__file__).resolve().parents[1]
KICAD_SPECCTRA_TRANSACTION = ROOT / "scripts" / "kicad_specctra_transaction.py"
SCHEMA = "copper-mcp/benchmark/freerouting-comparison/v1"
FREEROUTING_LICENSE = "GPL-3.0-only"
FREEROUTING_RELEASE_SCHEMA = "copper-mcp/freerouting-release-provenance/v1"
_FREEROUTING_RELEASE_URL = "https://github.com/freerouting/freerouting/releases/download/"
REDACTED = "[redacted]"
_SECRET = re.compile(r"(?i)(bearer\s+|token=|password=|api[_-]?key=)[^\s]+")
_PATH = re.compile(r"(?<![A-Za-z0-9])(?:/[A-Za-z0-9._~+@%=-]+){2,}|[A-Za-z]:\\[^\s]+")
_VERSION = re.compile(r"\b\d+\.\d+(?:\.\d+)?(?:[-+][A-Za-z0-9._-]+)?\b")
_VIA = re.compile(r"\(via\b")
_SEGMENT = re.compile(
    r"\(segment\s+\(start\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)"
    r"\s+\(end\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)",
)
MAX_PROCESS_OUTPUT_BYTES = 8 * 1024
MAX_BOARD_BYTES = 32 * 1024 * 1024
MAX_DSN_BYTES = 64 * 1024 * 1024
MAX_PROVENANCE_BYTES = 128 * 1024
MAX_DRC_REPORT_BYTES = 32 * 1024 * 1024
MAX_JAR_BYTES = 512 * 1024 * 1024
MAX_EXECUTABLE_BYTES = 128 * 1024 * 1024
MAX_BOARD_ITEMS = 100_000
MAX_TRANSACTION_FILE_BYTES = MAX_DSN_BYTES
MIN_PRIVATE_WORKSPACE_QUOTA_BYTES = MAX_BOARD_BYTES * 3 + MAX_DSN_BYTES * 2 + MAX_DRC_REPORT_BYTES
FREEROUTING_RECEIPT_SCHEMA = "copper-mcp/freerouting-ses-import-receipt/v1"
COPPER_RECEIPT_SCHEMA = "copper-mcp/candidate-runner-receipt/v1"
FREEROUTING_TRANSACTION_SCHEMA = "copper-mcp/freerouting-kicad-transaction/v1"
_RECEIPT_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")
_RELEASE_TAG = re.compile(r"^v(\d+\.\d+\.\d+)$")
_GUI_DRC_HEADER = re.compile(r"^\*\* Drc report for (?P<board>[^\n]+) \*\*$", re.MULTILINE)
_GUI_DRC_CREATED = re.compile(r"^\*\* Created on \d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2} \*\*$")
_GUI_DRC_COUNT = re.compile(
    r"^\*\* Found (?P<count>\d+) (?P<kind>DRC violations|unconnected pads|Footprint errors) \*\*$"
)
_GUI_DRC_UNCONNECTED_LOCATION = re.compile(
    r"^    @\(-?\d+\.\d{4} mm, -?\d+\.\d{4} mm\): Pad [1-9]\d* "
    r"\[[A-Za-z0-9_.-]+\] of [A-Za-z0-9_.-]+ on [A-Za-z0-9_.-]+$"
)
_GUI_DRC_IGNORED_CHECKS = (
    "    - Footprint has no courtyard defined",
    "    - Track endpoint not centered on via",
    "    - Tuning profile track geometries",
    "    - Footprint doesn't match symbol's footprint filters",
    "    - Footprint component type doesn't match footprint pads",
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    elapsed_ns: int
    returncode: int | None
    status: str
    stdout: str
    stderr: str


@dataclass(frozen=True, slots=True)
class PrivateWorkspaceCapability:
    """An internally supplied aggregate-quota workspace, never a caller path.

    A platform-specific provider must create this directory and enforce ``quota_bytes`` for its
    whole tree.  The harness verifies the local filesystem facts it can observe before every
    transaction, but cannot turn an arbitrary directory into a quota boundary by inspection.
    """

    root: Path
    quota_bytes: int


def private_workspace_capability() -> PrivateWorkspaceCapability | None:
    """Return a provider-created aggregate-quota root when a reviewed provider exists.

    No provider is currently enabled.  Keeping this narrow seam explicit prevents a CLI caller
    from supplying a convenient but unbounded workspace path as if it were containment.
    """

    return None


def read_bounded_bytes(path: Path, maximum: int) -> bytes:
    """Read an untrusted regular file only after enforcing a byte ceiling."""

    if maximum <= 0 or not path.is_file():
        raise ValueError("required file is unavailable")
    if path.stat().st_size > maximum:
        raise ValueError("input exceeds its configured byte limit")
    with path.open("rb") as source:
        payload = source.read(maximum + 1)
    if len(payload) > maximum:
        raise ValueError("input exceeds its configured byte limit")
    return payload


def sha256_file(path: Path, maximum: int) -> str:
    """Hash an untrusted file only after enforcing its relevant byte ceiling."""

    digest = hashlib.sha256()
    digest.update(read_bounded_bytes(path, maximum))
    return "sha256:" + digest.hexdigest()


def redact(value: str) -> str:
    """Remove secrets and private paths before retaining bounded diagnostics."""

    value = _SECRET.sub(lambda match: match.group(1) + REDACTED, value)
    return _PATH.sub(REDACTED, value)[:MAX_PROCESS_OUTPUT_BYTES]


def _output_text(value: str | bytes | None) -> str:
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return value or ""


def freerouting_argv(java: Path, jar: Path, dsn: Path, ses: Path) -> tuple[str, ...]:
    """Return a headless documented FreeRouting v2 DSN-to-SES command, without a shell.

    The official JAR initializes Swing before it sees the DSN arguments.  The JVM headless flag
    prevents a physical-display requirement from silently turning a CLI benchmark into a macOS
    graphics-pipeline failure; it does not alter FreeRouting's DSN/SES protocol.
    """

    return (
        str(java),
        "-Djava.awt.headless=true",
        "-jar",
        str(jar),
        "-de",
        str(dsn),
        "-do",
        str(ses),
        "-l",
        "en",
    )


def kicad_specctra_argv(
    kicad_python: Path,
    command: str,
    source: Path,
    output: Path,
    *,
    ses: Path | None = None,
) -> tuple[str, ...]:
    """Return a fixed KiCad-bundled-Python transaction argv without a shell.

    KiCad documents DSN export and SES import as PCB-editor operations.  Its bundled ``pcbnew``
    binding exposes the same two operations; the helper receives only private workspace paths
    created by this harness.  No user-supplied command template participates in this boundary.
    """

    if command == "export-dsn" and ses is None:
        return (
            str(kicad_python),
            str(KICAD_SPECCTRA_TRANSACTION),
            command,
            "--source",
            str(source),
            "--output",
            str(output),
        )
    if command == "import-ses" and ses is not None:
        return (
            str(kicad_python),
            str(KICAD_SPECCTRA_TRANSACTION),
            command,
            "--source",
            str(source),
            "--ses",
            str(ses),
            "--output",
            str(output),
        )
    raise ValueError("unsupported KiCad Specctra transaction")


def copper_argv(
    template: tuple[str, ...], source: Path, output: Path, seed: int
) -> tuple[str, ...]:
    """Expand the intentionally small command-template contract for a CopperMCP runner."""

    values = {"source": str(source), "output": str(output), "seed": str(seed)}
    try:
        return tuple(part.format(**values) for part in template)
    except KeyError as error:
        raise ValueError(f"unsupported CopperMCP command placeholder: {error.args[0]}") from error


def _kill_process(process: subprocess.Popen[bytes]) -> None:
    """Kill the child's whole session, tolerating only a target that is provably gone.

    ``killpg(2)`` raises ``EPERM`` when the process group is not ours to signal.  Under
    load that happens benignly: the child exits, its PID -- and with it the session's
    PGID -- is recycled onto a foreign process, and the signal we aimed at our own group
    lands on someone else's.  The bound this kill enforces is already satisfied by that
    exit, so there is nothing to retry.  ``EPERM`` on a *live, owned* child is the
    opposite: the kill did not happen and swallowing it would drop the bound silently.

    ``poll()`` separates the two, and is the only admissible proof: it reaps without
    blocking and returns ``None`` for as long as the child is still running.  So the
    handler reaps first and only then decides that ``EPERM`` was benign.
    """

    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
            return
        except PermissionError:
            if process.poll() is None:
                raise
            return
    process.kill()


def minimal_environment(workspace: Path) -> dict[str, str]:
    """Return the only inherited environment visible to a benchmark child process."""

    return {
        "HOME": str(workspace),
        "LANG": "C",
        "LC_ALL": "C",
        "PATH": os.defpath,
        "TMPDIR": str(workspace),
    }


def _private_directory(path: Path, *, parent: Path | None = None) -> Path | None:
    """Accept only a canonical, owned, non-symlink, owner-private directory."""

    try:
        raw = path.absolute()
        metadata = raw.lstat()
        canonical = raw.resolve(strict=True)
        resolved_metadata = canonical.lstat()
        if (
            raw != canonical
            or not canonical.is_dir()
            or metadata.st_ino != resolved_metadata.st_ino
        ):
            return None
        if metadata.st_uid != os.getuid() or metadata.st_mode & 0o077:
            return None
        if parent is not None:
            canonical_parent = parent.resolve(strict=True)
            if canonical.parent != canonical_parent:
                return None
    except (OSError, RuntimeError):
        return None
    return canonical


def verified_private_workspace_capability() -> tuple[
    PrivateWorkspaceCapability | None, dict[str, str]
]:
    """Verify the exact root returned by the reviewed provider, or fail closed.

    Filesystem metadata cannot prove an aggregate quota.  ``private_workspace_capability`` is
    therefore the sole future platform-provider seam: it must create the root and vouch for the
    quota, while this function prevents a symlink, shared, foreign-owned, or undersized directory
    from reaching any child process.
    """

    capability = private_workspace_capability()
    if not isinstance(capability, PrivateWorkspaceCapability):
        system = platform.system()
        if system == "Darwin":
            reason = (
                "macOS legacy sandbox cannot yet provide a quota-backed KiCad workspace with "
                "a verified runtime-read allowlist"
            )
        elif system == "Linux":
            reason = (
                "no verified private tmpfs, mount-namespace, or cgroup quota provider is configured"
            )
        else:
            reason = f"no verified aggregate private-workspace quota provider exists for {system}"
        return None, {"status": "unavailable", "reason": reason}
    if (
        isinstance(capability.quota_bytes, bool)
        or capability.quota_bytes < MIN_PRIVATE_WORKSPACE_QUOTA_BYTES
    ):
        return None, {"status": "unavailable", "reason": "provider workspace quota is insufficient"}
    root = _private_directory(capability.root)
    if root is None:
        return None, {"status": "unavailable", "reason": "provider workspace root is invalid"}
    return (
        PrivateWorkspaceCapability(root=root, quota_bytes=capability.quota_bytes),
        {"status": "available", "quota_bytes": str(capability.quota_bytes)},
    )


def aggregate_workspace_containment() -> dict[str, str]:
    """Describe the current provider state without accepting a caller workspace."""

    _, containment = verified_private_workspace_capability()
    return containment


def verified_workspace_capability_value(
    capability: PrivateWorkspaceCapability,
) -> tuple[PrivateWorkspaceCapability | None, dict[str, str]]:
    """Revalidate an already-acquired internal capability before a launch boundary."""

    if (
        isinstance(capability.quota_bytes, bool)
        or capability.quota_bytes < MIN_PRIVATE_WORKSPACE_QUOTA_BYTES
    ):
        return None, {"status": "unavailable", "reason": "provider workspace quota is insufficient"}
    root = _private_directory(capability.root)
    if root is None:
        return None, {"status": "unavailable", "reason": "provider workspace root is invalid"}
    return (
        PrivateWorkspaceCapability(root=root, quota_bytes=capability.quota_bytes),
        {"status": "available", "quota_bytes": str(capability.quota_bytes)},
    )


@contextmanager
def private_transaction_workspace(capability: PrivateWorkspaceCapability) -> Iterator[Path]:
    """Create one owner-private child inside a verified provider root."""

    verified, containment = verified_workspace_capability_value(capability)
    if verified is None or containment["status"] != "available":
        raise ValueError("provider workspace root is invalid")
    with tempfile.TemporaryDirectory(
        prefix="copper-mcp-freerouting-", dir=verified.root
    ) as directory:
        workspace = _private_directory(Path(directory), parent=verified.root)
        if workspace is None:
            raise ValueError("provider transaction workspace is invalid")
        yield workspace


def _file_limit_preexec(limit_bytes: int) -> None:
    """Set the POSIX per-file write ceiling in the child before it executes."""

    assert resource is not None
    resource.setrlimit(resource.RLIMIT_FSIZE, (limit_bytes, limit_bytes))


def run_process(
    argv: tuple[str, ...],
    timeout_seconds: int,
    cwd: Path,
    *,
    file_limit_bytes: int = MAX_TRANSACTION_FILE_BYTES,
) -> ProcessResult:
    """Run one process with bounded streaming capture and group termination on failure."""

    if not 1 <= file_limit_bytes <= MAX_TRANSACTION_FILE_BYTES:
        raise ValueError("external process file limit is outside the supported range")
    started = time.perf_counter_ns()
    preexec = (
        (lambda: _file_limit_preexec(file_limit_bytes))
        if resource is not None and os.name == "posix"
        else None
    )
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is explicit, shell is never used
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            env=minimal_environment(cwd),
            preexec_fn=preexec,
        )
    except OSError:
        return ProcessResult(
            argv=argv,
            elapsed_ns=time.perf_counter_ns() - started,
            returncode=None,
            status="unavailable",
            stdout="",
            stderr="",
        )
    stdout_stream = process.stdout
    stderr_stream = process.stderr
    assert stdout_stream is not None and stderr_stream is not None
    streams: tuple[IO[Any], IO[Any]] = (stdout_stream, stderr_stream)
    captured: dict[IO[Any], bytearray] = {stream: bytearray() for stream in streams}
    status = "ok"
    deadline = time.monotonic() + timeout_seconds
    with selectors.DefaultSelector() as selector:
        for stream in captured:
            selector.register(stream, selectors.EVENT_READ)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                status = "timeout"
                _kill_process(process)
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = cast(IO[Any], key.fileobj)
                chunk = os.read(stream.fileno(), 4096)
                if not chunk:
                    selector.unregister(stream)
                    continue
                target = captured[stream]
                if len(target) + len(chunk) > MAX_PROCESS_OUTPUT_BYTES:
                    status = "output_limit"
                    _kill_process(process)
                    break
                target.extend(chunk)
            if status != "ok":
                break
    try:
        returncode = process.wait(timeout=1)
    except subprocess.TimeoutExpired:
        _kill_process(process)
        returncode = process.wait(timeout=1)
        status = "timeout"
    finally:
        for stream in streams:
            stream.close()
    return ProcessResult(
        argv=argv,
        elapsed_ns=time.perf_counter_ns() - started,
        returncode=returncode,
        status=status if status != "ok" else ("ok" if returncode == 0 else "failed"),
        stdout=redact(_output_text(bytes(captured[stdout_stream]))),
        stderr=redact(_output_text(bytes(captured[stderr_stream]))),
    )


def version_probe(executable: Path, cwd: Path) -> dict[str, Any]:
    """Capture a local executable version as diagnostic evidence, never a requirement."""

    result = run_process((str(executable), "--version"), 10, cwd)
    match = _VERSION.search(result.stdout)
    return {"status": result.status, "version": match.group(0) if match else "unknown"}


def _tool_probes(
    *,
    java: Path | None,
    kicad_cli: Path | None,
    kicad_python: Path | None,
    harness_transaction: bool,
    cwd: Path,
    reasons: list[str],
) -> dict[str, Any]:
    """Run only eligible version probes inside the supplied child workspace."""

    probes: dict[str, Any] = {}
    if java is not None and java.is_file() and java.stat().st_size <= MAX_EXECUTABLE_BYTES:
        probes["java"] = version_probe(java, cwd)
        if probes["java"]["status"] != "ok":
            reasons.append("Java runtime did not execute --version")
    if (
        kicad_cli is not None
        and kicad_cli.is_file()
        and kicad_cli.stat().st_size <= MAX_EXECUTABLE_BYTES
    ):
        probes["kicad_cli"] = version_probe(kicad_cli, cwd)
        if probes["kicad_cli"]["status"] != "ok":
            reasons.append("KiCad CLI did not execute --version")
    if (
        harness_transaction
        and kicad_python is not None
        and kicad_python.is_file()
        and kicad_python.stat().st_size <= MAX_EXECUTABLE_BYTES
    ):
        probes["kicad_python"] = version_probe(kicad_python, cwd)
        if probes["kicad_python"]["status"] != "ok":
            reasons.append("KiCad bundled Python did not execute --version")
    return probes


def _parse_template(path: Path | None) -> tuple[str, ...] | None:
    if path is None:
        return None
    raw = _strict_json(read_bounded_bytes(path, MAX_PROVENANCE_BYTES), "command template")
    if (
        not isinstance(raw, list)
        or not raw
        or not all(isinstance(part, str) and part for part in raw)
    ):
        raise ValueError("CopperMCP command JSON must be a non-empty JSON array of strings")
    return tuple(raw)


def _provenance(path: Path | None) -> tuple[dict[str, Any] | None, list[str]]:
    if path is None:
        return None, ["fixture provenance JSON is required"]
    try:
        value = _strict_json(read_bounded_bytes(path, MAX_PROVENANCE_BYTES), "fixture provenance")
    except (OSError, ValueError):
        return None, ["fixture provenance cannot be read safely"]
    if not isinstance(value, dict):
        return None, ["fixture provenance must be a JSON object"]
    fields = ("license_spdx", "origin", "derivation_statement")
    if any(not isinstance(value.get(key), str) or not value[key] for key in fields):
        return None, ["fixture provenance has required malformed fields"]
    if any(len(value[key]) > 256 for key in fields):
        return None, ["fixture provenance field exceeds its length limit"]
    origin = value["origin"]
    if origin not in {"coppermcp-original", "independently-authored"}:
        return None, ["fixture provenance must identify an independently authored fixture"]
    return {"license_spdx": value["license_spdx"], "origin": origin}, []


def _fixture_provenance_object(path: Path | None) -> dict[str, Any] | None:
    """Read a fixture provenance object for an optional, non-authoritative claim."""

    if path is None:
        return None
    try:
        value = _strict_json(read_bounded_bytes(path, MAX_PROVENANCE_BYTES), "fixture provenance")
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def source_drc_expectation(path: Path | None) -> dict[str, int] | None:
    """Read the declared pre-route DRC baseline without treating it as DRC evidence."""

    provenance = _fixture_provenance_object(path)
    if provenance is None:
        return None
    value = provenance.get("source_drc_expectation")
    if not isinstance(value, dict) or set(value) != {
        "hard_violations",
        "intentional_unconnected_items",
    }:
        return None
    hard_violations = value.get("hard_violations")
    intentional_unconnected = value.get("intentional_unconnected_items")
    if (
        isinstance(hard_violations, bool)
        or not isinstance(hard_violations, int)
        or isinstance(intentional_unconnected, bool)
        or not isinstance(intentional_unconnected, int)
        or not 0 <= hard_violations <= MAX_BOARD_ITEMS
        or not 0 <= intentional_unconnected <= MAX_BOARD_ITEMS
    ):
        return None
    return {
        "hard_violations": hard_violations,
        "intentional_unconnected_items": intentional_unconnected,
    }


def dsn_source_export_binding(path: Path | None) -> dict[str, str]:
    """Expose a DSN-export statement without misrepresenting it as a verified causal binding."""

    provenance = _fixture_provenance_object(path)
    if provenance is None:
        return {"status": "unavailable"}
    value = provenance.get("dsn_source_export")
    if not isinstance(value, dict):
        return {"status": "unavailable"}
    if (
        value.get("workflow") != "kicad-ui-specctra-dsn-export"
        or value.get("status") != "self_attested_unverified"
        or not isinstance(value.get("statement"), str)
        or not 1 <= len(value["statement"]) <= 512
    ):
        return {"status": "invalid"}
    return {
        "status": "self_attested_unverified",
        "workflow": "kicad-ui-specctra-dsn-export",
    }


def freerouting_release_provenance(
    path: Path | None, jar: Path | None
) -> tuple[dict[str, str] | None, str]:
    """Bind one official GitHub-release JAR to a bounded public provenance record.

    This proves that the evaluated local bytes match the caller-recorded SHA-256 and names the
    official release URL.  It does not claim an upstream signature or substitute for a published
    checksum, neither of which FreeRouting currently supplies with the JAR asset.
    """

    if path is None:
        return None, "unavailable"
    try:
        value = _strict_json(read_bounded_bytes(path, MAX_PROVENANCE_BYTES), "release provenance")
    except (OSError, ValueError):
        return None, "invalid"
    if not isinstance(value, dict) or value.get("schema") != FREEROUTING_RELEASE_SCHEMA:
        return None, "invalid"
    keys = ("release_tag", "asset_name", "asset_sha256", "source_url", "license_spdx")
    if any(not isinstance(value.get(key), str) or not value[key] for key in keys):
        return None, "invalid"
    if any(len(value[key]) > 512 for key in keys):
        return None, "invalid"
    tag_match = _RELEASE_TAG.fullmatch(value["release_tag"])
    if tag_match is None or value["license_spdx"] != FREEROUTING_LICENSE:
        return None, "invalid"
    expected_asset = f"freerouting-{tag_match.group(1)}.jar"
    expected_url = _FREEROUTING_RELEASE_URL + f"{value['release_tag']}/{expected_asset}"
    if value["asset_name"] != expected_asset or value["source_url"] != expected_url:
        return None, "invalid"
    if not _RECEIPT_SHA256.fullmatch(value["asset_sha256"]):
        return None, "invalid"
    actual_hash = _hash_or_none(jar, MAX_JAR_BYTES)
    if actual_hash is None:
        return None, "unavailable"
    if actual_hash != value["asset_sha256"]:
        return None, "mismatch"
    return {key: value[key] for key in keys}, "verified"


def _strict_json(payload: bytes, label: str) -> Any:
    try:
        text = payload.decode("utf-8", errors="strict")
        _preflight_drc_json(text)
        value = json.loads(
            text,
            object_pairs_hook=_drc_object_pairs,
            parse_constant=_reject_json_constant,
            parse_float=_finite_json_float,
        )
        _validate_drc_json_tree(value)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError, ValueError) as error:
        raise ValueError(f"{label} is not bounded valid JSON") from error
    return value


def _receipt(path: Path | None, schema: str) -> tuple[dict[str, str] | None, str]:
    """Read an untrusted receipt as a small, path-free content binding."""

    if path is None:
        return None, "unavailable"
    try:
        value = _strict_json(read_bounded_bytes(path, MAX_PROVENANCE_BYTES), "receipt")
    except (OSError, ValueError):
        return None, "invalid"
    if not isinstance(value, dict) or value.get("schema") != schema:
        return None, "invalid"
    required: tuple[str, ...] = ("source_sha256", "result_board_sha256")
    if schema == FREEROUTING_RECEIPT_SCHEMA:
        required += ("ses_sha256", "workflow")
    else:
        required += ("runner_output_sha256", "workflow")
    if any(not isinstance(value.get(key), str) or len(value[key]) > 256 for key in required):
        return None, "invalid"
    if any(not _RECEIPT_SHA256.fullmatch(value[key]) for key in required if key.endswith("sha256")):
        return None, "invalid"
    expected_workflow = (
        "kicad-specctra-ses-import"
        if schema == FREEROUTING_RECEIPT_SCHEMA
        else "coppermcp-candidate-runner"
    )
    if value["workflow"] != expected_workflow:
        return None, "invalid"
    return {key: value[key] for key in required}, "ok"


def _validate_ses(path: Path) -> str | None:
    """Accept only a bounded, nonempty Specctra-session shaped output."""

    try:
        payload = read_bounded_bytes(path, MAX_DSN_BYTES)
        text = payload.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    stripped = text.lstrip()
    if not stripped.startswith("(session") or not stripped.rstrip().endswith(")"):
        return None
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _validate_dsn(path: Path) -> str | None:
    """Accept only a bounded, nonempty Specctra-design shaped KiCad export."""

    try:
        payload = read_bounded_bytes(path, MAX_DSN_BYTES)
        text = payload.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, ValueError):
        return None
    stripped = text.lstrip()
    if not stripped.startswith("(pcb") or not stripped.rstrip().endswith(")"):
        return None
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _private_copy(source: Path, destination: Path) -> str | None:
    """Copy source bytes only after the board-size guard and return their digest."""

    try:
        payload = read_bounded_bytes(source, MAX_BOARD_BYTES)
    except (OSError, ValueError):
        return None
    destination.write_bytes(payload)
    return "sha256:" + hashlib.sha256(payload).hexdigest()


def _harness_freerouting_transaction(
    *,
    source: Path,
    java: Path,
    jar: Path,
    kicad_python: Path,
    kicad_cli: Path | None,
    timeout_seconds: int,
    source_sha256: str | None,
    cwd: Path,
    workspace_capability: PrivateWorkspaceCapability | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Causally bind private KiCad export, FreeRouting, KiCad import, and result DRC.

    All intermediate bytes are confined to one temporary directory.  The resulting report
    retains only hashes, process summaries, aggregate metrics, and a status.  Every stage must
    succeed before the result is marked ``bound``; a caller-supplied board or receipt cannot
    upgrade this transaction.
    """

    capability, containment = (
        verified_workspace_capability_value(workspace_capability)
        if workspace_capability is not None
        else verified_private_workspace_capability()
    )
    if capability is None or containment["status"] != "available":
        return {
            "schema": FREEROUTING_TRANSACTION_SCHEMA,
            "status": "unavailable",
            "containment": containment,
        }, None
    if source_sha256 is None:
        return {"schema": FREEROUTING_TRANSACTION_SCHEMA, "status": "unavailable"}, None
    try:
        workspace_context = private_transaction_workspace(capability)
        with workspace_context as workspace:
            private_source = workspace / "source.kicad_pcb"
            private_hash = _private_copy(source, private_source)
            record: dict[str, Any] = {
                "schema": FREEROUTING_TRANSACTION_SCHEMA,
                "source_sha256": source_sha256,
                "source_copy_sha256": private_hash,
                "status": "failed",
            }
            if private_hash != source_sha256:
                return record, None

            dsn = workspace / "source.dsn"
            export = run_process(
                kicad_specctra_argv(kicad_python, "export-dsn", private_source, dsn),
                timeout_seconds,
                workspace,
                file_limit_bytes=MAX_DSN_BYTES,
            )
            dsn_sha256 = _validate_dsn(dsn) if export.status == "ok" else None
            record["kicad_export"] = {
                **process_record(export, "kicad_specctra_dsn_export"),
                "dsn_sha256": dsn_sha256,
                "dsn_status": "valid" if dsn_sha256 else "missing_or_invalid",
            }
            export_source_after = _hash_or_none(private_source, MAX_BOARD_BYTES)
            record["kicad_export"]["source_copy_preserved"] = export_source_after == source_sha256
            if dsn_sha256 is None or export_source_after != source_sha256:
                return record, None

            ses = workspace / "freerouting.ses"
            router = run_process(
                freerouting_argv(java, jar, dsn, ses),
                timeout_seconds,
                workspace,
                file_limit_bytes=MAX_DSN_BYTES,
            )
            ses_sha256 = _validate_ses(ses) if router.status == "ok" else None
            record["freerouting_process"] = {
                **process_record(router, "freerouting_dsn_ses"),
                "ses_sha256": ses_sha256,
                "ses_status": "valid" if ses_sha256 else "missing_or_invalid",
            }
            if ses_sha256 is None:
                return record, None

            import_source = workspace / "import-source.kicad_pcb"
            import_source_hash = _private_copy(source, import_source)
            record["import_source_copy_sha256"] = import_source_hash
            if import_source_hash != source_sha256:
                return record, None
            imported = workspace / "freerouting-imported.kicad_pcb"
            imported_result = run_process(
                kicad_specctra_argv(kicad_python, "import-ses", import_source, imported, ses=ses),
                timeout_seconds,
                workspace,
                file_limit_bytes=MAX_BOARD_BYTES,
            )
            imported_sha256 = _hash_or_none(imported, MAX_BOARD_BYTES)
            import_source_after = _hash_or_none(import_source, MAX_BOARD_BYTES)
            record["kicad_import"] = {
                **process_record(imported_result, "kicad_specctra_ses_import"),
                "result_board_sha256": imported_sha256,
                "result_status": "valid" if imported_sha256 else "missing_or_invalid",
                "source_copy_preserved": import_source_after == source_sha256,
            }
            if (
                imported_result.status != "ok"
                or imported_sha256 is None
                or import_source_after != source_sha256
            ):
                return record, None

            metrics = _result_for_board(
                "freerouting",
                imported,
                kicad_cli,
                timeout_seconds,
                workspace,
                router.elapsed_ns,
            )
            if metrics.get("drc", {}).get("status") != "ok":
                record["drc_status"] = metrics.get("drc", {}).get("status", "unavailable")
                return record, metrics
            record["status"] = "bound"
            return record, metrics
    except ValueError:
        return {
            "schema": FREEROUTING_TRANSACTION_SCHEMA,
            "status": "unavailable",
            "containment": {
                "status": "unavailable",
                "reason": "provider workspace root is invalid",
            },
        }, None


def _binding_status(
    receipt: dict[str, str] | None,
    *,
    source_sha256: str | None,
    result_board: Path | None,
    output_sha256: str | None,
    output_key: str,
) -> str:
    if receipt is None or source_sha256 is None or output_sha256 is None:
        return "unavailable"
    result_sha256 = _hash_or_none(result_board, MAX_BOARD_BYTES)
    if result_sha256 is None:
        return "unavailable"
    if (
        receipt["source_sha256"] != source_sha256
        or receipt["result_board_sha256"] != result_sha256
        or receipt[output_key] != output_sha256
    ):
        return "mismatch"
    return "bound"


def preflight(
    *,
    source: Path,
    dsn: Path | None,
    java: Path | None,
    jar: Path | None,
    kicad_cli: Path | None,
    provenance: Path | None,
    cwd: Path,
    release_provenance: Path | None = None,
    kicad_python: Path | None = None,
    harness_transaction: bool = False,
    containment: dict[str, str] | None = None,
    workspace_capability: PrivateWorkspaceCapability | None = None,
) -> dict[str, Any]:
    """Return all prerequisite failures in one truthful, serializable record."""

    reasons: list[str] = []
    requirements: tuple[tuple[str, Path | None, int], ...] = (
        ("source board", source, MAX_BOARD_BYTES),
        ("Java", java, MAX_EXECUTABLE_BYTES),
        ("FreeRouting JAR", jar, MAX_JAR_BYTES),
        ("KiCad CLI", kicad_cli, MAX_EXECUTABLE_BYTES),
    ) + (() if harness_transaction else (("DSN", dsn, MAX_DSN_BYTES),))
    if harness_transaction:
        requirements += (("KiCad bundled Python", kicad_python, MAX_EXECUTABLE_BYTES),)
    for label, path, maximum in requirements:
        if path is None or not path.is_file():
            reasons.append(f"{label} is unavailable")
        elif path.stat().st_size > maximum:
            reasons.append(f"{label} exceeds its byte limit")
    _, provenance_reasons = _provenance(provenance)
    reasons.extend(provenance_reasons)
    _, release_status = freerouting_release_provenance(release_provenance, jar)
    if release_status not in {"unavailable", "verified"}:
        reasons.append("FreeRouting release provenance is invalid or does not match its JAR")
    containment = containment if containment is not None else aggregate_workspace_containment()
    if harness_transaction and containment["status"] != "available":
        reasons.append("aggregate private-workspace quota is unavailable")
    # A refused harness transaction must not probe Java or KiCad: version probes are subprocesses
    # too, and a preflight boundary is meaningful only before every executable launch seam.
    if harness_transaction and containment["status"] != "available":
        return {"available": False, "reasons": reasons, "probes": {}}
    if harness_transaction:
        if workspace_capability is None:
            reasons.append("aggregate private-workspace quota is unavailable")
            return {"available": False, "reasons": reasons, "probes": {}}
        probe_capability, probe_containment = verified_workspace_capability_value(
            workspace_capability
        )
        if probe_capability is None or probe_containment["status"] != "available":
            reasons.append("aggregate private-workspace quota is unavailable")
            return {"available": False, "reasons": reasons, "probes": {}}
        try:
            with private_transaction_workspace(probe_capability) as probe_workspace:
                probes = _tool_probes(
                    java=java,
                    kicad_cli=kicad_cli,
                    kicad_python=kicad_python,
                    harness_transaction=True,
                    cwd=probe_workspace,
                    reasons=reasons,
                )
        except ValueError:
            reasons.append("aggregate private-workspace quota is unavailable")
            return {"available": False, "reasons": reasons, "probes": {}}
    else:
        probes = _tool_probes(
            java=java,
            kicad_cli=kicad_cli,
            kicad_python=kicad_python,
            harness_transaction=False,
            cwd=cwd,
            reasons=reasons,
        )
    return {"available": not reasons, "reasons": reasons, "probes": probes}


def _hash_or_none(path: Path | None, maximum: int) -> str | None:
    if path is None:
        return None
    try:
        return sha256_file(path, maximum)
    except (OSError, ValueError):
        return None


def board_metrics(board: Path) -> dict[str, int | str]:
    """Count board-text vias and segment length only after KiCad DRC remains authoritative."""

    content = read_bounded_bytes(board, MAX_BOARD_BYTES).decode("utf-8", errors="strict")
    segments = _SEGMENT.findall(content)
    vias = _VIA.findall(content)
    if len(segments) > MAX_BOARD_ITEMS or len(vias) > MAX_BOARD_ITEMS:
        raise ValueError("board routing item count exceeds its limit")
    length_mm = sum(
        math.hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
        for start_x, start_y, end_x, end_y in segments
    )
    return {
        "board_sha256": sha256_file(board, MAX_BOARD_BYTES),
        "length_nm": round(length_mm * 1_000_000),
        "vias": len(vias),
    }


def drc_metrics(
    kicad_cli: Path,
    board: Path,
    timeout_seconds: int,
    cwd: Path,
    *,
    role: str = "kicad_drc",
) -> dict[str, Any]:
    """Run the same authoritative KiCad DRC command for either result board."""

    with tempfile.TemporaryDirectory(prefix="copper-mcp-freerouting-drc-", dir=cwd) as directory:
        report = Path(directory) / "drc.json"
        command = (
            sys.executable,
            "-I",
            str(_BOUNDED_EXEC),
            str(MAX_DRC_REPORT_BYTES),
            str(kicad_cli),
            "pcb",
            "drc",
            "--format",
            "json",
            "--units",
            "mm",
            "--severity-all",
            "--exit-code-violations",
            "--output",
            str(report),
            str(board),
        )
        result = run_process(
            command,
            timeout_seconds,
            cwd,
            file_limit_bytes=MAX_DRC_REPORT_BYTES,
        )
        output: dict[str, Any] = {
            "process": process_record(result, role),
            "status": result.status,
        }
        if result.status != "ok" and result.returncode != 5:
            return output
        assert result.returncode is not None
        if not report.is_file():
            output["status"] = "failed"
            return output
        try:
            payload = read_bounded_bytes(report, MAX_DRC_REPORT_BYTES)
            summary = _parse_drc_report(
                payload,
                return_code=result.returncode,
                base_revision=sha256_file(board, MAX_BOARD_BYTES),
                drc_context_revision=sha256_file(board, MAX_BOARD_BYTES),
                # KiCad CLI v10 records only the input basename in a board-only DRC report.
                # Comparing that stable value still prevents substituting another board result.
                expected_source=board.name,
            )
        except (KiCadCliError, ValueError, OSError):
            output["status"] = "failed"
            return output
        output.update(
            {
                "board_sha256": sha256_file(board, MAX_BOARD_BYTES),
                # One `kicad-cli pcb drc` invocation per board, so the literal ADR-0109 requires
                # is `single_invocation` and it is derived from the observation list rather than
                # written as a constant: a later slice that runs the gate twice changes the list
                # and the literal follows it.  `hard_violations` and `unconnected` are
                # `DrcSummary.error_count` and `DrcSummary.unconnected_count` renamed on the way
                # out, so they carry exactly the run-to-run instability `B-107` measured.
                LITERAL_KEY: comparability_of(
                    [
                        {
                            "hard_violations": summary.error_count,
                            "unconnected": summary.unconnected_count,
                        }
                    ]
                ),
                "hard_violations": summary.error_count,
                "kicad_version": summary.kicad_version,
                "report_sha256": sha256_file(report, MAX_DRC_REPORT_BYTES),
                "status": "ok",
                "unconnected": summary.unconnected_count,
            }
        )
        return output


def source_drc_binding(
    source_sha256: str | None,
    source_drc: dict[str, Any],
    expectation: dict[str, int] | None,
) -> dict[str, int | str]:
    """Compare a source DRC observation with its declared exact fixture baseline.

    A KiCad GUI report names only a board basename, so it cannot causally bind its report digest
    to the source bytes.  A retained attestation containing both exact hashes would be needed to
    strengthen that GUI evidence beyond a self-attested observation.
    """

    if source_sha256 is None or expectation is None or source_drc.get("status") != "ok":
        return {"status": "unavailable"}
    actual_source = source_drc.get("board_sha256")
    hard_violations = source_drc.get("hard_violations")
    unconnected = source_drc.get("unconnected")
    if (
        not isinstance(actual_source, str)
        or isinstance(hard_violations, bool)
        or not isinstance(hard_violations, int)
        or isinstance(unconnected, bool)
        or not isinstance(unconnected, int)
    ):
        return {"status": "unavailable"}
    matches = (
        actual_source == source_sha256
        and hard_violations == expectation["hard_violations"]
        and unconnected == expectation["intentional_unconnected_items"]
    )
    output: dict[str, int | str] = {
        "status": "mismatch" if not matches else "bound",
        "expected_hard_violations": expectation["hard_violations"],
        "expected_intentional_unconnected_items": expectation["intentional_unconnected_items"],
    }
    if matches and source_drc.get("workflow") == "kicad-gui-drc-report":
        output["status"] = "self_attested_unverified"
        output["evidence_limit"] = "GUI report header identifies only a board basename"
    return output


def gui_drc_metrics(board: Path, report: Path | None) -> dict[str, Any]:
    """Parse a bounded KiCad-GUI DRC report for the named board.

    This is deliberately a separate evidence path from ``drc_metrics``: KiCad's GUI can emit a
    report on platforms where the local CLI cannot complete.  The report header only identifies
    a basename, so the returned board hash records the bytes evaluated by this invocation.  It
    does not imply DSN provenance or a causal routing/import transaction.  The parser accepts
    only the KiCad 10.0.5 report sequence recorded for this fixed fixture: an exact header,
    timestamp, report-includes line, three zero-or-one count sections, the documented single
    blank separators, the known ignored-check list, and one end marker.  Every other line,
    duplicate, reordered section, or unsupported nonzero count fails closed.
    """

    if report is None:
        return {"status": "unavailable", "reason": "source DRC report was not supplied"}
    try:
        payload = read_bounded_bytes(report, MAX_DRC_REPORT_BYTES)
        text = payload.decode("utf-8", errors="strict")
    except (OSError, UnicodeDecodeError, ValueError):
        return {"status": "failed"}
    observed_counts = _parse_gui_drc_report(text, board.name)
    if observed_counts is None:
        return {"status": "failed"}
    try:
        board_sha256 = sha256_file(board, MAX_BOARD_BYTES)
        report_sha256 = sha256_file(report, MAX_DRC_REPORT_BYTES)
    except (OSError, ValueError):
        return {"status": "failed"}
    return {
        "board_sha256": board_sha256,
        # A GUI report is one operator-run invocation, transcribed once.  Nothing here can make
        # it `repeated_agreement`: this runner reads a file it did not produce, so it cannot even
        # assert the precondition that two observations came from one commit over identical bytes.
        LITERAL_KEY: comparability_of([dict(observed_counts)]),
        "footprint_errors": observed_counts["Footprint errors"],
        "hard_violations": observed_counts["DRC violations"],
        "report_sha256": report_sha256,
        "status": "ok",
        "unconnected": observed_counts["unconnected pads"],
        "workflow": "kicad-gui-drc-report",
    }


def _parse_gui_drc_report(text: str, board_name: str) -> dict[str, int] | None:
    """Parse exactly the bounded KiCad GUI report grammar accepted by this benchmark."""

    if not text.endswith("\n"):
        return None
    lines = text.splitlines()
    if len(lines) < 18:
        return None
    header = _GUI_DRC_HEADER.fullmatch(lines[0])
    if (
        header is None
        or header.group("board") != board_name
        or _GUI_DRC_CREATED.fullmatch(lines[1]) is None
        or lines[2] != "** Report includes: Errors, Warnings **"
        or lines[3] != ""
    ):
        return None

    def count_at(index: int, expected_kind: str) -> int | None:
        match = _GUI_DRC_COUNT.fullmatch(lines[index])
        if match is None or match.group("kind") != expected_kind:
            return None
        return int(match.group("count"))

    hard_violations = count_at(4, "DRC violations")
    unconnected = count_at(6, "unconnected pads")
    if hard_violations != 0 or unconnected not in {0, 1} or lines[5] != "":
        return None
    footprint_index = 8
    if unconnected == 1:
        if (
            len(lines) < 22
            or lines[7] != "[unconnected_items]: Missing connection between items"
            or lines[8] != "    Local override; error"
            or _GUI_DRC_UNCONNECTED_LOCATION.fullmatch(lines[9]) is None
            or _GUI_DRC_UNCONNECTED_LOCATION.fullmatch(lines[10]) is None
            or lines[11] != ""
        ):
            return None
        footprint_index = 12
    elif lines[7] != "":
        return None
    footprint_errors = count_at(footprint_index, "Footprint errors")
    ignored_index = footprint_index + 2
    end_index = ignored_index + len(_GUI_DRC_IGNORED_CHECKS) + 2
    if (
        footprint_errors != 0
        or len(lines) != end_index + 1
        or lines[footprint_index + 1] != ""
        or lines[ignored_index] != "** Ignored checks **"
        or tuple(lines[ignored_index + 1 : ignored_index + 1 + len(_GUI_DRC_IGNORED_CHECKS)])
        != _GUI_DRC_IGNORED_CHECKS
        or lines[end_index - 1] != ""
        or lines[end_index] != "** End of Report **"
    ):
        return None
    return {
        "DRC violations": hard_violations,
        "Footprint errors": footprint_errors,
        "unconnected pads": unconnected,
    }


def gui_source_drc_metrics(source: Path, report: Path | None) -> dict[str, Any]:
    """Parse a bounded GUI DRC report for the pre-route source board."""

    return gui_drc_metrics(source, report)


def process_record(result: ProcessResult, role: str) -> dict[str, Any]:
    return {
        "role": role,
        "elapsed_ns": result.elapsed_ns,
        "returncode": result.returncode,
        "status": result.status,
    }


def metric_priority(result: dict[str, Any]) -> tuple[int, int, int, int, int, int]:
    """Order results: completion/connectivity, KiCad DRC, then vias, length, runtime."""

    raw_drc = result.get("drc")
    drc: dict[str, Any] = raw_drc if isinstance(raw_drc, dict) else {}
    completed = result.get("status") == "ok" and drc.get("status") == "ok"
    return (
        0 if completed and drc.get("unconnected", 1) == 0 else 1,
        int(drc.get("unconnected", 1_000_000)),
        int(drc.get("hard_violations", 1_000_000)),
        int(result.get("vias", 1_000_000)),
        int(result.get("length_nm", 1_000_000_000_000)),
        int(result.get("elapsed_ns", 1_000_000_000_000_000)),
    )


def _result_for_board(
    name: str,
    board: Path | None,
    kicad_cli: Path | None,
    timeout_seconds: int,
    cwd: Path,
    elapsed_ns: int = 0,
    gui_drc_report: Path | None = None,
) -> dict[str, Any]:
    if board is None or not board.is_file():
        return {"name": name, "status": "unavailable", "reason": "result board is unavailable"}
    try:
        metrics: dict[str, Any] = {
            "name": name,
            "elapsed_ns": elapsed_ns,
            "status": "ok",
            **board_metrics(board),
        }
    except (OSError, UnicodeDecodeError, ValueError):
        return {"name": name, "status": "failed", "reason": "result board exceeds safe limits"}
    metrics["drc"] = (
        gui_drc_metrics(board, gui_drc_report)
        if gui_drc_report is not None
        else (
            drc_metrics(kicad_cli, board, timeout_seconds, cwd)
            if kicad_cli
            else {"status": "unavailable"}
        )
    )
    return metrics


def private_source_drc_metrics(
    capability: PrivateWorkspaceCapability,
    kicad_cli: Path,
    source: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    """Run source DRC only on a private copy inside the provider-owned boundary."""

    try:
        with private_transaction_workspace(capability) as workspace:
            private_source = workspace / "source-drc.kicad_pcb"
            if _private_copy(source, private_source) is None:
                return {"status": "unavailable", "reason": "source board is unavailable"}
            return drc_metrics(
                kicad_cli, private_source, timeout_seconds, workspace, role="kicad_source_drc"
            )
    except ValueError:
        return {"status": "unavailable", "reason": "provider workspace root is invalid"}


def private_result_for_board(
    name: str,
    board: Path | None,
    capability: PrivateWorkspaceCapability,
    kicad_cli: Path | None,
    timeout_seconds: int,
    elapsed_ns: int,
    gui_drc_report: Path | None,
) -> dict[str, Any]:
    """Copy a caller result into the provider boundary before KiCad can inspect it."""

    if board is None or not board.is_file():
        return {"name": name, "status": "unavailable", "reason": "result board is unavailable"}
    # A supplied GUI report is parsed locally and launches no KiCad child. Preserve its expected
    # basename contract; the private-copy requirement applies to every live KiCad invocation.
    if gui_drc_report is not None:
        return _result_for_board(
            name, board, None, timeout_seconds, capability.root, elapsed_ns, gui_drc_report
        )
    try:
        original_sha256 = _hash_or_none(board, MAX_BOARD_BYTES)
        if original_sha256 is None:
            return {"name": name, "status": "failed", "reason": "result board exceeds safe limits"}
        with private_transaction_workspace(capability) as workspace:
            private_board = workspace / "result.kicad_pcb"
            copied_sha256 = _private_copy(board, private_board)
            # Recheck both sides immediately before DRC so an external rewrite cannot make KiCad
            # resolve project/rule context from the caller's directory after the copy boundary.
            if (
                copied_sha256 != original_sha256
                or _hash_or_none(private_board, MAX_BOARD_BYTES) != original_sha256
                or _hash_or_none(board, MAX_BOARD_BYTES) != original_sha256
            ):
                return {"name": name, "status": "unavailable", "reason": "result board changed"}
            return _result_for_board(
                name,
                private_board,
                kicad_cli,
                timeout_seconds,
                workspace,
                elapsed_ns,
                gui_drc_report,
            )
    except ValueError:
        return {
            "name": name,
            "status": "unavailable",
            "reason": "provider workspace root is invalid",
        }


def build_report(
    *,
    source: Path,
    dsn: Path | None,
    java: Path | None,
    jar: Path | None,
    kicad_cli: Path | None,
    provenance: Path | None,
    copper_board: Path | None,
    freerouting_board: Path | None,
    copper_receipt: Path | None,
    freerouting_receipt: Path | None,
    copper_command: tuple[str, ...] | None,
    seed: int,
    timeout_seconds: int,
    release_provenance: Path | None = None,
    kicad_python: Path | None = None,
    source_drc_report: Path | None = None,
    copper_drc_report: Path | None = None,
    freerouting_drc_report: Path | None = None,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Run available bounded stages and return content-addressed evidence; never mutate source."""

    workspace_capability, containment = verified_private_workspace_capability()
    gate = preflight(
        source=source,
        dsn=dsn,
        java=java,
        jar=jar,
        kicad_cli=kicad_cli,
        provenance=provenance,
        cwd=ROOT,
        release_provenance=release_provenance,
        kicad_python=kicad_python,
        harness_transaction=kicad_python is not None,
        containment=containment,
        workspace_capability=workspace_capability,
    )
    source_before = _hash_or_none(source, MAX_BOARD_BYTES)
    fixture, _ = _provenance(provenance)
    baseline_expectation = source_drc_expectation(provenance)
    dsn_export = dsn_source_export_binding(provenance)
    release, release_status = freerouting_release_provenance(release_provenance, jar)
    free_receipt, free_receipt_status = _receipt(freerouting_receipt, FREEROUTING_RECEIPT_SCHEMA)
    copper_run_receipt, copper_receipt_status = _receipt(copper_receipt, COPPER_RECEIPT_SCHEMA)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "recorded_at_utc": (timestamp or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "fixture": {
            "source_sha256": source_before,
            "dsn_sha256": _hash_or_none(dsn, MAX_DSN_BYTES),
            "provenance_sha256": _hash_or_none(provenance, MAX_PROVENANCE_BYTES),
            "provenance": fixture,
            "dsn_source_export_binding": dsn_export,
        },
        "toolchain": {
            "freerouting_license": FREEROUTING_LICENSE,
            "freerouting_jar_sha256": _hash_or_none(jar, MAX_JAR_BYTES),
            "freerouting_release_provenance": release,
            "freerouting_release_provenance_status": release_status,
            "java_sha256": _hash_or_none(java, MAX_EXECUTABLE_BYTES),
            "kicad_cli_sha256": _hash_or_none(kicad_cli, MAX_EXECUTABLE_BYTES),
            "kicad_python_sha256": _hash_or_none(kicad_python, MAX_EXECUTABLE_BYTES),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "seed": seed,
        "timeout_seconds": timeout_seconds,
        "preflight": gate,
        "method": {
            "freerouting_boundary": "documented DSN/SES CLI",
            "kicad_specctra_transaction": (
                "harness-owned private DSN export and SES import"
                if kicad_python is not None
                else "not requested"
            ),
            "source_preservation": "source bytes hashed before and after every process",
            "gpl_boundary": "released JAR process only; no FreeRouting source is copied or linked",
            "command_environment": "minimal allowlisted environment; no inherited provider tokens",
            "isolation_limit": (
                "authorized executables are not sandboxed; isolation is lifecycle and resource "
                "containment"
            ),
        },
    }
    harness_requested = kicad_python is not None
    containment_refused = harness_requested and workspace_capability is None
    source_drc = (
        gui_source_drc_metrics(source, source_drc_report)
        if source_drc_report is not None
        else (
            private_source_drc_metrics(workspace_capability, kicad_cli, source, timeout_seconds)
            if harness_requested and workspace_capability is not None and kicad_cli is not None
            else (
                {"status": "unavailable", "reason": "private workspace containment is unavailable"}
                if containment_refused
                else drc_metrics(kicad_cli, source, timeout_seconds, ROOT, role="kicad_source_drc")
            )
        )
    )
    report["source_drc"] = source_drc
    report["source_drc_binding"] = source_drc_binding(
        source_before,
        source_drc,
        baseline_expectation,
    )
    freerouting_process: ProcessResult | None = None
    freerouting_ses_sha256: str | None = None
    transaction_result: dict[str, Any] | None = None
    harness_transaction_bound = False
    if gate["available"] and java and jar and kicad_python is not None:
        transaction, transaction_result = _harness_freerouting_transaction(
            source=source,
            java=java,
            jar=jar,
            kicad_python=kicad_python,
            kicad_cli=kicad_cli,
            timeout_seconds=timeout_seconds,
            source_sha256=source_before,
            cwd=ROOT,
            workspace_capability=workspace_capability,
        )
        report["freerouting_transaction"] = transaction
        export_record = transaction.get("kicad_export")
        if isinstance(export_record, dict):
            exported_dsn = export_record.get("dsn_sha256")
            if isinstance(exported_dsn, str):
                report["fixture"]["dsn_sha256"] = exported_dsn
                report["fixture"]["dsn_source_export_binding"] = {
                    "status": "harness_bound",
                    "workflow": "kicad_pcbnew_export_specctra_dsn",
                }
        router_record = transaction.get("freerouting_process")
        if isinstance(router_record, dict):
            report["freerouting_process"] = router_record
        else:
            report["freerouting_process"] = {"status": "unavailable"}
        harness_transaction_bound = transaction.get("status") == "bound"
    elif gate["available"] and dsn and java and jar:
        with tempfile.TemporaryDirectory(prefix="copper-mcp-freerouting-") as directory:
            workspace = Path(directory)
            ses = workspace / "freerouting.ses"
            freerouting_process = run_process(
                freerouting_argv(java, jar, dsn, ses), timeout_seconds, workspace
            )
            if freerouting_process.status == "ok":
                freerouting_ses_sha256 = _validate_ses(ses)
            report["freerouting_process"] = {
                **process_record(freerouting_process, "freerouting_dsn_ses"),
                "ses_sha256": freerouting_ses_sha256,
                "ses_status": "valid" if freerouting_ses_sha256 else "missing_or_invalid",
            }
    else:
        report["freerouting_process"] = {
            "status": "unavailable",
            "reason": "preflight did not close",
        }

    freerouting_binding = (
        "harness_bound"
        if harness_transaction_bound
        else (
            _binding_status(
                free_receipt,
                source_sha256=source_before,
                result_board=freerouting_board,
                output_sha256=freerouting_ses_sha256
                if freerouting_process and freerouting_process.status == "ok"
                else None,
                output_key="ses_sha256",
            )
            if free_receipt_status == "ok"
            else free_receipt_status
        )
    )
    report["freerouting_import_binding"] = {"status": freerouting_binding}

    copper_elapsed = 0
    generated_copper: Path | None = copper_board
    copper_output_sha256: str | None = None
    report["copper_process"] = {"status": "unavailable", "reason": "runner was not supplied"}
    if copper_command is not None and source_before is not None and not containment_refused:
        workspace_context = (
            private_transaction_workspace(workspace_capability)
            if harness_requested and workspace_capability is not None
            else tempfile.TemporaryDirectory(prefix="copper-mcp-copper-runner-")
        )
        with workspace_context as directory:
            workspace = Path(directory)
            private_source = workspace / source.name
            private_source.write_bytes(read_bounded_bytes(source, MAX_BOARD_BYTES))
            output = workspace / "copper-result.kicad_pcb"
            copper_process = run_process(
                copper_argv(copper_command, private_source, output, seed),
                timeout_seconds,
                workspace,
            )
            report["copper_process"] = process_record(copper_process, "copper_runner")
            copper_elapsed = copper_process.elapsed_ns
            if copper_process.status == "ok" and output.is_file():
                try:
                    output_bytes = read_bounded_bytes(output, MAX_BOARD_BYTES)
                except (OSError, ValueError):
                    report["copper_process"]["output_status"] = "missing_or_invalid"
                else:
                    copper_output_sha256 = "sha256:" + hashlib.sha256(output_bytes).hexdigest()
                    report["copper_process"]["output_sha256"] = copper_output_sha256
                    report["copper_process"]["output_status"] = "valid"
            elif copper_process.status == "ok":
                report["copper_process"]["output_status"] = "missing_or_invalid"
    elif containment_refused:
        report["copper_process"] = {
            "status": "unavailable",
            "reason": "private workspace containment is unavailable",
        }
    copper_binding = (
        _binding_status(
            copper_run_receipt,
            source_sha256=source_before,
            result_board=copper_board,
            output_sha256=copper_output_sha256,
            output_key="runner_output_sha256",
        )
        if copper_receipt_status == "ok"
        else copper_receipt_status
    )
    report["copper_runner_binding"] = {"status": copper_binding}
    result_kicad_cli = None if containment_refused else kicad_cli
    result_cwd = workspace_capability.root if harness_requested and workspace_capability else ROOT
    report["results"] = [
        (
            private_result_for_board(
                "copper_mcp",
                generated_copper,
                workspace_capability,
                result_kicad_cli,
                timeout_seconds,
                copper_elapsed,
                copper_drc_report,
            )
            if harness_requested and workspace_capability is not None
            else _result_for_board(
                "copper_mcp",
                generated_copper,
                result_kicad_cli,
                timeout_seconds,
                result_cwd,
                copper_elapsed,
                copper_drc_report,
            )
        ),
        transaction_result
        if transaction_result is not None
        else (
            private_result_for_board(
                "freerouting",
                freerouting_board,
                workspace_capability,
                result_kicad_cli,
                timeout_seconds,
                freerouting_process.elapsed_ns if freerouting_process else 0,
                freerouting_drc_report,
            )
            if harness_requested and workspace_capability is not None
            else _result_for_board(
                "freerouting",
                freerouting_board,
                result_kicad_cli,
                timeout_seconds,
                result_cwd,
                freerouting_process.elapsed_ns if freerouting_process else 0,
                freerouting_drc_report,
            )
        ),
    ]
    report["results"].sort(key=metric_priority)
    source_after = _hash_or_none(source, MAX_BOARD_BYTES)
    report["source_preserved"] = source_before is not None and source_before == source_after
    self_attested_evidence = bool(
        gate["available"]
        and report["source_preserved"]
        and freerouting_binding in {"bound", "harness_bound"}
        and copper_binding == "bound"
        and all(item.get("drc", {}).get("status") == "ok" for item in report["results"])
    )
    # The KiCad transaction can causally bind the FreeRouting side, but the optional CopperMCP
    # command template remains an external, self-attested runner.  Do not turn matching DRC and
    # hashes into a parity or completion claim until that competing runner is harness-owned too.
    report["comparison_closed"] = False
    if self_attested_evidence and harness_transaction_bound:
        report["incomplete_reason"] = "copper_runner_self_attested_unverified"
    else:
        report["incomplete_reason"] = (
            "self_attested_unverified" if self_attested_evidence else "incomplete_evidence"
        )
    report["status"] = "unavailable_or_incomplete"
    # ADR-0109's emission gate, before the digest that is this artifact's identity: it walks the
    # whole report rather than the sections this function remembered to build, so a DRC count
    # published under a new key in a nested record is refused by the call already made here.
    require_qualified(report, where=SCHEMA)
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    report["run_id"] = "sha256:" + hashlib.sha256(canonical).hexdigest()
    return report


def _path(value: str) -> Path:
    return Path(value).expanduser().resolve()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=_path,
        required=True,
        help="Independently authored KiCad source board; never modified.",
    )
    parser.add_argument("--fixture-provenance", type=_path, required=True)
    parser.add_argument("--dsn", type=_path, help="KiCad-exported DSN for the exact source board.")
    parser.add_argument("--java", type=_path, default=Path(shutil.which("java") or "java"))
    parser.add_argument("--freerouting-jar", type=_path)
    parser.add_argument(
        "--freerouting-release-provenance",
        type=_path,
        help=(
            "Optional bounded record binding the JAR to the official FreeRouting GitHub "
            "release URL and its SHA-256."
        ),
    )
    parser.add_argument(
        "--kicad-cli", type=_path, default=Path(shutil.which("kicad-cli") or "kicad-cli")
    )
    parser.add_argument(
        "--kicad-python",
        type=_path,
        help=(
            "KiCad-bundled Python interpreter. When supplied, the harness itself exports DSN "
            "and imports SES in a private workspace using KiCad's pcbnew binding."
        ),
    )
    parser.add_argument("--copper-board", type=_path, help="CopperMCP's disposable result board.")
    parser.add_argument(
        "--freerouting-board", type=_path, help="KiCad copy after importing FreeRouting's SES."
    )
    parser.add_argument("--freerouting-import-receipt", type=_path)
    parser.add_argument("--copper-receipt", type=_path)
    parser.add_argument(
        "--source-drc-report",
        type=_path,
        help=(
            "Bounded KiCad-GUI DRC .rpt for the exact source basename. This is required when "
            "the local KiCad CLI cannot produce the pre-route DRC report."
        ),
    )
    parser.add_argument("--copper-drc-report", type=_path)
    parser.add_argument("--freerouting-drc-report", type=_path)
    parser.add_argument(
        "--copper-command-json",
        type=_path,
        help="JSON argv template; allowed placeholders: source, output, seed.",
    )
    parser.add_argument("--seed", type=int, default=23)
    parser.add_argument("--timeout-seconds", type=int, default=300)
    parser.add_argument("--output", type=_path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if not 1 <= args.timeout_seconds <= 3_600:
        raise SystemExit("--timeout-seconds must be between 1 and 3600")
    try:
        report = build_report(
            source=args.source,
            dsn=args.dsn,
            java=args.java,
            jar=args.freerouting_jar,
            kicad_cli=args.kicad_cli,
            provenance=args.fixture_provenance,
            copper_board=args.copper_board,
            freerouting_board=args.freerouting_board,
            copper_receipt=args.copper_receipt,
            freerouting_receipt=args.freerouting_import_receipt,
            copper_command=_parse_template(args.copper_command_json),
            seed=args.seed,
            timeout_seconds=args.timeout_seconds,
            release_provenance=args.freerouting_release_provenance,
            kicad_python=args.kicad_python,
            source_drc_report=args.source_drc_report,
            copper_drc_report=args.copper_drc_report,
            freerouting_drc_report=args.freerouting_drc_report,
        )
    except (OSError, ValueError, json.JSONDecodeError) as error:
        raise SystemExit(
            f"FreeRouting comparison benchmark failed: {redact(str(error))}"
        ) from error
    rendered = json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main())
