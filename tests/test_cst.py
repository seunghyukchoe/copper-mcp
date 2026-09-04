"""Byte-preserving span splices.

The property that matters is negative: a splice must change what it was asked to change and
*nothing else*. Every test here is therefore about the bytes it did not touch, not the ones it
did.

Non-ASCII coverage is deliberate rather than incidental. Offsets in this codebase are character
indices, so a board containing any multi-byte character is where a byte/character confusion
shows up - and both of this repository's reference boards contain one.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from copper_mcp.adapters.cst import (
    CstError,
    Splice,
    apply_splices,
    expression_end,
    line_indent,
    root_close_offset,
    span,
    splice_source,
)
from copper_mcp.adapters.sexpr import SExprError, parse_sexpr
from copper_mcp.board_ir import ParseLimits

ROOT = Path(__file__).resolve().parents[1]
BOARDS = sorted((ROOT / "tests" / "fixtures").rglob("*.kicad_pcb"))
COPPERTONE = ROOT / "hardware" / "coppertone-buffer" / "coppertone-buffer.kicad_pcb"
#: Boards known to contain multi-byte characters, where byte and character offsets diverge.
NON_ASCII_BOARDS = [
    ROOT / "tests" / "fixtures" / "board-ir-v0.1" / "subset.kicad_pcb",
    COPPERTONE,
]


class SpanTests(unittest.TestCase):
    def test_a_span_covers_exactly_one_balanced_expression(self) -> None:
        text = '(a (b "x") (c 1))'
        root = parse_sexpr(text.encode(), ParseLimits())
        start, end = span(root, text)
        self.assertEqual(text[start:end], text)
        for child in root.items[1:]:
            if isinstance(child, str):
                continue
            child_start, child_end = span(child, text)
            extract = text[child_start:child_end]
            self.assertTrue(extract.startswith("(") and extract.endswith(")"))
            self.assertEqual(extract.count("("), extract.count(")"))

    def test_a_closing_delimiter_inside_a_quoted_atom_does_not_end_an_expression(self) -> None:
        """The reason this scanner exists rather than a bracket count."""

        text = '(gr_text "a ) b" (layer "F.SilkS"))'
        self.assertEqual(expression_end(text, 0), len(text))

    def test_an_escaped_quote_does_not_end_a_quoted_atom(self) -> None:
        text = '(property "a \\" ) b")'
        self.assertEqual(expression_end(text, 0), len(text))

    def test_a_position_that_is_not_an_expression_is_refused(self) -> None:
        for start in (-1, 1, 99):
            with self.subTest(start=start), self.assertRaises(CstError):
                expression_end("(a)", start)

    def test_an_unterminated_expression_is_refused(self) -> None:
        with self.assertRaises(CstError):
            expression_end("(a (b)", 0)

    def test_line_indent_falls_back_when_a_line_holds_more_than_whitespace(self) -> None:
        self.assertEqual(line_indent("    (a)", 4), "    ")
        self.assertEqual(line_indent("(x) (a)", 4), "  ")


class SpliceTests(unittest.TestCase):
    def test_an_insertion_leaves_both_sides_untouched(self) -> None:
        text = "abcdef"
        self.assertEqual(apply_splices(text, [Splice(3, 3, "XY")]), "abcXYdef")

    def test_splices_apply_against_original_coordinates(self) -> None:
        """Applying forwards would shift every later offset by the earlier edits' delta."""

        text = "0123456789"
        result = apply_splices(
            text, [Splice(0, 1, "AAAA"), Splice(5, 6, "B"), Splice(9, 10, "CCC")]
        )
        self.assertEqual(result, "AAAA1234B678CCC")

    def test_overlapping_splices_are_refused_rather_than_resolved(self) -> None:
        text = "0123456789"
        for first, second, reason in (
            (Splice(0, 5, "a"), Splice(3, 7, "b"), "partial overlap"),
            (Splice(0, 5, "a"), Splice(0, 5, "b"), "identical ranges"),
            (Splice(2, 8, "a"), Splice(3, 4, "b"), "containment"),
            (Splice(4, 4, "a"), Splice(4, 4, "b"), "two insertions at one point"),
        ):
            with self.subTest(reason=reason), self.assertRaises(CstError):
                apply_splices(text, [first, second])

    def test_an_insertion_at_the_edge_of_a_replacement_is_allowed(self) -> None:
        """Adjacent is not overlapping: the ranges are half-open and do not intersect."""

        self.assertEqual(apply_splices("0123", [Splice(0, 2, "A"), Splice(2, 2, "B")]), "AB23")

    def test_a_range_past_the_end_is_refused(self) -> None:
        with self.assertRaises(CstError):
            apply_splices("abc", [Splice(2, 9, "x")])

    def test_a_malformed_splice_is_refused_at_construction(self) -> None:
        for start, end, replacement, reason in (
            (-1, 0, "x", "negative start"),
            (5, 2, "x", "reversed range"),
            (0, 0, b"x", "bytes replacement"),
        ):
            with self.subTest(reason=reason), self.assertRaises(CstError):
                Splice(start, end, replacement)  # type: ignore[arg-type]

    def test_splices_apply_to_text_not_bytes(self) -> None:
        with self.assertRaises(CstError):
            apply_splices(b"abc", [])  # type: ignore[arg-type]
        with self.assertRaises(CstError):
            splice_source("abc", [])  # type: ignore[arg-type]

    def test_invalid_utf8_is_refused(self) -> None:
        with self.assertRaises(CstError):
            splice_source(b"\xff\xfe", [])


class RootCloseTests(unittest.TestCase):
    def test_the_root_close_is_the_final_delimiter_before_trailing_whitespace(self) -> None:
        for text, expected in (("(a)", 2), ("(a)\n", 2), ("(a)  \n\n", 2)):
            with self.subTest(text=repr(text)):
                self.assertEqual(root_close_offset(text), expected)

    def test_a_source_without_a_root_close_is_refused(self) -> None:
        for text in ("", "   ", "(a"):
            with self.subTest(text=repr(text)), self.assertRaises(CstError):
                root_close_offset(text)


class RealBoardTests(unittest.TestCase):
    def test_every_committed_board_round_trips_with_no_splices(self) -> None:
        """The premise the whole module rests on: strict UTF-8 decoding is lossless."""

        self.assertGreater(len(BOARDS), 10, "the fixture sweep must actually find boards")
        for board in [*BOARDS, COPPERTONE]:
            if not board.exists():
                continue
            with self.subTest(board=board.name):
                source = board.read_bytes()
                self.assertEqual(splice_source(source, []), source)

    def test_inserting_at_the_root_close_leaves_the_rest_bit_identical(self) -> None:
        for board in [*BOARDS, COPPERTONE]:
            if not board.exists():
                continue
            with self.subTest(board=board.name):
                source = board.read_bytes()
                text = source.decode("utf-8")
                close = root_close_offset(text)
                result = splice_source(source, [Splice(close, close, "  (comment)\n")])
                prefix = text[:close].encode("utf-8")
                suffix = text[close:].encode("utf-8")
                self.assertEqual(result[: len(prefix)], prefix)
                self.assertEqual(result[len(prefix) + len("  (comment)\n") :], suffix)

    def test_a_board_with_multibyte_characters_is_not_corrupted(self) -> None:
        """Where a byte/character confusion would show, and only here."""

        for board in NON_ASCII_BOARDS:
            if not board.exists():
                continue
            with self.subTest(board=board.name):
                source = board.read_bytes()
                text = source.decode("utf-8")
                self.assertNotEqual(
                    len(source), len(text), "this board must actually contain multi-byte characters"
                )
                # Replace the very last expression with itself: a no-op that nonetheless runs
                # every offset through the encode/decode boundary.
                root = parse_sexpr(source, ParseLimits())
                last = [item for item in root.items[1:] if not isinstance(item, str)][-1]
                start, end = span(last, text)
                result = splice_source(source, [Splice(start, end, text[start:end])])
                self.assertEqual(result, source)

    def test_replacing_a_node_with_itself_is_a_no_op_on_every_board(self) -> None:
        checked = 0
        for board in [*BOARDS, COPPERTONE]:
            if not board.exists():
                continue
            source = board.read_bytes()
            text = source.decode("utf-8")
            try:
                root = parse_sexpr(source, ParseLimits())
            except SExprError:
                # Some fixtures exist to be unparseable; a splice has nothing to say about
                # them, and skipping silently would hide a board that stopped parsing, so the
                # count below keeps this sweep honest.
                continue
            checked += 1
            splices = []
            for child in root.items[1:]:
                if isinstance(child, str):
                    continue
                start, end = span(child, text)
                splices.append(Splice(start, end, text[start:end]))
            with self.subTest(board=board.name, nodes=len(splices)):
                self.assertEqual(splice_source(source, splices), source)
        self.assertGreater(checked, 10, "the sweep must cover most committed boards")


TEXT = st.text(alphabet=st.characters(blacklist_categories=("Cs",)), min_size=0, max_size=200)


class SplicePropertyTests(unittest.TestCase):
    @settings(max_examples=200, deadline=None)
    @given(TEXT)
    def test_no_splices_reproduces_the_source_exactly(self, text: str) -> None:
        self.assertEqual(apply_splices(text, []), text)

    @settings(max_examples=200, deadline=None)
    @given(TEXT, st.data())
    def test_a_self_replacement_is_always_a_no_op(self, text: str, data: st.DataObject) -> None:
        if not text:
            return
        start = data.draw(st.integers(min_value=0, max_value=len(text)))
        end = data.draw(st.integers(min_value=start, max_value=len(text)))
        self.assertEqual(apply_splices(text, [Splice(start, end, text[start:end])]), text)

    @settings(max_examples=300, deadline=None)
    @given(TEXT, st.data())
    def test_everything_outside_the_spliced_range_survives_unchanged(
        self, text: str, data: st.DataObject
    ) -> None:
        start = data.draw(st.integers(min_value=0, max_value=len(text)))
        end = data.draw(st.integers(min_value=start, max_value=len(text)))
        replacement = data.draw(TEXT)
        result = apply_splices(text, [Splice(start, end, replacement)])
        self.assertEqual(result[:start], text[:start])
        self.assertEqual(result[start : start + len(replacement)], replacement)
        self.assertEqual(result[start + len(replacement) :], text[end:])

    @settings(max_examples=200, deadline=None)
    @given(st.data())
    def test_disjoint_splices_compose_independently_of_order(self, data: st.DataObject) -> None:
        text = data.draw(st.text(alphabet="abcdef", min_size=10, max_size=60))
        cuts = sorted(
            data.draw(
                st.lists(
                    st.integers(min_value=0, max_value=len(text)),
                    min_size=4,
                    max_size=4,
                    unique=True,
                )
            )
        )
        first = Splice(cuts[0], cuts[1], "X")
        second = Splice(cuts[2], cuts[3], "YY")
        forward = apply_splices(text, [first, second])
        backward = apply_splices(text, [second, first])
        self.assertEqual(forward, backward)
        self.assertEqual(len(forward), len(text) - (cuts[1] - cuts[0]) - (cuts[3] - cuts[2]) + 3)

    @settings(max_examples=200, deadline=None)
    @given(
        st.text(
            alphabet=st.characters(min_codepoint=0x80, blacklist_categories=("Cs",)),
            min_size=1,
            max_size=60,
        )
    )
    def test_multibyte_text_survives_a_byte_level_round_trip(self, text: str) -> None:
        """The byte/character boundary, exercised on text where the two genuinely differ."""

        source = f"({text})".encode()
        self.assertNotEqual(len(source), len(source.decode("utf-8")))
        self.assertEqual(splice_source(source, []), source)
        close = root_close_offset(source.decode("utf-8"))
        result = splice_source(source, [Splice(close, close, "Z")])
        self.assertEqual(result, source[:-1] + b"Z" + source[-1:])


class SexprRefusalDeterminismTests(unittest.TestCase):
    """The two syntax-error paths coverage used to reach only by chance (#255).

    A Hypothesis strategy that never draws an unterminated string or a stray
    closing parenthesis leaves both lines uncovered, so a coverage gate sees a
    phantom delta. These deterministic examples pin both paths regardless of
    what generation draws.
    """

    def test_an_unterminated_quoted_string_refuses_deterministically(self) -> None:
        with self.assertRaises(SExprError) as error:
            parse_sexpr(b'"unterminated', ParseLimits())
        self.assertEqual(error.exception.code, "syntax.invalid")

    def test_a_stray_closing_parenthesis_refuses_deterministically(self) -> None:
        with self.assertRaises(SExprError) as error:
            parse_sexpr(b")", ParseLimits())
        self.assertEqual(error.exception.code, "syntax.invalid")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
