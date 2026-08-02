# Benchmark Ledger

No routing benchmark claims are recorded because the repository does not yet ship a router.

Every future entry must include:

| Field | Requirement |
|---|---|
| Run ID | Stable content-addressed identifier |
| Date and commit | UTC date, exact Git commit, dirty-tree status |
| Environment | CPU/GPU, memory, OS, dependency and KiCad versions |
| Dataset | Name, licence, provenance, split, and exclusion rules |
| Configuration | Router, policy/model digest, seed, time/memory budget |
| Metrics | Completion, hard DRC, unrouted, vias, length, runtime, memory, cleanup |
| Artifacts | Machine-readable results and logs without proprietary content |
| Interpretation | Limitations, failures, and comparison caveats |

Never delete failed runs. Supersede an invalid result with a linked corrective entry.
