"""Workspace isolation: baseline snapshot, working copy, edit application and diffs.

The agent only ever touches ``runs/<task>/workspace``. The source repository is untouched
until a human approves delivery, and ``runs/<task>/baseline`` keeps a pristine copy for
diffs, repair resets and drift checks.
"""

import difflib
import hashlib
import shutil
from pathlib import Path

from .errors import AgentFailure, PolicyViolation
from .spec import safe_relative_path

IGNORED_NAMES = (
    "__pycache__",
    ".git",
    "runs",
    ".pytest_cache",
    "bin",
    "obj",
    ".vs",
    "TestResults",
    "publish",
    "installer",
    "msix-stage",
)
MAX_FILE_BYTES = 200_000


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str | None:
    return sha256_bytes(path.read_bytes()) if path.is_file() else None


def _ignore(_dir: str, names: list[str]) -> list[str]:
    return [n for n in names if n in IGNORED_NAMES]


def manifest(root: Path) -> dict[str, str]:
    root = Path(root)
    result = {}
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root)
        if any(part in IGNORED_NAMES for part in rel.parts) or not path.is_file():
            continue
        result[rel.as_posix()] = sha256_file(path)
    return result


def snapshot(source: Path, baseline: Path) -> dict[str, str]:
    shutil.copytree(source, baseline, ignore=_ignore)
    return manifest(baseline)


def reset_workspace(baseline: Path, workspace: Path) -> None:
    if workspace.exists():
        shutil.rmtree(workspace)
    shutil.copytree(baseline, workspace, ignore=_ignore)


def apply_edits(workspace: Path, edits: list, allowed_files: tuple[str, ...]) -> list[str]:
    """Validate every edit first, then write them all. Either all edits land or none do."""
    allowed = {safe_relative_path(f) for f in allowed_files}
    staged: list[tuple[str, bytes]] = []
    for edit in edits:
        if not isinstance(edit, dict) or set(edit) != {"path", "content"}:
            raise AgentFailure("edit must be an object with exactly 'path' and 'content'")
        rel = safe_relative_path(edit["path"])
        if rel is None or rel not in allowed:
            raise PolicyViolation("POLICY_FAILURE", f"edit targets a path outside allowed_files: {edit['path']!r}")
        if not isinstance(edit["content"], str):
            raise AgentFailure(f"content for {rel} must be a string")
        data = edit["content"].encode("utf-8")
        if len(data) > MAX_FILE_BYTES:
            raise PolicyViolation("RESOURCE_LIMIT", f"edit to {rel} exceeds {MAX_FILE_BYTES} bytes")
        staged.append((rel, data))

    written = []
    for rel, data in staged:
        dst = Path(workspace) / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(data)
        written.append(rel)
    return written


def changed_files(baseline_manifest: dict[str, str], workspace: Path) -> dict[str, str]:
    current = manifest(workspace)
    changes: dict[str, str] = {}
    for path, digest in current.items():
        if path not in baseline_manifest:
            changes[path] = "created"
        elif baseline_manifest[path] != digest:
            changes[path] = "modified"
    for path in baseline_manifest:
        if path not in current:
            changes[path] = "deleted"
    return dict(sorted(changes.items()))


def _lines(path: Path | None) -> list[str]:
    if path is None or not path.is_file():
        return []
    return path.read_bytes().decode("utf-8", errors="replace").splitlines(keepends=True)


def unified_diff(baseline: Path, workspace: Path, changes: dict[str, str]) -> str:
    parts = []
    for rel, status in changes.items():
        before = _lines(Path(baseline) / rel)
        after = _lines(Path(workspace) / rel)
        parts.extend(
            difflib.unified_diff(before, after, fromfile=f"a/{rel}", tofile=f"b/{rel}", lineterm="\n")
        )
    return "".join(parts)
