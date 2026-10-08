# AI Dark Factory (MVP)

A bounded, verified, recorded pipeline for automated code changes. It takes a task spec, has an agent propose edits inside an isolated working copy, verifies them with allowlisted commands, repairs failures within a fixed budget, and stops at a human review gate before anything touches the real repository.

Design and status of every spec item: [docs/DESIGN.md](docs/DESIGN.md).

## Requirements
Python 3.12 or later. No third-party packages.

## Quick start

```bash
python -m unittest discover -s tests -v
python -m darkfactory run examples/tasks/fix_average.json
python -m darkfactory show fix-average-001
python -m darkfactory approve fix-average-001 --by yourname
python -m darkfactory rollback fix-average-001 --reason "checking the undo path"
```

The example run stops at `REVIEW_GATE`. Read `runs/fix-average-001/review.md` and `changes.patch` before approving. Approval changes `examples/sample_project/calc.py`, and `rollback` reverts it.

## What happens in a run
1. **Validate.** Unknown or missing fields are rejected. Unsafe paths and commands are blocked.
2. **Classify.** A workflow is chosen from `workflows/registry.json`. Risk is raised for CI, infrastructure, or sensitive paths. If the risk is above the workflow's limit, the run escalates to a human.
3. **Plan.** `plan.json` is written before any file changes.
4. **Snapshot.** The repository is copied to `baseline/` and `workspace/`.
5. **Execute.** The agent proposes whole-file edits. They are accepted only for `allowed_files`, and they are applied all-or-nothing.
6. **Scan and verify.** Edits are scanned for secret-like content. Required commands run with a scrubbed environment and a time budget.
7. **Repair.** A failing test sends the failure output back to the agent, after the workspace is reset to baseline. This repeats until the retry budget (`max_retries`, capped by the workflow) runs out. Any other failure category escalates immediately.
8. **Review gate.** The diff and evidence are written. Nothing is delivered without approval unless the task and workflow both allow auto-delivery.
9. **Deliver or roll back.** Delivery backs up originals and refuses to overwrite files that changed since the snapshot. Rollback restores the backups and refuses if the source has changed since delivery.

Every step is appended to `runs/<task>/ledger.jsonl`, a SHA-256 hash chain. `python -m darkfactory verify <task>` checks it.

## Exit codes
- `run`: 0 at `REVIEW_GATE` or `COMPLETED`, otherwise 1 (blocked, escalated, or failed). A usage or I/O error returns 2.
- `approve`: 0 if the run completes. A drift escalation returns 1.
- `reject`, `rollback`, `show`, `verify`: 0 on success.

## Layout

```
darkfactory/        pipeline package
workflows/          registry.json (data-driven workflow definitions)
examples/           sample project with a real bug, the task spec, and scripted agent proposals
tests/              unittest suite
docs/DESIGN.md      architecture, decisions, and status of each spec item
runs/               generated run records (git-ignored)
```

## Adding a task
Write a JSON spec with every field from `darkfactory/spec.py` (`TaskSpec`). `allowed_files` should be as narrow as the change allows. `required_tests` must start with a prefix from the workflow's `allowed_command_prefixes`. For now, `agent_script` points to a JSON file of proposals. A model-backed agent is not implemented yet.

## Limitations of this MVP
- **Isolation is weaker than the spec calls for.** Work happens in a temp copy with an environment allowlist, not in a container. There are no network or process limits.
- **The agent is scripted.** The repair loop is real, but the proposals come from a file, so this demonstrates control flow and not model capability.
- **The ledger is tamper-evident, not tamper-proof.**
- **Only one workflow exists.** The dashboard, metrics, multi-agent orchestration, and multi-model routing are not built.
- **Windows-tested.** The code uses only portable Python, but I have only run it on Windows 11.

## Next steps
1. Add a model-backed agent behind the existing `propose(attempt, feedback)` interface, and log its model and parameters in `environment` and `agent_proposal`.
2. Run the sandbox in a container with no network and a read-only source mount.
3. Add a metrics command that reads ledgers: first-pass rate, retry rate, rollback rate, and cost per task. Use it to decide when the workflow can be promoted to narrow lights-out.
4. Add a second workflow, such as dependency bumps or lint fixes, to test the registry design.
