"""Bounded, read-only observation through KiCad's official IPC Python binding.

The optional ``kicad-python`` package (imported as :mod:`kipy`) talks to a running
KiCad PCB Editor over its local IPC socket.  This adapter deliberately exposes only
redacted counts and a content digest.  It never returns board text, net names, UUIDs,
or model-controlled strings, and it has no write path.  File-backed Board IR remains
the authoritative route/placement input until a live snapshot can be bound to the
same revision contract.

Reaching a running editor is an outbound action against the operator's machine rather
than a read of a file they handed us, so every capture here is gated on the explicit
``COPPER_MCP_ALLOW_LIVE_IPC`` opt-in and refuses before the endpoint is even read.  The
live tools stay listed when it is off; they answer with a typed refusal that names the
flag, exactly as the apply surface does.
"""

from __future__ import annotations

import hashlib
import hmac
import importlib
import math
import os
import platform
import re
import secrets
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from typing import Any, Protocol, cast

from copper_mcp.adapters.sexpr import SExpr, SExprError, parse_sexpr
from copper_mcp.board_ir import ParseLimits
from copper_mcp.config import Settings

IPC_SCHEMA_VERSION = "0.1.0"
_DEFAULT_TIMEOUT_MS = 2_000
_MAX_TIMEOUT_MS = 10_000
_MAX_SOCKET_CHARS = 4_096
_MAX_TOKEN_CHARS = 4_096
_MAX_IPC_ITEMS = 1_000_000
_COUNT_NAMES = (
    "nets",
    "footprints",
    "pads",
    "tracks",
    "vias",
    "zones",
    "shapes",
    "text",
    "dimensions",
    "groups",
)
_SHAPE_HEADS = {"gr_line", "gr_rect", "gr_arc", "gr_poly", "gr_curve", "gr_circle"}
_MAX_EDITOR_SELECTION = 256
_SESSION_REVISION_PREFIX = "pbkdf2-hmac-sha256:"
_SESSION_REVISION_HEX_LENGTH = 64
# This salt is intentionally process-local and never serialized. KiCad documents that the plugin
# token identifies one running editor; binding it with fresh 256-bit local entropy makes the
# public session precondition opaque and intentionally invalid after a fresh process start.
_SESSION_REVISION_SALT = secrets.token_bytes(32)
# Python's hashlib documentation recommends hundreds of thousands of SHA-256 PBKDF2 iterations
# for limited-input secrets. Keep this fixed, CPU-only cost bounded for the local live-CAS path.
_SESSION_REVISION_ITERATIONS = 200_000
_SESSION_REVISION_DKLEN = 32
_SESSION_REVISION_SALT_DOMAIN = b"copper-mcp:kicad-ipc-session-revision:v2\x00"
_KICAD_PCB_ROOT = "kicad_pcb"
_LIVE_IPC_DISABLED_MESSAGE = (
    "live KiCad IPC observation is disabled; set COPPER_MCP_ALLOW_LIVE_IPC=1 to enable it"
)
_LAYER_NAME = re.compile(r"^[A-Za-z0-9_.-]{1,64}$")
_UUID = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

# The selection API returns wrappers, not board text.  Keep this allow-list deliberately
# narrow: a future wrapper type must be mapped explicitly before it can become an AI-facing
# reference, and unknown wrappers fail closed instead of leaking repr()/protobuf contents.
_SELECTION_KINDS = {
    "FootprintInstance": "footprint",
    "Pad": "pad",
    "Track": "segment",
    "ArcTrack": "arc",
    "Via": "via",
    "Zone": "zone",
    "BoardShape": "shape",
    "BoardSegment": "shape",
    "BoardArc": "shape",
    "BoardBezier": "shape",
    "BoardCircle": "shape",
    "BoardRectangle": "shape",
    "BoardPolygon": "shape",
    "BoardText": "text",
    "BoardTextBox": "text",
    "Dimension": "dimension",
    "AlignedDimension": "dimension",
    "CenterDimension": "dimension",
    "LeaderDimension": "dimension",
    "OrthogonalDimension": "dimension",
    "RadialDimension": "dimension",
    "Group": "group",
}


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


class KicadIpcDisabledError(KicadIpcError):
    """Raised when live IPC capture is attempted without the operator opt-in."""


class KicadIpcDeadlineError(KicadIpcConnectionError):
    """Raised when a bounded multi-call capture reaches its operation-wide deadline."""


class _VersionLike(Protocol):
    major: int
    minor: int
    patch: int


class _BoardLike(Protocol):
    def get_as_string(self) -> str: ...


class _EditorContextBoardLike(_BoardLike, Protocol):
    def get_active_layer(self) -> int: ...

    def get_layer_name(self, layer: int) -> str: ...

    def get_selection(self) -> Any: ...


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


def _session_revision() -> str | None:
    """Return an opaque, process-local PBKDF2 binding for KiCad's instance token.

    The token itself is a credential, not a password verifier.  A plain SHA-256 fingerprint
    would let an observer test candidate token values offline. PBKDF2-HMAC-SHA256 with a
    non-persistent process salt makes offline guesses deliberately expensive and keeps the wire
    value suitable only for same-process CAS; a fresh process cannot reproduce a prior value and
    must refuse it as stale.
    """

    raw = os.environ.get("KICAD_API_TOKEN", "")
    if not raw:
        return None
    if len(raw) > _MAX_TOKEN_CHARS or any(ord(char) < 0x20 for char in raw):
        raise KicadIpcConfigurationError("KICAD_API_TOKEN is invalid")
    try:
        encoded = raw.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KicadIpcConfigurationError("KICAD_API_TOKEN is invalid") from error
    tag = hashlib.pbkdf2_hmac(
        "sha256",
        encoded,
        _SESSION_REVISION_SALT_DOMAIN + _SESSION_REVISION_SALT,
        _SESSION_REVISION_ITERATIONS,
        dklen=_SESSION_REVISION_DKLEN,
    ).hex()
    return f"{_SESSION_REVISION_PREFIX}{tag}"


def _is_session_revision(value: object) -> bool:
    """Recognize only the fixed opaque live-session wire type."""

    return (
        isinstance(value, str)
        and len(value) == len(_SESSION_REVISION_PREFIX) + _SESSION_REVISION_HEX_LENGTH
        and value.startswith(_SESSION_REVISION_PREFIX)
        and all(
            character in "0123456789abcdef" for character in value[len(_SESSION_REVISION_PREFIX) :]
        )
    )


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


def _close_ipc_client(client: object) -> None:
    """Close one official IPC client without masking the observation result.

    ``kicad-python`` exposes ``KiCad.close()`` for the socket-backed client.  The test seam and
    older bindings may not implement it, so closure is capability-detected and best-effort. A
    close failure must never replace the typed observation error or leak private exception text.
    """

    close = getattr(client, "close", None)
    if not callable(close):
        return
    try:
        close()
    except Exception:
        return


def _count_serialized_items(
    source: bytes,
    max_bytes: int,
    *,
    check_deadline: Callable[[], None] | None = None,
) -> dict[str, int]:
    """Count objects from the captured serialization, not mutable live collections.

    KiCad's IPC API does not expose a count-only or max-items request. Calling ten collection
    getters would materialize unbounded responses and could mix revisions while a GUI saves.
    Counting the already-captured bytes keeps the summary tied to one revision and charges the
    parser against the same input/token/node ceilings used by the Board IR boundary.
    """

    limits = replace(
        ParseLimits(),
        max_input_bytes=min(max_bytes, 64 * 1024 * 1024),
        max_tokens=2_000_000,
        max_nodes=1_000_000,
    )
    try:
        root = parse_sexpr(source, limits, check_deadline=check_deadline)
    except SExprError as error:
        raise KicadIpcPayloadError(
            "KiCad board serialization is not a bounded S-expression"
        ) from error
    # The counter recognises heads (footprint, pad, via, ...) wherever they appear, so without
    # this gate any well-formed S-expression is summarised as if it were a PCB. The Board IR
    # adapter refuses a foreign root, but nothing downstream of it re-derives these counts, so
    # the observation boundary has to establish the document type for itself.
    if root.head != _KICAD_PCB_ROOT:
        raise KicadIpcPayloadError("KiCad returned a serialization whose root is not kicad_pcb")
    counts = dict.fromkeys(_COUNT_NAMES, 0)
    stack: list[tuple[SExpr, bool]] = [(root, False)]
    while stack:
        if check_deadline is not None:
            check_deadline()
        expression, is_top_level_child = stack.pop()
        head = expression.head
        name: str | None = None
        if head == "net" and is_top_level_child:
            name = "nets"
        elif head == "footprint":
            name = "footprints"
        elif head == "pad":
            name = "pads"
        elif head in {"segment", "arc"}:
            name = "tracks"
        elif head == "via":
            name = "vias"
        elif head == "zone":
            name = "zones"
        elif head in _SHAPE_HEADS:
            name = "shapes"
        elif head in {"gr_text", "fp_text"}:
            name = "text"
        elif head == "dimension":
            name = "dimensions"
        elif head == "group":
            name = "groups"
        if name is not None:
            counts[name] += 1
            if counts[name] > _MAX_IPC_ITEMS:
                raise KicadIpcPayloadError("serialized object count exceeds the observation budget")
        stack.extend(
            (item, expression is root) for item in expression.items if isinstance(item, SExpr)
        )
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
    # ``None`` is a truthful capability result for an observation made without a plugin token.
    # It is deliberately still serialized so a client can distinguish that state from a field
    # omitted by an older or lossy transport, and will then fail closed for a live proposal.
    session_revision: str | None = None
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
        if self.session_revision is not None and not _is_session_revision(self.session_revision):
            raise KicadIpcError("live board session revision is invalid")
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
            "session_revision": self.session_revision,
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
    session_revision: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.source, bytes) or not self.source:
            raise KicadIpcPayloadError("live board source is empty")
        if len(self.source) != self.observation.board_bytes:
            raise KicadIpcPayloadError("live board source size is not bound to its observation")
        digest = f"sha256:{hashlib.sha256(self.source).hexdigest()}"
        if digest != self.observation.board_digest:
            raise KicadIpcPayloadError("live board source digest is not bound to its observation")
        if self.session_revision is not None and not _is_session_revision(self.session_revision):
            raise KicadIpcPayloadError("live IPC session revision is invalid")
        if self.session_revision != self.observation.session_revision:
            raise KicadIpcPayloadError("live IPC session revision is not bound to its observation")


@dataclass(frozen=True, slots=True)
class LiveEditorSelection:
    """One native, type-qualified item reference from KiCad's current selection."""

    ref_id: str
    kind: str


@dataclass(frozen=True, slots=True)
class LiveEditorContextSnapshot:
    """Read-only editor state captured from one confirmed board serialization."""

    board_digest: str
    board_bytes: int
    active_layer_index: int
    active_layer_name: str
    selection: tuple[LiveEditorSelection, ...]

    def __post_init__(self) -> None:
        if not self.board_digest.startswith("sha256:") or len(self.board_digest) != 71:
            raise KicadIpcPayloadError("live editor board digest is invalid")
        if not 1 <= self.board_bytes <= 64 * 1024 * 1024:
            raise KicadIpcPayloadError("live editor board size is outside the observation budget")
        if (
            not isinstance(self.active_layer_index, int)
            or isinstance(self.active_layer_index, bool)
            or not 0 <= self.active_layer_index <= 4095
        ):
            raise KicadIpcPayloadError("live editor active layer is invalid")
        if not _LAYER_NAME.fullmatch(self.active_layer_name):
            raise KicadIpcPayloadError("live editor active layer name is invalid")
        if len(self.selection) > _MAX_EDITOR_SELECTION:
            raise KicadIpcPayloadError("live editor selection exceeds the observation budget")
        if (
            tuple(sorted(self.selection, key=lambda item: (item.ref_id, item.kind)))
            != self.selection
        ):
            raise KicadIpcPayloadError("live editor selection is not canonical")


def _selection_identity(item: Any) -> LiveEditorSelection:
    """Extract only a validated KiCad KIID from an official wrapper."""

    class_name = type(item).__name__
    kind = _SELECTION_KINDS.get(class_name)
    if kind is None:
        raise KicadIpcPayloadError("KiCad returned an unsupported selected item type")
    try:
        identifier = item.id
        value = getattr(identifier, "value", identifier)
    except Exception as error:
        raise KicadIpcPayloadError("KiCad returned an unreadable selected item identity") from error
    if not isinstance(value, str) or _UUID.fullmatch(value) is None:
        raise KicadIpcPayloadError("KiCad returned an empty or malformed selected item identity")
    return LiveEditorSelection(ref_id=f"{kind}:kicad:{value.lower()}", kind=kind)


def _read_editor_selection(
    board: _EditorContextBoardLike, max_selection: int
) -> tuple[LiveEditorSelection, ...]:
    """Read a bounded selection without calling KiCad's raw selection-string API."""

    try:
        selected = iter(board.get_selection())
    except Exception as error:
        raise KicadIpcConnectionError("KiCad editor selection observation failed") from error
    result: list[LiveEditorSelection] = []
    for item in selected:
        if len(result) >= max_selection:
            raise KicadIpcPayloadError("KiCad editor selection exceeds the observation budget")
        result.append(_selection_identity(item))
    return tuple(sorted(result, key=lambda item: (item.ref_id, item.kind)))


def capture_live_editor_context(
    settings: Settings | None = None,
    *,
    client_factory: Callable[..., _KiCadLike] | None = None,
    allow_future_api: bool = False,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    max_selection: int = _MAX_EDITOR_SELECTION,
) -> LiveEditorContextSnapshot:
    """Capture the active layer and native selection against one stable live revision.

    This uses the official ``Board.get_active_layer``, ``Board.get_layer_name`` and
    ``Board.get_selection`` APIs.  It deliberately never calls ``get_selection_as_string``
    or touches project/board mutation APIs.  The board serialization is confirmed before
    returning so the context digest cannot be mistaken for a different editor revision.
    """

    active_settings = settings or Settings.from_env()
    if not isinstance(active_settings, Settings):
        raise KicadIpcConfigurationError("live editor settings are malformed")
    if not active_settings.allow_live_ipc:
        # Refuse before the endpoint is read, so a disabled deployment never discovers an
        # ambient KICAD_API_SOCKET and never opens the binding's default socket either.
        raise KicadIpcDisabledError(_LIVE_IPC_DISABLED_MESSAGE)
    if not 1 <= timeout_ms <= _MAX_TIMEOUT_MS:
        raise KicadIpcConfigurationError("IPC timeout is outside the bounded range")
    if not 1 <= max_selection <= _MAX_EDITOR_SELECTION:
        raise KicadIpcConfigurationError("editor selection budget is outside the bounded range")

    socket_path, _socket_kind_value = _socket_path()
    factory = client_factory or _load_kicad_factory()
    try:
        if socket_path is None:
            client = factory(timeout_ms=timeout_ms)
        else:
            client = factory(socket_path=socket_path, timeout_ms=timeout_ms)
    except KicadIpcError:
        raise
    except Exception as error:
        raise KicadIpcConnectionError("could not create a KiCad IPC client") from error

    try:
        return _capture_live_editor_context_from_client(
            client,
            active_settings,
            allow_future_api=allow_future_api,
            max_selection=max_selection,
        )
    finally:
        _close_ipc_client(client)


def _capture_live_editor_context_from_client(
    client: _KiCadLike,
    settings: Settings,
    *,
    allow_future_api: bool,
    max_selection: int,
) -> LiveEditorContextSnapshot:
    """Read and validate one editor context while the caller owns client closure."""

    try:
        _version_string(client.get_version())
        _version_string(client.get_api_version())
        try:
            version_ok = client.check_version()
        except Exception as error:
            if error.__class__.__name__ != "FutureVersionError":
                raise KicadIpcVersionError("KiCad IPC version validation failed") from error
            if not allow_future_api:
                raise KicadIpcVersionError(
                    "connected KiCad is newer than the installed kicad-python API"
                ) from error
        else:
            if version_ok is not True:
                raise KicadIpcVersionError("KiCad IPC version validation was inconclusive")
        board = cast(_EditorContextBoardLike, client.get_board())
        source = board.get_as_string()
        if not isinstance(source, str):
            raise KicadIpcPayloadError("KiCad returned a non-text board snapshot")
        source_bytes = source.encode("utf-8", errors="strict")
        max_bytes = min(settings.max_board_bytes, 64 * 1024 * 1024)
        if not 1 <= len(source_bytes) <= max_bytes:
            raise KicadIpcPayloadError("KiCad board snapshot exceeds the observation budget")
        active_index = board.get_active_layer()
        active_name = board.get_layer_name(active_index)
        if (
            not isinstance(active_index, int)
            or isinstance(active_index, bool)
            or not 0 <= active_index <= 4095
            or not isinstance(active_name, str)
            or _LAYER_NAME.fullmatch(active_name) is None
        ):
            raise KicadIpcPayloadError("KiCad returned an invalid active layer")
        selection = _read_editor_selection(board, max_selection)
        # Read the context twice. Selection and active-layer changes are editor state, not board
        # bytes, so a stable board alone is insufficient for a compare-and-swap gate.
        second_active_index = board.get_active_layer()
        second_active_name = board.get_layer_name(second_active_index)
        second_selection = _read_editor_selection(board, max_selection)
        confirmation = board.get_as_string()
    except (KicadIpcError, UnicodeError):
        raise
    except Exception as error:
        raise KicadIpcConnectionError("KiCad editor context observation failed") from error
    if not isinstance(confirmation, str):
        raise KicadIpcPayloadError("KiCad returned a non-text board snapshot")
    # Same budget rule as the first read: charge the confirmation before encoding it, so an
    # oversized second read is refused as a payload-budget violation rather than encoded in
    # full and then reported as if the operator had edited the board.
    if len(confirmation) > max_bytes:
        raise KicadIpcPayloadError("KiCad board confirmation exceeds the observation budget")
    if confirmation != source:
        raise KicadIpcConnectionError("KiCad board changed during editor context observation")
    if (active_index, active_name, selection) != (
        second_active_index,
        second_active_name,
        second_selection,
    ):
        raise KicadIpcConnectionError("KiCad editor context changed during observation")
    return LiveEditorContextSnapshot(
        board_digest=f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        board_bytes=len(source_bytes),
        active_layer_index=active_index,
        active_layer_name=active_name,
        selection=selection,
    )


def capture_live_board(
    settings: Settings | None = None,
    *,
    client_factory: Callable[..., _KiCadLike] | None = None,
    allow_future_api: bool = False,
    timeout_ms: int = _DEFAULT_TIMEOUT_MS,
    deadline: float | None = None,
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
    if not active_settings.allow_live_ipc:
        # Talking to a running editor is an outbound action against the operator's machine,
        # not a read of a file they handed us. It stays off until they say otherwise, and the
        # refusal happens before KICAD_API_SOCKET is read or any default socket is opened.
        raise KicadIpcDisabledError(_LIVE_IPC_DISABLED_MESSAGE)
    if not 1 <= timeout_ms <= _MAX_TIMEOUT_MS:
        raise KicadIpcConfigurationError("IPC timeout is outside the bounded range")
    if deadline is not None and (
        isinstance(deadline, bool)
        or not isinstance(deadline, int | float)
        or not math.isfinite(float(deadline))
    ):
        raise KicadIpcConfigurationError("IPC deadline is malformed")

    socket_path, socket_kind = _socket_path()
    session_revision = _session_revision()
    factory = client_factory or _load_kicad_factory()
    try:
        if socket_path is None:
            client = factory(timeout_ms=timeout_ms)
        else:
            client = factory(socket_path=socket_path, timeout_ms=timeout_ms)
    except KicadIpcError:
        raise
    except Exception as error:
        raise KicadIpcConnectionError("could not create a KiCad IPC client") from error

    try:
        return _capture_live_board_from_client(
            client,
            active_settings,
            socket_kind=socket_kind,
            allow_future_api=allow_future_api,
            session_revision=session_revision,
            deadline=deadline,
        )
    finally:
        _close_ipc_client(client)


def _capture_live_board_from_client(
    client: _KiCadLike,
    settings: Settings,
    *,
    socket_kind: str,
    allow_future_api: bool,
    session_revision: str | None,
    deadline: float | None,
) -> LiveBoardSnapshot:
    """Read one board while the public capture function owns client closure."""

    def check_deadline() -> None:
        if deadline is not None and time.monotonic() >= deadline:
            raise KicadIpcDeadlineError("live IPC capture deadline expired")

    try:
        check_deadline()
        kicad_version = _version_string(client.get_version())
        check_deadline()
        api_version = _version_string(client.get_api_version())
        check_deadline()
        compatibility = "compatible"
        try:
            version_ok = client.check_version()
        except Exception as error:
            if error.__class__.__name__ != "FutureVersionError":
                raise KicadIpcVersionError("KiCad IPC version validation failed") from error
            if not allow_future_api:
                raise KicadIpcVersionError(
                    "connected KiCad is newer than the installed kicad-python API"
                ) from error
            compatibility = "future_api_unverified"
        else:
            if version_ok is not True:
                raise KicadIpcVersionError("KiCad IPC version validation was inconclusive")
        check_deadline()
        board = client.get_board()
        check_deadline()
        source = board.get_as_string()
        check_deadline()
    except KicadIpcError:
        raise
    except Exception as error:
        raise KicadIpcConnectionError("KiCad IPC observation failed") from error

    if not isinstance(source, str):
        raise KicadIpcPayloadError("KiCad returned a non-text board snapshot")
    try:
        source_bytes = source.encode("utf-8", errors="strict")
    except UnicodeError as error:
        raise KicadIpcPayloadError("KiCad returned invalid board text") from error
    if not 1 <= len(source_bytes) <= min(settings.max_board_bytes, 64 * 1024 * 1024):
        raise KicadIpcPayloadError("KiCad board snapshot exceeds the observation budget")

    max_bytes = min(settings.max_board_bytes, 64 * 1024 * 1024)
    counts = _count_serialized_items(
        source_bytes,
        max_bytes,
        check_deadline=check_deadline,
    )
    try:
        check_deadline()
        confirmation = board.get_as_string()
        check_deadline()
    except KicadIpcError:
        raise
    except Exception as error:
        raise KicadIpcConnectionError(
            "KiCad changed before observation could be confirmed"
        ) from error
    if not isinstance(confirmation, str):
        raise KicadIpcPayloadError("KiCad returned a non-text board snapshot")
    # Charge the confirmation against the same budget as the first read, and do it before
    # encoding rather than after. UTF-8 never encodes a character to fewer than one byte, so a
    # character count over the byte ceiling is already over the byte ceiling; refusing here
    # keeps an oversized second read a payload-budget refusal instead of mis-reporting it as a
    # concurrent board edit, and never materializes an unbudgeted encoded copy of it.
    if len(confirmation) > max_bytes:
        raise KicadIpcPayloadError("KiCad board confirmation exceeds the observation budget")
    if confirmation != source:
        raise KicadIpcConnectionError("KiCad board changed during observation")
    confirmed_session_revision = _session_revision()
    if session_revision is None:
        session_matches = confirmed_session_revision is None
    elif confirmed_session_revision is None:
        session_matches = False
    else:
        session_matches = hmac.compare_digest(confirmed_session_revision, session_revision)
    if not session_matches:
        raise KicadIpcConnectionError("KiCad IPC session changed during observation")
    observation = LiveBoardObservation(
        kicad_version=kicad_version,
        api_version=api_version,
        compatibility=compatibility,
        board_digest=f"sha256:{hashlib.sha256(source_bytes).hexdigest()}",
        board_bytes=len(source_bytes),
        object_counts=counts,
        socket_kind=socket_kind,
        session_revision=session_revision,
    )
    return LiveBoardSnapshot(
        observation=observation,
        source=source_bytes,
        session_revision=session_revision,
    )


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
