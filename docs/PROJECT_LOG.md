# Project log

## Current state
- Branch `main`, tracking `origin/main` (https://github.com/jasonmanuel-cmd/darkfactory.git).
- Latest commit on origin before this log: `70bf21c`.
- Working tree clean and verified.

## Changes so far
| Commit | Change | Files |
|---|---|---|
| `05e882e` | MVP: spec validation, classification, permissions, hash-chained ledger, state machine, repair loop, review gate, approve/reject/rollback, scripted agent | `darkfactory/*`, `workflows/registry.json`, `examples/sample_project/*`, `examples/tasks/fix_average.json`, `examples/agents/fix_average.json`, `tests/test_factory.py`, `docs/DESIGN.md`, `README.md`, `.gitignore` |
| `d324b93` | Model-backed agent (`anthropic:<model>`), `agent` field replaces `agent_script`, agent config recorded in ledger, timeout test, live-run task file | `darkfactory/agents.py`, `darkfactory/runner.py`, `darkfactory/spec.py`, `tests/test_factory.py`, `examples/tasks/fix_average.json`, `examples/tasks/fix_average_model.json`, `README.md` |
| `70bf21c` | Add project log and bring design doc up to date | `docs/PROJECT_LOG.md`, `docs/DESIGN.md` |
| (this commit) | Add C#/.NET workflow (`wf.bug-fix.dotnet.v1`), Windows environment expansion, build artifact ignores, WinCare task example, and unit tests | `darkfactory/classify.py`, `darkfactory/verify.py`, `darkfactory/workspace.py`, `workflows/registry.json`, `tests/test_factory.py`, `examples/agents/wincare_max_history.json`, `examples/tasks/wincare_max_history.json`, `docs/PROJECT_LOG.md`, `docs/DESIGN.md` |

## Checks performed
- `python -m unittest discover -s tests -v`: **32 tests, all pass** (including new `test_dotnet_workflow_classification_and_command_parsing`).
- Secret scan on all changed files: **0 findings** (verified by `darkfactory.security.scan_files` and regex audit).
- Command parsing and classification check for `dotnet test` and `dotnet build`: verified that `argv[0]` is preserved for non-python binaries and workflow is matched via test command prefixes.
- Verification environment check: Windows path/environment keys (`LOCALAPPDATA`, `PROGRAMFILES`, etc.) allow .NET/MSBuild toolchains to run in scrubbed environment.
- Review diff for accidental changes: clean diff confined strictly to .NET workflow support, ignore rules, and tests.

## Not verified
- **Live Anthropic call has not been run.** `ANTHROPIC_API_KEY` is not set in this shell, the Windows user environment, or the current process. The model path is covered only by tests with a fake transport.
- The timeout path is tested only with a sleeping test file in a temp copy; it is not tested against a real slow project.
- Only Windows 11 has been tried.

## Remaining problems
1. No live model run yet (blocked on the API key; see below).
2. Ledger is tamper-evident, not tamper-proof; no external head-hash anchor.
3. No container or network isolation. Isolation is a temp copy plus an environment allowlist.
4. The model adapter sends `allowed_files` contents and task text to Anthropic. This is documented in the README but there is no consent gate or redaction beyond the secret scan on the output.

## Next concrete action
Run the WinCare .NET scripted repair task through the pipeline:

```bash
python -m darkfactory run examples/tasks/wincare_max_history.json
```

Verify that the first attempt fails on compile, the second attempt fixes the syntax, the run reaches `REVIEW_GATE`, and the ledger verifies cleanly.

## Decisions and corrections
- **Decision:** stdlib-only Python 3.12, no dependencies (reproducibility and no supply-chain surface).
- **Decision:** the ledger is append-only hash-chained JSON Lines; state is derived from the last transition.
- **Decision:** source repo is untouched until human approval; delivery refuses to overwrite drifted files.
- **Decision:** the Anthropic adapter uses `urllib` and an injectable transport, so tests run offline.
- **Decision:** Expanded workflow classification in `darkfactory/classify.py` to inspect `required_tests` prefixes against workflow `allowed_command_prefixes` so tasks sharing `task_type: bug_fix` disambiguate automatically between Python and .NET.
- **Decision:** Preserved original binary path in `parse_command` for non-python tools (e.g. `dotnet`), restricting `sys.executable` substitution strictly to `python` and `python3`.
- **Decision:** Added common Windows user/build variables (`USERPROFILE`, `LOCALAPPDATA`, `PROGRAMFILES`, etc.) to `SAFE_ENV_KEYS` to enable .NET CLI and MSBuild toolchains to resolve SDKs without exposing arbitrary secrets.
- **Decision:** Added `.NET` build outputs (`bin`, `obj`, `.vs`, `TestResults`, `publish`, `installer`, `msix-stage`) to `workspace.py`'s `IGNORED_NAMES` to keep snapshotting fast and tamper-check clean.
