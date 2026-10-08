"""Planning: a machine-readable plan recorded before any file is touched."""

from pathlib import Path

from .classify import Classification
from .registry import Workflow
from .spec import TaskSpec

PLAN_SCHEMA = "darkfactory.plan/1"


def make_plan(spec: TaskSpec, classification: Classification, workflow: Workflow, source: Path) -> dict:
    existing = [f for f in spec.allowed_files if (Path(source) / f).is_file()]
    new = [f for f in spec.allowed_files if f not in existing]
    return {
        "schema": PLAN_SCHEMA,
        "task_id": spec.task_id,
        "understanding": {
            "objective": spec.objective,
            "problem": spec.problem_description,
            "requirements": list(spec.requirements),
        },
        "workflow": workflow.id,
        "risk_level": classification.risk_level,
        "relevant_files": {"existing": existing, "new": new},
        "dependencies": [],
        "steps": [
            "snapshot the source repository into a baseline and a working copy",
            "agent proposes whole-file edits limited to allowed_files",
            "apply edits to the working copy only",
            "run the security scan on the edited files",
            "run allowlisted verification commands",
            "on test failure: restore baseline, repair with evidence, verify again (within retry budget)",
            "on pass: hold at REVIEW_GATE; deliver only after human approval",
        ],
        "verification": list(spec.required_tests),
        "expected_risks": [
            "the agent's change may be wrong although the tests pass; the review gate covers this",
            "the source may change between snapshot and approval; delivery refuses to overwrite drifted files",
        ],
        "rollback": workflow.rollback_strategy,
        "completion_criteria": list(spec.completion_conditions),
    }
