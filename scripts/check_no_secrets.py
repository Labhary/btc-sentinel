#!/usr/bin/env python3
"""Fail CI when a common credential shape is committed."""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_PARTS = {
    ".git",
    ".venv",
    ".wrangler",
    "__pycache__",
    ".pytest_cache",
    ".ruff_cache",
    "coverage",
    "node_modules",
}
SKIP_SUFFIXES = {".db", ".sqlite", ".sqlite3", ".zip", ".png", ".jpg", ".pdf"}

PATTERNS = {
    "Telegram bot token": re.compile(r"(?<![A-Za-z0-9_-])\d{6,12}:[A-Za-z0-9_-]{20,}"),
    "GitHub classic token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
}


def files_to_scan() -> list[Path]:
    return [
        path
        for path in ROOT.rglob("*")
        if path.is_file()
        and not any(part in SKIP_PARTS for part in path.parts)
        and path.suffix.lower() not in SKIP_SUFFIXES
    ]


def main() -> int:
    findings: list[str] = []
    for path in files_to_scan():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for label, pattern in PATTERNS.items():
            if pattern.search(text):
                findings.append(f"{path.relative_to(ROOT)}: possible {label}")
    if findings:
        print("Potential secrets detected:", file=sys.stderr)
        for finding in findings:
            print(f"- {finding}", file=sys.stderr)
        return 1
    print("Secret scan passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
