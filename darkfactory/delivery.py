"""Delivery and rollback of reviewed changes to the source repository.

Delivery refuses to overwrite a file that changed after the run's snapshot (drift). Originals
are backed up first so the change can be reversed. Rollback refuses to overwrite a file that
changed after delivery.
"""

import shutil
from pathlib import Path

from .errors import Escalate
from .failures import Failure
from .workspace import manifest, sha256_file


def deliver(source: Path, workspace: Path, baseline: dict, changes: dict, backup_dir: Path) -> list[str]:
    source, workspace, backup_dir = Path(source), Path(workspace), Path(backup_dir)
    current = manifest(source)
    drifted = sorted(p for p in changes if current.get(p) != baseline.get(p))
    if drifted:
        raise Escalate(
            Failure.HUMAN_REQUIRED.value,
            "source changed since the run snapshot; refusing to overwrite",
            evidence={"drifted_paths": drifted},
        )

    applied = []
    for rel, status in changes.items():
        dst = source / rel
        if dst.is_file():
            backup = backup_dir / rel
            backup.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(dst, backup)
        if status == "deleted":
            dst.unlink()
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(workspace / rel, dst)
        applied.append(rel)
    return applied


def restore(source: Path, workspace: Path, changes: dict, backup_dir: Path) -> None:
    source, workspace, backup_dir = Path(source), Path(workspace), Path(backup_dir)
    drifted = []
    for rel, status in changes.items():
        expected = None if status == "deleted" else sha256_file(workspace / rel)
        if sha256_file(source / rel) != expected:
            drifted.append(rel)
    if drifted:
        raise Escalate(
            Failure.HUMAN_REQUIRED.value,
            "source changed after delivery; rollback refused to avoid losing later edits",
            evidence={"drifted_paths": drifted},
        )

    for rel, status in changes.items():
        dst = source / rel
        if status == "created":
            if dst.exists():
                dst.unlink()
        else:
            shutil.copy2(backup_dir / rel, dst)
