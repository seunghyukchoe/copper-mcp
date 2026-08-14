"""The comparability literal every benchmark DRC section carries, and the prohibition it enforces.

`B-107` ran the corpus runner twice at the **same commit** over **byte-identical** boards and
nine boards' records still differed -- every one of them only in the `drc` section. `error_count`
moved 936 to 941 on one board, `hole_clearance` moved 201 to 202, and a whole violation type
(`tracks_crossing`, 4) appeared in one run and not the other. `B-108` corroborates the mechanism:
the counts saturate near per-rule caps (199/200/201, 499/500/502) and which rules fill the caps
varies run to run.

So a KiCad DRC count is not a function of the bytes it was taken over, and a benchmark artifact
that publishes one as if it were promises more than its `run_id` can deliver.

**The policy is not a tolerance.** A numeric tolerance would be an oracle-fitted constant, and
[ADR-0095](../../../docs/adr/0095-copper-text-has-no-derivable-envelope.md) already refused that
shape of argument in another domain: an envelope is *derived* and then oracle-checked, and a
constant fitted to the oracle inverts that. The policy is instead a **one-value literal** on every
DRC section, plus one prohibition:

* `single_invocation` -- the counts are one invocation's answer. Publishable as an observation.
* `repeated_agreement` -- N >= 2 invocations, taken at one commit over byte-identical inputs,
  agreed **exactly** on every published field.
* `repeated_disagreement` -- N >= 2 invocations did not agree. The counts are still published;
  what is withdrawn is the claim that they describe the board.

**The prohibition: a before/after differential may not cite a DRC count whose comparability is
not `repeated_agreement`.** `drc_differential` is the sanctioned way to compute one and refuses
every other case. Nothing stops a document quoting a number by hand -- that is what
`scripts/check_drc_comparability.py` sweeps for, and what `B-111` qualifies for the counts already
published.

**What this module does not own.** It cannot verify that N observations were taken at one commit
over identical bytes: that is the runner's obligation and it is asserted, not checked, when a
runner hands over more than one observation. It has no opinion on whether any count is *correct*.
And it deliberately does not touch `schemas/drc-summary` -- that schema is the **live payload** a
caller receives from `run_board_drc`, one invocation by construction, and qualifying a single
invocation as `single_invocation` inside its own response would add a field that is constant. The
policy governs the **benchmark projection**: the artifact that quotes a count as evidence, where
another number could have been quoted instead and the difference would matter.

**What has not been run.** No N-run characterisation of the distribution exists. That is issue
#170's step 1 and plan item `P4.1a`, and it remains open: the literal ships on what `B-107` and
`B-108` already found, and the characterisation is what would size the mitigation rather than
what justifies it. `R-154` carries the open risk.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, cast

__all__ = [
    "COMPARABILITY_LITERALS",
    "COUNT_KEYS",
    "INCOMPARABLE_KEYS",
    "LITERAL_KEY",
    "REPETITIONS_KEY",
    "SECTION_KEYS",
    "DrcComparabilityError",
    "admissible_in_differential",
    "comparability_of",
    "drc_differential",
    "drc_sections",
    "qualified",
    "require_qualified",
    "weakest",
]

#: The closed set, in order from weakest claim to strongest and then to withdrawn. Order is not
#: decorative: `weakest` reads it.
COMPARABILITY_LITERALS: Final[tuple[str, ...]] = (
    "repeated_disagreement",
    "single_invocation",
    "repeated_agreement",
)

#: The one value a differential may cite.
_ADMISSIBLE_IN_DIFFERENTIAL: Final[str] = "repeated_agreement"

LITERAL_KEY: Final[str] = "drc_comparability"
REPETITIONS_KEY: Final[str] = "drc_repetitions"

#: A mapping carrying any of these is a **DRC section** and must carry the literal. Every name
#: here is unambiguous -- it names a KiCad DRC quantity and nothing else -- because this table
#: decides what the sweep over committed artifacts *demands* a literal of, and a name that also
#: means something else elsewhere would make the gate fire on records that have no DRC in them.
#: The table is closed on purpose: a count published under a name that is not here is not
#: policed, which is the way to defeat this module and the reason `COUNT_KEYS` below is reviewed
#: whenever a runner publishes a new DRC field.
SECTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "clean_drc_runs",
        "drc_clean",
        "drc_passed",
        "drc_reported",
        "error_count",
        "exclusion_count",
        "ignored_check_count",
        "passed_drc_runs",
        "unconnected_count",
        "violation_type_counts",
        "warning_count",
    }
)

#: Every field whose value is a DRC count, which is a superset of `SECTION_KEYS`: `clean` and
#: `passed` are *derived from* the counts and inherit their instability -- a board whose
#: `error_count` saturates at a per-rule cap in one run and one below it in the next can cross
#: the `clean` boundary without a byte of the board changing -- but neither name is specific
#: enough to identify a section by, so they are policed once a section is identified and never
#: used to identify one.
COUNT_KEYS: Final[frozenset[str]] = SECTION_KEYS | frozenset({"clean", "passed"})

#: Keys stripped before two observations are compared for agreement, each for a stated reason.
#: Nothing that is a **count** may join this set -- excluding a count would let a section claim
#: `repeated_agreement` while the number it publishes moved, which is precisely the defect the
#: literal exists to disclose.
INCOMPARABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        # Wall clock. Never equal across two runs, and not a property of the board.
        "elapsed_ms",
        # This module's own output, which is not an input to deciding it.
        LITERAL_KEY,
        REPETITIONS_KEY,
    }
)


class DrcComparabilityError(ValueError):
    """A DRC count was published, or cited, without the comparability its use requires."""


def _canonical(observation: Mapping[str, Any]) -> str:
    return json.dumps(
        {key: value for key, value in observation.items() if key not in INCOMPARABLE_KEYS},
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def comparability_of(observations: Sequence[Mapping[str, Any]]) -> str:
    """Return the literal earned by ``observations``.

    The caller asserts -- this module cannot check -- that every observation was taken at one
    commit over byte-identical inputs. Given that, one observation earns `single_invocation`,
    and two or more earn `repeated_agreement` only if they agree exactly on every published
    field outside `INCOMPARABLE_KEYS`.
    """

    if not observations:
        raise DrcComparabilityError("a DRC section needs at least one observation to be qualified")
    if len(observations) == 1:
        return "single_invocation"
    rendered = {_canonical(observation) for observation in observations}
    return "repeated_agreement" if len(rendered) == 1 else "repeated_disagreement"


def weakest(literals: Sequence[str]) -> str:
    """Return the weakest literal in ``literals``.

    An aggregate over several DRC sections is only as comparable as its least comparable input:
    a total that sums one `repeated_agreement` board and one `repeated_disagreement` board is a
    number that moves between runs, and saying otherwise would launder the disagreement through
    an addition.
    """

    unknown = sorted(set(literals) - set(COMPARABILITY_LITERALS))
    if unknown:
        raise DrcComparabilityError(f"{unknown[0]!r} is not a comparability literal")
    if not literals:
        raise DrcComparabilityError("an aggregate over no DRC section has no comparability")
    return min(literals, key=COMPARABILITY_LITERALS.index)


def qualified(
    section: Mapping[str, Any], observations: Sequence[Mapping[str, Any]]
) -> dict[str, Any]:
    """Return ``section`` carrying the literal ``observations`` earned, and how many there were."""

    return {
        **section,
        LITERAL_KEY: comparability_of(observations),
        REPETITIONS_KEY: len(observations),
    }


def drc_sections(document: object, path: str = "") -> Iterator[tuple[str, Mapping[str, Any]]]:
    """Yield every ``(path, mapping)`` in ``document`` that publishes a DRC count."""

    if isinstance(document, Mapping):
        if any(key in SECTION_KEYS for key in document):
            yield path or "/", document
        for key, value in document.items():
            yield from drc_sections(value, f"{path}/{key}")
    elif isinstance(document, list | tuple):
        for index, value in enumerate(document):
            yield from drc_sections(value, f"{path}[{index}]")


def require_qualified(document: object, *, where: str) -> None:
    """Refuse to emit ``document`` while any DRC section in it lacks a valid literal.

    This is the emission gate. It walks the whole report rather than the section a runner
    remembered to pass, so a count published under a new key in a nested record is caught by the
    same call the runner already makes.
    """

    for path, section in drc_sections(document):
        literal = section.get(LITERAL_KEY)
        if literal not in COMPARABILITY_LITERALS:
            raise DrcComparabilityError(
                f"{where}: the DRC section at {path} publishes a count without a "
                f"{LITERAL_KEY!r} literal (got {literal!r}); one of "
                f"{', '.join(COMPARABILITY_LITERALS)} is required"
            )


def admissible_in_differential(section: Mapping[str, Any], *, where: str) -> None:
    """Refuse a DRC section that a before/after differential may not cite."""

    literal = section.get(LITERAL_KEY)
    if literal != _ADMISSIBLE_IN_DIFFERENTIAL:
        raise DrcComparabilityError(
            f"{where}: a differential may not cite a DRC count whose comparability is "
            f"{literal!r}; only {_ADMISSIBLE_IN_DIFFERENTIAL!r} counts are comparable across runs"
        )


def drc_differential(
    before: Mapping[str, Any], after: Mapping[str, Any], field: str, *, where: str
) -> int:
    """Return ``after[field] - before[field]``, or refuse because the difference means nothing.

    Both sides must be `repeated_agreement`. `B-108` measured baseline-against-baseline
    disagreement reaching an absolute difference of 32 against 109 across a real change -- a
    factor of 3.4, not a factor of 100 -- so run-to-run noise does not swamp a real signal but is
    the same order of magnitude as one. That is exactly the regime in which a single-invocation
    differential is unsafe, and exactly why this refuses rather than warns.
    """

    admissible_in_differential(before, where=f"{where} (before)")
    admissible_in_differential(after, where=f"{where} (after)")
    if field not in COUNT_KEYS:
        raise DrcComparabilityError(f"{where}: {field!r} is not a DRC count")
    first, second = before.get(field), after.get(field)
    for value in (first, second):
        if isinstance(value, bool) or not isinstance(value, int):
            raise DrcComparabilityError(f"{where}: {field!r} is not an integer count on both sides")
    return cast("int", second) - cast("int", first)
