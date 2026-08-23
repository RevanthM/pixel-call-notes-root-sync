"""Read-only Call Notes queries and binary-safe file streams over adb."""

from __future__ import annotations

import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

CALL_LOG_COLUMNS = [
    "_id",
    "date",
    "duration",
    "number",
    "name",
    "type",
    "countryiso",
    "normalized_number",
    "features",
]
CALL_TYPES = {
    1: "incoming",
    2: "outgoing",
    3: "missed",
    4: "voicemail",
    5: "rejected",
    6: "blocked",
}
FERMAT_AUDIO_DIR = "/data/user/0/com.google.android.dialer/files/fermat_files"


class AdbError(RuntimeError):
    pass


def find_adb() -> str:
    candidates = [
        "adb",
        str(Path.home() / "Library/Android/sdk/platform-tools/adb"),
        "/opt/homebrew/bin/adb",
    ]
    for candidate in candidates:
        resolved = shutil.which(candidate) or (
            candidate if Path(candidate).exists() else None
        )
        if resolved:
            return resolved
    raise AdbError("adb not found; install Android SDK Platform Tools")


@dataclass
class Device:
    adb: str = field(default_factory=find_adb)
    serial: str | None = None
    _description: dict | None = field(default=None, repr=False)

    def _base(self) -> list[str]:
        return [self.adb] + (["-s", self.serial] if self.serial else [])

    def shell(self, command: str, timeout: int = 60) -> str:
        proc = subprocess.run(
            self._base() + ["shell", command],
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if proc.returncode != 0:
            raise AdbError(f"adb shell failed\n{proc.stderr.strip()}")
        return proc.stdout

    def root_shell(self, command: str, timeout: int = 60) -> str:
        return self.shell(f"su -c {shlex.quote(command)}", timeout=timeout)

    def has_root(self) -> bool:
        try:
            return self.root_shell("id -u", timeout=15).strip() == "0"
        except (AdbError, subprocess.TimeoutExpired):
            return False

    def pull_root(self, remote: str, local: Path, timeout: int = 300) -> None:
        """Stream a root-only file without staging it in shared device storage."""
        local.parent.mkdir(parents=True, exist_ok=True)
        command = f"cat -- {shlex.quote(remote)}"
        wrapped = f"su -c {shlex.quote(command)}"
        with local.open("wb") as handle:
            proc = subprocess.run(
                self._base() + ["exec-out", wrapped],
                stdout=handle,
                stderr=subprocess.PIPE,
                timeout=timeout,
            )
        if proc.returncode != 0:
            local.unlink(missing_ok=True)
            error = proc.stderr.decode(errors="replace").strip()
            raise AdbError(f"root stream failed for private Call Notes audio\n{error}")

    def require_authorized(self) -> None:
        proc = subprocess.run(
            [self.adb, "devices"], capture_output=True, text=True, timeout=30
        )
        rows = [line.split() for line in proc.stdout.splitlines()[1:] if line.strip()]
        if self.serial is None and len(rows) > 1:
            raise AdbError("Several devices are attached; pass --serial explicitly.")
        matching = [row for row in rows if self.serial is None or row[0] == self.serial]
        if not matching:
            raise AdbError("No matching Android device detected over USB.")
        serial, state = matching[0][0], matching[0][1]
        if state == "unauthorized":
            raise AdbError("Device is unauthorized; accept the USB-debugging prompt.")
        if state != "device":
            raise AdbError(f"Device state is {state!r}, expected 'device'.")
        self.serial = serial

    def describe(self) -> dict[str, str]:
        """Non-identifying device facts safe for private object metadata."""
        if self._description is None:
            props = self.shell(
                "getprop ro.product.model; "
                "getprop ro.product.device; "
                "getprop ro.build.version.release; "
                "getprop ro.build.version.sdk; "
                "getprop ro.build.id"
            ).splitlines()
            props += [""] * (5 - len(props))
            self._description = {
                "model": props[0].strip(),
                "codename": props[1].strip(),
                "android_release": props[2].strip(),
                "api_level": props[3].strip(),
                "build_id": props[4].strip(),
            }
        return self._description


def _parse_rows(output: str, columns: list[str]) -> list[dict[str, str]]:
    splitter = re.compile(
        r", (?=(?:" + "|".join(re.escape(column) for column in columns) + r")=)"
    )
    rows = []
    for line in output.splitlines():
        if not line.strip().startswith("Row:"):
            continue
        parts = line.strip().split(" ", 2)
        body = parts[2] if len(parts) == 3 else ""
        row = {}
        for field in splitter.split(body):
            key, _, value = field.partition("=")
            row[key.strip()] = value
        rows.append(row)
    return rows


def _integer(value: str | None) -> int | None:
    if value in (None, "", "NULL"):
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _text(value: str | None) -> str | None:
    return None if value in (None, "", "NULL") else value


def query_call_log(device: Device, since_epoch_ms: int = 0) -> list[dict]:
    projection = ":".join(CALL_LOG_COLUMNS)
    where = f'"date > {int(since_epoch_ms)}"'
    output = device.shell(
        "content query --uri content://call_log/calls "
        f"--projection {projection} --where {where}"
    )
    calls = []
    for row in _parse_rows(output, CALL_LOG_COLUMNS):
        epoch_ms = _integer(row.get("date"))
        if epoch_ms is None:
            continue
        calls.append(
            {
                "call_log_id": _integer(row.get("_id")),
                "call_id": str(epoch_ms),
                "call_epoch_ms": epoch_ms,
                "duration_sec": _integer(row.get("duration")) or 0,
                "phone_number": _text(row.get("normalized_number"))
                or _text(row.get("number")),
                "contact_name": _text(row.get("name")),
                "direction": CALL_TYPES.get(
                    _integer(row.get("type")) or -1, "unknown"
                ),
                "country_iso": _text(row.get("countryiso")),
                "features": _integer(row.get("features")),
            }
        )
    return sorted(calls, key=lambda call: call["call_epoch_ms"])


def query_fermat_audio(device: Device, since_epoch_ms: int = 0) -> list[dict]:
    """List private Dialer audio; timestamp filenames make the call join exact."""
    device.root_shell(f"test -d {FERMAT_AUDIO_DIR} && test -r {FERMAT_AUDIO_DIR}")
    command = (
        f"for f in {FERMAT_AUDIO_DIR}/*.m4a; do "
        '[ -f "$f" ] || continue; '
        '[ ! -L "$f" ] || continue; '
        'stat -c "%n|%s|%Y" "$f"; '
        "done"
    )
    items = []
    for line in device.root_shell(command).splitlines():
        try:
            path_text, bytes_text, modified_text = line.rsplit("|", 2)
            path = Path(path_text)
            call_epoch_ms = int(path.stem)
            byte_count = int(bytes_text)
            modified_epoch_s = int(modified_text)
        except (TypeError, ValueError):
            continue
        if call_epoch_ms <= since_epoch_ms:
            continue
        if byte_count <= 0:
            raise AdbError("zero-byte Call Notes audio found; retry after Dialer finishes")
        items.append(
            {
                "display_name": path.name,
                "device_path": str(path),
                "call_epoch_ms": call_epoch_ms,
                "bytes": byte_count,
                "modified_epoch_s": modified_epoch_s,
                "mime_type": "audio/mp4",
            }
        )
    return sorted(items, key=lambda item: item["call_epoch_ms"])
