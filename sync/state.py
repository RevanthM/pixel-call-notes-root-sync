"""Atomic local cursor for resumable Call Notes sync."""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass, field
from pathlib import Path


@dataclass
class SyncState:
    path: Path
    last_call_epoch_ms: int = 0
    records: dict = field(default_factory=dict)
    scope: dict | None = None

    @classmethod
    def load(cls, path: Path) -> "SyncState":
        if not path.exists():
            return cls(path=path)
        if path.is_symlink():
            raise RuntimeError("refusing a symlinked sync-state file")
        if path.stat().st_mode & 0o077:
            os.chmod(path, 0o600)
        raw = json.loads(path.read_text())
        return cls(
            path=path,
            last_call_epoch_ms=raw.get("last_call_epoch_ms", 0),
            records=raw.get("records", {}),
            scope=raw.get("scope"),
        )

    def bind_scope(self, expected: dict) -> None:
        if self.scope is None:
            if self.records:
                raise RuntimeError(
                    "existing state has no destination scope; select a new --state "
                    "path and run an explicit --backfill"
                )
            self.scope = expected
            return
        if self.scope != expected:
            raise RuntimeError(
                "sync-state scope differs from the AWS destination, device label, "
                "or privacy profile; select a new --state path and use --backfill"
            )

    def save(self) -> None:
        payload = {key: value for key, value in asdict(self).items() if key != "path"}
        if self.path.parent.is_symlink():
            raise RuntimeError("refusing a symlinked sync-state directory")
        parent_existed = self.path.parent.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not parent_existed:
            os.chmod(self.path.parent, 0o700)
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=self.path.parent,
            prefix=".state.",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            os.chmod(temporary, 0o600)
            handle.write(json.dumps(payload, indent=2))
            handle.flush()
            os.fsync(handle.fileno())
        temporary.replace(self.path)
        os.chmod(self.path, 0o600)
