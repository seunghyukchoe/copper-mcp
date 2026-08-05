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
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = "copper-mcp/benchmark/freerouting-comparison/v1"
FREEROUTING_LICENSE = "GPL-3.0-only"
REDACTED = "[redacted]"
_SECRET = re.compile(r"(?i)(bearer\s+|token=|password=|api[_-]?key=)[^\s]+")
_VIA = re.compile(r"\(via\b")
_SEGMENT = re.compile(
    r"\(segment\s+\(start\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)"
    r"\s+\(end\s+(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\)",
)


@dataclass(frozen=True, slots=True)
class ProcessResult:
    argv: tuple[str, ...]
    elapsed_ns: int
    returncode: int | None
    status: str
    stdout: str
    stderr: str


def sha256_file(path: Path) -> str:
    """Hash a file without interpreting its content."""

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return "sha256:" + digest.hexdigest()


def redact(value: str) -> str:
    """Keep bounded process diagnostics while removing common credentials."""

    return _SECRET.sub(lambda match: match.group(1) + REDACTED, value)[:8_000]


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


def run_process(argv: tuple[str, ...], timeout_seconds: int, cwd: Path) -> ProcessResult:
    """Run one process with no shell and return redacted, bounded evidence."""

    started = time.perf_counter_ns()
    try:
        completed = subprocess.run(  # noqa: S603 - argv is explicit, shell is never used
            argv,
            cwd=cwd,
            capture_output=True,
            check=False,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired as error:
        return ProcessResult(
            argv=argv,
            elapsed_ns=time.perf_counter_ns() - started,
            returncode=None,
            status="timeout",
            stdout=redact(_output_text(error.stdout)),
            stderr=redact(_output_text(error.stderr)),
        )
    except OSError as error:
        return ProcessResult(
            argv=argv,
            elapsed_ns=time.perf_counter_ns() - started,
            returncode=None,
            status="unavailable",
            stdout="",
            stderr=redact(str(error)),
        )
    return ProcessResult(
        argv=argv,
        elapsed_ns=time.perf_counter_ns() - started,
        returncode=completed.returncode,
        status="ok" if completed.returncode == 0 else "failed",
        stdout=redact(completed.stdout),
        stderr=redact(completed.stderr),
    )


def version_probe(executable: Path, cwd: Path) -> dict[str, Any]:
    """Capture a local executable version as diagnostic evidence, never a requirement."""

    result = run_process((str(executable), "--version"), 10, cwd)
    return {"status": result.status, "stdout": result.stdout, "stderr": result.stderr}


def _parse_template(path: Path | None) -> tuple[str, ...] | None:
    if path is None:
        return None
    raw = json.loads(path.read_text(encoding="utf-8"))
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
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        return None, [f"fixture provenance cannot be read: {error}"]
    if not isinstance(value, dict):
        return None, ["fixture provenance must be a JSON object"]
    missing = [
        key for key in ("license_spdx", "origin", "derivation_statement") if not value.get(key)
    ]
    if value.get("origin") in {"third-party", "unknown"}:
        missing.append("fixture provenance must say independently authored or coppermcp-original")
    return value, missing


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
    for label, path in (
        ("source board", source),
        ("DSN", dsn),
        ("Java", java),
        ("FreeRouting JAR", jar),
        ("KiCad CLI", kicad_cli),
    ):
        if path is None or not path.is_file():
            reasons.append(f"{label} is unavailable")
    _, provenance_reasons = _provenance(provenance)
    reasons.extend(provenance_reasons)
    probes: dict[str, Any] = {}
    if java is not None and java.is_file():
        probes["java"] = version_probe(java, cwd)
        if probes["java"]["status"] != "ok":
            reasons.append("Java runtime did not execute --version")
    if kicad_cli is not None and kicad_cli.is_file():
        probes["kicad_cli"] = version_probe(kicad_cli, cwd)
        if probes["kicad_cli"]["status"] != "ok":
            reasons.append("KiCad CLI did not execute --version")
    return {"available": not reasons, "reasons": reasons, "probes": probes}


def board_metrics(board: Path) -> dict[str, int | str]:
    """Count board-text vias and segment length only after KiCad DRC remains authoritative."""

    content = board.read_text(encoding="utf-8")
    length_mm = sum(
        math.hypot(float(end_x) - float(start_x), float(end_y) - float(start_y))
        for start_x, start_y, end_x, end_y in _SEGMENT.findall(content)
    )
    return {
        "board_sha256": sha256_file(board),
        "length_nm": round(length_mm * 1_000_000),
        "vias": len(_VIA.findall(content)),
    }


def _walk_drc(value: Any) -> tuple[int, int]:
    """Best-effort count of hard violations and unconnected evidence from KiCad JSON."""

    if isinstance(value, dict):
        values = list(value.values())
        text = " ".join(str(item).lower() for item in values if isinstance(item, str))
        hard = 1 if value.get("severity") == "error" else 0
        unconnected = 1 if "unconnected" in text else 0
        nested = [_walk_drc(item) for item in values]
        return hard + sum(item[0] for item in nested), unconnected + sum(item[1] for item in nested)
    if isinstance(value, list):
        nested = [_walk_drc(item) for item in value]
        return sum(item[0] for item in nested), sum(item[1] for item in nested)
    return 0, 0


def drc_metrics(kicad_cli: Path, board: Path, timeout_seconds: int, cwd: Path) -> dict[str, Any]:
    """Run the same authoritative KiCad DRC command for either result board."""

    with tempfile.TemporaryDirectory(prefix="copper-mcp-freerouting-drc-") as directory:
        report = Path(directory) / "drc.json"
        result = run_process(
            (str(kicad_cli), "pcb", "drc", "--format", "json", "--output", str(report), str(board)),
            timeout_seconds,
            cwd,
        )
        output: dict[str, Any] = {"process": process_record(result), "status": result.status}
        if result.status != "ok" or not report.is_file():
            return output
        try:
            parsed = json.loads(report.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            output["status"] = "failed"
            output["parse_error"] = str(error)
            return output
        hard, unconnected = _walk_drc(parsed)
        output.update(
            {
                "hard_violations": hard,
                "report_sha256": sha256_file(report),
                "status": "ok",
                "unconnected": unconnected,
            }
        )
        return output


def process_record(result: ProcessResult) -> dict[str, Any]:
    return {
        "argv": list(result.argv),
        "elapsed_ns": result.elapsed_ns,
        "returncode": result.returncode,
        "status": result.status,
        "stderr": result.stderr,
        "stdout": result.stdout,
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
    metrics: dict[str, Any] = {
        "name": name,
        "elapsed_ns": elapsed_ns,
        "status": "ok",
        **board_metrics(board),
    }
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
    copper_command: tuple[str, ...] | None,
    seed: int,
    timeout_seconds: int,
    timestamp: datetime | None = None,
) -> dict[str, Any]:
    """Run available bounded stages and return content-addressed evidence; never mutate source."""

    source_before = sha256_file(source) if source.is_file() else None
    gate = preflight(
        source=source,
        dsn=dsn,
        java=java,
        jar=jar,
        kicad_cli=kicad_cli,
        provenance=provenance,
        cwd=ROOT,
    )
    fixture, _ = _provenance(provenance)
    report: dict[str, Any] = {
        "schema": SCHEMA,
        "recorded_at_utc": (timestamp or datetime.now(UTC)).replace(microsecond=0).isoformat(),
        "fixture": {
            "source_sha256": source_before,
            "dsn_sha256": sha256_file(dsn) if dsn and dsn.is_file() else None,
            "provenance_sha256": sha256_file(provenance)
            if provenance and provenance.is_file()
            else None,
            "provenance": fixture,
        },
        "toolchain": {
            "freerouting_license": FREEROUTING_LICENSE,
            "freerouting_jar_name": jar.name if jar else None,
            "freerouting_jar_sha256": sha256_file(jar) if jar and jar.is_file() else None,
            "java_sha256": sha256_file(java) if java and java.is_file() else None,
            "kicad_cli_sha256": sha256_file(kicad_cli)
            if kicad_cli and kicad_cli.is_file()
            else None,
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
        },
    }
    freerouting_process: ProcessResult | None = None
    if gate["available"] and dsn and java and jar:
        with tempfile.TemporaryDirectory(prefix="copper-mcp-freerouting-") as directory:
            workspace = Path(directory)
            ses = workspace / "freerouting.ses"
            freerouting_process = run_process(
                freerouting_argv(java, jar, dsn, ses), timeout_seconds, workspace
            )
            report["freerouting_process"] = {
                **process_record(freerouting_process),
                "ses_sha256": sha256_file(ses) if ses.is_file() else None,
            }
    else:
        report["freerouting_process"] = {
            "status": "unavailable",
            "reason": "preflight did not close",
        }

    copper_elapsed = 0
    generated_copper: Path | None = copper_board
    if copper_command is not None and source.is_file():
        with tempfile.TemporaryDirectory(prefix="copper-mcp-copper-runner-") as directory:
            workspace = Path(directory)
            private_source = workspace / source.name
            private_source.write_bytes(source.read_bytes())
            output = workspace / "copper-result.kicad_pcb"
            copper_process = run_process(
                copper_argv(copper_command, private_source, output, seed),
                timeout_seconds,
                workspace,
            )
            report["copper_process"] = process_record(copper_process)
            copper_elapsed = copper_process.elapsed_ns
            if copper_process.status == "ok" and output.is_file():
                persisted = workspace / "result-copy.kicad_pcb"
                persisted.write_bytes(output.read_bytes())
                # The process output is ephemeral; an explicit board is required for DRC evidence.
                report["copper_process"]["output_sha256"] = sha256_file(persisted)
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
    source_after = sha256_file(source) if source.is_file() else None
    report["source_preserved"] = source_before is not None and source_before == source_after
    report["comparison_closed"] = bool(
        gate["available"]
        and report["source_preserved"]
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
