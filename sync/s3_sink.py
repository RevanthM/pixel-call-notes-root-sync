"""Fail-closed, idempotent S3 writer for private Call Notes records."""

from __future__ import annotations

import base64
import hashlib
import json
import time
from dataclasses import dataclass
from pathlib import Path

import boto3
from botocore.exceptions import ClientError


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass
class UploadResult:
    key: str
    uploaded: bool
    sha256: str
    bytes: int
    version_id: str | None = None


class S3Sink:
    def __init__(
        self,
        bucket: str,
        *,
        region: str,
        profile: str | None = None,
        dry_run: bool = False,
    ):
        self.bucket = bucket
        self.region = region
        self.dry_run = dry_run
        self.account_id: str | None = None
        self._client = None
        if not dry_run:
            session = boto3.Session(profile_name=profile, region_name=region)
            caller = session.client("sts").get_caller_identity()
            if caller.get("Arn", "").endswith(":root"):
                raise RuntimeError("refusing to use AWS account-root credentials")
            self.account_id = caller["Account"]
            self._client = session.client("s3")
            self._verify_bucket_safety()

    def _verify_bucket_safety(self) -> None:
        expected = self.account_id
        common = {"Bucket": self.bucket, "ExpectedBucketOwner": expected}
        self._client.head_bucket(**common)

        location = self._client.get_bucket_location(**common).get(
            "LocationConstraint"
        )
        location = "us-east-1" if location in (None, "") else location
        location = "eu-west-1" if location == "EU" else location
        if location != self.region:
            raise RuntimeError("configured AWS Region does not own the selected bucket")

        if self._client.get_bucket_versioning(**common).get("Status") != "Enabled":
            raise RuntimeError("refusing an S3 bucket without enabled versioning")

        public = self._client.get_public_access_block(**common).get(
            "PublicAccessBlockConfiguration", {}
        )
        required = {
            "BlockPublicAcls",
            "IgnorePublicAcls",
            "BlockPublicPolicy",
            "RestrictPublicBuckets",
        }
        if not all(public.get(key) is True for key in required):
            raise RuntimeError("refusing an S3 bucket without full Block Public Access")

        rules = self._client.get_bucket_encryption(**common).get(
            "ServerSideEncryptionConfiguration", {}
        ).get("Rules", [])
        if not any(
            rule.get("ApplyServerSideEncryptionByDefault", {}).get("SSEAlgorithm")
            == "AES256"
            for rule in rules
        ):
            raise RuntimeError("this release requires default SSE-S3 encryption")

        ownership = self._client.get_bucket_ownership_controls(**common).get(
            "OwnershipControls", {}
        ).get("Rules", [])
        if not any(
            rule.get("ObjectOwnership") == "BucketOwnerEnforced" for rule in ownership
        ):
            raise RuntimeError("refusing a bucket without BucketOwnerEnforced ownership")

        tags = {
            item["Key"]: item["Value"]
            for item in self._client.get_bucket_tagging(**common).get("TagSet", [])
        }
        if tags.get("ManagedBy") != "pixel-call-notes-root-sync":
            raise RuntimeError("refusing a bucket without the managed-project tag")

        if self._client.get_bucket_policy_status(**common).get(
            "PolicyStatus", {}
        ).get("IsPublic"):
            raise RuntimeError("refusing a bucket with a public policy")

        policy = json.loads(self._client.get_bucket_policy(**common)["Policy"])
        for statement in policy.get("Statement", []):
            if statement.get("Effect") != "Allow":
                continue
            principal = statement.get("Principal", {})
            if principal == "*":
                raise RuntimeError("refusing a bucket policy with wildcard access")
            if not isinstance(principal, dict) or set(principal) - {"AWS"}:
                raise RuntimeError("refusing a bucket policy with external principals")
            aws_principals = principal.get("AWS", [])
            if isinstance(aws_principals, str):
                aws_principals = [aws_principals]
            for value in aws_principals:
                parts = value.split(":")
                principal_account = (
                    parts[4] if len(parts) > 4 and parts[4] else value
                )
                if principal_account != expected:
                    raise RuntimeError("refusing a bucket policy with cross-account access")

    def _existing_object(self, key: str) -> tuple[str, str | None] | None:
        try:
            head = self._client.head_object(
                Bucket=self.bucket,
                Key=key,
                ExpectedBucketOwner=self.account_id,
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
        # Empty means the object exists without provenance and must not be replaced.
        digest = head.get("Metadata", {}).get("sha256", "")
        expected_checksum = (
            base64.b64encode(bytes.fromhex(digest)).decode()
            if len(digest) == 64
            else ""
        )
        if head.get("ChecksumSHA256") != expected_checksum:
            digest = ""
        return (digest, head.get("VersionId"))

    def _existing_digest(self, key: str) -> str | None:
        existing = self._existing_object(key)
        return None if existing is None else existing[0]

    def put_file(self, local: Path, key: str, *, immutable: bool) -> UploadResult:
        digest = sha256_file(local)
        size = local.stat().st_size
        if self.dry_run:
            return UploadResult(key, False, digest, size)
        existing = self._existing_object(key)
        if existing and existing[0] == digest:
            return UploadResult(key, False, digest, size, existing[1])
        if immutable and existing is not None:
            raise RuntimeError("refusing to replace immutable S3 audio object")
        for attempt in range(3):
            try:
                with local.open("rb") as body:
                    request = {
                        "Bucket": self.bucket,
                        "Key": key,
                        "Body": body,
                        "ContentLength": size,
                        "ExpectedBucketOwner": self.account_id,
                        "ServerSideEncryption": "AES256",
                        "ContentType": "audio/mp4",
                        "Metadata": {"sha256": digest},
                        "ChecksumSHA256": base64.b64encode(
                            bytes.fromhex(digest)
                        ).decode(),
                    }
                    if immutable:
                        request["IfNoneMatch"] = "*"
                    response = self._client.put_object(
                        **request
                    )
                return UploadResult(
                    key, True, digest, size, response.get("VersionId")
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if code not in {
                    "409",
                    "412",
                    "ConditionalRequestConflict",
                    "PreconditionFailed",
                }:
                    raise
                concurrent = self._existing_object(key)
                if concurrent and concurrent[0] == digest:
                    return UploadResult(key, False, digest, size, concurrent[1])
                if code in {"409", "ConditionalRequestConflict"} and attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise RuntimeError("concurrent immutable S3 write conflict") from exc
        raise RuntimeError("conditional S3 upload retries exhausted")

    def put_json(
        self, payload: dict, key: str, *, immutable: bool = False
    ) -> UploadResult:
        data = json.dumps(payload, indent=2, ensure_ascii=False).encode()
        digest = hashlib.sha256(data).hexdigest()
        if self.dry_run:
            return UploadResult(key, False, digest, len(data))
        existing = self._existing_object(key)
        if existing and existing[0] == digest:
            return UploadResult(key, False, digest, len(data), existing[1])
        if immutable and existing is not None:
            raise RuntimeError("refusing to replace immutable S3 JSON object")
        request = {
            "Bucket": self.bucket,
            "Key": key,
            "Body": data,
            "ExpectedBucketOwner": self.account_id,
            "ContentType": "application/json",
            "ServerSideEncryption": "AES256",
            "Metadata": {"sha256": digest},
            "ChecksumSHA256": base64.b64encode(bytes.fromhex(digest)).decode(),
        }
        if immutable:
            request["IfNoneMatch"] = "*"
        for attempt in range(3):
            try:
                response = self._client.put_object(**request)
                return UploadResult(
                    key, True, digest, len(data), response.get("VersionId")
                )
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code")
                if not immutable or code not in {
                    "409",
                    "412",
                    "ConditionalRequestConflict",
                    "PreconditionFailed",
                }:
                    raise
                concurrent = self._existing_object(key)
                if concurrent and concurrent[0] == digest:
                    return UploadResult(
                        key, False, digest, len(data), concurrent[1]
                    )
                if code in {"409", "ConditionalRequestConflict"} and attempt < 2:
                    time.sleep(0.2 * (attempt + 1))
                    continue
                raise RuntimeError("concurrent immutable S3 JSON conflict") from exc
        raise RuntimeError("conditional S3 JSON retries exhausted")
