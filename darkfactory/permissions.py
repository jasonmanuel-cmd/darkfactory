"""Capability-based permissions, granted per task and per actor.

Two layers must both allow an action:

1. The task requests a capability, and the workflow allows it (checked once at start).
2. The acting component (planner, coding agent, verifier, deliverer) holds that capability.

Agents never receive ambient access; the coding agent cannot run tests or deliver,
and the verifier cannot write to the repository.
"""

from .errors import PolicyViolation

CAPABILITIES = frozenset(
    {
        "READ_REPOSITORY",
        "WRITE_REPOSITORY",
        "EXECUTE_TESTS",
        "INSTALL_DEPENDENCIES",
        "ACCESS_NETWORK",
        "ACCESS_DATABASE",
        "CREATE_PULL_REQUEST",
        "MERGE_PULL_REQUEST",
        "DEPLOY",
        "ACCESS_SECRETS",
        "MODIFY_INFRASTRUCTURE",
    }
)

ACTOR_GRANTS: dict[str, frozenset[str]] = {
    "planner": frozenset({"READ_REPOSITORY"}),
    "coding_agent": frozenset({"READ_REPOSITORY", "WRITE_REPOSITORY"}),
    "verifier": frozenset({"READ_REPOSITORY", "EXECUTE_TESTS"}),
    "deliverer": frozenset({"READ_REPOSITORY", "WRITE_REPOSITORY"}),
}


class PermissionPolicy:
    def __init__(self, requested, workflow_allowed, ledger):
        requested = set(requested)
        excess = requested - set(workflow_allowed)
        if excess:
            raise PolicyViolation("POLICY_FAILURE", f"task requests permissions outside its workflow: {sorted(excess)}")
        self.task_grants = frozenset(requested)
        self.ledger = ledger

    def check(self, actor: str, capability: str) -> None:
        allowed = capability in self.task_grants and capability in ACTOR_GRANTS.get(actor, frozenset())
        self.ledger.record("permission_decision", actor=actor, capability=capability, allowed=allowed)
        if not allowed:
            raise PolicyViolation("POLICY_FAILURE", f"{actor} may not use {capability}")
