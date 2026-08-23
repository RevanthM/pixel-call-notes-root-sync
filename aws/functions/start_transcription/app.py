"""Commit an exact S3 source version, then start one idempotent Transcribe job."""

from __future__ import annotations

import base64
import datetime as dt
import hashlib
import json
import logging
import os
import re
import time
from urllib.parse import unquote_plus

import boto3
from botocore.exceptions import ClientError

READY_PATTERN = re.compile(
    r"^(?P<prefix>call-notes/dt=\d{4}-\d{2}-\d{2}/[0-9a-f]{24})/ready\.json$"
)
PIPELINE_VERSION = "v1"
JOB_PREFIX = "callnotes-"
TERMINAL_STATES = {"COMPLETED", "FAILED"}
LOGGER = logging.getLogger(__name__)

_s3 = None
_sqs = None
_transcribe = None


def s3_client():
    global _s3
    if _s3 is None:
        _s3 = boto3.client("s3")
    return _s3


def sqs_client():
    global _sqs
    if _sqs is None:
        _sqs = boto3.client("sqs")
    return _sqs


def transcribe_client():
    global _transcribe
    if _transcribe is None:
        _transcribe = boto3.client("transcribe")
    return _transcribe


def expected_owner() -> str:
    return os.environ["EXPECTED_BUCKET_OWNER"]


def checksum_b64(digest: str) -> str:
    return base64.b64encode(bytes.fromhex(digest)).decode()


def source_objects(message: dict) -> list[dict]:
    """Normalize EventBridge and native S3 notification payloads."""
    if message.get("source") == "aws.s3":
        detail = message.get("detail", {})
        obj = detail.get("object", {})
        return [
            {
                "bucket": detail.get("bucket", {}).get("name"),
                "key": unquote_plus(obj.get("key", "")),
                "etag": obj.get("etag", ""),
                "version_id": obj.get("version-id", ""),
            }
        ]

    objects = []
    for record in message.get("Records", []):
        if record.get("eventSource") != "aws:s3":
            continue
        obj = record.get("s3", {}).get("object", {})
        objects.append(
            {
                "bucket": record.get("s3", {}).get("bucket", {}).get("name"),
                "key": unquote_plus(obj.get("key", "")),
                "etag": obj.get("eTag", ""),
                "version_id": obj.get("versionId", ""),
            }
        )
    return objects


def head_descriptor(descriptor: dict, *, require_current: bool = False) -> dict:
    version_id = descriptor.get("version_id")
    if not version_id:
        raise ValueError("versioned source descriptor required")
    request = {
        "Bucket": descriptor["bucket"],
        "Key": descriptor["key"],
        "VersionId": version_id,
        "ExpectedBucketOwner": expected_owner(),
        "ChecksumMode": "ENABLED",
    }
    head = s3_client().head_object(**request)
    if require_current:
        current = s3_client().head_object(
            Bucket=descriptor["bucket"],
            Key=descriptor["key"],
            ExpectedBucketOwner=expected_owner(),
            ChecksumMode="ENABLED",
        )
        if current.get("VersionId") != version_id:
            raise ValueError("source event no longer names the current object")

    digest = descriptor.get("sha256") or head.get("Metadata", {}).get("sha256", "")
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise ValueError("source object lacks a valid SHA-256")
    if head.get("Metadata", {}).get("sha256") != digest:
        raise ValueError("source SHA-256 metadata mismatch")
    if head.get("ChecksumSHA256") != checksum_b64(digest):
        raise ValueError("source lacks an S3-validated SHA-256 checksum")
    size = head.get("ContentLength", 0)
    if size <= 0 or (
        descriptor.get("bytes") is not None and descriptor["bytes"] != size
    ):
        raise ValueError("source object size mismatch")
    event_etag = (descriptor.get("etag") or "").strip('"')
    object_etag = (head.get("ETag") or "").strip('"')
    if event_etag and object_etag and event_etag != object_etag:
        raise ValueError("source ETag mismatch")
    return {
        **descriptor,
        "sha256": digest,
        "bytes": size,
        "etag": object_etag,
    }


def verified_event_object(source: dict) -> dict:
    if not source.get("version_id"):
        raise ValueError("versioned source event required")
    return head_descriptor(source, require_current=True)


def read_versioned_json(descriptor: dict) -> dict:
    response = s3_client().get_object(
        Bucket=descriptor["bucket"],
        Key=descriptor["key"],
        VersionId=descriptor["version_id"],
        ExpectedBucketOwner=expected_owner(),
        ChecksumMode="ENABLED",
    )
    data = response["Body"].read()
    if hashlib.sha256(data).hexdigest() != descriptor["sha256"]:
        raise ValueError("versioned JSON content digest mismatch")
    return json.loads(data)


def ready_contract(ready_source: dict, prefix: str) -> tuple[dict, dict, dict]:
    payload = read_versioned_json(ready_source)
    if (
        payload.get("schema_version") != 1
        or payload.get("pipeline_version") != PIPELINE_VERSION
    ):
        raise ValueError("unsupported ready contract")

    descriptors = []
    for name, suffix in (
        ("audio", "/audio.m4a"),
        ("metadata", "/source/metadata.json"),
    ):
        item = payload.get(name) or {}
        if item.get("key") != prefix + suffix:
            raise ValueError("ready contract contains an unexpected source key")
        descriptor = {
            **item,
            "bucket": ready_source["bucket"],
        }
        descriptors.append(head_descriptor(descriptor, require_current=True))
    return payload, descriptors[0], descriptors[1]


def existing_staged(bucket: str, key: str, digest: str) -> dict | None:
    try:
        head = s3_client().head_object(
            Bucket=bucket,
            Key=key,
            ExpectedBucketOwner=expected_owner(),
            ChecksumMode="ENABLED",
        )
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return None
        raise
    if (
        head.get("Metadata", {}).get("sha256") != digest
        or head.get("ChecksumSHA256") != checksum_b64(digest)
        or head.get("ContentLength", 0) <= 0
        or not head.get("VersionId")
    ):
        raise ValueError("staged input does not match its content address")
    return {
        "bucket": bucket,
        "key": key,
        "version_id": head["VersionId"],
        "sha256": digest,
        "bytes": head["ContentLength"],
        "etag": (head.get("ETag") or "").strip('"'),
    }


def stage_audio(audio: dict, prefix: str) -> dict:
    if audio["bytes"] > int(os.environ.get("MAX_SOURCE_BYTES", "262144000")):
        raise ValueError("source audio exceeds the configured staging limit")
    key = f"{prefix}/pipeline/{PIPELINE_VERSION}/input/{audio['sha256']}.m4a"
    staged = existing_staged(audio["bucket"], key, audio["sha256"])
    if staged:
        return staged

    for attempt in range(3):
        source = s3_client().get_object(
            Bucket=audio["bucket"],
            Key=audio["key"],
            VersionId=audio["version_id"],
            ExpectedBucketOwner=expected_owner(),
            ChecksumMode="ENABLED",
        )
        try:
            response = s3_client().put_object(
                Bucket=audio["bucket"],
                Key=key,
                Body=source["Body"],
                ContentLength=audio["bytes"],
                ContentType="audio/mp4",
                IfNoneMatch="*",
                ExpectedBucketOwner=expected_owner(),
                ServerSideEncryption="AES256",
                ChecksumSHA256=checksum_b64(audio["sha256"]),
                Metadata={
                    "sha256": audio["sha256"],
                    "source-version": audio["version_id"],
                },
            )
            version_id = response.get("VersionId")
            if not version_id:
                raise ValueError("staged write did not return a VersionId")
            return {
                "bucket": audio["bucket"],
                "key": key,
                "version_id": version_id,
                "sha256": audio["sha256"],
                "bytes": audio["bytes"],
                "etag": (response.get("ETag") or "").strip('"'),
            }
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code")
            if code not in {
                "409",
                "412",
                "ConditionalRequestConflict",
                "PreconditionFailed",
            }:
                raise
            staged = existing_staged(audio["bucket"], key, audio["sha256"])
            if staged:
                return staged
            if attempt < 2:
                time.sleep(0.2 * (attempt + 1))
                continue
            raise
        finally:
            source["Body"].close()
    raise RuntimeError("staging retries exhausted")


def transcription_spec(
    staged: dict, ready_source: dict, audio: dict, metadata: dict
) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "language_code": os.environ.get("LANGUAGE_CODE", "en-US"),
        "media_format": "m4a",
        "channel_identification": True,
        "allow_deferred_execution": True,
        "staged": staged,
        "source_contract": {
            "ready_version_id": ready_source["version_id"],
            "ready_sha256": ready_source["sha256"],
            "audio_version_id": audio["version_id"],
            "audio_sha256": audio["sha256"],
            "metadata_version_id": metadata["version_id"],
            "metadata_sha256": metadata["sha256"],
        },
    }


def job_name(specification: dict) -> str:
    canonical = json.dumps(
        specification, sort_keys=True, separators=(",", ":")
    ).encode()
    return JOB_PREFIX + hashlib.sha256(canonical).hexdigest()[:24]


def object_exists(bucket: str, key: str) -> bool:
    try:
        s3_client().head_object(
            Bucket=bucket,
            Key=key,
            ExpectedBucketOwner=expected_owner(),
        )
        return True
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") in {
            "404",
            "NoSuchKey",
            "NotFound",
        }:
            return False
        raise


def put_submitted_status(
    ready_source: dict,
    prefix: str,
    name: str,
    specification: dict,
    audio: dict,
    metadata: dict,
) -> None:
    terminal_key = f"{prefix}/pipeline/{PIPELINE_VERSION}/terminal.json"
    if object_exists(ready_source["bucket"], terminal_key):
        return
    payload = {
        "pipeline_version": PIPELINE_VERSION,
        "status": "TRANSCRIBE_SUBMITTED",
        "transcription_job": name,
        "updated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "specification": specification,
        "source": {
            "ready": ready_source,
            "audio": audio,
            "metadata": metadata,
            "staged": specification["staged"],
        },
    }
    s3_client().put_object(
        Bucket=ready_source["bucket"],
        Key=f"{prefix}/pipeline/{PIPELINE_VERSION}/status.json",
        Body=json.dumps(payload, indent=2).encode(),
        ContentType="application/json",
        ExpectedBucketOwner=expected_owner(),
        ServerSideEncryption="AES256",
        Metadata={"pipeline-version": PIPELINE_VERSION},
    )


def request_for(specification: dict, prefix: str, name: str) -> dict:
    staged = specification["staged"]
    return {
        "TranscriptionJobName": name,
        "LanguageCode": specification["language_code"],
        "MediaFormat": specification["media_format"],
        "Media": {"MediaFileUri": f"s3://{staged['bucket']}/{staged['key']}"},
        "OutputBucketName": staged["bucket"],
        "OutputKey": (
            f"{prefix}/asr/{PIPELINE_VERSION}/jobs/{name}/transcribe.json"
        ),
        "Settings": {
            "ChannelIdentification": specification["channel_identification"]
        },
        "JobExecutionSettings": {
            "AllowDeferredExecution": specification["allow_deferred_execution"],
            "DataAccessRoleArn": os.environ["TRANSCRIBE_DATA_ROLE_ARN"],
        },
        "Tags": [{"Key": "PipelineVersion", "Value": PIPELINE_VERSION}],
    }


def validate_existing_job(existing: dict, request: dict) -> None:
    expected = {
        "MediaFileUri": request["Media"]["MediaFileUri"],
        "LanguageCode": request["LanguageCode"],
        "MediaFormat": request["MediaFormat"],
        "ChannelIdentification": request["Settings"]["ChannelIdentification"],
    }
    actual = {
        "MediaFileUri": existing.get("Media", {}).get("MediaFileUri"),
        "LanguageCode": existing.get("LanguageCode"),
        "MediaFormat": existing.get("MediaFormat"),
        "ChannelIdentification": existing.get("Settings", {}).get(
            "ChannelIdentification"
        ),
    }
    if actual != expected:
        raise RuntimeError("Transcribe job-name collision")


def enqueue_terminal_job(name: str, status: str) -> None:
    sqs_client().send_message(
        QueueUrl=os.environ["COMPLETION_QUEUE_URL"],
        MessageBody=json.dumps(
            {
                "source": "aws.transcribe",
                "detail-type": "Transcribe Job State Change",
                "detail": {
                    "TranscriptionJobName": name,
                    "TranscriptionJobStatus": status,
                },
            }
        ),
    )


def start_one(source: dict) -> bool:
    bucket = source.get("bucket")
    key = source.get("key") or ""
    match = READY_PATTERN.fullmatch(key)
    if not bucket or not match:
        return False
    if bucket != os.environ["AUDIO_BUCKET"]:
        raise ValueError("unexpected source bucket")

    ready_source = verified_event_object(source)
    prefix = match.group("prefix")
    if object_exists(
        bucket, f"{prefix}/pipeline/{PIPELINE_VERSION}/terminal.json"
    ):
        return True
    _payload, audio, metadata = ready_contract(ready_source, prefix)
    staged = stage_audio(audio, prefix)
    specification = transcription_spec(staged, ready_source, audio, metadata)
    name = job_name(specification)
    request = request_for(specification, prefix, name)

    existing_status = None
    try:
        transcribe_client().start_transcription_job(**request)
    except ClientError as exc:
        if exc.response.get("Error", {}).get("Code") != "ConflictException":
            raise
        existing = transcribe_client().get_transcription_job(
            TranscriptionJobName=name
        )["TranscriptionJob"]
        validate_existing_job(existing, request)
        existing_status = existing.get("TranscriptionJobStatus")

    put_submitted_status(
        ready_source, prefix, name, specification, audio, metadata
    )
    if existing_status in TERMINAL_STATES:
        enqueue_terminal_job(name, existing_status)
    return True


def handler(event, _context):
    failures = []
    for record in event.get("Records", []):
        message_id = record.get("messageId", "")
        try:
            body = json.loads(record.get("body", "{}"))
            for source in source_objects(body):
                start_one(source)
        except Exception as exc:
            LOGGER.error("starter failed: %s", type(exc).__name__)
            failures.append({"itemIdentifier": message_id})
    return {"batchItemFailures": failures}
