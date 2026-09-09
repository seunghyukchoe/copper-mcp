"""Fixed cooperative-cancellation boundary shared by private layered-tree code."""

from __future__ import annotations

from collections.abc import Callable


class LayeredTreeCancellationError(ValueError):
    """A cancellation callback failed or returned a non-boolean value."""


def poll_cancelled(callback: Callable[[], bool] | None) -> bool:
    if callback is None:
        return False
    failed = False
    try:
        result = callback()
    except Exception:
        failed = True
        result = False
    if failed:
        raise LayeredTreeCancellationError("layered tree cancellation check failed")
    if type(result) is not bool:
        raise LayeredTreeCancellationError("layered tree cancellation check failed")
    return result


__all__: list[str] = []
