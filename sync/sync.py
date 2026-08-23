#!/usr/bin/env python3
"""Stream private Pixel Call Notes audio to a private, versioned S3 bucket."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from adb_source import AdbError, Device, query_call_log, query_fermat_audio
from s3_sink import S3Sink
from state import SyncState

DEFAULT_STATE = (
    Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    / "pixel-call-notes-root-sync"
    / "state.json"
)
DEVICE_ID_PATTERN = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def day_partition(epoch_ms: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.timezone.utc).strftime(
        "%Y-%m-%d"
    )


def iso_utc(epoch_ms: int) -> str:
    return dt.datetime.fromtimestamp(epoch_ms / 1000, tz=dt.timezone.utc).isoformat()


def opaque_record_id(device_id: str, epoch_ms: int) -> str:
    """Stable pseudonym that keeps the exact call epoch out of object names."""
    value = f"{device_id}\0{epoch_ms}".encode()
    return hashlib.sha256(value).hexdigest()[:24]


def resolve_bucket(value: str | None, dry_run: bool) -> str:
    if value:
        return value
    if os.environ.get("CALLNOTES_BUCKET"):
        return os.environ["CALLNOTES_BUCKET"]
    if dry_run:
        return "<dry-run-bucket>"
    raise SystemExit("Pass --bucket or set CALLNOTES_BUCKET.")


def resolve_region(value: str | None, dry_run: bool) -> str:
    region = (
        value
        or os.environ.get("AWS_REGION")
        or os.environ.get("AWS_DEFAULT_REGION")
    )
    if region:
        return region
    if dry_run:
        return "us-west-1"
    raise SystemExit("Pass --region or set AWS_REGION.")


def probe_audio(path: Path) -> dict:
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        raise RuntimeError("ffprobe is required before uploading Call Notes audio")
    proc = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-select_streams",
            "a:0",
            "-show_entries",
            "stream=codec_name,sample_rate,channels,channel_layout,duration",
            "-of",
            "json",
            str(path),
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode:
        raise RuntimeError("ffprobe rejected the candidate Call Notes audio")
    try:
        stream = json.loads(proc.stdout)["streams"][0]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        raise RuntimeError("ffprobe returned no usable Call Notes audio stream")
    result = {
        "codec": stream.get("codec_name"),
        "sample_rate_hz": int(stream["sample_rate"])
        if stream.get("sample_rate")
        else None,
        "channels": stream.get("channels"),
        "channel_layout": stream.get("channel_layout"),
        "duration_sec": float(stream["duration"]) if stream.get("duration") else None,
    }
    result = {key: value for key, value in result.items() if value is not None}
    if (
        result.get("codec") != "aac"
        or result.get("channels") != 2
        or result.get("sample_rate_hz", 0) <= 0
        or result.get("duration_sec", 0) <= 0
    ):
        raise RuntimeError("candidate audio does not match the validated stereo AAC format")
    return result


def remote_stat(device: Device, path: str) -> tuple[int, int]:
    output = device.root_shell(f"stat -c '%s|%Y' {shlex.quote(path)}").strip()
    size, modified = output.rsplit("|", 1)
    return int(size), int(modified)


def pull_stable_audio(device: Device, audio: dict, local: Path) -> None:
    expected = (audio["bytes"], audio["modified_epoch_s"])
    before = remote_stat(device, audio["device_path"])
    if before != expected:
        raise RuntimeError("Call Notes audio changed before transfer; retry later")
    device.pull_root(audio["device_path"], local)
    after = remote_stat(device, audio["device_path"])
    if after != before or local.stat().st_size != before[0]:
        local.unlink(missing_ok=True)
        raise RuntimeError("Call Notes audio changed during transfer; retry later")


def metadata_for(
    call: dict | None,
    audio: dict,
    *,
    device: Device,
    device_id: str,
    record_id: str,
    include_call_metadata: bool,
    include_contact_details: bool,
    include_device_details: bool,
) -> dict:
    epoch_ms = audio["call_epoch_ms"]
    metadata = {
        "schema_version": 4,
        "record_type": "call_note",
        "partition_tz": "UTC",
        "record_id": record_id,
        "call_metadata_included": include_call_metadata,
        "contact_details_included": include_contact_details,
        "device_details_included": include_device_details,
        "source": {"device_id": device_id},
        "audio": None,
    }
    if include_call_metadata:
        metadata["call"] = {
            "epoch_ms": epoch_ms,
            "started_utc": iso_utc(epoch_ms),
            "direction": call.get("direction", "unknown") if call else "unknown",
            "duration_sec": call.get("duration_sec") if call else None,
            "features": call.get("features") if call else None,
        }
    if include_contact_details and call:
        metadata["contact"] = {
            "name": call.get("contact_name"),
            "phone_number": call.get("phone_number"),
            "country_iso": call.get("country_iso"),
        }
    if include_device_details:
        metadata["source"].update(device.describe())
    return metadata


def main() -> int:
    if sys.version_info < (3, 10):
        raise SystemExit("Python 3.10 or newer is required.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket")
    parser.add_argument("--region")
    parser.add_argument("--profile", help="AWS named profile; never use account root")
    parser.add_argument("--serial", help="adb serial when several devices are connected")
    parser.add_argument("--device-id", default="primary", help="non-identifying S3 label")
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--backfill", action="store_true")
    parser.add_argument(
        "--minimum-age-seconds",
        type=int,
        default=120,
        help="wait after the source file's final modification before considering it stable",
    )
    parser.add_argument(
        "--include-call-metadata",
        action="store_true",
        help="include exact call time, direction, duration, and feature flags in S3",
    )
    parser.add_argument(
        "--include-contact-details",
        action="store_true",
        help="explicitly include contact names and phone numbers in private S3 metadata",
    )
    parser.add_argument(
        "--include-device-details",
        action="store_true",
        help="include model, codename, Android version, and build ID in S3",
    )
    args = parser.parse_args()

    if not DEVICE_ID_PATTERN.fullmatch(args.device_id):
        parser.error("--device-id must match [A-Za-z0-9_-]{1,64}")
    if not 0 <= args.minimum_age_seconds <= 86400:
        parser.error("--minimum-age-seconds must be between 0 and 86400")
    bucket = resolve_bucket(args.bucket, args.dry_run)
    region = resolve_region(args.region, args.dry_run)
    state = SyncState.load(args.state)

    try:
        device = Device(serial=args.serial)
        device.require_authorized()
        if not device.has_root():
            raise AdbError("Magisk root is required to read private Call Notes audio.")
        calls = (
            {
                call["call_id"]: call
                for call in query_call_log(device, since_epoch_ms=0)
            }
            if args.include_call_metadata or args.include_contact_details
            else {}
        )
        audio_items = query_fermat_audio(device, since_epoch_ms=0)
        sink = S3Sink(
            bucket,
            region=region,
            profile=args.profile,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            state.bind_scope(
                {
                    "schema_version": 1,
                    "aws_account_id": sink.account_id,
                    "bucket": bucket,
                    "region": region,
                    "device_id": args.device_id,
                    "device_binding": hashlib.sha256(
                        f"adb-serial\0{device.serial}".encode()
                    ).hexdigest(),
                    "privacy": {
                        "call_metadata": args.include_call_metadata,
                        "contact_details": args.include_contact_details,
                        "device_details": args.include_device_details,
                    },
                }
            )
    except (AdbError, RuntimeError, OSError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    settled = [
        audio
        for audio in audio_items
        if time.time() - audio["modified_epoch_s"] >= args.minimum_age_seconds
    ]
    pending = []
    for audio in settled:
        state_key = f"call:{audio['call_epoch_ms']}"
        existing = state.records.get(state_key)
        signature = {
            "bytes": audio["bytes"],
            "modified_epoch_s": audio["modified_epoch_s"],
        }
        existing_source = existing.get("source", {}) if existing else {}
        if existing and any(
            existing_source.get(key) != value for key, value in signature.items()
        ):
            print(
                "error: a previously synced source recording changed; refusing to "
                "replace its immutable destination",
                file=sys.stderr,
            )
            return 1
        if args.backfill or existing is None:
            pending.append(audio)
    mode = "DRY RUN" if args.dry_run else "private S3 bucket"
    print(f"target  {mode} (name hidden)")
    print(
        "privacy "
        f"call-metadata={'included' if args.include_call_metadata else 'redacted'}, "
        f"contacts={'included' if args.include_contact_details else 'redacted'}, "
        f"device-details={'included' if args.include_device_details else 'redacted'}"
    )
    print(f"delta   {len(pending)} Call Notes recording(s)")
    if len(settled) != len(audio_items):
        print(f"wait    {len(audio_items) - len(settled)} recording(s) still settling")

    try:
        for index, audio in enumerate(pending, start=1):
            call_id = str(audio["call_epoch_ms"])
            call = calls.get(call_id)
            record_id = opaque_record_id(args.device_id, audio["call_epoch_ms"])
            prefix = (
                f"call-notes/dt={day_partition(audio['call_epoch_ms'])}/{record_id}"
            )
            metadata = metadata_for(
                call,
                audio,
                device=device,
                device_id=args.device_id,
                record_id=record_id,
                include_call_metadata=args.include_call_metadata,
                include_contact_details=args.include_contact_details,
                include_device_details=args.include_device_details,
            )

            if args.dry_run:
                metadata["audio"] = {
                    "key": "audio.m4a",
                    "bytes": audio["bytes"],
                    "mime_type": audio["mime_type"],
                }
                print(f"  candidate {index}: ready (identifier hidden)")
                continue

            with tempfile.TemporaryDirectory() as temporary:
                local = Path(temporary) / "audio.m4a"
                pull_stable_audio(device, audio, local)
                audio_facts = probe_audio(local)
                result = sink.put_file(
                    local, f"{prefix}/audio.m4a", immutable=True
                )
                metadata["audio"] = {
                    "key": "audio.m4a",
                    "sha256": result.sha256,
                    "bytes": result.bytes,
                    "mime_type": audio["mime_type"],
                    **audio_facts,
                }
            metadata_result = sink.put_json(
                metadata,
                f"{prefix}/source/metadata.json",
                immutable=True,
            )
            if not result.version_id or not metadata_result.version_id:
                raise RuntimeError("versioned S3 writes did not return source VersionIds")
            ready_result = sink.put_json(
                {
                    "schema_version": 1,
                    "pipeline_version": "v1",
                    "record_id": record_id,
                    "audio": {
                        "key": f"{prefix}/audio.m4a",
                        "version_id": result.version_id,
                        "sha256": result.sha256,
                        "bytes": result.bytes,
                    },
                    "metadata": {
                        "key": f"{prefix}/source/metadata.json",
                        "version_id": metadata_result.version_id,
                        "sha256": metadata_result.sha256,
                        "bytes": metadata_result.bytes,
                    },
                },
                f"{prefix}/ready.json",
                immutable=True,
            )
            if not ready_result.version_id:
                raise RuntimeError("versioned S3 write did not return a ready VersionId")
            state.records[f"call:{call_id}"] = {
                "prefix": prefix,
                "source": {
                    "bytes": audio["bytes"],
                    "modified_epoch_s": audio["modified_epoch_s"],
                    "sha256": result.sha256,
                },
                "audio_sha256": result.sha256,
                "audio_version_id": result.version_id,
                "ready_version_id": ready_result.version_id,
            }
            state.last_call_epoch_ms = max(
                state.last_call_epoch_ms, audio["call_epoch_ms"]
            )
            print(f"  synced record {index} (identifier hidden)")

        if not args.dry_run:
            sink.put_json(
                {
                    "schema_version": 1,
                    "device_id": args.device_id,
                    "device_details_included": args.include_device_details,
                    "source": (
                        {**device.describe(), "device_id": args.device_id}
                        if args.include_device_details
                        else {"device_id": args.device_id}
                    ),
                    "record_count": len(state.records),
                    "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                },
                f"_manifests/device={args.device_id}/latest.json",
            )
            state.save()
    except Exception as exc:
        print(f"error: sync incomplete; cursor not saved: {exc}", file=sys.stderr)
        return 1

    suffix = "reviewed; nothing uploaded" if args.dry_run else "processed"
    print(f"done    {len(pending)} record(s) {suffix}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
