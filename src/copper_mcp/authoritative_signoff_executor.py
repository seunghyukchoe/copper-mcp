"""The coordinator-owned bounded executor that authoritative sign-off claims rest on.

ADR-0118 kept ``SIGNED_OFF`` unreachable because nothing in this repository could *earn* it: a
caller-supplied callable proves nothing about which authority ran, and a surrogate ranking is not
a domain result. ADR-0119 lifts that for exactly one domain, on exactly one authority, because
one already exists here -- ADR-0004 delegated design-rule authority to KiCad's own DRC, and a DFM
question about a candidate is the question that DRC answers.

What makes this an executor rather than an adapter is that it owns the invocation. It names its
runner as an import, not as an argument; it decides how many times to run it; and it derives the
comparability of the answer it got instead of accepting a caller's word for it. Nothing about the
authority that runs is reachable from a request.

Two boundaries are worth stating because they are easy to misread:

* **This module owns the supported evidence-construction path.** The sign-off core refuses to
  construct ``AuthoritativeEvidence`` without its module-private sentinel. This is a cooperative
  internal-misuse guard, not an in-process security sandbox against a privileged Python caller
  importing or monkeypatching private symbols.
* **It has no MCP, CLI, apply, persistence or mutation authority.** It reads a board through the
  existing workspace boundary, writes only into the disposable snapshot the DRC adapter already
  owns, and returns a redacted verdict. A sign-off is not an authorization to write copper; the
  apply surfaces remain separately gated and untouched by anything here.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

from copper_mcp.adapters.kicad_board_ir import KiCadConstraintProfile
from copper_mcp.config import Settings
from copper_mcp.kicad_cli import (
    KiCadCliError,
    RouteCandidateDrcEvidence,
    run_route_candidate_drc,
)
from copper_mcp.routing.astar import VerifiedFill
from copper_mcp.routing.authoritative_signoff import (
    _EVIDENCE_CAPABILITY,
    _FIXED_BACKEND_ID,
    _FIXED_BACKEND_VERSION,
    _MAX_REPETITIONS,
    _MIN_COMPARABLE_REPETITIONS,
    AuthoritativeEvidence,
    AuthoritativeSignoffResult,
    CancellationCheck,
    CandidateBinding,
    DeadlineCheck,
    SignoffCode,
    SignoffComparability,
    SignoffDomain,
    SignoffStatus,
    _result,
    evaluate_authoritative_signoff,
    parse_surrogate_advisory,
)
from copper_mcp.routing.contracts import RouteCandidate
from copper_mcp.security import WorkspaceViolationError

#: The one domain this executor speaks for. It is a module constant rather than a parameter: the
#: authority it runs answers design-rule questions, and pointing it at `si` would be a naming
#: change dressed as a capability.
EXECUTED_DOMAIN: Final = SignoffDomain.DFM

#: How many invocations a claim rests on by default. Two is the minimum that can *disagree*, which
#: is the property ADR-0109 asks for; more is available and costs a full DRC run each.
DEFAULT_REPETITIONS: Final = _MIN_COMPARABLE_REPETITIONS


class AuthoritativeSignoffExecutorError(RuntimeError):
    """Raised when this module is called wrongly, which is never a statement about a board."""


def _refuse(code: SignoffCode, *, advisory_present: bool) -> AuthoritativeSignoffResult:
    return _result(SignoffStatus.REFUSED, EXECUTED_DOMAIN, code, advisory_present=advisory_present)


def _stopped(
    cancelled: CancellationCheck | None, deadline: DeadlineCheck | None
) -> SignoffCode | None:
    """Return the stop code observed now, treating a misbehaving check as a stop."""

    for callback, code in (
        (cancelled, SignoffCode.CANCELLED),
        (deadline, SignoffCode.DEADLINE_EXCEEDED),
    ):
        if callback is None:
            continue
        try:
            observed = callback()
        except Exception:
            return code
        if type(observed) is not bool or observed:
            return code
    return None


def _agreement(observations: Sequence[RouteCandidateDrcEvidence]) -> SignoffComparability:
    """Derive what these observations earn: ADR-0109's rule, taken over the whole statement.

    ADR-0109's own ``comparability_of`` compares the published DRC counts after stripping the
    keys that cannot be compared -- wall clock, and the literal's own output. It is not reachable
    from here: it lives in the benchmark package, which production code may not import at all
    (pinned by ``test_the_import_seam_is_not_reachable_from_the_tool_surface``). Re-deriving the
    literal here is therefore deliberate, and it is written to be the *strictest* faithful form of
    the same rule rather than a second opinion about it.

    The comparison is the in-toto Statement's canonical bytes, which is sound because that
    Statement is a pure function of the candidate, the four revisions and the DRC summary --
    ADR-0052 put no timestamp, no path and no run label in it, so there is nothing in these bytes
    that ADR-0109 would have had to strip. Comparing them therefore covers every count ADR-0109
    compares *and* the revisions it does not, so a patched board or DRC-context revision that
    moved between runs cannot be laundered into agreement by summaries that happened to match.

    ``tests/test_authoritative_signoff_executor.py`` pins the two vocabularies identical, so the
    literals cannot drift apart without a test failing.
    """

    if len(observations) < _MIN_COMPARABLE_REPETITIONS:
        return SignoffComparability.SINGLE_INVOCATION
    statements = {observation.canonical_statement_bytes() for observation in observations}
    if len(statements) != 1:
        return SignoffComparability.REPEATED_DISAGREEMENT
    return SignoffComparability.REPEATED_AGREEMENT


def _suppressed(observation: RouteCandidateDrcEvidence) -> bool:
    """Whether the run left checks unevaluated, by exclusion or by rule suppression."""

    return observation.summary.ignored_check_count > 0 or observation.summary.exclusion_count > 0


def execute_dfm_signoff(
    requested_path: str,
    candidate: RouteCandidate,
    profile: KiCadConstraintProfile,
    settings: Settings,
    *,
    verified_fill: tuple[VerifiedFill, ...] = (),
    repetitions: int = DEFAULT_REPETITIONS,
    surrogate: object = None,
    cancelled: CancellationCheck | None = None,
    deadline: DeadlineCheck | None = None,
) -> AuthoritativeSignoffResult:
    """Run authoritative DRC ``repetitions`` times over one candidate and grade what came back.

    Every invocation replays the same immutable candidate and records the source, patched-board,
    and DRC-context revisions in its canonical statement.  Exact statement agreement therefore
    establishes ADR-0109's byte-identity precondition; a board or rules change between invocations
    produces disagreement rather than relying on a caller's promise.

    Returns a redacted result in every case a board or an authority can cause, including backend
    failure. It raises only when this module itself is called wrongly, because a malformed
    repetition count is a defect in the caller and not a finding about the board.
    """

    if isinstance(repetitions, bool) or type(repetitions) is not int:
        raise AuthoritativeSignoffExecutorError("repetition count must be an integer")
    if not _MIN_COMPARABLE_REPETITIONS <= repetitions <= _MAX_REPETITIONS:
        raise AuthoritativeSignoffExecutorError(
            "a sign-off claim needs between "
            f"{_MIN_COMPARABLE_REPETITIONS} and {_MAX_REPETITIONS} invocations"
        )
    if type(candidate) is not RouteCandidate:
        raise AuthoritativeSignoffExecutorError("sign-off requires one immutable route candidate")

    # Grade the advisory before spending a DRC run on it. The core would refuse a malformed one
    # anyway, but only after this loop had already paid for N invocations, and `advisory_present`
    # would meanwhile have been reporting a ranking that does not parse.
    advisory = parse_surrogate_advisory(surrogate) if surrogate is not None else None
    advisory_present = advisory is not None
    if surrogate is not None and advisory is None:
        return _refuse(SignoffCode.INVALID_ADVISORY, advisory_present=False)

    observations: list[RouteCandidateDrcEvidence] = []
    for _ in range(repetitions):
        stop = _stopped(cancelled, deadline)
        if stop is not None:
            return _refuse(stop, advisory_present=advisory_present)
        try:
            observations.append(
                run_route_candidate_drc(
                    requested_path,
                    candidate,
                    profile,
                    settings,
                    verified_fill=verified_fill,
                )
            )
        except (KiCadCliError, WorkspaceViolationError):
            # Deliberately no exception text: a backend's message is untrusted output, and a
            # failure to run is never evidence about the board either way.
            return _refuse(SignoffCode.BACKEND_FAILURE, advisory_present=advisory_present)

    first = observations[0]
    if any(
        observation.candidate_id != first.candidate_id
        or observation.candidate_base_revision != first.candidate_base_revision
        for observation in observations[1:]
    ):
        # Unreachable through this function, which replays one candidate; kept because the check
        # is cheap and the failure it guards against would be a silent cross-candidate claim.
        return _refuse(SignoffCode.CANDIDATE_MISMATCH, advisory_present=advisory_present)

    try:
        binding = CandidateBinding(
            candidate_id=first.candidate_id, base_revision=first.candidate_base_revision
        )
    except ValueError:
        return _refuse(SignoffCode.INVALID_CANDIDATE, advisory_present=advisory_present)

    comparability = _agreement(observations)
    # The verdict is read off the first observation. That is exact when the runs agreed, because
    # agreement here means their whole Statements were byte-identical; and when they did not, the
    # comparability carried alongside it refuses the claim before the verdict is reached at all.
    evidence = AuthoritativeEvidence(
        _EVIDENCE_CAPABILITY,
        backend_id=_FIXED_BACKEND_ID,
        backend_version=_FIXED_BACKEND_VERSION,
        domain=EXECUTED_DOMAIN,
        candidate_id=binding.candidate_id,
        base_revision=binding.base_revision,
        # The in-toto Statement is already this project's redacted, deterministic projection of a
        # DRC run, so the content address of a claim is the content address of the artefact the
        # claim is about rather than a second, parallel encoding of it.
        authoritative_output=first.canonical_statement_bytes(),
        completed=True,
        passed=first.summary.passed,
        suppressed=_suppressed(first),
        comparability=comparability,
        repetitions=len(observations),
    )
    return evaluate_authoritative_signoff(
        binding,
        EXECUTED_DOMAIN,
        evidence=evidence,
        surrogate=advisory,
        cancelled=cancelled,
        deadline=deadline,
    )


__all__ = [
    "DEFAULT_REPETITIONS",
    "EXECUTED_DOMAIN",
    "AuthoritativeSignoffExecutorError",
    "execute_dfm_signoff",
]
