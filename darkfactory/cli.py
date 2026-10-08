"""Command-line interface: run, approve, reject, rollback, show, verify."""

import argparse
import getpass
import json
import sys
from pathlib import Path

from .errors import FactoryError
from .gate import approve, reject, rollback
from .record import RunRecord
from .registry import load_registry
from .runner import Runner
from .states import State

REPO_ROOT = Path(__file__).resolve().parents[1]
SUCCESS_STATES = {State.REVIEW_GATE.value, State.COMPLETED.value}


def _print_record(rec: RunRecord) -> None:
    print(f"task:   {rec.run_dir.name}")
    print(f"state:  {rec.state.value if rec.state else 'NONE'}")
    print(f"run:    {rec.run_dir}")
    for name in ("review.md", "escalation.json"):
        if (rec.run_dir / name).exists() and (name != "review.md" or rec.state is State.REVIEW_GATE):
            print(f"{name}: {rec.run_dir / name}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="darkfactory", description="Bounded autonomous software-change factory.")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="repository root (resolves relative task paths)")
    parser.add_argument("--runs-dir", type=Path, default=None, help="where run ledgers live (default: <root>/runs)")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("run", help="execute a task spec through the pipeline")
    p.add_argument("spec", type=Path)

    for name, help_text in (("approve", "approve a REVIEW_GATE run and deliver it"), ("reject", "reject a REVIEW_GATE run"), ("rollback", "undo a delivered run")):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("task_id")
        p.add_argument("--by", default=getpass.getuser(), help="approver identity recorded in the ledger")
        if name != "approve":
            p.add_argument("--reason", required=True)

    p = sub.add_parser("show", help="print the state and full ledger timeline of a run")
    p.add_argument("task_id")

    p = sub.add_parser("verify", help="check a run ledger's hash chain")
    p.add_argument("task_id")

    args = parser.parse_args(argv)
    root = args.root.resolve()
    runs_dir = (args.runs_dir or root / "runs").resolve()

    try:
        if args.command == "run":
            workflows = load_registry(root / "workflows" / "registry.json")
            try:
                raw = json.loads(args.spec.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                print(f"cannot read task spec: {exc}", file=sys.stderr)
                return 2
            rec = Runner(root, runs_dir, workflows).run(raw)
            _print_record(rec)
            return 0 if rec.state and rec.state.value in SUCCESS_STATES else 1

        if args.command == "approve":
            rec = approve(runs_dir, args.task_id, args.by)
            _print_record(rec)
            return 0 if rec.state is State.COMPLETED else 1

        if args.command == "reject":
            rec = reject(runs_dir, args.task_id, args.by, args.reason)
            _print_record(rec)
            return 0

        if args.command == "rollback":
            rec = rollback(runs_dir, args.task_id, args.by, args.reason)
            _print_record(rec)
            return 0

        if args.command == "show":
            rec = RunRecord.open(runs_dir / args.task_id)
            _print_record(rec)
            print("\ntimeline:")
            for entry in rec.ledger.read():
                detail = ""
                if entry["kind"] == "transition":
                    detail = f"{entry['data']['frm']} -> {entry['data']['to']}  ({entry['data']['reason']})"
                elif entry["kind"] == "failure":
                    detail = f"attempt {entry['data']['attempt']}: {entry['data']['category']} -> {entry['data']['response']}"
                elif entry["kind"] == "verification":
                    detail = f"attempt {entry['data']['attempt']}: passed={entry['data']['passed']}"
                print(f"  {entry['seq']:>3}  {entry['ts']}  {entry['kind']:<20} {detail}")
            return 0

        if args.command == "verify":
            problems = RunRecord.open(runs_dir / args.task_id).ledger.verify()
            print("ledger OK" if not problems else "ledger FAILED:\n  " + "\n  ".join(problems))
            return 0 if not problems else 1
    except FactoryError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    return 2
