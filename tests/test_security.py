from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from copper_mcp.security import (
    WorkspaceViolationError,
    read_workspace_file,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def _make_target_recipes(path: Path, target: str) -> list[str]:
    lines = path.read_text(encoding="utf-8").splitlines()
    target_line = f"{target}:"
    try:
        start = lines.index(target_line) + 1
    except ValueError as error:
        raise AssertionError(f"missing Make target {target!r}") from error

    recipes: list[str] = []
    for line in lines[start:]:
        if line.startswith("\t"):
            recipes.append(line.strip())
        elif line.strip():
            break
    return recipes


def _workflow_named_step_run(path: Path, step_name: str) -> str:
    lines = path.read_text(encoding="utf-8").splitlines()
    marker = f"- name: {step_name}"
    try:
        start = next(index + 1 for index, line in enumerate(lines) if line.strip() == marker)
    except StopIteration as error:
        raise AssertionError(f"missing workflow step {step_name!r}") from error

    for line in lines[start:]:
        stripped = line.strip()
        if stripped.startswith("- name:"):
            break
        if stripped.startswith("run:"):
            return stripped.removeprefix("run:").strip()
    raise AssertionError(f"workflow step {step_name!r} has no run command")


class WorkspaceSecurityTests(unittest.TestCase):
    def test_dependency_audits_resolve_declared_project_graph(self) -> None:
        make_recipes = _make_target_recipes(REPOSITORY_ROOT / "Makefile", "security")
        self.assertIn("$(PYTHON) scripts/check_secrets.py", make_recipes)
        self.assertIn("$(PYTHON) -m pip_audit .", make_recipes)
        self.assertNotIn("$(PYTHON) -m pip_audit", make_recipes)

        workflow_run = _workflow_named_step_run(
            REPOSITORY_ROOT / ".github/workflows/security.yml",
            "Audit Python dependencies",
        )
        self.assertEqual(workflow_run, "python -m pip_audit .")

    def test_descriptor_anchored_read_returns_exact_immutable_snapshot(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            target = root / "nested" / "board.kicad_pcb"
            target.parent.mkdir(parents=True)
            target.write_bytes(b"(kicad_pcb (version 20240108))")

            snapshot = read_workspace_file(
                root,
                "nested/board.kicad_pcb",
                allowed_suffixes={".kicad_pcb"},
                max_bytes=1024,
            )

            self.assertEqual(snapshot.path, target.resolve(strict=True))
            self.assertEqual(snapshot.content, target.read_bytes())

    def test_rejects_parent_directory_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.kicad_pcb"
            outside.write_text("(kicad_pcb)", encoding="utf-8")
            with self.assertRaises(WorkspaceViolationError):
                read_workspace_file(
                    root,
                    "../outside.kicad_pcb",
                    allowed_suffixes={".kicad_pcb"},
                    max_bytes=1024,
                )

    def test_rejects_symlink_escape(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            outside = Path(directory) / "outside.kicad_pcb"
            outside.write_text("(kicad_pcb)", encoding="utf-8")
            link = root / "link.kicad_pcb"
            try:
                link.symlink_to(outside)
            except OSError:
                self.skipTest("symlinks are not available")
            with self.assertRaises(WorkspaceViolationError):
                read_workspace_file(
                    root,
                    "link.kicad_pcb",
                    allowed_suffixes={".kicad_pcb"},
                    max_bytes=1024,
                )

    def test_descriptor_anchored_read_rejects_symlink_components(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "workspace"
            outside = base / "outside"
            root.mkdir()
            outside.mkdir()
            (outside / "board.kicad_pcb").write_bytes(b"private board")
            try:
                (root / "linked").symlink_to(outside, target_is_directory=True)
                (root / "board.kicad_pcb").symlink_to(outside / "board.kicad_pcb")
            except OSError:
                self.skipTest("symlinks are not available")

            for requested_path in ("board.kicad_pcb", "linked/board.kicad_pcb"):
                with self.subTest(path=requested_path):
                    with self.assertRaises(WorkspaceViolationError):
                        read_workspace_file(
                            root,
                            requested_path,
                            allowed_suffixes={".kicad_pcb"},
                            max_bytes=1024,
                        )

    def test_descriptor_anchored_read_rejects_final_component_swap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            base = Path(directory)
            root = base / "workspace"
            root.mkdir()
            target = root / "board.kicad_pcb"
            outside = base / "outside.kicad_pcb"
            target.write_bytes(b"public board")
            outside.write_bytes(b"private board")
            real_open = os.open
            supported_dir_fd = set(os.supports_dir_fd)
            swapped = False

            def swapping_open(
                path: str | bytes | os.PathLike[str] | os.PathLike[bytes],
                flags: int,
                mode: int = 0o777,
                *,
                dir_fd: int | None = None,
            ) -> int:
                nonlocal swapped
                if path == target.name and dir_fd is not None and not swapped:
                    swapped = True
                    target.unlink()
                    target.symlink_to(outside)
                if dir_fd is None:
                    return real_open(path, flags, mode)
                return real_open(path, flags, mode, dir_fd=dir_fd)

            supported_dir_fd.add(swapping_open)
            with patch("copper_mcp.security.os.open", new=swapping_open):
                with patch(
                    "copper_mcp.security.os.supports_dir_fd",
                    new=supported_dir_fd,
                ):
                    with self.assertRaises(WorkspaceViolationError):
                        read_workspace_file(
                            root,
                            target.name,
                            allowed_suffixes={".kicad_pcb"},
                            max_bytes=1024,
                        )
            self.assertTrue(swapped)

    def test_descriptor_anchored_read_rejects_mutation_during_read(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "workspace"
            root.mkdir()
            target = root / "board.kicad_pcb"
            target.write_bytes(b"a" * (128 * 1024))
            target_before = target.stat()
            real_read = os.read
            mutated = False

            def mutating_read(file_descriptor: int, length: int) -> bytes:
                nonlocal mutated
                chunk = real_read(file_descriptor, length)
                if chunk and not mutated:
                    mutated = True
                    target.write_bytes(b"b" * target_before.st_size)
                    os.utime(
                        target,
                        ns=(target_before.st_atime_ns, target_before.st_mtime_ns + 1_000_000),
                    )
                return chunk

            with patch("copper_mcp.security.os.read", new=mutating_read):
                with self.assertRaisesRegex(WorkspaceViolationError, "changed while"):
                    read_workspace_file(
                        root,
                        target.name,
                        allowed_suffixes={".kicad_pcb"},
                        max_bytes=target_before.st_size,
                    )
            self.assertTrue(mutated)


if __name__ == "__main__":
    unittest.main()
