"""Closed, non-public sign-off seam for candidate-bound physics evidence.

This module is deliberately smaller than a physics engine.  It is the production-core gate
between a candidate/evidence producer and an authoritative SI/PI/thermal/DFM adapter.  A
surrogate may rank a candidate, but it cannot enter the claim path.  The only successful result
is evidence minted by a coordinator-owned executor for a backend this module has registered, and
bound to exactly one candidate and one base revision.

Three closed gates stand between a caller and ``SIGNED_OFF``, and none of them is request-shaped:

* **The backend registry** is a fixed module constant.  It names which ``(backend, version)`` pair
  may speak for which domain, and it is not extensible at runtime, by argument, or by import.  A
  domain with no registered backend can only produce a non-claim.
* **The evidence capability** is a private sentinel.  ``AuthoritativeEvidence`` refuses to
  construct without it through supported intake and construction paths.  It is a cooperative
  internal-misuse guard, not a security boundary against privileged same-process Python code.
* **Comparability** is carried on the evidence rather than assumed of it.  Per ADR-0109 a single
  authoritative invocation is an observation, not a comparable count, so a claim requires N >= 2
  invocations over byte-identical inputs that agreed exactly.  Disagreement is a refusal, not a
  silently weaker claim.

The module has no MCP, persistence, process, network, geometry, board-byte, prompt, or mutation
authority; it never runs the authority whose evidence it grades.  Execution lives in
``copper_mcp.authoritative_signoff_executor``, and the seam is still not exported through MCP
or CLI.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

_SHA256_PREFIX: Final = "sha256:"
_SHA256_LENGTH: Final = 71
_MAX_TEXT: Final = 128
_SCHEMA: Final = "copper-mcp/authoritative-signoff/v1"
_FIXED_BACKEND_ID: Final = "copper-mcp-authoritative-v1"
_FIXED_BACKEND_VERSION: Final = "1"
_MAX_AUTHORITATIVE_OUTPUT_BYTES: Final = 1_048_576
_MIN_COMPARABLE_REPETITIONS: Final = 2
_MAX_REPETITIONS: Final = 8


class SignoffDomain(StrEnum):
    """Physics domains for which an authoritative adapter may make a claim."""

    SI = "si"
    PI = "pi"
    THERMAL = "thermal"
    DFM = "dfm"


class SignoffComparability(StrEnum):
    """How many authoritative invocations an evidence envelope rests on, and whether they agreed.

    The literals are ADR-0109's, unchanged, because the fact they describe is the same one: a
    KiCad DRC count is not a function of the bytes it was taken over, so a single invocation is an
    observation rather than a comparable measurement.  ADR-0109 governs what a benchmark artifact
    may *publish*; this seam governs what a claim may *rest on*, which is the same question asked
    at higher stakes.
    """

    SINGLE_INVOCATION = "single_invocation"
    REPEATED_AGREEMENT = "repeated_agreement"
    REPEATED_DISAGREEMENT = "repeated_disagreement"


class SignoffStatus(StrEnum):
    """The redacted result union."""

    SIGNED_OFF = "signed_off"
    NON_CLAIM = "non_claim"
    REFUSED = "refused"


class SignoffCode(StrEnum):
    """Stable, non-echoing refusal and non-claim taxonomy."""

    NO_AUTHORITATIVE_BACKEND = "no_authoritative_backend"
    NO_AUTHORITATIVE_EVIDENCE = "no_authoritative_evidence"
    SURROGATE_ONLY = "surrogate_only"
    INVALID_CANDIDATE = "invalid_candidate"
    INVALID_BACKEND = "invalid_backend"
    INVALID_EVIDENCE = "invalid_evidence"
    INVALID_ADVISORY = "invalid_advisory"
    UNSUPPORTED_DOMAIN = "unsupported_domain"
    BACKEND_MISMATCH = "backend_mismatch"
    CANDIDATE_MISMATCH = "candidate_mismatch"
    STALE_REVISION = "stale_revision"
    EVIDENCE_MISMATCH = "evidence_mismatch"
    INCOMPLETE_EVIDENCE = "incomplete_evidence"
    FAILED_EVIDENCE = "failed_evidence"
    UNCOMPARABLE_EVIDENCE = "uncomparable_evidence"
    SUPPRESSED_EVIDENCE = "suppressed_evidence"
    BACKEND_FAILURE = "backend_failure"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_DIAGNOSTICS: Final[dict[SignoffCode, str]] = {
    SignoffCode.NO_AUTHORITATIVE_BACKEND: "authoritative sign-off is unavailable",
    SignoffCode.NO_AUTHORITATIVE_EVIDENCE: "authoritative evidence was not produced",
    SignoffCode.SURROGATE_ONLY: "surrogate output is advisory and cannot sign off",
    SignoffCode.INVALID_CANDIDATE: "candidate binding is invalid",
    SignoffCode.INVALID_BACKEND: "authoritative backend is invalid",
    SignoffCode.INVALID_EVIDENCE: "authoritative evidence is invalid",
    SignoffCode.INVALID_ADVISORY: "surrogate advisory is invalid",
    SignoffCode.UNSUPPORTED_DOMAIN: "authoritative sign-off domain is unsupported",
    SignoffCode.BACKEND_MISMATCH: "authoritative backend identity does not match",
    SignoffCode.CANDIDATE_MISMATCH: "authoritative evidence names another candidate",
    SignoffCode.STALE_REVISION: "authoritative evidence is stale",
    SignoffCode.EVIDENCE_MISMATCH: "authoritative evidence does not match",
    SignoffCode.INCOMPLETE_EVIDENCE: "authoritative evidence is incomplete",
    SignoffCode.FAILED_EVIDENCE: "authoritative evidence did not pass",
    SignoffCode.UNCOMPARABLE_EVIDENCE: "authoritative evidence is not repeatable",
    SignoffCode.SUPPRESSED_EVIDENCE: "authoritative evidence skipped checks",
    SignoffCode.BACKEND_FAILURE: "authoritative sign-off could not be completed",
    SignoffCode.CANCELLED: "authoritative sign-off was cancelled",
    SignoffCode.DEADLINE_EXCEEDED: "authoritative sign-off exceeded its deadline",
}

CancellationCheck = Callable[[], object]
DeadlineCheck = Callable[[], object]
_RESULT_CAPABILITY: Final = object()

#: Used by ``copper_mcp.authoritative_signoff_executor`` as a cooperative guard on the supported
#: evidence-construction path.  A private Python name is not a sandbox: privileged same-process
#: code can import or monkeypatch it, so hostile in-process callers require an operational or
#: process-isolation boundary outside this module.
_EVIDENCE_CAPABILITY: Final = object()

#: The closed backend registry: which fixed ``(backend ID, backend version)`` may speak for which
#: domain.  It is a module constant rather than a mutable registry on purpose -- a registration
#: call would be a seam through which a caller, a plugin, or a test could install an authority,
#: and ADR-0118 already refused every shape of that.  Adding a domain here is a source change that
#: goes through review with the adapter that earns it.
#:
#: Only DFM is registered, and only because one authority already exists in this repository that
#: can answer a DFM question about a candidate: KiCad's own DRC, which ADR-0004 made this
#: project's authority for exactly that.  SI, PI and thermal have no such adapter, so they stay
#: unregistered and can produce nothing but a non-claim.
_REGISTERED_BACKENDS: Final[Mapping[tuple[str, str], frozenset[SignoffDomain]]] = MappingProxyType(
    {(_FIXED_BACKEND_ID, _FIXED_BACKEND_VERSION): frozenset({SignoffDomain.DFM})}
)


def registered_signoff_domains() -> frozenset[SignoffDomain]:
    """Return the domains some registered backend may sign off; a read, never a registration."""

    admitted: set[SignoffDomain] = set()
    for domains in _REGISTERED_BACKENDS.values():
        admitted |= domains
    return frozenset(admitted)


def _registered_for(backend_id: str, backend_version: str, domain: SignoffDomain) -> bool:
    """Whether this exact ``(ID, version)`` pair may speak for ``domain``.  The evidence gate."""

    return domain in _REGISTERED_BACKENDS.get((backend_id, backend_version), frozenset())


def _registered_id_for(backend_id: str, domain: SignoffDomain) -> bool:
    """Whether *any* registered version of ``backend_id`` may speak for ``domain``.

    A result carries the backend ID but not its version, so this is the strongest check the
    result type can make on its own.  It is a backstop, not the gate: ``_validate_evidence_binding``
    has already checked the exact pair against the evidence that produced the claim.
    """

    return any(
        identifier == backend_id and domain in domains
        for (identifier, _version), domains in _REGISTERED_BACKENDS.items()
    )


def _digest(name: str, value: object) -> None:
    if (
        type(value) is not str
        or len(value) != _SHA256_LENGTH
        or not value.startswith(_SHA256_PREFIX)
        or any(character not in "0123456789abcdef" for character in value[7:])
    ):
        raise ValueError(f"{name} must be a sha256 digest")


def _text(name: str, value: object, *, maximum: int = _MAX_TEXT) -> None:
    if type(value) is not str or not 1 <= len(value) <= maximum:
        raise ValueError(f"{name} is malformed")
    if not value.isascii() or any(character.isspace() for character in value):
        raise ValueError(f"{name} is malformed")


def _bounded_integer(name: str, value: object, *, minimum: int, maximum: int) -> None:
    if isinstance(value, bool) or type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{name} is outside the supported range")


@dataclass(frozen=True, slots=True)
class CandidateBinding:
    """The redacted identity of one immutable candidate.

    Geometry is intentionally absent.  Candidate producers already bind this identity to their
    immutable route candidate; this contract carries only the identity needed by sign-off.
    """

    candidate_id: str
    base_revision: str

    def __post_init__(self) -> None:
        _digest("candidate ID", self.candidate_id)
        _digest("base revision", self.base_revision)

    @property
    def binding_digest(self) -> str:
        return _sha256((self.candidate_id, self.base_revision))

    def to_dict(self) -> dict[str, str]:
        return {"candidate_id": self.candidate_id, "base_revision": self.base_revision}


@dataclass(frozen=True, slots=True)
class SurrogateAdvisory:
    """Bounded ranking data that is never evidence and never a sign-off."""

    rank: int
    score_milli: int

    def __post_init__(self) -> None:
        _bounded_integer("surrogate rank", self.rank, minimum=0, maximum=1_000_000)
        _bounded_integer("surrogate score", self.score_milli, minimum=-1_000_000, maximum=1_000_000)


@dataclass(frozen=True, slots=True)
class AuthoritativeEvidence:
    """Completed, candidate-bound evidence minted by the coordinator-owned executor.

    ``authoritative_output`` is retained only inside this private core object.  It is never
    serialized or returned.  ``evidence_revision`` is computed from those exact bytes, so it is a
    content digest rather than a caller-selected run label.

    ``capability`` is the module-private sentinel used by the supported executor path.  Requiring
    it prevents accidental or serialized-evidence construction; it does not prevent privileged
    same-process Python code from importing private symbols.
    """

    capability: object = field(repr=False, compare=False)
    backend_id: str
    backend_version: str
    domain: SignoffDomain
    candidate_id: str
    base_revision: str
    authoritative_output: bytes = field(repr=False, compare=False)
    completed: bool
    passed: bool
    suppressed: bool
    comparability: SignoffComparability
    repetitions: int

    def __post_init__(self) -> None:
        if self.capability is not _EVIDENCE_CAPABILITY:
            raise ValueError("authoritative evidence capability is invalid")
        _text("backend ID", self.backend_id)
        _text("backend version", self.backend_version, maximum=32)
        if type(self.domain) is not SignoffDomain:
            raise ValueError("evidence domain is unsupported")
        _digest("evidence candidate ID", self.candidate_id)
        _digest("evidence base revision", self.base_revision)
        if (
            type(self.authoritative_output) is not bytes
            or not 1 <= len(self.authoritative_output) <= _MAX_AUTHORITATIVE_OUTPUT_BYTES
        ):
            raise ValueError("authoritative output is outside the supported bound")
        for name in ("completed", "passed", "suppressed"):
            if type(getattr(self, name)) is not bool:
                raise ValueError("evidence completion flags are malformed")
        if type(self.comparability) is not SignoffComparability:
            raise ValueError("evidence comparability is malformed")
        _bounded_integer(
            "evidence repetitions", self.repetitions, minimum=1, maximum=_MAX_REPETITIONS
        )
        if (self.comparability is SignoffComparability.SINGLE_INVOCATION) != (
            self.repetitions == 1
        ):
            raise ValueError("evidence comparability does not match its repetition count")

    @property
    def evidence_revision(self) -> str:
        """Return the content address of the exact private authoritative output bytes."""

        return _bytes_digest(self.authoritative_output)

    @property
    def evidence_digest(self) -> str:
        """Return a deterministic content address for the evidence envelope."""

        return _sha256(
            (
                self.backend_id,
                self.backend_version,
                self.domain.value,
                self.candidate_id,
                self.base_revision,
                self.evidence_revision,
                self.completed,
                self.passed,
                self.suppressed,
                self.comparability.value,
                self.repetitions,
            )
        )

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA,
            "backend_id": self.backend_id,
            "backend_version": self.backend_version,
            "domain": self.domain.value,
            "candidate_id": self.candidate_id,
            "base_revision": self.base_revision,
            "evidence_revision": self.evidence_revision,
            "completed": self.completed,
            "passed": self.passed,
            "suppressed": self.suppressed,
            "comparability": self.comparability.value,
            "repetitions": self.repetitions,
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True, slots=True)
class AuthoritativeSignoffResult:
    """Redacted sign-off union with no public constructor for claimable results.

    The supported construction path reaches a claim only through the evaluator and evidence for a
    registered backend.  As with the evidence sentinel, this sealed-by-convention Python type is
    not a security boundary against privileged same-process imports or monkeypatching.  A claim
    carries what it rests on -- the candidate and revision it is bound to, the content address of
    the evidence, and how many agreeing invocations produced it -- and nothing else.
    """

    status: SignoffStatus
    domain: SignoffDomain
    code: SignoffCode | None = None
    candidate_id: str | None = None
    base_revision: str | None = None
    backend_id: str | None = None
    evidence_digest: str | None = None
    comparability: SignoffComparability | None = None
    repetitions: int | None = None
    advisory_present: bool = False

    def __init__(self, *args: object, **kwargs: object) -> None:
        raise TypeError("authoritative sign-off results are coordinator-created")

    @classmethod
    def _create(
        cls,
        *,
        capability: object,
        status: SignoffStatus,
        domain: SignoffDomain,
        code: SignoffCode | None = None,
        candidate_id: str | None = None,
        base_revision: str | None = None,
        backend_id: str | None = None,
        evidence_digest: str | None = None,
        comparability: SignoffComparability | None = None,
        repetitions: int | None = None,
        advisory_present: bool = False,
    ) -> AuthoritativeSignoffResult:
        if capability is not _RESULT_CAPABILITY:
            raise ValueError("authoritative sign-off result capability is invalid")
        result = object.__new__(cls)
        for name, value in (
            ("status", status),
            ("domain", domain),
            ("code", code),
            ("candidate_id", candidate_id),
            ("base_revision", base_revision),
            ("backend_id", backend_id),
            ("evidence_digest", evidence_digest),
            ("comparability", comparability),
            ("repetitions", repetitions),
            ("advisory_present", advisory_present),
        ):
            object.__setattr__(result, name, value)
        result._validate()
        return result

    def _validate(self) -> None:
        if type(self.status) is not SignoffStatus or type(self.domain) is not SignoffDomain:
            raise ValueError("sign-off result tags are malformed")
        if self.code is not None and type(self.code) is not SignoffCode:
            raise ValueError("sign-off result code is malformed")
        if type(self.advisory_present) is not bool:
            raise ValueError("sign-off advisory flag is malformed")
        if self.status is SignoffStatus.SIGNED_OFF:
            if self.code is not None or self.candidate_id is None or self.base_revision is None:
                raise ValueError("signed-off result is malformed")
            if self.backend_id is None or self.evidence_digest is None:
                raise ValueError("signed-off result is not backend-bound")
            if not _registered_id_for(self.backend_id, self.domain):
                raise ValueError("signed-off result names an unregistered backend")
            _digest("result candidate ID", self.candidate_id)
            _digest("result base revision", self.base_revision)
            _digest("result evidence digest", self.evidence_digest)
            if self.comparability is not SignoffComparability.REPEATED_AGREEMENT:
                raise ValueError("signed-off result does not carry repeated agreement")
            _bounded_integer(
                "result repetitions",
                self.repetitions,
                minimum=_MIN_COMPARABLE_REPETITIONS,
                maximum=_MAX_REPETITIONS,
            )
        else:
            if self.code is None or self.candidate_id is not None or self.base_revision is not None:
                raise ValueError("non-claim/refusal result is malformed")
            if self.backend_id is not None or self.evidence_digest is not None:
                raise ValueError("non-claim/refusal result leaks backend evidence")
            if self.comparability is not None or self.repetitions is not None:
                raise ValueError("non-claim/refusal result leaks backend evidence")

    @property
    def claimed(self) -> bool:
        return self.status is SignoffStatus.SIGNED_OFF

    def to_dict(self) -> dict[str, object]:
        payload: dict[str, object] = {
            "schema": _SCHEMA,
            "status": self.status.value,
            "domain": self.domain.value,
            "advisory_present": self.advisory_present,
        }
        if self.claimed:
            assert self.comparability is not None
            payload.update(
                {
                    "candidate_id": self.candidate_id,
                    "base_revision": self.base_revision,
                    "backend_id": self.backend_id,
                    "evidence_digest": self.evidence_digest,
                    "comparability": self.comparability.value,
                    "repetitions": self.repetitions,
                }
            )
        else:
            assert self.code is not None
            payload.update({"code": self.code.value, "diagnostic": _DIAGNOSTICS[self.code]})
        return payload


def _sha256(parts: tuple[object, ...]) -> str:
    canonical = json.dumps(parts, ensure_ascii=True, separators=(",", ":"), sort_keys=False)
    return _SHA256_PREFIX + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _bytes_digest(value: bytes) -> str:
    return _SHA256_PREFIX + hashlib.sha256(value).hexdigest()


def _domain(value: object) -> SignoffDomain | None:
    if type(value) is SignoffDomain:
        return value
    if type(value) is str:
        try:
            return SignoffDomain(value)
        except ValueError:
            return None
    return None


def _candidate(value: object) -> CandidateBinding | None:
    if type(value) is CandidateBinding:
        return value
    if type(value) is dict:
        assert isinstance(value, dict)
        if len(value) != 2 or set(value) != {"candidate_id", "base_revision"}:
            return None
        try:
            return CandidateBinding(
                candidate_id=value["candidate_id"], base_revision=value["base_revision"]
            )
        except (TypeError, ValueError):
            return None
    return None


def parse_candidate_binding(value: object) -> CandidateBinding | None:
    """Parse a closed redacted candidate binding without echoing hostile values."""

    return _candidate(value)


def parse_authoritative_evidence(value: object) -> AuthoritativeEvidence | None:
    """Accept only the private immutable evidence object; serialized evidence is not intake."""

    if type(value) is AuthoritativeEvidence:
        return value
    return None


def _validate_evidence_binding(
    candidate: CandidateBinding,
    domain: SignoffDomain,
    evidence: AuthoritativeEvidence,
    expected_digest: object = None,
) -> SignoffCode | None:
    """Return the code that denies this evidence a claim, or ``None`` when it earns one.

    The order is deliberate and is the order of the questions: *who* produced this, *what* is it
    about, *when* was it taken, *is it the artefact I was told to expect*, and only then *what
    does it say*.  Answering the last question first would let a passing verdict from the wrong
    authority, or about another candidate, get as far as being read.
    """

    if not _registered_for(evidence.backend_id, evidence.backend_version, evidence.domain):
        return SignoffCode.BACKEND_MISMATCH
    if evidence.domain is not domain:
        return SignoffCode.EVIDENCE_MISMATCH
    if evidence.candidate_id != candidate.candidate_id:
        return SignoffCode.CANDIDATE_MISMATCH
    if evidence.base_revision != candidate.base_revision:
        return SignoffCode.STALE_REVISION
    if expected_digest is not None:
        if type(expected_digest) is not str:
            return SignoffCode.EVIDENCE_MISMATCH
        try:
            _digest("expected evidence digest", expected_digest)
        except ValueError:
            return SignoffCode.EVIDENCE_MISMATCH
        if evidence.evidence_digest != expected_digest:
            return SignoffCode.EVIDENCE_MISMATCH
    if not evidence.completed:
        return SignoffCode.INCOMPLETE_EVIDENCE
    if evidence.suppressed:
        # A run that skipped checks cannot say the checks it skipped would have passed.  This is
        # a refusal rather than a weaker claim because the caller cannot tell from the outside
        # which checks were dropped.
        return SignoffCode.SUPPRESSED_EVIDENCE
    if (
        evidence.comparability is not SignoffComparability.REPEATED_AGREEMENT
        or evidence.repetitions < _MIN_COMPARABLE_REPETITIONS
    ):
        return SignoffCode.UNCOMPARABLE_EVIDENCE
    if not evidence.passed:
        return SignoffCode.FAILED_EVIDENCE
    return None


def parse_surrogate_advisory(value: object) -> SurrogateAdvisory | None:
    """Parse bounded advisory data; this parser never creates evidence."""

    if type(value) is SurrogateAdvisory:
        return value
    if type(value) is not dict:
        return None
    assert isinstance(value, dict)
    if len(value) != 2 or set(value) != {"rank", "score_milli"}:
        return None
    try:
        return SurrogateAdvisory(rank=value["rank"], score_milli=value["score_milli"])
    except (TypeError, ValueError):
        return None


def _stop_code(
    cancelled: CancellationCheck | None, deadline: DeadlineCheck | None
) -> SignoffCode | None:
    for callback, code in (
        (cancelled, SignoffCode.CANCELLED),
        (deadline, SignoffCode.DEADLINE_EXCEEDED),
    ):
        if callback is None:
            continue
        try:
            observed = callback()
            if type(observed) is not bool:
                return code
            if observed:
                return code
        except Exception:
            return code
    return None


def _result(
    status: SignoffStatus,
    domain: SignoffDomain,
    code: SignoffCode,
    *,
    advisory_present: bool,
) -> AuthoritativeSignoffResult:
    return AuthoritativeSignoffResult._create(
        capability=_RESULT_CAPABILITY,
        status=status,
        domain=domain,
        code=code,
        advisory_present=advisory_present,
    )


def evaluate_authoritative_signoff(
    candidate: object,
    domain: object,
    backend: object = None,
    *,
    evidence: object = None,
    expected_evidence_digest: object = None,
    surrogate: object = None,
    cancelled: CancellationCheck | None = None,
    deadline: DeadlineCheck | None = None,
) -> AuthoritativeSignoffResult:
    """Grade already-produced authoritative evidence; never execute a caller-selected authority.

    ``backend`` remains a rejection slot rather than a parameter: ADR-0118 refused caller-supplied
    runners, and the way to keep refusing them is to keep the argument, never inspect it for a
    callable, and refuse deterministically when it is present.  Execution happens in
    ``copper_mcp.authoritative_signoff_executor``, which holds the evidence capability; this
    function only decides whether what it produced is admissible.
    """

    checked_domain = _domain(domain)
    advisory = parse_surrogate_advisory(surrogate) if surrogate is not None else None
    advisory_present = advisory is not None
    if checked_domain is None:
        # Keep the result constructible for hostile input without returning a hostile domain.
        checked_domain = SignoffDomain.DFM
        return _result(
            SignoffStatus.REFUSED,
            checked_domain,
            SignoffCode.UNSUPPORTED_DOMAIN,
            advisory_present=advisory_present,
        )
    checked_candidate = _candidate(candidate)
    if checked_candidate is None:
        return _result(
            SignoffStatus.REFUSED,
            checked_domain,
            SignoffCode.INVALID_CANDIDATE,
            advisory_present=advisory_present,
        )
    if surrogate is not None and advisory is None:
        return _result(
            SignoffStatus.REFUSED,
            checked_domain,
            SignoffCode.INVALID_ADVISORY,
            advisory_present=False,
        )
    stop = _stop_code(cancelled, deadline)
    if stop is not None:
        return _result(
            SignoffStatus.REFUSED, checked_domain, stop, advisory_present=advisory_present
        )
    if backend is not None:
        return _result(
            SignoffStatus.REFUSED,
            checked_domain,
            SignoffCode.INVALID_BACKEND,
            advisory_present=advisory_present,
        )
    checked_evidence = parse_authoritative_evidence(evidence) if evidence is not None else None
    if evidence is not None and checked_evidence is None:
        # Serialized evidence is not intake: an envelope that merely looks right is exactly what
        # a forged claim would look like, and only the capability distinguishes them.
        return _result(
            SignoffStatus.REFUSED,
            checked_domain,
            SignoffCode.INVALID_EVIDENCE,
            advisory_present=advisory_present,
        )
    if checked_evidence is None:
        if advisory is not None:
            # A ranking is a ranking whether or not the domain has an authority behind it.
            code = SignoffCode.SURROGATE_ONLY
        elif checked_domain in registered_signoff_domains():
            code = SignoffCode.NO_AUTHORITATIVE_EVIDENCE
        else:
            code = SignoffCode.NO_AUTHORITATIVE_BACKEND
        return _result(
            SignoffStatus.NON_CLAIM, checked_domain, code, advisory_present=advisory_present
        )
    denial = _validate_evidence_binding(
        checked_candidate, checked_domain, checked_evidence, expected_evidence_digest
    )
    if denial is not None:
        return _result(
            SignoffStatus.REFUSED, checked_domain, denial, advisory_present=advisory_present
        )
    stop = _stop_code(cancelled, deadline)
    if stop is not None:
        # Re-checked after grading: a deadline that expired while the evidence was being read is
        # a reason not to hand back the claim it would have earned.
        return _result(
            SignoffStatus.REFUSED, checked_domain, stop, advisory_present=advisory_present
        )
    return AuthoritativeSignoffResult._create(
        capability=_RESULT_CAPABILITY,
        status=SignoffStatus.SIGNED_OFF,
        domain=checked_domain,
        candidate_id=checked_candidate.candidate_id,
        base_revision=checked_candidate.base_revision,
        backend_id=checked_evidence.backend_id,
        evidence_digest=checked_evidence.evidence_digest,
        comparability=checked_evidence.comparability,
        repetitions=checked_evidence.repetitions,
        advisory_present=advisory_present,
    )


evaluate_signoff = evaluate_authoritative_signoff


__all__ = [
    "AuthoritativeEvidence",
    "AuthoritativeSignoffResult",
    "CandidateBinding",
    "SignoffCode",
    "SignoffComparability",
    "SignoffDomain",
    "SignoffStatus",
    "SurrogateAdvisory",
    "evaluate_authoritative_signoff",
    "evaluate_signoff",
    "parse_authoritative_evidence",
    "parse_candidate_binding",
    "parse_surrogate_advisory",
    "registered_signoff_domains",
]
