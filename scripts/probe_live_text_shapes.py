#!/usr/bin/env python3
"""Observe one real KiCad editor through CopperMCP's own transport, and measure text-to-shape.

This instrument does two things against **one** live editor session, and both are read-only.

**Part 1 -- the M3 entry-criterion E3 observation.** The audit's E3 asks for "one real-editor IPC
observation".  It is taken through CopperMCP's own surfaces rather than through raw ``kipy``, so
what is recorded is what *this project's transport* binds and returns, not what the binding is
capable of in principle.  All three live surfaces are exercised and each verdict is recorded --
including the refusals, which are the substance of the observation rather than a failure of it.

**Part 2 -- the ADR-0095 text-to-shape probe.** ADR-0095 refuses copper ``gr_text``/``gr_text_box``
because no envelope containing the plotted copper is derivable from the board document, and lists
five conditions that would have to become true.  The research note's section 5.2 recorded
``GetTextAsShapes`` as a *lead*: "with board context available, KiCad can expand text and expose
GetTextAsShapes".  This probe measures the real call -- ``kipy.kicad.KiCad.get_text_as_shapes``,
which sends a ``GetTextAsShapes`` command and returns ``CompoundShape`` per input -- and asks which
of the five conditions it actually moves.

**Redaction.** No board text string is ever written to the artifact.  Board-resident text items are
identified by document order and reported as character counts and shape counts only.  Synthetic
probe strings are this instrument's own input, not board content, and are published verbatim so the
measurement is reproducible.

**Refusal when there is no session.** Every path here needs a running editor.  With none, the
instrument refuses loudly and by name rather than publishing an artifact of absences, exactly as
the census instruments refuse an unusable corpus.  A ``skipped`` artifact would be worse than no
artifact: it would look like evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import stat
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from copper_mcp.config import Settings
from copper_mcp.kicad_ipc import (
    KicadIpcError,
    capture_live_editor_context,
    inspect_live_board,
)
from copper_mcp.kicad_ipc_oracle import probe_live_kicad_ipc
from copper_mcp.live_editor_context import (
    LiveEditorContextError,
    inspect_live_editor_context_raw,
)

SCHEMA_VERSION = "0.1.0"
_TIMEOUT_MS = 10_000
_REPEATS = 3
# Synthetic probe strings. Every one is this instrument's own input; none comes from a board.
_VARIABLE_PROBES = (
    "${CURRENT_DATE}",
    "${PROJECTNAME}",
    "${FILENAME}",
    "${MYVAR}",
    "AAAAAAAAAAAAAA",
    "AAAAAAAAAA",
    "AAAAAAAA",
)
_FACE_PROBES = (None, "Helvetica", "NoSuchFaceXYZ", "Times New Roman", "Courier")
_CONTAINMENT_PROBES = ("ABC", "(g)pqy", "~{ABC}")
_GLYPH_PROBES = ("m", "—", "Ж", "漢")


class ProbeRefusal(SystemExit):
    """Raised, loudly, when this instrument cannot honestly measure anything."""


@dataclass(frozen=True, slots=True)
class OutputTarget:
    path: Path
    parent_fd: int

    def close(self) -> None:
        if self.parent_fd >= 0:
            os.close(self.parent_fd)


def _require_live_session() -> Any:
    """Return a connected raw client, or refuse by name.

    The raw binding is loaded here for the census half only. Part 1 never uses it: the observation
    is taken through CopperMCP's surfaces, and this connection exists so that a missing editor is
    reported as a refusal before any measurement claims to have happened.
    """

    try:
        import kipy
    except ModuleNotFoundError as error:  # pragma: no cover - environment refusal
        raise ProbeRefusal(
            "REFUSED: kicad-python (kipy) is not installed; this instrument needs a live editor"
        ) from error
    try:
        client = kipy.KiCad(timeout_ms=_TIMEOUT_MS)
        version = client.get_version()
        client.get_board()
    except Exception as error:  # any failure to reach the editor is the same refusal
        raise ProbeRefusal(
            "REFUSED: no running KiCad PCB editor answered the local IPC socket "
            f"({type(error).__name__}). Start KiCad, open a board, enable the IPC API server, "
            "and re-run. This instrument does not publish an artifact without a live session."
        ) from error
    return client, version


def _copper_mcp_surfaces(settings: Settings) -> dict[str, Any]:
    """Record what each of CopperMCP's three live surfaces does with this real editor."""

    surfaces: dict[str, Any] = {}

    # 1. The MCP-facing bounded observation, on its default path (no escape hatch).
    try:
        observation = inspect_live_board(settings, timeout_ms=_TIMEOUT_MS)
        surfaces["inspect_live_board_default"] = {
            "verdict": "observed",
            "observation": observation.to_dict(),
        }
    except KicadIpcError as error:
        surfaces["inspect_live_board_default"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }

    # 2. The same surface through the documented, deliberately non-MCP development escape hatch.
    try:
        observation = inspect_live_board(settings, allow_future_api=True, timeout_ms=_TIMEOUT_MS)
        surfaces["inspect_live_board_allow_future_api"] = {
            "verdict": "observed",
            "observation": observation.to_dict(),
        }
    except KicadIpcError as error:
        surfaces["inspect_live_board_allow_future_api"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }

    # 3. The capability oracle, which requires the KiCad-launched plugin environment.
    oracle = probe_live_kicad_ipc(settings, timeout_ms=_TIMEOUT_MS)
    surfaces["probe_live_kicad_ipc"] = {
        "verdict": oracle.status,
        "result": oracle.to_dict(),
    }

    # 4. The editor-context surface: internal capture, then the MCP-shaped compare-and-swap.
    try:
        snapshot = capture_live_editor_context(
            settings, allow_future_api=True, timeout_ms=_TIMEOUT_MS
        )
        surfaces["capture_live_editor_context_allow_future_api"] = {
            "verdict": "observed",
            "board_revision": snapshot.board_digest,
            "board_bytes": snapshot.board_bytes,
            "active_layer_index": snapshot.active_layer_index,
            "active_layer_name": snapshot.active_layer_name,
            "selection_count": len(snapshot.selection),
        }
        revision = snapshot.board_digest
    except KicadIpcError as error:
        surfaces["capture_live_editor_context_allow_future_api"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }
        revision = None

    if revision is not None:
        try:
            context = inspect_live_editor_context_raw(
                {"board": "live", "expect_board_revision": revision}, settings
            )
            surfaces["inspect_live_editor_context_default"] = {
                "verdict": "observed",
                "context": context.to_dict(),
            }
        except (KicadIpcError, LiveEditorContextError) as error:
            surfaces["inspect_live_editor_context_default"] = {
                "verdict": "refused",
                "error_type": type(error).__name__,
                "message": str(error),
            }
    return surfaces


def _shape_digest(compounds: Any) -> str:
    """Digest the returned geometry deterministically, over the wire protos themselves."""

    digest = hashlib.sha256()
    for compound in compounds:
        digest.update(compound._proto.SerializeToString(deterministic=True))
    return "sha256:" + digest.hexdigest()


def _extent(compounds: Any) -> dict[str, Any]:
    """Summarize returned geometry: shape kinds, pen widths, and a pen-inclusive extent."""

    kinds: dict[str, int] = {}
    xs: list[int] = []
    ys: list[int] = []
    widths: set[int] = set()
    for compound in compounds:
        for shape in compound.shapes:
            name = type(shape).__name__
            kinds[name] = kinds.get(name, 0) + 1
            widths.add(int(shape.attributes.stroke.width))
            if name == "Segment":
                xs += [shape.start.x, shape.end.x]
                ys += [shape.start.y, shape.end.y]
            elif name == "Polygon":
                for polygon in shape.polygons:
                    for node in polygon.outline:
                        if node.has_point:
                            xs.append(node.point.x)
                            ys.append(node.point.y)
    half = max(widths) // 2 if widths else 0
    return {
        "shape_kinds": dict(sorted(kinds.items())),
        "shape_count": sum(kinds.values()),
        "stroke_widths_nm": sorted(widths),
        "extent_nm": (
            {
                "x_min": min(xs) - half,
                "x_max": max(xs) + half,
                "y_min": min(ys) - half,
                "y_max": max(ys) + half,
            }
            if xs
            else None
        ),
    }


def _text_proto(template: Any, value: str, face: str | None = None) -> Any:
    from kipy.common_types import Text

    text = Text()
    text._proto.CopyFrom(template.proto)
    text.value = value
    if face is not None:
        text._proto.attributes.font_name = face
    return text


def measure_text_shapes(client: Any) -> dict[str, Any]:
    """Measure ``GetTextAsShapes`` on the live board and on synthetic controls."""

    board = client.get_board()
    board_texts = list(board.get_text())
    if not board_texts:
        raise ProbeRefusal(
            "REFUSED: the open board carries no root text items, so there is nothing to census"
        )

    # The board accessor returns BoardText/BoardTextBox; the command takes base Text/TextBox.
    # kipy 0.7.1 does not bridge these itself -- see `binding_findings` below.
    base = [
        item.as_text() if type(item).__name__ == "BoardText" else item.as_textbox()
        for item in board_texts
    ]
    template = base[0]

    census = []
    for index, (item, text) in enumerate(zip(board_texts, base, strict=True)):
        single = client.get_text_as_shapes([text])
        census.append(
            {
                "document_order": index,
                "wrapper": type(item).__name__,
                "layer_index": int(item.layer),
                "character_count": len(text.value),
                "non_ascii_characters": sum(1 for c in text.value if ord(c) > 127),
                **_extent(single),
            }
        )

    all_digests = [_shape_digest(client.get_text_as_shapes(base)) for _ in range(_REPEATS)]

    variables = []
    for probe in _VARIABLE_PROBES:
        result = client.get_text_as_shapes([_text_proto(template, probe)])
        summary = _extent(result)
        variables.append(
            {
                "probe": probe,
                "character_count": len(probe),
                "shape_count": summary["shape_count"],
                "width_nm": (
                    summary["extent_nm"]["x_max"] - summary["extent_nm"]["x_min"]
                    if summary["extent_nm"]
                    else None
                ),
                "digest": _shape_digest(result),
            }
        )

    faces = []
    for face in _FACE_PROBES:
        result = client.get_text_as_shapes([_text_proto(template, "mmmm", face)])
        summary = _extent(result)
        faces.append(
            {
                "face": face,
                "shape_kinds": summary["shape_kinds"],
                "shape_count": summary["shape_count"],
                "width_nm": (
                    summary["extent_nm"]["x_max"] - summary["extent_nm"]["x_min"]
                    if summary["extent_nm"]
                    else None
                ),
                "digest": _shape_digest(result),
            }
        )

    containment = []
    for probe in _CONTAINMENT_PROBES:
        text = _text_proto(template, probe)
        result = client.get_text_as_shapes([text])
        summary = _extent(result)
        extents = client.get_text_extents(text)
        box_top, box_bottom = extents.pos.y, extents.pos.y + extents.size.y
        containment.append(
            {
                "probe": probe,
                "copper_y_min_nm": summary["extent_nm"]["y_min"],
                "copper_y_max_nm": summary["extent_nm"]["y_max"],
                "extents_y_min_nm": box_top,
                "extents_y_max_nm": box_bottom,
                # Positive means plotted copper leaves KiCad's own reported box.
                "overflow_above_nm": box_top - summary["extent_nm"]["y_min"],
                "overflow_below_nm": summary["extent_nm"]["y_max"] - box_bottom,
            }
        )

    glyphs = []
    for probe in _GLYPH_PROBES:
        result = client.get_text_as_shapes([_text_proto(template, probe)])
        summary = _extent(result)
        glyphs.append(
            {
                "codepoint": f"U+{ord(probe):04X}",
                "shape_count": summary["shape_count"],
                "width_nm": summary["extent_nm"]["x_max"] - summary["extent_nm"]["x_min"],
            }
        )

    response_fields = sorted(
        {
            field.name
            for compound in client.get_text_as_shapes([template])
            for shape in compound._proto.shapes
            for field, _ in shape.ListFields()
        }
    )

    return {
        "api_symbol": "kipy.kicad.KiCad.get_text_as_shapes",
        "wire_command": "kiapi.common.commands.GetTextAsShapes",
        "board_text_census": census,
        "repeated_agreement": {
            "calls": _REPEATS,
            "digests": all_digests,
            "agrees": len(set(all_digests)) == 1,
        },
        "text_variable_expansion": variables,
        "font_dependence": faces,
        "extents_containment": containment,
        "glyph_widths": glyphs,
        "response_graphic_shape_fields": response_fields,
    }


def _open_output_parent(parent: Path) -> int:
    if os.open not in os.supports_dir_fd:
        raise ProbeRefusal("platform must support anchored output creation")
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    try:
        expected = parent.stat(follow_symlinks=False)
    except OSError as error:
        raise ProbeRefusal("output parent must be an existing directory") from error
    if not stat.S_ISDIR(expected.st_mode):
        raise ProbeRefusal("output parent must be an existing directory")
    descriptor = -1
    try:
        descriptor = os.open(parent.anchor, flags)
        for component in parent.parts[1:]:
            following = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = following
    except (NotImplementedError, OSError) as error:
        if descriptor >= 0:
            os.close(descriptor)
        raise ProbeRefusal("output parent must be an anchored no-follow directory") from error
    actual = os.fstat(descriptor)
    if (actual.st_dev, actual.st_ino) != (expected.st_dev, expected.st_ino):
        os.close(descriptor)
        raise ProbeRefusal("output parent changed during validation")
    return descriptor


def _write_output(target: OutputTarget, payload: str) -> None:
    """Publish a complete artifact or none at all, create-only and never following a link."""

    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    staged = f".{target.path.name}.{os.getpid()}.{os.urandom(8).hex()}.tmp"
    try:
        descriptor = os.open(staged, flags, 0o600, dir_fd=target.parent_fd)
    except (NotImplementedError, OSError) as error:
        raise ProbeRefusal("output could not be created safely") from error
    try:
        stream = os.fdopen(descriptor, "w", encoding="utf-8")
        with stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(
                staged,
                target.path.name,
                src_dir_fd=target.parent_fd,
                dst_dir_fd=target.parent_fd,
            )
        except FileExistsError as error:
            raise ProbeRefusal("output must remain a new path") from error
        except (NotImplementedError, OSError) as error:
            raise ProbeRefusal("output could not be published safely") from error
    finally:
        try:
            os.unlink(staged, dir_fd=target.parent_fd)
        except OSError:
            pass


def _git_state(root: Path) -> tuple[str, bool]:
    git = shutil.which("git") or "git"
    commit = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "rev-parse", "HEAD"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    dirty = bool(
        subprocess.run(  # noqa: S603
            [git, "-C", str(root), "status", "--porcelain"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
    )
    return commit, dirty


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--board", required=True, type=Path, help="committed board, digested only")
    args = parser.parse_args()

    output = args.output.resolve()
    if output.exists():
        raise ProbeRefusal("output must remain a new path")
    target = OutputTarget(path=output, parent_fd=_open_output_parent(output.parent))
    runner = Path(__file__).resolve()
    root = runner.parents[1]

    try:
        settings = Settings.from_env()
        if not settings.allow_live_ipc:
            raise ProbeRefusal(
                "REFUSED: live IPC observation is disabled; set COPPER_MCP_ALLOW_LIVE_IPC=1. "
                "This is the operator gate and this instrument will not bypass it."
            )
        commit, dirty = _git_state(root)
        runner_bytes = runner.read_bytes()
        board = args.board.resolve()
        board_bytes = board.read_bytes()

        client, version = _require_live_session()
        surfaces = _copper_mcp_surfaces(settings)
        shapes = measure_text_shapes(client)

        final_commit, final_dirty = _git_state(root)
        if final_commit != commit or runner.read_bytes() != runner_bytes:
            raise ProbeRefusal("measurement inputs changed during run")

        result: dict[str, Any] = {
            "schema_version": SCHEMA_VERSION,
            "instrument": "live_editor_observation_and_text_to_shape_probe",
            "commit": commit,
            "dirty_at_start": dirty,
            "dirty_at_end": final_dirty,
            "recorded_at_utc": datetime.now(UTC).isoformat(timespec="seconds"),
            "runner_digest": "sha256:" + hashlib.sha256(runner_bytes).hexdigest(),
            "environment": {
                "platform": platform.platform(),
                "python": platform.python_version(),
                "kicad_reported_version": str(version),
                "kipy_version": _kipy_version(),
                "kicad_api_socket_configured": bool(os.environ.get("KICAD_API_SOCKET")),
                "kicad_api_token_configured": bool(os.environ.get("KICAD_API_TOKEN")),
            },
            "committed_board": {
                "path": str(board.relative_to(root)),
                "digest": "sha256:" + hashlib.sha256(board_bytes).hexdigest(),
                "bytes": len(board_bytes),
            },
            "copper_mcp_surfaces": surfaces,
            "text_to_shape": shapes,
            "binding_findings": [
                "kipy 0.7.1 KiCad.get_text_as_shapes accepts common_types.Text/TextBox only; "
                "Board.get_text() returns BoardText/BoardTextBox, which are not subclasses. "
                "Passing the board's own objects falls through the isinstance check into the "
                "TextBox branch and raises a CopyFrom TypeError naming the wrong type. The "
                "caller must bridge with BoardText.as_text() / BoardTextBox.as_textbox().",
                "GetTextAsShapes carries no document or project reference, and the response "
                "carries no font, font-build, or KiCad-build identity.",
            ],
            "committed_board_text_strings": 0,
            "operation": "read_only_live_observation_and_text_shape_census",
            "not_claimed": [
                "no board write, editor mutation, apply authority, or undo transaction",
                "no copper text support, and no change to ADR-0095's refusal",
                "no offline capability: every measurement here needs a live editor session",
                "not a corpus result: one board, one session, one KiCad build, one host",
                "no routing, DRC, placement, fabrication, or conversion result",
            ],
        }
        canonical = json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False)
        result["run_id"] = "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()
        _write_output(target, json.dumps(result, sort_keys=True, indent=2) + "\n")
        return 0
    finally:
        target.close()


def _kipy_version() -> str:
    try:
        from importlib.metadata import version

        return version("kicad-python")
    except Exception:
        return "unknown"


if __name__ == "__main__":
    raise SystemExit(main())
