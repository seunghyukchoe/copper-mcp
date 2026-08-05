# Unshipped KiCad ERC containment experiment

Date: 2026-08-05

## Decision

No schematic-ERC execution capability ships from this experiment. CopperMCP keeps KiCad as a
future authoritative validation target, but an AI-facing wrapper must not claim private-input
containment until it has supported, independently reviewable OS isolation.

## Primary sources

- KiCad 10 documents `kicad-cli sch erc`, JSON output, severity selection, and violation exit
  code `5`: <https://docs.kicad.org/10.0/en/cli/cli.html#schematic-erc>.
- Apple describes App Sandbox as kernel-enforced, least-privilege access control for containing
  damage from compromised apps: <https://developer.apple.com/documentation/xcode/configuring-the-macos-app-sandbox>.
- The local Apple `sandbox-exec(1)` manual on the observed macOS host marks that helper
  deprecated; it is not treated as a durable production isolation API.

## Observed experiment

On the local macOS host, the fixed KiCad 10.0.5 CLI ran ERC on the original deterministic passive
RC fixture. The unshipped, redacted result had zero errors, zero `pin_not_connected` findings,
four warnings, and `clean=false`. This is only an experimental tool observation—not ERC-clean,
electrical, schematic-to-PCB, placement, routing, manufacturing, or release evidence.

The prototype replayed deterministic schematic bytes, fixed the executable and command, discarded
tool text, and accepted only a bounded JSON report. An initial macOS `sandbox-exec` profile used
canonical `/private/var` paths (rather than the `/var` spelling) and could constrain KiCad output
to a fresh private directory. That finding is not a shipping security claim.

## Security blockers

Independent review found two P1 issues:

1. `RLIMIT_FSIZE` limits each file, not total writes. A writable output directory can still be
   filled with many bounded files before post-run cleanup rejects leftovers.
2. KiCad required a broad runtime read rule in the prototype. Under a parser compromise, that
   rule could disclose workspace or user data through the report channel; a private snapshot alone
   is not containment.

We tested a fresh 2 MB HFS+ disk image mounted at the private output path. macOS enforced its
aggregate capacity (1,868 KiB available after formatting), but the legacy profile sandbox did not
honor the mountpoint-only write rule: both a probe and KiCad’s atomic report save were denied, and
no report appeared despite KiCad’s success message. The image was detached after the test. Allowing
broader writes would defeat the boundary, so the prototype was removed rather than weakened.

## Safer next options

Use a supported VM/container or entitlement-backed helper with a quota-backed private volume,
explicit runtime dependency allowlist, no host-home/workspace reads, verified unmount cleanup, and
adversarial overflow/exfiltration tests. Alternatively, retain ERC as a manually invoked local
developer command with no MCP exposure and no privacy-containment claim. Either path needs a new
architecture decision, pinned-tool evidence, and independent security review before implementation.
