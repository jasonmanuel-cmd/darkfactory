"""Pipeline orchestration.

RECEIVED -> VALIDATING -> CLASSIFIED -> APPROVED -> PLANNING -> ENVIRONMENT_READY
  -> EXECUTING -> VERIFYING -> (DIAGNOSING -> REPAIRING -> VERIFYING)* -> REVIEW_GATE
  -> [human approval] -> DELIVERING -> COMPLETED

Each stage either advances, blocks the task (invalid input), or escalates to a human with an
evidence report. Work is confined to the run directory until delivery, and every step lands in
the hash-chained ledger.
"""

import hashlib
import json
import platform
import sys
import time
import traceback
from dataclasses import asdict
from pathlib import Path

from . import __version__
from .agents import build_agent
from .classify import classify
from .errors import AgentFailure, Block, Escalate, PolicyViolation, SpecError
from .failures import RESPONSES, Action, Failure, diagnose
from .gate import deliver_run
from .permissions import PermissionPolicy
from .planner import make_plan
from .record import RunRecord
from .registry import Workflow
from .security import scan_files
from .spec import TASK_ID_RE, TaskSpec, parse_spec
from .states import State
from .verify import SAFE_ENV_KEYS, feedback_from, run_checks, validate_commands
from .workspace import apply_edits, changed_files, reset_workspace, sha256_bytes, snapshot, unified_diff

MIN_GATED_AUTONOMY_LEVEL = 3


def _sha_json(payload: object) -> str:
    text = json.dumps(payload, sort_keys=True, default=str, ensure_ascii=False)
    return sha256_bytes(text.encode("utf-8"))


class Runner:
    def __init__(self, root: Path, runs_dir: Path, workflows: dict[str, Workflow], transport=None):
        self.root = Path(root)
        self.runs_dir = Path(runs_dir)
        self.workflows = workflows
        self.transport = transport  # model client override; None uses the real Anthropic API

    def _resolve(self, raw: str) -> Path:
        path = Path(raw)
        return (path if path.is_absolute() else self.root / path).resolve()

    def run(self, raw: object) -> RunRecord:
        spec_sha = _sha_json(raw)
        task_id = raw.get("task_id") if isinstance(raw, dict) else None
        if not (isinstance(task_id, str) and TASK_ID_RE.match(task_id)):
            task_id = f"rejected-{spec_sha[:12]}"

        rec = RunRecord.create(self.runs_dir / task_id)
        rec.record("task_received", task_id=task_id, spec_sha256=spec_sha, spec=raw)
        rec.move(State.RECEIVED, "task received")
        rec.move(State.VALIDATING, "validating specification")
        try:
            self._pipeline(rec, raw)
        except Block as exc:
            rec.record("blocked", reason=str(exc))
            rec.move(State.BLOCKED, str(exc))
        except Escalate as exc:
            rec.escalate(exc.category, exc.summary, evidence=exc.evidence)
        except PolicyViolation as exc:
            rec.escalate(exc.category, str(exc))
        except AgentFailure as exc:
            rec.escalate(Failure.AGENT_FAILURE.value, str(exc))
        except Exception as exc:
            # Unexpected errors are recorded and re-raised; they are never hidden as a normal outcome.
            rec.record("error", error=repr(exc), traceback=traceback.format_exc())
            if rec.can_move(State.FAILED):
                rec.move(State.FAILED, "unexpected internal error")
            raise
        return rec

    def _pipeline(self, rec: RunRecord, raw: object) -> None:
        started = time.monotonic()
        try:
            spec = parse_spec(raw)
        except SpecError as exc:
            raise Block(str(exc)) from exc

        source = self._resolve(spec.repository)
        if not source.is_dir():
            raise Block(f"repository not found: {spec.repository}")

        classification = classify(spec, self.workflows)
        workflow = self.workflows[classification.workflow_id]
        try:
            validate_commands(spec.required_tests, workflow.allowed_command_prefixes)
        except PolicyViolation as exc:
            raise Block(str(exc)) from exc

        rec.record("classification", **asdict(classification))
        rec.move(State.CLASSIFIED, f"workflow {workflow.id}, risk {classification.risk_level}")

        if not classification.within_policy:
            raise Escalate(Failure.HUMAN_REQUIRED.value, "task risk exceeds the workflow's autonomy limit", list(classification.reasons))
        if workflow.autonomy_level < MIN_GATED_AUTONOMY_LEVEL:
            raise Escalate(Failure.HUMAN_REQUIRED.value, f"workflow {workflow.id} is below gated autonomy")
        perms = PermissionPolicy(spec.permissions, workflow.allowed_permissions, rec)
        rec.move(State.APPROVED, "policy checks passed")

        rec.move(State.PLANNING, "building plan")
        perms.check("planner", "READ_REPOSITORY")
        plan = make_plan(spec, classification, workflow, source)
        rec.write_json("plan.json", plan)
        rec.record("plan", relevant_files=plan["relevant_files"], steps=len(plan["steps"]), plan_sha256=_sha_json(plan))

        rec.move(State.ENVIRONMENT_READY, "snapshotting repository")
        baseline_dir = rec.run_dir / "baseline"
        workspace = rec.run_dir / "workspace"
        base_manifest = snapshot(source, baseline_dir)
        reset_workspace(baseline_dir, workspace)
        rec.write_json("baseline_manifest.json", base_manifest)
        rec.write_json("run.json", {"task_id": spec.task_id, "source": str(source), "workflow": workflow.id})
        rec.record(
            "environment",
            factory_version=__version__,
            python=sys.version.split()[0],
            executable=sys.executable,
            platform=platform.platform(),
            source=str(source),
            files=len(base_manifest),
            manifest_sha256=_sha_json(base_manifest),
            env_keys=list(SAFE_ENV_KEYS),
        )

        agent = build_agent(spec, self.root, workspace, self.transport)
        rec.record("agent_config", **agent.describe())
        self._execute_and_verify(rec, spec, workflow, perms, agent, baseline_dir, workspace, base_manifest, started)

        changes = changed_files(base_manifest, workspace)
        patch = unified_diff(baseline_dir, workspace, changes)
        rec.write_text("changes.patch", patch)
        rec.write_json("changes.json", changes)
        rec.record("changes", files=changes, patch_sha256=sha256_bytes(patch.encode("utf-8")))
        rec.move(State.REVIEW_GATE, "all checks passed; awaiting review")
        rec.write_text("review.md", _render_review(spec, classification, rec, changes))

        gated = spec.human_approval_required or workflow.human_approval_required
        if not gated:
            rec.record("human_decision", decision="auto-approve", by="policy", reason="neither task nor workflow requires a human gate")
            deliver_run(rec)

    def _execute_and_verify(self, rec, spec: TaskSpec, workflow: Workflow, perms, agent, baseline_dir, workspace, base_manifest, started) -> None:
        deadline = started + spec.max_execution_seconds
        retry_budget = min(spec.max_retries, workflow.max_retries)
        feedback: str | None = None
        attempt = 0

        while True:
            attempt += 1
            self._check_deadline(deadline, attempt)
            if attempt == 1:
                rec.move(State.EXECUTING, "first attempt")
            else:
                rec.move(State.REPAIRING, f"repair attempt {attempt}")
                reset_workspace(baseline_dir, workspace)
                rec.record("workspace_reset", attempt=attempt, reason="restore baseline before repair")

            perms.check("coding_agent", "WRITE_REPOSITORY")
            proposal = agent.propose(attempt - 1, feedback)
            rec.write_json(
                f"attempts/{attempt:02d}-proposal.json",
                {"intent": proposal.intent, "edits": proposal.edits, "raw_response": proposal.raw},
            )
            rec.record(
                "agent_proposal",
                attempt=attempt,
                agent=agent.name,
                intent=proposal.intent,
                edit_paths=[e.get("path") for e in proposal.edits if isinstance(e, dict)],
                received_feedback=feedback is not None,
            )

            written = apply_edits(workspace, proposal.edits, spec.allowed_files)
            rec.record("files_written", attempt=attempt, paths=written)

            findings = scan_files(workspace, written)
            if findings:
                raise Escalate(Failure.SECURITY_FAILURE.value, "secret-like content in proposed edits", findings)

            self._check_deadline(deadline, attempt)
            rec.move(State.VERIFYING, "running required checks")
            perms.check("verifier", "EXECUTE_TESTS")
            remaining = deadline - time.monotonic()
            results = run_checks(spec.required_tests, workspace, workflow.allowed_command_prefixes, remaining)
            rec.record("verification", attempt=attempt, passed=all(r.passed for r in results), results=[r.to_dict() for r in results])

            category = diagnose(results)
            if category is None:
                return

            rec.move(State.DIAGNOSING, category.value)
            response = RESPONSES[category]
            can_repair = (
                response is Action.REPAIR
                and category.value in workflow.repairable_failures
                and attempt - 1 < retry_budget
            )
            rec.record("failure", attempt=attempt, category=category.value, response=response.value, repair_allowed=can_repair)
            if can_repair:
                feedback = feedback_from(results)
                rec.record("retry", next_attempt=attempt + 1, retries_left=retry_budget - attempt)
                continue
            raise Escalate(category.value, f"verification failed on attempt {attempt} with {category.value}", [r.to_dict() for r in results])

    @staticmethod
    def _check_deadline(deadline: float, attempt: int) -> None:
        if time.monotonic() > deadline:
            raise Escalate(Failure.TIMEOUT.value, f"execution time budget exhausted before attempt {attempt}")


def _render_review(spec: TaskSpec, classification, rec: RunRecord, changes: dict) -> str:
    lines = [
        f"# Review packet: {spec.task_id}",
        "",
        f"**Objective:** {spec.objective}",
        "",
        f"**Workflow:** {classification.workflow_id}  ",
        f"**Risk:** {classification.risk_level} (declared {classification.declared_risk})",
        "",
        "## Acceptance criteria",
        *[f"- {c}" for c in spec.acceptance_criteria],
        "",
        "## Changed files",
        *([f"- `{p}`: {s}" for p, s in changes.items()] or ["- none"]),
        "",
        "## Evidence",
        f"- Diff: `changes.patch`",
        f"- Ledger: `ledger.jsonl` ({len(rec.ledger.read())} entries)",
        "",
        "## Decision",
        f"- Approve: `python -m darkfactory approve {spec.task_id}`",
        f"- Reject: `python -m darkfactory reject {spec.task_id} --reason \"...\"`",
        "",
    ]
    return "\n".join(lines)
