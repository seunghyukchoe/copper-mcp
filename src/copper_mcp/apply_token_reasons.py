"""The closed set of reasons an apply token is withheld, and the order they are decided in.

Six preview surfaces can return ``apply_token: null`` and, before this module existed, a
caller who received one could not tell which of eight causes produced it (``R-149``). The
vocabulary lives here, in a leaf module that imports nothing from this package, for two
reasons: it is the only way every surface can share one definition without an import cycle
through :mod:`copper_mcp.apply`, and a set defined beside any one surface would soon describe
that surface rather than all of them.

**Nothing in a reason comes from the board.** The literals are fixed strings chosen here, they
carry no digit, path, net, reference designator or coordinate, and no surface may interpolate
one. A withheld reason is disclosed to whoever asked for the preview; it says why a capability
was refused and nothing about the design it was refused for.
"""

from __future__ import annotations

from typing import Final, Literal, get_args

#: Why an apply token is absent from a preview response. **This is the whole set**: every
#: surface that can return ``apply_token: null`` names one of these and nothing else, and the
#: only place the vocabulary is written down is here.
#:
#: The literals are deliberately *bare*. A withheld reason travels to whoever asked for the
#: preview, so it may not carry a digit, a path, a net, a reference designator, or any other
#: value derived from the board — a caller learns why the capability was refused and learns
#: nothing about the board it was refused for.
ApplyTokenWithheldReason = Literal[
    # The surface mints no capability at all — a live single-layer proposal, a live placement
    # proposal, or the file-backed layered seam. Asking again cannot change the answer.
    "unsupported_surface",
    # ``include_apply_token`` was false. This is the ordinary case and it is not a refusal.
    "not_requested",
    # The operator did not enable apply, or the embedder wired no token authority.
    "apply_disabled",
    # There is nothing to authorize: the preview proposed no candidate.
    "no_candidate",
    # A placement candidate that moves no footprint. Applying it would write the board it read.
    "no_move",
    # The board carries revision-derived geometry identities the append-only apply engine
    # refuses, so a token would name a write that could only fail (ADR-0025).
    "board_not_appliable",
    # The candidate was shaped by verified zone fill, which the apply process cannot reproduce,
    # so its replay is guaranteed to refuse (#163, ADR-0103).
    "fill_bound_candidate",
    # The source-preserving replay refused this candidate. Until PR #205 this branch was a
    # bare ``except ...: pass`` and the caller received an unexplained ``null`` (R-149).
    "replay_refused",
]

#: The same set as a value, derived from the type rather than retyped beside it, so the two
#: cannot drift. Membership tests and response validation use this.
APPLY_TOKEN_WITHHELD_REASONS: Final[frozenset[str]] = frozenset(get_args(ApplyTokenWithheldReason))


def apply_token_withheld_reason(
    *,
    requested: bool,
    apply_enabled: bool,
    has_candidate: bool,
    surface_mints_tokens: bool = True,
    candidate_moves: bool = True,
    board_appliable: bool = True,
    fill_bound: bool = False,
    replay_accepted: bool = True,
) -> ApplyTokenWithheldReason | None:
    """Name why no apply token is issued, or return ``None`` when one may be.

    Every surface that can withhold a token calls exactly this function, so the precedence
    among reasons is written once. An unsupported surface is named first because no request or
    operator setting can make it mint a token. On a surface that does mint, the remaining order
    is what a caller can act on: what the *request* asked for, then what the *server* permits,
    then what the *proposal* is, then what the *board and its replay* allow. A caller who did not
    ask for a token is told that before anything about the board, because nothing further about
    the board is any of their business.

    ``None`` is the single gate to issuance. A surface that mints a token on any condition this
    function does not know about has left the set open, which is the failure mode the closed
    vocabulary above exists to prevent.
    """

    if not surface_mints_tokens:
        return "unsupported_surface"
    if not requested:
        return "not_requested"
    if not apply_enabled:
        return "apply_disabled"
    if not has_candidate:
        return "no_candidate"
    if not candidate_moves:
        return "no_move"
    if not board_appliable:
        return "board_not_appliable"
    if fill_bound:
        return "fill_bound_candidate"
    if not replay_accepted:
        return "replay_refused"
    return None


__all__ = [
    "APPLY_TOKEN_WITHHELD_REASONS",
    "ApplyTokenWithheldReason",
    "apply_token_withheld_reason",
]
