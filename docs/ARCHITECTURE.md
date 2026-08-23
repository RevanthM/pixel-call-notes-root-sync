# Architecture

## Android restoration path

```text
stock Pixel boot
  └─ Magisk: systemless root and module lifecycle
      └─ NeoZygisk: injects the Zygisk API into Zygote
          └─ Vector: Xposed-compatible ART hook framework
              └─ PCS: redirects protected model-manifest requests
                  └─ Google AICore: downloads and runs Gemini Nano
                      └─ Google Phone: writes Fermat audio/transcript/summary
```

PCS does not summarize calls. It supplies mirrored manifests to Google's protected
download client. AICore downloads model files from Google's CDN and runs Gemini Nano
on-device. Google Phone records the summarization task internally as
`Duplex / postCall`; SODA handles the transcript separately.

## Export path

`sync/sync.py` performs read-only device queries:

- Root-only Call Notes audio from
  `/data/user/0/com.google.android.dialer/files/fermat_files/`.
- Public call metadata from `content://call_log/calls` only when call/contact metadata
  is explicitly requested.

The private filename is the call's epoch-millisecond start time, which makes the local
call/audio join deterministic. The S3 record ID is instead an opaque deterministic
pseudonym. Root-only files stream through `adb exec-out`; they are never copied to
shared storage on the phone. The host validates a private temporary copy with
`ffprobe` before upload.

```text
call-notes/dt=YYYY-MM-DD/<opaque-record-id>/
  audio.m4a
  source/metadata.json
  ready.json
```

Audio and metadata are immutable. Metadata is redacted by default. `ready.json` is
written last and names their exact S3 versions and S3-validated SHA-256 checksums.

## AWS derivation path

```text
S3 ready.json Object Created
  → EventBridge
  → encrypted starter SQS + DLQ
  → Lambda
  → exact audio VersionId copied to protected content-addressed staging
  → Amazon Transcribe with ChannelIdentification
  → EventBridge completion
  → encrypted completion SQS + DLQ
  → Lambda
  → Bedrock Converse
  → immutable derived objects
  → terminal.json committed last
```

Derived keys:

```text
pipeline/v1/input/<audio-sha256>.m4a
asr/v1/jobs/<job-name>/transcribe.json
asr/v1/jobs/<job-name>/transcript.json
asr/v1/jobs/<job-name>/transcript.txt
llm/v1/jobs/<job-name>/summary.json
llm/v1/jobs/<job-name>/summary.md
pipeline/v1/status.json
pipeline/v1/terminal.json
```

The job name hashes the complete source contract and transcription settings. The
terminal manifest references exact source and output VersionIds and SHA-256 digests.
Mutable status is observability only. Raw `ch_0`/`ch_1` labels are preserved; this
release does not apply a global speaker identity.

## Agent boundary

An agent should consume S3/Fermat outputs. It should not hook Magisk, Zygisk, Vector,
PCS, AICore, or Google Phone. Those are implementation layers, not stable APIs.
