# AWS implementation

This directory contains an AWS SAM implementation for:

```text
immutable S3 audio + metadata + ready commit
  → EventBridge
  → encrypted SQS + DLQ
  → exact-version content-addressed staging
  → Transcribe channel identification
  → encrypted completion SQS + DLQ
  → Bedrock structured summary
  → immutable S3 artifacts + terminal manifest
```

It is infrastructure source, not evidence that anything is deployed. Follow
`docs/AWS_SETUP.md`; never deploy with AWS account-root credentials.

## Templates

- `bucket-template.yaml` creates and retains a versioned, SSE-S3 encrypted bucket,
  blocks all public access, enforces TLS, and enables EventBridge.
- `template.yaml` creates the pipeline, least-privilege runtime roles, 30-day Lambda
  log retention, DLQs, and DLQ alarms.

## Security properties

- Only an immutable canonical `ready.json` commit triggers work.
- Ready, audio, and metadata carry exact VersionIds plus S3-validated SHA-256 checksums.
- The exact audio version is copied to a protected content-addressed key before
  deferred Transcribe execution.
- Job names hash the full source contract and transcription settings; tags contain
  only the pipeline version.
- `terminal.json` is written last and references exact artifact versions and hashes.
  Mutable status is never a trusted completion boundary.
- Speaker identity always remains raw `ch_0`/`ch_1` in this release.
- The summary prompt treats call content as untrusted and forbids invented facts.
- Malformed model output produces a failed terminal record, not a completed summary.
- Runtime IAM is constrained to Call Notes prefixes and one Bedrock profile.
- Call content is not written to Lambda logs by application code.

The bucket policy requires `If-None-Match: *` for canonical, staged, derived, and
terminal writes and blocks their deletion by default.

## Validate

```bash
python3 -m unittest discover -s aws/tests -p 'test_*.py' -v
python3 -m py_compile aws/functions/*/app.py
cfn-lint aws/template.yaml aws/bucket-template.yaml
sam validate --lint --template-file aws/template.yaml
sam build --use-container --template-file aws/template.yaml
```

## Data residency

The default `us.amazon.nova-lite-v1:0` geographic profile may route inference across
approved US Regions. It does not promise that processing stays in the stack's Region.
Review current AWS documentation and your legal requirements before deployment.
