"""Operator-settable structural parse budgets, their calibration, and their discriminated refusals.

Three separate claims are pinned here, and they are separate on purpose.

1. **Configurability.** Each structural budget is readable from its own environment variable,
   bounded to a range whose upper end is the point where the budget stops being reachable, and
   refused with a typed ``ConfigurationError`` on anything malformed.
2. **Coherence.** The shipped defaults admit a board that fits inside the parser's byte ceiling.
   Before this calibration they did not: the node budget refused every board above roughly
   3.2 MiB while the byte ceiling sat unused at 16 MiB, so the byte ceiling was not a control at
   all (issue #112).
3. **Discrimination.** A refusal names the budget that ran out, so an operator knows which knob to
   turn. Every code is ``budget.exceeded.<field>`` for a real ``ParseLimits`` field.

The measured densities the defaults are derived from live in
``docs/research/parse-budget-calibration-v1.md`` and are re-stated as constants below, so a change
to a default that breaks the derivation fails here rather than being discovered on a real board.
"""

from __future__ import annotations

import os
import tempfile
import tracemalloc
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from copper_mcp.adapters import KiCadConstraintProfile, parse_kicad_bytes
from copper_mcp.adapters.sexpr import SExprError, parse_sexpr
from copper_mcp.board_ir import BUDGET_EXCEEDED_PREFIX, NetClass, ParseBudget, ParseLimits
from copper_mcp.config import ConfigurationError, Settings
from copper_mcp.parse_budgets import parse_limits_for

MIB = 1024 * 1024

#: Measured on 37 convertible KiCad boards (the repository's own fixtures, the CopperTone buffer,
#: and one private 4.4 MB four-layer board), taking the densest observation of each quantity.
#: See docs/research/parse-budget-calibration-v1.md, table 1.
DENSEST_NODES_PER_MIB = 169_092
DENSEST_TOKENS_PER_MIB = 215_168
DENSEST_OBJECTS_PER_MIB = 14_899
#: The widest single S-expression list on the largest board measured, per mebibyte of that board.
DENSEST_CHILDREN_PER_MIB = 6_970

#: Every environment variable this change introduces, with its default, a legal in-range value,
#: and the ``ParseLimits`` field it drives.
BUDGET_VARIABLES: tuple[tuple[str, str, int, int, str], ...] = (
    ("COPPER_MCP_MAX_PARSE_TOKENS", "max_parse_tokens", 4_000_000, 250_000, "max_tokens"),
    ("COPPER_MCP_MAX_PARSE_NODES", "max_parse_nodes", 3_000_000, 120_000, "max_nodes"),
    (
        "COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST",
        "max_parse_children_per_list",
        500_000,
        4_096,
        "max_children_per_list",
    ),
    ("COPPER_MCP_MAX_PARSE_OBJECTS", "max_parse_objects", 250_000, 1_000, "max_objects"),
    (
        "COPPER_MCP_MAX_PARSE_TOTAL_VERTICES",
        "max_parse_total_vertices",
        2_000_000,
        50_000,
        "max_total_vertices",
    ),
    (
        "COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS",
        "max_parse_intersection_tests",
        2_000_000,
        10_000,
        "max_intersection_tests",
    ),
)

CONSTRAINTS = NetClass(
    id="class:default",
    name="Default",
    clearance_nm=200_000,
    track_width_nm=250_000,
    via_diameter_nm=800_000,
    via_drill_nm=400_000,
)
PROFILE = KiCadConstraintProfile(net_classes=(CONSTRAINTS,), default_net_class_id="class:default")


def _settings(**environment: str) -> Settings:
    with tempfile.TemporaryDirectory() as directory:
        with patch.dict(os.environ, {"COPPER_MCP_WORKSPACE": directory, **environment}, clear=True):
            return Settings.from_env()


def _uuid(index: int) -> str:
    return f"20000000-0000-0000-0000-{index:012d}"


def large_board(segments: int) -> bytes:
    """Build one ordinary, convertible KiCad board with a chosen number of copper segments.

    Generated rather than committed. A fixture large enough to be interesting here is several
    megabytes, and committing several megabytes of synthetic S-expressions to prove a *density*
    claim would put a number in Git that nobody can check against the boards it came from. The
    generator is checkable: ``test_the_generated_board_matches_measured_board_density`` asserts
    that what it emits sits inside the density range measured on real boards, so a board this
    test admits is not a shape that only exists in this file.
    """

    parts = [
        "(kicad_pcb",
        "  (version 20260206)",
        '  (generator "pcbnew")',
        "  (layers",
        '    (0 "F.Cu" signal)',
        '    (2 "B.Cu" signal)',
        '    (25 "Edge.Cuts" user)',
        "  )",
        "  (gr_rect",
        "    (start 0 0)",
        "    (end 400 300)",
        "    (stroke (width 0.1) (type default))",
        "    (fill no)",
        '    (layer "Edge.Cuts")',
        f'    (uuid "{_uuid(1)}")',
        "  )",
    ]
    for index in range(segments):
        # Full six-decimal coordinates, as KiCad writes them, so the generated board's bytes per
        # node land inside the range measured on real boards rather than in an artificially
        # compact shape that would make the budgets look roomier than they are.
        x = 1 + (index % 300) + (index % 997) / 1_000_000
        y = 1 + (index // 300) % 250 + (index % 991) / 1_000_000
        parts.append(
            "  (segment"
            f" (start {x:.6f} {y:.6f})"
            f" (end {x + 0.5:.6f} {y:.6f})"
            " (width 0.25)"
            ' (layer "F.Cu")'
            ' (net "SIG")'
            f' (uuid "{_uuid(index + 100)}")'
            ")"
        )
    parts.append(")")
    return "\n".join(parts).encode()


def _node_count(source: bytes) -> int:
    """Count nodes exactly as ``parse_sexpr`` charges them: one per atom, one per closed list."""

    root = parse_sexpr(source, replace(ParseLimits(), max_nodes=64_000_000, max_tokens=64_000_000))
    nodes = 0
    stack = [root]
    while stack:
        expression = stack.pop()
        nodes += 1
        for item in expression.items:
            if isinstance(item, str):
                nodes += 1
            else:
                stack.append(item)
    return nodes


# --------------------------------------------------------------------------------------------
# 1. The budget names, the limit fields, and the refusal codes are one vocabulary
# --------------------------------------------------------------------------------------------


def test_every_budget_code_names_a_real_limits_field() -> None:
    for budget in ParseBudget:
        assert budget.value.startswith(f"{BUDGET_EXCEEDED_PREFIX}."), budget
        assert budget.limit_field in ParseLimits.__dataclass_fields__, budget
        # The code's discriminator is exactly the field name minus its `max_` prefix, so an
        # operator reading `budget.exceeded.nodes` can find `max_nodes` without a lookup table.
        assert budget.value == f"{BUDGET_EXCEEDED_PREFIX}.{budget.limit_field.removeprefix('max_')}"


def test_every_enforced_limit_has_a_budget_code() -> None:
    """No ``ParseLimits`` field may refuse without a name — that is the defect being fixed."""

    named = {budget.limit_field for budget in ParseBudget}
    # `max_diagnostics` is the sole exception: it is a declared cap that no code path enforces,
    # so there is no refusal for it to discriminate. It is listed here rather than dropped so
    # that giving it teeth later fails this test instead of shipping an unnamed refusal.
    unnamed = set(ParseLimits.__dataclass_fields__) - named - {"max_diagnostics"}
    assert unnamed == set()


def test_no_bare_budget_exceeded_code_survives_on_the_board_path() -> None:
    """The undiscriminated code must not linger anywhere it could still reach a caller."""

    root = Path(__file__).resolve().parents[1] / "src" / "copper_mcp"
    board_path_modules = [
        # `limits.py` is excluded because it *defines* the prefix every discriminated code is
        # built from; it is the one place the bare string is supposed to appear.
        *(path for path in (root / "board_ir").rglob("*.py") if path.name != "limits.py"),
        root / "adapters" / "sexpr.py",
        root / "adapters" / "kicad_board_ir.py",
    ]
    offenders = [
        path.relative_to(root).as_posix()
        for path in board_path_modules
        if '"budget.exceeded"' in path.read_text(encoding="utf-8")
    ]
    assert offenders == []


# --------------------------------------------------------------------------------------------
# 2. Configurability: every budget is settable, bounded, and typed
# --------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("variable", "field", "default", "legal", "limit_field"),
    BUDGET_VARIABLES,
    ids=[entry[0] for entry in BUDGET_VARIABLES],
)
def test_each_budget_is_readable_from_its_own_environment_variable(
    variable: str, field: str, default: int, legal: int, limit_field: str
) -> None:
    assert getattr(_settings(), field) == default
    settings = _settings(**{variable: str(legal)})
    assert getattr(settings, field) == legal
    # And it must actually reach the parser, not merely land in a settings object.
    assert getattr(parse_limits_for(settings), limit_field) == legal


@pytest.mark.parametrize(
    ("variable", "field", "default", "legal", "limit_field"),
    BUDGET_VARIABLES,
    ids=[entry[0] for entry in BUDGET_VARIABLES],
)
@pytest.mark.parametrize(
    "malformed",
    ["", " ", "0", "-1", "1.5", "1_000", "abc", "0x10", "1e6", "٤", "+", "999999999999999999999"],
)
def test_a_malformed_budget_is_a_typed_configuration_error(
    variable: str, field: str, default: int, legal: int, limit_field: str, malformed: str
) -> None:
    """No coercion, no truthiness, no silent fallback to the default — the process refuses."""

    with pytest.raises(ConfigurationError) as caught:
        _settings(**{variable: malformed})
    assert variable in str(caught.value)


@pytest.mark.parametrize(
    ("variable", "beyond"),
    [
        ("COPPER_MCP_MAX_PARSE_TOKENS", 16 * MIB + 1),
        ("COPPER_MCP_MAX_PARSE_NODES", 8 * MIB + 1),
        ("COPPER_MCP_MAX_PARSE_CHILDREN_PER_LIST", 8 * MIB + 1),
        ("COPPER_MCP_MAX_PARSE_OBJECTS", 250_001),
        ("COPPER_MCP_MAX_PARSE_TOTAL_VERTICES", 2_000_001),
        ("COPPER_MCP_MAX_PARSE_INTERSECTION_TESTS", 20_000_001),
    ],
)
def test_a_budget_beyond_its_reachable_range_is_refused(variable: str, beyond: int) -> None:
    """This must not become an unbounded parser: every range has a top, and it is enforced.

    Each ceiling is the point past which the value could no longer do anything — the parser's own
    16 MiB input ceiling for the byte-derived budgets, the Board IR schema's object limit, and an
    explicit wall-clock ceiling for the one superlinear budget. A range that accepted more would
    be accepting a number that changes nothing.
    """

    with pytest.raises(ConfigurationError):
        _settings(**{variable: str(beyond)})
    assert _settings(**{variable: str(beyond - 1)}).workspace is not None


def test_configuration_can_only_tighten_the_parser_byte_ceiling() -> None:
    """``COPPER_MCP_MAX_BOARD_BYTES`` bounds several unrelated reads, so it may not widen this."""

    default_ceiling = ParseLimits().max_input_bytes
    raised = _settings(COPPER_MCP_MAX_BOARD_BYTES=str(512 * MIB))
    assert raised.max_board_bytes == 512 * MIB
    assert parse_limits_for(raised).max_input_bytes == default_ceiling

    lowered = _settings(COPPER_MCP_MAX_BOARD_BYTES=str(MIB))
    assert parse_limits_for(lowered).max_input_bytes == MIB


def test_parse_limits_require_typed_settings() -> None:
    with pytest.raises(TypeError):
        parse_limits_for({"max_parse_nodes": 1})  # type: ignore[arg-type]


def test_the_shipped_defaults_are_exactly_the_settings_defaults() -> None:
    """A settings default that drifts from its ``ParseLimits`` default is a silent change."""

    assert parse_limits_for(_settings()) == replace(
        ParseLimits(), max_input_bytes=ParseLimits().max_input_bytes
    )


# --------------------------------------------------------------------------------------------
# 3. Coherence: the defaults admit what the byte ceiling admits
# --------------------------------------------------------------------------------------------


def test_defaults_admit_the_densest_measured_board_filling_the_byte_ceiling() -> None:
    """The rule the calibration exists to enforce, stated as an assertion.

    A board that fits in ``max_input_bytes`` must normally fit in every scale budget too. The
    densities are the *maxima* observed across the measured corpus, so this is a worst-case
    check rather than a median one.
    """

    limits = ParseLimits()
    ceiling_mib = limits.max_input_bytes / MIB
    assert limits.max_tokens >= ceiling_mib * DENSEST_TOKENS_PER_MIB
    assert limits.max_nodes >= ceiling_mib * DENSEST_NODES_PER_MIB
    assert limits.max_objects >= min(ceiling_mib * DENSEST_OBJECTS_PER_MIB, 250_000)
    assert limits.max_children_per_list >= ceiling_mib * DENSEST_CHILDREN_PER_MIB


def test_the_two_budgets_that_are_not_byte_derived_stay_where_measurement_put_them() -> None:
    """Guard the deliberate exceptions, so a future "scale everything up" pass has to argue.

    ``max_intersection_tests`` buys O(n^2) work: a 4,000-point ring is only ~80 KiB but costs
    7.99M tests at ~0.83 us each, so deriving it from the byte ceiling would buy a slow refusal
    instead of a fast one. ``max_objects`` is pinned to the Board IR schema's own ceiling, above
    which validation ignores it.
    """

    limits = ParseLimits()
    assert limits.max_intersection_tests == 2_000_000
    assert limits.max_objects == 250_000


def test_the_generated_board_matches_measured_board_density() -> None:
    """Keep the synthetic board honest against the real corpus it stands in for."""

    source = large_board(2_000)
    density = _node_count(source) / (len(source) / MIB)
    assert 120_000 <= density <= DENSEST_NODES_PER_MIB


# --------------------------------------------------------------------------------------------
# 4. A large board converts at the defaults and refuses at a tightened budget
# --------------------------------------------------------------------------------------------

#: Chosen to exceed the node budget CopperMCP shipped before this calibration (500,000), so this
#: is a regression against the exact board size issue #112 reported as refused.
LARGE_BOARD_SEGMENTS = 24_000
PREVIOUS_NODE_DEFAULT = 500_000


def test_a_board_beyond_the_previous_node_default_now_converts() -> None:
    source = large_board(LARGE_BOARD_SEGMENTS)
    assert _node_count(source) > PREVIOUS_NODE_DEFAULT

    stale = parse_kicad_bytes(
        source, PROFILE, replace(ParseLimits(), max_nodes=PREVIOUS_NODE_DEFAULT)
    )
    assert stale.snapshot is None
    assert [item.code for item in stale.diagnostics] == ["budget.exceeded.nodes"]

    conversion = parse_kicad_bytes(source, PROFILE, ParseLimits())
    assert conversion.diagnostics == ()
    assert conversion.snapshot is not None
    assert len(conversion.snapshot.content.segments) == LARGE_BOARD_SEGMENTS


@pytest.mark.parametrize(
    ("tightened", "expected_code"),
    [
        ({"max_input_bytes": 1024}, "budget.exceeded.input_bytes"),
        ({"max_tokens": 1_000}, "budget.exceeded.tokens"),
        ({"max_nodes": 1_000}, "budget.exceeded.nodes"),
        ({"max_children_per_list": 32}, "budget.exceeded.children_per_list"),
        ({"max_objects": 100}, "budget.exceeded.objects"),
    ],
)
def test_the_same_board_refuses_under_a_deliberately_tightened_budget(
    tightened: dict[str, int], expected_code: str
) -> None:
    """One board, five ceilings, five distinguishable answers."""

    source = large_board(1_500)
    conversion = parse_kicad_bytes(source, PROFILE, replace(ParseLimits(), **tightened))

    assert conversion.snapshot is None
    assert [item.code for item in conversion.diagnostics] == [expected_code]


def test_a_tightened_budget_reaches_the_parser_through_configuration_alone() -> None:
    """The operator path end to end: an environment variable changes which refusal comes back."""

    source = large_board(1_500)
    tightened = parse_limits_for(_settings(COPPER_MCP_MAX_PARSE_NODES="1000"))
    conversion = parse_kicad_bytes(source, PROFILE, tightened)

    assert conversion.snapshot is None
    assert [item.code for item in conversion.diagnostics] == ["budget.exceeded.nodes"]


def test_a_budget_refusal_names_only_configuration_and_never_board_content() -> None:
    """A budget name and its value are process configuration; the document must not leak."""

    marker = "SECRET_NET_NAME_DO_NOT_DISCLOSE"
    source = large_board(200).replace(b'(net "SIG")', f'(net "{marker}")'.encode())
    conversion = parse_kicad_bytes(source, PROFILE, replace(ParseLimits(), max_nodes=1_000))

    assert conversion.snapshot is None
    rendered = repr(conversion.diagnostics)
    assert marker not in rendered
    # The locator is a byte offset, which is a position in the input rather than any of its
    # content, and the message names the budget only.
    assert conversion.diagnostics[0].message == "node budget exceeded"
    assert conversion.diagnostics[0].source_locator.startswith("byte:")


# --------------------------------------------------------------------------------------------
# 5. Adversarial input stays bounded at the shipped defaults
# --------------------------------------------------------------------------------------------


def _adversarial_wide(byte_ceiling: int) -> bytes:
    """One list far wider than any real board's: binds ``max_children_per_list``."""

    atoms = (byte_ceiling - 24) // 2
    return ("(kicad_pcb(gr_poly" + " a" * atoms + "))").encode()


def _adversarial_tree(byte_ceiling: int) -> bytes:
    """Maximal node count with every list narrow and shallow: binds ``max_nodes``."""

    chunk = "(g" + " a" * 1000 + ")"
    group = "(p" + chunk * 100 + ")"
    return ("(kicad_pcb" + group * ((byte_ceiling - 64) // len(group)) + ")").encode()


def _adversarial_deep(byte_ceiling: int) -> bytes:
    """Repeated maximal-depth nesting: the worst case for retained parser memory."""

    unit = "(" * 126 + "a" + ")" * 126
    return ("(kicad_pcb" + unit * max(1, (byte_ceiling - 16) // len(unit)) + ")").encode()


@pytest.mark.parametrize(
    ("shape", "expected_code", "peak_ceiling_bytes"),
    [
        (_adversarial_wide, "budget.exceeded.children_per_list", 48 * MIB),
        (_adversarial_tree, "budget.exceeded.nodes", 96 * MIB),
    ],
)
def test_adversarial_input_refuses_within_a_bounded_arena_at_the_shipped_defaults(
    shape: object, expected_code: str, peak_ceiling_bytes: int
) -> None:
    """Raising the defaults must not have removed the ceiling, only moved it.

    Each payload fills the parser's whole byte ceiling with the shape that maximises the budget
    under test, and is parsed at the *shipped* defaults. The assertion is not "it is fast" — it
    is that the refusal is typed, names the budget that stopped it, and that the allocation
    charged along the way stayed inside a stated bound.
    """

    limits = ParseLimits()
    payload = shape(limits.max_input_bytes)  # type: ignore[operator]
    assert len(payload) <= limits.max_input_bytes

    tracemalloc.start()
    try:
        with pytest.raises(SExprError) as caught:
            parse_sexpr(payload, limits)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert caught.value.code == expected_code
    assert peak < peak_ceiling_bytes


def test_retained_parser_memory_stays_linear_in_the_token_budget() -> None:
    """Pin the law that makes the recorded worst case checkable rather than merely asserted.

    Maximal nesting is the memory worst case: each level costs two tokens and one retained list
    object. Peak parse-arena residency measured ~61 bytes per admitted token across two orders of
    magnitude, which is what puts the shipped 4,000,000-token default at the ~244 MiB recorded in
    B-090. Measuring 244 MiB in CI would be wasteful, so the *law* is measured at a tightened
    budget and the recorded figure follows from it.
    """

    tightened = replace(ParseLimits(), max_tokens=200_000)
    payload = _adversarial_deep(tightened.max_input_bytes)

    tracemalloc.start()
    try:
        with pytest.raises(SExprError) as caught:
            parse_sexpr(payload, tightened)
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    assert caught.value.code == "budget.exceeded.tokens"
    assert peak < 200 * tightened.max_tokens
    # And the shipped default is the same law with a bigger constant, not a different regime.
    assert ParseLimits().max_tokens * 200 < 1024 * MIB
