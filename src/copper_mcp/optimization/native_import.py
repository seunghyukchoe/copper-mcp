"""Bounded native board import consumed by v2 preparation, child execution, and review."""

from __future__ import annotations

import hashlib
import math
import os
import stat
import subprocess
import sys
import tempfile
import time
import unicodedata
import uuid
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from copper_mcp import kicad_cli
from copper_mcp.adapters.cst import Splice, apply_splices, span
from copper_mcp.adapters.sexpr import SExpr, atoms, child, children, parse_sexpr
from copper_mcp.config import Settings
from copper_mcp.kicad_drc_rule_liveness import admit_rule_liveness_context
from copper_mcp.optimization.contracts import (
    BackendVersion,
    ClosedModel,
    Digest,
    OptimizationError,
    digest_document,
)
from copper_mcp.optimization.contracts import Counter as Count
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.security import read_workspace_file

_METHOD: Literal["native-mandatory-field-uuid5/v1"] = "native-mandatory-field-uuid5/v1"
_DISCARD_POLICY: Literal["same-stem-private-kicad-prl/v1"] = "same-stem-private-kicad-prl/v1"
_NAMESPACE = uuid.UUID("ad870f80-cd5c-5b8f-a51d-75a4612f3d82")


class NativeImportError(OptimizationError):
    """Fixed refusal without a source path, diagnostics, or a native traceback."""


class NativeImportBinding(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v2/native-import"
    original_board_revision: Digest
    imported_board_revision: Digest
    original_context_digest: Digest
    executable_digest: Digest
    backend_version: BackendVersion
    command_digest: Digest
    normalization_method: Literal["native-mandatory-field-uuid5/v1"] = _METHOD
    discard_policy: Literal["same-stem-private-kicad-prl/v1"] = _DISCARD_POLICY
    repetitions: Literal[2] = 2
    generated_field_count: Count
    authority: Literal["executable-consistency-only"] = "executable-consistency-only"


@dataclass(frozen=True, slots=True, repr=False)
class ImportedBoard:
    source: bytes
    binding: NativeImportBinding
    raw_output_digests: tuple[str, str]
    output_bytes: int


def _check(deadline: float) -> None:
    if (
        type(deadline) not in (int, float)
        or not -sys.float_info.max <= deadline <= sys.float_info.max
        or not math.isfinite(deadline)
        or time.monotonic() >= deadline
    ):
        raise NativeImportError("native board import deadline expired")


def _sha(payload: bytes, deadline: float) -> str:
    digest = hashlib.sha256()
    for offset in range(0, len(payload), 65536):
        _check(deadline)
        digest.update(memoryview(payload)[offset : offset + 65536])
    _check(deadline)
    return "sha256:" + digest.hexdigest()


def _identity(node: SExpr) -> str:
    fields = children(node, "uuid") + children(node, "tstamp")
    if len(fields) != 1 or len(atoms(fields[0])) != 1:
        raise NativeImportError("native import object identity is malformed")
    return str(uuid.UUID(atoms(fields[0])[0]))


def _identities(root: SExpr, deadline: float) -> dict[str, tuple[str, str | None, str | None]]:
    # KiCad serializes one dimension object's identity both on the dimension and
    # on its single gr_text. Only that immediate, agreeing representation is skipped.
    # Each existing UUID binds to its semantic kind, nearest identified owner and
    # stable pad number/field name. Bare UUID counts cannot detect swapped ownership.
    # Owners are bounded IDs, never concatenated hierarchical paths.
    pending: list[tuple[SExpr, str | None, str | None]] = [(root, None, None)]
    result: dict[str, tuple[str, str | None, str | None]] = {}
    while pending:
        _check(deadline)
        node, owner, parent_dimension = pending.pop()
        fields = children(node, "uuid") + children(node, "tstamp")
        identity = _identity(node) if fields else None
        if parent_dimension is not None:
            if identity is not None and identity != parent_dimension:
                raise NativeImportError("native import dimension identities disagree")
        elif identity is not None:
            kind = node.head
            if kind is None:
                raise NativeImportError("native import object kind is malformed")
            name = None
            if kind in {"footprint", "pad", "property"}:
                if len(node.items) < 2 or not isinstance(node.items[1], str):
                    raise NativeImportError("native import object name is malformed")
                name = str(node.items[1])
                if kind == "property":
                    kind = "field"
            elif kind == "fp_text" and len(node.items) > 1:
                # Legacy reference/value text becomes a modern mandatory property.
                # Both spellings identify the same field under the same footprint.
                if node.items[1] == "reference":
                    kind, name = "field", "Reference"
                elif node.items[1] == "value":
                    kind, name = "field", "Value"
            if identity in result:
                raise NativeImportError("native import object identity is reused")
            result[identity] = (kind, owner, name)
        dimension_text = None
        if node.head == "dimension":
            texts = children(node, "gr_text")
            if len(texts) != 1 or identity is None:
                raise NativeImportError("native import dimension text is ambiguous")
            dimension_text = texts[0]
        for item in node.items:
            _check(deadline)
            if isinstance(item, SExpr) and item.head not in {"uuid", "tstamp"}:
                pending.append(
                    (
                        item,
                        identity if identity is not None else owner,
                        identity if item is dimension_text else None,
                    )
                )
    return result


def normalize_imported_board(
    original: bytes, converted: bytes, settings: Settings, deadline: float
) -> tuple[bytes, int]:
    """Only mint deterministic IDs for unique new Datasheet/Description fields."""
    _check(deadline)
    limits = parse_limits_for(settings)
    if type(original) is not bytes or type(converted) is not bytes:
        raise NativeImportError("native import requires captured bytes")
    source = parse_sexpr(original, limits, check_deadline=lambda: _check(deadline))
    output = parse_sexpr(converted, limits, check_deadline=lambda: _check(deadline))
    source_ids = _identities(source, deadline)
    output_ids = _identities(output, deadline)
    for key, semantic_owner in source_ids.items():
        _check(deadline)
        if output_ids.get(key) != semantic_owner:
            raise NativeImportError("native import changed an existing object identity or owner")
    source_fps = children(source, "footprint")
    old_parents = {_identity(fp) for fp in source_fps}
    output_fps = children(output, "footprint")
    if (
        len(old_parents) != len(source_fps)
        or {_identity(fp) for fp in output_fps} != old_parents
        or len(output_fps) != len(old_parents)
    ):
        raise NativeImportError("native import changed footprint identities")
    original_digest = _sha(original, deadline)
    text = converted.decode("utf-8")
    edits = []
    recognized = set()
    replacements = set()
    for footprint in output_fps:
        _check(deadline)
        parent = _identity(footprint)
        props = children(footprint, "property")
        names = Counter(
            str(prop.items[1])
            for prop in props
            if len(prop.items) > 1 and isinstance(prop.items[1], str)
        )
        for prop in props:
            _check(deadline)
            node = child(prop, "uuid")
            if node is None:
                continue
            identity = _identity(prop)
            if identity in source_ids:
                continue
            if (
                len(prop.items) < 3
                or not isinstance(prop.items[1], str)
                or prop.items[1] not in {"Datasheet", "Description"}
                or names[str(prop.items[1])] != 1
            ):
                raise NativeImportError("native import generated an unsupported identity")
            replacement = str(
                uuid.uuid5(_NAMESPACE, original_digest + ":" + parent + ":" + str(prop.items[1]))
            )
            if replacement in output_ids or replacement in replacements:
                raise NativeImportError("native import generated colliding identities")
            recognized.add(identity)
            replacements.add(replacement)
            start, end = span(node, text)
            edits.append(Splice(start, end, f'(uuid "{replacement}")'))
    if set(output_ids) - set(source_ids) != recognized:
        raise NativeImportError("native import generated an unsupported identity")
    _check(deadline)
    projected = len(converted)
    for edit in edits:
        _check(deadline)
        projected += len(edit.replacement.encode("utf-8")) - len(
            text[edit.start : edit.end].encode("utf-8")
        )
    if projected > limits.max_input_bytes:
        raise NativeImportError("native import normalized bytes exceed their budget")
    result = apply_splices(text, edits).encode("utf-8")
    _check(deadline)
    return result, len(edits)


def _discard_generated_preferences(
    snapshot: Path,
    board_relative: str,
    context: dict[str, bytes],
    *,
    max_bytes: int,
    deadline: float,
) -> int:
    """Dispose only the regular, unaliased same-stem private presentation output."""
    _check(deadline)
    relative = Path(board_relative).with_suffix(".kicad_prl")
    alias = unicodedata.normalize("NFC", relative.as_posix()).casefold()
    for name in context:
        _check(deadline)
        if unicodedata.normalize("NFC", name).casefold() == alias:
            # Captured input, including a filesystem-equivalent spelling, is never disposable.
            return 0
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    parent = os.open(snapshot, flags)
    try:
        for part in relative.parts[:-1]:
            _check(deadline)
            next_parent = os.open(part, flags, dir_fd=parent)
            os.close(parent)
            parent = next_parent
        try:
            descriptor = os.open(
                relative.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent
            )
        except FileNotFoundError:
            return 0
        try:
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or not 0 <= info.st_size <= max_bytes
            ):
                raise NativeImportError("native import preferences output is malformed")
            exact_name = False
            with os.scandir(parent) as entries:
                for index, entry in enumerate(entries):
                    _check(deadline)
                    if index >= len(context) + 1:
                        raise NativeImportError("native import output tree exceeds its budget")
                    if entry.name == relative.name:
                        exact_name = True
            if (
                not exact_name
                or os.fstat(descriptor) != info
                or os.stat(relative.name, dir_fd=parent, follow_symlinks=False) != info
            ):
                raise NativeImportError("native import preferences output changed or aliases")
            _check(deadline)
            os.fchmod(parent, 0o700)
            try:
                os.unlink(relative.name, dir_fd=parent)
            finally:
                os.fchmod(parent, 0o500)
            _check(deadline)
            return info.st_size
        finally:
            os.close(descriptor)
    finally:
        os.close(parent)


def import_native_board(
    context: dict[str, bytes],
    board_relative: str,
    settings: Settings,
    *,
    deadline: float,
    max_output_bytes: int,
    account_output: Callable[[int], None] | None = None,
) -> ImportedBoard:
    """Two private upgrades; original context and all unrelated files remain untouched."""
    result = None
    stopped = None
    try:
        _check(deadline)
        admit_rule_liveness_context(
            context,
            board_relative,
            max_file_bytes=settings.max_board_bytes,
            max_context_files=settings.max_drc_context_files,
            max_context_bytes=settings.max_drc_context_bytes,
            deadline=deadline,
        )
        if type(max_output_bytes) is not int or not 1 <= max_output_bytes <= 16_777_216:
            raise NativeImportError("native import output budget is malformed")
        executable = kicad_cli._capture_kicad_cli_executable(settings, deadline)
        python = kicad_cli._validated_executable(Path(sys.executable))
        wrapper = kicad_cli._BOUNDED_EXEC.resolve(strict=True)
        if python is None or os.name != "posix" or not wrapper.is_file():
            raise NativeImportError("native import execution is unavailable")
        normalized = []
        raw_digests = []
        sizes = 0
        version_cap = min(4096, max_output_bytes)
        generated = []
        with tempfile.TemporaryDirectory(prefix="copper-native-import-") as directory:
            root = Path(directory)
            environment = kicad_cli._private_kicad_environment(root / "state")
            version_path = root / "version.txt"
            kicad_cli._verify_kicad_cli_executable(executable, settings, deadline)
            with version_path.open("wb") as stream:
                code = subprocess.run(  # noqa: S603 - pinned executable, fixed version command
                    [
                        str(python),
                        "-I",
                        str(wrapper),
                        str(version_cap),
                        str(executable.path),
                        "--version",
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=stream,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    timeout=kicad_cli._candidate_drc_deadline_settings(
                        settings, deadline
                    ).kicad_timeout_seconds,
                    env=environment,
                    cwd=environment["TMPDIR"],
                ).returncode
            version = read_workspace_file(
                root, "version.txt", allowed_suffixes={".txt"}, max_bytes=version_cap
            ).content
            sizes += len(version)
            if account_output is not None:
                account_output(len(version))
            if code or version.strip() != b"10.0.5":
                raise NativeImportError("native import backend profile is unsupported")
            for index in range(2):
                _check(deadline)
                snapshot = root / f"input-{index}"
                kicad_cli._write_drc_snapshot(context, snapshot)
                kicad_cli._make_snapshot_read_only(snapshot)
                board = snapshot / board_relative
                descriptor = os.open(board, os.O_RDONLY | os.O_NOFOLLOW)
                try:
                    os.fchmod(descriptor, 0o600)
                    board.parent.chmod(0o700)
                    kicad_cli._verify_kicad_cli_executable(executable, settings, deadline)
                    remaining = min(settings.max_board_bytes, max_output_bytes - sizes)
                    if remaining < 1:
                        raise NativeImportError("native import output budget exhausted")
                    code = subprocess.run(  # noqa: S603 - fixed upgrade of a confined private copy
                        [
                            str(python),
                            "-I",
                            str(wrapper),
                            str(remaining),
                            str(executable.path),
                            "pcb",
                            "upgrade",
                            str(board),
                        ],
                        stdin=subprocess.DEVNULL,
                        stdout=subprocess.DEVNULL,
                        stderr=subprocess.DEVNULL,
                        shell=False,
                        check=False,
                        timeout=kicad_cli._candidate_drc_deadline_settings(
                            settings, deadline
                        ).kicad_timeout_seconds,
                        env=environment,
                        cwd=environment["TMPDIR"],
                    ).returncode
                finally:
                    try:
                        os.fchmod(descriptor, 0o400)
                    finally:
                        os.close(descriptor)
                    board.parent.chmod(0o500)
                _check(deadline)
                if code:
                    raise NativeImportError("native board upgrade failed")
                # The one authorized output may be atomically replaced by the native writer.
                # Pin that regular output before changing only its mode; all other files stay RO.
                descriptor = os.open(board, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(descriptor)
                    if (
                        not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1
                        or not 0 < info.st_size <= remaining
                    ):
                        raise NativeImportError("native import output is malformed")
                    os.fchmod(descriptor, 0o400)
                finally:
                    os.close(descriptor)
                discarded = _discard_generated_preferences(
                    snapshot,
                    board_relative,
                    context,
                    max_bytes=min(
                        settings.max_board_bytes, max_output_bytes - sizes - info.st_size
                    ),
                    deadline=deadline,
                )
                sizes += discarded
                if account_output is not None and discarded:
                    account_output(discarded)
                kicad_cli._validate_snapshot_tree(
                    snapshot,
                    frozenset(context),
                    kicad_cli._candidate_drc_deadline_settings(settings, deadline),
                )
                payload = read_workspace_file(
                    snapshot, board_relative, allowed_suffixes={".kicad_pcb"}, max_bytes=remaining
                ).content
                sizes += len(payload)
                if account_output is not None:
                    account_output(len(payload))
                for name, original in context.items():
                    _check(deadline)
                    if (
                        name != board_relative
                        and read_workspace_file(
                            snapshot, name, allowed_names={Path(name).name}, max_bytes=len(original)
                        ).content
                        != original
                    ):
                        raise NativeImportError("native import changed an unrelated input")
                raw_digests.append(_sha(payload, deadline))
                converted, count = normalize_imported_board(
                    context[board_relative], payload, settings, deadline
                )
                normalized.append(converted)
                generated.append(count)
                kicad_cli._validate_private_kicad_state(
                    root / "state", kicad_cli._candidate_drc_deadline_settings(settings, deadline)
                )
            if normalized[0] != normalized[1] or generated[0] != generated[1]:
                raise NativeImportError("native import replay disagrees")
            binding = NativeImportBinding(
                original_board_revision=_sha(context[board_relative], deadline),
                imported_board_revision=_sha(normalized[0], deadline),
                original_context_digest=kicad_cli._context_revision(context),
                executable_digest=executable.digest,
                backend_version="10.0.5",
                command_digest=digest_document(
                    "copper-mcp/optimization/v2/native-import-command",
                    {
                        "flags": ["pcb", "upgrade"],
                        "repetitions": 2,
                        "normalization": _METHOD,
                        "discard_policy": _DISCARD_POLICY,
                    },
                ),
                generated_field_count=generated[0],
            )
        kicad_cli._verify_kicad_cli_executable(executable, settings, deadline)
        _check(deadline)
        result = ImportedBoard(normalized[0], binding, (raw_digests[0], raw_digests[1]), sizes)
    except OptimizationError as error:
        if not isinstance(error, NativeImportError):
            stopped = error
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError, OverflowError):
        pass
    if stopped is not None:
        raise stopped
    if result is None:
        raise NativeImportError("native board import could not produce bound input")
    return result
