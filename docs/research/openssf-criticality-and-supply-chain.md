# OpenSSF criticality and supply-chain research

**Snapshot date:** 2026-08-04
**Repository:** `github.com/seunghyukchoe/copper-mcp`
**Status:** diagnostic baseline; not a release or security certification

## What the measures mean

[OpenSSF Criticality Score](https://github.com/ossf/criticality_score#criticality-score) is an
activity and adoption proxy. Its inputs include project age and recent updates, contributors,
organization context, commit activity, releases, issue activity, discussion, and commit mentions.
It can reveal a young or single-maintainer project's public-sustainability gap, but it does not
measure routing correctness, security, electrical safety, or maintainer intent. The [OpenSSF project
description](https://openssf.org/projects/criticality-score/) is the authoritative overview.

[OpenSSF Scorecard](https://github.com/ossf/scorecard/blob/main/docs/checks.md) is a separate
supply-chain-practice assessment. It covers workflow, dependency, code-review, release, and
security controls; it must not be reported as the Criticality Score.

## CopperMCP baseline

The repository was created on 2026-08-02. The public Criticality Score dataset/API snapshot
available on 2026-08-04 did not yet contain a row for this newly created repository, so `0.23/1.00`
is a reconstruction from the public GitHub signals and the published formula, not an official
score. The reconstruction used the following public inputs and should be recomputed rather than
copied forward:

| Signal | Snapshot input |
|---|---:|
| Repository age / last update | `0` / `0` months |
| Contributors | `2` |
| Organization signal | `0` |
| Main-branch commit activity | `34/52` weeks, about `0.65` commits/week |
| Releases | `4` |
| Updated / closed issues | `51` / `44` |
| Issue comments / updated issue | `95` / `51`, about `1.86` |
| Commit mentions | `0` |

These are public repository signals, not private contributor analytics. The reconstruction is
rounded and is not a substitute for the official service.

The distinct Scorecard API snapshot for the default branch's 2026-08-04 commit reported `5.8/10`.
The useful gaps were packaging, code review/branch protection, maintained-project age, signed
releases, CII best practices, fuzzing, and contributor breadth. Several checks were already strong:
dependency update, dangerous-workflow, binary-artifact, license, vulnerability, SAST, and CI-test
coverage. “Maintained” and contributor breadth will change with time and real participation; they
must not be manufactured.

## Engineering interpretation

The roadmap therefore treats `0.4` as an observation after sustainable progress, not an optimization
objective. The practical work is:

1. Make it easy for a new engineer to run a fixture, open a focused issue, and submit a reviewable
   change.
2. Make releases reproducible, attributable, and independently verifiable.
3. Make review, branch protection, dependency updates, CodeQL, Scorecard, and security reporting
   enforceable on GitHub rather than merely documented.
4. Publish useful, licensed audio-board examples and benchmark evidence that downstream users can
   reproduce without exposing proprietary designs.
5. Build maintainer redundancy and record monthly public health evidence without private data.

These are valid project improvements even if the Criticality formula changes. A score increase that
comes only from artificial activity is considered a regression in project integrity.

## Planned evidence

Each monthly snapshot should include the capture date, default-branch commit, official score/API URL
when available, Scorecard result URL, and the public counts used for any fallback reconstruction.
The snapshot belongs in an append-only ledger entry; it must not include tokens, credentials, private
board content, or contributor-sensitive data.
