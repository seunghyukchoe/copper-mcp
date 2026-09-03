"""Shared pytest configuration: deterministic Hypothesis generation.

`tests/test_cst.py` feeds Hypothesis-generated text into `parse_sexpr`, and
with no seed or `derandomize` setting anywhere, which syntax-error branch a run
happens to exercise is random. Two full runs on identical source then disagree
by one covered statement (`src/copper_mcp/adapters/sexpr.py` refusal paths),
so a coverage gate cannot distinguish "a test was lost" from "the dice rolled
differently" (#255).

Loading a `derandomize`d profile makes generation deterministic while leaving
every test's `max_examples` and `deadline` choices untouched. The two refusal
paths that used to depend on the strategy additionally get deterministic
examples in `tests/test_cst.py`, so their coverage no longer depends on
generation at all.
"""

from __future__ import annotations

from hypothesis import settings

settings.register_profile("deterministic-ci", derandomize=True)
settings.load_profile("deterministic-ci")
