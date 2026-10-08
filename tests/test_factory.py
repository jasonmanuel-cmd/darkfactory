import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from darkfactory.errors import SpecError
from darkfactory.gate import approve, reject, rollback
from darkfactory.ledger import Ledger
from darkfactory.errors import FactoryError
from darkfactory.record import RunRecord
from darkfactory.registry import load_registry
from darkfactory.runner import Runner
from darkfactory.spec import parse_spec
from darkfactory.states import InvalidTransition, State, check_transition

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "sample_project"
ORIGINAL_CALC = (SAMPLE / "calc.py").read_text(encoding="utf-8")
ORIGINAL_TEST = (SAMPLE / "tests" / "test_calc.py").read_text(encoding="utf-8")
GOOD_CALC = "def average(values):\n    if not values:\n        return 0.0\n    return sum(values) / len(values)\n"
BAD_CALC = "def average(values):\n    return sum(values) / len(values)\n"
REGISTRY = load_registry(ROOT / "workflows" / "registry.json")


def attempt(content: str, path: str = "calc.py") -> dict:
    return {"intent": "test attempt", "edits": [{"path": path, "content": content}]}


def run_suite(repo: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo,
        capture_output=True,
        text=True,
    )


class FactoryCase(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="darkfactory-test-"))
        self.repo = self.tmp / "repo"
        shutil.copytree(SAMPLE, self.repo)
        self.runs = self.tmp / "runs"
        self.runner = Runner(ROOT, self.runs, REGISTRY)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def base_spec(self) -> dict:
        spec = json.loads((ROOT / "examples" / "tasks" / "fix_average.json").read_text(encoding="utf-8"))
        spec["repository"] = str(self.repo)
        return spec

    def spec_with_script(self, attempts: list[dict], **overrides) -> dict:
        script = self.tmp / "agent.json"
        script.write_text(json.dumps({"attempts": attempts}), encoding="utf-8")
        spec = self.base_spec()
        spec["agent_script"] = str(script)
        spec.update(overrides)
        return spec

    def events(self, rec: RunRecord, kind: str) -> list[dict]:
        return [e for e in rec.ledger.read() if e["kind"] == kind]

    def escalation(self, rec: RunRecord) -> dict:
        return json.loads((rec.run_dir / "escalation.json").read_text(encoding="utf-8"))


class PipelineTests(FactoryCase):
    def test_repair_loop_then_gate_then_approve_then_rollback(self):
        rec = self.runner.run(self.base_spec())

        self.assertEqual(rec.state, State.REVIEW_GATE)
        self.assertEqual(rec.ledger.verify(), [])
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), ORIGINAL_CALC, "source must be untouched before approval")

        failures = self.events(rec, "failure")
        self.assertEqual([(f["data"]["category"], f["data"]["response"]) for f in failures], [("TEST_FAILURE", "REPAIR")])
        self.assertEqual(len(self.events(rec, "verification")), 2)
        transitions = [e["data"]["to"] for e in rec.ledger.read() if e["kind"] == "transition"]
        self.assertIn("DIAGNOSING", transitions)
        self.assertIn("REPAIRING", transitions)

        rec = approve(self.runs, "fix-average-001", "tester")
        self.assertEqual(rec.state, State.COMPLETED)
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), GOOD_CALC)
        self.assertEqual(run_suite(self.repo).returncode, 0)

        rec = rollback(self.runs, "fix-average-001", "tester", "testing rollback")
        self.assertEqual(rec.state, State.ROLLED_BACK)
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), ORIGINAL_CALC)
        self.assertEqual(rec.ledger.verify(), [])

    def test_policy_violation_escalates_and_leaves_source_alone(self):
        spec = self.spec_with_script([attempt(GOOD_CALC, path="tests/test_calc.py")])
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "POLICY_FAILURE")
        self.assertEqual((self.repo / "tests" / "test_calc.py").read_text(encoding="utf-8"), ORIGINAL_TEST)

    def test_exhausted_retries_escalate(self):
        spec = self.spec_with_script([attempt(BAD_CALC), attempt(BAD_CALC)], max_retries=1)
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "TEST_FAILURE")
        self.assertEqual(len(self.events(rec, "verification")), 2)
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), ORIGINAL_CALC)

    def test_agent_script_exhaustion_is_agent_failure(self):
        spec = self.spec_with_script([attempt(BAD_CALC)], max_retries=2)
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "AGENT_FAILURE")

    def test_secret_like_content_escalates_without_echoing_value(self):
        key = "AKIA" + "IOSFODNN7EXAMPLE"
        spec = self.spec_with_script([attempt(f"KEY = '{key}'\n")])
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        report = self.escalation(rec)
        self.assertEqual(report["category"], "SECURITY_FAILURE")
        self.assertNotIn(key, json.dumps(report))
        self.assertNotIn(key, (rec.run_dir / "ledger.jsonl").read_text(encoding="utf-8"))

    def test_sensitive_path_exceeding_workflow_risk_escalates_to_human(self):
        spec = self.base_spec()
        spec["allowed_files"] = ["deploy/release.py"]
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "HUMAN_REQUIRED")

    def test_permission_outside_workflow_escalates(self):
        spec = self.base_spec()
        spec["permissions"] = spec["permissions"] + ["DEPLOY"]
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "POLICY_FAILURE")

    def test_unsupported_task_type_is_blocked(self):
        spec = self.base_spec()
        spec["task_type"] = "dependency_upgrade"
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.BLOCKED)
        self.assertEqual(len(self.events(rec, "blocked")), 1)

    def test_invalid_spec_is_blocked(self):
        spec = self.base_spec()
        spec["surprise_field"] = 1
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.BLOCKED)
        self.assertIn("unknown fields", self.events(rec, "blocked")[0]["data"]["reason"])

    def test_missing_field_is_blocked(self):
        spec = self.base_spec()
        del spec["objective"]
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.BLOCKED)

    def test_command_outside_allowlist_is_blocked(self):
        spec = self.base_spec()
        spec["required_tests"] = ["python -c print(1)"]
        rec = self.runner.run(spec)
        self.assertEqual(rec.state, State.BLOCKED)

    def test_reject_cancels_without_touching_source(self):
        self.runner.run(self.base_spec())
        rec = reject(self.runs, "fix-average-001", "tester", "not wanted")
        self.assertEqual(rec.state, State.CANCELLED)
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), ORIGINAL_CALC)

    def test_drift_since_snapshot_blocks_delivery(self):
        self.runner.run(self.base_spec())
        (self.repo / "calc.py").write_text("def average(values):\n    return 42\n", encoding="utf-8")
        rec = approve(self.runs, "fix-average-001", "tester")
        self.assertEqual(rec.state, State.ESCALATED)
        self.assertEqual(self.escalation(rec)["category"], "HUMAN_REQUIRED")
        self.assertEqual((self.repo / "calc.py").read_text(encoding="utf-8"), "def average(values):\n    return 42\n")

    def test_rollback_refused_after_later_source_edit(self):
        self.runner.run(self.base_spec())
        approve(self.runs, "fix-average-001", "tester")
        (self.repo / "calc.py").write_text("def average(values):\n    return 7\n", encoding="utf-8")
        with self.assertRaises(FactoryError):
            rollback(self.runs, "fix-average-001", "tester", "try")
        rec = RunRecord.open(self.runs / "fix-average-001")
        self.assertEqual(rec.state, State.COMPLETED)
        self.assertEqual(len(self.events(rec, "rollback_refused")), 1)

    def test_approve_requires_review_gate(self):
        self.runner.run(self.spec_with_script([attempt(BAD_CALC)], max_retries=0))
        with self.assertRaises(FactoryError):
            approve(self.runs, "fix-average-001", "tester")


class SpecTests(unittest.TestCase):
    def valid(self) -> dict:
        return json.loads((ROOT / "examples" / "tasks" / "fix_average.json").read_text(encoding="utf-8"))

    def test_valid_example_parses(self):
        self.assertEqual(parse_spec(self.valid()).task_id, "fix-average-001")

    def test_path_traversal_rejected(self):
        spec = self.valid()
        spec["allowed_files"] = ["../outside.py"]
        with self.assertRaises(SpecError):
            parse_spec(spec)

    def test_absolute_path_rejected(self):
        spec = self.valid()
        spec["allowed_files"] = ["C:/Windows/system.ini"]
        with self.assertRaises(SpecError):
            parse_spec(spec)

    def test_retry_budget_capped(self):
        spec = self.valid()
        spec["max_retries"] = 50
        with self.assertRaises(SpecError):
            parse_spec(spec)


class StateMachineTests(unittest.TestCase):
    def test_cannot_skip_review_gate(self):
        with self.assertRaises(InvalidTransition):
            check_transition(State.VERIFYING, State.DELIVERING)

    def test_terminal_states_have_no_exits(self):
        with self.assertRaises(InvalidTransition):
            check_transition(State.COMPLETED, State.EXECUTING)

    def test_run_must_start_in_received(self):
        with self.assertRaises(InvalidTransition):
            check_transition(None, State.EXECUTING)


class LedgerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="darkfactory-ledger-"))
        self.path = self.tmp / "ledger.jsonl"
        ledger = Ledger(self.path)
        for i in range(3):
            ledger.append("event", index=i, note=f"entry-{i}")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_intact_chain_verifies(self):
        self.assertEqual(Ledger(self.path).verify(), [])

    def test_edited_entry_is_detected(self):
        text = self.path.read_text(encoding="utf-8")
        self.path.write_text(text.replace("entry-1", "edited"), encoding="utf-8")
        self.assertTrue(Ledger(self.path).verify())

    def test_deleted_entry_is_detected(self):
        lines = self.path.read_text(encoding="utf-8").splitlines(keepends=True)
        self.path.write_text("".join(lines[:1] + lines[2:]), encoding="utf-8")
        self.assertTrue(Ledger(self.path).verify())


if __name__ == "__main__":
    unittest.main()
