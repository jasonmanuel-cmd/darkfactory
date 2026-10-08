"""Agent interface and the deterministic scripted agent.

An agent receives the task context and, on repair, the verification evidence from the previous
attempt. It returns a ``Proposal``: an intent statement and whole-file edits. It never gets
a shell, the network, or write access outside the workspace; the runner enforces that.

``ScriptedAgent`` replays a fixed JSON list of proposals. It is the stand-in used for
offline and CI runs, and it is what makes the repair loop testable without a model. A
model-backed agent implements the same ``propose`` method; that adapter is not part of this MVP.
"""

import json
from dataclasses import dataclass
from pathlib import Path

from .errors import AgentFailure


@dataclass(frozen=True)
class Proposal:
    intent: str
    edits: list


class ScriptedAgent:
    name = "scripted"

    def __init__(self, script_path: Path):
        try:
            data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentFailure(f"cannot load agent script {script_path}: {exc}") from exc
        attempts = data.get("attempts") if isinstance(data, dict) else None
        if not isinstance(attempts, list) or not attempts:
            raise AgentFailure("agent script must contain a non-empty 'attempts' list")
        self._attempts = attempts

    def propose(self, attempt_index: int, feedback: str | None) -> Proposal:
        # ``feedback`` is accepted for interface parity; a scripted agent cannot react to it.
        if attempt_index >= len(self._attempts):
            raise AgentFailure(f"agent script exhausted: no proposal for attempt {attempt_index + 1}")
        raw = self._attempts[attempt_index]
        if not isinstance(raw, dict) or not isinstance(raw.get("edits"), list):
            raise AgentFailure(f"attempt {attempt_index + 1} must have an 'edits' list")
        return Proposal(intent=str(raw.get("intent", "")), edits=raw["edits"])
