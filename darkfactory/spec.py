"""Standardized task format and validation.

Unknown or missing fields are rejected rather than ignored, so a typo in a task file
cannot silently weaken a constraint.
"""

import re
from dataclasses import dataclass, fields
from pathlib import PurePosixPath

from .errors import SpecError
from .permissions import CAPABILITIES

TASK_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{2,63}$")
RISK_LEVELS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
MAX_EXECUTION_SECONDS = 900
MAX_RETRIES = 3

STRING_FIELDS = ("task_id", "task_type", "objective", "repository", "problem_description", "risk_level", "agent_script")
LIST_FIELDS = (
    "requirements",
    "acceptance_criteria",
    "constraints",
    "allowed_files",
    "required_tests",
    "security_requirements",
    "permissions",
    "completion_conditions",
)
NONEMPTY_LISTS = ("acceptance_criteria", "allowed_files", "required_tests", "permissions", "completion_conditions")
INT_FIELDS = ("max_execution_seconds", "max_retries")


@dataclass(frozen=True)
class TaskSpec:
    task_id: str
    task_type: str
    objective: str
    repository: str
    problem_description: str
    requirements: tuple[str, ...]
    acceptance_criteria: tuple[str, ...]
    constraints: tuple[str, ...]
    allowed_files: tuple[str, ...]
    required_tests: tuple[str, ...]
    security_requirements: tuple[str, ...]
    permissions: tuple[str, ...]
    risk_level: str
    max_execution_seconds: int
    max_retries: int
    human_approval_required: bool
    completion_conditions: tuple[str, ...]
    agent_script: str


def safe_relative_path(raw: object) -> str | None:
    """Normalise a repo-relative path, or return None if it is unsafe.

    Rejects absolute paths, drive letters, parent traversal, and the factory's own
    bookkeeping directories.
    """
    if not isinstance(raw, str) or not raw.strip() or ":" in raw:
        return None
    path = PurePosixPath(raw.replace("\\", "/"))
    if path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    if path.parts[0] in {".git", "runs"}:
        return None
    return path.as_posix()


def parse_spec(raw: object) -> TaskSpec:
    if not isinstance(raw, dict):
        raise SpecError("task spec must be a JSON object")

    known = {f.name for f in fields(TaskSpec)}
    problems: list[str] = []
    unknown = sorted(set(raw) - known)
    if unknown:
        problems.append(f"unknown fields: {', '.join(unknown)}")
    missing = sorted(known - set(raw))
    if missing:
        problems.append(f"missing fields: {', '.join(missing)}")
    if problems:
        raise SpecError("; ".join(problems))

    values: dict[str, object] = {}
    for name in known:
        value = raw[name]
        if name in LIST_FIELDS:
            if isinstance(value, list) and all(isinstance(v, str) for v in value):
                value = tuple(value)
            else:
                problems.append(f"{name} must be a list of strings")
        elif name in INT_FIELDS:
            if isinstance(value, bool) or not isinstance(value, int):
                problems.append(f"{name} must be an integer")
        elif name == "human_approval_required":
            if not isinstance(value, bool):
                problems.append("human_approval_required must be a boolean")
        elif name in STRING_FIELDS:
            if not isinstance(value, str):
                problems.append(f"{name} must be a string")
        values[name] = value
    if problems:
        raise SpecError("; ".join(problems))

    spec = TaskSpec(**values)  # type: ignore[arg-type]
    semantic = _semantic_problems(spec)
    if semantic:
        raise SpecError("; ".join(semantic))
    return spec


def _semantic_problems(spec: TaskSpec) -> list[str]:
    problems = []
    if not TASK_ID_RE.match(spec.task_id):
        problems.append(f"task_id {spec.task_id!r} must match {TASK_ID_RE.pattern}")
    for name in ("task_type", "objective", "repository", "problem_description", "agent_script"):
        if not getattr(spec, name).strip():
            problems.append(f"{name} must not be empty")
    for name in NONEMPTY_LISTS:
        if not getattr(spec, name):
            problems.append(f"{name} must not be empty")
    if spec.risk_level not in RISK_LEVELS:
        problems.append(f"risk_level must be one of {RISK_LEVELS}")
    if not 10 <= spec.max_execution_seconds <= MAX_EXECUTION_SECONDS:
        problems.append(f"max_execution_seconds must be between 10 and {MAX_EXECUTION_SECONDS}")
    if not 0 <= spec.max_retries <= MAX_RETRIES:
        problems.append(f"max_retries must be between 0 and {MAX_RETRIES}")
    unknown_perms = sorted(set(spec.permissions) - CAPABILITIES)
    if unknown_perms:
        problems.append(f"unknown permissions: {unknown_perms}")
    for path in spec.allowed_files:
        if safe_relative_path(path) is None:
            problems.append(f"allowed_files entry {path!r} is not a safe relative path")
    for command in spec.required_tests:
        if not command.strip():
            problems.append("required_tests must not contain empty commands")
    return problems
