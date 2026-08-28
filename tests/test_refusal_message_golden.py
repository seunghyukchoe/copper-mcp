"""A golden set for the KiCad adapter's tabled refusal messages.

**These messages are not a contract, and this test does not make them one.**
Nothing here says a consumer may match on a message; ADR-0095 and ADR-0096 both
say the opposite, and the source's own comments say a message is *a value from a
table, selected by equality against the source token and never built from it* --
which is a privacy property, not a stability promise. What the golden set says
is narrower and entirely about review: a member cannot leave, arrive, or be
reworded **silently**.

Three refusal messages disappeared from the source in 0.8.0. The migration note
was complete and the change was right; the problem is that nothing would have
noticed if it had not been. The regime that held was a rule -- "mention it in
the migration note" -- and a rule held by attention rather than by construction
held that time and would not hold every time.

Deprecation would have been the wrong answer here, and it is worth writing down
why rather than re-deriving it: a deprecation path means emitting a refusal the
server no longer means, which is false output produced to protect a consumer of
a string the project never promised. A golden set costs one test and produces no
false output at all. Its failure mode is a diff.

## What is in the set, and what is deliberately not

In: every message that is **a value in a closed table** keyed by a source token,
plus the two token tables whose refusal sentence is built from a format string at
the use site (`_UNSUPPORTED_PAD_FIELDS`, `_REFUSED_PAD_PROPERTY`). Those two are
listed with their formatter here, and the formatter itself is anchored
behaviourally in `test_kicad_board_ir.py`, which asserts the emitted sentence for
a real board.

Out: the roughly eighty inline `self.fail(...)` sentences elsewhere in the
adapter. They are not table values, so pinning them would pin the adapter's whole
prose surface rather than its closed refusal vocabulary -- and *that* would make
the messages more contractual, which is the thing this test says it is not doing.
`test_the_registry_covers_every_tabled_message_in_the_module` is what keeps that
line honest in the other direction: a new table added to the module and not
registered here fails, so the exclusion is scoped to inline literals rather than
being a hole anything can fall through.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from copper_mcp.adapters import kicad_board_ir

GOLDEN = Path(__file__).parent / "fixtures" / "refusal-messages" / "kicad-board-ir-tables-v1.json"

# A refusal message is a sentence. A diagnostic code -- `unsupported.construct`,
# `unsupported.document` -- is a dotted token and carries no space, which is what
# separates the two mechanically without a hand-written exclusion list.
_MESSAGE = re.compile(r"\bunsupported\b")


def _looks_like_a_refusal_message(value: str) -> bool:
    return " " in value and _MESSAGE.search(value) is not None


def _strings(value: Any, depth: int = 0) -> list[str]:
    if depth > 2:
        return []
    if isinstance(value, str):
        return [value]
    if isinstance(value, tuple | list | set | frozenset):
        return [item for element in value for item in _strings(element, depth + 1)]
    if isinstance(value, dict):
        return [
            item
            for key, element in value.items()
            for item in (*_strings(key, depth + 1), *_strings(element, depth + 1))
        ]
    return []


def _messages_by_table() -> dict[str, dict[str, str]]:
    """Extract the current message set from the live module, table by table.

    Read from the module rather than from a second hand-written list, so the
    golden compares source against record instead of comparing one record against
    another.
    """

    module = kicad_board_ir
    tables: dict[str, dict[str, str]] = {
        # head -> the sentence the refusal names it by.
        "_UNMODELLED_ROOT_HEADS": dict(module._UNMODELLED_ROOT_HEADS),
        # head -> (sentence, object kind); the kind is pinned with the sentence
        # because an operator reading a diagnostic sees both.
        "_UNMODELLED_COPPER_GRAPHIC_HEADS": {
            head: f"{message} [{kind}]"
            for head, (message, kind) in module._UNMODELLED_COPPER_GRAPHIC_HEADS.items()
        },
        # The fallback for a copper graphic head the table above does not name.
        "_UNNAMED_COPPER_GRAPHIC": {
            "<fallback>": f"{module._UNNAMED_COPPER_GRAPHIC[0]} "
            f"[{module._UNNAMED_COPPER_GRAPHIC[1]}]"
        },
        # Named separately from the dict that holds it: repointing `gr_text_box`
        # away from this constant is exactly the split ADR-0095's fifth exit
        # condition exists to prevent, and it would leave the dict's value set
        # unchanged in size.
        "_COPPER_TEXT_REFUSAL": {
            "<shared>": f"{module._COPPER_TEXT_REFUSAL[0]} [{module._COPPER_TEXT_REFUSAL[1]}]"
        },
        # head -> the sentence for an `Edge.Cuts` outline primitive that stays refused.  Four
        # entries, three sentences: `gr_curve` and `gr_bezier` are the same construct under two
        # spellings and deliberately share a sentence, which is a fact about the format rather
        # than a duplication to tidy away.
        "_EDGE_CUTS_REFUSED_OUTLINE_HEADS": dict(module._EDGE_CUTS_REFUSED_OUTLINE_HEADS),
        # token -> the sentence, which says *why* rather than *that*.
        "_UNMODELLED_PAD_SHAPES": dict(module._UNMODELLED_PAD_SHAPES),
        # A token table whose sentence is built at the use site. The formatter is
        # duplicated here and anchored behaviourally in `test_kicad_board_ir.py`
        # (`assert diagnostic.message == f"pad field {head!r} is unsupported"`),
        # so the two cannot drift without a failure.
        "_UNSUPPORTED_PAD_FIELDS": {
            head: f"pad field {head!r} is unsupported" for head in module._UNSUPPORTED_PAD_FIELDS
        },
        # The one writable pad property that refuses, likewise formatted at the
        # use site from the constant rather than from the board's own atom.
        "_REFUSED_PAD_PROPERTY": {
            module._REFUSED_PAD_PROPERTY: (
                f"pad fabrication property {module._REFUSED_PAD_PROPERTY!r} removes board area "
                "the outline still claims and is unsupported"
            )
        },
    }
    return tables


def _read_golden() -> dict[str, dict[str, str]]:
    document = json.loads(GOLDEN.read_text(encoding="utf-8"))
    assert document["schema"] == "refusal-message-golden/1"
    tables: dict[str, dict[str, str]] = document["tables"]
    return tables


def test_the_tabled_refusal_messages_match_their_committed_golden_set() -> None:
    """One test, and its fix is updating the golden file in the same diff.

    A message that changes, leaves, or arrives shows up here as a named
    difference. The remedy is never to loosen this assertion; it is to write the
    new golden and let review see the words that moved.
    """

    current = _messages_by_table()
    golden = _read_golden()

    assert set(current) == set(golden), (
        "the set of pinned tables moved; add the new table to `_messages_by_table` and to the "
        "golden file, or remove it from both"
    )
    for table in sorted(current):
        missing = {key: value for key, value in golden[table].items() if key not in current[table]}
        added = {key: value for key, value in current[table].items() if key not in golden[table]}
        changed = {
            key: (golden[table][key], value)
            for key, value in current[table].items()
            if key in golden[table] and golden[table][key] != value
        }
        assert not missing, f"{table} no longer emits: {missing}"
        assert not added, f"{table} now also emits: {added}"
        assert not changed, f"{table} reworded: {changed}"


def test_deleting_a_tabled_message_is_named_by_the_failure() -> None:
    """P3.7's proving check, run against a constructed deletion rather than asserted.

    The set is only useful if the failure says which sentence went; a bare "the
    sets differ" would send a reader back to `git diff` to find out what the test
    was for.
    """

    golden = _read_golden()
    table = "_UNMODELLED_ROOT_HEADS"
    surviving = dict(golden[table])
    deleted, sentence = sorted(surviving.items())[0]
    surviving.pop(deleted)

    missing = {key: value for key, value in golden[table].items() if key not in surviving}

    assert missing == {deleted: sentence}
    assert "unsupported" in missing[deleted]


def test_the_registry_covers_every_tabled_message_in_the_module() -> None:
    """A new closed table cannot be added without joining the golden set.

    Scanned rather than listed: every module-level constant whose value contains
    a refusal *sentence* -- a string carrying a space, which a dotted diagnostic
    code never does -- must be one this test extracts. Without this, the golden
    would pin whatever it happened to know about on the day it was written, and a
    sixth table would be invisible for exactly the reason the fifth conversion
    counter was.
    """

    registered = set(_messages_by_table())
    found = {
        name
        for name, value in vars(kicad_board_ir).items()
        if not name.startswith("__")
        and not callable(value)
        and not isinstance(value, type)
        and any(_looks_like_a_refusal_message(item) for item in _strings(value))
    }

    unregistered = found - registered
    assert not unregistered, (
        f"{sorted(unregistered)} hold refusal messages and are not in the golden set; add them to "
        "`_messages_by_table` and regenerate the golden file"
    )
    # The scan is evidence only if it could have reported a presence. A predicate
    # that stopped recognising a refusal sentence would empty `found` and leave
    # `unregistered` empty too -- a green run that observed nothing. These five are
    # the message-bearing tables in the module today, asserted as a floor rather
    # than as equality so that a properly registered sixth table still passes.
    assert found >= {
        "_COPPER_TEXT_REFUSAL",
        "_UNMODELLED_COPPER_GRAPHIC_HEADS",
        "_UNMODELLED_PAD_SHAPES",
        "_UNMODELLED_ROOT_HEADS",
        "_UNNAMED_COPPER_GRAPHIC",
    }


def test_the_two_token_tables_are_registered_although_no_scan_can_find_them() -> None:
    """`_UNSUPPORTED_PAD_FIELDS` and `_REFUSED_PAD_PROPERTY` hold tokens, not sentences.

    The scan above cannot see them -- `chamfer` and `pad_prop_castellated` are
    field names -- so they are registered by hand, and this test says so out loud
    rather than leaving a reader to wonder whether the scan missed them. Their
    sentences are built at the use site, which is why the formatter is duplicated
    in this module and anchored behaviourally elsewhere.
    """

    tables = _messages_by_table()

    assert set(tables["_UNSUPPORTED_PAD_FIELDS"]) == set(kicad_board_ir._UNSUPPORTED_PAD_FIELDS)
    assert set(tables["_REFUSED_PAD_PROPERTY"]) == {kicad_board_ir._REFUSED_PAD_PROPERTY}
    assert not any(
        _looks_like_a_refusal_message(item)
        for item in _strings(kicad_board_ir._UNSUPPORTED_PAD_FIELDS)
    )


def test_the_golden_set_says_in_its_own_file_that_it_promises_nothing() -> None:
    """The file a future reader finds first has to carry the disclaimer, not just this module."""

    document = json.loads(GOLDEN.read_text(encoding="utf-8"))

    assert "not a contract" in document["not_a_contract"]
    assert "review" in document["why"]
