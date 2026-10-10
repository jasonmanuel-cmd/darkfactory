# Project log

## Current state
- Branch `main`, tracking `origin/main` (https://github.com/jasonmanuel-cmd/darkfactory.git).
- Latest commit on origin before this log: `d324b93`.
- Working tree was clean before this log was added.

## Changes so far
| Commit | Change | Files |
|---|---|---|
| `05e882e` | MVP: spec validation, classification, permissions, hash-chained ledger, state machine, repair loop, review gate, approve/reject/rollback, scripted agent | `darkfactory/*`, `workflows/registry.json`, `examples/sample_project/*`, `examples/tasks/fix_average.json`, `examples/agents/fix_average.json`, `tests/test_factory.py`, `docs/DESIGN.md`, `README.md`, `.gitignore` |
| `d324b93` | Model-backed agent (`anthropic:<model>`), `agent` field replaces `agent_script`, agent config recorded in ledger, timeout test, live-run task file | `darkfactory/agents.py`, `darkfactory/runner.py`, `darkfactory/spec.py`, `tests/test_factory.py`, `examples/tasks/fix_average.json`, `examples/tasks/fix_average_model.json`, `README.md` |
| (this commit) | Project log added | `docs/PROJECT_LOG.md` |

## Checks performed
- `python -m unittest discover -s tests`: **31 tests, all pass** (re-run before this log was written; 39s, the timeout test accounts for ~10s).
- Scripted example via CLI into a scratch runs dir: reaches `REVIEW_GATE`, ledger verifies.
- Approve then rollback of the scripted example in the real sample project: state went `REVIEW_GATE → COMPLETED → ROLLED_BACK`; `calc.py` was restored to the original (`git status` is clean, so the committed file matches).
- Secret scan of all tracked files (`AKIA…`, private-key blocks, `sk-ant-…`, `ANTHROPIC_API_KEY=`): **no matches**.
- Diff review before this commit: the only change is the new log file. No credentials are in the repo or the ledgers; the ledger records the agent's config (model, settings) but never the key.

## Not verified
- **Live Anthropic call has not been run.** `ANTHROPIC_API_KEY` is not set in this shell, the Windows user environment, or the current process. The model path is covered only by tests with a fake transport.
- The timeout path is tested only with a sleeping test file in a temp copy; it is not tested against a real slow project.
- Only Windows 11 has been tried.

## Remaining problems
1. No live model run yet (blocked on the API key; see below).
2. Ledger is tamper-evident, not tamper-proof; no external head-hash anchor.
3. No container or network isolation. Isolation is a temp copy plus an environment allowlist.
4. Only one workflow exists (bug fix in a Python project).
5. The model adapter sends `allowed_files` contents and task text to Anthropic. This is documented in the README but there is no consent gate or redaction beyond the secret scan on the output.

## Next concrete action
Set `ANTHROPIC_API_KEY` in the Windows user environment, restart the Claude app, then run:

```bash
python -m darkfactory run examples/tasks/fix_average_model.json
```

If it reaches `REVIEW_GATE`, record the model, attempt count, and final state here, then decide whether to approve.

## Decisions and corrections
- **Decision:** stdlib-only Python 3.12, no dependencies (reproducibility and no supply-chain surface).
- **Decision:** the ledger is append-only hash-chained JSON Lines; state is derived from the last transition.
- **Decision:** source repo is untouched until human approval; delivery refuses to overwrite drifted files.
- **Decision:** the Anthropic adapter uses `urllib` and an injectable transport, so tests run offline.
- **Correction:** the first timeout test run used an invalid unittest module id (`tests.test_factory`); `tests/` is not a package. Fixed by running through `discover`. No code change needed.
- **Correction:** the MVP's first README and DESIGN described the agent as scripted-only; both now describe the `agent` field and the Anthropic option.
