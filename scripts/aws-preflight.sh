#!/usr/bin/env bash
set -euo pipefail

profile="${AWS_PROFILE:-}"
region="${AWS_REGION:-us-west-1}"
bucket="${CALLNOTES_BUCKET:-}"
aws_args=(--region "$region")
if [[ -n "$profile" ]]; then
  aws_args+=(--profile "$profile")
fi

arn="$(aws "${aws_args[@]}" sts get-caller-identity --query Arn --output text)"
account_id="$(aws "${aws_args[@]}" sts get-caller-identity --query Account --output text)"
if [[ "$arn" == *":root" ]]; then
  echo "Refusing AWS account-root credentials." >&2
  exit 1
fi
echo "AWS identity: non-root"

if [[ -z "$bucket" ]]; then
  echo "CALLNOTES_BUCKET is unset; identity check only."
  exit 0
fi

aws "${aws_args[@]}" s3api head-bucket \
  --bucket "$bucket" --expected-bucket-owner "$account_id" >/dev/null

bucket_region="$(
  aws "${aws_args[@]}" s3api get-bucket-location \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query LocationConstraint --output text
)"
[[ "$bucket_region" == "None" ]] && bucket_region="us-east-1"
[[ "$bucket_region" == "EU" ]] && bucket_region="eu-west-1"
if [[ "$bucket_region" != "$region" ]]; then
  echo "Configured Region does not match the bucket Region." >&2
  exit 1
fi

versioning="$(
  aws "${aws_args[@]}" s3api get-bucket-versioning \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query Status --output text
)"
if [[ "$versioning" != "Enabled" ]]; then
  echo "Bucket versioning is not enabled." >&2
  exit 1
fi

public="$(
  aws "${aws_args[@]}" s3api get-public-access-block \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query 'PublicAccessBlockConfiguration.[BlockPublicAcls,IgnorePublicAcls,BlockPublicPolicy,RestrictPublicBuckets]' \
    --output text
)"
if [[ "$public" != $'True\tTrue\tTrue\tTrue' ]]; then
  echo "Bucket-level Block Public Access is incomplete." >&2
  exit 1
fi

encryption="$(
  aws "${aws_args[@]}" s3api get-bucket-encryption \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query 'ServerSideEncryptionConfiguration.Rules[0].ApplyServerSideEncryptionByDefault.SSEAlgorithm' \
    --output text
)"
if [[ "$encryption" != "AES256" ]]; then
  echo "This release requires bucket default SSE-S3 (AES256)." >&2
  exit 1
fi

ownership="$(
  aws "${aws_args[@]}" s3api get-bucket-ownership-controls \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query 'OwnershipControls.Rules[0].ObjectOwnership' \
    --output text
)"
if [[ "$ownership" != "BucketOwnerEnforced" ]]; then
  echo "Bucket ownership is not BucketOwnerEnforced." >&2
  exit 1
fi

managed_by="$(
  aws "${aws_args[@]}" s3api get-bucket-tagging \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query "TagSet[?Key=='ManagedBy'].Value | [0]" --output text
)"
if [[ "$managed_by" != "pixel-call-notes-root-sync" ]]; then
  echo "Bucket lacks the required managed-project identity tag." >&2
  exit 1
fi

is_public="$(
  aws "${aws_args[@]}" s3api get-bucket-policy-status \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query PolicyStatus.IsPublic --output text
)"
if [[ "$is_public" != "False" ]]; then
  echo "Bucket policy is public." >&2
  exit 1
fi

bucket_policy="$(
  aws "${aws_args[@]}" s3api get-bucket-policy \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query Policy --output text
)"
if ! ACCOUNT_ID="$account_id" BUCKET_POLICY="$bucket_policy" python3 -c '
import json
import os

account = os.environ["ACCOUNT_ID"]
policy = json.loads(os.environ["BUCKET_POLICY"])
for statement in policy.get("Statement", []):
    if statement.get("Effect") != "Allow":
        continue
    principal = statement.get("Principal")
    if not isinstance(principal, dict) or set(principal) - {"AWS"}:
        raise SystemExit(1)
    values = principal.get("AWS", [])
    values = [values] if isinstance(values, str) else values
    for value in values:
        parts = value.split(":")
        principal_account = parts[4] if len(parts) > 4 and parts[4] else value
        if principal_account != account:
            raise SystemExit(1)
'; then
  echo "Bucket policy grants wildcard, service, or cross-account access." >&2
  exit 1
fi

eventbridge="$(
  aws "${aws_args[@]}" s3api get-bucket-notification-configuration \
    --bucket "$bucket" --expected-bucket-owner "$account_id" \
    --query EventBridgeConfiguration.EventBridgeEnabled --output text
)"
if [[ "$eventbridge" != "True" ]]; then
  echo "Bucket EventBridge delivery is not enabled." >&2
  exit 1
fi

logging_config="$(
  aws "${aws_args[@]}" bedrock get-model-invocation-logging-configuration \
    --query loggingConfig --output json
)"
if ! LOGGING_CONFIG="$logging_config" python3 -c '
import json
import os

config = json.loads(os.environ["LOGGING_CONFIG"] or "{}") or {}
enabled = any(
    config.get(key) is True
    for key in (
        "textDataDeliveryEnabled",
        "imageDataDeliveryEnabled",
        "embeddingDataDeliveryEnabled",
        "videoDataDeliveryEnabled",
    )
)
raise SystemExit(1 if enabled else 0)
'; then
  echo "Bedrock model invocation content logging is enabled." >&2
  exit 1
fi

echo "AWS preflight: owned, region-matched, private, event-enabled, logging-safe"
