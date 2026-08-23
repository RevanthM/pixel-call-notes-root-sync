#!/usr/bin/env python3
"""Fail when a public tree contains likely secrets, PII, or private artifacts."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
from pathlib import Path

SKIP_DIRS = {".git", ".venv", "__pycache__", ".pytest_cache", ".aws-sam"}
PRIVATE_SUFFIXES = {
    ".apk",
    ".img",
    ".m4a",
    ".mp4",
    ".mov",
    ".webm",
    ".flac",
    ".aac",
    ".amr",
    ".mp3",
    ".ogg",
    ".wav",
    ".pb",
    ".db",
    ".db-wal",
    ".db-shm",
    ".sqlite",
    ".sqlite3",
    ".log",
    ".jsonl",
    ".csv",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".heic",
    ".pdf",
    ".docx",
    ".bin",
    ".zip",
    ".tar",
    ".tgz",
    ".gz",
    ".7z",
    ".rar",
    ".xz",
    ".bz2",
    ".pcap",
    ".har",
    ".pem",
    ".key",
    ".p12",
    ".jks",
    ".keystore",
    ".tfstate",
    ".tfvars",
}
PRIVATE_NAMES = {
    ".env",
    ".bucket-name",
    ".sync-state.json",
    "credentials",
    "samconfig.toml",
    ".npmrc",
    ".pypirc",
    ".netrc",
    "id_rsa",
    "id_ed25519",
    "id_ecdsa",
}

PATTERNS = {
    "AWS access key": re.compile(r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b"),
    "GitHub token": re.compile(
        r"\b(?:ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{30,255}\b"
        r"|\bgithub_pat_[A-Za-z0-9_]{20,255}\b"
    ),
    "private key": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "AWS account ARN": re.compile(r"arn:aws[a-z-]*:[^:\s]*:[^:\s]*:\d{12}:"),
    "AWS account ID": re.compile(r"\b\d{12}\b"),
    "local home path": re.compile(r"(?:/Users|/home)/[A-Za-z0-9._-]+/"),
    "personal email": re.compile(
        r"\b[A-Za-z0-9._%+-]+@(?!example\.com\b|users\.noreply\.github\.com\b)"
        r"[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
    ),
    "US phone number": re.compile(
        r"(?<!\d)(?:\+?1[\s.-]?)?\(?[2-9]\d{2}\)?[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
    ),
    "millisecond call epoch": re.compile(r"\b1[5-9]\d{11}\b"),
    "static device serial": re.compile(
        r"(?i)[\"']?(?:adb[_-]?serial|device[_-]?serial|serial)[\"']?"
        r"\s*[:=]\s*[\"'][A-Za-z0-9]{8,}[\"']"
        r"|\badb\s+-s\s+(?!\$|<|\{)[A-Za-z0-9]{8,}\b"
    ),
    "literal S3 bucket URL": re.compile(
        r"s3://(?!(?:<|\$|\{|synthetic-|example-))"
        r"[a-z0-9][a-z0-9.-]{2,62}/"
    ),
}


def files(root: Path):
    if (root / ".git").is_dir() and shutil.which("git"):
        proc = subprocess.run(
            [
                "git",
                "-C",
                str(root),
                "ls-files",
                "--cached",
                "--others",
                "--exclude-standard",
                "-z",
            ],
            capture_output=True,
            timeout=30,
        )
        if proc.returncode == 0:
            for relative in proc.stdout.decode(errors="replace").split("\0"):
                if relative:
                    yield root / relative
            return
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_symlink() or path.is_file():
            yield path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("."))
    args = parser.parse_args()
    root = args.root.resolve()
    findings: list[str] = []

    denylist = [
        item.strip()
        for item in os.environ.get("PUBLIC_SAFETY_DENYLIST", "").split(",")
        if item.strip()
    ]

    for path in files(root):
        relative = path.relative_to(root)
        name = path.name.lower()
        if path.is_symlink():
            findings.append(f"{relative}: symbolic links are forbidden")
            continue
        if (
            name in PRIVATE_NAMES
            or (name.startswith(".env.") and name != ".env.example")
            or name.startswith("credentials.")
            or name.startswith(("id_rsa", "id_ed25519", "id_ecdsa", "kubeconfig"))
            or ".tfstate" in name
            or path.suffix.lower() in PRIVATE_SUFFIXES
        ):
            findings.append(f"{relative}: forbidden private artifact")
            continue
        if path.stat().st_size > 2 * 1024 * 1024:
            findings.append(f"{relative}: file exceeds the 2 MiB public-source limit")
            continue
        try:
            raw = path.read_bytes()
        except OSError as exc:
            findings.append(f"{relative}: cannot read ({exc})")
            continue
        if b"\0" in raw:
            findings.append(f"{relative}: unexpected binary file")
            continue
        text = raw.decode("utf-8", errors="replace")
        for label, pattern in PATTERNS.items():
            for match in pattern.finditer(text):
                if (
                    label == "AWS account ARN"
                    and ":000000000000:" in match.group()
                ):
                    continue
                if label == "AWS account ID" and len(set(match.group())) == 1:
                    continue
                line = text.count("\n", 0, match.start()) + 1
                findings.append(f"{relative}:{line}: likely {label}")
        folded = text.casefold()
        for forbidden in denylist:
            if forbidden.casefold() in folded:
                findings.append(f"{relative}: contains configured deny-list value")

    if findings:
        print("Public safety check failed:")
        for finding in findings:
            print(f"  {finding}")
        return 1

    print("Public safety check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
