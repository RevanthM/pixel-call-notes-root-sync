# Version-aware erasure

Deleting only the current S3 key is not erasure. The private bucket is versioned,
retained when its stack is deleted, and protects source and terminal artifacts from
deletion by default.

For one record:

1. Stop the processing stack or disable its EventBridge rules.
2. Update the bucket stack with `ProtectSourceObjects=false`.
3. Identify the exact opaque record prefix. Do not use a date-only prefix.
4. Preview every version and delete marker:

   ```bash
   python scripts/purge_s3_record.py \
     --bucket "$CALLNOTES_BUCKET" \
     --region "$AWS_REGION" \
     --prefix 'call-notes/dt=YYYY-MM-DD/24_HEX_CHARACTERS/'
   ```

5. Re-run with `--execute`, plus exact `--confirm-bucket` and
   `--confirm-prefix` values.
6. Run the preview again; it must report zero versions.
7. Restore the bucket stack with `ProtectSourceObjects=true` before processing
   more calls.

Also review copies outside this bucket: local sync state, host backups, CloudTrail
data events, support bundles, and any downstream agent output. The AWS preflight
refuses Bedrock model-invocation content logging, but previously configured logs
must be handled under their own retention and deletion policies.

For full-bucket erasure, first stop processing, then enumerate and delete **all**
object versions and delete markers before deleting the retained bucket. Stack
deletion alone intentionally does not erase call content.
