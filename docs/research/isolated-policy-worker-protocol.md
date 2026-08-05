# Isolated reference policy-worker protocol

**Snapshot date:** 2026-08-05

## Scope

This milestone specifies a one-shot, local subprocess boundary for the existing
`deterministic-routing-policy-v1` baseline.  It is intentionally not connected to MCP,
`negotiate_routes`, KiCad IPC, candidate publication, or apply.  The only accepted input is the
neutral ADR-0064 policy view: a board-revision digest and bounded scalar net features, with
`PolicyBounds(0, 0, 0, 0)` and no corridor or repair candidates.  The only successful output is a
digest-bound permutation of those supplied net IDs.  The wire schema cannot encode board bytes,
pad locations, geometry, route vertices, widths, layers, copper, candidates, validation output,
apply tokens, model prompts, provider credentials, endpoints, or plugins.

## Protocol and checks

One UTF-8 JSON request and one UTF-8 JSON response are each capped at 32,768 bytes, have closed
object shapes, reject duplicate keys at every nesting level, reject non-finite constants, and are
canonicalized with sorted keys and SHA-256.  A 256-bit nonce and the request digest bind the reply
to this exact invocation.  The parent additionally checks the reference policy identity, the
policy-input digest, exact net-set permutation, empty window selections, and decision digest.  Any
bad frame, child exit, timeout, cancellation, extra output, identity mismatch, or validation
failure becomes the single fixed `POLICY_WORKER_REJECTED` error; child diagnostics are discarded.

The worker launches only `sys.executable` with an argument sequence, `shell=False`, isolated
Python mode, a replacement three-variable locale/timezone environment, `close_fds=True`, no
passed descriptors, a temporary working directory, and a new POSIX session.  It has no
caller-selected executable or backend.  The parent polls a monotonic deadline and cancellation
callback, then kills the child session before returning the redacted failure.  The child also makes
a best-effort Unix CPU and output-file-size limit before reading its one input frame.

Python documents argument sequences and `sys.executable` as the reliable subprocess form;
`env` replaces rather than extends the parent environment; `close_fds` closes all descriptors
other than standard streams; `start_new_session` invokes `setsid()` on POSIX; and `preexec_fn` is
unsafe with threads.  This design relies on those documented mechanisms rather than a shell or a
thread-unsafe pre-exec hook.  Source: [Python `subprocess` documentation](https://docs.python.org/3/library/subprocess.html#subprocess.Popen).
Python documents `setrlimit` process ceilings as Unix-only and host-dependent; hence limits are
defense in depth and the parent deadline/cancellation path is still authoritative.  Source:
[Python `resource` documentation](https://docs.python.org/3/library/resource.html).

## macOS and non-claims

Apple's App Sandbox is an entitlement-based application sandbox, not a property automatically
conferred on an arbitrary Python subprocess.  This milestone therefore makes **no** claim of
filesystem, network, or device sandboxing on macOS.  Instead it fails closed for every backend
except the in-package deterministic reference backend on all operating systems, including macOS;
there is no public profile argument to select an untrusted evaluator.  A future model or plugin
backend must not reuse this entry point.  It needs a separate ADR with a deployable OS-sandbox
design, explicit network denial, resource/cancellation evidence, and new hostile integration
tests.  Source: [Apple App Sandbox documentation](https://developer.apple.com/documentation/security/app-sandbox).

This protocol is an isolation and provenance milestone, not evidence of improved routing quality,
KiCad DRC success, manufacturability, or a safe model execution environment.  The deterministic
router, candidate validator, authoritative KiCad DRC, and explicit apply authorization remain the
only routes to copper mutation.
