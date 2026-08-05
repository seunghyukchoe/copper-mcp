"""Isolated, one-shot evaluator for the fixed deterministic routing-policy backend.

The parent launches only its own Python executable in isolated mode and passes a
single closed JSON frame.  No caller-selected executable, module, profile,
model endpoint, or environment is admitted.  See
``docs/research/isolated-policy-worker-protocol.md`` for the resource and
platform limits of this deliberately narrow milestone.
"""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

from copper_mcp.routing.policy import (
    DeterministicReferencePolicy,
    RoutingPolicyDecision,
    RoutingPolicyInput,
    evaluate_policy,
    policy_decision_digest,
)
from copper_mcp.routing.policy_worker_protocol import (
    MAX_POLICY_WORKER_FRAME_BYTES,
    POLICY_WORKER_REJECTED,
    PolicyWorkerProtocolError,
    PolicyWorkerRequest,
    PolicyWorkerResponse,
    canonical_policy_worker_request_bytes,
    canonical_policy_worker_response_bytes,
    decode_policy_worker_request,
    decode_policy_worker_response,
    policy_worker_request_digest,
    rejected_policy_worker_response,
    validate_policy_worker_response,
)

_MAX_TIMEOUT_SECONDS: Final = 2.0
_MIN_TIMEOUT_SECONDS: Final = 0.01
_POLL_SECONDS: Final = 0.02
_SAFE_ENV: Final = {"LANG": "C", "LC_ALL": "C", "TZ": "UTC"}
_SOURCE_ROOT: Final = Path(__file__).resolve().parents[2]
_BOOTSTRAP: Final = (
    "import runpy,sys;"
    "source,module=sys.argv[1:3];"
    "sys.path.insert(0,source);"
    "sys.argv=[module,*sys.argv[3:]];"
    "runpy.run_module(module,run_name='__main__')"
)


class PolicyWorkerError(RuntimeError):
    """Fixed parent-visible worker rejection; it intentionally has no child output."""

    def __init__(self) -> None:
        super().__init__(POLICY_WORKER_REJECTED)


def _worker_command() -> tuple[str, ...]:
    """Return the only allowed child: this package's fixed reference worker."""

    return (
        sys.executable,
        "-I",
        "-c",
        _BOOTSTRAP,
        str(_SOURCE_ROOT),
        "copper_mcp.routing.policy_worker",
        "--serve-reference-v1",
    )


def _spawn_worker() -> subprocess.Popen[bytes]:
    # Python documents ``env`` as a replacement rather than an extension of the
    # parent environment, ``close_fds`` as closing inherited FDs, and
    # ``start_new_session`` as creating a POSIX session.  Source:
    # https://docs.python.org/3/library/subprocess.html#subprocess.Popen
    return subprocess.Popen(  # noqa: S603 - command is the fixed in-package _worker_command.
        _worker_command(),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        cwd=tempfile.gettempdir(),
        env=_SAFE_ENV,
        shell=False,
        start_new_session=os.name == "posix",
    )


def _terminate(process: subprocess.Popen[bytes]) -> None:
    """Terminate the one-shot child/session and discard all its output."""

    try:
        if os.name == "posix" and process.pid is not None:
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.communicate(timeout=_POLL_SECONDS)
    except (OSError, subprocess.SubprocessError):
        pass


def _run_closed_frame(
    frame: bytes,
    *,
    timeout_seconds: float,
    cancelled: Callable[[], bool] | None,
) -> bytes:
    if cancelled is not None and cancelled():
        raise PolicyWorkerError()
    try:
        process = _spawn_worker()
    except (OSError, subprocess.SubprocessError) as error:
        raise PolicyWorkerError() from error
    deadline = time.monotonic() + timeout_seconds
    first_input: bytes | None = frame
    try:
        while True:
            if cancelled is not None and cancelled():
                raise PolicyWorkerError()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise PolicyWorkerError()
            try:
                stdout, _stderr = process.communicate(
                    input=first_input,
                    timeout=min(_POLL_SECONDS, remaining),
                )
                if process.returncode != 0 or len(stdout) > MAX_POLICY_WORKER_FRAME_BYTES:
                    raise PolicyWorkerError()
                return stdout
            except subprocess.TimeoutExpired:
                first_input = None
    except PolicyWorkerError:
        _terminate(process)
        raise
    except (OSError, subprocess.SubprocessError) as error:
        _terminate(process)
        raise PolicyWorkerError() from error


def evaluate_reference_policy_in_worker(
    policy_input: RoutingPolicyInput,
    *,
    timeout_seconds: float = 1.0,
    cancelled: Callable[[], bool] | None = None,
) -> RoutingPolicyDecision:
    """Return one reference decision or a fixed rejection before routing begins.

    There is no profile selector.  Therefore an untrusted/non-reference backend
    cannot be selected on macOS (or any other platform), and nothing in this
    module can apply or construct copper.
    """

    if (
        isinstance(timeout_seconds, bool)
        or not isinstance(timeout_seconds, float)
        or not _MIN_TIMEOUT_SECONDS <= timeout_seconds <= _MAX_TIMEOUT_SECONDS
        or (cancelled is not None and not callable(cancelled))
    ):
        raise PolicyWorkerError()
    try:
        request = PolicyWorkerRequest(nonce=secrets.token_hex(32), policy_input=policy_input)
        response_frame = _run_closed_frame(
            canonical_policy_worker_request_bytes(request),
            timeout_seconds=timeout_seconds,
            cancelled=cancelled,
        )
        response = decode_policy_worker_response(response_frame)
        return validate_policy_worker_response(request, response)
    except (OSError, ValueError, PolicyWorkerProtocolError) as error:
        raise PolicyWorkerError() from error


def _apply_worker_resource_limits() -> None:
    """Best-effort Unix CPU/file-size ceilings, applied inside the isolated child.

    Python's ``resource`` module documents these Unix process limits at
    https://docs.python.org/3/library/resource.html .  The parent timeout remains
    authoritative if a host declines a requested lower resource ceiling.
    """

    try:
        import resource

        for limit, ceiling in ((resource.RLIMIT_CPU, 2), (resource.RLIMIT_FSIZE, 131_072)):
            _soft, hard = resource.getrlimit(limit)
            target = ceiling if hard == resource.RLIM_INFINITY else min(ceiling, hard)
            resource.setrlimit(limit, (target, target))
    except (ImportError, OSError, ValueError):
        return


def _serve_reference_once(frame: bytes) -> bytes:
    """Evaluate only the allowlisted in-package reference policy and redact failure."""

    try:
        request = decode_policy_worker_request(frame)
        decision = evaluate_policy(DeterministicReferencePolicy(), request.policy_input)
        response = PolicyWorkerResponse(
            nonce=request.nonce,
            request_digest=policy_worker_request_digest(request),
            status="ok",
            decision=decision,
            decision_digest=policy_decision_digest(decision),
        )
    except (TypeError, ValueError):
        response = rejected_policy_worker_response()
    return canonical_policy_worker_response_bytes(response)


def main(argv: Sequence[str] | None = None) -> int:
    """Serve exactly one reference-policy frame; no general worker CLI exists."""

    arguments = tuple(sys.argv[1:] if argv is None else argv)
    if arguments != ("--serve-reference-v1",):
        return 2
    _apply_worker_resource_limits()
    frame = sys.stdin.buffer.read(MAX_POLICY_WORKER_FRAME_BYTES + 1)
    sys.stdout.buffer.write(_serve_reference_once(frame))
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":  # pragma: no cover - invoked by the isolated child.
    raise SystemExit(main())
