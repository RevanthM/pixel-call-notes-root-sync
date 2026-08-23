# Rooted Call Notes → S3 sync

This host-side CLI streams only Google Phone's private Call Notes `.m4a` files. It
does not tap, swipe, launch an app, use the share sheet, copy files into shared phone
storage, or scan unrelated recordings.

## Privacy behavior

- Exact call times, direction, duration, contact details, and device/build facts are
  omitted by default.
- The public call-log provider is queried only when call or contact metadata is
  explicitly requested.
- The adb serial is never uploaded.
- `--device-id` is a user-selected non-identifying label (`primary` by default).
- S3 record IDs are deterministic pseudonyms; they do not repeat the call epoch.
- `--include-call-metadata`, `--include-contact-details`, and
  `--include-device-details` are separate explicit opt-ins.
- Audio is immutable: a different file cannot replace an existing canonical key.
- Metadata and the final `ready.json` commit are also immutable and version-addressed.
- New audio must be at least 120 seconds old and retain the same size/mtime across the
  transfer, preventing a still-growing recording from becoming the immutable source.
- A positive-length stereo AAC stream must pass `ffprobe` before any S3 write.
- Sync state is scoped to the AWS account, bucket, Region, device label, and privacy
  flags, plus a one-way hash of the attached ADB device serial that is never uploaded.
  A scope change requires a new state path and an explicit backfill.
- S3 403 errors fail closed.
- AWS account-root credentials are rejected.

## Run

```bash
pip install -r requirements.txt
ffprobe -version
python3 sync/sync.py --dry-run
python3 sync/sync.py --profile callnotes-sync --region us-west-1
```

Set the bucket with `--bucket` or `CALLNOTES_BUCKET`; set the Region with `--region`
or `AWS_REGION`:

```bash
export CALLNOTES_BUCKET=replace-with-private-bucket
export AWS_REGION=us-west-1
```

The IAM principal needs:

- `sts:GetCallerIdentity`
- `s3:GetBucketLocation`, `s3:GetBucketVersioning`,
  `s3:GetBucketPublicAccessBlock`, `s3:GetEncryptionConfiguration`,
  `s3:GetBucketOwnershipControls`, `s3:GetBucketTagging`,
  `s3:GetBucketPolicy`, and `s3:GetBucketPolicyStatus`
- `s3:ListBucket` for same-owner `HeadBucket`
- `s3:GetObject` only for existing `*/audio.m4a`,
  `*/source/metadata.json`, `*/ready.json`, and the device manifest
- `s3:PutObject` only for `*/audio.m4a`, `*/source/metadata.json`,
  `*/ready.json`, and `_manifests/device=*/latest.json`

Do not grant the host write access to ASR, LLM, status, terminal, or staging keys.
The dedicated bucket must pass every fail-closed check. See `docs/AWS_SETUP.md`.

## Object contract

```text
call-notes/dt=YYYY-MM-DD/<opaque-record-id>/
  audio.m4a
  source/metadata.json
  ready.json
_manifests/device=<device-id>/latest.json
```

The date is UTC and remains sensitive coarse metadata. Dialer's private filename uses
the call epoch internally for an exact local join, but the S3 record ID is a truncated
SHA-256 pseudonym derived from the non-identifying device label and epoch. It prevents
casual disclosure in logs; it is not cryptographic anonymization.

The owner-only local ledger defaults to
`$XDG_STATE_HOME/pixel-call-notes-root-sync/state.json` (or
`~/.local/state/pixel-call-notes-root-sync/state.json`). `--backfill` reconsiders
every source record; digest checks keep identical S3 objects idempotent. Advanced
users can adjust the settling window with `--minimum-age-seconds`.

The host uses an owner-private plaintext temporary file for validation and upload.
Use full-disk encryption; see `docs/PRIVACY.md`.
