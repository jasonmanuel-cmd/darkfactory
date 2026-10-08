"""Exception types shared across the factory.

Two control-flow exceptions drive the pipeline:

* ``Block``    - the task is invalid or unsupported; it never starts executing.
* ``Escalate`` - execution stopped and a human must decide what happens next.

Any other ``PolicyViolation`` or ``AgentFailure`` raised mid-run is converted to an
escalation by the runner, so nothing is silently swallowed.
"""


class FactoryError(Exception):
    """Base class for all factory errors."""


class SpecError(FactoryError):
    """A task specification failed validation."""


class Block(FactoryError):
    """The task cannot be run at all (invalid, unsupported or out of scope)."""


class Escalate(FactoryError):
    """Execution must stop and a human must decide. ``category`` is a Failure value."""

    def __init__(self, category: str, summary: str, evidence: object = None):
        super().__init__(summary)
        self.category = category
        self.summary = summary
        self.evidence = evidence


class PolicyViolation(FactoryError):
    """An action was denied by policy. ``category`` is the failure category to record."""

    def __init__(self, category: str, message: str):
        super().__init__(message)
        self.category = category


class AgentFailure(FactoryError):
    """The agent could not produce a usable proposal."""


class LedgerIntegrityError(FactoryError):
    """The execution record failed hash-chain verification."""
