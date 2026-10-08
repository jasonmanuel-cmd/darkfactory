"""Append-only, hash-chained execution record (JSON Lines).

Each entry commits to the hash of the entry before it. Editing, deleting, reordering or
inserting any entry makes ``verify`` report a problem. This makes the record tamper-evident;
it does not make it tamper-proof, since someone with write access can rebuild the whole chain.
Anchoring the head hash somewhere external is a later-phase item.
"""

import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .errors import LedgerIntegrityError

GENESIS = "0" * 64


def _digest(body: dict) -> str:
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class Ledger:
    def __init__(self, path: Path):
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def append(self, kind: str, **data: object) -> dict:
        entries = self.read()
        prev = entries[-1]["hash"] if entries else GENESIS
        # Normalise to plain JSON types so the digest matches what is read back later.
        payload = json.loads(json.dumps(data, default=str, ensure_ascii=False))
        body = {
            "seq": len(entries),
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": kind,
            "data": payload,
            "prev": prev,
        }
        entry = {**body, "hash": _digest(body)}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return entry

    def read(self) -> list[dict]:
        if not self.path.exists():
            return []
        entries = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), 1):
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise LedgerIntegrityError(f"{self.path}:{lineno} is not valid JSON") from exc
        return entries

    def verify(self) -> list[str]:
        """Return a list of integrity problems; an empty list means the chain is intact."""
        problems = []
        prev = GENESIS
        for index, entry in enumerate(self.read()):
            if entry.get("seq") != index:
                problems.append(f"entry {index}: sequence number is {entry.get('seq')}")
            if entry.get("prev") != prev:
                problems.append(f"entry {index}: previous-hash link is broken")
            body = {k: v for k, v in entry.items() if k != "hash"}
            if _digest(body) != entry.get("hash"):
                problems.append(f"entry {index}: content hash does not match")
            prev = entry.get("hash", "")
        return problems

    def current_state(self) -> str | None:
        for entry in reversed(self.read()):
            if entry["kind"] == "transition":
                return entry["data"]["to"]
        return None
