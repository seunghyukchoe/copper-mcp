"""Tests for the candidate-byte source-to-board parity primitive."""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from copper_mcp.adapters.kicad_schematic import render_kicad_schematic
from copper_mcp.circuit_ir import decode_snapshot_json
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KICAD_DRC_SCHEMA,
    PARITY_BOARD_SNAPSHOT_NAME,
    KiCadCliError,
    _run_captured_source_to_board_parity,
)

ROOT = Path(__file__).resolve().parents[1]
INTENT = ROOT / "benchmarks" / "audio" / "fixtures" / "rc-low-pass-intent-v1.json"
BOARDS = ROOT / "tests" / "fixtures" / "source-to-board-parity"
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")


def digest(payload: bytes) -> str:
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def inputs() -> tuple[bytes, bytes, dict[str, object]]:
    snapshot = decode_snapshot_json(INTENT.read_bytes())
    delivered = render_kicad_schematic(snapshot)
    projection = render_kicad_schematic(snapshot, board_eligible=True)
    board = (BOARDS / "matching.kicad_pcb").read_bytes()
    return (
        board,
        projection.content,
        {
            "expected_board_revision": digest(board),
            "component_count": projection.component_count,
            "intent_digest": delivered.intent_digest,
            "schematic_digest": delivered.artifact_digest,
            "parity_schematic_digest": projection.artifact_digest,
        },
    )


def report() -> bytes:
    return json.dumps(
        {
            "$schema": KICAD_DRC_SCHEMA,
            "coordinate_units": "mm",
            "date": "2026-09-06T00:00:00",
            "ignored_checks": [],
            "included_severities": ["error", "warning", "exclusion"],
            "kicad_version": "10.0.5",
            "schematic_parity": [
                {
                    "type": "footprint_symbol_mismatch",
                    "description": "",
                    "severity": "warning",
                    "items": [],
                },
                {
                    "type": "footprint_symbol_mismatch",
                    "description": "",
                    "severity": "warning",
                    "items": [],
                },
            ],
            "source": PARITY_BOARD_SNAPSHOT_NAME,
            "unconnected_items": [],
            "violations": [],
        }
    ).encode()


def hermetic_settings(tmp_path: Path) -> Settings:
    return Settings(workspace=tmp_path, kicad_cli=Path(sys.executable))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("board_bytes", b""),
        ("board_bytes", True),
        ("expected_board_revision", True),
        ("expected_board_revision", "sha256:" + "0" * 64),
    ],
)
def test_rejects_invalid_captured_board_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, field: str, value: object
) -> None:
    board, projection, fields = inputs()
    monkeypatch.setattr(
        "copper_mcp.kicad_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run for invalid captured input"),
    )
    if field == "board_bytes":
        board = value  # type: ignore[assignment]
    else:
        fields[field] = value
    with pytest.raises(KiCadCliError):
        _run_captured_source_to_board_parity(
            board,
            projection,
            settings=hermetic_settings(tmp_path),
            **fields,  # type: ignore[arg-type]
        )


def test_rejects_oversized_captured_board_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    monkeypatch.setattr(
        "copper_mcp.kicad_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run for oversized input"),
    )
    settings = Settings(
        workspace=tmp_path,
        kicad_cli=Path(sys.executable),
        max_board_bytes=len(board) - 1,
    )
    with pytest.raises(KiCadCliError, match="byte ceiling"):
        _run_captured_source_to_board_parity(board, projection, settings=settings, **fields)


def test_rejects_stale_captured_board_revision_before_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    monkeypatch.setattr(
        "copper_mcp.kicad_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("subprocess must not run for a stale board revision"),
    )
    with pytest.raises(KiCadCliError, match="does not match"):
        _run_captured_source_to_board_parity(
            board + b"\n",
            projection,
            settings=hermetic_settings(tmp_path),
            **fields,
        )


def test_deadline_exhaustion_before_execution_refuses_without_subprocess(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    monkeypatch.setattr("copper_mcp.kicad_cli.time.monotonic", lambda: 10.0)
    monkeypatch.setattr(
        "copper_mcp.kicad_cli._revision",
        lambda _payload: pytest.fail("expired deadline must prevent content hashing"),
    )
    monkeypatch.setattr(
        "copper_mcp.kicad_cli.subprocess.run",
        lambda *args, **kwargs: pytest.fail("expired deadline must prevent execution"),
    )
    with pytest.raises(KiCadCliError, match="deadline exceeded"):
        _run_captured_source_to_board_parity(
            board, projection, settings=hermetic_settings(tmp_path), deadline=10.0, **fields
        )


@pytest.mark.parametrize(
    "deadline",
    [True, "soon", float("nan"), float("inf"), float("-inf"), 10**1000, -(10**1000)],
)
def test_rejects_malformed_deadline_before_candidate_deadline_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, deadline: object
) -> None:
    board, projection, fields = inputs()
    monkeypatch.setattr(
        "copper_mcp.kicad_cli._candidate_drc_deadline_settings",
        lambda *args, **kwargs: pytest.fail("malformed deadline reached the candidate helper"),
    )
    with pytest.raises(KiCadCliError, match="captured parity deadline is malformed"):
        _run_captured_source_to_board_parity(
            board,
            projection,
            settings=hermetic_settings(tmp_path),
            deadline=deadline,  # type: ignore[arg-type]
            **fields,
        )


def test_deadline_exhaustion_after_execution_refuses_late_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    clock = [10.0]
    invoked = []

    def late_completion(*args: object, **kwargs: object) -> SimpleNamespace:
        invoked.append(True)
        clock[0] = 11.0
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr("copper_mcp.kicad_cli.time.monotonic", lambda: clock[0])
    monkeypatch.setattr("copper_mcp.kicad_cli.subprocess.run", late_completion)
    with pytest.raises(KiCadCliError, match="deadline exceeded"):
        _run_captured_source_to_board_parity(
            board, projection, settings=hermetic_settings(tmp_path), deadline=11.0, **fields
        )
    assert invoked == [True]


def test_deadline_exhaustion_after_report_parsing_refuses_late_success(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    from copper_mcp import kicad_cli

    original_parse = kicad_cli._parse_parity_report
    clock = [10.0]
    parsed = []

    def late_parse(*args: object, **kwargs: object) -> object:
        result = original_parse(*args, **kwargs)
        parsed.append(True)
        clock[0] = 70.0
        return result

    def write_report(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[command.index("--output") + 1]).write_bytes(report())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli, "_parse_parity_report", late_parse)
    monkeypatch.setattr(kicad_cli.subprocess, "run", write_report)
    with pytest.raises(KiCadCliError, match="deadline exceeded"):
        _run_captured_source_to_board_parity(
            board,
            projection,
            settings=hermetic_settings(tmp_path),
            deadline=70.0,
            **fields,
        )
    assert parsed == [True]


@pytest.mark.parametrize("stage", ["board_hash", "private_state", "snapshot_tree"])
def test_expiry_between_phases_prevents_the_next_phase(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    from copper_mcp import kicad_cli

    board, projection, fields = inputs()
    clock = [10.0]
    completed = []
    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])

    def write_report(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[command.index("--output") + 1]).write_bytes(report())
        return SimpleNamespace(returncode=0)

    def forbidden(*args: object, **kwargs: object) -> None:
        pytest.fail("expired budget reached the next phase")

    monkeypatch.setattr(kicad_cli.subprocess, "run", write_report)
    name = {
        "board_hash": "_revision",
        "private_state": "_validate_private_kicad_state",
        "snapshot_tree": "_validate_snapshot_tree",
    }[stage]
    original = getattr(kicad_cli, name)

    def expire_after_phase(*args: object, **kwargs: object) -> object:
        result = original(*args, **kwargs)
        completed.append(stage)
        clock[0] = 70.0
        return result

    monkeypatch.setattr(kicad_cli, name, expire_after_phase)
    if stage == "board_hash":
        monkeypatch.setattr(kicad_cli, "_validate_source_to_board_parity_projection", forbidden)
    elif stage == "private_state":
        monkeypatch.setattr(kicad_cli, "_validate_snapshot_tree", forbidden)
    else:
        monkeypatch.setattr(Path, "read_bytes", forbidden)
    with pytest.raises(KiCadCliError, match="deadline exceeded"):
        _run_captured_source_to_board_parity(
            board, projection, settings=hermetic_settings(tmp_path), deadline=70.0, **fields
        )
    assert completed == [stage]


def test_captured_board_never_reads_workspace_source_but_may_read_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    board, projection, fields = inputs()
    settings = hermetic_settings(tmp_path)
    from copper_mcp import kicad_cli

    original_read = kicad_cli.read_workspace_file

    def only_private_report(root: Path, *args: object, **kwargs: object) -> object:
        if root == settings.workspace:
            pytest.fail("captured primitive must not re-read the workspace board")
        return original_read(root, *args, **kwargs)

    def write_report(command: list[str], **kwargs: object) -> SimpleNamespace:
        Path(command[command.index("--output") + 1]).write_bytes(report())
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(kicad_cli, "read_workspace_file", only_private_report)
    monkeypatch.setattr(kicad_cli.subprocess, "run", write_report)
    evidence = _run_captured_source_to_board_parity(board, projection, settings=settings, **fields)
    assert evidence.passed is True


@pytest.mark.real_kicad
@pytest.mark.skipif(not REAL_KICAD_CLI.exists(), reason="real KiCad CLI is unavailable")
@pytest.mark.parametrize(
    ("captured_name", "workspace_name", "passed"),
    [
        ("matching.kicad_pcb", "net-mismatch.kicad_pcb", True),
        ("net-mismatch.kicad_pcb", "matching.kicad_pcb", False),
    ],
)
def test_real_kicad_uses_supplied_captured_board_not_workspace_file(
    captured_name: str, workspace_name: str, passed: bool
) -> None:
    board, projection, fields = inputs()
    board = (BOARDS / captured_name).read_bytes()
    captured_digest = digest(board)
    fields["expected_board_revision"] = captured_digest
    with tempfile.TemporaryDirectory() as temporary_directory:
        workspace = Path(temporary_directory)
        workspace_board = workspace / "board.kicad_pcb"
        shutil.copy(BOARDS / workspace_name, workspace_board)
        workspace_bytes = workspace_board.read_bytes()
        evidence = _run_captured_source_to_board_parity(
            board,
            projection,
            settings=Settings(workspace=workspace, kicad_cli=REAL_KICAD_CLI),
            **fields,
        )
        assert workspace_board.read_bytes() == workspace_bytes
    assert evidence.passed is passed
    assert evidence.board_revision == captured_digest
    if not passed:
        assert evidence.parity_type_counts["net_conflict"] == 1
