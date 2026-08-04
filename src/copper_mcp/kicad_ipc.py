"""Bounded, read-only observation through KiCad's official IPC Python binding.

The optional ``kicad-python`` package (imported as :mod:`kipy`) talks to a running
KiCad PCB Editor over its local IPC socket.  This adapter deliberately exposes only
redacted counts and a content digest.  It never returns board text, net names, UUIDs,
or model-controlled strings, and it has no write path.  File-backed Board IR remains
the authoritative route/placement input until a live snapshot can be bound to the
same revision contract.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import platform
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from copper_mcp.config import Settings

IPC_SCHEMA_VERSION = "0.1.0"
_DEFAULT_TIMEOUT_MS = 2_000
_MAX_TIMEOUT_MS = 10_000
_MAX_SOCKET_CHARS = 4_096
_MAX_IPC_ITEMS = 1_000_000
_COUNT_METHODS = (
    ("nets", "get_nets"),
    ("footprints", "get_footprints"),
    ("pads", "get_pads"),
    ("tracks", "get_tracks"),
    ("vias", "get_vias"),
    ("zones", "get_zones"),
    ("shapes", "get_shapes"),
    ("text", "get_text"),
    ("dimensions", "get_dimensions"),
    ("groups", "get_groups"),
)


class KicadIpcError(RuntimeError):
    """Base error for a bounded live KiCad observation."""


class KicadIpcUnavailableError(KicadIpcError):
    """Raised when the optional official binding is not installed."""


class KicadIpcConfigurationError(KicadIpcError):
    """Raised when an IPC endpoint is not a local, bounded endpoint."""


class KicadIpcConnectionError(KicadIpcError):
    """Raised when KiCad cannot answer an IPC request."""


class KicadIpcVersionError(KicadIpcError):
    """Raised when the binding cannot prove compatibility with KiCad."""


class KicadIpcPayloadError(KicadIpcError):
    """Raised when a live board snapshot exceeds the configured safety budget."""


class _VersionLike(Protocol):
    major: int
    minor: int
    patch: int


class _BoardLike(Protocol):
    def get_as_string(self) -> str: ...


class _KiCadLike(Protocol):
    def get_version(self) -> _VersionLike: ...

    def get_api_version(self) -> _VersionLike: ...

    def check_version(self) -> bool: ...

    def get_board(self) -> _BoardLike: ...


def _version_string(version: _VersionLike) -> str:
    """Return only numeric version components from an untrusted IPC object."""

    components: list[int] = []
    for name in ("major", "minor", "patch"):
        value = getattr(version, name, None)
        if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 999:
            raise KicadIpcVersionError("KiCad returned an invalid version")
        components.append(value)
    return ".".join(str(value) for value in components)


def _socket_path() -> tuple[str | None, str]:
    """Validate and normalize the KiCad-provided local IPC endpoint.

    ``kicad-python`` accepts an NNG URL.  KiCad documentation describes
    ``KICAD_API_SOCKET`` as a path, so absolute POSIX paths are normalized to an
    ``ipc://`` URL.  TCP endpoints are refused to keep this local-first adapter from
    becoming an unintended network client.
    """

    raw = os.environ.get("KICAD_API_SOCKET", "").strip()
    if not raw:
        return None, "default-local-ipc"
    if len(raw) > _MAX_SOCKET_CHARS or any(ord(char) < 0x20 for char in raw):
        raise KicadIpcConfigurationError("KICAD_API_SOCKET is invalid")
    if raw.startswith("ipc://"):
        return raw, "configured-local-ipc"
    if raw.startswith("/"):
        return f"ipc://{raw}", "configured-local-ipc"
    if platform.system() == "Windows" and raw.startswith("\\\\.\\pipe\\"):
        return f"ipc://{raw}", "configured-local-ipc"
    raise KicadIpcConfigurationError("KICAD_API_SOCKET must identify a local IPC endpoint")


def _load_kicad_factory() -> Callable[..., _KiCadLike]:
    """Load the official binding lazily so the core remains dependency-light."""

    try:
        module = importlib.import_module("kipy")
    except ModuleNotFoundError as error:
        raise KicadIpcUnavailableError(
            "install the optional 'kicad-python' dependency to use live KiCad IPC"
        ) from error
    factory = getattr(module, "KiCad", None)
    if not callable(factory):
        raise KicadIpcUnavailableError("the installed kicad-python binding is incomplete")
    return cast(Callable[..., _KiCadLike], factory)


def _count_items(board: _BoardLike) -> dict[str, int]:
    counts: dict[str, int] = {}
    for name, method_name in _COUNT_METHODS:
        method = getattr(board, method_name, None)
        if not callable(method):
            raise KicadIpcConnectionError("the KiCad board API is missing an observation method")
        try:
            items = method()
        except Exception as error:  # pragma: no cover - exercised by the real binding
            raise KicadIpcConnectionError(
                "KiCad rejected a read-only observation request"
            ) from error
        if not isinstance(items, Sequence) or isinstance(items, str | bytes | bytearray):
            raise KicadIpcConnectionError("KiCad returned an invalid item collection")
        count = len(items)
        if not 0 <= count <= _MAX_IPC_ITEMS:
            raise KicadIpcPayloadError("KiCad item collection exceeds the observation budget")
        counts[name] = count
    return counts


@dataclass(frozen=True, slots=True)
class LiveBoardObservation:
    """Privacy-preserving summary of one live KiCad PCB document."""

    kicad_version: str
    api_version: str
    compatibility: str
    board_digest: str
    board_bytes: int
    object_counts: Mapping[str, int]
    socket_kind: str
    read_only: bool = True
    schema_version: str = IPC_SCHEMA_VERSION
    source: str = "kicad-ipc-live"

    def __post_init__(self) -> None:
        if self.schema_version != IPC_SCHEMA_VERSION:
            raise KicadIpcError("unsupported live observation schema")
        if self.source != "kicad-ipc-live" or not self.read_only:
            raise KicadIpcError("live observations are read-only")
        if self.compatibility not in {"compatible", "future_api_unverified"}:
            raise KicadIpcError("live observation compatibility is invalid")
        if not self.board_digest.startswith("sha256:") or len(self.board_digest) != 71:
            raise KicadIpcError("live board digest is invalid")
        if not 1 <= self.board_bytes <= 64 * 1024 * 1024:
            raise KicadIpcError("live board size is outside the observation budget")
        frozen = dict(sorted(self.object_counts.items()))
        if any(
            not isinstance(name, str)
            or not name
            or not isinstance(value, int)
            or isinstance(value, bool)
            or not 0 <= value <= _MAX_IPC_ITEMS
            for name, value in frozen.items()
        ):
            raise KicadIpcError("live object counts are invalid")
        object.__setattr__(self, "object_counts", frozen)

    def to_dict(self) -> dict[str, Any]:
        """Return detached, redacted structured output for MCP and CLI adapters."""

        return {
            "schema_version": self.schema_version,
            "source": self.source,
            "kicad_version": self.kicad_version,
            "api_version": self.api_version,
            "compatibility": self.compatibility,
            "board_digest": self.board_digest,
            "board_bytes": self.board_bytes,
            "object_counts": dict(self.object_counts),
            "socket_kind": self.socket_kind,
            "read_only": self.read_only,
        }


@dataclass(frozen=True, slots=True)
class LiveBoardSnapshot:
    """The bounded source bytes paired with their redacted live observation.

    The source is an internal hand-off to the Board IR/Circuit Scene converter.  MCP and
    plugin adapters receive only :attr:`observation`, so a caller cannot accidentally turn
    the IPC transport into a raw-board export.
    """

    observation: LiveBoardObservation
    source: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.source, bytes) or not self.source:
            raise KicadIpcPayloadError("live board source is empty")
        if len(self.source) != self.observation.board_bytes:
            raise KicadIpcPayloadError("live board source size is not bound to its observation")
        digest = f"sha256:{hashlib.sha256(self.source).hexdigest()}"
        if digest != self.observation.board_digest:
            raise KicadIpcPayloadError("live board source digest is not bound to its observation")


def capture_live_board(
    settings: Settings | None = None,
    *,
    client_factory: Callable[..., _KiCadLike] | None = None,
    allow_future_api: bool = False,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
) -> LiveBoardSnapshot:
    """Capture one bounded live board for an internal semantic conversion.

    The optional ``client_factory`` is a test seam; production calls lazily load
    ``kicad-python``.  ``allow_future_api`` is intentionally not an MCP argument:
    operators may use it in a controlled development probe, but the public tool
    refuses a newer KiCad than the binding by default.
    """

    active_settings = settings or Settings.from_env()
    if not isinstance(active_settings, Settings):
        raise KicadIpcConfigurationError("live observation settings are malformed")
    if not 1 <= timeout_ms <= _MAX_TIMEOUT_MS:
        raise KicadIpcConfigurationError("IPC timeout is outside the bounded range")

    socket_path, socket_kind = _socket_path()
    factory = client_factory or _load_kicad_factory()
    try:
        if socket_path is None:
            client = factory(timeout_ms=timeout_ms)
        else:
            client = factory(socket_path=socket_path, timeout_ms=timeout_ms)
    except KicadIpcError:
        raise
    except Exception as error:  # pragma: no cover - exercised by the real binding
        raise KicadIpcConnectionError("could not create a KiCad IPC client") from error

    try:
        kicad_version = _version_string(client.get_version())
        api_version = _version_string(client.get_api_version())
        compatibility = "compatible"
        try:
            client.check_version()
        except Exception as error:
            if error.__class__.__name__ != "FutureVersionError":
                raise KicadIpcVersionError("KiCad IPC version validation failed") from error
            if not allow_future_api:
                raise KicadIpcVersionError(
                    "connected KiCad is newer than the installed kicad-python API"
                ) from error
            compatibility = "future_api_unverified"
        board = client.get_board()
        source = board.get_as_string()
    except KicadIpcError:
        raise
    except Exception as error:  # pragma: no cover - exercised by the real binding
        raise KicadIpcConnectionError("KiCad IPC observation failed") from error

    if not isinstance(source, str):
        raise KicadIpcPayloadError("KiCad returned a non-text board snapshot")
    try:
        source_bytes = source.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KicadIpcPayloadError("KiCad returned invalid board text") from error
    if not 1 <= len(source_bytes) <= min(active_settings.max_board_bytes, 64 * 1024 * 1024):
        raise KicadIpcPayloadError("KiCad board snapshot exceeds the observation budget")

    counts = _count_items(board)
    observation = LiveBoardObservation(
        kicad_version=kicad_version,
        api_version=api_version,
        compatibility=compatibility,
        board_digest=f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        board_bytes=len(source_bytes),
        object_counts=counts,
        socket_kind=socket_kind,
    )
    return LiveBoardSnapshot(observation=observation, source=source_bytes)


def inspect_live_board(
    settings: Settings | None = None,
    *,
    client_factory: Callable[..., _KiCadLike] | None = None,
    allow_future_api: bool = False,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
) -> LiveBoardObservation:
    """Observe the first open PCB through a local KiCad IPC session.

    The public result is intentionally redacted.  Internal consumers that need to prove a
    Circuit Scene revision came from the same live bytes must call :func:`capture_live_board`
    and keep the returned source within the same bounded process path.
    """

    return capture_live_board(
        settings,
        client_factory=client_factory,
        allow_future_api=allow_future_api,
        timeout_ms=timeout_ms,
    ).observation
