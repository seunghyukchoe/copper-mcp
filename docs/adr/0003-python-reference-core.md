# ADR-0003: Python reference core with Rust-ready contracts

- Status: Accepted
- Date: 2026-08-03
- Owners: `@seunghyukchoe`

## Context

The project needs an executable, reviewable foundation before performance measurements exist. Python
has the Tier-1 MCP SDK and AI ecosystem; geometry and GPU kernels may later require stronger systems
performance.

## Decision

Ship the initial reference services, models, and tests in Python 3.11+. Keep routing behind a small
backend-neutral protocol. Introduce Rust only after profiling identifies a kernel and add differential
tests against the reference behavior.

## Consequences

Early contributions and protocol experiments are accessible, but Python is not assumed to meet final
routing-performance goals. Cross-language packaging is postponed until evidence justifies it.

## Alternatives considered

- Rust from the first commit: rejected because the current environment lacks a Rust toolchain and no
  measured bottleneck yet justifies cross-language complexity.
- Python-only forever: rejected as a premature restriction on geometry and accelerator work.
