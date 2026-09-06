"""Five MCP tools over host-owned optimization services; confirmation stays in the host."""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Annotated, Any, Literal, TypeVar

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.context import Context
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.types import ToolAnnotations
from pydantic import Field, WithJsonSchema

from copper_mcp.config import Settings
from copper_mcp.mcp_contracts import _inline_json_schema
from copper_mcp.optimization.contracts import (
    ClosedModel,
    Counter,
    Digest,
    OptimizationError,
    Verdict,
    bounded_json,
)
from copper_mcp.optimization.inputs import OptimizationLaunch
from copper_mcp.optimization.judge import JudgeReport
from copper_mcp.optimization.lifecycle import OptimizationJobRecord
from copper_mcp.optimization.package import OptimizationPackage
from copper_mcp.security import read_workspace_file

if TYPE_CHECKING:
    from copper_mcp.optimization.service import OptimizationService


class Lookup(ClosedModel):
    job_id: Digest


class Cancel(Lookup):
    expected_record_revision: Counter


class Export(Lookup):
    expected_package_digest: Digest
    expected_record_revision: Counter
    include_geometry: bool = False


class Approve(Lookup):
    expected_package_digest: Digest
    expected_record_revision: Counter
    expected_judge_digest: Digest


class HumanDecision(ClosedModel):
    decision: Literal["approve", "decline"] = "decline"


class JudgementView(ClosedModel):
    report: JudgeReport
    report_digest: Digest
    aggregate_status: Verdict
    required_status: Verdict


class OptimizationStatus(ClosedModel):
    record: OptimizationJobRecord
    judge_reports: Annotated[tuple[JudgementView, ...], Field(max_length=32)]
    private_retention_seconds: Literal[900] = 900
    apply_authority: Literal["none"] = "none"


class PackageExport(ClosedModel):
    package: OptimizationPackage
    package_digest: Digest
    candidate_digest: Digest
    judge_digest: Digest
    aggregate_status: Verdict
    required_status: Verdict
    geometry_disclosure: Literal["not_disclosed", "explicitly_authorized"] = "not_disclosed"
    artifact_uri: str | None = None
    artifact_ttl_seconds: Literal[300] | None = None
    apply_authority: Literal["none"] = "none"


LaunchArgument = Annotated[Any, WithJsonSchema(_inline_json_schema(OptimizationLaunch))]
LookupArgument = Annotated[Any, WithJsonSchema(_inline_json_schema(Lookup))]
CancelArgument = Annotated[Any, WithJsonSchema(_inline_json_schema(Cancel))]
ExportArgument = Annotated[Any, WithJsonSchema(_inline_json_schema(Export))]
ApproveArgument = Annotated[Any, WithJsonSchema(_inline_json_schema(Approve))]
_Model = TypeVar("_Model", bound=ClosedModel)


@dataclass(frozen=True)
class _Artifact:
    expires_at: float
    job_id: str
    owner: str
    package_digest: str
    source: bytes


def _decode(payload: object, model: type[_Model]) -> _Model:
    try:
        encoded = json.dumps(payload, allow_nan=False, ensure_ascii=True).encode("ascii")
        bounded_json(encoded)
        return model.model_validate_json(encoded)
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise OptimizationError("optimization command is malformed") from None


class OptimizationGateway:
    """One stdio operator context; unauthenticated network clients cannot claim this owner."""

    def __init__(self, settings: Callable[[], Settings]) -> None:
        self._settings = settings
        self._service: OptimizationService | None = None
        self._owner: str | None = None
        self._lock = threading.RLock()
        self._artifacts: dict[str, _Artifact] = {}

    def _purge_artifacts(self) -> None:
        expired = [
            token
            for token, artifact in self._artifacts.items()
            if artifact.expires_at <= time.monotonic()
        ]
        for token in expired:
            del self._artifacts[token]

    def retain_artifact(self, source: bytes, job_id: str, owner: str, package_digest: str) -> str:
        with self._lock:
            self._purge_artifacts()
            if (
                type(source) is not bytes
                or len(self._artifacts) >= 128
                or sum(len(value.source) for value in self._artifacts.values()) + len(source)
                > 64 * 1024 * 1024
            ):
                raise OptimizationError("optimization artifact capacity is exhausted")
            token = secrets.token_urlsafe(32)
            if token in self._artifacts:
                raise OptimizationError("optimization artifact capability is unavailable")
            self._artifacts[token] = _Artifact(
                time.monotonic() + 300, job_id, owner, package_digest, source
            )
            return "pcb://optimization/" + token + "/candidate.kicad_pcb"

    def read_artifact(self, token: str) -> bytes:
        with self._lock:
            self._purge_artifacts()
            if (
                type(token) is not str
                or re.fullmatch(r"[A-Za-z0-9_-]{43}", token) is None
                or token not in self._artifacts
            ):
                raise ResourceError("optimization artifact is unavailable")
            artifact = self._artifacts[token]
            try:
                service, owner = self.service()
                record, _ = service.get(artifact.job_id, owner)
                if (
                    owner != artifact.owner
                    or record.package_digest != artifact.package_digest
                    or record.status not in {"awaiting_approval", "approved", "completed"}
                ):
                    raise OptimizationError("optimization artifact was revoked")
            except OptimizationError:
                del self._artifacts[token]
                raise ResourceError("optimization artifact is unavailable") from None
            return artifact.source

    def service(self) -> tuple[OptimizationService, str]:
        with self._lock:
            settings = self._settings()
            if settings.transport != "stdio":
                raise OptimizationError("optimization requires an authenticated local host")
            if self._service is None:
                # Merely registering schemas (including on unsupported HTTP transports) must
                # not import the execution/database stack. Execution is admitted above first.
                from copper_mcp.optimization.repository import OptimizationJobRepository
                from copper_mcp.optimization.service import OptimizationService

                directory = settings.workspace / ".copper-mcp"
                if directory.is_symlink():
                    raise OptimizationError("optimization state directory is unavailable")
                directory.mkdir(mode=0o700, exist_ok=True)
                if directory.stat().st_mode & 0o077:
                    raise OptimizationError("optimization state directory must be private")
                key_path = directory / "optimization-owner.key"
                try:
                    descriptor = os.open(
                        key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
                    )
                except FileExistsError:
                    pass
                else:
                    with os.fdopen(descriptor, "wb") as stream:
                        stream.write(secrets.token_bytes(32))
                key = read_workspace_file(
                    settings.workspace,
                    ".copper-mcp/optimization-owner.key",
                    allowed_suffixes={".key"},
                    max_bytes=32,
                ).content
                if len(key) != 32 or key_path.stat().st_mode & 0o077:
                    raise OptimizationError("optimization owner context is unavailable")
                path = directory / "optimization.sqlite3"
                if path.is_symlink():
                    raise OptimizationError("optimization job store is unavailable")
                self._owner = (
                    "sha256:" + hashlib.sha256(b"copper-stdio-owner/v1\0" + key).hexdigest()
                )
                self._service = OptimizationService(
                    settings,
                    OptimizationJobRepository(path),
                    allow_host_confirmation=settings.optimization_host_confirmation,
                )
            elif self._service.settings != settings:
                raise OptimizationError("optimization configuration changed; restart the server")
            assert self._owner is not None
            return self._service, self._owner

    def status(self, job_id: str) -> OptimizationStatus:
        service, owner = self.service()
        record, reports = service.get(job_id, owner)
        return OptimizationStatus(
            record=record,
            judge_reports=tuple(
                JudgementView(
                    report=report,
                    report_digest=report.digest,
                    aggregate_status=report.aggregate_status,
                    required_status=report.required_status,
                )
                for report in reports
            ),
        )


def register_optimization_tools(
    mcp: MCPServer[None], settings: Callable[[], Settings]
) -> OptimizationGateway:
    gateway = OptimizationGateway(settings)
    read = ToolAnnotations(
        read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False
    )
    change = ToolAnnotations(
        read_only_hint=False, destructive_hint=False, idempotent_hint=False, open_world_hint=False
    )

    @mcp.tool(annotations=change, structured_output=True)
    def start_optimization(request: LaunchArgument) -> OptimizationStatus:
        """Queue a bounded native optimization package; no board is applied or disclosed."""
        try:
            service, owner = gateway.service()
            return gateway.status(service.start(request, owner).job_id)
        except (OptimizationError, OSError):
            raise ToolError("optimization start was refused") from None

    @mcp.tool(annotations=read, structured_output=True)
    def get_optimization_job(request: LookupArgument) -> OptimizationStatus:
        """Read owner-bound lifecycle metadata and explicitly inconclusive judge domains."""
        try:
            return gateway.status(_decode(request, Lookup).job_id)
        except (OptimizationError, OSError):
            raise ToolError("optimization job is unavailable") from None

    @mcp.tool(annotations=change, structured_output=True)
    def cancel_optimization_job(request: CancelArgument) -> OptimizationStatus:
        """Fence a queued/running job using its exact record revision."""
        try:
            command = _decode(request, Cancel)
            service, owner = gateway.service()
            service.cancel(command.job_id, owner, command.expected_record_revision)
            return gateway.status(command.job_id)
        except (OptimizationError, OSError):
            raise ToolError("optimization cancellation was refused") from None

    @mcp.tool(annotations=change, structured_output=True)
    async def export_optimization_package(
        request: ExportArgument, ctx: Context[None, Any]
    ) -> PackageExport:
        """Export metadata; full candidate-board disclosure additionally requires host consent."""
        try:
            command = _decode(request, Export)
            service, owner = gateway.service()
            package = service.export(command.job_id, owner, command.expected_package_digest)
            record, _ = service.get(command.job_id, owner)
            if record.revision != command.expected_record_revision:
                raise OptimizationError("optimization export revision is stale")
            uri = None
            if command.include_geometry:
                if not service.allow_host_confirmation:
                    raise OptimizationError("trusted geometry disclosure is not configured")
                service.ensure_review_ready(command.job_id, owner)
                decision = await ctx.elicit(
                    "Disclose the complete candidate KiCad board, "
                    "including original design content, "
                    "to this MCP client for package "
                    + package.digest
                    + "? This does not apply or approve the board.",
                    HumanDecision,
                )
                if decision.action != "accept" or decision.data.decision != "approve":
                    raise OptimizationError("optimization geometry disclosure was declined")
                source = service.private_candidate(
                    command.job_id,
                    owner,
                    command.expected_record_revision,
                    command.expected_package_digest,
                )
                uri = gateway.retain_artifact(source, command.job_id, owner, package.digest)
            return PackageExport(
                package=package,
                package_digest=package.digest,
                candidate_digest=package.binding.digest,
                judge_digest=package.judge.digest,
                aggregate_status=package.judge.aggregate_status,
                required_status=package.judge.required_status,
                geometry_disclosure="not_disclosed" if uri is None else "explicitly_authorized",
                artifact_uri=uri,
                artifact_ttl_seconds=None if uri is None else 300,
            )
        except (OptimizationError, OSError):
            raise ToolError("optimization package is unavailable") from None

    @mcp.tool(annotations=change, structured_output=True)
    async def approve_optimization_job(
        request: ApproveArgument, ctx: Context[None, Any]
    ) -> OptimizationStatus:
        """Request confirmation through the configured trusted human channel."""
        try:
            command = _decode(request, Approve)
            service, owner = gateway.service()
            if not service.allow_host_confirmation:
                raise OptimizationError("trusted human confirmation is not configured")
            service.ensure_review_ready(command.job_id, owner)
            package = service.export(command.job_id, owner, command.expected_package_digest)
            record, _ = service.get(command.job_id, owner)
            if (
                record.revision != command.expected_record_revision
                or package.judge.digest != command.expected_judge_digest
            ):
                raise OptimizationError("optimization confirmation is stale")
            decision = await ctx.elicit(
                "Review this CopperMCP optimization package: "
                + package.digest
                + ". Judge: "
                + package.judge.digest
                + ". Unresolved domains: "
                + ", ".join(package.judge.inconclusive_domains)
                + ". Approving acknowledges the package. "
                + "Board application remains separately authorized.",
                HumanDecision,
            )
            if decision.action != "accept" or decision.data.decision != "approve":
                return gateway.status(command.job_id)
            service.approve_from_host(
                command.job_id,
                owner,
                command.expected_record_revision,
                command.expected_package_digest,
                command.expected_judge_digest,
            )
            return gateway.status(command.job_id)
        except (OptimizationError, OSError):
            raise ToolError("optimization approval was refused") from None

    if settings().transport == "stdio":

        @mcp.resource(
            "pcb://optimization/{token}/candidate.kicad_pcb", mime_type="application/octet-stream"
        )
        def optimization_candidate_artifact(token: str) -> bytes:
            """Read a bounded expiring candidate artifact after explicit geometry disclosure."""
            return gateway.read_artifact(token)

    return gateway
