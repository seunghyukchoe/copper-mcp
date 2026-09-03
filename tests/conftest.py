"""Shared pytest configuration: deterministic Hypothesis generation in CI.

`tests/test_cst.py` feeds Hypothesis-generated text into `parse_sexpr`, and
with no seed or `derandomize` setting anywhere, which syntax-error branch a run
happens to exercise is random. Two full runs on identical source then disagree
by one covered statement (`src/copper_mcp/adapters/sexpr.py` refusal paths),
so a coverage gate cannot distinguish "a test was lost" from "the dice rolled
differently" (#255).

The profile is registered here but only *loaded* when `HYPOTHESIS_PROFILE` asks
for it, and the hosted workflow sets `HYPOTHESIS_PROFILE=deterministic-ci` on the
test job. That split is deliberate: `derandomize` buys a comparable coverage
number by pinning generation to one fixed example stream, which is exactly what
a gate needs and exactly what exploration must not have. A local run keeps the
default profile and can still stumble onto a counterexample CI's fixed stream
will never reach. The two refusal paths that used to depend on the strategy
additionally get deterministic examples in `tests/test_cst.py`, so their
coverage no longer depends on generation under either profile.
"""

from __future__ import annotations

import os

from hypothesis import settings

settings.register_profile("deterministic-ci", derandomize=True)
settings.load_profile(os.environ.get("HYPOTHESIS_PROFILE", "default"))
