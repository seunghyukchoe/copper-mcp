"""Print the frozen readiness catalog or check a submission; never certify its artifacts."""

from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from copper_mcp.optimization.contracts import MAX_MESSAGE_BYTES
from copper_mcp.optimization.readiness import FROZEN_CATALOG, assess_readiness, decode_submission


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("submission", nargs="?", type=Path)
    args = parser.parse_args(argv)
    if args.submission is None:
        print(
            json.dumps(
                {"catalog": FROZEN_CATALOG.document(), "digest": FROZEN_CATALOG.digest}, indent=2
            )
        )
        return 0
    try:
        descriptor = os.open(args.submission, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_MESSAGE_BYTES:
                raise ValueError("invalid readiness submission")
            result = assess_readiness(decode_submission(stream.read(MAX_MESSAGE_BYTES + 1)))
    except (OSError, ValueError):
        print(json.dumps({"status": "refused", "code": "invalid_readiness_submission"}))
        return 2
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0 if result["status"] == "eligible_for_independent_artifact_review" else 1


if __name__ == "__main__":
    raise SystemExit(main())
