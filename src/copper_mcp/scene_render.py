"""Deterministic canonicalization of a KiCad SVG board export.

This module is pure: it runs no subprocess and touches no file. KiCad execution lives in
``kicad_cli`` for the same reason zone fill is split that way — the part that has to be
argued about correctness should be testable without spawning anything.

**Measured against KiCad 10.0.5.** Two exports of an unchanged board taken three seconds
apart differ in exactly one line: the ``<title>`` element, which embeds both a wall-clock
timestamp and the output filename. Everything else — path data, ordering, viewBox, style
attributes — is byte-identical. Neutralising that single line is therefore sufficient to
make the export content-addressable, and doing anything more would be unjustified rewriting
of bytes KiCad is authoritative for.
"""

from __future__ import annotations

import hashlib
import re
import time
from collections.abc import Callable
from dataclasses import dataclass

from copper_mcp.artifact_store import ArtifactUnavailableError, BoundedArtifactStore

#: Identifier recorded in evidence so a digest can never be compared across rules.
SVG_CANONICALIZATION = "title-line-v1"

#: The model-facing artifact. Copper plus the board outline and nothing else — see
#: ``RENDER_LAYER_RATIONALE`` for why this list is a security control rather than a
#: presentation choice.
RENDER_LAYERS: tuple[str, ...] = ("F.Cu", "B.Cu", "Edge.Cuts")

RENDER_LAYER_RATIONALE = (
    "Silkscreen and fabrication layers are excluded because they carry board-author text. "
    "Measured on KiCad 10.0.5: an export including F.SilkS and F.Fab embeds each string "
    "twice in literal, greppable form - once in a <desc> beside the stroked paths and once "
    'in an invisible <text opacity="0"> element - so the strings are not merely drawn, they '
    "are readable. Excluding the layers is the only control that keeps them out; filtering "
    "<text> nodes after the fact would leave the <desc> copy behind."
)

#: The exact line KiCad 10.0.5 emits. Anchored deliberately: a format change must be a loud
#: failure, not a silently unnormalized digest.
_TITLE_LINE = re.compile(
    rb"^<title>SVG Image created as .* date "
    rb"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2} ?</title>$",
    re.MULTILINE,
)

#: What the volatile line becomes. Fixed, and carrying no board-derived text of its own.
_CANONICAL_TITLE = b"<title>copper-mcp deterministic board render</title>"

#: Recognising the already-rewritten line is what makes canonicalization idempotent without
#: loosening the check above. Matching any ``<title>`` at all would be idempotent too, but it
#: would also quietly absorb a future KiCad whose title moved the timestamp somewhere else.
_CANONICAL_TITLE_LINE = re.compile(
    b"^" + re.escape(_CANONICAL_TITLE) + b"$",
    re.MULTILINE,
)


class SceneRenderError(RuntimeError):
    """Raised when a render cannot be produced or canonicalized."""


def canonicalize_svg(source: bytes) -> bytes:
    """Rewrite the one nondeterministic line so equal boards produce equal bytes.

    Fails closed. If the expected title line is absent or appears more than once, the
    assumption this canonicalization rests on no longer holds, and returning the bytes
    unchanged would silently produce a digest that is not reproducible.
    """

    if not isinstance(source, bytes):
        raise SceneRenderError("a render must be canonicalized from bytes")
    # Completeness, before anything else. Measured: when the file-size ceiling is reached,
    # KiCad 10.0.5 does not die on SIGXFSZ - it exits 0 having written a truncated file. The
    # title line sits near the top of the document and survives that truncation, so every
    # other check here would pass and a partial render would be digested as though whole.
    # Requiring the closing tag is what makes a truncated export a refusal.
    if not source.startswith(b"<?xml") or not source.rstrip().endswith(b"</svg>"):
        raise SceneRenderError(
            "the board render is not a complete SVG document; it was probably truncated"
        )
    volatile = len(_TITLE_LINE.findall(source))
    already = len(_CANONICAL_TITLE_LINE.findall(source))
    if volatile + already != 1:
        raise SceneRenderError(
            "the KiCad SVG export does not have the single expected title line, so "
            f"{SVG_CANONICALIZATION} cannot make it reproducible"
        )
    if already:
        return source
    return _TITLE_LINE.sub(_CANONICAL_TITLE, source, count=1)


def render_digest(canonical: bytes) -> str:
    """Digest already-canonical bytes.

    Deliberately does not canonicalize first: a caller that digests raw export bytes has a
    bug, and silently normalizing here would hide it.
    """

    return f"sha256:{hashlib.sha256(canonical).hexdigest()}"


@dataclass(frozen=True, slots=True)
class SceneRenderEvidence:
    """What a render is, and what it is bound to.

    Every field exists so a caller can decide whether two renders are comparable. A digest
    alone cannot answer that: the same board rendered with different layers, a different
    canonicalization rule, or a different KiCad is a different artifact.
    """

    normalized_digest: str
    source_revision: str
    context_revision: str
    kicad_version: str
    layers: tuple[str, ...]
    side: str
    canonicalization: str
    byte_count: int

    def __post_init__(self) -> None:
        for name in ("normalized_digest", "source_revision", "context_revision"):
            value = getattr(self, name)
            if not re.fullmatch(r"sha256:[0-9a-f]{64}", value):
                raise SceneRenderError(f"scene render {name} must be a sha256 digest")
        if self.canonicalization != SVG_CANONICALIZATION:
            raise SceneRenderError("scene render evidence records exactly one canonicalization")
        if not self.layers:
            raise SceneRenderError("a scene render must name the layers it drew")
        if self.side not in {"top", "bottom"}:
            raise SceneRenderError("a scene render side must be top or bottom")
        if self.byte_count < 1:
            raise SceneRenderError("a scene render must have content")
        if not self.kicad_version:
            raise SceneRenderError("a scene render must record the KiCad that produced it")

    def to_dict(self) -> dict[str, object]:
        return {
            "normalized_digest": self.normalized_digest,
            "source_revision": self.source_revision,
            "context_revision": self.context_revision,
            "kicad_version": self.kicad_version,
            "layers": list(self.layers),
            "side": self.side,
            "canonicalization": self.canonicalization,
            "byte_count": self.byte_count,
        }


#: A render store separate from the schematic store, deliberately.
#:
#: Sharing one store would let a 4 MiB render evict schematic capabilities a caller is still
#: holding, and vice versa - two unrelated features silently competing for one budget. The
#: capacity is set so the store can hold its full complement at the per-artifact ceiling
#: (8 x 4 MiB = 32 MiB) rather than being able to promise entries it cannot keep.
#:
#: The TTL matches the schematic store's 15 minutes on purpose. A render is cheaper to
#: recreate than a schematic - about 0.8s for this repository's board - so a shorter life
#: would be defensible, but "a capability expires 15 minutes after it is issued" is a
#: security property that is easier to state and audit as one number than as two.
MAX_SCENE_RENDERS = 8
MAX_SCENE_RENDER_STORE_BYTES = 32 * 1024 * 1024
MAX_SCENE_RENDER_BYTES = 4 * 1024 * 1024
SCENE_RENDER_TTL_SECONDS = 15 * 60
SCENE_RENDER_URI_TEMPLATE = "pcb://artifacts/scene/{token}/board.svg"


class SceneRenderUnavailableError(ArtifactUnavailableError):
    """Raised uniformly for invalid, expired, evicted, or unknown render capabilities."""


class SceneRenderStore(BoundedArtifactStore):
    """Bounded process-local store for deterministic board renders."""

    def __init__(
        self,
        max_artifacts: int = MAX_SCENE_RENDERS,
        max_total_bytes: int = MAX_SCENE_RENDER_STORE_BYTES,
        ttl_seconds: int = SCENE_RENDER_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        super().__init__(
            uri_template=SCENE_RENDER_URI_TEMPLATE,
            max_artifacts=max_artifacts,
            max_total_bytes=max_total_bytes,
            ttl_seconds=ttl_seconds,
            max_artifact_bytes=MAX_SCENE_RENDER_BYTES,
            ceilings=(MAX_SCENE_RENDERS, MAX_SCENE_RENDER_STORE_BYTES, SCENE_RENDER_TTL_SECONDS),
            clock=clock,
            token_factory=token_factory,
        )

    def put(self, canonical: bytes, evidence: SceneRenderEvidence) -> str:
        """Store canonical render bytes under the digest its evidence already claims."""

        if not isinstance(evidence, SceneRenderEvidence):
            raise SceneRenderError("scene render evidence is malformed")
        if render_digest(canonical) != evidence.normalized_digest:
            # The URI would otherwise name bytes the evidence does not describe, which is the
            # one thing a digest-bound artifact must never allow.
            raise SceneRenderError("scene render bytes do not match their evidence digest")
        try:
            return self._store(canonical, evidence.normalized_digest)
        except ValueError as error:
            raise SceneRenderError("the board render exceeds the store byte budget") from error

    def read(self, token: str) -> bytes:
        try:
            return super().read(token)
        except ArtifactUnavailableError as error:
            raise SceneRenderUnavailableError("board render is unavailable") from error
