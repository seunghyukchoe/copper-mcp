from __future__ import annotations

import hashlib
import json
import shutil
import signal
import stat
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

import copper_mcp.kicad_cli as kicad_cli
from copper_mcp.adapters import (
    KiCadConstraintProfile,
    net_id_for_name,
    parse_kicad_bytes,
    render_kicad_candidate_board,
)
from copper_mcp.board_ir import NetClass
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    RouteCandidateDrcEvidence,
    run_route_candidate_drc,
)
from copper_mcp.models import DrcSummary
from copper_mcp.routing import (
    AStarRouter,
    RouteCandidate,
    RouteRequest,
    canonical_candidate_bytes,
)

FIXTURE = Path(__file__).parent / "fixtures" / "route-candidate" / "two-pad.kicad_pcb"
_DISCOVERED_KICAD_CLI = shutil.which("kicad-cli")
REAL_KICAD_CLI = (
    Path(_DISCOVERED_KICAD_CLI)
    if _DISCOVERED_KICAD_CLI is not None
    else Path("/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli")
)
EMPTY_DIGEST = f"sha256:{'0' * 64}"


def _profile(*, track_width_nm: int = 250_000) -> KiCadConstraintProfile:
    net_class = NetClass(
        id="class:default",
        name="Default",
        clearance_nm=250_000,
        track_width_nm=track_width_nm,
        via_diameter_nm=800_000,
        via_drill_nm=400_000,
    )
    return KiCadConstraintProfile(
        net_classes=(net_class,),
        default_net_class_id=net_class.id,
    )


def _candidate(source: bytes, profile: KiCadConstraintProfile) -> RouteCandidate:
    conversion = parse_kicad_bytes(source, profile)
    assert conversion.snapshot is not None
    assert conversion.diagnostics == ()
    result = AStarRouter().propose(
        conversion.snapshot,
        RouteRequest(
            board_revision=conversion.snapshot.snapshot_digest,
            net_id=net_id_for_name("AUDIO"),
            layer_id="layer:F.Cu",
            seed=23,
        ),
    )
    assert result.candidate is not None
    assert result.diagnostic is None
    return result.candidate


def _rehash(candidate: RouteCandidate) -> RouteCandidate:
    digest = f"sha256:{hashlib.sha256(canonical_candidate_bytes(candidate)).hexdigest()}"
    return replace(candidate, candidate_id=digest)


def _finding(
    finding_type: str,
    severity: str,
    *,
    description: str = "finding",
    excluded: bool = False,
    items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "type": finding_type,
        "description": description,
        "severity": severity,
        "excluded": excluded,
        "items": items or [],
    }


def _report(
    source: str,
    *,
    schema: str = "https://schemas.kicad.org/drc.v1.json",
    violations: list[dict[str, object]] | None = None,
    unconnected_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "$schema": schema,
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


def _workspace_board(tmp_path: Path) -> tuple[Path, bytes, KiCadConstraintProfile, RouteCandidate]:
    board = tmp_path / FIXTURE.name
    shutil.copy2(FIXTURE, board)
    source = board.read_bytes()
    profile = _profile()
    return board, source, profile, _candidate(source, profile)


def _fake_completed_run(
    report: dict[str, object] | bytes,
    *,
    returncode: int = 0,
    after_report: Callable[[list[str]], None] | None = None,
    capture: dict[str, Any] | None = None,
) -> Callable[..., subprocess.CompletedProcess[str]]:
    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        assert kwargs["shell"] is False
        assert kwargs["stdin"] == subprocess.DEVNULL
        assert kwargs["stdout"] == subprocess.DEVNULL
        assert kwargs["stderr"] == subprocess.DEVNULL
        assert "preexec_fn" not in kwargs
        assert "--save-board" not in command
        assert "--refill-zones" not in command
        assert "--define-var" not in command
        snapshot_board = Path(command[-1])
        report_path = Path(command[command.index("--output") + 1])
        if isinstance(report, bytes):
            report_path.write_bytes(report)
        else:
            report_path.write_text(json.dumps(report), encoding="utf-8")
        if capture is not None:
            capture["command"] = command
            capture["kwargs"] = kwargs
            capture["snapshot_board"] = snapshot_board
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
        kicad_cli,
        "discover_kicad_cli",
        lambda settings: Path("/trusted/kicad-cli"),
    )
    monkeypatch.setattr(subprocess, "run", run)


def _summary(
    board_revision: str,
    context_revision: str,
    counts: dict[str, int] | None = None,
) -> DrcSummary:
    copied_counts = counts or {}
    return DrcSummary(
        base_revision=board_revision,
        drc_context_revision=context_revision,
        kicad_version="10.0.5",
        drc_schema="https://schemas.kicad.org/drc.v1.json",
        coordinate_units="mm",
        error_count=0,
        warning_count=sum(copied_counts.values()),
        exclusion_count=0,
        ignored_check_count=0,
        unconnected_count=0,
        violation_type_counts=copied_counts,
        passed=True,
    )


def test_binds_candidate_source_patched_board_and_context_revisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, source, profile, candidate = _workspace_board(tmp_path)
    rules = board.with_suffix(".kicad_dru")
    rules.write_text("(version 1)", encoding="utf-8")
    settings = Settings(workspace=tmp_path, max_drc_report_bytes=4096)
    snapshot = parse_kicad_bytes(source, profile).snapshot
    assert snapshot is not None
    rendered = render_kicad_candidate_board(source, snapshot, candidate, profile)
    expected_context = kicad_cli._drc_context(board, settings)
    expected_context[board.name] = rendered
    capture: dict[str, Any] = {}
    _install_fake_kicad(
        monkeypatch,
        _fake_completed_run(_report(board.name), capture=capture),
    )

    evidence = run_route_candidate_drc(board.name, candidate, profile, settings)

    source_revision = f"sha256:{hashlib.sha256(source).hexdigest()}"
    patched_revision = f"sha256:{hashlib.sha256(rendered).hexdigest()}"
    assert evidence.candidate_id == candidate.candidate_id
    assert evidence.candidate_base_revision == candidate.base_revision
    assert evidence.source_revision == source_revision
    assert evidence.patched_board_revision == patched_revision
    assert evidence.patched_drc_context_revision == kicad_cli._context_revision(expected_context)
    assert evidence.summary.base_revision == patched_revision
    assert evidence.summary.drc_context_revision == evidence.patched_drc_context_revision
    assert evidence.source_revision != evidence.candidate_base_revision
    assert capture["snapshot_bytes"] == rendered
    assert capture["temporary_mode"] == 0o700
    assert not Path(capture["temporary_root"]).exists()
    assert board.read_bytes() == source

    command = capture["command"]
    assert isinstance(command, list)
    kicad_index = command.index("/trusted/kicad-cli")
    report_path = Path(command[command.index("--output") + 1])
    snapshot_path = Path(command[-1])
    assert command[kicad_index:] == [
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
        str(report_path),
        str(snapshot_path),
    ]
    assert "-I" in command[:kicad_index]
    assert snapshot_path.name == board.name
    assert tmp_path not in snapshot_path.parents
    assert capture["kwargs"]["timeout"] == settings.kicad_timeout_seconds


def test_violation_exit_is_valid_negative_candidate_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, _, profile, candidate = _workspace_board(tmp_path)
    report = _report(
        board.name,
        violations=[
            _finding(
                "clearance",
                "error",
                description="PRIVATE_NET_DESCRIPTION",
                items=[{"description": "NET_SECRET", "uuid": "private-uuid", "pos": [1, 2]}],
            )
        ],
        unconnected_items=[_finding("unconnected_items", "error")],
    )
    _install_fake_kicad(
        monkeypatch,
        _fake_completed_run(report, returncode=5),
    )

    evidence = run_route_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, max_drc_report_bytes=4096),
    )

    assert evidence.summary.error_count == 1
    assert evidence.summary.unconnected_count == 1
    assert not evidence.summary.passed
    serialized = json.dumps(evidence.to_dict())
    assert "PRIVATE_NET_DESCRIPTION" not in serialized
    assert "NET_SECRET" not in serialized
    assert "private-uuid" not in serialized


def test_candidate_evidence_is_deeply_immutable_and_detached() -> None:
    counts = {"z-clearance": 2, "a-unconnected": 1}
    patched_revision = f"sha256:{'1' * 64}"
    context_revision = f"sha256:{'2' * 64}"
    summary = _summary(patched_revision, context_revision, counts)
    evidence = RouteCandidateDrcEvidence(
        candidate_id=f"sha256:{'3' * 64}",
        candidate_base_revision=f"sha256:{'4' * 64}",
        source_revision=f"sha256:{'5' * 64}",
        patched_board_revision=patched_revision,
        patched_drc_context_revision=context_revision,
        summary=summary,
    )

    counts["late-mutation"] = 99
    assert "late-mutation" not in evidence.summary.violation_type_counts
    assert list(evidence.summary.violation_type_counts) == ["a-unconnected", "z-clearance"]
    with pytest.raises(TypeError):
        evidence.summary.violation_type_counts["forbidden"] = 1  # type: ignore[index]

    payload = evidence.to_dict()
    nested = payload["summary"]
    assert isinstance(nested, dict)
    nested_counts = nested["violation_type_counts"]
    assert isinstance(nested_counts, dict)
    nested_counts["detached"] = 1
    assert "detached" not in evidence.summary.violation_type_counts


def test_rejects_stale_tampered_forged_and_malformed_inputs_before_kicad(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, source, profile, candidate = _workspace_board(tmp_path)
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    _install_fake_kicad(monkeypatch, unexpected_run)
    forged = _rehash(
        replace(candidate, candidate_id=EMPTY_DIGEST, router_version="forged-router-v1")
    )
    cases: list[tuple[object, object]] = [
        (replace(candidate, base_revision=EMPTY_DIGEST), profile),
        (replace(candidate, candidate_id=EMPTY_DIGEST), profile),
        (forged, profile),
        (candidate, _profile(track_width_nm=300_000)),
        (object(), profile),
        (candidate, object()),
    ]
    for rejected_candidate, rejected_profile in cases:
        with pytest.raises(KiCadCliError):
            run_route_candidate_drc(
                board.name,
                rejected_candidate,  # type: ignore[arg-type]
                rejected_profile,  # type: ignore[arg-type]
                Settings(workspace=tmp_path),
            )

    board.write_bytes(source.replace(b"(version 20260206)", b"(version 20240108)"))
    with pytest.raises(KiCadCliError, match="supported Board IR"):
        run_route_candidate_drc(board.name, candidate, profile, Settings(workspace=tmp_path))
    assert calls == 0


def test_rechecks_patched_board_and_cumulative_context_budgets(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, source, profile, candidate = _workspace_board(tmp_path)
    calls = 0

    def unexpected_run(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        nonlocal calls
        calls += 1
        return subprocess.CompletedProcess([], 0)

    _install_fake_kicad(monkeypatch, unexpected_run)
    monkeypatch.setattr(
        kicad_cli,
        "render_kicad_candidate_board",
        lambda *args, **kwargs: b"x" * (len(source) + 1),
    )
    with pytest.raises(KiCadCliError, match="configured board limit"):
        run_route_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_board_bytes=len(source)),
        )

    monkeypatch.setattr(
        kicad_cli,
        "render_kicad_candidate_board",
        render_kicad_candidate_board,
    )
    with pytest.raises(KiCadCliError, match="cumulative limit"):
        run_route_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(
                workspace=tmp_path,
                max_board_bytes=1024 * 1024,
                max_drc_context_bytes=len(source) + 8,
            ),
        )
    assert calls == 0


@pytest.mark.parametrize("mutation", ["source", "rules", "library", "add", "remove"])
def test_discards_candidate_evidence_when_original_context_changes(
    mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, _, profile, candidate = _workspace_board(tmp_path)
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
        elif mutation == "library":
            library.write_text("(footprint R changed)", encoding="utf-8")
        elif mutation == "add":
            (library.parent / "C.kicad_mod").write_text("(footprint C)", encoding="utf-8")
        else:
            library.unlink()

    _install_fake_kicad(
        monkeypatch,
        _fake_completed_run(
            _report(board.name),
            after_report=lambda command: mutate(),
        ),
    )

    with pytest.raises(KiCadCliError, match="candidate DRC was running"):
        run_route_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        )


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        ("timeout", "timed out"),
        ("exit", "exit code 3"),
        ("signal", "configured limit"),
        ("missing", "did not create"),
        ("malformed", "valid UTF-8 JSON"),
        ("schema", "schema is unsupported"),
        ("source", "source does not match"),
        ("oversized", "configured limit"),
    ],
)
def test_candidate_path_fails_closed_on_process_and_report_errors(
    failure: str,
    message: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, _, profile, candidate = _workspace_board(tmp_path)
    capture: dict[str, Path] = {}

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
        elif failure == "schema":
            report_path.write_text(
                json.dumps(_report(board.name, schema="https://example.invalid/drc.json")),
                encoding="utf-8",
            )
        elif failure == "source":
            report_path.write_text(json.dumps(_report("wrong.kicad_pcb")), encoding="utf-8")
        else:
            report_path.write_bytes(b"x" * 1025)
        return subprocess.CompletedProcess(command, 0)

    _install_fake_kicad(monkeypatch, run)
    with pytest.raises(KiCadCliError, match=message):
        run_route_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=1024),
        )
    assert not capture["temporary_root"].exists()


@pytest.mark.parametrize("private_mutation", ["board", "rules", "library-add"])
def test_rejects_private_candidate_context_mutation(
    private_mutation: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    board, source, profile, candidate = _workspace_board(tmp_path)
    rules = board.with_suffix(".kicad_dru")
    rules.write_text("(version 1)", encoding="utf-8")
    before_entries = _workspace_entries(tmp_path)

    def mutate_private_context(command: list[str]) -> None:
        snapshot = Path(command[-1])
        snapshot_root = snapshot.parent
        snapshot_root.chmod(0o700)
        if private_mutation == "board":
            snapshot.chmod(0o600)
            snapshot.write_bytes(b"mutated by subprocess")
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
        _fake_completed_run(_report(board.name), after_report=mutate_private_context),
    )

    with pytest.raises(KiCadCliError, match="private KiCad DRC context changed"):
        run_route_candidate_drc(
            board.name,
            candidate,
            profile,
            Settings(workspace=tmp_path, max_drc_report_bytes=4096),
        )
    assert board.read_bytes() == source
    assert rules.read_text(encoding="utf-8") == "(version 1)"
    assert _workspace_entries(tmp_path) == before_entries


def test_evidence_model_rejects_digest_and_cross_field_tampering() -> None:
    patched_revision = f"sha256:{'1' * 64}"
    context_revision = f"sha256:{'2' * 64}"
    evidence = RouteCandidateDrcEvidence(
        candidate_id=f"sha256:{'3' * 64}",
        candidate_base_revision=f"sha256:{'4' * 64}",
        source_revision=f"sha256:{'5' * 64}",
        patched_board_revision=patched_revision,
        patched_drc_context_revision=context_revision,
        summary=_summary(patched_revision, context_revision),
    )

    digest_tampering: tuple[Callable[[], RouteCandidateDrcEvidence], ...] = (
        lambda: replace(evidence, candidate_id="not-a-digest"),
        lambda: replace(evidence, candidate_base_revision="not-a-digest"),
        lambda: replace(evidence, source_revision="not-a-digest"),
        lambda: replace(evidence, patched_board_revision="not-a-digest"),
        lambda: replace(evidence, patched_drc_context_revision="not-a-digest"),
    )
    for tamper in digest_tampering:
        with pytest.raises(ValueError, match="sha256"):
            tamper()
    with pytest.raises(ValueError, match="patched board revision"):
        replace(
            evidence,
            summary=_summary(f"sha256:{'6' * 64}", context_revision),
        )
    with pytest.raises(ValueError, match="patched context revision"):
        replace(
            evidence,
            summary=_summary(patched_revision, f"sha256:{'7' * 64}"),
        )
    with pytest.raises(ValueError, match="aggregate finding counts"):
        replace(
            _summary(patched_revision, context_revision, {"clearance": 1}),
            warning_count=0,
        )


def _workspace_entries(root: Path) -> tuple[str, ...]:
    return tuple(sorted(path.relative_to(root).as_posix() for path in root.rglob("*")))


@pytest.mark.skipif(not REAL_KICAD_CLI.is_file(), reason="KiCad CLI is not installed")
def test_real_kicad_candidate_evidence_is_private_and_read_only(tmp_path: Path) -> None:
    board, source, profile, candidate = _workspace_board(tmp_path)
    before_stat = board.stat()
    before_entries = _workspace_entries(tmp_path)

    evidence = run_route_candidate_drc(
        board.name,
        candidate,
        profile,
        Settings(workspace=tmp_path, kicad_cli=REAL_KICAD_CLI),
    )

    after_stat = board.stat()
    assert evidence.summary.passed
    assert evidence.summary.error_count == 0
    assert evidence.summary.warning_count == 0
    assert evidence.summary.unconnected_count == 0
    assert evidence.summary.kicad_version.startswith("10.")
    assert evidence.source_revision != evidence.patched_board_revision
    assert board.read_bytes() == source
    assert after_stat.st_ino == before_stat.st_ino
    assert after_stat.st_mtime_ns == before_stat.st_mtime_ns
    assert _workspace_entries(tmp_path) == before_entries
