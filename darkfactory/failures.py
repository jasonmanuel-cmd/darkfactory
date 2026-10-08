"""Failure taxonomy and the predefined response to each category.

Only TEST_FAILURE is ever sent back to the agent, and only while the workflow's retry budget
lasts. Every other category stops the run and escalates to a human.
"""

from enum import Enum

from .verify import CheckResult


class Failure(str, Enum):
    TASK_FAILURE = "TASK_FAILURE"
    AGENT_FAILURE = "AGENT_FAILURE"
    MODEL_FAILURE = "MODEL_FAILURE"
    TOOL_FAILURE = "TOOL_FAILURE"
    ENVIRONMENT_FAILURE = "ENVIRONMENT_FAILURE"
    DEPENDENCY_FAILURE = "DEPENDENCY_FAILURE"
    TEST_FAILURE = "TEST_FAILURE"
    SECURITY_FAILURE = "SECURITY_FAILURE"
    POLICY_FAILURE = "POLICY_FAILURE"
    TIMEOUT = "TIMEOUT"
    RESOURCE_LIMIT = "RESOURCE_LIMIT"
    HUMAN_REQUIRED = "HUMAN_REQUIRED"


class Action(str, Enum):
    REPAIR = "REPAIR"
    ESCALATE = "ESCALATE"


RESPONSES: dict[Failure, Action] = {f: Action.ESCALATE for f in Failure}
RESPONSES[Failure.TEST_FAILURE] = Action.REPAIR

NEXT_ACTIONS: dict[str, list[str]] = {
    "TEST_FAILURE": ["Inspect the verification output in ledger.jsonl", "Re-scope the task or the acceptance criteria if the tests themselves are wrong"],
    "POLICY_FAILURE": ["Check whether the agent needs a file or permission it should not have", "Re-scope allowed_files only if the change is genuinely required"],
    "SECURITY_FAILURE": ["Treat the proposal as suspect; rotate any real credential it contains", "Rerun with a sanitised proposal"],
    "TIMEOUT": ["Raise max_execution_seconds if the work is legitimately larger", "Split the task"],
    "AGENT_FAILURE": ["Inspect the agent script or model output", "Provide a different proposal or redesign the task"],
    "TOOL_FAILURE": ["Confirm the verification command exists in the environment"],
    "HUMAN_REQUIRED": ["Make the scope or risk decision, then resubmit the task"],
    "RESOURCE_LIMIT": ["Reduce the size of the proposed change"],
}


def diagnose(results: list[CheckResult]) -> Failure | None:
    """Map verification results to a failure category, or None if everything passed."""
    if any(r.timed_out for r in results):
        return Failure.TIMEOUT
    if any(r.exit_code is None for r in results):
        return Failure.TOOL_FAILURE
    if any(r.exit_code != 0 for r in results):
        return Failure.TEST_FAILURE
    return None
