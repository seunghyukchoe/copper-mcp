#!/usr/bin/env python3
"""High-confidence repository secret scan without printing secret values."""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MAX_SCAN_BYTES = 2 * 1024 * 1024
EXCLUDED_PARTS = {".git", ".venv", "dist", "build", "target", "__pycache__"}
ALLOWLISTED_FILES = {".env.example"}
PATTERNS = {
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "OpenAI-style key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "generic assigned secret": re.compile(
        r"(?i)\b(?:api[_-]?key|access[_-]?token|client[_-]?secret|password)\b\s*[:=]\s*"
        r"['\"]?(?!disabled\b|none\b|changeme\b|example\b|placeholder\b)[A-Za-z0-9_./+=-]{16,}"
    ),
}


def candidate_files() -> list[Path]:
    git = shutil.which("git")
    if git is None:
        raise SystemExit("git is required for the secret scan")
    command = [
        git,
        "ls-files",
        "--cached",
        "--others",
        "--exclude-standard",
        "-z",
    ]
    result = subprocess.run(command, cwd=ROOT, check=True, capture_output=True)  # noqa: S603
    return [ROOT / item.decode() for item in result.stdout.split(b"\0") if item]


def main() -> int:
    findings: list[tuple[Path, int, str]] = []
    for path in candidate_files():
        relative = path.relative_to(ROOT)
        if path.name in ALLOWLISTED_FILES or EXCLUDED_PARTS.intersection(relative.parts):
            continue
        try:
            if not path.is_file() or path.stat().st_size > MAX_SCAN_BYTES:
                continue
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for line_number, line in enumerate(text.splitlines(), start=1):
            for label, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((relative, line_number, label))

    if findings:
        print("Potential secrets detected; values are intentionally redacted:", file=sys.stderr)
        for path, line, label in findings:
            print(f"  {path}:{line}: {label}", file=sys.stderr)
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
