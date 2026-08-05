from __future__ import annotations

import hashlib
import json
import signal
import stat
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any, BinaryIO, cast

import pytest

import copper_mcp.kicad_cli as kicad_cli
from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.models import DrcSummary
from copper_mcp.placement import build_placement_view, evaluate_placement, parse_placement_intent
from copper_mcp.placement_drc import PlacementCandidateDrcEvidence, run_placement_candidate_drc

FIXTURE = (
    Path(__file__).parent / "fixtures" / "board-ir-v0.2" / "footprint-pose-courtyard.kicad_pcb"
)
REAL_KICAD_CLI = Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
CONSTRAINTS = {
    "clearance_nm": 200_000,
    "track_width_nm": 250_000,
    "via_diameter_nm": 600_000,
    "via_drill_nm": 300_000,
}


def _profile() -> KiCadConstraintProfile:
    net_class = NetClass(id="class:request", name="Request", **CONSTRAINTS)
    return KiCadConstraintProfile(net_classes=(net_class,), default_net_class_id=net_class.id)


def _candidate(source: bytes | None = None) -> tuple[bytes, object, KiCadConstraintProfile, object]:
    source = FIXTURE.read_bytes() if source is None else source
    profile = _profile()
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    snapshot = conversion.snapshot
    view = build_placement_view(source, snapshot)
    refs = sorted(view.footprints)
    result = evaluate_placement(
        parse_placement_intent(
            {
                "board": "placement-fixture.kicad_pcb",
                "constraints": CONSTRAINTS,
                "subjects": refs,
                "proposals": [
                    {
                        "subject": refs[1],
                        "offset_x_nm": 2_000_000,
                        "offset_y_nm": 1_000_000,
                        "orientation_udeg": 180_000_000,
                    }
                ],
            }
        ),
        snapshot,
        view,
    )
    assert result.candidate is not None
    return source, snapshot, profile, result.candidate


def _report(
    source: str,
    *,
    violations: list[dict[str, object]] | None = None,
    unconnected_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "$schema": "https://schemas.kicad.org/drc.v1.json",
        "source": source,
        "date": "2026-08-03T12:00:00+09:00",
        "coordinate_units": "mm",
        "kicad_version": "10.0.5",
        "violations": violations or [],
        "unconnected_items": unconnected_items or [],
        "schematic_parity": [],
        "included_severities": ["error", "warning", "exclusion"],
        "ignored_checks": [],
    }


def _finding(
    finding_type: str,
    severity: str,
    *,
    description: str = "finding",
    items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "type": finding_type,
        "description": description,
        "severity": severity,
        "excluded": False,
        "items": items or [],
    }


def _fake_run(
    report: dict[str, object] | bytes,
    *,
    returncode: int = 0,
    after_report: Callable[[list[str]], None] | None = None,
    capture: dict[str, Any] | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert kwargs["stdin"] is subprocess.DEVNULL
        stdout = cast(BinaryIO, kwargs["stdout"])
        stderr = cast(BinaryIO, kwargs["stderr"])
        assert stdout.writable()
        assert stderr.writable()
        stdout.write(b"fake kicad stdout")
        stderr.write(b"fake kicad stderr")
        assert "preexec_fn" not in kwargs
        assert "--save-board" not in command
        assert "--refill-zones" not in command
        report_path = Path(command[command.index("--output") + 1])
        snapshot_board = Path(command[-1])
        if isinstance(report, bytes):
            report_path.write_bytes(report)
        else:
            report_path.write_text(json.dumps(report), encoding="utf-8")
        if capture is not None:
            capture["command"] = command
            capture["kwargs"] = kwargs
            capture["snapshot_bytes"] = snapshot_board.read_bytes()
            capture["temporary_root"] = report_path.parent
            capture["temporary_mode"] = stat.S_IMODE(report_path.parent.stat().st_mode)
        if after_report is not None:
            after_report(command)
        return subprocess.CompletedProcess(command, returncode)

    return run


def _install_fake_kicad(
    monkeypatch: pytest.MonkeyPatch,
    run: Callable[..., subprocess.CompletedProcess[str]],
) -> None:
    monkeypatch.setattr(
        kicad_cli, "discover_kicad_cli", lambda settings: Path("/trusted/kicad-cli")
    )
    monkeypatch.setattr(subprocess, "run", run)


def test_binds_clean_candidate_to_private_drc_context_and_preserves_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, snapshot, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    rules = board.with_suffix(".kicad_dru")
    rules.write_text("(version 1)", encoding="utf-8")
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    capture: dict[str, Any] = {}
    _install_fake_kicad(monkeypatch, _fake_run(_report(board.name), capture=capture))

    evidence = run_placement_candidate_drc(board.name, candidate, profile, settings)

    expected_context = kicad_cli._drc_context(board, settings)
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.candidate_base_revision == candidate.base_revision == snapshot.snapshot_digest
    assert evidence.source_revision == f"sha256:{hashlib.sha256(source).hexdigest()}"
    assert evidence.patched_board_revision != evidence.source_revision
    assert evidence.summary.base_revision == evidence.patched_board_revision
    assert evidence.summary.drc_context_revision == evidence.patched_drc_context_revision
    assert evidence.patched_drc_context_revision == kicad_cli._context_revision(
        {**expected_context, board.name: capture["snapshot_bytes"]}
    )
    assert capture["temporary_mode"] == 0o700
    assert not Path(capture["temporary_root"]).exists()
    assert board.read_bytes() == source
    command = capture["command"]
    assert command[command.index("/trusted/kicad-cli") :] == [
        "/trusted/kicad-cli",
        "pcb",
        "drc",
        "--format",
        "json",
        "--units",
        "mm",
        "--severity-all",
        "--exit-code-violations",
        "--output",
        command[command.index("--output") + 1],
        command[-1],
    ]


def test_violation_evidence_is_negative_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    report = _report(
        board.name,
        violations=[
            _finding(
                "courtyard_overlap",
                "error",
                description="PRIVATE_FOOTPRINT_TEXT",
                items=[{"uuid": "private-uuid", "description": "PRIVATE_NET"}],
            )
        ],
        unconnected_items=[_finding("unconnected_items", "error")],
    )
    _install_fake_kicad(monkeypatch, _fake_run(report, returncode=5))

    evidence = run_placement_candidate_drc(
        board.name, candidate, profile, Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    )

    assert evidence.summary.error_count == 1
    assert evidence.summary.unconnected_count == 1
    assert not evidence.summary.passed
    serialized = json.dumps(evidence.to_dict())
    assert "PRIVATE_FOOTPRINT_TEXT" not in serialized
    assert "PRIVATE_NET" not in serialized
    assert "private-uuid" not in serialized


def test_evidence_is_immutable_and_detached() -> None:
    patched = "sha256:" + "1" * 64
    context = "sha256:" + "2" * 64
    counts = {"clearance": 1}
    summary = DrcSummary(
        base_revision=patched,
        drc_context_revision=context,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=1,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts=counts,
        passed=True,
    )
    evidence = PlacementCandidateDrcEvidence(
        candidate_id="sha256:" + "3" * 64,
        candidate_base_revision="sha256:" + "4" * 64,
        source_revision="sha256:" + "5" * 64,
        patched_board_revision=patched,
        patched_drc_context_revision=context,
        summary=summary,
    )
    counts["late"] = 1
    assert "late" not in evidence.summary.violation_type_counts
    with pytest.raises(TypeError):
        evidence.summary.violation_type_counts["forbidden"] = 1  # type: ignore[index]
    detached = evidence.to_dict()
    assert isinstance(detached["summary"], dict)
    detached["summary"]["changed"] = True  # type: ignore[index]
    assert "changed" not in evidence.to_dict()["summary"]


def test_rejects_stale_tampered_and_unsupported_inputs_before_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    _install_fake_kicad(monkeypatch, unexpected_run)
    cases: list[tuple[object, object]] = [
        (replace(candidate, base_revision="sha256:" + "0" * 64), profile),
        (replace(candidate, candidate_id="sha256:" + "0" * 64), profile),
        (candidate, object()),
        (object(), profile),
    ]
    for rejected_candidate, rejected_profile in cases:
        with pytest.raises(kicad_cli.KiCadCliError):
            run_placement_candidate_drc(
                board.name,
                rejected_candidate,  # type: ignore[arg-type]
                rejected_profile,  # type: ignore[arg-type]
                Settings(workspace=tmp_path),
            )

    unsupported_source = source.replace(b'(layer "F.CrtYd")', b'(layer "F.Fab")', 1)
    unsupported_board = tmp_path / "unsupported.kicad_pcb"
    unsupported_board.write_bytes(unsupported_source)
    conversion = parse_kicad_bytes(unsupported_source, profile)
    assert conversion.snapshot is not None
    unsupported_view = build_placement_view(unsupported_source, conversion.snapshot)
    refs = sorted(unsupported_view.footprints)
    result = evaluate_placement(
        parse_placement_intent(
            {"board": unsupported_board.name, "constraints": CONSTRAINTS, "subjects": refs}
        ),
        conversion.snapshot,
        unsupported_view,
    )
    assert result.candidate is not None
    with pytest.raises(kicad_cli.KiCadCliError, match="serialization"):
        run_placement_candidate_drc(
            unsupported_board.name, result.candidate, profile, Settings(workspace=tmp_path)
        )
    assert calls == 0


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("timeout", "timed out"),
        ("exit", "exit code 3"),
        ("signal", "configured limit"),
        ("missing", "did not create"),
        ("malformed", "valid UTF-8 JSON"),
        ("source", "source does not match"),
        ("oversized", "configured limit"),
    ],
)
def test_fails_closed_on_process_and_report_errors(
    failure: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    capture: dict[str, Any] = {}

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        report_path = Path(command[command.index("--output") + 1])
        capture["temporary_root"] = report_path.parent
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "exit":
            return subprocess.CompletedProcess(command, 3)
        if failure == "signal":
            return subprocess.CompletedProcess(command, -signal.SIGXFSZ)
        if failure == "missing":
            return subprocess.CompletedProcess(command, 0)
        if failure == "malformed":
            report_path.write_bytes(b"not-json")
        elif failure == "source":
            report_path.write_text(json.dumps(_report("wrong.kicad_pcb")), encoding="utf-8")
        else:
            report_path.write_bytes(b"x" * 1025)
        return subprocess.CompletedProcess(command, 0)

    _install_fake_kicad(monkeypatch, run)
    with pytest.raises(kicad_cli.KiCadCliError, match=message):
        run_placement_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=1024),
        )
    assert not capture["temporary_root"].exists()


@pytest.mark.parametrize("mutation", ["source", "rules", "add"])
def test_discards_evidence_when_original_context_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    rules = board.with_suffix(".kicad_dru")
    rules.write_text("(version 1)", encoding="utf-8")
    library = tmp_path / "local.pretty" / "R.kicad_mod"
    library.parent.mkdir()
    library.write_text("(footprint R)", encoding="utf-8")
    (tmp_path / "fp-lib-table").write_text(
        '(fp_lib_table (version 7) (lib (name "Local") (type "KiCad") '
        '(uri "${KIPRJMOD}/local.pretty") (options "") (descr "")))',
        encoding="utf-8",
    )

    def mutate() -> None:
        if mutation == "source":
            board.write_bytes(board.read_bytes() + b"\n")
        elif mutation == "rules":
            rules.write_text("(version 2)", encoding="utf-8")
        else:
            (library.parent / "C.kicad_mod").write_text("(footprint C)", encoding="utf-8")

    _install_fake_kicad(
        monkeypatch,
        _fake_run(
            _report(board.name),
            after_report=lambda command: mutate(),
        ),
    )
    with pytest.raises(kicad_cli.KiCadCliError, match="candidate DRC was running"):
        run_placement_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        )


@pytest.mark.parametrize("private_mutation", ["board", "rules", "library-add"])
def test_rejects_private_candidate_context_mutation(
    private_mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    rules = board.with_suffix(".kicad_dru")
    rules.write_text("(version 1)", encoding="utf-8")

    def mutate_private_context(command: list[str]) -> None:
        snapshot = Path(command[-1])
        snapshot_root = snapshot.parent
        snapshot_root.chmod(0o700)
        if private_mutation == "board":
            snapshot.chmod(0o600)
            snapshot.write_bytes(b"mutated")
            snapshot.chmod(0o400)
        elif private_mutation == "rules":
            snapshot_rules = snapshot.with_suffix(".kicad_dru")
            snapshot_rules.chmod(0o600)
            snapshot_rules.write_text("(version 2)", encoding="utf-8")
            snapshot_rules.chmod(0o400)
        else:
            library = snapshot.parent / "private.pretty" / "R.kicad_mod"
            library.parent.mkdir()
            library.write_text("(footprint R)", encoding="utf-8")
            library.chmod(0o400)
            library.parent.chmod(0o500)
        snapshot_root.chmod(0o500)

    _install_fake_kicad(
        monkeypatch,
        _fake_run(_report(board.name), after_report=mutate_private_context),
    )
    with pytest.raises(kicad_cli.KiCadCliError, match="private KiCad DRC context changed"):
        run_placement_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        )
    assert board.read_bytes() == source


def test_evidence_model_rejects_cross_field_tampering() -> None:
    patched = "sha256:" + "1" * 64
    context = "sha256:" + "2" * 64
    summary = DrcSummary(
        base_revision=patched,
        drc_context_revision=context,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=0,
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts={},
        passed=True,
    )
    evidence = PlacementCandidateDrcEvidence(
        candidate_id="sha256:" + "3" * 64,
        candidate_base_revision="sha256:" + "4" * 64,
        source_revision="sha256:" + "5" * 64,
        patched_board_revision=patched,
        patched_drc_context_revision=context,
        summary=summary,
    )
    with pytest.raises(ValueError, match="sha256"):
        replace(evidence, candidate_id="not-a-digest")
    with pytest.raises(ValueError, match="patched board revision"):
        replace(evidence, summary=replace(summary, base_revision="sha256:" + "6" * 64))
    with pytest.raises(ValueError, match="patched context revision"):
        replace(evidence, summary=replace(summary, drc_context_revision="sha256:" + "7" * 64))


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_accepts_private_placement_candidate_without_workspace_mutation(
    tmp_path: Path,
) -> None:
    source, _, profile, candidate = _candidate()
    board = tmp_path / FIXTURE.name
    board.write_bytes(source)
    before_stat = board.stat()

    evidence = run_placement_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI),
    )

    after_stat = board.stat()
    assert evidence.summary.passed
    assert evidence.summary.error_count == 0
    assert evidence.summary.unconnected_count == 0
    assert evidence.summary.kicad_version.startswith("10.")
    assert board.read_bytes() == source
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
