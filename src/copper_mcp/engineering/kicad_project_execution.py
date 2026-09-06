"""Private, bounded KiCad execution context for captured schematic projects."""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from copper_mcp.config import Settings
from copper_mcp.engineering.erc_profile import BACKEND_VERSION
from copper_mcp.engineering.project_erc_inputs import LIBRARY_ENVIRONMENT_KEY, PreparedProjectErc
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_EMPTY_TABLES = {
    "sym-lib-table": b"(sym_lib_table (version 7))\n",
    "fp-lib-table": b"(fp_lib_table (version 7))\n",
}
_APPLE_KICAD_REQUIREMENT = '=anchor apple generic and certificate leaf[subject.OU] = "9FQDHNY6U2"'
# Isolate Fontconfig from host/user configuration; this profile claims no typography evidence.
# https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html
_FONTCONFIG = (
    b'<fontconfig><reset-dirs/><cachedir prefix="xdg">fontconfig</cachedir></fontconfig>\n'
)
_FONTCONFIG_DIGEST = "sha256:" + hashlib.sha256(_FONTCONFIG).hexdigest()


class ProjectErcError(ValueError):
    """Sanitized refusal; no underlying project data is retained as an exception context."""


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectErcError("project ERC deadline expired")


def _file_sha(path: Path, deadline: float) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        total = 0
        while chunk := stream.read(64 * 1024):
            _check(deadline)
            total += len(chunk)
            if total > 512 * 1024 * 1024:
                raise ProjectErcError("project ERC executable exceeds its profile byte bound")
            digest.update(chunk)
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _verify_configuration(state: Path, expected: dict[Path, bytes], deadline: float) -> None:
    observed = set(state.rglob("*lib-table"))
    if observed != set(expected):
        raise ProjectErcError("project ERC global library state is not the fixed empty closure")
    for path, content in expected.items():
        _check(deadline)
        actual = read_workspace_file(
            state,
            path.relative_to(state).as_posix(),
            allowed_names={path.name},
            max_bytes=len(content),
        ).content
        if actual != content:
            raise ProjectErcError("project ERC global library tables changed")
    if (
        read_workspace_file(
            state, "fonts.conf", allowed_names={"fonts.conf"}, max_bytes=len(_FONTCONFIG)
        ).content
        != _FONTCONFIG
    ):
        raise ProjectErcError("project ERC private font configuration changed")
    _check(deadline)


def _invoke(
    command: list[str],
    *,
    settings: Settings,
    environment: dict[str, str],
    deadline: float,
    stdout: BinaryIO | int = subprocess.DEVNULL,
    stderr: BinaryIO | int = subprocess.DEVNULL,
) -> int:
    from copper_mcp import kicad_cli

    active = kicad_cli._candidate_drc_deadline_settings(settings, deadline)
    completed = subprocess.run(  # noqa: S603 - only fixed reviewed commands reach this helper
        command,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=stderr,
        check=False,
        shell=False,
        timeout=active.kicad_timeout_seconds,
        env=environment,
        cwd=environment["TMPDIR"],
    )
    _check(deadline)
    if completed.returncode == -signal.SIGXFSZ:
        raise ProjectErcError("project ERC output exceeded its bound")
    return completed.returncode


def _verify_files(
    snapshot: Path, files: Mapping[str, bytes], settings: Settings, deadline: float
) -> None:
    from copper_mcp import kicad_cli

    _check(deadline)
    kicad_cli._validate_snapshot_tree(
        snapshot,
        frozenset(files),
        kicad_cli._candidate_drc_deadline_settings(settings, deadline),
    )
    for name, original in files.items():
        _check(deadline)
        observed = read_workspace_file(
            snapshot, name, allowed_names={PurePosixPath(name).name}, max_bytes=len(original)
        ).content
        if observed != original:
            raise ProjectErcError("project ERC input changed during execution")
    _check(deadline)


def _verify_workspace_source(
    workspace: Path, files: tuple[tuple[str, bytes], ...], deadline: float
) -> None:
    """Observe only byte-exact captured source bindings in the live workspace."""

    for name, original in files:
        _check(deadline)
        observed = read_workspace_file(
            workspace,
            name,
            allowed_suffixes=(".kicad_sch", ".kicad_pro"),
            max_bytes=len(original),
        ).content
        _check(deadline)
        if observed != original:
            raise ProjectErcError("project ERC workspace source changed")
    _check(deadline)


def _verify_private_state(
    state: Path,
    global_tables: dict[Path, bytes],
    settings: Settings,
    deadline: float,
) -> None:
    from copper_mcp import kicad_cli

    kicad_cli._validate_private_kicad_state(
        state, kicad_cli._candidate_drc_deadline_settings(settings, deadline)
    )
    _verify_configuration(state, global_tables, deadline)


def _authenticate_backend(
    executable: Path, settings: Settings, environment: dict[str, str], deadline: float
) -> str:
    """Authenticate the fixed Apple KiCad CLI and its complete enclosing bundle."""

    from copper_mcp import kicad_cli

    # A runtime alias keeps mypy checking the verifier body on non-macOS hosts too.
    runtime_platform = sys.platform
    if runtime_platform != "darwin" or len(executable.parents) < 3:
        raise ProjectErcError("project ERC has no authenticated backend profile for this platform")
    bundle = executable.parents[2]
    if (
        bundle.suffix != ".app"
        or executable.relative_to(bundle).as_posix() != "Contents/MacOS/kicad-cli"
    ):
        raise ProjectErcError("project ERC requires a vendor-sealed application bundle")
    for target, identifier, extra in (
        (bundle, "org.kicad.kicad", ["--deep"]),
        (executable, "kicad-cli", []),
    ):
        active = kicad_cli._candidate_drc_deadline_settings(settings, deadline)
        checked = subprocess.run(  # noqa: S603 - fixed system verifier and closed requirements
            [
                "/usr/bin/codesign",
                "--verify",
                "--strict",
                *extra,
                "-R",
                f'{_APPLE_KICAD_REQUIREMENT} and identifier "{identifier}"',
                str(target),
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            check=False,
            shell=False,
            timeout=active.kicad_timeout_seconds,
            cwd=environment["TMPDIR"],
            env=environment,
        )
        _check(deadline)
        if checked.returncode:
            raise ProjectErcError("project ERC backend signature or resource seal is invalid")
    return digest_document(
        "copper-mcp/apple-sealed-kicad/v1",
        {
            "requirement": _APPLE_KICAD_REQUIREMENT,
            "bundle_id": "org.kicad.kicad",
            "cli_id": "kicad-cli",
            "sealed_components": {
                name: _file_sha(bundle / name, deadline)
                for name in (
                    "Contents/_CodeSignature/CodeResources",
                    "Contents/Info.plist",
                    "Contents/MacOS/kicad",
                    "Contents/MacOS/kicad-cli",
                )
            },
        },
    )


@dataclass(frozen=True, slots=True, repr=False)
class ProjectExecutionContext:
    temporary: Path
    snapshot: Path
    environment: dict[str, str]
    executable: Path
    executable_digest: str
    authentication_digest: str
    version: str
    base_command: tuple[str, ...]
    native_syntax_digest: str
    source_names: tuple[str, ...]
    _prepared: PreparedProjectErc = field(repr=False)
    _settings: Settings = field(repr=False)
    _deadline: float = field(repr=False)
    _state: Path = field(repr=False)
    _global_tables: dict[Path, bytes] = field(repr=False)

    def __repr__(self) -> str:
        return "<ProjectExecutionContext redacted>"

    def verify(self) -> None:
        """Revalidate the captured inputs and private process state."""

        _verify_files(self.snapshot, dict(self._prepared.files), self._settings, self._deadline)
        _verify_private_state(
            self._state,
            self._global_tables,
            self._settings,
            self._deadline,
        )


@contextmanager
def open_project_execution_context(
    prepared: PreparedProjectErc, settings: Settings, deadline: float
) -> Iterator[ProjectExecutionContext]:
    """Open one fixed, private KiCad project execution closure."""

    from copper_mcp import kicad_cli

    _check(deadline)
    retained_bytes = sum(len(data) for _, data in prepared.files)
    retained_files = len(prepared.files)
    if (
        retained_bytes > settings.max_drc_context_bytes
        or retained_files > settings.max_drc_context_files
        or any(len(data) > settings.max_board_bytes for _, data in prepared.files)
    ):
        raise ProjectErcError("project execution context exceeds its budget")
    if os.name != "posix":
        raise ProjectErcError("bounded project ERC is unavailable on this platform")
    executable = kicad_cli.discover_kicad_cli(settings)
    python = kicad_cli._validated_executable(Path(sys.executable))
    wrapper = kicad_cli._BOUNDED_EXEC.resolve(strict=True)
    if python is None or not wrapper.is_file():
        raise ProjectErcError("bounded project ERC helper is unavailable")
    executable_digest = _file_sha(executable, deadline)
    with tempfile.TemporaryDirectory(prefix="copper-project-erc-") as directory:
        temporary = Path(directory).resolve()
        temporary.chmod(0o700)
        snapshot = temporary / "input"
        snapshot.mkdir(mode=0o700)
        kicad_cli._write_drc_snapshot(dict(prepared.files), snapshot)
        kicad_cli._make_snapshot_read_only(snapshot)
        state = temporary / "state"
        environment = kicad_cli._private_kicad_environment(state)
        # Do not create unsealed bytecode inside a vendor-authenticated application bundle.
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        (state / "fonts.conf").write_bytes(_FONTCONFIG)
        environment["FONTCONFIG_FILE"] = str(state / "fonts.conf")
        environment["FONTCONFIG_PATH"] = str(state)
        authentication_digest = _authenticate_backend(executable, settings, environment, deadline)
        # Both supported settings-directory forms are empty: no live global library fallback.
        config = Path(environment["KICAD_CONFIG_HOME"])
        global_tables = {}
        for table_root in (config, config / "10.0"):
            table_root.mkdir(exist_ok=True, mode=0o700)
            for name, payload in _EMPTY_TABLES.items():
                table_path = table_root / name
                table_path.write_bytes(payload)
                global_tables[table_path] = payload
        environment[LIBRARY_ENVIRONMENT_KEY] = str(snapshot / prepared.library_directory)
        base_command = (str(python), "-I", str(wrapper))
        version_file = temporary / "version.txt"
        with version_file.open("wb") as version_stream:
            code = _invoke(
                [*base_command, "4096", str(executable), "--version"],
                settings=settings,
                environment=environment,
                deadline=deadline,
                stdout=version_stream,
            )
        version = (
            read_workspace_file(temporary, "version.txt", allowed_suffixes={".txt"}, max_bytes=4096)
            .content.decode()
            .strip()
        )
        if code != 0 or version != BACKEND_VERSION:
            raise ProjectErcError("project ERC backend does not match its reviewed profile")
        _verify_private_state(state, global_tables, settings, deadline)

        # Native 10.0.5 queues child load errors without failing project ERC. Loading each
        # original source as the root makes those parse errors fatal. Save only disposable
        # copies; never feed the upgraded output into the final project analysis.
        context_files = dict(prepared.files)
        project = context_files[str(PurePosixPath(prepared.root_path).with_suffix(".kicad_pro"))]
        source_names = tuple(name for name in context_files if name.endswith(".kicad_sch"))
        for source in source_names:
            _check(deadline)
            probe_context = dict(context_files)
            probe_context[str(PurePosixPath(source).with_suffix(".kicad_pro"))] = project
            if (
                retained_files + len(probe_context) > settings.max_drc_context_files
                or retained_bytes + sum(map(len, probe_context.values()))
                > settings.max_drc_context_bytes
                or any(len(data) > settings.max_board_bytes for data in probe_context.values())
            ):
                raise ProjectErcError("project ERC syntax context exceeds its budget")
            with tempfile.TemporaryDirectory(prefix="syntax-", dir=temporary) as syntax_directory:
                syntax_root = Path(syntax_directory)
                kicad_cli._write_drc_snapshot(probe_context, syntax_root)
                kicad_cli._make_snapshot_read_only(syntax_root)
                # Hold the original private inode: only this source may be resaved, and
                # cleanup never follows a path that the native process could have replaced.
                descriptor = os.open(syntax_root / source, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fchmod(descriptor, 0o600)
                    code = _invoke(
                        [
                            *base_command,
                            str(settings.max_board_bytes),
                            str(executable),
                            "sch",
                            "upgrade",
                            "--force",
                            str(syntax_root / source),
                        ],
                        settings=settings,
                        environment=environment,
                        deadline=deadline,
                    )
                finally:
                    try:
                        os.fchmod(descriptor, 0o400)
                    finally:
                        os.close(descriptor)
                if code != 0:
                    raise ProjectErcError("project ERC source failed native root-file validation")
                kicad_cli._validate_snapshot_tree(
                    syntax_root,
                    frozenset(probe_context),
                    kicad_cli._candidate_drc_deadline_settings(settings, deadline),
                )
                for name, original in probe_context.items():
                    _check(deadline)
                    actual = read_workspace_file(
                        syntax_root,
                        name,
                        allowed_names={PurePosixPath(name).name},
                        max_bytes=settings.max_board_bytes if name == source else len(original),
                    ).content
                    if not actual or (name != source and actual != original):
                        raise ProjectErcError("project ERC syntax probe changed unrelated inputs")
                _verify_private_state(state, global_tables, settings, deadline)
        _verify_files(snapshot, dict(prepared.files), settings, deadline)
        native_syntax_digest = digest_document(
            "copper-mcp/native-schematic-syntax/v1",
            {
                "execution_digest": prepared.execution_digest,
                "sources": source_names,
                "command": ("sch", "upgrade", "--force"),
                "project_policy": "strict-root-project-copy-per-source",
            },
        )
        execution = ProjectExecutionContext(
            temporary,
            snapshot,
            environment,
            executable,
            executable_digest,
            authentication_digest,
            version,
            base_command,
            native_syntax_digest,
            source_names,
            prepared,
            settings,
            deadline,
            state,
            global_tables,
        )
        yield execution
        if _file_sha(executable, deadline) != executable_digest:
            raise ProjectErcError("project ERC backend changed during execution")
        if (
            _authenticate_backend(executable, settings, environment, deadline)
            != authentication_digest
        ):
            raise ProjectErcError("project ERC authenticated backend closure changed")
        execution.verify()
        _check(deadline)


__all__ = [
    "ProjectErcError",
    "ProjectExecutionContext",
    "open_project_execution_context",
]
