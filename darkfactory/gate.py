"""Human decision points: approve (deliver), reject (cancel) and rollback (undo delivery)."""

from pathlib import Path

from .delivery import deliver, restore
from .errors import Escalate, FactoryError
from .record import RunRecord
from .states import State


def _open(runs_dir: Path, task_id: str) -> RunRecord:
    return RunRecord.open(Path(runs_dir) / task_id)


def deliver_run(rec: RunRecord) -> RunRecord:
    """Move REVIEW_GATE -> DELIVERING -> COMPLETED, or ESCALATED if the source drifted."""
    info = rec.read_json("run.json")
    changes = rec.read_json("changes.json")
    baseline = rec.read_json("baseline_manifest.json")
    rec.move(State.DELIVERING, "delivering reviewed changes")
    try:
        applied = deliver(Path(info["source"]), rec.run_dir / "workspace", baseline, changes, rec.run_dir / "backup")
    except Escalate as exc:
        rec.escalate(exc.category, exc.summary, evidence=exc.evidence)
        return rec
    except Exception as exc:
        rec.record("error", error=repr(exc))
        rec.move(State.FAILED, "delivery crashed")
        raise
    rec.record("delivery", applied=applied, source=info["source"])
    rec.move(State.COMPLETED, "delivered")
    return rec


def approve(runs_dir: Path, task_id: str, approver: str) -> RunRecord:
    rec = _open(runs_dir, task_id)
    if rec.state is not State.REVIEW_GATE:
        raise FactoryError(f"{task_id} is in {rec.state.value}; only REVIEW_GATE can be approved")
    rec.record("human_decision", decision="approve", by=approver)
    return deliver_run(rec)


def reject(runs_dir: Path, task_id: str, approver: str, reason: str) -> RunRecord:
    rec = _open(runs_dir, task_id)
    if rec.state is not State.REVIEW_GATE:
        raise FactoryError(f"{task_id} is in {rec.state.value}; only REVIEW_GATE can be rejected")
    rec.record("human_decision", decision="reject", by=approver, reason=reason)
    rec.move(State.CANCELLED, reason)
    return rec


def rollback(runs_dir: Path, task_id: str, approver: str, reason: str) -> RunRecord:
    rec = _open(runs_dir, task_id)
    if rec.state is not State.COMPLETED:
        raise FactoryError(f"{task_id} is in {rec.state.value}; only COMPLETED runs can be rolled back")
    info = rec.read_json("run.json")
    changes = rec.read_json("changes.json")
    rec.record("human_decision", decision="rollback", by=approver, reason=reason)
    try:
        restore(Path(info["source"]), rec.run_dir / "workspace", changes, rec.run_dir / "backup")
    except Escalate as exc:
        # The run stays COMPLETED: the source is not what was delivered, so nothing was reverted.
        rec.record("rollback_refused", category=exc.category, summary=exc.summary, evidence=exc.evidence)
        raise FactoryError(f"rollback refused: {exc.summary} ({exc.evidence})") from exc
    rec.move(State.ROLLED_BACK, reason)
    return rec
