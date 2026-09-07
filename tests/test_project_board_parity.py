"""Native project parity and closed liveness controls, never full DRC or physics approval."""

import os
from pathlib import Path

import pytest
from project_parity_fixtures import assigned_project, digest

from copper_mcp.config import Settings
from copper_mcp.engineering.project_board_parity import (
    ProjectBoardParityError,
    _validate_parity_diagnostics,
    run_project_board_parity,
)
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    capture_schematic_project,
)

CONFIGURED_CLI = os.environ.get("COPPER_MCP_TEST_PROJECT_ERC_CLI")
CLI = Path(CONFIGURED_CLI) if CONFIGURED_CLI else None


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
@pytest.mark.parametrize("case", ("matching", "net-mismatch"))
def test_real_project_candidate_parity_preserves_all_source_bytes(tmp_path, case):
    capture, libraries, files, boards = assigned_project(tmp_path)
    board = boards[case]
    workspace_board = tmp_path / Path(capture.root_path).with_suffix(".kicad_pcb")
    workspace_board.write_bytes(b"unrelated live board must never be read")
    settings = Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=60)
    report = run_project_board_parity(capture, libraries, board, digest(board), settings)
    assert report.status == ("pass" if case == "matching" else "fail")
    assert report.samples[0] == report.samples[1]
    assert dict(report.samples[0].parity_type_counts) == (
        {} if case == "matching" else {"net_conflict": 1}
    )
    assert report.board_revision == digest(board)
    assert report.capture_digest == capture.digest
    assert report.document()["drc_validation"] == "inconclusive"
    assert report.document()["apply_authority"] == "none"
    assert "intent_digest" not in report.document()
    assert all((tmp_path / name).read_bytes() == content for name, content in files.items())
    assert workspace_board.read_bytes() == b"unrelated live board must never be read"
    if case == "matching":
        assert (
            run_project_board_parity(capture, libraries, board, digest(board), settings).digest
            == report.digest
        )


@pytest.mark.real_kicad
@pytest.mark.skipif(CLI is None, reason="requires explicit vendor-sealed KiCad 10.0.5 backend")
def test_malformed_child_source_refuses_before_board_parity(tmp_path, monkeypatch):
    from copper_mcp.engineering import kicad_project_execution as execution

    capture, libraries, files, boards = assigned_project(tmp_path)
    files["child.kicad_sch"] = (
        files["child.kicad_sch"].rstrip()[:-1] + b'(unsupported_native_token "x"))'
    )
    (tmp_path / "child.kicad_sch").write_bytes(files["child.kicad_sch"])
    capture = capture_schematic_project(
        tmp_path,
        capture.root_path,
        tuple(ProjectFileBinding(name, digest(data)) for name, data in files.items()),
    )
    invoke = execution._invoke

    def no_parity(command, **kwargs):
        assert "--schematic-parity" not in command
        return invoke(command, **kwargs)

    monkeypatch.setattr(execution, "_invoke", no_parity)
    with pytest.raises(ProjectBoardParityError):
        run_project_board_parity(
            capture,
            libraries,
            boards["matching"],
            digest(boards["matching"]),
            Settings(workspace=tmp_path, kicad_cli=CLI, kicad_timeout_seconds=60),
        )


@pytest.mark.parametrize("fault", ("missing", "extra", "wrong-count", "wrong-path", "oversized"))
def test_diagnostics_require_the_exact_live_native_summary(tmp_path, fault):
    output = tmp_path / "parity.json"
    payload = (
        "Found 2 violations\nFound 1 unconnected items\nFound 0 schematic parity issues\n"
        f"Saved DRC Report to {output}\n"
    ).encode()
    assert _validate_parity_diagnostics(payload, output, (2, 1, 0)) is None
    if fault == "missing":
        payload = payload.replace(b"Found 0 schematic parity issues\n", b"")
    elif fault == "extra":
        payload += b"unexpected diagnostic\n"
    elif fault == "wrong-count":
        payload = payload.replace(b"Found 0 schematic", b"Found 1 schematic")
    elif fault == "wrong-path":
        payload = payload.replace(str(output).encode(), b"other.json")
    else:
        payload += b"x" * (64 * 1024)
    with pytest.raises(ProjectBoardParityError):
        _validate_parity_diagnostics(payload, output, (2, 1, 0))


@pytest.mark.parametrize("candidate", (b"", "not-bytes", b"different"))
def test_bad_candidate_identity_refuses_before_native_work(tmp_path, monkeypatch, candidate):
    from copper_mcp.engineering import project_board_parity

    capture, libraries, _files, boards = assigned_project(tmp_path)
    monkeypatch.setattr(
        project_board_parity.execution,
        "open_project_execution_context",
        lambda *_args: pytest.fail("must not invoke native work"),
    )
    with pytest.raises(ProjectBoardParityError):
        run_project_board_parity(
            capture, libraries, candidate, digest(boards["matching"]), Settings(workspace=tmp_path)
        )


def test_expired_request_refuses_before_hashing_candidate(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_board_parity

    capture, libraries, _files, boards = assigned_project(tmp_path)
    monkeypatch.setattr(project_board_parity.time, "monotonic", lambda: 2.0)
    monkeypatch.setattr(project_board_parity, "_sha", lambda *_args: pytest.fail("expired hashing"))
    with pytest.raises(ProjectBoardParityError):
        run_project_board_parity(
            capture,
            libraries,
            boards["matching"],
            digest(boards["matching"]),
            Settings(workspace=tmp_path),
            deadline=1.0,
        )


def test_candidate_hash_stops_between_bounded_chunks(monkeypatch):
    from copper_mcp.engineering import project_board_parity

    payload = b"x" * (128 * 1024)
    clock = [0.0]
    sha256 = project_board_parity.hashlib.sha256
    monkeypatch.setattr(project_board_parity.time, "monotonic", lambda: clock[0])
    assert project_board_parity._sha(payload, 1.0) == "sha256:" + sha256(payload).hexdigest()
    consumed = []

    class ExpiringHash:
        def __init__(self):
            self.hash = sha256()

        def update(self, data):
            consumed.append(len(data))
            self.hash.update(data)
            clock[0] = 2.0

        def hexdigest(self):
            return self.hash.hexdigest()

    monkeypatch.setattr(project_board_parity.hashlib, "sha256", ExpiringHash)
    with pytest.raises(ProjectBoardParityError, match="deadline expired"):
        project_board_parity._sha(payload, 1.0)
    assert consumed == [64 * 1024]


def test_derivative_file_ceiling_is_checked_before_native_context(tmp_path, monkeypatch):
    from copper_mcp.engineering import project_board_parity

    capture, libraries, files, boards = assigned_project(tmp_path)
    ceiling = max(len(boards["matching"]), *(len(value) for value in files.values())) + 1024
    monkeypatch.setattr(project_board_parity, "_parity_project", lambda *_a: b"{}" + b" " * ceiling)
    monkeypatch.setattr(
        project_board_parity.execution,
        "open_project_execution_context",
        lambda *_a: pytest.fail("oversized derivative reached native context"),
    )
    with pytest.raises(ProjectBoardParityError):
        run_project_board_parity(
            capture,
            libraries,
            boards["matching"],
            digest(boards["matching"]),
            Settings(workspace=tmp_path, max_board_bytes=ceiling),
        )
