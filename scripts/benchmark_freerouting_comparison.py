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
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import IO, Any, cast

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
SCHEMA = "copper-mcp/benchmark/freerouting-comparison/v1"
FREEROUTING_LICENSE = "GPL-3.0-only"
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
FREEROUTING_RECEIPT_SCHEMA = "copper-mcp/freerouting-ses-import-receipt/v1"
COPPER_RECEIPT_SCHEMA = "copper-mcp/candidate-runner-receipt/v1"
_RECEIPT_SHA256 = re.compile(r"^sha256:[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    elapsed_ns: int
    returncode: int | None
    status: str
    stdout: str
    stderr: str


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
    """Return the documented FreeRouting v2 DSN-to-SES command, without a shell."""

    return (str(java), "-jar", str(jar), "-de", str(dsn), "-do", str(ses), "-l", "en")


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
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
            return
        except ProcessLookupError:
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


def run_process(argv: tuple[str, ...], timeout_seconds: int, cwd: Path) -> ProcessResult:
    """Run one process with bounded streaming capture and group termination on failure."""

    started = time.perf_counter_ns()
    try:
        process = subprocess.Popen(  # noqa: S603 - argv is explicit, shell is never used
            argv,
            cwd=cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=os.name == "posix",
            env=minimal_environment(cwd),
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
) -> dict[str, Any]:
    """Return all prerequisite failures in one truthful, serializable record."""

    reasons: list[str] = []
    for label, path, maximum in (
        ("source board", source, MAX_BOARD_BYTES),
        ("DSN", dsn, MAX_DSN_BYTES),
        ("Java", java, MAX_EXECUTABLE_BYTES),
        ("FreeRouting JAR", jar, MAX_JAR_BYTES),
        ("KiCad CLI", kicad_cli, MAX_EXECUTABLE_BYTES),
    ):
        if path is None or not path.is_file():
            reasons.append(f"{label} is unavailable")
        elif path.stat().st_size > maximum:
            reasons.append(f"{label} exceeds its byte limit")
    _, provenance_reasons = _provenance(provenance)
    reasons.extend(provenance_reasons)
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


def drc_metrics(kicad_cli: Path, board: Path, timeout_seconds: int, cwd: Path) -> dict[str, Any]:
    """Run the same authoritative KiCad DRC command for either result board."""

    with tempfile.TemporaryDirectory(prefix="copper-mcp-freerouting-drc-") as directory:
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
        result = run_process(command, timeout_seconds, cwd)
        output: dict[str, Any] = {
            "process": process_record(result, "kicad_drc"),
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
                expected_source=str(board),
            )
        except (KiCadCliError, ValueError, OSError):
            output["status"] = "failed"
            return output
        output.update(
            {
                "hard_violations": summary.error_count,
                "kicad_version": summary.kicad_version,
                "report_sha256": sha256_file(report, MAX_DRC_REPORT_BYTES),
                "status": "ok",
                "unconnected": summary.unconnected_count,
            }
        )
        return output


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
        drc_metrics(kicad_cli, board, timeout_seconds, cwd)
        if kicad_cli
        else {"status": "unavailable"}
    )
    return metrics


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
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Run available bounded stages and return content-addressed evidence; never mutate source."""

    gate = preflight(
        source=source,
        dsn=dsn,
        java=java,
        jar=jar,
        kicad_cli=kicad_cli,
        provenance=provenance,
        cwd=ROOT,
    )
    source_before = _hash_or_none(source, MAX_BOARD_BYTES)
    fixture, _ = _provenance(provenance)
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
        },
        "toolchain": {
            "freerouting_license": FREEROUTING_LICENSE,
            "freerouting_jar_sha256": _hash_or_none(jar, MAX_JAR_BYTES),
            "java_sha256": _hash_or_none(java, MAX_EXECUTABLE_BYTES),
            "kicad_cli_sha256": _hash_or_none(kicad_cli, MAX_EXECUTABLE_BYTES),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "seed": seed,
        "timeout_seconds": timeout_seconds,
        "preflight": gate,
        "method": {
            "freerouting_boundary": "documented DSN/SES CLI",
            "source_preservation": "source bytes hashed before and after every process",
            "gpl_boundary": "released JAR process only; no FreeRouting source is copied or linked",
            "command_environment": "minimal allowlisted environment; no inherited provider tokens",
            "isolation_limit": (
                "authorized executables are not sandboxed; isolation is lifecycle and resource "
                "containment"
            ),
        },
    }
    freerouting_process: ProcessResult | None = None
    freerouting_ses_sha256: str | None = None
    if gate["available"] and dsn and java and jar:
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
    report["freerouting_import_binding"] = {"status": freerouting_binding}

    copper_elapsed = 0
    generated_copper: Path | None = copper_board
    copper_output_sha256: str | None = None
    report["copper_process"] = {"status": "unavailable", "reason": "runner was not supplied"}
    if copper_command is not None and source_before is not None:
        with tempfile.TemporaryDirectory(prefix="copper-mcp-copper-runner-") as directory:
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
    report["results"] = [
        _result_for_board(
            "copper_mcp", generated_copper, kicad_cli, timeout_seconds, ROOT, copper_elapsed
        ),
        _result_for_board(
            "freerouting",
            freerouting_board,
            kicad_cli,
            timeout_seconds,
            ROOT,
            freerouting_process.elapsed_ns if freerouting_process else 0,
        ),
    ]
    report["results"].sort(key=metric_priority)
    source_after = _hash_or_none(source, MAX_BOARD_BYTES)
    report["source_preserved"] = source_before is not None and source_before == source_after
    report["comparison_closed"] = bool(
        gate["available"]
        and report["source_preserved"]
        and freerouting_binding == "bound"
        and copper_binding == "bound"
        and all(item.get("drc", {}).get("status") == "ok" for item in report["results"])
    )
    report["status"] = "completed" if report["comparison_closed"] else "unavailable_or_incomplete"
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
        "--kicad-cli", type=_path, default=Path(shutil.which("kicad-cli") or "kicad-cli")
    )
    parser.add_argument("--copper-board", type=_path, help="CopperMCP's disposable result board.")
    parser.add_argument(
        "--freerouting-board", type=_path, help="KiCad copy after importing FreeRouting's SES."
    )
    parser.add_argument("--freerouting-import-receipt", type=_path)
    parser.add_argument("--copper-receipt", type=_path)
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
