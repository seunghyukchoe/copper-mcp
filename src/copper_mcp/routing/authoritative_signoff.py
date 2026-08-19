"""Closed, non-public sign-off seam for candidate-bound physics evidence.

This module is deliberately smaller than a physics engine.  It is the production-core gate
between a candidate/evidence producer and a future authoritative SI/PI/thermal/DFM adapter.  A
surrogate may rank a candidate, but it cannot enter the claim path.  The only successful result
is evidence returned by the fixed authoritative backend and bound to exactly one candidate and
one base revision.

The module has no MCP, persistence, process, network, geometry, board-byte, prompt, or mutation
authority.  It is intentionally not imported by the public transport layer in this slice.
Positive authoritative execution is deferred until a coordinator-owned adapter and registration
boundary are reviewed; this slice can only return a typed non-claim or refusal.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Final

_SHA256_PREFIX: Final = "sha256:"
_SHA256_LENGTH: Final = 71
_MAX_TEXT: Final = 128
_SCHEMA: Final = "copper-mcp/authoritative-signoff/v1"
_FIXED_BACKEND_ID: Final = "copper-mcp-authoritative-v1"
_FIXED_BACKEND_VERSION: Final = "1"
_MAX_AUTHORITATIVE_OUTPUT_BYTES: Final = 1_048_576


class SignoffDomain(StrEnum):
    """Physics domains for which an authoritative adapter may make a claim."""

    SI = "si"
    PI = "pi"
    THERMAL = "thermal"
    DFM = "dfm"


class SignoffStatus(StrEnum):
    """The redacted result union."""

    SIGNED_OFF = "signed_off"
    NON_CLAIM = "non_claim"
    REFUSED = "refused"


class SignoffCode(StrEnum):
    """Stable, non-echoing refusal and non-claim taxonomy."""

    NO_AUTHORITATIVE_BACKEND = "no_authoritative_backend"
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
    BACKEND_FAILURE = "backend_failure"
    CANCELLED = "cancelled"
    DEADLINE_EXCEEDED = "deadline_exceeded"


_DIAGNOSTICS: Final[dict[SignoffCode, str]] = {
    SignoffCode.NO_AUTHORITATIVE_BACKEND: "authoritative sign-off is unavailable",
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
    SignoffCode.BACKEND_FAILURE: "authoritative sign-off could not be completed",
    SignoffCode.CANCELLED: "authoritative sign-off was cancelled",
    SignoffCode.DEADLINE_EXCEEDED: "authoritative sign-off exceeded its deadline",
}

CancellationCheck = Callable[[], object]
DeadlineCheck = Callable[[], object]
_RESULT_CAPABILITY: Final = object()


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
    """Completed, candidate-bound evidence returned by the fixed backend.

    ``authoritative_output`` is retained only inside this private core object.  It is never
    serialized or returned.  ``evidence_revision`` is computed from those exact bytes, so it is a
    content digest rather than a caller-selected run label.
    """

    backend_id: str
    backend_version: str
    domain: SignoffDomain
    candidate_id: str
    base_revision: str
    authoritative_output: bytes = field(repr=False, compare=False)
    completed: bool
    passed: bool

    def __post_init__(self) -> None:
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
        if type(self.completed) is not bool or type(self.passed) is not bool:
            raise ValueError("evidence completion flags are malformed")

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
            "evidence_digest": self.evidence_digest,
        }


@dataclass(frozen=True, slots=True)
class AuthoritativeSignoffResult:
    """Redacted sign-off union with no public constructor for claimable results.

    The authoritative executor is intentionally absent in this slice.  Keeping the result type
    sealed prevents a caller from manufacturing ``SIGNED_OFF`` while the evidence vocabulary is
    reviewed and a coordinator-owned adapter is still deferred.
    """

    status: SignoffStatus
    domain: SignoffDomain
    code: SignoffCode | None = None
    candidate_id: str | None = None
    base_revision: str | None = None
    backend_id: str | None = None
    evidence_digest: str | None = None
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
        advisory_present: bool = False,
    ) -> AuthoritativeSignoffResult:
        if capability is not _RESULT_CAPABILITY:
            raise ValueError("authoritative sign-off result capability is invalid")
        if status is SignoffStatus.SIGNED_OFF:
            # This slice has no authoritative executor.  Keeping the vocabulary is useful for
            # the reviewed contract, but even code that imports this module's private symbols
            # must not manufacture a claim before the coordinator-owned adapter exists.
            raise ValueError("authoritative sign-off claims are deferred")
        result = object.__new__(cls)
        for name, value in (
            ("status", status),
            ("domain", domain),
            ("code", code),
            ("candidate_id", candidate_id),
            ("base_revision", base_revision),
            ("backend_id", backend_id),
            ("evidence_digest", evidence_digest),
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
            if self.backend_id != _FIXED_BACKEND_ID or self.evidence_digest is None:
                raise ValueError("signed-off result is not backend-bound")
            _digest("result candidate ID", self.candidate_id)
            _digest("result base revision", self.base_revision)
            _digest("result evidence digest", self.evidence_digest)
        else:
            if self.code is None or self.candidate_id is not None or self.base_revision is not None:
                raise ValueError("non-claim/refusal result is malformed")
            if self.backend_id is not None or self.evidence_digest is not None:
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
            payload.update(
                {
                    "candidate_id": self.candidate_id,
                    "base_revision": self.base_revision,
                    "backend_id": self.backend_id,
                    "evidence_digest": self.evidence_digest,
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
    """Purely validate evidence binding without creating a claimable result.

    This helper is intentionally unreachable from the production evaluator while no authoritative
    executor is registered.  It preserves the reviewed candidate/revision/content binding rules
    for a future coordinator-owned adapter without creating a test or caller-selected execution
    seam today.
    """

    if (
        evidence.backend_id != _FIXED_BACKEND_ID
        or evidence.backend_version != _FIXED_BACKEND_VERSION
    ):
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
    expected_evidence_digest: object = None,
    surrogate: object = None,
    cancelled: CancellationCheck | None = None,
    deadline: DeadlineCheck | None = None,
) -> AuthoritativeSignoffResult:
    """Evaluate the deferred sign-off seam without executing caller-selected authority.

    No authoritative adapter is registered in this slice.  ``backend`` is accepted only as a
    compatibility-shaped rejection slot so hostile callers can be refused deterministically; it
    is never inspected for a runner and is never invoked.  A future coordinator-owned adapter must
    be added behind a reviewed, non-request-controlled seam before ``SIGNED_OFF`` is reachable.
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
    code = (
        SignoffCode.SURROGATE_ONLY if advisory is not None else SignoffCode.NO_AUTHORITATIVE_BACKEND
    )
    return _result(SignoffStatus.NON_CLAIM, checked_domain, code, advisory_present=advisory_present)


evaluate_signoff = evaluate_authoritative_signoff


__all__ = [
    "AuthoritativeEvidence",
    "AuthoritativeSignoffResult",
    "CandidateBinding",
    "SignoffCode",
    "SignoffDomain",
    "SignoffStatus",
    "SurrogateAdvisory",
    "evaluate_authoritative_signoff",
    "evaluate_signoff",
    "parse_authoritative_evidence",
    "parse_candidate_binding",
    "parse_surrogate_advisory",
]
