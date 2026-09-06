from __future__ import annotations

import hashlib
import os
import traceback
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.schematic_project_capture import (
    ProjectFileBinding,
    SchematicProjectCaptureError,
    capture_schematic_project,
)

ROOT_UUID = "00000000-0000-0000-0000-000000000001"
CHILD_UUID = "00000000-0000-0000-0000-000000000002"
SHEET_UUID = "00000000-0000-0000-0000-00000000000a"


def _digest(content: bytes) -> str:
    return "sha256:" + hashlib.sha256(content).hexdigest()


def _source(root_uuid: str, *children: str) -> bytes:
    sheets = "".join(
        f'''(sheet (uuid 00000000-0000-0000-0000-{index:012x})
            (property "Sheetname" "Child")
            (property "Sheetfile" "{child}"))'''
        for index, child in enumerate(children, start=10)
    )
    return (
        f'(kicad_sch (version 20250114) (generator "test") (uuid {root_uuid}) {sheets})'
    ).encode()


def _bindings(*items: tuple[str, bytes]) -> tuple[ProjectFileBinding, ...]:
    return tuple(ProjectFileBinding(path, _digest(content)) for path, content in items)


def _project(tmp_path: Path) -> tuple[bytes, bytes, bytes, tuple[ProjectFileBinding, ...]]:
    root = _source(ROOT_UUID, "child.kicad_sch")
    child = _source(CHILD_UUID)
    project = b'{"text_variables":{}}'
    for path, content in (
        ("root.kicad_sch", root),
        ("child.kicad_sch", child),
        ("root.kicad_pro", project),
    ):
        (tmp_path / path).write_bytes(content)
    return (
        root,
        child,
        project,
        _bindings(
            ("root.kicad_sch", root),
            ("child.kicad_sch", child),
            ("root.kicad_pro", project),
        ),
    )


def test_captures_root_child_project_and_has_deterministic_identity(tmp_path: Path) -> None:
    root, child, project, bindings = _project(tmp_path)
    first = capture_schematic_project(tmp_path, "root.kicad_sch", bindings)
    second = capture_schematic_project(tmp_path, "root.kicad_sch", tuple(reversed(bindings)))
    assert first.digest == second.digest
    assert first.root_path == "root.kicad_sch"
    assert first.project_path == "root.kicad_pro"
    assert {item.path for item in first._files} == {
        "root.kicad_sch",
        "child.kicad_sch",
        "root.kicad_pro",
    }
    assert {item.content for item in first._files} == {root, child, project}
    assert first.hierarchy.file_edges[0].child_path == "child.kicad_sch"


def test_shared_hierarchy_is_derived_from_captured_bytes(tmp_path: Path) -> None:
    root = _source(ROOT_UUID, "child.kicad_sch", "child.kicad_sch")
    child, project = _source(CHILD_UUID), b"{}"
    for path, content in (
        ("root.kicad_sch", root),
        ("child.kicad_sch", child),
        ("root.kicad_pro", project),
    ):
        (tmp_path / path).write_bytes(content)
    result = capture_schematic_project(
        tmp_path,
        "root.kicad_sch",
        _bindings(
            ("root.kicad_sch", root),
            ("child.kicad_sch", child),
            ("root.kicad_pro", project),
        ),
    )
    child_instances = [
        item for item in result.hierarchy.instance_paths if item.source_path == "child.kicad_sch"
    ]
    assert len(child_instances) == 2


def test_capture_requires_parseable_project_settings_and_passes_variables(tmp_path: Path) -> None:
    root = _source(ROOT_UUID, "${SUBDIR}/child.kicad_sch")
    child = _source(CHILD_UUID)
    project = b'{"text_variables":{"SUBDIR":"nested"}}'
    for path, content in (
        ("root.kicad_sch", root),
        ("nested/child.kicad_sch", child),
        ("root.kicad_pro", project),
    ):
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    result = capture_schematic_project(
        tmp_path,
        "root.kicad_sch",
        _bindings(
            ("root.kicad_sch", root),
            ("nested/child.kicad_sch", child),
            ("root.kicad_pro", project),
        ),
    )
    assert result.hierarchy.file_edges[0].child_path == "nested/child.kicad_sch"


def test_capture_refuses_opaque_project_garbage(tmp_path: Path) -> None:
    root, child, _project_bytes, _bindings_value = _project(tmp_path)
    opaque = b"not JSON and never executed"
    (tmp_path / "root.kicad_pro").write_bytes(opaque)
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(
            tmp_path,
            "root.kicad_sch",
            _bindings(
                ("root.kicad_sch", root),
                ("child.kicad_sch", child),
                ("root.kicad_pro", opaque),
            ),
        )


@pytest.mark.parametrize(
    "paths",
    [
        ("root.kicad_sch", "child.kicad_sch"),
        ("root.kicad_sch", "root.kicad_pro", "extra.kicad_pro"),
        ("root.kicad_sch", "wrong.kicad_pro"),
    ],
)
def test_missing_extra_or_nonmatching_project_refuses_before_io(
    tmp_path: Path, paths: tuple[str, ...]
) -> None:
    content = b"x"
    bindings = tuple(ProjectFileBinding(path, _digest(content)) for path in paths)
    with patch("copper_mcp.engineering.schematic_project_capture.read_workspace_file") as reader:
        with pytest.raises(SchematicProjectCaptureError, match="bindings are malformed"):
            capture_schematic_project(tmp_path, "root.kicad_sch", bindings)
    reader.assert_not_called()


@pytest.mark.parametrize(
    "paths",
    [
        ("A.kicad_sch", "a.kicad_sch", "A.kicad_pro"),
        ("root.kicad_sch", "root.kicad_sch/file.kicad_sch", "root.kicad_pro"),
        ("Root.kicad_sch", "root.kicad_sch/file.kicad_sch", "Root.kicad_pro"),
    ],
)
def test_aliases_and_prefix_conflicts_refuse_before_io(
    tmp_path: Path, paths: tuple[str, ...]
) -> None:
    bindings = tuple(ProjectFileBinding(path, _digest(b"x")) for path in paths)
    with patch("copper_mcp.engineering.schematic_project_capture.read_workspace_file") as reader:
        with pytest.raises(SchematicProjectCaptureError):
            capture_schematic_project(tmp_path, paths[0], bindings)
    reader.assert_not_called()


def test_binding_repr_does_not_disclose_private_paths() -> None:
    binding = ProjectFileBinding("private-project/root.kicad_sch", _digest(b"x"))
    assert "private-project" not in repr(binding)


@pytest.mark.parametrize("kind", ("overflow_deadline", "invalid_limits"))
def test_malformed_copied_bounds_do_not_retain_exception_context(tmp_path, kind):
    _, _, _, bindings = _project(tmp_path)
    options = {"deadline": 10**10_000} if kind == "overflow_deadline" else {}
    if kind == "invalid_limits":
        limits = CaptureLimits()
        object.__setattr__(limits, "max_file_bytes", 0)
        options["limits"] = limits
    with pytest.raises(SchematicProjectCaptureError) as caught:
        capture_schematic_project(tmp_path, "root.kicad_sch", bindings, **options)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def test_project_parser_receives_shared_deadline_and_expiry_is_not_malformed(tmp_path, monkeypatch):
    from copper_mcp.engineering import schematic_project_capture as capture
    from copper_mcp.engineering.project_settings import ProjectSettingsDeadlineError

    _, _, _, bindings = _project(tmp_path)
    now = [0.0]
    monkeypatch.setattr(capture.time, "monotonic", lambda: now[0])

    def expired_parser(_payload, *, deadline):
        assert deadline == 2.0
        now[0] = deadline
        raise ProjectSettingsDeadlineError("KiCad project settings deadline expired")

    monkeypatch.setattr(capture, "extract_project_variables", expired_parser)
    with pytest.raises(SchematicProjectCaptureError, match="deadline expired") as caught:
        capture_schematic_project(tmp_path, "root.kicad_sch", bindings, deadline=2.0)
    assert caught.value.__context__ is None


@pytest.mark.parametrize("kind", ("missing_path", "invalid_utf8"))
def test_capture_error_chain_and_traceback_do_not_expose_private_input(
    tmp_path: Path, kind: str
) -> None:
    marker = "PRIVATE_CAPTURE_SENTINEL"
    if kind == "missing_path":
        root_path = f"{marker}/root.kicad_sch"
        bindings = _bindings((root_path, b"x"), (f"{marker}/root.kicad_pro", b"{}"))
    else:
        root_path = "root.kicad_sch"
        payload = marker.encode() + b"\xff"
        (tmp_path / root_path).write_bytes(payload)
        (tmp_path / "root.kicad_pro").write_bytes(b"{}")
        bindings = _bindings((root_path, payload), ("root.kicad_pro", b"{}"))
    with pytest.raises(SchematicProjectCaptureError) as caught:
        capture_schematic_project(tmp_path, root_path, bindings)
    error = caught.value
    rendered = "".join(traceback.format_exception(type(error), error, error.__traceback__))
    assert marker not in rendered
    assert error.__cause__ is None
    assert error.__context__ is None


@pytest.mark.parametrize(
    ("root_path", "bindings"),
    [
        ("root.kicad_pro", ()),
        ("../root.kicad_sch", ()),
        ("root.kicad_sch", []),
        ("root.kicad_sch", (object(),)),
        (
            "root.kicad_sch",
            (
                ProjectFileBinding("root.kicad_sch", "sha256:" + "A" * 64),
                ProjectFileBinding("root.kicad_pro", "sha256:" + "a" * 64),
            ),
        ),
        (
            "caf\N{LATIN SMALL LETTER E WITH ACUTE}.kicad_sch",
            (
                ProjectFileBinding(
                    "caf\N{LATIN SMALL LETTER E WITH ACUTE}.kicad_sch", "sha256:" + "a" * 64
                ),
                ProjectFileBinding(
                    "cafe\N{COMBINING ACUTE ACCENT}.kicad_sch", "sha256:" + "b" * 64
                ),
                ProjectFileBinding(
                    "caf\N{LATIN SMALL LETTER E WITH ACUTE}.kicad_pro", "sha256:" + "c" * 64
                ),
            ),
        ),
    ],
)
def test_untrusted_bindings_refuse_before_io(
    tmp_path: Path, root_path: object, bindings: object
) -> None:
    with patch("copper_mcp.engineering.schematic_project_capture.read_workspace_file") as reader:
        with pytest.raises(SchematicProjectCaptureError):
            capture_schematic_project(tmp_path, root_path, bindings)  # type: ignore[arg-type]
    reader.assert_not_called()


def test_symlink_fifo_and_digest_mismatch_refuse(tmp_path: Path) -> None:
    root, child, project, bindings = _project(tmp_path)
    (tmp_path / "child.kicad_sch").unlink()
    (tmp_path / "child.kicad_sch").symlink_to(tmp_path / "root.kicad_sch")
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(tmp_path, "root.kicad_sch", bindings)
    (tmp_path / "child.kicad_sch").unlink()
    os.mkfifo(tmp_path / "child.kicad_sch")
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(tmp_path, "root.kicad_sch", bindings)
    (tmp_path / "child.kicad_sch").unlink()
    (tmp_path / "child.kicad_sch").write_bytes(child)
    bad = _bindings(
        ("root.kicad_sch", root),
        ("child.kicad_sch", b"wrong"),
        ("root.kicad_pro", project),
    )
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(tmp_path, "root.kicad_sch", bad)


def test_limits_deadline_and_second_sweep_changes_include_project(tmp_path: Path) -> None:
    root, child, _project_bytes, bindings = _project(tmp_path)
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(
            tmp_path,
            "root.kicad_sch",
            bindings,
            limits=CaptureLimits(max_file_bytes=len(root) - 1),
        )
    with pytest.raises(SchematicProjectCaptureError, match="capture refused"):
        capture_schematic_project(
            tmp_path,
            "root.kicad_sch",
            bindings,
            limits=CaptureLimits(max_total_bytes=len(root) + len(child)),
        )
    with pytest.raises(SchematicProjectCaptureError, match="deadline expired"):
        capture_schematic_project(tmp_path, "root.kicad_sch", bindings, deadline=0)

    from copper_mcp.engineering import schematic_project_capture

    original, calls = schematic_project_capture.read_workspace_file, 0  # type: ignore[attr-defined]

    def changing_read(*args: Any, **kwargs: Any) -> Any:
        nonlocal calls
        result = original(*args, **kwargs)
        calls += 1
        if calls == 3:
            (tmp_path / "root.kicad_pro").write_bytes(b'{"changed":true}')
        return result

    with patch.object(schematic_project_capture, "read_workspace_file", side_effect=changing_read):
        with pytest.raises(SchematicProjectCaptureError, match="changed during capture"):
            capture_schematic_project(tmp_path, "root.kicad_sch", bindings)


def test_result_is_frozen_redacted_and_never_executes_project_bytes(tmp_path: Path) -> None:
    _root, _child, project, bindings = _project(tmp_path)
    result = capture_schematic_project(tmp_path, "root.kicad_sch", bindings)
    with pytest.raises((AttributeError, TypeError)):
        result.root_path = "other.kicad_sch"  # type: ignore[misc]
    assert "root.kicad_sch" not in repr(result)
    assert project.decode() not in repr(result)
