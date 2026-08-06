"""Deterministic structural budgets for untrusted Board IR and KiCad inputs.

Every default here is derived from measurement rather than intuition, and the derivation is
recorded in ``docs/research/parse-budget-calibration-v1.md``. The rule the defaults follow:
**a board that fits inside ``max_input_bytes`` should normally fit inside every structural
budget too.** A byte ceiling that cannot bind is not a control, and before
this calibration it could not — 16 MiB of ordinary KiCad source carries roughly 2.7 million
S-expression nodes, against a node budget of 500,000, so the node budget refused every board above
about 3.2 MiB while the byte ceiling sat unused five times higher (issue #112).

Two budgets are deliberately *not* derived from the byte ceiling, because their cost is not linear
in input size:

- ``max_intersection_tests`` bounds an O(n^2) scan per ring. A 4,000-point ring occupies about
  80 KiB — nothing against a 16 MiB ceiling — and costs 7.99 million tests at roughly 0.83 us each.
  Sizing it for "whatever fits in the byte ceiling" would buy a multi-minute refusal.
- ``max_objects`` is already pinned to the Board IR schema's own object ceiling. Raising it past
  that changes nothing, because validation takes the minimum of the two.

``max_tokens`` is the parser's effective *memory* control. Measured peak parse-arena residency is
about 61 bytes per admitted token across two orders of magnitude, because the worst-case shape —
maximal nesting — spends two tokens and one retained list object per level.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

#: Common prefix of every discriminated budget refusal. Callers that only need to know "some
#: budget ran out" should match this prefix rather than any single code, so a future budget does
#: not silently fall out of their handling.
BUDGET_EXCEEDED_PREFIX = "budget.exceeded"


class ParseBudget(StrEnum):
    """One structural budget, named by the diagnostic code its exhaustion is refused under.

    Before v0.7.0 every one of these refused under the bare code ``budget.exceeded``, which told
    an operator that *a* ceiling was hit and nothing about *which*. The knob and the refusal now
    share a name: ``budget.exceeded.nodes`` is raised by ``max_nodes``, which is set by
    ``COPPER_MCP_MAX_PARSE_NODES``. The member value is written out in full rather than composed
    from the prefix so that the literal code string is greppable in the source, which is what
    ``tests/test_agents_doc.py`` checks the published contract against.

    A budget name and its configured value are process configuration, never board content, so
    naming them in a refusal discloses nothing about the document that triggered it.
    """

    INPUT_BYTES = "budget.exceeded.input_bytes"
    DEPTH = "budget.exceeded.depth"
    TOKENS = "budget.exceeded.tokens"
    NODES = "budget.exceeded.nodes"
    ATOM_CHARS = "budget.exceeded.atom_chars"
    CHILDREN_PER_LIST = "budget.exceeded.children_per_list"
    OBJECTS = "budget.exceeded.objects"
    VERTICES_PER_RING = "budget.exceeded.vertices_per_ring"
    TOTAL_VERTICES = "budget.exceeded.total_vertices"
    INTERSECTION_TESTS = "budget.exceeded.intersection_tests"

    @property
    def limit_field(self) -> str:
        """Return the :class:`ParseLimits` field this budget refuses on behalf of."""

        return f"max_{self.name.lower()}"


@dataclass(frozen=True, slots=True)
class ParseLimits:
    """Independent caps that prevent parser memory and structure amplification."""

    #: The headline ceiling. Operators move it with ``COPPER_MCP_MAX_BOARD_BYTES``; the service
    #: layer takes the minimum of that setting and this default.
    max_input_bytes: int = 16 * 1024 * 1024
    #: Not operator-settable. Every board measured during calibration nests at most 6 deep,
    #: including a 4.4 MB four-layer board, so this is a shape guard with a factor of 21 in hand
    #: and no evidence that any real document needs it moved.
    max_depth: int = 128
    #: 16 MiB x 215,000 tokens/MiB (the densest board measured) = 3.44M, rounded up.
    max_tokens: int = 4_000_000
    #: 16 MiB x 170,000 nodes/MiB (the densest board measured) = 2.72M, rounded up.
    max_nodes: int = 3_000_000
    #: Not operator-settable: this bounds one token, not the document, and no measured board
    #: carries an atom within two orders of magnitude of it.
    max_atom_chars: int = 4096
    #: 16 MiB x ~7,000 children/MiB (the widest list on the largest board measured) = ~112,000,
    #: with a factor of 4 in hand for a board whose top-level list is unusually flat.
    max_children_per_list: int = 500_000
    #: Held at the Board IR schema's own object ceiling; validation takes the minimum of the two,
    #: so a larger value here would be inert. 16 MiB admits at most ~240,000 objects anyway.
    max_objects: int = 250_000
    #: Not operator-settable: bounded by the schema's own ring ceiling, and reached in practice
    #: through ``max_intersection_tests`` long before this value.
    max_vertices_per_ring: int = 100_000
    #: The shortest legal point expression, ``(xy a b)``, is 10 bytes, so a 16 MiB document can
    #: hold at most ~1.68M vertices.
    max_total_vertices: int = 2_000_000
    #: Deliberately *not* derived from the byte ceiling: this is the one budget whose cost is
    #: superlinear in input size. 2,000,000 tests is a ~1.65 s ceiling at 0.83 us per test.
    max_intersection_tests: int = 2_000_000
    max_diagnostics: int = 100

    def __post_init__(self) -> None:
        for name in self.__dataclass_fields__:
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
