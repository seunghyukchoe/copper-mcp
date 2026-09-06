"""Host-owned scheduling, private input retention, and review of optimization packages."""

from __future__ import annotations

import hashlib
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import _context_revision, _drc_context
from copper_mcp.optimization.approval import HumanApprovalAuthority
from copper_mcp.optimization.contracts import OptimizationError, OptimizationRequest
from copper_mcp.optimization.inputs import prepare_optimization
from copper_mcp.optimization.isolated import run_isolated_job
from copper_mcp.optimization.judge import JudgeReport
from copper_mcp.optimization.lifecycle import OptimizationJobRecord
from copper_mcp.optimization.package import OptimizationPackage
from copper_mcp.optimization.repository import OptimizationJobRepository
from copper_mcp.parse_budgets import parse_limits_for
from copper_mcp.security import read_workspace_file

_PRIVATE_TTL_SECONDS = 900
_MAX_PRIVATE_BYTES = 64 * 1024 * 1024


@dataclass
class _PrivateJob:
    owner: str
    request: OptimizationRequest
    board_path: str
    context_digest: str
    expires_at: float
    reserved_bytes: int
    profile: KiCadConstraintProfile
    artifact_bindings: tuple[tuple[str, str], ...] = ()
    future: Future[OptimizationJobRecord] | None = None
    judges: list[JudgeReport] = field(default_factory=list)
    source: bytes | None = None


class OptimizationService:
    """One bounded local worker; repository rows never retain private board/intent data."""

    def __init__(
        self,
        settings: Settings,
        repository: OptimizationJobRepository,
        *,
        allow_host_confirmation: bool = False,
    ) -> None:
        self.settings = settings
        self.repository = repository
        self.authority = HumanApprovalAuthority(enabled=allow_host_confirmation)
        self.allow_host_confirmation = allow_host_confirmation
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="copper-optimization")
        self._jobs: dict[str, _PrivateJob] = {}
        self._preparing = 0
        self._job_runner = run_isolated_job
        self._lock = threading.RLock()

    def close(self) -> None:
        self._executor.shutdown(wait=True)
        with self._lock:
            self._jobs.clear()

    def _purge(self) -> None:
        now = time.monotonic()
        expired = [
            job_id
            for job_id, private in self._jobs.items()
            if private.expires_at <= now and (private.future is None or private.future.done())
        ]
        for job_id in expired:
            private = self._jobs.pop(job_id)
            record = self.repository.get(job_id, private.owner)
            if record.status == "awaiting_approval":
                self.repository.recover_interrupted(
                    job_id, private.owner, expected_revision=record.revision
                )

    def _private(self, job_id: str, owner: str) -> _PrivateJob:
        self._purge()
        private = self._jobs.get(job_id)
        if private is None or private.owner != owner:
            raise OptimizationError("optimization private inputs are unavailable")
        return private

    def start(self, payload: object, owner: str) -> OptimizationJobRecord:
        with self._lock:
            self._purge()
            if (
                sum(
                    item.future is not None and not item.future.done()
                    for item in self._jobs.values()
                )
                + self._preparing
                >= 2
            ):
                raise OptimizationError("optimization queue capacity is exhausted")
            self._preparing += 1
        try:
            prepared = prepare_optimization(payload, self.settings)
        except BaseException:
            with self._lock:
                self._preparing -= 1
            raise
        with self._lock:
            self._preparing -= 1
            self._purge()
            reserved = sum(len(content) for content in prepared.context.values()) + len(
                prepared.electrical_source or b""
            )
            if (
                len(self._jobs) >= 128
                or reserved + sum(item.reserved_bytes for item in self._jobs.values())
                > _MAX_PRIVATE_BYTES
            ):
                raise OptimizationError("optimization private retention capacity is exhausted")
            record = self.repository.create(prepared.request, owner)
            if record.job_id in self._jobs:
                return record
            if record.status != "queued":
                return record
            private = _PrivateJob(
                owner,
                prepared.request,
                prepared.board_path,
                prepared.original_context_digest,
                time.monotonic() + _PRIVATE_TTL_SECONDS,
                reserved,
                prepared.profile,
                artifact_bindings=prepared.input_artifact_bindings,
            )
            self._jobs[record.job_id] = private
            pending_source: bytes | None = None
            pending_reports: list[JudgeReport] = []

            def retain(package: OptimizationPackage, source: bytes) -> None:
                nonlocal pending_source
                with self._lock:
                    if len(source) > self.settings.max_board_bytes:
                        raise OptimizationError("optimization result exceeds its byte budget")
                    if (
                        sum(item.reserved_bytes for item in self._jobs.values()) + len(source)
                        > _MAX_PRIVATE_BYTES
                    ):
                        raise OptimizationError("optimization private result capacity is exhausted")
                    if package.request_digest != private.request.digest:
                        raise OptimizationError("optimization result binding is invalid")
                    if pending_source is not None:
                        raise OptimizationError("optimization result was already retained")
                    pending_source = source
                    private.reserved_bytes += len(source)

            def observe(report: JudgeReport) -> None:
                with self._lock:
                    if len(pending_reports) >= private.request.limits.max_candidates:
                        raise OptimizationError(
                            "optimization judge retention capacity is exhausted"
                        )
                    pending_reports.append(report)

            future = self._executor.submit(
                self._job_runner,
                self.repository,
                record.job_id,
                prepared,
                owner,
                self.settings,
                payload,
                retain,
                observe,
            )
            private.future = future

            def finished(completed: Future[OptimizationJobRecord]) -> None:
                nonlocal pending_source, pending_reports
                with self._lock:
                    try:
                        result = completed.result()
                        current = self.repository.get(record.job_id, owner)
                        if result == current and result.status == "awaiting_approval":
                            private.source = pending_source
                            private.judges = pending_reports
                        elif (
                            result == current
                            and result.failure_code == "required_domain_inconclusive"
                        ):
                            # Diagnostic unknown-domain evidence is useful, but never a candidate.
                            private.judges = pending_reports
                        elif result == current and result.status == "cancelled":
                            private.source = None
                            private.judges.clear()
                    except Exception:
                        # Future exception text can contain private input; no logging or echoing.
                        private.source = None
                        private.judges.clear()
                        # Failed futures keep tracebacks, including private execution inputs.
                        private.future = None
                    finally:
                        # Future retains its done callback. Do not retain staged geometry through
                        # that closure after transferring it (or discarding a failed delivery).
                        pending_source = None
                        pending_reports = []
                    private.reserved_bytes = (
                        len(private.source) if private.source is not None else 0
                    )
                    private.expires_at = time.monotonic() + _PRIVATE_TTL_SECONDS

            future.add_done_callback(finished)
            return record

    def get(self, job_id: str, owner: str) -> tuple[OptimizationJobRecord, tuple[JudgeReport, ...]]:
        with self._lock:
            self._purge()
            record = self.repository.get(job_id, owner)
            private = self._jobs.get(job_id)
            reports = (
                tuple(private.judges) if private is not None and private.owner == owner else ()
            )
            return record, reports

    def cancel(self, job_id: str, owner: str, expected_revision: int) -> OptimizationJobRecord:
        with self._lock:
            private = self._private(job_id, owner)
            result = self.repository.cancel(
                job_id, private.request, owner, expected_revision=expected_revision
            )
            private.source = None
            private.judges.clear()
            if private.future is None or private.future.done():
                private.reserved_bytes = 0
            else:
                private.future.cancel()
            return result

    def export(self, job_id: str, owner: str, expected_package_digest: str) -> OptimizationPackage:
        with self._lock:
            self._private(job_id, owner)
            package = self.repository.get_package(job_id, owner)
            if package.digest != expected_package_digest:
                raise OptimizationError("optimization package revision is stale")
            return package

    def approve_from_host(
        self,
        job_id: str,
        owner: str,
        expected_revision: int,
        expected_package_digest: str,
        expected_judge_digest: str,
    ) -> OptimizationJobRecord:
        """Call only after the configured trusted host has confirmed this exact package."""

        with self._lock:
            self.ensure_review_ready(job_id, owner)
            private = self._private(job_id, owner)
            record = self.repository.get(job_id, owner)
            package = self.export(job_id, owner, expected_package_digest)
            if (
                record.revision != expected_revision
                or package.judge.digest != expected_judge_digest
            ):
                raise OptimizationError("optimization confirmation is stale")
            board = read_workspace_file(
                self.settings.workspace,
                private.board_path,
                allowed_suffixes={".kicad_pcb"},
                max_bytes=self.settings.max_board_bytes,
            )
            observed_board_revision = "sha256:" + hashlib.sha256(board.content).hexdigest()
            parsed = parse_kicad_bytes(
                board.content, private.profile, parse_limits_for(self.settings)
            )
            if parsed.snapshot is None or parsed.diagnostics:
                raise OptimizationError("optimization source is no longer reviewable")
            observed_snapshot_digest = parsed.snapshot.snapshot_digest
            context = _drc_context(board.path, self.settings, board)
            if _context_revision(context) != private.context_digest:
                raise OptimizationError("optimization source or rules changed before approval")
            if (
                observed_board_revision != private.request.board_revision
                or observed_snapshot_digest != private.request.snapshot_digest
            ):
                raise OptimizationError("optimization source changed before approval")
            for path, expected_digest in private.artifact_bindings:
                artifact = read_workspace_file(
                    self.settings.workspace, path, allowed_suffixes={".json"}, max_bytes=96_000
                )
                if "sha256:" + hashlib.sha256(artifact.content).hexdigest() != expected_digest:
                    raise OptimizationError("optimization intent changed before approval")
            capability = self.authority.issue_from_human_channel(
                record, package, owner_binding=owner
            )
            approved = self.repository.approve(
                record.job_id,
                private.request,
                owner,
                expected_revision=record.revision,
                package=package,
                capability=capability,
                authority=self.authority,
                observed_board_revision=observed_board_revision,
                observed_snapshot_digest=observed_snapshot_digest,
                complete=True,
            )
            return approved

    def ensure_review_ready(self, job_id: str, owner: str) -> None:
        """Child metadata is not approval authority before parent delivery validation."""
        with self._lock:
            private = self._private(job_id, owner)
            if (
                private.future is None
                or not private.future.done()
                or private.future.cancelled()
                or private.future.exception() is not None
                or not private.source
            ):
                raise OptimizationError("optimization candidate delivery is incomplete")
            package = self.repository.get_package(job_id, owner)
            if (
                "sha256:" + hashlib.sha256(private.source).hexdigest()
                != package.binding.candidate_board_revision
            ):
                raise OptimizationError("optimization private candidate binding is inconsistent")

    def private_candidate(
        self, job_id: str, owner: str, expected_revision: int, expected_package_digest: str
    ) -> bytes:
        """Read immutable private bytes after the transport obtains disclosure consent."""
        with self._lock:
            self.ensure_review_ready(job_id, owner)
            private = self._private(job_id, owner)
            record = self.repository.get(job_id, owner)
            package = self.export(job_id, owner, expected_package_digest)
            if record.revision != expected_revision or type(private.source) is not bytes:
                raise OptimizationError("optimization private candidate is unavailable")
            if (
                "sha256:" + hashlib.sha256(private.source).hexdigest()
                != package.binding.candidate_board_revision
            ):
                raise OptimizationError("optimization private candidate binding is inconsistent")
            return private.source
