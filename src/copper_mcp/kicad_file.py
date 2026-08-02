"""Conservative read-only inspection of documented KiCad board files.

This is intentionally not a complete S-expression parser. It provides safe MVP
metadata extraction while the canonical Board IR and KiCad IPC adapter mature.
No write operation should use these regular expressions.
"""

from __future__ import annotations

import hashlib
import re

from copper_mcp.config import Settings
from copper_mcp.models import BoardCounts, BoardManifest
from copper_mcp.security import read_bounded_file, resolve_workspace_file

_VERSION_RE = re.compile(r"\(version\s+(\d+)\)")
_GENERATOR_RE = re.compile(r'\(generator\s+(?:"([^"]+)"|([^\s\)]+))\)')
_FOOTPRINT_RE = re.compile(r"^\s*\(footprint\s", re.MULTILINE)
_NET_RE = re.compile(r'^\s*\(net\s+(\d+)\s+"', re.MULTILINE)
_SEGMENT_RE = re.compile(r"^\s*\(segment\s", re.MULTILINE)
_VIA_RE = re.compile(r"^\s*\(via\s", re.MULTILINE)
_ZONE_RE = re.compile(r"^\s*\(zone\s", re.MULTILINE)
_COPPER_LAYER_RE = re.compile(
    r'^\s*\(\d+\s+"(?:F\.Cu|B\.Cu|In\d+\.Cu)"\s+(?:signal|power|mixed|jumper)\b',
    re.MULTILINE,
)


class BoardFormatError(ValueError):
    """Raised when an input is not a supported KiCad board."""


def _first_match(pattern: re.Pattern[str], text: str) -> str | None:
    match = pattern.search(text)
    if match is None:
        return None
    return next((group for group in match.groups() if group is not None), None)


def inspect_kicad_board(requested_path: str, settings: Settings) -> BoardManifest:
    """Create a content-addressed manifest for one board inside the workspace."""

    board_path = resolve_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".kicad_pcb"},
        max_bytes=settings.max_board_bytes,
    )
    payload = read_bounded_file(board_path, max_bytes=settings.max_board_bytes)
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise BoardFormatError("KiCad board must be valid UTF-8") from error
    if not text.lstrip().startswith("(kicad_pcb"):
        raise BoardFormatError("file does not begin with the kicad_pcb token")

    revision_digest = hashlib.sha256(payload).hexdigest()
    relative_path = board_path.relative_to(settings.workspace).as_posix()
    net_ids = {int(match) for match in _NET_RE.findall(text)}
    net_ids.discard(0)
    counts = BoardCounts(
        copper_layers=len(_COPPER_LAYER_RE.findall(text)),
        footprints=len(_FOOTPRINT_RE.findall(text)),
        nets=len(net_ids),
        segments=len(_SEGMENT_RE.findall(text)),
        vias=len(_VIA_RE.findall(text)),
        zones=len(_ZONE_RE.findall(text)),
    )
    return BoardManifest(
        board_id=f"board:{revision_digest[:16]}",
        revision=f"sha256:{revision_digest}",
        relative_path=relative_path,
        format="kicad_pcb",
        size_bytes=len(payload),
        counts=counts,
        source_version=_first_match(_VERSION_RE, text),
        source_generator=_first_match(_GENERATOR_RE, text),
    )


def load_json_file(
    requested_path: str, settings: Settings, *, max_bytes: int = 5 * 1024 * 1024
) -> bytes:
    """Read a bounded JSON artifact from the configured workspace."""

    artifact = resolve_workspace_file(
        settings.workspace,
        requested_path,
        allowed_suffixes={".json"},
        max_bytes=max_bytes,
    )
    return read_bounded_file(artifact, max_bytes=max_bytes)
