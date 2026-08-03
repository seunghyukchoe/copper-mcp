"""Byte-preserving span edits over an already-parsed KiCad S-expression.

`parse_sexpr` produces a tree that records where each expression *starts* but not where it
ends, which is half of what a partial edit needs. This module supplies the other half and the
splice operation built on it, so an edit can change one region of a board and leave every other
byte exactly as its author wrote it.

**Offsets here are character indices into the decoded text, not byte offsets.** `parse_sexpr`
decodes strictly (`sexpr.parse_sexpr`) and `SExpr.offset` counts characters, so a board
containing any non-ASCII character - both of this repository's reference boards do, an em-dash
in CopperTone and a `µ` in the Board IR subset fixture - has byte and character offsets that
disagree. Treating one as the other would corrupt exactly the boards we test against.

Working in the character domain is nonetheless byte-exact, because strict UTF-8 decoding is
injective: `source.decode("utf-8").encode("utf-8") == source` for every input `parse_sexpr`
accepts. So the discipline is: decode once, splice in characters, encode once. Callers should
use `splice_source` rather than assembling that themselves.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import pairwise

from copper_mcp.adapters.sexpr import SExpr


class CstError(ValueError):
    """Raised when a span cannot be determined or a splice set is not applicable."""


def expression_end(text: str, start: int) -> int:
    """Return the exclusive end of one already-validated S-expression.

    Quote- and escape-aware, because a `")"` inside a quoted atom is payload rather than a
    closing delimiter and a naive depth count would end the expression in the wrong place.
    """

    if start < 0 or start >= len(text) or text[start] != "(":
        raise CstError("expression does not start at the supplied position")
    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(text)):
        character = text[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character == "(":
            depth += 1
        elif character == ")":
            depth -= 1
            if depth == 0:
                return index + 1
    raise CstError("expression has no closing delimiter")


def span(node: SExpr, text: str) -> tuple[int, int]:
    """Return the half-open character span an expression occupies in its source."""

    if not isinstance(node, SExpr):
        raise CstError("a span can only be taken of an expression")
    return node.offset, expression_end(text, node.offset)


def line_indent(text: str, offset: int) -> str:
    """Return the indentation of the line an offset sits on, or a two-space default."""

    line_start = text.rfind("\n", 0, offset) + 1
    indentation = text[line_start:offset]
    return indentation if indentation and not indentation.strip(" \t") else "  "


@dataclass(frozen=True, slots=True)
class Splice:
    """Replace the half-open character range ``[start, end)`` with ``replacement``.

    An insertion is the degenerate case where ``start == end``; nothing is removed and the
    replacement is placed at that point.
    """

    start: int
    end: int
    replacement: str

    def __post_init__(self) -> None:
        if not isinstance(self.replacement, str):
            raise CstError("a splice replacement must be text")
        if self.start < 0 or self.end < self.start:
            raise CstError("a splice range must be ordered and non-negative")

    @property
    def is_insertion(self) -> bool:
        return self.start == self.end


def apply_splices(text: str, splices: Iterable[Splice]) -> str:
    """Apply every splice to ``text``, leaving all other characters untouched.

    Splices are applied from the highest offset downwards so that each one's coordinates still
    refer to the original text when its turn comes; applying forwards would shift every later
    offset by the length delta of the edits before it.

    Overlapping splices are **rejected outright** rather than resolved. Two edits claiming the
    same region have no well-defined combined meaning, and any rule this function invented for
    them - last wins, longest wins, merge - would be a silent guess about intent. Insertions at
    the same point are equally ambiguous in ordering and are refused for the same reason.
    """

    if not isinstance(text, str):
        raise CstError("splices apply to decoded text, not bytes")
    ordered = sorted(splices, key=lambda item: (item.start, item.end))
    for item in ordered:
        if item.end > len(text):
            raise CstError("a splice range extends past the end of the source")
    for earlier, later in pairwise(ordered):
        if later.start < earlier.end:
            raise CstError("splice ranges overlap")
        if later.start == earlier.start and earlier.is_insertion and later.is_insertion:
            raise CstError("two insertions claim the same position")
    result = text
    for item in reversed(ordered):
        result = result[: item.start] + item.replacement + result[item.end :]
    return result


def splice_source(source: bytes, splices: Iterable[Splice]) -> bytes:
    """Decode, splice, and re-encode in one step so callers never hold the two domains apart.

    Untouched regions come back bit-identical: strict UTF-8 round-trips exactly, and every
    character outside a splice range is copied rather than re-rendered.
    """

    if not isinstance(source, bytes):
        raise CstError("a source must be immutable bytes")
    try:
        text = source.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise CstError("source must be valid UTF-8") from error
    return apply_splices(text, splices).encode("utf-8", errors="strict")


def root_close_offset(text: str) -> int:
    """Return the position of the root expression's closing delimiter.

    This is where purely additive board content goes. It is the only insertion point that
    modifies no existing span at all, which is what makes "every untouched byte is identical"
    trivially true rather than an assertion over many edited regions.
    """

    stripped = text.rstrip(" \t\r\n")
    if not stripped or stripped[-1] != ")":
        raise CstError("source has no root closing delimiter")
    return len(stripped) - 1


__all__ = [
    "CstError",
    "Splice",
    "apply_splices",
    "expression_end",
    "line_indent",
    "root_close_offset",
    "span",
    "splice_source",
]
