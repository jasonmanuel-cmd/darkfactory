# AI Dark Factory: design (MVP)

Status key: **built** = implemented and tested in this repo; **designed** = specified here, not implemented.

## 1. System architecture — built
A single Python package (`darkfactory/`) with a linear, state-driven pipeline. Each stage either advances, blocks the task (invalid input), or escalates to a human with an evidence report. The agent is a replaceable interface (`agents.py`): a scripted agent for offline runs and an Anthropic model agent for live runs.

## 2. Component diagram — built

```
 task.json ─► spec.py (validate) ─► classify.py (workflow + risk) ─► permissions.py (grants)
                                              │
                    registry.json ◄───────────┘
                                              ▼
 planner.py ─► record.py / ledger.py (hash-chained log, state machine in states.py)
                                              ▼
 workspace.py (baseline + working copy) ◄── agents.py (propose edits)
          │                                   ▲
          ▼                                   │ feedback (verification evidence)
 security.py (secret scan) ─► verify.py (allowlisted commands) ─► failures.py (diagnose)
                                              ▼
                              REVIEW_GATE ─► gate.py (approve / reject / rollback)
                                              ▼
                                   delivery.py (drift check, backup, apply)
```

## 3. Data-flow diagram — built
Inputs: the task spec and the workflow registry. Writes: `runs/<task>/` containing `ledger.jsonl`, `plan.json`, `baseline/`, `workspace/`, `attempts/*.json`, `changes.patch`, `review.md`, and, when relevant, `escalation.json` and `backup/`. The source repository is read at snapshot time and written only by an approved delivery.

## 4. State machine — built
Defined in `states.py`. Every transition is validated and ledgered. Key properties:
- Delivery is reachable only through `REVIEW_GATE`.
- `COMPLETED` can only be left via `ROLLED_BACK`.
- Terminal states (`BLOCKED`, `CANCELLED`, `ROLLED_BACK`, `FAILED`) have no exits.
- `PAUSED` is in the enum with transitions defined, but no code path reaches it yet.

## 5. Security model — built (partial)
- Least privilege: task capabilities ∩ workflow allowance ∩ per-actor grant (`permissions.py`).
- Path allowlist: agent edits are confined to `allowed_files`; the spec validator rejects absolute paths, drive letters, and `..`.
- Command allowlist: only allowlisted workflow prefixes run (`python -m unittest`, `dotnet test`, `dotnet build`, `py_compile`), with `shell=False` and `python` mapped to the factory's own interpreter.
- Secret isolation: verification runs with an environment allowlist (`PATH`, `SYSTEMROOT`, `TEMP`, `TMP`, plus necessary Windows OS and SDK paths) plus hash-seed and bytecode pinning.
- Secret scan of edited files. It reports rule and location, never the matched value.
- Source-of-truth protection: the agent works in a copy; delivery refuses to overwrite drifted files.
- **Designed, not built:** container or VM isolation, network egress controls, process and memory limits, and credential expiry. The MVP relies on a temp working copy plus the restricted environment, and that is weaker than a real sandbox.

## 6. Permission model — built
Capabilities are defined in `permissions.py`. A task requests a subset, and the workflow caps it. Actors: `planner` (read), `coding_agent` (read + write), `verifier` (read + execute tests), and `deliverer` (read + write). Every decision is ledgered as `permission_decision`. The coding agent cannot run tests, and the verifier cannot write.

## 7. Agent architecture — built (minimal)
`Agent.propose(attempt, feedback) -> Proposal(intent, edits)`. Two implementations exist. `ScriptedAgent` replays a JSON file of proposals; it is deterministic and needs no network, which is what makes the repair loop testable. `ModelAgent` (`agent: "anthropic:<model>"`) uses the Anthropic Messages API via `urllib`; it is built and tested offline with an injected transport, but a live call has not yet been run. The planning, research, review, and security agents from the spec are **designed, not built**. Each would be another actor with its own grant set.

## 8. Workflow architecture — built
`workflows/registry.json` is data-driven. Each entry gives task types, max risk, permissions, command allowlist, repairable failure categories, retry cap, autonomy level, and whether a human gate is required. Adding a workflow that reuses the existing stages needs no code change.

## 9. Database / data model — built as files
Run state lives on disk: `ledger.jsonl` is the source of truth, and the state is derived from its last transition. A database is **designed, not built**. It is unnecessary at this scale and would add a dependency.

## 10. Execution-log schema — built
Each line is `{seq, ts, kind, data, prev, hash}`. `hash` is SHA-256 over the canonical JSON of the entry, including `prev`, so any edit, deletion, or reorder breaks `verify`. Kinds: `task_received`, `transition`, `blocked`, `classification`, `permission_decision`, `plan`, `environment`, `agent_proposal`, `files_written`, `workspace_reset`, `verification`, `failure`, `retry`, `changes`, `escalation`, `human_decision`, `delivery`, `rollback_refused`, `error`.
Limit: this is tamper-evident, not tamper-proof. Someone with write access can rebuild the chain. Anchoring the head hash externally is **designed, not built**.

## 11. API specification — built as CLI
- `python -m darkfactory run <spec.json>`: exit 0 at `REVIEW_GATE`/`COMPLETED`, 1 otherwise.
- `approve <task> [--by]`, `reject <task> --reason`, `rollback <task> --reason`
- `show <task>`: state plus timeline. `verify <task>`: hash-chain check.
An HTTP API is **designed, not built**.

## 12. Repository structure — built
```
darkfactory/   package (pipeline modules)
workflows/     registry.json
examples/      sample_project (a real bug), tasks/, agents/ (scripted proposals)
tests/         test_factory.py (unittest, stdlib only)
docs/          this file
runs/          generated per-run records (git-ignored)
```

## 13. Technology decisions — built
- **Python 3.12, standard library only.** No third-party dependencies, so there is no supply-chain surface to audit and no install step. Reliability and reproducibility are the priorities.
- **unittest.** Used for the same reason: no test-runner dependency.
- **JSON Lines + SHA-256 for the ledger.** It is human-readable, appendable, and verifiable with no database.
- **Subprocess with `shell=False`.** Avoids shell injection and keeps the command allowlist meaningful.

## 14. Development roadmap — see README "Next steps"

## 15. MVP definition — built
Two workflows (bug fix in a Python project verified by unittest, and C#/.NET bug fix verified by dotnet test, gated for human approval), an end-to-end path through repair, gate, delivery, and rollback, and the complete audit trail.

## 16. Testing strategy — built
`tests/test_factory.py` (32 tests) covers the happy path with repair, policy and secret escalations, retry exhaustion, agent script exhaustion, spec and risk blocking, permission denial, drift protection, rejection, rollback and its refusal after a later edit, state-machine rules, and ledger tamper and deletion detection, plus .NET workflow classification and command parsing. Every pipeline test uses a temp copy of the sample project, so the real repo is never modified by tests.

## 17. Deployment strategy — designed
Currently a local CLI run on a developer machine. A container image with a pinned Python version and a read-only source mount is the intended next step.

## 18. Monitoring strategy — partially built
The ledger is the observability surface, and `show` renders it. A dashboard and metrics (success rate, retry rate, cost per task) are **designed, not built**.

## 19. Failure-handling strategy — built
`failures.py` defines the 12 categories and a fixed response for each. Only `TEST_FAILURE` may be repaired, and only within the retry budget. Everything else escalates. Each category has predefined next actions in the escalation report.

## 20. Autonomy-promotion criteria — designed
The workflow declares `autonomy_level` 3 (gated) and requires human approval. Promotion to Level 4 (narrow lights-out) should require at least N consecutive approved runs with no drift or rollback, a stable first-pass rate, zero security findings, and a clean ledger audit. These thresholds are not yet computed; the metrics that would compute them are not built.

## Technology evaluation: OpenHands
Not evaluated in this session. I did not verify its current capabilities, licensing, or API, so I am not making a recommendation. It is a candidate for the execution-isolation layer in a later phase, and the `ScriptedAgent` / runner boundary is where it would plug in.

## Deliberate deviations from the spec
- No containers in the MVP. Isolation is a temp working copy plus an environment allowlist. This is weaker, and it is named as such.
- The coding agent is scripted. The repair loop is real, but the proposals come from a file, so this shows the control flow and not model capability.
- Dashboard, metrics, multi-model routing, and multi-agent orchestration are deferred. They depend on having more than one real workflow with run history to measure.
