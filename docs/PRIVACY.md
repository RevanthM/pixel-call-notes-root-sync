# Privacy

Phone calls can contain biometric voice data, financial information, health details,
credentials, addresses, relationships, and statements from people who never agreed to
cloud processing. Treat every artifact as highly sensitive.

## Local data

Google Phone stores Call Notes under its private Fermat directory. Root bypasses the
normal Android sandbox, so any root process can read those files. This toolkit streams
audio directly to a private host temporary directory and never stages it in shared
phone storage. The temporary file is plaintext while it is validated and uploaded.
Use a host with full-disk encryption; abrupt host or process termination can leave
recoverable temporary data until the operating system cleans it up.

## Metadata defaults

The sync excludes exact call metadata, contacts, and device/build facts unless their
separate `--include-*` flags are passed. Device serials are never uploaded; use a
non-identifying `--device-id` such as `primary`. Canonical S3 record IDs are
pseudonymous, though the UTC date partition remains sensitive.

Even redacted metadata can identify a person through call time, duration, direction,
location, voice, or content. Redaction is risk reduction, not anonymization.

## Cloud processing

When enabled:

- Amazon Transcribe receives the audio.
- Amazon Bedrock receives the rendered transcript and selected metadata.
- S3 stores source and derived artifacts.

The host commits `ready.json` only after immutable audio and metadata writes. The
pipeline transcribes an exact source version copied to a protected content-addressed
key, then writes `terminal.json` last with exact artifact versions and digests.

Review current AWS terms, retention, data residency, abuse monitoring, and model
logging before deployment. Keep Bedrock invocation logging disabled.
See [Version-aware erasure](ERASURE.md) before relying on deletion.

## Git and support

Never commit or attach:

- Audio, transcripts, summaries, Fermat databases, protobufs, screenshots, or logs.
- Contact names, numbers, emails, device serials, call timestamps, UUIDs, bucket
  names, account IDs, or local paths.
- Factory images, patched boot images, APKs, module archives, or model files.
- Cloud credentials, tokens, keystores, recovery codes, or access-key IDs.

Create synthetic fixtures when reporting a bug.
