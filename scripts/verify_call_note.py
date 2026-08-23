#!/usr/bin/env python3
"""Verify the latest rooted Pixel Call Note without exposing its text by default."""

from __future__ import annotations

import argparse
import shlex
import shutil
import sqlite3
import subprocess
import tempfile
import time
from pathlib import Path

REMOTE_DB = "/data/user/0/com.google.android.dialer/files/fermat.db"


def adb_base(serial: str | None) -> list[str]:
    adb = shutil.which("adb")
    if not adb:
        raise SystemExit("adb not found")
    if not serial:
        proc = subprocess.run(
            [adb, "devices"], capture_output=True, text=True, timeout=30
        )
        devices = [
            row[0]
            for line in proc.stdout.splitlines()[1:]
            if (row := line.split()) and len(row) > 1 and row[1] == "device"
        ]
        if len(devices) != 1:
            raise SystemExit("Expected one authorized device; pass --serial explicitly.")
        serial = devices[0]
    return [adb, "-s", serial]


def root_stream(base: list[str], remote: str) -> bytes:
    command = f"cat -- {shlex.quote(remote)}"
    wrapped = f"su -c {shlex.quote(command)}"
    proc = subprocess.run(
        base + ["exec-out", wrapped],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=60,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.decode(errors="replace").strip())
    return proc.stdout


def root_test(base: list[str], remote: str) -> bool:
    command = f"test -s {shlex.quote(remote)}"
    wrapped = f"su -c {shlex.quote(command)}"
    return subprocess.run(
        base + ["shell", wrapped],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=30,
    ).returncode == 0


def root_signature(base: list[str], remote: str) -> str:
    quoted = shlex.quote(remote)
    command = (
        f"if test -e {quoted}; then "
        f"stat -c '%i:%s:%Y' -- {quoted} && sha256sum -- {quoted}; "
        "else printf missing; fi"
    )
    wrapped = f"su -c {shlex.quote(command)}"
    proc = subprocess.run(
        base + ["shell", wrapped],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError("could not inspect the Call Notes database")
    return proc.stdout.strip()


def stable_snapshot(base: list[str]) -> dict[str, bytes]:
    paths = [REMOTE_DB, REMOTE_DB + "-wal", REMOTE_DB + "-shm"]
    for _attempt in range(3):
        before = {path: root_signature(base, path) for path in paths}
        files = {REMOTE_DB: root_stream(base, REMOTE_DB)}
        for path in paths[1:]:
            if before[path] == "missing":
                continue
            try:
                files[path] = root_stream(base, path)
            except RuntimeError:
                files = {}
                break
        after = {path: root_signature(base, path) for path in paths}
        if files and before == after:
            return files
    raise RuntimeError("Call Notes database changed during snapshot; retry")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial")
    parser.add_argument("--show-summary", action="store_true")
    parser.add_argument(
        "--max-age-hours",
        type=float,
        default=24,
        help="reject a latest record older than this; use 0 to disable",
    )
    args = parser.parse_args()
    base = adb_base(args.serial)

    with tempfile.TemporaryDirectory() as tmp:
        directory = Path(tmp)
        snapshot = stable_snapshot(base)
        (directory / "fermat.db").write_bytes(snapshot[REMOTE_DB])
        for suffix in ("-wal", "-shm"):
            data = snapshot.get(REMOTE_DB + suffix)
            if data is not None:
                (directory / f"fermat.db{suffix}").write_bytes(data)

        with sqlite3.connect(directory / "fermat.db") as connection:
            connection.row_factory = sqlite3.Row
            result = connection.execute(
                """
            SELECT d.createdTimestampMs,
                   a.durationMs,
                   a.audioFilePath,
                   t.transcriberModel,
                   t.transcriberVersion,
                   t.transcriptionPath,
                   t.errorType AS transcriptionError,
                   s.modelName,
                   s.modelVersion,
                   s.summary,
                   s.errorType AS summaryError
            FROM fermat_data d
            LEFT JOIN fermat_audio a
              ON a.fermatDataCreatedTimestampMs = d.createdTimestampMs
            LEFT JOIN fermat_transcription_state t
              ON t.fermatDataCreatedTimestampMs = d.createdTimestampMs
            LEFT JOIN fermat_summary_state s
              ON s.fermatDataCreatedTimestampMs = d.createdTimestampMs
            ORDER BY d.createdTimestampMs DESC
            LIMIT 1
            """
            ).fetchone()
            row = dict(result) if result is not None else None

    if row is None:
        print("No Call Notes records found.")
        return 1
    if args.max_age_hours > 0:
        age_ms = time.time() * 1000 - row["createdTimestampMs"]
        if age_ms < 0 or age_ms > args.max_age_hours * 60 * 60 * 1000:
            print("Latest Call Notes record is outside the requested freshness window.")
            return 1

    audio_ok = bool(row["audioFilePath"] and root_test(base, row["audioFilePath"]))
    transcript_ok = bool(
        row["transcriptionPath"]
        and not row["transcriptionError"]
        and root_test(base, row["transcriptionPath"])
    )
    summary = row["summary"] or ""
    summary_ok = bool(summary.strip() and not row["summaryError"])

    print(f"audio:      {'ok' if audio_ok else 'missing'}")
    print(
        "transcript: "
        f"{'ok' if transcript_ok else 'missing/error'} "
        f"({row['transcriberModel'] or '-'} {row['transcriberVersion'] or '-'})"
    )
    print(
        "summary:    "
        f"{'ok' if summary_ok else 'missing/error'} "
        f"({row['modelName'] or '-'} {row['modelVersion'] or '-'})"
    )
    if row["transcriptionError"]:
        print(f"transcription error: {row['transcriptionError']}")
    if row["summaryError"]:
        print(f"summary error: {row['summaryError']}")
    if args.show_summary and summary:
        print("\nSummary:\n" + summary)
    elif summary:
        print(f"summary text: redacted ({len(summary)} characters)")

    return 0 if audio_ok and transcript_ok and summary_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
