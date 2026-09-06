"""ADR-0136 electrical capture remains private, bounded, and non-authoritative."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from unittest.mock import patch

import pytest

from copper_mcp.engineering.capture import (
    CaptureLimits,
    ElectricalCaptureError,
    capture_electrical_artifacts,
)


def _digest(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _declaration(payload: bytes = b"part,qty\nu1,1\n") -> bytes:
    return json.dumps(
        {
            "schema_version": "electrical-inputs/v1",
            "board_revision": "sha256:" + "a" * 64,
            "snapshot_digest": "sha256:" + "b" * 64,
            "project_context_digest": "sha256:" + "c" * 64,
            "profile_id": "mcu-sensor-v1",
            "source_artifacts": [
                {"artifact_id": "bom-main", "role": "bom", "artifact_digest": _digest(payload)}
            ],
        },
        separators=(",", ":"),
    ).encode()


def _paths(path: str = "inputs/bom.csv") -> bytes:
    return json.dumps(
        {
            "schema_version": "electrical-artifact-paths/v1",
            "artifacts": [{"artifact_id": "bom-main", "path": path}],
        },
        separators=(",", ":"),
    ).encode()


def _paths_document(artifacts: list[dict[str, str]]) -> bytes:
    return json.dumps(
        {"schema_version": "electrical-artifact-paths/v1", "artifacts": artifacts},
        separators=(",", ":"),
    ).encode()


def _write(workspace: Path, payload: bytes = b"part,qty\nu1,1\n") -> Path:
    target = workspace / "inputs" / "bom.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(payload)
    return target


def _two_artifact_declaration(payload: bytes) -> bytes:
    return json.dumps(
        {
            "schema_version": "electrical-inputs/v1",
            "board_revision": "sha256:" + "a" * 64,
            "snapshot_digest": "sha256:" + "b" * 64,
            "project_context_digest": "sha256:" + "c" * 64,
            "profile_id": "mcu-sensor-v1",
            "source_artifacts": [
                {"artifact_id": "bom-a", "role": "bom", "artifact_digest": _digest(payload)},
                {"artifact_id": "bom-b", "role": "bom", "artifact_digest": _digest(payload)},
            ],
        },
        separators=(",", ":"),
    ).encode()


def test_capture_binds_exact_bytes_and_exposes_only_nonclaims(tmp_path: Path) -> None:
    source = b"part,qty\nu1,1\n"
    _write(tmp_path, source)
    result = capture_electrical_artifacts(_declaration(source), _paths(), tmp_path)
    assert result.digest == result.redacted_projection.capture_digest
    assert result.redacted_projection.total_bytes == len(source)
    assert result.redacted_projection.model_execution == "not_run"
    assert result.redacted_projection.apply_authority == "none"
    assert "u1" not in repr(result)
    assert "inputs" not in repr(result)
    assert set(result.redacted_projection.model_dump()) == {
        "declaration_digest",
        "capture_digest",
        "artifact_count",
        "total_bytes",
        "binding_scope",
        "project_capture_complete",
        "semantic_validation",
        "model_execution",
        "apply_authority",
    }


@pytest.mark.parametrize(
    "limit",
    [
        lambda: CaptureLimits(max_file_bytes=True),
        lambda: CaptureLimits(max_total_bytes=True),
        lambda: CaptureLimits(max_capture_seconds=True),
    ],
)
def test_boolean_limits_are_refused(limit) -> None:
    with pytest.raises(ElectricalCaptureError, match="limits are malformed"):
        limit()


def test_tight_limits_allow_exact_bound_and_refuse_overbound_and_empty(tmp_path: Path) -> None:
    exact = b"abc"
    _write(tmp_path, exact)
    limits = CaptureLimits(max_file_bytes=3, max_total_bytes=3, max_capture_seconds=1)
    assert capture_electrical_artifacts(
        _declaration(exact), _paths(), tmp_path, limits=limits
    ).digest
    _write(tmp_path, b"abcd")
    with pytest.raises(ElectricalCaptureError, match="capture refused"):
        capture_electrical_artifacts(_declaration(b"abcd"), _paths(), tmp_path, limits=limits)
    _write(tmp_path, b"")
    with pytest.raises(ElectricalCaptureError, match="capture refused"):
        capture_electrical_artifacts(_declaration(b""), _paths(), tmp_path, limits=limits)


@pytest.mark.parametrize(
    "limits",
    [
        {"max_file_bytes": 32 * 1024 * 1024 + 1},
        {"max_total_bytes": 128 * 1024 * 1024 + 1},
        {"max_capture_seconds": 31},
    ],
)
def test_limits_remain_capped(limits: dict[str, int]) -> None:
    with pytest.raises(ElectricalCaptureError, match="limits are malformed"):
        CaptureLimits(**limits)


@pytest.mark.parametrize(
    "path",
    [
        "../bom.csv",
        "/tmp/bom.csv",
        "~/bom.csv",
        "inputs\\bom.csv",
        "inputs/./bom.csv",
        "inputs/bom\x00.csv",
    ],
)
def test_malformed_paths_are_redacted(tmp_path: Path, path: str) -> None:
    _write(tmp_path)
    with pytest.raises(ElectricalCaptureError, match="paths are malformed"):
        capture_electrical_artifacts(_declaration(), _paths(path), tmp_path)


def test_symlink_and_fifo_are_refused_without_disclosure(tmp_path: Path) -> None:
    target = _write(tmp_path)
    linked = tmp_path / "inputs" / "linked.csv"
    linked.symlink_to(target)
    with pytest.raises(ElectricalCaptureError, match="capture refused"):
        capture_electrical_artifacts(_declaration(), _paths("inputs/linked.csv"), tmp_path)
    fifo = tmp_path / "inputs" / "pipe.csv"
    os.mkfifo(fifo)
    with pytest.raises(ElectricalCaptureError, match="capture refused"):
        capture_electrical_artifacts(_declaration(), _paths("inputs/pipe.csv"), tmp_path)


@pytest.mark.parametrize(
    "artifacts",
    [
        [],
        [
            {"artifact_id": "bom-main", "path": "inputs/bom.csv"},
            {"artifact_id": "other", "path": "inputs/other.csv"},
        ],
        [
            {"artifact_id": "bom-main", "path": "inputs/bom.csv"},
            {"artifact_id": "bom-main", "path": "inputs/other.csv"},
        ],
    ],
)
def test_missing_extra_and_duplicate_bindings_are_refused(
    tmp_path: Path, artifacts: list[dict[str, str]]
) -> None:
    _write(tmp_path)
    with pytest.raises(ElectricalCaptureError):
        capture_electrical_artifacts(_declaration(), _paths_document(artifacts), tmp_path)


def test_duplicate_canonical_paths_are_refused_before_any_read(tmp_path: Path) -> None:
    source = b"abc"
    _write(tmp_path, source)
    bindings = _paths_document(
        [
            {"artifact_id": "bom-a", "path": "inputs/bom.csv"},
            {"artifact_id": "bom-b", "path": "inputs/bom.csv"},
        ]
    )
    with patch("copper_mcp.engineering.capture.read_workspace_file") as reader:
        with pytest.raises(ElectricalCaptureError, match="bindings are malformed"):
            capture_electrical_artifacts(_two_artifact_declaration(source), bindings, tmp_path)
    reader.assert_not_called()


@pytest.mark.parametrize(
    "paths", [("inputs/bom.csv", "inputs/BOM.csv"), ("inputs/café.csv", "inputs/cafe\u0301.csv")]
)
def test_portable_aliases_refuse_before_any_read(tmp_path, paths):
    bindings = _paths_document(
        [{"artifact_id": "bom-a", "path": paths[0]}, {"artifact_id": "bom-b", "path": paths[1]}]
    )
    with patch("copper_mcp.engineering.capture.read_workspace_file") as reader:
        with pytest.raises(ElectricalCaptureError, match="bindings are malformed"):
            capture_electrical_artifacts(_two_artifact_declaration(b"abc"), bindings, tmp_path)
    reader.assert_not_called()


@pytest.mark.parametrize("character", ["\u0085", "\u009f", "\ud800"])
def test_unicode_controls_and_surrogates_refuse_before_io(tmp_path, character):
    with patch("copper_mcp.engineering.capture.read_workspace_file") as reader:
        with pytest.raises(ElectricalCaptureError):
            capture_electrical_artifacts(
                _declaration(), _paths(f"inputs/bom{character}.csv"), tmp_path
            )
    reader.assert_not_called()


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("max_file_bytes", True),
        ("max_total_bytes", 128 * 1024 * 1024 + 1),
        ("max_capture_seconds", 31),
    ],
)
def test_mutated_limit_instances_are_revalidated_before_io(tmp_path, field, value):
    limits = CaptureLimits()
    object.__setattr__(limits, field, value)
    with patch("copper_mcp.engineering.capture.read_workspace_file") as reader:
        with pytest.raises(ElectricalCaptureError, match="limits are malformed"):
            capture_electrical_artifacts(_declaration(), _paths(), tmp_path, limits=limits)
    reader.assert_not_called()


def test_caller_cannot_increase_total_limit_during_capture(tmp_path):
    from copper_mcp.engineering import capture

    (tmp_path / "inputs").mkdir()
    for name in ("a.csv", "b.csv"):
        (tmp_path / "inputs" / name).write_bytes(b"abc")
    limits = CaptureLimits(max_file_bytes=3, max_total_bytes=5)
    bindings = _paths_document(
        [
            {"artifact_id": "bom-a", "path": "inputs/a.csv"},
            {"artifact_id": "bom-b", "path": "inputs/b.csv"},
        ]
    )
    original = capture.read_workspace_file
    budgets = []

    def changing_limits(*args, **kwargs):
        budgets.append(kwargs["max_bytes"])
        result = original(*args, **kwargs)
        object.__setattr__(limits, "max_total_bytes", 6)
        return result

    with patch.object(capture, "read_workspace_file", side_effect=changing_limits):
        with pytest.raises(ElectricalCaptureError, match="capture refused"):
            capture_electrical_artifacts(
                _two_artifact_declaration(b"abc"), bindings, tmp_path, limits=limits
            )
    assert budgets == [3, 2]


def test_digest_mismatch_and_aggregate_budget_are_refused(tmp_path: Path) -> None:
    source = b"part,qty\nu1,1\n"
    _write(tmp_path, source)
    with pytest.raises(ElectricalCaptureError, match="digest does not match"):
        capture_electrical_artifacts(_declaration(b"other"), _paths(), tmp_path)
    large = b"x" * (17 * 1024 * 1024)
    (tmp_path / "inputs" / "a.csv").write_bytes(large)
    (tmp_path / "inputs" / "b.csv").write_bytes(large)
    with pytest.raises(ElectricalCaptureError, match="capture refused"):
        capture_electrical_artifacts(
            _two_artifact_declaration(large),
            _paths_document(
                [
                    {"artifact_id": "bom-a", "path": "inputs/a.csv"},
                    {"artifact_id": "bom-b", "path": "inputs/b.csv"},
                ]
            ),
            tmp_path,
            limits=CaptureLimits(max_file_bytes=32 * 1024 * 1024),
        )


def test_second_read_refuses_file_changed_after_earlier_capture(tmp_path: Path) -> None:
    source = b"part,qty\nu1,1\n"
    target = _write(tmp_path, source)
    from copper_mcp.engineering import capture

    original = capture.read_workspace_file
    calls = 0

    def changing_read(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 1:
            target.write_bytes(b"part,qty\nu2,1\n")
        return result

    with patch("copper_mcp.engineering.capture.read_workspace_file", side_effect=changing_read):
        with pytest.raises(ElectricalCaptureError, match="changed during capture"):
            capture_electrical_artifacts(_declaration(source), _paths(), tmp_path)


def test_second_sweep_refuses_an_earlier_file_changed_while_later_file_is_first_read(
    tmp_path: Path,
) -> None:
    source = b"abc"
    first = tmp_path / "inputs" / "a.csv"
    first.parent.mkdir()
    first.write_bytes(source)
    (tmp_path / "inputs" / "b.csv").write_bytes(source)
    bindings = _paths_document(
        [
            {"artifact_id": "bom-a", "path": "inputs/a.csv"},
            {"artifact_id": "bom-b", "path": "inputs/b.csv"},
        ]
    )
    from copper_mcp.engineering import capture

    original = capture.read_workspace_file
    calls = 0

    def changing_read(*args, **kwargs):
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 2:
            first.write_bytes(b"def")
        return result

    with patch("copper_mcp.engineering.capture.read_workspace_file", side_effect=changing_read):
        with pytest.raises(ElectricalCaptureError, match="changed during capture"):
            capture_electrical_artifacts(_two_artifact_declaration(source), bindings, tmp_path)


def test_remaining_budget_is_passed_to_reader_and_stops_before_next_io(tmp_path: Path) -> None:
    source = b"abc"
    (tmp_path / "inputs").mkdir()
    (tmp_path / "inputs" / "a.csv").write_bytes(source)
    (tmp_path / "inputs" / "b.csv").write_bytes(source)
    bindings = _paths_document(
        [
            {"artifact_id": "bom-a", "path": "inputs/a.csv"},
            {"artifact_id": "bom-b", "path": "inputs/b.csv"},
        ]
    )
    from copper_mcp.engineering import capture

    original = capture.read_workspace_file
    with patch("copper_mcp.engineering.capture.read_workspace_file", wraps=original) as reader:
        with pytest.raises(ElectricalCaptureError, match="capture refused"):
            capture_electrical_artifacts(
                _two_artifact_declaration(source),
                bindings,
                tmp_path,
                limits=CaptureLimits(max_file_bytes=9, max_total_bytes=3, max_capture_seconds=1),
            )
    assert reader.call_count == 1
    assert reader.call_args.kwargs["max_bytes"] == 3


@pytest.mark.parametrize(
    "expired_at",
    [4, 5, 12],
)
def test_deadlines_are_checked_at_read_hash_and_before_return(
    tmp_path: Path, expired_at: int
) -> None:
    source = b"part,qty\nu1,1\n"
    _write(tmp_path, source)
    with patch(
        "copper_mcp.engineering.capture.time.monotonic",
        side_effect=[0.0] * (expired_at - 1) + [6.0],
    ):
        with pytest.raises(ElectricalCaptureError, match="deadline expired"):
            capture_electrical_artifacts(_declaration(source), _paths(), tmp_path)


@pytest.mark.parametrize(
    "value", [True, "5", float("nan"), float("inf"), float("-inf"), 10**1000, -(10**1000)]
)
def test_malformed_deadlines_are_redacted(tmp_path: Path, value: object) -> None:
    _write(tmp_path)
    with pytest.raises(ElectricalCaptureError, match="deadline is malformed"):
        capture_electrical_artifacts(_declaration(), _paths(), tmp_path, deadline=value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "declaration,paths",
    [(None, _paths()), (_declaration(), None), ([], _paths()), (_declaration(), [])],
)
def test_non_bytes_payloads_are_fixed_redacted_refusals(
    tmp_path: Path, declaration: object, paths: object
) -> None:
    _write(tmp_path)
    with pytest.raises(ElectricalCaptureError) as error:
        capture_electrical_artifacts(declaration, paths, tmp_path)  # type: ignore[arg-type]
    assert "inputs" not in str(error.value)


def test_malicious_model_content_is_captured_as_data_without_execution(tmp_path: Path) -> None:
    content = b".include /private/never-run\n.end\n"
    target = tmp_path / "inputs" / "model.sp"
    target.parent.mkdir()
    target.write_bytes(content)
    declaration = json.dumps(
        {
            "schema_version": "electrical-inputs/v1",
            "board_revision": "sha256:" + "a" * 64,
            "snapshot_digest": "sha256:" + "b" * 64,
            "project_context_digest": "sha256:" + "c" * 64,
            "profile_id": "mcu-sensor-v1",
            "source_artifacts": [
                {
                    "artifact_id": "model-main",
                    "role": "model-library",
                    "artifact_digest": _digest(content),
                }
            ],
        },
        separators=(",", ":"),
    ).encode()
    result = capture_electrical_artifacts(
        declaration,
        _paths_document([{"artifact_id": "model-main", "path": "inputs/model.sp"}]),
        tmp_path,
    )
    assert result.redacted_projection.model_execution == "not_run"


def test_inputs_remain_unchanged_and_identity_is_deterministic(tmp_path: Path) -> None:
    source = b"part,qty\nu1,1\n"
    _write(tmp_path, source)
    declaration = _declaration(source)
    paths = _paths()
    first = capture_electrical_artifacts(declaration, paths, tmp_path)
    second = capture_electrical_artifacts(declaration, paths, tmp_path)
    assert declaration == _declaration(source)
    assert paths == _paths()
    assert first.digest == second.digest
