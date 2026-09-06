"""Check a v0.13 evidence checklist; never publish or modify a repository or board."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from pydantic import ValidationError

from copper_mcp.optimization.contracts import MAX_MESSAGE_BYTES, OptimizationError, bounded_json
from copper_mcp.optimization.release_gate import ReleaseEvidence, release_blockers


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args(argv)
    try:
        descriptor = os.open(args.evidence, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MESSAGE_BYTES:
                raise OptimizationError("optimization release evidence is malformed")
            payload = stream.read(MAX_MESSAGE_BYTES + 1)
        bounded_json(payload)
        evidence = ReleaseEvidence.model_validate_json(payload)
        blockers = release_blockers(evidence)
    except (OSError, OptimizationError, ValidationError, ValueError):
        print(json.dumps({"status": "refused", "code": "invalid_release_evidence"}))
        return 2
    print(
        json.dumps(
            {
                "status": "blocked" if blockers else "eligible_for_independent_artifact_review",
                "blockers": blockers,
                "evaluated_commit": evidence.evaluated_commit,
                "artifact_authenticity_verified": False,
                "release_authorized": False,
            },
            sort_keys=True,
        )
    )
    return 1 if blockers else 0


if __name__ == "__main__":
    raise SystemExit(main())
