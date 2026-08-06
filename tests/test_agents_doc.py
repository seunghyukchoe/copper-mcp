"""Keep `docs/agents.md` from going stale without anyone noticing.

`docs/agents.md` is contract documentation: an agent reads it and then decides what to send and how
to react to a refusal. A wrong statement there is worse than a missing one, so the two facts most
likely to drift -- the set of registered MCP tools and the set of diagnostic codes -- are checked
mechanically rather than by review.

The checks are deliberately grep-level. They assert that a name the document uses still exists in
the source, not that the surrounding prose is accurate; no test can do the latter. They are
therefore cheap to keep passing while a rename or a removed code still fails the build.
"""

from __future__ import annotations

import asyncio
import re
from pathlib import Path

import pytest

from copper_mcp.mcp_server import mcp

ROOT = Path(__file__).resolve().parents[1]
AGENTS_DOC = ROOT / "docs" / "agents.md"
SOURCE_ROOT = ROOT / "src" / "copper_mcp"

TOOL_SECTION = "Tool reference"
CODE_SECTION = "Refusal codes: what to do next"

#: A first-column cell that names a code or a tool: one backticked lowercase token. Anything else
#: in a first column -- an error class name, prose, a heading -- is ignored rather than guessed at.
NAME_CELL = re.compile(r"^`([a-z][a-z0-9_.]*)`$")

#: Sanity floors. They exist so that renaming a heading, reformatting a table, or deleting a
#: section fails loudly instead of silently checking nothing at all.
MIN_DOCUMENTED_CODES = 40


def _document() -> str:
    return AGENTS_DOC.read_text(encoding="utf-8")


def _section(title: str) -> str:
    """Return the body of one `## ` section, up to the next `## ` heading."""

    text = _document()
    start = text.find(f"\n## {title}\n")
    assert start != -1, (
        f"docs/agents.md no longer has a '## {title}' section. "
        "tests/test_agents_doc.py keys its checks to that heading; update both together."
    )
    body = text[start + 1 :]
    end = body.find("\n## ", 1)
    return body if end == -1 else body[:end]


def _first_column_names(section_body: str) -> set[str]:
    """Collect every backticked lowercase name in the first column of the section's tables."""

    names: set[str] = set()
    for line in section_body.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        first_cell = stripped.split("|")[1].strip()
        match = NAME_CELL.match(first_cell)
        if match is not None:
            names.add(match.group(1))
    return names


def _source_text() -> str:
    return "\n".join(path.read_text(encoding="utf-8") for path in sorted(SOURCE_ROOT.rglob("*.py")))


def _registered_tool_names() -> set[str]:
    return {tool.name for tool in asyncio.run(mcp.list_tools())}


def test_agents_doc_exists_and_is_linked_from_the_documentation_index() -> None:
    assert AGENTS_DOC.is_file()
    index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
    assert "agents.md" in index
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "docs/agents.md" in readme


def test_llms_txt_points_at_the_agent_contract() -> None:
    """The llms.txt convention is a pointer file; it is useless if it points nowhere."""

    llms = (ROOT / "llms.txt").read_text(encoding="utf-8")
    assert llms.startswith("# CopperMCP")
    assert "> " in llms, "llms.txt must carry a blockquote summary"
    assert "docs/agents.md" in llms


def test_every_documented_tool_is_registered() -> None:
    documented = _first_column_names(_section(TOOL_SECTION))
    registered = _registered_tool_names()
    unknown = sorted(documented - registered)
    assert not unknown, (
        f"docs/agents.md documents tools that are not registered over MCP: {unknown}. "
        "Remove them, or restore the tool."
    )


def test_every_registered_tool_is_documented() -> None:
    documented = _first_column_names(_section(TOOL_SECTION))
    registered = _registered_tool_names()
    missing = sorted(registered - documented)
    assert not missing, (
        f"MCP tools are missing from the docs/agents.md tool reference: {missing}. "
        "An agent that cannot find a tool's contract will guess at it."
    )


def test_the_documented_code_list_is_not_empty() -> None:
    codes = _first_column_names(_section(CODE_SECTION))
    assert len(codes) >= MIN_DOCUMENTED_CODES, (
        f"docs/agents.md documents only {len(codes)} diagnostic codes. "
        "Either the tables lost their shape or the section was gutted; check the heading and "
        "the first column of each table."
    )


def test_every_documented_diagnostic_code_exists_in_the_source() -> None:
    codes = _first_column_names(_section(CODE_SECTION))
    source = _source_text()
    missing = sorted(code for code in codes if f'"{code}"' not in source)
    assert not missing, (
        f"docs/agents.md names diagnostic codes that no longer exist in src/copper_mcp: {missing}. "
        "A documented code that the server cannot emit is worse than an undocumented one."
    )


@pytest.mark.parametrize(
    "literal",
    ["not_run", "not_modelled", "inconclusive", "untrusted_board_author"],
)
def test_one_value_literals_are_documented_and_still_exist(literal: str) -> None:
    """The literals exist precisely so a caller cannot read ignorance as a pass."""

    assert f'"{literal}"' in _source_text(), f"{literal} is no longer emitted by the source"
    assert f"`{literal}`" in _document(), f"{literal} is no longer documented in docs/agents.md"
