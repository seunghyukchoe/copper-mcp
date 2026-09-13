"""Candidate-only native refill, source-preserving cache replacement, and bound evidence."""

from __future__ import annotations

import hashlib
import os
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from pydantic import model_validator

from copper_mcp import kicad_cli
from copper_mcp.adapters import parse_kicad_bytes
from copper_mcp.adapters.cst import Splice, apply_splices, line_indent, span
from copper_mcp.adapters.sexpr import SExpr, atoms, children, parse_sexpr
from copper_mcp.board_ir import BoardIRSnapshot
from copper_mcp.board_ir.limits import ParseLimits
from copper_mcp.config import Settings
from copper_mcp.optimization.contracts import ClosedModel, Counter, Digest, OptimizationError
from copper_mcp.optimization.native_import import _discard_generated_preferences
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.routing.astar import VerifiedFill
from copper_mcp.security import read_workspace_file
from copper_mcp.zone_fill import fill_digest, read_fill_islands

if TYPE_CHECKING:
    from copper_mcp.optimization.evaluation_v2 import SlotProbe
    from copper_mcp.optimization.inputs import PreparedOptimization


class CandidateFillError(OptimizationError):
    """A fixed refusal; never authority to use old fill or publish a partial candidate."""


class CandidateFillBinding(ClosedModel):
    identity_namespace = "copper-mcp/optimization/v2/candidate-fill"
    input_board_revision: Digest
    output_board_revision: Digest
    input_snapshot_digest: Digest
    output_snapshot_digest: Digest
    input_context_digest: Digest
    without_fill_revision: Digest
    executable_digest: Digest
    backend_version: Literal["10.0.5"]
    fill_digest: Digest
    repeat_fill_digest: Digest
    island_count: Counter
    vertex_count: Counter
    repetitions: Literal[2] = 2
    method: Literal["native-refill-cache-splice/v1"] = "native-refill-cache-splice/v1"

    @model_validator(mode="after")
    def agreement(self) -> CandidateFillBinding:
        if self.fill_digest != self.repeat_fill_digest:
            raise ValueError("candidate fill replay disagrees")
        return self


@dataclass(frozen=True)
class FilledCandidate:
    source: bytes
    snapshot: BoardIRSnapshot
    binding: CandidateFillBinding
    verified_fill: tuple[VerifiedFill, ...]


def _revision(source: bytes) -> str:
    return "sha256:" + hashlib.sha256(source).hexdigest()


def _zone_id(zone: SExpr) -> str:
    fields = children(zone, "uuid") + children(zone, "tstamp")
    if len(fields) != 1 or len(atoms(fields[0])) != 1:
        raise CandidateFillError("candidate fill requires explicit zone identities")
    return str(uuid.UUID(atoms(fields[0])[0]))


def replace_fill_cache(
    source: bytes, native: bytes | None, limits: ParseLimits, *, checkpoint: Callable[[], None]
) -> bytes:
    """Replace only cache nodes/adjacent whitespace; None produces a canonical cache-free view."""
    checkpoint()
    root = parse_sexpr(source, limits, check_deadline=checkpoint)
    text = source.decode("utf-8")
    native_zones: dict[str, SExpr] = {}
    native_text = ""
    if native is not None:
        other = parse_sexpr(native, limits, check_deadline=checkpoint)
        native_text = native.decode("utf-8")
        for zone in children(other, "zone"):
            checkpoint()
            identity = _zone_id(zone)
            if identity in native_zones:
                raise CandidateFillError("candidate fill zone identities are ambiguous")
            native_zones[identity] = zone
    edits: list[Splice] = []
    seen: set[str] = set()
    for zone in children(root, "zone"):
        checkpoint()
        identity = _zone_id(zone)
        if identity in seen or (native is not None and identity not in native_zones):
            raise CandidateFillError("candidate fill zone membership changed")
        seen.add(identity)
        start, end = span(zone, text)
        caches = children(zone, "filled_polygon")
        for cache in caches:
            left, right = span(cache, text)
            while left > start and text[left - 1].isspace():
                left -= 1
            edits.append(Splice(left, right, ""))
        tail = end - 1
        while tail > start and text[tail - 1].isspace():
            tail -= 1
        indent = line_indent(text, start)
        pieces: list[str] = []
        if native is not None:
            for cache in children(native_zones[identity], "filled_polygon"):
                checkpoint()
                left, right = span(cache, native_text)
                pieces.append(native_text[left:right])
        rendered = "".join("\n" + indent + "\t" + piece for piece in sorted(pieces))
        edits.append(Splice(tail, end - 1, rendered + "\n" + indent))
    if native is not None and seen != set(native_zones):
        raise CandidateFillError("candidate fill added a zone")
    projected = len(source)
    for edit in edits:
        checkpoint()
        projected += len(edit.replacement.encode()) - len(text[edit.start : edit.end].encode())
    if projected > limits.max_input_bytes:
        raise CandidateFillError("candidate fill exceeds its byte limit")
    result = apply_splices(text, edits).encode("utf-8")
    checkpoint()
    return result


def refill_candidate(
    prepared: PreparedOptimization,
    source: bytes,
    snapshot: BoardIRSnapshot,
    settings: Settings,
    probe: SlotProbe,
) -> FilledCandidate:
    """Generate and verify fresh fill in the production candidate workflow, never the workspace."""
    from copper_mcp.optimization.evaluation import composition_context, verify_original_context
    from copper_mcp.optimization.lifecycle import ResourceUsage
    from copper_mcp.optimization.worker import OptimizationExecutionError

    deadline = min(
        prepared.started_at + prepared.request.limits.max_runtime_ms / 1000,
        getattr(probe, "deadline", float("inf")),
    )

    def check() -> None:
        probe.checkpoint()
        if time.monotonic() >= deadline:
            raise OptimizationExecutionError("budget_exhausted")

    def remaining() -> int:
        check()
        return min(
            settings.max_board_bytes,
            probe.budget.external_output_bytes - probe.usage.external_output_bytes,
        )

    def charge(count: int) -> None:
        probe.reserve(ResourceUsage(external_output_bytes=count))

    completed: FilledCandidate | None = None
    timed_out = False
    try:
        check()
        limits = parse_limits_for(settings)
        before = parse_kicad_bytes(source, prepared.profile, limits)
        if before.snapshot != snapshot or before.diagnostics or not snapshot.content.zones:
            raise CandidateFillError("candidate fill source is inconsistent")
        verify_original_context(prepared, settings, deadline=deadline)
        context = composition_context(prepared, source, settings, deadline=deadline)
        executable = kicad_cli._capture_kicad_cli_executable(settings, deadline)
        python = kicad_cli._validated_executable(Path(sys.executable))
        if python is None or os.name != "posix":
            raise CandidateFillError("candidate fill execution is unavailable")
        outputs: list[bytes] = []
        digests: list[str] = []
        without = replace_fill_cache(source, None, limits, checkpoint=check)
        with tempfile.TemporaryDirectory(prefix="copper-candidate-fill-") as temporary:
            root = Path(temporary)
            environment = kicad_cli._private_kicad_environment(root / "state")
            for repetition in range(2):
                check()
                tree = root / str(repetition)
                kicad_cli._write_drc_snapshot(context, tree)
                kicad_cli._make_snapshot_read_only(tree)
                board = tree / prepared.board_path
                report = root / f"report-{repetition}.json"
                cap = remaining()
                if cap < 1:
                    raise OptimizationExecutionError("budget_exhausted")
                board.chmod(0o600)
                board.parent.chmod(0o700)
                kicad_cli._verify_kicad_cli_executable(executable, settings, deadline)
                # This is the same fixed refill/save operation as ADR-0021, on a private copy.
                code = subprocess.run(  # noqa: S603 - fixed native operation and confined paths
                    [
                        str(python),
                        "-I",
                        str(kicad_cli._BOUNDED_EXEC.resolve(strict=True)),
                        str(cap),
                        str(executable.path),
                        "pcb",
                        "drc",
                        "--format",
                        "json",
                        "--units",
                        "mm",
                        "--severity-all",
                        "--refill-zones",
                        "--save-board",
                        "--output",
                        str(report),
                        str(board),
                    ],
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    shell=False,
                    check=False,
                    env=environment,
                    cwd=environment["TMPDIR"],
                    timeout=kicad_cli._candidate_drc_deadline_settings(
                        settings, deadline
                    ).kicad_timeout_seconds,
                ).returncode
                check()
                if code not in kicad_cli._ACCEPTED_DRC_RETURN_CODES:
                    raise CandidateFillError("native candidate refill failed")
                native = read_workspace_file(
                    tree, prepared.board_path, allowed_suffixes={".kicad_pcb"}, max_bytes=cap
                ).content
                charge(len(native))
                observation = read_workspace_file(
                    root,
                    report.name,
                    allowed_suffixes={".json"},
                    max_bytes=min(settings.max_drc_report_bytes, remaining()),
                ).content
                charge(len(observation))
                if kicad_cli._report_kicad_version(observation) != "10.0.5":
                    raise CandidateFillError("candidate fill backend is unsupported")
                fd = os.open(board, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(fd)
                    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                        raise CandidateFillError("candidate fill output is not a private file")
                    os.fchmod(fd, 0o400)
                finally:
                    os.close(fd)
                board.parent.chmod(0o500)
                charge(
                    _discard_generated_preferences(
                        tree, prepared.board_path, context, max_bytes=remaining(), deadline=deadline
                    )
                )
                kicad_cli._validate_snapshot_tree(
                    tree,
                    frozenset(context),
                    kicad_cli._candidate_drc_deadline_settings(settings, deadline),
                )
                for name, value in context.items():
                    check()
                    if (
                        name != prepared.board_path
                        and read_workspace_file(
                            tree, name, allowed_names={Path(name).name}, max_bytes=len(value)
                        ).content
                        != value
                    ):
                        raise CandidateFillError("candidate fill changed its rule context")
                native_snapshot = parse_kicad_bytes(native, prepared.profile, limits)
                if (
                    native_snapshot.snapshot is None
                    or native_snapshot.diagnostics
                    or replace(native_snapshot.snapshot.content, source=snapshot.content.source)
                    != snapshot.content
                ):
                    raise CandidateFillError("native fill changed modeled geometry or intent")
                islands = read_fill_islands(
                    native, max_vertices=settings.max_fill_vertices, limits=limits
                )
                candidate = replace_fill_cache(source, native, limits, checkpoint=check)
                if replace_fill_cache(candidate, None, limits, checkpoint=check) != without:
                    raise CandidateFillError("candidate fill changed non-cache source")
                outputs.append(candidate)
                digests.append(fill_digest(islands))
                kicad_cli._validate_private_kicad_state(
                    root / "state", kicad_cli._candidate_drc_deadline_settings(settings, deadline)
                )
                kicad_cli._verify_kicad_cli_executable(executable, settings, deadline)
        if outputs[0] != outputs[1] or digests[0] != digests[1]:
            raise CandidateFillError("candidate fill replay disagrees")
        parsed = parse_kicad_bytes(outputs[0], prepared.profile, limits)
        if parsed.snapshot is None or parsed.diagnostics:
            raise CandidateFillError("filled candidate is unsupported")
        islands = read_fill_islands(
            outputs[0], max_vertices=settings.max_fill_vertices, limits=limits
        )
        if fill_digest(islands) != digests[0]:
            raise CandidateFillError("candidate fill cache differs from native output")
        for island in islands:
            check()
            matching = [
                zone
                for zone in snapshot.content.zones
                if zone.net_id == island.net_id and zone.layer_id == island.layer_id
            ]
            if not matching or not any(
                min(point.x for point in zone.boundary.points)
                <= min(point.x for point in island.points)
                and max(point.x for point in island.points)
                <= max(point.x for point in zone.boundary.points)
                and min(point.y for point in zone.boundary.points)
                <= min(point.y for point in island.points)
                and max(point.y for point in island.points)
                <= max(point.y for point in zone.boundary.points)
                for zone in matching
            ):
                raise CandidateFillError("candidate fill escapes its backing zone")
        binding = CandidateFillBinding(
            input_board_revision=_revision(source),
            output_board_revision=_revision(outputs[0]),
            input_snapshot_digest=snapshot.snapshot_digest,
            output_snapshot_digest=parsed.snapshot.snapshot_digest,
            input_context_digest=kicad_cli._context_revision(context),
            without_fill_revision=_revision(without),
            executable_digest=executable.digest,
            backend_version="10.0.5",
            fill_digest=digests[0],
            repeat_fill_digest=digests[1],
            island_count=len(islands),
            vertex_count=sum(len(item.points) for item in islands),
        )
        verify_original_context(prepared, settings, deadline=deadline)
        check()
        completed = FilledCandidate(
            outputs[0],
            parsed.snapshot,
            binding,
            tuple(
                VerifiedFill(item.net_id, item.layer_id, item.points, binding.output_board_revision)
                for item in islands
            ),
        )
    except OptimizationExecutionError:
        raise
    except subprocess.TimeoutExpired:
        timed_out = True
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError):
        pass
    if completed is None:
        probe.checkpoint()
        if timed_out:
            raise OptimizationExecutionError("backend_failure")
        raise CandidateFillError("candidate refill could not produce bound evidence")
    return completed
