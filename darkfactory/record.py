"""Per-run handle: ledger access, state transitions, artifacts and escalation reports."""

import json
from pathlib import Path

from .errors import FactoryError, LedgerIntegrityError
from .failures import NEXT_ACTIONS
from .ledger import Ledger
from .states import TRANSITIONS, State, check_transition


class RunRecord:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.ledger = Ledger(self.run_dir / "ledger.jsonl")
        self.state: State | None = None

    @classmethod
    def create(cls, run_dir: Path) -> "RunRecord":
        run_dir = Path(run_dir)
        if run_dir.exists():
            raise FactoryError(f"run directory already exists: {run_dir} (task ids must be unique)")
        run_dir.mkdir(parents=True)
        return cls(run_dir)

    @classmethod
    def open(cls, run_dir: Path) -> "RunRecord":
        rec = cls(run_dir)
        if not rec.ledger.exists():
            raise FactoryError(f"no ledger found at {rec.ledger.path}")
        problems = rec.ledger.verify()
        if problems:
            raise LedgerIntegrityError(f"{rec.ledger.path} failed verification: {problems}")
        current = rec.ledger.current_state()
        rec.state = State(current) if current else None
        return rec

    def record(self, kind: str, **data: object) -> dict:
        return self.ledger.append(kind, **data)

    def can_move(self, to: State) -> bool:
        return self.state is not None and to in TRANSITIONS[self.state]

    def move(self, to: State, reason: str = "") -> None:
        check_transition(self.state, to)
        self.ledger.append("transition", frm=self.state.value if self.state else None, to=to.value, reason=reason)
        self.state = to

    def write_json(self, name: str, payload: object) -> Path:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2, ensure_ascii=False, default=str) + "\n", encoding="utf-8")
        return path

    def write_text(self, name: str, text: str) -> Path:
        path = self.run_dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def read_json(self, name: str) -> object:
        return json.loads((self.run_dir / name).read_text(encoding="utf-8"))

    def escalate(self, category: str, summary: str, evidence: object = None) -> dict:
        report = {
            "task_id": self.run_dir.name,
            "category": category,
            "what_happened": summary,
            "state_when_escalated": self.state.value if self.state else None,
            "evidence": evidence,
            "proposed_next_actions": NEXT_ACTIONS.get(category, ["Inspect ledger.jsonl and decide how to proceed"]),
            "required_decision": "Re-scope and resubmit, correct the inputs and re-run, or cancel the task.",
        }
        self.write_json("escalation.json", report)
        self.record("escalation", category=category, summary=summary)
        self.move(State.ESCALATED, reason=category)
        return report
