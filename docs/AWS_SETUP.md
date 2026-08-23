# AWS setup

AWS is optional. Pixel Call Notes already transcribes and summarizes on-device.
This pipeline creates a private off-device copy and a second, structured summary for
agents and automations.

## Security gates

Do not deploy until:

- `aws sts get-caller-identity` returns an IAM/Identity Center role, not `:root`.
- The deployment account has MFA-backed administration.
- CloudTrail and a small Budget alert are enabled.
- You selected a retention period for audio, transcripts, summaries, and old versions.
- S3 Block Public Access will remain enabled.
- Bedrock invocation content logging is disabled.
- You accept Transcribe and Bedrock processing in the configured Regions.
- Every recorded participant has legally sufficient notice/consent.

## 1. Create a private versioned bucket

Choose a globally unique name and a non-root profile:

```bash
export AWS_PROFILE=callnotes-admin
export AWS_REGION=us-west-1
export CALLNOTES_BUCKET=replace-with-a-unique-name

aws sts get-caller-identity
```

Stop if the returned ARN ends in `:root`.

Deploy the retained bucket stack:

```bash
aws cloudformation deploy \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --stack-name callnotes-storage \
  --template-file aws/bucket-template.yaml \
  --parameter-overrides BucketName="$CALLNOTES_BUCKET"

scripts/aws-preflight.sh
```

The bucket is versioned, encrypted with SSE-S3, owned by the bucket owner, blocks all
public access, and publishes events to EventBridge. CloudFormation retains it if the
stack is deleted.

This release supports only a dedicated bucket created from the supplied template.
The host verifies the same-account owner, Region, project identity tag, policy,
versioning, SSE-S3 encryption, ownership controls, and Block Public Access before it
uploads call content.

## 2. Deploy the processing stack

Install Docker and the AWS SAM CLI, then:

```bash
sam validate --lint --template-file aws/template.yaml
sam build --use-container --template-file aws/template.yaml
sam deploy \
  --resolve-s3 \
  --profile "$AWS_PROFILE" \
  --region "$AWS_REGION" \
  --stack-name callnotes-processing \
  --capabilities CAPABILITY_IAM \
  --parameter-overrides \
    AudioBucketName="$CALLNOTES_BUCKET"
```

To receive DLQ alarm notifications, also pass an SNS topic ARN with
`AlarmTopicArn=arn:...`. The pipeline intentionally preserves `ch_0` and `ch_1`
instead of applying a global speaker identity that could silently misattribute calls.

The default model is the US geographic inference profile
`us.amazon.nova-lite-v1:0`. Requests originating in `us-west-1` may route to approved
US destination Regions. Review `aws/template.yaml` before selecting another model.

## 3. Configure the host sync

```bash
python3 sync/sync.py --dry-run
```

Exact call metadata, contacts, and device/build facts are all redacted by default.
Opt in only to fields your downstream agent actually needs:

```bash
python3 sync/sync.py --include-call-metadata
python3 sync/sync.py --include-contact-details
python3 sync/sync.py --include-device-details
```

Use `--device-id` for a non-identifying stable label:

```bash
python3 sync/sync.py --device-id primary
```

Drop `--dry-run` only after reviewing the output and IAM permissions.
Choose the destination, `--device-id`, and privacy flags before the first upload.
Sync state is bound to that exact scope and fails closed if it changes.

## 4. Backfill and verify

Enabling EventBridge does not replay older objects. `sync.py --backfill` commits a
new immutable `ready.json` only for records whose source objects match their recorded
versions and hashes.

For a new audio object, verify:

```text
ready.json
pipeline/v1/input/<audio-sha256>.m4a
asr/v1/jobs/<job-name>/transcribe.json
asr/v1/jobs/<job-name>/transcript.json
asr/v1/jobs/<job-name>/transcript.txt
llm/v1/jobs/<job-name>/summary.json
llm/v1/jobs/<job-name>/summary.md
pipeline/v1/status.json
pipeline/v1/terminal.json
```

Treat `terminal.json`, not mutable `status.json`, as the authoritative completion
boundary. It is written last and references exact versions and SHA-256 digests.
Check both processing DLQs and the EventBridge delivery DLQ.

## 5. Production hardening

- This release deliberately supports only SSE-S3. Do not switch the bucket to KMS
  without first propagating one CMK through every writer and Transcribe output.
- Add explicit current and noncurrent retention after deciding your deletion policy.
- Add CloudTrail S3 data events for this bucket.
- Add a periodic reconciler for missed/stale events.
- Treat mutable `status.json` as best-effort observability; the immutable terminal
  manifest is the monotonic state boundary.
- Keep transcripts, prompts, names, numbers, and summaries out of application logs.
- Follow [Version-aware erasure](ERASURE.md) to delete retained object versions.
