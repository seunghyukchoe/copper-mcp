# ADR-0153: Confined ngspice execution confirms cleanup

- Status: Proposed; isolated executor controls validated
- Date: 2026-09-08
- Owners: CopperMCP maintainers
- Related: [ADR-0151](0151-operating-point-numbers-are-not-physics-authority.md),
  [ADR-0152](0152-joint-native-views-share-acquisition-not-authority.md)

## Decision

Run only a fixed Python driver in the digest-pinned image
`sha256:79f7f67951d42a16f4ada950f42c7b90c425b1e0dc9ad976fbe1a029c3d782a5`.
The driver requires Python 3.13.9 and invokes the fixed ngspice 45.2 command identity
`ngspice-45.2, Build Sun Sep  7 00:00:00 UTC 2025`. Callers supply bounded deck bytes only;
they cannot select an image, executable, command, environment, mount, network or cleanup action.

The executor admits 1 byte through 16 MiB input bytes, bounds work to 60 seconds plus bounded
cleanup grace, and accepts a
strict JSON frame with a 256 KiB raw payload and 64 KiB stdout/stderr streams. It rejects a
nonzero exit, any stderr, absent raw output, malformed/base64-noncanonical fields, duplicate
keys, oversized frames, cancellation and expired deadlines. It retains private raw and diagnostic
bytes only in the immutable redacted internal result; no raw design, model or log is committed or
published.

The container has no network, is read-only, drops capabilities, uses a non-root user, has bounded
CPU/memory/PID/tmpfs resources and receives a reconstructed fixed locale/environment. Its temporary
container name is forcibly removed; cleanup requires confirmed process reap and successful removal.
Failure defaults to refusal. The shared container owner retains the existing router contract and
does not cache authentication or authorize a new execution profile.

After frame processing and successful temporary-workspace cleanup, poll cancellation once more
and then recheck the deadline before delivering the result. A faulting cancellation callback
also refuses. The future coordinator is not responsible for closing this executor-local gap.

Use unbuffered, nonblocking POSIX pipe I/O through the standard-library selector; unsupported
pipe polling refuses. There are no production reader/writer threads or buffered-close locks.
Keep partial-write handling, separate stream ceilings, cancellation and the shared deadline.
Do not poll/reap the group leader while pipe work is incomplete: abort must signal the still-owned
group before reaping it. Already-reaped positive handles refuse without signalling a potentially
reused group. Router exchange and removal exceptions still enter cleanup, and local pipe closure
does not wait for descendant EOF or successful reaping. A failed operation yields no numerical
or routing result; bounded local closure is not proof that escaped descendants were terminated.

Sources: [Python selectors](https://docs.python.org/3.11/library/selectors.html),
[nonblocking descriptors](https://docs.python.org/3.11/library/os.html#os.set_blocking),
[unbuffered subprocess pipes](https://docs.python.org/3.11/library/subprocess.html#subprocess.Popen).

## Non-authority and remaining gates

This isolated executor is not a coordinator, case/topology resolver, deck generator, convergence
checker, model-accuracy assessment, physics result, engineering verdict, human approval or apply
surface. A later internal coordinator must bind captured source/models, explicit operating-point
cases, output interpretation, source freshness and the remaining simulator controls before any
separate engineering assessment.
