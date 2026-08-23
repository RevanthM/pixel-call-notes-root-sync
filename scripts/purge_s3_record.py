#!/usr/bin/env python3
"""List or permanently purge every S3 version for one opaque Call Notes record."""

from __future__ import annotations

import argparse
import re
from collections.abc import Iterable

import boto3

PREFIX_PATTERN = re.compile(
    r"^call-notes/dt=\d{4}-\d{2}-\d{2}/[0-9a-f]{24}/$"
)


def chunks(items: list[dict], size: int = 1000) -> Iterable[list[dict]]:
    for index in range(0, len(items), size):
        yield items[index : index + size]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bucket", required=True)
    parser.add_argument("--prefix", required=True)
    parser.add_argument("--region", required=True)
    parser.add_argument("--profile")
    parser.add_argument("--execute", action="store_true")
    parser.add_argument("--confirm-bucket")
    parser.add_argument("--confirm-prefix")
    args = parser.parse_args()

    prefix = args.prefix if args.prefix.endswith("/") else args.prefix + "/"
    if not PREFIX_PATTERN.fullmatch(prefix):
        raise SystemExit("Prefix must identify exactly one opaque Call Notes record.")

    session = boto3.Session(profile_name=args.profile, region_name=args.region)
    caller = session.client("sts").get_caller_identity()
    if caller.get("Arn", "").endswith(":root"):
        raise SystemExit("Refusing AWS account-root credentials.")
    account_id = caller["Account"]
    s3 = session.client("s3")
    common = {"Bucket": args.bucket, "ExpectedBucketOwner": account_id}
    s3.head_bucket(**common)
    tags = {
        item["Key"]: item["Value"]
        for item in s3.get_bucket_tagging(**common).get("TagSet", [])
    }
    if tags.get("ManagedBy") != "pixel-call-notes-root-sync":
        raise SystemExit("Refusing a bucket without the managed-project tag.")

    objects = []
    paginator = s3.get_paginator("list_object_versions")
    for page in paginator.paginate(Prefix=prefix, **common):
        for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
            objects.append({"Key": item["Key"], "VersionId": item["VersionId"]})

    if not args.execute:
        print(f"dry run: {len(objects)} object version(s) would be permanently deleted")
        return 0
    if args.confirm_bucket != args.bucket or args.confirm_prefix != prefix:
        raise SystemExit(
            "Execution requires exact --confirm-bucket and --confirm-prefix values."
        )
    for batch in chunks(objects):
        response = s3.delete_objects(
            **common,
            Delete={"Objects": batch, "Quiet": True},
        )
        if response.get("Errors"):
            raise RuntimeError("S3 reported one or more version-deletion failures.")
    print(f"deleted {len(objects)} object version(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
