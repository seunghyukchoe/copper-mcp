"""Primary-owned boundary regressions; no native process is launched."""

from pathlib import Path

import pytest
from kicad_drc_mock import make_fake_kicad_cli
from test_kicad_drc_rule_liveness import _build, _context, _summary

from copper_mcp import kicad_cli
from copper_mcp import kicad_drc_rule_liveness as rules
from copper_mcp.config import Settings


def _settings(tmp_path: Path) -> Settings:
    return Settings(workspace=tmp_path, kicad_cli=make_fake_kicad_cli(tmp_path))


@pytest.mark.parametrize("extension", [".KICAD_PCB", ".KiCaD_PcB"])
def test_no_rule_fast_path_preserves_case_insensitive_board_suffixes(
    tmp_path: Path, monkeypatch, extension: str
):
    board_name = "board" + extension
    context = {board_name: b"(kicad_pcb (version 20240108))"}
    expected = _summary(kicad_cli._context_revision(context))

    def run_pass(payload, **kwargs):
        assert kwargs["board_relative"] == board_name
        payload.clear()
        return expected

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    assert (
        kicad_cli._run_captured_drc(
            context, board_relative=board_name, settings=_settings(tmp_path)
        )
        == expected
    )
    assert context == {}


@pytest.mark.parametrize("extension", [".KICAD_PCB", ".KiCaD_PcB"])
def test_rule_liveness_path_preserves_case_insensitive_board_suffixes(
    tmp_path: Path, monkeypatch, extension: str
):
    board_name = "board" + extension
    context = {
        board_name: b"(kicad_pcb (version 20240108))",
        "board.kicad_dru": b"(version 1)\n",
    }
    expected = _summary(kicad_cli._context_revision(context))
    calls = []

    def run_pass(payload, **kwargs):
        assert kwargs["board_relative"] == board_name
        calls.append(kwargs.get("witness"))
        payload.clear()
        return expected

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    assert (
        kicad_cli._run_captured_drc(
            context, board_relative=board_name, settings=_settings(tmp_path)
        )
        == expected
    )
    assert calls[0] is not None and calls[1] is None


def test_final_private_tree_check_cannot_outlive_shared_deadline(tmp_path: Path, monkeypatch):
    clock = [0.0]
    checks = []
    actual_check = kicad_cli._validate_snapshot_tree

    def check_tree(*args):
        actual_check(*args)
        checks.append(True)
        if len(checks) == 3:
            clock[0] = 11.0

    def run_pass(payload, **kwargs):
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        return _summary(revision)

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli, "_validate_snapshot_tree", check_tree)
    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline"):
        kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path),
            deadline=10.0,
        )
    assert len(checks) == 3


@pytest.mark.parametrize("oversized", ["board", "rules"])
def test_known_projected_oversize_refuses_before_project_serialization(monkeypatch, oversized: str):
    context = _context()
    key = "board.kicad_pcb" if oversized == "board" else "board.kicad_dru"
    context[key] = b" " * 10_000 + context[key]
    maximum = len(context[key])
    monkeypatch.setattr(
        rules,
        "_project_derivative",
        lambda *args: pytest.fail("known oversized derivative reached project serialization"),
    )
    with pytest.raises(rules.KiCadDrcRuleLivenessError):
        _build(context, max_file_bytes=maximum)


@pytest.mark.parametrize(
    ("context", "board_relative"),
    [
        ({"../board.kicad_pcb": b"x"}, "../board.kicad_pcb"),
        ({"/board.kicad_pcb": b"x"}, "/board.kicad_pcb"),
        ({"board//x.kicad_pcb": b"x"}, "board//x.kicad_pcb"),
        (
            {"board.kicad_pcb": b"x", "BOARD.KICAD_PCB": b"y"},
            "board.kicad_pcb",
        ),
        ({"board.kicad_pcb": "not bytes"}, "board.kicad_pcb"),
    ],
)
def test_context_is_admitted_before_hash_or_store_write(
    tmp_path: Path, monkeypatch, context, board_relative
):
    monkeypatch.setattr(
        kicad_cli,
        "_context_revision",
        lambda *_args: pytest.fail("malformed context reached hashing"),
    )
    monkeypatch.setattr(
        kicad_cli,
        "_write_drc_snapshot",
        lambda *_args: pytest.fail("malformed context reached store write"),
    )
    with pytest.raises(kicad_cli.KiCadCliError, match="context is malformed"):
        kicad_cli._run_captured_drc(
            context,
            board_relative=board_relative,
            settings=Settings(workspace=tmp_path),
        )


def test_valid_unicode_and_space_paths_survive_context_admission(tmp_path: Path, monkeypatch):
    board_name = "space dir/보드.KICAD_PCB"
    context = {board_name: b"(kicad_pcb (version 20240108))"}
    expected = _summary(kicad_cli._context_revision(context))

    def run_pass(payload, **_kwargs):
        payload.clear()
        return expected

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    assert (
        kicad_cli._run_captured_drc(
            context, board_relative=board_name, settings=_settings(tmp_path)
        )
        == expected
    )


def test_missing_witness_identity_cannot_match_missing_report_uuid():
    with pytest.raises(rules.KiCadDrcRuleLivenessError):
        witness = rules.RuleLivenessWitness(None, "private")  # type: ignore[arg-type]
        rules.require_rule_liveness_witness(
            b'{"violations":[{"type":"assertion_failure","severity":"error","items":[{}]}]}',
            witness,
            deadline=10**10,
        )


@pytest.mark.parametrize(
    "deadline",
    (
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-infinity"),
        pytest.param(float("-inf"), id="negative-infinity"),
        pytest.param(10**10_000, id="overflowing-integer"),
    ),
)
def test_nonfinite_or_overflowing_deadline_is_a_fixed_refusal(tmp_path: Path, deadline: object):
    with pytest.raises(rules.KiCadDrcRuleLivenessError):
        _build(_context(), deadline=deadline)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline is malformed"):
        kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path),
            deadline=deadline,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("failure", ["creation", "store", "restore", "cleanup"])
def test_new_store_restore_and_cleanup_errors_are_context_free(
    tmp_path: Path, monkeypatch, failure: str
):
    private = "PRIVATE-PATH-CONTEXT"

    def run_pass(payload, **_kwargs):
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        return _summary(revision)

    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    if failure == "creation":

        def fail_creation(*_args, **_kwargs):
            raise OSError(private)

        monkeypatch.setattr(kicad_cli.tempfile, "TemporaryDirectory", fail_creation)
        message = "could not be secured"
    elif failure == "store":

        def fail_store(*_args):
            raise OSError(private)

        monkeypatch.setattr(kicad_cli, "_write_drc_snapshot", fail_store)
        message = "could not be secured"
    elif failure == "restore":

        def fail_restore(*_args, **_kwargs):
            raise kicad_cli.KiCadCliError(private)

        monkeypatch.setattr(kicad_cli, "_drc_context", fail_restore)
        message = "source store changed"
    else:
        real_temporary = kicad_cli.tempfile.TemporaryDirectory

        class FailedCleanup:
            def __init__(self, *args, **kwargs):
                self._temporary = real_temporary(*args, **kwargs)
                self.name = self._temporary.name

            def cleanup(self):
                self._temporary.cleanup()
                raise OSError(private)

        monkeypatch.setattr(kicad_cli.tempfile, "TemporaryDirectory", FailedCleanup)
        message = "cleanup failed"

    with pytest.raises(kicad_cli.KiCadCliError, match=message) as error:
        kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path),
        )
    assert private not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_cleanup_completion_is_inside_the_shared_deadline(tmp_path: Path, monkeypatch):
    clock = [0.0]
    cleaned = []
    real_temporary = kicad_cli.tempfile.TemporaryDirectory

    class DeadlineCleanup:
        def __init__(self, *args, **kwargs):
            self._temporary = real_temporary(*args, **kwargs)
            self.name = self._temporary.name

        def cleanup(self):
            self._temporary.cleanup()
            cleaned.append(True)
            clock[0] = 11.0

    def run_pass(payload, **_kwargs):
        revision = kicad_cli._context_revision(payload)
        payload.clear()
        return _summary(revision)

    monkeypatch.setattr(kicad_cli.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(kicad_cli.tempfile, "TemporaryDirectory", DeadlineCleanup)
    monkeypatch.setattr(kicad_cli, "_run_captured_drc_pass", run_pass)
    with pytest.raises(kicad_cli.KiCadCliError, match="deadline"):
        kicad_cli._run_captured_drc(
            _context(),
            board_relative="board.kicad_pcb",
            settings=_settings(tmp_path),
            deadline=10.0,
        )
    assert cleaned == [True]


@pytest.mark.parametrize(
    "name",
    [
        pytest.param("bad\x00name", id="nul"),
        pytest.param("x" * 4097, id="oversized-path"),
        pytest.param("bad\ud800name", id="invalid-utf8"),
    ],
)
def test_invalid_path_text_is_refused_before_context_hash(tmp_path: Path, monkeypatch, name):
    context = _context()
    context[name] = b""
    monkeypatch.setattr(
        kicad_cli,
        "_context_revision",
        lambda *_args: pytest.fail("unadmitted path reached context hashing"),
    )
    with pytest.raises(kicad_cli.KiCadCliError):
        kicad_cli._run_captured_drc(
            context, board_relative="board.kicad_pcb", settings=_settings(tmp_path)
        )


def test_new_original_store_validation_error_has_no_private_cause(tmp_path: Path, monkeypatch):
    def fail_validation(*_args):
        try:
            raise OSError("PRIVATE original store path")
        except OSError as error:
            raise kicad_cli.KiCadCliError("private tree could not be inspected") from error

    monkeypatch.setattr(kicad_cli, "_validate_snapshot_tree", fail_validation)
    with pytest.raises(kicad_cli.KiCadCliError) as error:
        kicad_cli._run_captured_drc(
            _context(), board_relative="board.kicad_pcb", settings=_settings(tmp_path)
        )
    assert error.value.__cause__ is None and error.value.__context__ is None
