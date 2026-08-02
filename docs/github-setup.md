# GitHub Repository Setup Checklist

The files in this repository configure automation, but several protections live in GitHub settings
and must be enabled after the remote repository is created.

## Repository metadata

- Create the public repository as `seunghyukchoe/copper-mcp` or update all canonical URLs first.
- Description: `Local-first, AI-extensible PCB automation through MCP.`
- Topics: `pcb`, `eda`, `kicad`, `mcp`, `autorouting`, `open-hardware`, `ai`.
- Enable Issues, Discussions, Projects, Releases, and the Security tab.
- Disable the wiki so architecture documentation remains versioned with code.
- Add a social preview only after project branding is approved.

## Security settings

- Enable Dependabot alerts and security updates.
- Enable secret scanning and push protection.
- Enable private vulnerability reporting.
- Keep Actions permissions read-only by default; allow write permissions only in reviewed workflows.
- Review and eventually pin third-party actions to full commit SHAs.

## Main-branch ruleset

Require:

- Pull requests with at least one approving review.
- Code-owner review for owned paths.
- Dismissal of stale approvals after new commits.
- Resolved conversations.
- Linear history and no force pushes or deletion.
- Passing `CI / Python 3.11`, `CI / Python 3.12`, `CI / Python 3.13`, `CodeQL`, and security checks.
- Signed commits or vigilant mode when contributor tooling permits it.

Permit maintainers to bypass only for a documented security emergency.

## Labels and project board

Run:

```bash
python scripts/sync_labels.py --repo seunghyukchoe/copper-mcp
```

Create a public roadmap project with `Backlog`, `Ready`, `In progress`, `In review`, and `Done`.
Track milestone, priority, area, target release, and blocking relationship.

## First release

Complete the `0.1.0` milestone, close the changelog section, update the release ledger, run
`make check`, then follow [the release process](releasing.md). Do not enable PyPI publication until
trusted publishing and package ownership receive a separate security review.
