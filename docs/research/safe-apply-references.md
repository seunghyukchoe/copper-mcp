# Safe candidate application references

Research grounding for the M3 apply contract, gathered 2026-08-04. Concepts inform CopperMCP's own
implementation; no external code is copied.

## File mutation and KiCad's own behavior

- Atomic replace = write-temp, fsync, rename, fsync-parent; rename gives name atomicity (readers
  never see a torn file) while durability needs the fsyncs. The FITO preprint (arXiv 2603.01384)
  catalogs the crash-consistency limits: execution order is not persistence order, and a failed
  fsync is not retryable.
- KiCad itself saves via temp+rename (network-filesystem failure reports #15620, #16017, #21330),
  so temp naming must not collide and network-FS failures are a known class to fail loudly on.
- KiCad's `.lck` lockfiles are advisory, carry username+host only (no PID, no liveness), and leak
  (#23734, #23220, #16034). A present lockfile is a hint a GUI may be open — a hard refusal
  condition for apply, never overridden.
- pcbnew has no external-change watcher: if KiCad holds the board open and the user saves later,
  the GUI silently overwrites any external edit. Apply therefore requires KiCad closed, stated in
  the contract.

## Undo transactions over IPC

- kicad-python (MIT, KiCad >= 9 opt-in IPC API) exposes begin_commit / push_commit / drop_commit —
  a genuine single-undo-step transaction pushed into a running KiCad. Caveats: socket+token are
  per-instance and injected at plugin launch; the API cannot open documents in GUI mode; busy
  timeouts require retry; SWIG bindings die in KiCad 11.
- Decisive v0.1 exclusion: IPC mutates an in-memory document whose state cannot be bound to our
  file digest, so revision binding is unsound there. File-level first; IPC apply is v0.2 material.
- No credible three-way merge exists for .kicad_pcb; the field locks non-mergeable artifacts and
  reviews derived artifacts. Do not attempt merge.

## Round-trip fidelity

- The lossless CST / span-splice pattern (Oil Shell lossless syntax tree; rowan/cstree red-green
  trees) is the standard for partial edits that keep untouched bytes identical. sexpdata
  (BSD-2-Clause) is not formatting-preserving and cannot be used for apply.
- The apply assertion set: untouched byte ranges bit-identical; result reparses through the
  fail-closed Board IR adapter; resulting IR equals source IR plus candidate exactly (ADR-0007's
  replay equality, now against the real file). Route patches are purely additive, which makes the
  assertion total; placement pose edits are not, which re-justifies deferring placement apply
  behind footprint-modelling Board IR 0.2.

## Authorization and revision races

- OWASP LLM06:2025: downstream systems must independently enforce authorization — never the model.
  MCP tool annotations (destructiveHint etc., spec 2025-03-26) are advisory hints, not
  enforcement; elicitation (2025-06-18) is the standards-track confirm step but adoption is early.
- Pattern adopted: operator opt-in flag (default off) + a single-use HMAC apply token derived from
  (candidate_id, base_revision, relative_path) issued by the preview — server-enforced and
  model-independent; annotations set truthfully for client UX.
- Terraform's plan/apply staleness (serial-bound saved plans, #27827/#25981) is the closest
  analogue for stale_candidate semantics; ETag/If-Match CAS transfers directly. Whole-file SHA-256
  verified before splice and again before rename under a held lock; Board IR digest as
  defence-in-depth; refusal, never auto-refresh.

## Backup UX

- KiCad already writes `-bak` siblings and size-bounded timestamped project archives; never touch
  them. Apply writes its own timestamped content-addressed pre-apply copy and returns the path;
  git cleanliness is surfaced as advisory metadata, not managed.

## Sources

- https://docs.kicad.org/kicad-python-main/board.html · https://dev-docs.kicad.org/en/apis-and-binding/ipc-api/index.html
- https://docs.kicad.org/doxygen/classLOCKFILE.html · gitlab.com/kicad/code/kicad issues 23734, 23220, 16034, 15620, 16017, 21330, 6802
- https://genai.owasp.org/llmrisk/llm062025-excessive-agency/
- https://blog.modelcontextprotocol.io/posts/2026-03-16-tool-annotations/ (post-cutoff id, verified 2026-08-04 via fetch)
- https://modelcontextprotocol.io/specification/2025-06-18/client/elicitation
- https://github.com/hashicorp/terraform/issues/27827 · /25981
- https://arxiv.org/html/2603.01384 (post-cutoff id, reported as fetched 2026-08-04)
- https://www.oilshell.org/blog/2017/02/11.html · https://lib.rs/crates/cstree
- https://www.cis.upenn.edu/~plclub/blog/2023-12-07-round-trip-properties/
- https://pypi.org/project/sexpdata/
