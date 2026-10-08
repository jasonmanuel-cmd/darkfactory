"""Workflow registry: data-driven definitions of what the factory is allowed to automate.

Adding a workflow means adding an entry to ``workflows/registry.json``; no code changes
are needed for a new workflow that reuses the existing pipeline stages.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import FactoryError
from .permissions import CAPABILITIES
from .spec import RISK_LEVELS


@dataclass(frozen=True)
class Workflow:
    id: str
    name: str
    description: str
    task_types: tuple[str, ...]
    max_risk: str
    allowed_permissions: tuple[str, ...]
    allowed_command_prefixes: tuple[str, ...]
    repairable_failures: tuple[str, ...]
    max_retries: int
    autonomy_level: int
    human_approval_required: bool
    rollback_strategy: str
    timeout_seconds: int


_TUPLE_KEYS = ("task_types", "allowed_permissions", "allowed_command_prefixes", "repairable_failures")


def load_registry(path: Path) -> dict[str, Workflow]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    entries = raw.get("workflows") if isinstance(raw, dict) else None
    if not isinstance(entries, list):
        raise FactoryError(f"{path}: expected a top-level 'workflows' list")

    workflows: dict[str, Workflow] = {}
    for entry in entries:
        entry = dict(entry)
        for key in _TUPLE_KEYS:
            entry[key] = tuple(entry[key])
        try:
            wf = Workflow(**entry)
        except TypeError as exc:
            raise FactoryError(f"{path}: invalid workflow entry {entry.get('id')!r}: {exc}") from exc
        if wf.id in workflows:
            raise FactoryError(f"duplicate workflow id {wf.id!r}")
        if wf.max_risk not in RISK_LEVELS:
            raise FactoryError(f"{wf.id}: unknown max_risk {wf.max_risk!r}")
        if not set(wf.allowed_permissions) <= CAPABILITIES:
            raise FactoryError(f"{wf.id}: unknown permissions {sorted(set(wf.allowed_permissions) - CAPABILITIES)}")
        if not 1 <= wf.autonomy_level <= 5:
            raise FactoryError(f"{wf.id}: autonomy_level must be 1-5")
        workflows[wf.id] = wf
    return workflows
