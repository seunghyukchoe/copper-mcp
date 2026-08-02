"""Bounded, structured conversion diagnostics for Board IR adapters."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from copper_mcp.board_ir.types import BoardIRSnapshot


class Severity(StrEnum):
    """Stable diagnostic severity values."""

    WARNING = "warning"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class Diagnostic:
    """One machine-readable adapter diagnostic without source-board disclosure."""

    code: str
    severity: Severity
    message: str
    source_locator: str
    object_kind: str | None = None
    object_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not 1 <= len(self.code) <= 96:
            raise ValueError("diagnostic code is malformed")
        if not isinstance(self.severity, Severity):
            raise ValueError("diagnostic severity is malformed")
        if not isinstance(self.message, str) or not 1 <= len(self.message) <= 512:
            raise ValueError("diagnostic message is malformed")
        if not isinstance(self.source_locator, str) or not 1 <= len(self.source_locator) <= 256:
            raise ValueError("diagnostic source locator is malformed")
        for name, value in (("object_kind", self.object_kind), ("object_id", self.object_id)):
            if value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 192):
                raise ValueError(f"diagnostic {name} is malformed")


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """Fail-closed adapter result: any error suppresses the snapshot."""

    snapshot: BoardIRSnapshot | None
    diagnostics: tuple[Diagnostic, ...] = ()

    def __post_init__(self) -> None:
        if self.snapshot is not None and not isinstance(self.snapshot, BoardIRSnapshot):
            raise ValueError("conversion snapshot is malformed")
        if not isinstance(self.diagnostics, tuple) or not all(
            isinstance(item, Diagnostic) for item in self.diagnostics
        ):
            raise ValueError("conversion diagnostics must be an immutable tuple")
        has_error = any(item.severity is Severity.ERROR for item in self.diagnostics)
        if has_error and self.snapshot is not None:
            raise ValueError("conversion errors cannot accompany a snapshot")
        if not has_error and self.snapshot is None:
            raise ValueError("a failed conversion must include an error diagnostic")
