"""Agent interface and implementations.

An agent receives the task context and, on repair, the verification evidence from the previous
attempt. It returns a ``Proposal``: an intent statement and whole-file edits. It never gets
a shell, the network, or write access outside the workspace; the runner enforces that. Whatever
an agent returns goes through the same allowlist, secret scan, and verification as any other
proposal, so a model cannot widen its own scope.

Implementations:

* ``ScriptedAgent`` replays a JSON file of proposals. It needs no network, which keeps the
  repair loop testable offline.
* ``ModelAgent`` asks an Anthropic model for a proposal over the Messages API. It uses only
  ``urllib``, so the package still has no third-party dependencies. The HTTP call is injected as
  ``transport``, which lets tests run without network access.
"""

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from .errors import AgentFailure
from .spec import TaskSpec

ANTHROPIC_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
MODEL_MAX_TOKENS = 8000
MODEL_TIMEOUT_S = 120

SYSTEM_PROMPT = (
    "You are the coding agent inside a verified change pipeline. You cannot run commands. "
    "Propose the smallest change that satisfies the task. Respond with one JSON object and nothing else: "
    '{"intent": "<one sentence>", "edits": [{"path": "<file>", "content": "<complete new file content>"}]}. '
    "Only edit files listed as allowed_files. Always give complete file contents, never diffs. "
    "Do not add credentials, keys, or tokens."
)


@dataclass(frozen=True)
class Proposal:
    intent: str
    edits: list
    raw: str = ""


class ScriptedAgent:
    name = "scripted"

    def __init__(self, script_path: Path):
        self.script = str(script_path)
        try:
            data = json.loads(Path(script_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise AgentFailure(f"cannot load agent script {script_path}: {exc}") from exc
        attempts = data.get("attempts") if isinstance(data, dict) else None
        if not isinstance(attempts, list) or not attempts:
            raise AgentFailure("agent script must contain a non-empty 'attempts' list")
        self._attempts = attempts

    def describe(self) -> dict:
        return {"agent": self.name, "script": self.script}

    def propose(self, attempt_index: int, feedback: str | None) -> Proposal:
        # ``feedback`` is accepted for interface parity; a scripted agent cannot react to it.
        if attempt_index >= len(self._attempts):
            raise AgentFailure(f"agent script exhausted: no proposal for attempt {attempt_index + 1}")
        raw = self._attempts[attempt_index]
        if not isinstance(raw, dict) or not isinstance(raw.get("edits"), list):
            raise AgentFailure(f"attempt {attempt_index + 1} must have an 'edits' list")
        return Proposal(intent=str(raw.get("intent", "")), edits=raw["edits"], raw=json.dumps(raw))


def _anthropic_transport(model: str, api_key: str):
    """Return a function (system, user) -> model text, calling the Messages API."""

    def call(system: str, user: str) -> str:
        body = json.dumps(
            {
                "model": model,
                "max_tokens": MODEL_MAX_TOKENS,
                "temperature": 0,
                "system": system,
                "messages": [{"role": "user", "content": user}],
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            ANTHROPIC_URL,
            data=body,
            method="POST",
            headers={
                "content-type": "application/json",
                "x-api-key": api_key,
                "anthropic-version": ANTHROPIC_VERSION,
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=MODEL_TIMEOUT_S) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # Report only the status code; the response body and request headers stay out of the ledger.
            raise AgentFailure(f"model API returned HTTP {exc.code}") from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AgentFailure(f"model API request failed: {type(exc).__name__}") from exc
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        if not text:
            raise AgentFailure("model returned no text")
        return text

    return call


def _extract_json(raw: str) -> dict:
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        raise AgentFailure("model output contained no JSON object")
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError as exc:
        raise AgentFailure(f"model output was not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise AgentFailure("model output JSON must be an object")
    return data


class ModelAgent:
    name = "anthropic"

    def __init__(self, spec: TaskSpec, workspace: Path, model: str, transport):
        self.spec = spec
        self.workspace = Path(workspace)
        self.model = model
        self._call = transport

    def describe(self) -> dict:
        return {
            "agent": self.name,
            "model": self.model,
            "max_tokens": MODEL_MAX_TOKENS,
            "temperature": 0,
            "api_version": ANTHROPIC_VERSION,
        }

    def _prompt(self, feedback: str | None) -> str:
        s = self.spec
        lines = [
            f"Objective: {s.objective}",
            f"Problem: {s.problem_description}",
            "Requirements:",
            *[f"- {r}" for r in s.requirements],
            "Acceptance criteria (checked by the required tests):",
            *[f"- {c}" for c in s.acceptance_criteria],
            "Constraints:",
            *[f"- {c}" for c in s.constraints],
            f"allowed_files: {list(s.allowed_files)}",
            "",
            "Current contents of allowed files:",
        ]
        for rel in s.allowed_files:
            path = self.workspace / rel
            content = path.read_text(encoding="utf-8", errors="replace") if path.is_file() else "(file does not exist)"
            lines += [f"--- {rel} ---", content]
        if feedback:
            lines += [
                "",
                "The previous attempt failed verification. Evidence:",
                feedback,
                "Fix the cause. Do not weaken or delete the tests.",
            ]
        return "\n".join(lines)

    def propose(self, attempt_index: int, feedback: str | None) -> Proposal:
        raw = self._call(SYSTEM_PROMPT, self._prompt(feedback))
        data = _extract_json(raw)
        intent, edits = data.get("intent"), data.get("edits")
        if not isinstance(intent, str) or not isinstance(edits, list):
            raise AgentFailure("model output must have a string 'intent' and a list 'edits'")
        return Proposal(intent=intent, edits=edits, raw=raw)


def build_agent(spec: TaskSpec, root: Path, workspace: Path, transport=None):
    """Create the agent named by ``spec.agent``. ``transport`` overrides the real model client (tests)."""
    kind, _, arg = spec.agent.partition(":")
    if kind == "scripted":
        path = Path(arg)
        return ScriptedAgent(path if path.is_absolute() else Path(root) / path)
    if kind == "anthropic":
        if transport is None:
            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key:
                raise AgentFailure("ANTHROPIC_API_KEY is not set in the environment")
            transport = _anthropic_transport(arg, api_key)
        return ModelAgent(spec, workspace, arg, transport)
    raise AgentFailure(f"unknown agent kind {kind!r}")
