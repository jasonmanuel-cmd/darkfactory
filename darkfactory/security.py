"""Security scan of changed files. Reports rule name and location only, never the matched value."""

import re
from pathlib import Path

SECRET_RULES = (
    ("aws_access_key_id", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    (
        "hardcoded_credential",
        re.compile(r"(?i)\b(password|passwd|secret|api[_-]?key|token)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"),
    ),
)


def scan_files(workspace: Path, paths: list[str]) -> list[dict]:
    findings = []
    for rel in paths:
        target = Path(workspace) / rel
        if not target.is_file():
            continue
        text = target.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for rule, pattern in SECRET_RULES:
                if pattern.search(line):
                    findings.append({"path": rel, "line": lineno, "rule": rule})
    return findings
