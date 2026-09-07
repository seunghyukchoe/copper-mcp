import hashlib
import time
from dataclasses import FrozenInstanceError
from typing import cast

import pytest

from copper_mcp.engineering import spice_model_library as library

VALID = b"""* ordinary captured model library\r
.model DFAST D (IS=2.0n N=1.3 RS=0.4)\r
.subckt FILTER IN OUT\r
R1 IN MID 10k\r
C1 MID OUT 20p\r
D1 OUT 0 DFAST\r
.ends filter\r
"""


def parse(content: bytes = VALID, *, deadline: object | None = None) -> library.SpiceModelLibrary:
    return library.parse_spice_model_library(
        content, deadline=time.monotonic() + 5 if deadline is None else cast(float, deadline)
    )


def test_parses_ordered_redacted_immutable_definitions_and_original_digests() -> None:
    result = parse()
    diode, subcircuit = result.definitions
    assert (diode.name, diode.kind, diode.ports, diode.dependencies) == (
        "DFAST",
        "diode",
        ("A", "K"),
        (),
    )
    assert (subcircuit.name, subcircuit.kind, subcircuit.ports, subcircuit.dependencies) == (
        "FILTER",
        "subcircuit",
        ("IN", "OUT"),
        ("DFAST",),
    )
    assert result.content == VALID
    assert result.digest == "sha256:" + hashlib.sha256(VALID).hexdigest()
    assert diode.digest == "sha256:" + hashlib.sha256(diode.source).hexdigest()
    assert repr(result) == "<SpiceModelLibrary redacted>"
    assert repr(diode) == "<SpiceDefinition redacted>"
    with pytest.raises(FrozenInstanceError):
        diode.name = "other"  # type: ignore[misc]


def test_plus_continuations_preserve_source_bytes_and_parse_parameters() -> None:
    content = b".model D1 D (IS=1n\n+ N=1.1 TT=2u)\n"
    result = parse(content)
    assert result.definitions[0].name == "D1"
    assert result.definitions[0].source == content


def test_accepts_spaced_and_compact_model_headers_with_distinct_source_digests() -> None:
    spaced = parse(b".model D1 D (IS=1n)\n").definitions[0]
    compact = parse(b".model D1 D(IS=1n)\n").definitions[0]
    assert spaced.ports == compact.ports == ("A", "K")
    assert spaced.source != compact.source
    assert spaced.digest != compact.digest


def test_accepts_full_line_comments_and_one_port_subcircuit_calls() -> None:
    content = b"""* ; $ // and backslash are comment text, not syntax
.subckt CHILD IN
.ends CHILD
.subckt TOP IN
X1 IN child
.ends TOP
"""
    result = parse(content)
    assert result.definitions[-1].dependencies == ("child",)


def test_invalid_content_and_deadline_refusals_do_not_retain_private_context() -> None:
    with pytest.raises(library.SpiceModelLibraryError) as invalid_content:
        parse(b".model D1 D (IS=1n)\xff")
    assert invalid_content.value.__context__ is None

    huge_deadline = 10**10_000
    with pytest.raises(library.SpiceModelLibraryError) as invalid_deadline:
        parse(deadline=huge_deadline)
    assert invalid_deadline.value.__context__ is None


@pytest.mark.parametrize("control", (b"\x00", b"\x0b", b"\x1f", b"\x7f", b"\xff"))
def test_rejects_non_ascii_and_disallowed_ascii_controls(control: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b".model D1 D (IS=1n)" + control + b"\n")


def test_rejects_line_logical_line_definition_and_port_ceilings() -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b"*" + b"x" * 8193 + b"\n.model D1 D (IS=1n)\n")
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b".model D1 D (IS=" + b"1" * 8180 + b"\n+ n)\n")
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b".model D1 D (IS=1n)\n" * 129)
    ports = b" ".join(f"P{index}".encode() for index in range(65))
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b".subckt MANY " + ports + b"\n.ends MANY\n")
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b"*\n" * 10_001 + b".model D1 D (IS=1n)\n")


def test_rejects_cumulative_source_element_budget_across_definitions() -> None:
    body = b"".join(f"R{index} A B 1k\n".encode() for index in range(2049))
    content = (
        b".subckt FIRST A B\n"
        + body
        + b".ends FIRST\n.subckt SECOND A B\n"
        + body
        + b".ends SECOND\n"
    )
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


def test_source_element_budget_refuses_before_parsing_an_exhausted_element(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0
    original = library._parse_element

    def counted_parse_element(line: library._LogicalLine, deadline: float) -> library._Element:
        nonlocal calls
        calls += 1
        return original(line, deadline)

    monkeypatch.setattr(library, "_MAX_ELEMENTS", 3)
    monkeypatch.setattr(library, "_parse_element", counted_parse_element)
    content = b""".subckt FIRST A B
R1 A B 1k
R2 A B 1k
.ends FIRST
.subckt SECOND A B
R3 A B 1k
R4 A B 1k
.ends SECOND
"""
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)
    assert calls == 3


@pytest.mark.parametrize("leaf", (b"", b"R1 A A 1k\n"))
def test_rejects_small_doubling_chain_by_expanded_work_not_source_size(leaf: bytes) -> None:
    content = b".subckt L A\n" + leaf + b".ends L\n"
    previous = b"L"
    for index in range(17):
        name = f"S{index}".encode()
        content += b".subckt " + name + b" A\nX1 A " + previous + b"\nX2 A " + previous
        content += b"\n.ends " + name + b"\n"
        previous = name
    assert content.count(b"\nX") < 4096
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


@pytest.mark.parametrize(
    "content",
    (
        b".model D1 D (IS=1n)\n; inline\n",
        b".model D1 D (IS=1n) $ comment\n",
        b".model D1 D (IS=1n) // comment\n",
        b".model D1 D (IS=1n)\\\n",
        b".model D1 D (IS=1n)\r",
        b".model D1 D (IS=1n)\n+ IS=2n\n",
        b".include external.lib\n",
        b".model D1 D (IS=1n)\n.end\n",
    ),
)
def test_rejects_disallowed_syntax(content: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


@pytest.mark.parametrize(
    "content",
    (
        b".model D1 D (IS=1n IS=2n)\n",
        b".model D1 D (IS=foo)\n",
        b".model D1 D (IS=1n+2n)\n",
        b".model D1 D (IS=1n) trailing\n",
        b".model D1 D IS=1n\n",
        b".model D1 N (IS=1n)\n",
    ),
)
def test_rejects_invalid_diode_models(content: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


@pytest.mark.parametrize(
    "number",
    (b"1e9999", b"1e308t", b"1" * 65),
)
def test_rejects_overflowing_or_overlong_numeric_literals(number: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(b".model D1 D (IS=" + number + b")\n")


@pytest.mark.parametrize(
    "content",
    (
        b".subckt A IN in\n.ends A\n",
        b".subckt A 0 OUT\n.ends A\n",
        b".subckt A IN OUT\nR1 IN OUT 1k\nr1 OUT IN 2k\n.ends A\n",
        b".subckt A IN OUT\nR1 IN OUT 1k\n.ends B\n",
        b".subckt A IN OUT\n.model D1 D (IS=1n)\n.ends A\n",
        b".subckt A IN OUT\nR1 IN OUT 1k\n",
    ),
)
def test_rejects_bad_subcircuit_structure(content: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


@pytest.mark.parametrize(
    "content",
    (
        b".subckt A IN OUT\nD1 IN OUT UNKNOWN\n.ends A\n",
        b".model D1 D (IS=1n)\n.subckt A IN OUT\nX1 IN OUT D1\n.ends A\n",
        b".subckt A IN OUT\nX1 IN OUT B\n.ends A\n.subckt B IN OUT\nX1 IN OUT A\n.ends B\n",
        b".subckt A IN OUT\nX1 IN OUT B\n.ends A\n.subckt B IN\nR1 IN IN 1k\n.ends B\n",
        b".model D1 D (IS=1n)\n.model d1 D (IS=2n)\n",
    ),
)
def test_rejects_dependency_kind_arity_cycles_and_identity_collisions(content: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


def test_accepts_case_insensitive_references_and_expands_iteratively() -> None:
    content = b""".model dIODe D (IS=1n)
.subckt child A B
D1 A B DIODE
.ends CHILD
.subckt TOP IN OUT
X1 IN OUT ChIlD
R1 IN OUT 1k
.ends
"""
    result = parse(content)
    assert result.definitions[-1].dependencies == ("ChIlD",)


def test_rejects_source_element_budget_before_expansion() -> None:
    leaf = b".subckt L A B\nR1 A B 1k\n.ends L\n"
    calls = b"".join(f"X{index} A B L\n".encode() for index in range(4096))
    level = b".subckt M A B\n" + calls + b".ends M\n"
    top_calls = b"".join(f"X{index} A B M\n".encode() for index in range(25))
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(leaf + level + b".subckt T A B\n" + top_calls + b".ends T\n")


@pytest.mark.parametrize("content", (b"", b"x" * (1024 * 1024 + 1), b".model D1 D (IS=1n)\xff"))
def test_rejects_empty_oversized_and_non_ascii_content(content: bytes) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(content)


@pytest.mark.parametrize("deadline", (float("nan"), float("inf"), True, "later"))
def test_rejects_malformed_deadlines(deadline: object) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="malformed"):
        parse(deadline=deadline)


def test_deadline_is_checked_before_parsing_and_during_hashing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with pytest.raises(library.SpiceModelLibraryError, match="deadline expired"):
        parse(deadline=time.monotonic() - 1)

    clock = [0.0]
    original = hashlib.sha256

    class ExpiringHash:
        def __init__(self) -> None:
            self._hash = original()

        def update(self, value: bytes) -> None:
            self._hash.update(value)
            clock[0] = 2.0

        def hexdigest(self) -> str:
            return self._hash.hexdigest()

    monkeypatch.setattr(time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(hashlib, "sha256", ExpiringHash)
    with pytest.raises(library.SpiceModelLibraryError, match="deadline expired"):
        library._digest(b"x" * (128 * 1024), 1.0)
