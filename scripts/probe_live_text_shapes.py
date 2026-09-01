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
import time
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

SCHEMA_VERSION = "0.2.0"
_TIMEOUT_MS = 10_000
_REPEATS = 3
# The largest board committed to this repository is ~162 KiB. This cap is deliberately about two
# orders of magnitude above that: it exists to stop `--board /dev/zero` and an unbounded read, not
# to express a policy about board size. It is charged against `st_size` from an `lstat` taken
# before any byte is read, and the refusal names it so an operator who hits it knows the number.
MAX_BOARD_FILE_BYTES = 16 * 1024 * 1024
# A bound on the live text census. The instrument issues several IPC calls per text item, so an
# editor holding an adversarial number of root texts would otherwise turn one probe into an
# unbounded run. Exceeding this refuses; it never truncates.
MAX_TEXT_ITEMS = 256
# A wall-clock budget across *every* IPC call this instrument makes. The client's per-call
# `timeout_ms` bounds one request and says nothing about a loop of them, which is exactly the gap
# a slow font path or a large census walks through.
MAX_PROBE_SECONDS = 180.0
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


class Budget:
    """One wall-clock budget shared by every IPC call in a run.

    The client's ``timeout_ms`` bounds a single request. Nothing bounds the *sequence*, and this
    instrument issues several calls per text item plus four surface probes. A budget that refuses
    is the only honest option here: truncating the census silently would publish an artifact that
    reads as complete, which is the failure mode the no-silent-caps rule exists to prevent. So the
    refusal names what had been measured and what had not, and no artifact is written.
    """

    def __init__(self, seconds: float = MAX_PROBE_SECONDS, clock: Any = None) -> None:
        self._clock = clock or time.monotonic
        self._seconds = seconds
        self._start = self._clock()
        self.calls = 0

    @property
    def elapsed(self) -> float:
        return float(self._clock() - self._start)

    def check(self, measured: str, unmeasured: str) -> None:
        """Charge one IPC call against the budget, or refuse naming both sides of the boundary."""

        self.calls += 1
        if self.elapsed > self._seconds:
            raise ProbeRefusal(
                f"REFUSED: the live probe exceeded its {self._seconds:.0f}s wall-clock budget "
                f"after {self.calls} IPC call(s) and {self.elapsed:.1f}s. "
                f"Measured before the stop: {measured}. NOT measured: {unmeasured}. "
                "No artifact is published: a truncated census would read as a complete one."
            )


def validate_board_argument(argument: Path, root: Path) -> tuple[Path, Path]:
    """Validate ``--board`` completely **before** a single byte of it is read.

    Order is the whole point and it is the order below. ``read_bytes()`` on an unvalidated path is
    how ``--board /dev/zero`` exhausts memory and how a FIFO blocks forever, so every check that
    can refuse has to happen in front of the read rather than after it. The size is charged from
    the same ``lstat`` that proves the file is regular, so nothing is opened to measure it.

    Containment is checked twice on purpose. The lexical check (``abspath``, which normalizes
    ``..`` without following links) answers "does the argument *name* a path inside the tree", and
    the ``realpath`` check answers "does it *resolve* to one" -- which is what catches a symlinked
    parent directory, since ``lstat`` only declines to follow the final component.

    Returns the validated absolute path and its repository-relative form.
    """

    # `abspath` is deliberate and `resolve()` would be wrong: this first check must be purely
    # lexical so that it answers "does the argument *name* a path inside the tree". The
    # symlink question is asked separately, immediately below, via `realpath`.
    candidate = Path(os.path.abspath(argument))  # noqa: PTH100
    try:
        relative = candidate.relative_to(root)
    except ValueError as error:
        raise ProbeRefusal(
            "REFUSED: --board must name a path inside the repository working tree"
        ) from error
    if Path(os.path.realpath(candidate)) != candidate:
        raise ProbeRefusal(
            "REFUSED: --board must not reach its target through a symbolic link; "
            "the resolved path differs from the named one"
        )
    try:
        info = candidate.lstat()
    except OSError as error:
        raise ProbeRefusal("REFUSED: --board must name an existing file") from error
    if stat.S_ISLNK(info.st_mode):
        raise ProbeRefusal("REFUSED: --board must not be a symbolic link")
    if not stat.S_ISREG(info.st_mode):
        raise ProbeRefusal(
            "REFUSED: --board must be a regular file; a device, FIFO, socket or directory "
            "is refused before it is opened"
        )
    if info.st_size == 0:
        raise ProbeRefusal("REFUSED: --board is empty")
    if info.st_size > MAX_BOARD_FILE_BYTES:
        raise ProbeRefusal(
            f"REFUSED: --board is {info.st_size} bytes, over this instrument's "
            f"{MAX_BOARD_FILE_BYTES} byte cap; no byte of it has been read"
        )
    git = shutil.which("git") or "git"
    tracked = subprocess.run(  # noqa: S603
        [git, "-C", str(root), "ls-files", "--error-unmatch", str(relative)],
        check=False,
        capture_output=True,
        text=True,
    )
    if tracked.returncode != 0:
        raise ProbeRefusal(
            "REFUSED: --board must be a git-tracked file; the artifact labels it "
            "'the committed board' and an untracked path cannot carry that label"
        )
    return candidate, relative


def bind_live_revision(settings: Settings) -> dict[str, str]:
    """Read the identity every measurement in this run must belong to.

    ``inspect_live_board`` already confirms the board is stable *within its own call*. What that
    cannot do is tie a later call to an earlier one, and this probe makes dozens of calls across
    several minutes: an operator nudging a track, or closing and reopening KiCad, would otherwise
    let one artifact combine observations from two revisions or two sessions. That artifact would
    be exactly the unbindable evidence D-231 warns about, so the binding is checked rather than
    assumed. ``session_revision`` is included because a board digest alone cannot see a restart
    that reopened the same unmodified file.
    """

    observation = inspect_live_board(settings, timeout_ms=_TIMEOUT_MS)
    return {
        "board_digest": observation.board_digest,
        "session_revision": observation.session_revision or "unavailable",
    }


def require_same_revision(
    expected: dict[str, str], actual: dict[str, str], *, checkpoint: str
) -> None:
    """Refuse unless a later reading of the live identity equals the one measurement began with."""

    if actual == expected:
        return
    moved = sorted(key for key in expected if expected[key] != actual.get(key))
    raise ProbeRefusal(
        f"REFUSED: the live board revision moved during measurement (checkpoint '{checkpoint}'; "
        f"changed: {', '.join(moved)}). The editor was modified, saved, or restarted mid-probe. "
        "No artifact is published, because one artifact may only describe one revision."
    )


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


def _copper_mcp_surfaces(settings: Settings, budget: Budget) -> dict[str, Any]:
    """Record what each of CopperMCP's three live surfaces does with this real editor."""

    surfaces: dict[str, Any] = {}

    # 1. The MCP-facing bounded observation, on its default path (no escape hatch).
    budget.check("revision binding", "all four live surfaces")
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

    # 2. The same surface again.  Before ADR-0128 this second probe used the non-MCP
    #    `allow_future_api` escape hatch and was the ONLY surface that observed anything.
    #    The flag is retired: the declared window now governs both probes, so 1 and 2 differ
    #    only in that this one is charged against the budget after the first has run.
    budget.check("the default observation surface", "three remaining live surfaces")
    try:
        observation = inspect_live_board(settings, timeout_ms=_TIMEOUT_MS)
        surfaces["inspect_live_board_policy_window"] = {
            "verdict": "observed",
            "observation": observation.to_dict(),
        }
    except KicadIpcError as error:
        surfaces["inspect_live_board_policy_window"] = {
            "verdict": "refused",
            "error_type": type(error).__name__,
            "message": str(error),
        }

    # 3. The capability oracle, which requires the KiCad-launched plugin environment.
    budget.check("both observation surfaces", "the oracle and editor-context surfaces")
    oracle = probe_live_kicad_ipc(settings, timeout_ms=_TIMEOUT_MS)
    surfaces["probe_live_kicad_ipc"] = {
        "verdict": oracle.status,
        "result": oracle.to_dict(),
    }

    # 4. The editor-context surface: internal capture, then the MCP-shaped compare-and-swap.
    budget.check("observation and oracle surfaces", "the editor-context surface")
    try:
        snapshot = capture_live_editor_context(settings, timeout_ms=_TIMEOUT_MS)
        surfaces["capture_live_editor_context_policy_window"] = {
            "verdict": "observed",
            "board_revision": snapshot.board_digest,
            "board_bytes": snapshot.board_bytes,
            "active_layer_index": snapshot.active_layer_index,
            "active_layer_name": snapshot.active_layer_name,
            "selection_count": len(snapshot.selection),
        }
        revision = snapshot.board_digest
    except KicadIpcError as error:
        surfaces["capture_live_editor_context_policy_window"] = {
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


def check_text_item_budget(count: int) -> None:
    """Refuse an oversized live text census rather than measuring a truncated prefix of it.

    This instrument issues one IPC call per item and then several more over the whole set, so an
    editor holding an adversarial number of root texts turns one probe into an unbounded run. The
    refusal names the count and the cap: a reader of a capped artifact could not tell a complete
    census from a silently truncated one, so no capped artifact is produced.
    """

    if count > MAX_TEXT_ITEMS:
        raise ProbeRefusal(
            f"REFUSED: the open board carries {count} root text items, over this instrument's "
            f"{MAX_TEXT_ITEMS} item census cap. Nothing is published rather than a truncated "
            "census that would read as a complete one."
        )


def measure_text_shapes(client: Any, budget: Budget) -> dict[str, Any]:
    """Measure ``GetTextAsShapes`` on the live board and on synthetic controls."""

    budget.check("all four live surfaces", "the entire text-to-shape census")
    board = client.get_board()
    board_texts = list(board.get_text())
    if not board_texts:
        raise ProbeRefusal(
            "REFUSED: the open board carries no root text items, so there is nothing to census"
        )
    # Charged before a single shape request is issued, not after the loop has already run.
    check_text_item_budget(len(board_texts))

    # The board accessor returns BoardText/BoardTextBox; the command takes base Text/TextBox.
    # kipy 0.7.1 does not bridge these itself -- see `binding_findings` below.
    base = [
        item.as_text() if type(item).__name__ == "BoardText" else item.as_textbox()
        for item in board_texts
    ]
    template = base[0]

    census = []
    for index, (item, text) in enumerate(zip(board_texts, base, strict=True)):
        budget.check(f"{index} of {len(base)} board text items", "the remaining items and controls")
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

    all_digests = []
    for repeat in range(_REPEATS):
        budget.check(
            f"the board census and {repeat} repeat(s)", "the remaining repeats and controls"
        )
        all_digests.append(_shape_digest(client.get_text_as_shapes(base)))

    variables = []
    for probe in _VARIABLE_PROBES:
        budget.check("the board census and repeats", "the variable, font and glyph controls")
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
        budget.check("the variable controls", "the font, containment and glyph controls")
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
        budget.check("the font controls", "the containment and glyph controls")
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
        budget.check("the containment controls", "the glyph controls")
        result = client.get_text_as_shapes([_text_proto(template, probe)])
        summary = _extent(result)
        glyphs.append(
            {
                "codepoint": f"U+{ord(probe):04X}",
                "shape_count": summary["shape_count"],
                "width_nm": summary["extent_nm"]["x_max"] - summary["extent_nm"]["x_min"],
            }
        )

    budget.check("every control probe", "the response-field inspection")
    response_fields = sorted(
        {
            field.name
            for compound in client.get_text_as_shapes([template])
            for shape in compound._proto.shapes
            for field, _ in shape.ListFields()
        }
    )

    return {
        "budget": {
            "max_text_items": MAX_TEXT_ITEMS,
            "board_text_items": len(base),
            "max_probe_seconds": MAX_PROBE_SECONDS,
            "truncated": False,
        },
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
        # Every check that can refuse happens in front of the read, never after it.
        board, board_relative = validate_board_argument(args.board, root)
        board_bytes = board.read_bytes()

        client, version = _require_live_session()
        budget = Budget()

        # Bind every measurement below to one board revision and one editor session.
        revision = bind_live_revision(settings)
        surfaces = _copper_mcp_surfaces(settings, budget)
        require_same_revision(revision, bind_live_revision(settings), checkpoint="after_surfaces")
        shapes = measure_text_shapes(client, budget)
        require_same_revision(revision, bind_live_revision(settings), checkpoint="after_census")

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
                "path": str(board_relative),
                "digest": "sha256:" + hashlib.sha256(board_bytes).hexdigest(),
                "bytes": len(board_bytes),
            },
            # Every measurement in this artifact belongs to this one revision and session, and
            # that is checked at each checkpoint rather than assumed -- see `bind_live_revision`.
            "revision_binding": {
                "board_digest": revision["board_digest"],
                "session_revision": revision["session_revision"],
                "checkpoints": ["after_surfaces", "after_census"],
                "stable_across_measurement": True,
            },
            "budget": {
                "max_probe_seconds": MAX_PROBE_SECONDS,
                "ipc_calls_charged": budget.calls,
                "elapsed_seconds": round(budget.elapsed, 3),
                "exhausted": False,
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
