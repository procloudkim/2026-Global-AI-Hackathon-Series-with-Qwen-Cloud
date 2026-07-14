#!/usr/bin/env python3
"""Scan tracked and untracked candidate text for credential-shaped values."""

from __future__ import annotations

import re
from pathlib import Path
import subprocess
import sys


PATTERNS = {
    "private_key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "alibaba_access_key_id": re.compile(r"\bLTAI[A-Za-z0-9]{12,30}\b"),
    "dashscope_or_openai_key": re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"),
    "github_token": re.compile(r"\bgh[oprsu]_[A-Za-z0-9]{20,}\b"),
    "aws_access_key_id": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
}


def candidate_files() -> list[Path]:
    output = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        check=True,
        capture_output=True,
    ).stdout
    return [Path(raw.decode("utf-8")) for raw in output.split(b"\0") if raw]


def main() -> int:
    findings: list[tuple[str, int, str]] = []
    files = candidate_files()
    for path in files:
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        if b"\0" in raw:
            continue
        text = raw.decode("utf-8", errors="replace")
        for line_number, line in enumerate(text.splitlines(), start=1):
            for name, pattern in PATTERNS.items():
                if pattern.search(line):
                    findings.append((path.as_posix(), line_number, name))
    if findings:
        for path, line, kind in findings:
            print(f"SECRET_SHAPE_FOUND {path}:{line} type={kind}", file=sys.stderr)
        return 2
    print(f"SECRET_SCAN_PASS candidate_files={len(files)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
