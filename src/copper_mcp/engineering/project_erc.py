"""Bounded real KiCad connectivity ERC over a captured hierarchical project.

Source: https://docs.kicad.org/10.0/en/cli/cli.html#schematic-erc
No Circuit Intent identities, simulation/fabrication pass, save, or apply authority is created.
"""

from __future__ import annotations

import hashlib
import math
import os
import signal
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict, dataclass, replace
from pathlib import Path, PurePosixPath
from typing import BinaryIO, Literal

from copper_mcp.config import Settings
from copper_mcp.engineering.capture import CaptureLimits
from copper_mcp.engineering.erc_profile import (
    BACKEND_VERSION,
    NATIVE_RULE_SEVERITIES,
    OUTSIDE_CONNECTIVITY_SCOPE,
    PROFILE_ID,
)
from copper_mcp.engineering.project_erc_inputs import (
    LIBRARY_ENVIRONMENT_KEY,
    PreparedProjectErc,
    SymbolLibraryInput,
    prepare_project_erc,
)
from copper_mcp.engineering.schematic_project_capture import SchematicProjectCapture
from copper_mcp.kicad_cli import (
    _BOUNDED_EXEC,
    KiCadCliError,
    _candidate_drc_deadline_settings,
    _make_snapshot_read_only,
    _parse_erc_observation,
    _private_kicad_environment,
    _validate_private_kicad_state,
    _validate_snapshot_tree,
    _validated_executable,
    _write_drc_snapshot,
    discover_kicad_cli,
)
from copper_mcp.optimization.contracts import digest_document
from copper_mcp.security import read_workspace_file

_ERC_FLAGS = (
    "sch",
    "erc",
    "--format",
    "json",
    "--units",
    "mm",
    "--severity-all",
    "--exit-code-violations",
)
_EMPTY_TABLES = {
    "sym-lib-table": b"(sym_lib_table (version 7))\n",
    "fp-lib-table": b"(fp_lib_table (version 7))\n",
}
_KNOWN_FINDINGS = frozenset(NATIVE_RULE_SEVERITIES) | {
    "duplicate_pins",
    "generic-warning",
    "generic-error",
}
_APPLE_KICAD_REQUIREMENT = '=anchor apple generic and certificate leaf[subject.OU] = "9FQDHNY6U2"'
# Isolate Fontconfig from host/user configuration; this profile claims no typography evidence.
# https://fontconfig.pages.freedesktop.org/fontconfig/fontconfig-user.html
_FONTCONFIG = (
    b'<fontconfig><reset-dirs/><cachedir prefix="xdg">fontconfig</cachedir></fontconfig>\n'
)


class ProjectErcError(ValueError):
    """Sanitized refusal; no underlying project data is retained as an exception context."""


@dataclass(frozen=True, slots=True)
class ProjectErcSample:
    normalized_report_digest: str
    error_count: int
    warning_count: int
    exclusion_count: int
    ignored_check_keys: tuple[str, ...]
    sheet_count: int
    violation_type_counts: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ProjectConnectivityErcReport:
    capture_digest: str
    execution_digest: str
    profile_digest: str
    executable_digest: str
    command_digest: str
    backend_authentication_digest: str
    native_syntax_digest: str
    native_syntax_file_count: int
    backend_version: str
    samples: tuple[ProjectErcSample, ...]
    rule_changes: tuple[str, ...]
    original_exclusion_count: int
    profile_id: str = PROFILE_ID

    @property
    def status(self) -> Literal["pass", "fail", "inconclusive"]:
        if any(sample.error_count for sample in self.samples):
            return "fail"
        if len(self.samples) != 2 or self.samples[0] != self.samples[1]:
            return "inconclusive"
        sample = self.samples[0]
        if sample.exclusion_count or sample.ignored_check_keys != tuple(
            sorted(OUTSIDE_CONNECTIVITY_SCOPE)
        ):
            return "inconclusive"
        if {"lib_symbol_issues", "lib_symbol_mismatch"} & dict(sample.violation_type_counts).keys():
            return "inconclusive"
        return "pass"

    @property
    def digest(self) -> str:
        return digest_document("copper-mcp/project-connectivity-erc/v1", asdict(self))

    def document(self) -> dict[str, object]:
        return {
            **asdict(self),
            "status": self.status,
            "report_digest": self.digest,
            "outside_scope_checks": sorted(OUTSIDE_CONNECTIVITY_SCOPE),
            "simulation_validation": "not_run",
            "fabrication_validation": "not_run",
            "board_parity": "not_run",
            "symbol_library_parity": "exact-ordered-flat-body/v1",
            "backend_authentication": "apple-sealed-kicad/v1",
            "typography_validation": "not_run",
            "apply_authority": "none",
        }


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


def _check(deadline: float) -> None:
    if time.monotonic() >= deadline:
        raise ProjectErcError("project ERC deadline expired")


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
) -> int:
    active = _candidate_drc_deadline_settings(settings, deadline)
    completed = subprocess.run(  # noqa: S603 - only fixed version/ERC commands reach this helper
        command,
        stdin=subprocess.DEVNULL,
        stdout=stdout,
        stderr=subprocess.DEVNULL,
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


def _verify_snapshot(
    snapshot: Path, prepared: PreparedProjectErc, settings: Settings, deadline: float
) -> None:
    _check(deadline)
    _validate_snapshot_tree(
        snapshot,
        frozenset(name for name, _ in prepared.files),
        _candidate_drc_deadline_settings(settings, deadline),
    )
    for name, original in prepared.files:
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
    # Observe only the validated captured bindings, not unrelated workspace files.
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


def _execute(
    prepared: PreparedProjectErc, settings: Settings, deadline: float
) -> ProjectConnectivityErcReport:
    if os.name != "posix":
        raise ProjectErcError("bounded project ERC is unavailable on this platform")
    executable = discover_kicad_cli(settings)
    python = _validated_executable(Path(sys.executable))
    wrapper = _BOUNDED_EXEC.resolve(strict=True)
    if python is None or not wrapper.is_file():
        raise ProjectErcError("bounded project ERC helper is unavailable")
    executable_digest = _file_sha(executable, deadline)
    with tempfile.TemporaryDirectory(prefix="copper-project-erc-") as directory:
        temporary = Path(directory).resolve()
        temporary.chmod(0o700)
        snapshot = temporary / "input"
        snapshot.mkdir(mode=0o700)
        _write_drc_snapshot(dict(prepared.files), snapshot)
        _make_snapshot_read_only(snapshot)
        state = temporary / "state"
        environment = _private_kicad_environment(state)
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
        base_command = [str(python), "-I", str(wrapper)]
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
        _validate_private_kicad_state(state, _candidate_drc_deadline_settings(settings, deadline))
        _verify_configuration(state, global_tables, deadline)
        # Native 10.0.5 queues child load errors without failing project ERC. Loading each
        # original source as the root makes those parse errors fatal. Save only disposable
        # copies; never feed the upgraded output into the final project analysis.
        # https://gitlab.com/kicad/code/kicad/-/blob/18fb9289ff0efdca53c0352ed81a0973f0a6b58c/eeschema/sch_io/kicad_sexpr/sch_io_kicad_sexpr.cpp
        context = dict(prepared.files)
        project = context[str(PurePosixPath(prepared.root_path).with_suffix(".kicad_pro"))]
        source_names = tuple(name for name in context if name.endswith(".kicad_sch"))
        for source in source_names:
            _check(deadline)
            probe_context = dict(context)
            probe_context[str(PurePosixPath(source).with_suffix(".kicad_pro"))] = project
            if (
                len(probe_context) > settings.max_drc_context_files
                or sum(map(len, probe_context.values())) > settings.max_drc_context_bytes
            ):
                raise ProjectErcError("project ERC syntax context exceeds its budget")
            with tempfile.TemporaryDirectory(prefix="syntax-", dir=temporary) as syntax_directory:
                syntax_root = Path(syntax_directory)
                _write_drc_snapshot(probe_context, syntax_root)
                _make_snapshot_read_only(syntax_root)
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
                _validate_snapshot_tree(
                    syntax_root,
                    frozenset(probe_context),
                    _candidate_drc_deadline_settings(settings, deadline),
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
                _validate_private_kicad_state(
                    state, _candidate_drc_deadline_settings(settings, deadline)
                )
                _verify_configuration(state, global_tables, deadline)
        _verify_snapshot(snapshot, prepared, settings, deadline)
        syntax_digest = digest_document(
            "copper-mcp/native-schematic-syntax/v1",
            {
                "execution_digest": prepared.execution_digest,
                "sources": source_names,
                "command": ("sch", "upgrade", "--force"),
                "project_policy": "strict-root-project-copy-per-source",
            },
        )
        samples = []
        for index in range(2):
            _check(deadline)
            output = temporary / f"erc-{index}.json"
            code = _invoke(
                [
                    *base_command,
                    str(settings.max_drc_report_bytes),
                    str(executable),
                    *_ERC_FLAGS,
                    "--output",
                    str(output),
                    str(snapshot / prepared.root_path),
                ],
                settings=settings,
                environment=environment,
                deadline=deadline,
            )
            _verify_snapshot(snapshot, prepared, settings, deadline)
            _validate_private_kicad_state(
                state, _candidate_drc_deadline_settings(settings, deadline)
            )
            _verify_configuration(state, global_tables, deadline)
            payload = read_workspace_file(
                temporary,
                output.name,
                allowed_suffixes={".json"},
                max_bytes=settings.max_drc_report_bytes,
            ).content
            # Discard only the unpredictable private directory spelling, never design content.
            payload = payload.replace(str(temporary).encode(), b"<private-erc>")
            observation = _parse_erc_observation(
                payload,
                deadline=deadline,
                return_code=code,
                expected_source=PurePosixPath(prepared.root_path).name,
                expected_uuid_paths=prepared.expected_uuid_paths,
                minimum_severities={
                    **dict(prepared.effective_rule_severities),
                    "duplicate_pins": "error",
                    "generic-error": "error",
                    "generic-warning": "warning",
                },
            )
            if observation.kicad_version != version:
                raise ProjectErcError("project ERC report backend binding is invalid")
            if set(observation.violation_type_counts) - _KNOWN_FINDINGS:
                raise ProjectErcError("project ERC report has unknown finding categories")
            samples.append(
                ProjectErcSample(
                    observation.normalized_report_digest,
                    observation.error_count,
                    observation.warning_count,
                    observation.exclusion_count,
                    observation.ignored_check_keys,
                    observation.sheet_count,
                    tuple(sorted(observation.violation_type_counts.items())),
                )
            )
        if _file_sha(executable, deadline) != executable_digest:
            raise ProjectErcError("project ERC backend changed during execution")
        if (
            _authenticate_backend(executable, settings, environment, deadline)
            != authentication_digest
        ):
            raise ProjectErcError("project ERC authenticated backend closure changed")
        _check(deadline)
        command_digest = digest_document(
            "copper-mcp/project-erc-command/v1",
            {
                "executable_digest": executable_digest,
                "flags": _ERC_FLAGS,
                "input": prepared.root_path,
                "output": "<private-report>",
                "repetitions": 2,
                "native_syntax_digest": syntax_digest,
                "fontconfig_digest": "sha256:" + hashlib.sha256(_FONTCONFIG).hexdigest(),
            },
        )
        return ProjectConnectivityErcReport(
            prepared.capture_digest,
            prepared.execution_digest,
            prepared.profile_digest,
            executable_digest,
            command_digest,
            authentication_digest,
            syntax_digest,
            len(source_names),
            version,
            tuple(samples),
            prepared.rule_changes,
            prepared.original_exclusion_count,
        )


def _authenticate_backend(
    executable: Path, settings: Settings, environment: dict[str, str], deadline: float
) -> str:
    """One closed platform authority; other platforms need separately reviewed profiles.

    Recording an arbitrary executable hash is not authorization. Verify both the CLI and
    its complete enclosing vendor-sealed bundle with the fixed operating-system verifier.
    This is not a sandbox against privileged concurrent modification of the operator's host.
    """
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
        active = _candidate_drc_deadline_settings(settings, deadline)
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


def run_project_erc(
    capture: SchematicProjectCapture,
    libraries: tuple[SymbolLibraryInput, ...],
    settings: Settings,
    *,
    deadline: float | None = None,
    limits: CaptureLimits | None = None,
) -> ProjectConnectivityErcReport:
    """Run two captured-input checks with workspace freshness observed at both boundaries.

    These observations are not an atomic workspace snapshot or live-editor mutation authority.
    """
    result = None
    try:
        started = time.monotonic()
        if type(settings) is not Settings:
            raise ProjectErcError("project ERC settings are malformed")
        settings = replace(settings)
        if not isinstance(settings.workspace, Path):
            raise ProjectErcError("project ERC workspace is malformed")
        if settings.kicad_cli is not None and not isinstance(settings.kicad_cli, Path):
            raise ProjectErcError("project ERC configured executable path is malformed")
        for value, maximum in (
            (settings.kicad_timeout_seconds, 3600),
            (settings.max_drc_report_bytes, 64 * 1024 * 1024),
            (settings.max_board_bytes, 128 * 1024 * 1024),
            (settings.max_drc_context_bytes, 512 * 1024 * 1024),
            (settings.max_drc_context_files, 100_000),
            (settings.max_drc_context_scan_seconds, 120),
        ):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ProjectErcError("project ERC settings exceed their profile bounds")
        if deadline is not None and (
            type(deadline) not in (int, float) or not math.isfinite(deadline)
        ):
            raise ProjectErcError("project ERC deadline is malformed")
        active_deadline = min(
            started + settings.kicad_timeout_seconds, deadline if deadline is not None else math.inf
        )
        active_limits = CaptureLimits(max_capture_seconds=30) if limits is None else limits
        prepared = prepare_project_erc(
            capture, libraries, limits=active_limits, deadline=active_deadline
        )
        source_files = tuple((item.path, item.content) for item in capture._files)
        _verify_workspace_source(settings.workspace, source_files, active_deadline)
        completed = _execute(prepared, settings, active_deadline)
        _verify_workspace_source(settings.workspace, source_files, active_deadline)
        result = completed
    except (
        ValueError,
        KiCadCliError,
        OSError,
        OverflowError,
        RuntimeError,
        subprocess.SubprocessError,
    ):
        pass
    if result is None:
        # Raised after the handler so private native/report exceptions are not retained.
        raise ProjectErcError("project ERC could not produce bound connectivity evidence")
    return result
