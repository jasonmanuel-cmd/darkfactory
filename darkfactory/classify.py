"""Task classification: picks a workflow and decides whether the risk is within its limit."""

from dataclasses import dataclass

from .errors import Block
from .registry import Workflow
from .spec import RISK_LEVELS, TaskSpec

RISK_RANK = {level: rank for rank, level in enumerate(RISK_LEVELS)}
CRITICAL_PREFIXES = (".github/", "infra/", "deploy/")
CRITICAL_SUFFIXES = (".tf", ".tfvars")
HIGH_MARKERS = ("secret", ".env", "dockerfile", "deploy", "terraform", "auth")


@dataclass(frozen=True)
class Classification:
    task_type: str
    workflow_id: str
    declared_risk: str
    risk_level: str
    within_policy: bool
    reasons: tuple[str, ...]


def _max_risk(a: str, b: str) -> str:
    return a if RISK_RANK[a] >= RISK_RANK[b] else b


def classify(spec: TaskSpec, workflows: dict[str, Workflow]) -> Classification:
    candidates = sorted((w for w in workflows.values() if spec.task_type in w.task_types), key=lambda w: w.id)
    if not candidates:
        supported = sorted({t for w in workflows.values() for t in w.task_types})
        raise Block(f"unsupported task_type {spec.task_type!r}; supported: {supported}")
    workflow = candidates[0]

    risk = spec.risk_level
    reasons: list[str] = []
    for path in spec.allowed_files:
        lower = path.lower()
        if lower.startswith(CRITICAL_PREFIXES) or lower.endswith(CRITICAL_SUFFIXES):
            risk = _max_risk(risk, "CRITICAL")
            reasons.append(f"{path}: infrastructure or CI path")
        elif any(marker in lower for marker in HIGH_MARKERS):
            risk = _max_risk(risk, "HIGH")
            reasons.append(f"{path}: sensitive path")

    within = RISK_RANK[risk] <= RISK_RANK[workflow.max_risk]
    if not within:
        reasons.append(f"risk {risk} exceeds workflow {workflow.id} limit {workflow.max_risk}")
    return Classification(
        task_type=spec.task_type,
        workflow_id=workflow.id,
        declared_risk=spec.risk_level,
        risk_level=risk,
        within_policy=within,
        reasons=tuple(reasons),
    )
