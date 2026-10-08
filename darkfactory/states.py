"""Explicit factory state machine.

Every state change goes through ``check_transition`` and is written to the ledger, so
an illegal jump (for example delivering without passing the review gate) is a hard error
rather than a silent bug.
"""

from enum import Enum

from .errors import FactoryError


class InvalidTransition(FactoryError):
    """A state transition that the state machine does not allow."""


class State(str, Enum):
    RECEIVED = "RECEIVED"
    VALIDATING = "VALIDATING"
    CLASSIFIED = "CLASSIFIED"
    APPROVED = "APPROVED"
    PLANNING = "PLANNING"
    ENVIRONMENT_READY = "ENVIRONMENT_READY"
    EXECUTING = "EXECUTING"
    VERIFYING = "VERIFYING"
    DIAGNOSING = "DIAGNOSING"
    REPAIRING = "REPAIRING"
    REVIEW_GATE = "REVIEW_GATE"
    DELIVERING = "DELIVERING"
    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    PAUSED = "PAUSED"
    ESCALATED = "ESCALATED"
    CANCELLED = "CANCELLED"
    ROLLED_BACK = "ROLLED_BACK"
    FAILED = "FAILED"


S = State

TRANSITIONS: dict[State, frozenset[State]] = {
    S.RECEIVED: frozenset({S.VALIDATING}),
    S.VALIDATING: frozenset({S.CLASSIFIED, S.BLOCKED, S.FAILED}),
    S.CLASSIFIED: frozenset({S.APPROVED, S.BLOCKED, S.ESCALATED, S.FAILED}),
    S.APPROVED: frozenset({S.PLANNING, S.ESCALATED, S.FAILED}),
    S.PLANNING: frozenset({S.ENVIRONMENT_READY, S.ESCALATED, S.FAILED}),
    S.ENVIRONMENT_READY: frozenset({S.EXECUTING, S.ESCALATED, S.FAILED}),
    S.EXECUTING: frozenset({S.VERIFYING, S.ESCALATED, S.FAILED}),
    S.VERIFYING: frozenset({S.DIAGNOSING, S.REVIEW_GATE, S.ESCALATED, S.FAILED}),
    S.DIAGNOSING: frozenset({S.REPAIRING, S.ESCALATED, S.FAILED}),
    S.REPAIRING: frozenset({S.VERIFYING, S.ESCALATED, S.FAILED}),
    S.REVIEW_GATE: frozenset({S.DELIVERING, S.CANCELLED}),
    S.DELIVERING: frozenset({S.COMPLETED, S.ESCALATED, S.FAILED}),
    S.COMPLETED: frozenset({S.ROLLED_BACK}),
    S.ESCALATED: frozenset({S.PAUSED, S.CANCELLED}),
    S.PAUSED: frozenset({S.CANCELLED}),
    S.BLOCKED: frozenset(),
    S.CANCELLED: frozenset(),
    S.ROLLED_BACK: frozenset(),
    S.FAILED: frozenset(),
}


def check_transition(src: State | None, dst: State) -> None:
    """Raise ``InvalidTransition`` unless ``src -> dst`` is allowed. ``src=None`` means start."""
    if src is None:
        if dst is not S.RECEIVED:
            raise InvalidTransition(f"a run must start in RECEIVED, not {dst.value}")
        return
    if dst not in TRANSITIONS[src]:
        raise InvalidTransition(f"{src.value} -> {dst.value} is not allowed")
