"""Deterministic verification: run only allowlisted commands, with a scrubbed environment.

Commands run with ``shell=False``, so there is no shell expansion. Only the executable
prefixes listed in the workflow are allowed, and the ``python`` token is replaced with the
interpreter that is running the factory, which keeps verification reproducible.
"""

import os
import shlex
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path

from .errors import PolicyViolation

OUTPUT_TAIL_CHARS = 4000
SAFE_ENV_KEYS = ("PATH", "SYSTEMROOT", "TEMP", "TMP")


@dataclass(frozen=True)
class CheckResult:
    command: str
    argv: tuple[str, ...]
    exit_code: int | None
    timed_out: bool
    duration_s: float
    stdout_tail: str
    stderr_tail: str

    @property
    def passed(self) -> bool:
        return self.exit_code == 0 and not self.timed_out

    def to_dict(self) -> dict:
        return {**asdict(self), "passed": self.passed}


def parse_command(command: str, prefixes: tuple[str, ...]) -> list[str]:
    argv = shlex.split(command, posix=True)
    for prefix in prefixes:
        parts = prefix.split()
        if argv[: len(parts)] == parts:
            argv[0] = sys.executable
            return argv
    raise PolicyViolation("POLICY_FAILURE", f"command is not in the workflow allowlist: {command!r}")


def validate_commands(commands: tuple[str, ...], prefixes: tuple[str, ...]) -> None:
    for command in commands:
        parse_command(command, prefixes)


def _tail(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, bytes):
        data = data.decode("utf-8", errors="replace")
    return data[-OUTPUT_TAIL_CHARS:]


def _env() -> dict[str, str]:
    env = {k: os.environ[k] for k in SAFE_ENV_KEYS if k in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONHASHSEED="0", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1")
    return env


def run_checks(commands: tuple[str, ...], workspace: Path, prefixes: tuple[str, ...], timeout_s: float) -> list[CheckResult]:
    results = []
    for command in commands:
        argv = parse_command(command, prefixes)
        started = time.monotonic()
        try:
            proc = subprocess.run(
                argv,
                cwd=str(workspace),
                env=_env(),
                capture_output=True,
                timeout=max(1.0, timeout_s),
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            results.append(
                CheckResult(command, tuple(argv), None, True, round(time.monotonic() - started, 3), _tail(exc.stdout), _tail(exc.stderr))
            )
            break  # The time budget is spent; later checks would only be cut off too.
        except OSError as exc:
            results.append(
                CheckResult(command, tuple(argv), None, False, round(time.monotonic() - started, 3), "", f"could not start process: {exc}")
            )
            continue
        results.append(
            CheckResult(command, tuple(argv), proc.returncode, False, round(time.monotonic() - started, 3), _tail(proc.stdout), _tail(proc.stderr))
        )
    return results


def feedback_from(results: list[CheckResult]) -> str:
    """Evidence handed to the repair attempt: which command failed and what it printed."""
    blocks = []
    for r in results:
        if r.passed:
            continue
        status = "TIMED OUT" if r.timed_out else f"exit {r.exit_code}"
        blocks.append(f"$ {r.command}  ({status})\n{r.stdout_tail}{r.stderr_tail}")
    return "\n".join(blocks)
