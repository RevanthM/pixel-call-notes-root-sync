# Agent integration

Agents should consume stable artifacts, not inject into Android's root/hooking stack.

## Recommended trigger

Subscribe an agent to the creation of:

```text
call-notes/dt=YYYY-MM-DD/<opaque-record-id>/pipeline/v1/terminal.json
```

Fetch the exact event VersionId, verify its S3 SHA-256 checksum, and proceed only when
`outcome == "COMPLETED"`. Treat every notification as duplicate and unordered.

## Read contract

Read only the exact `key`, `version_id`, and `sha256` descriptors named by
`terminal.json`. Never substitute a current unversioned object.

Validate before acting:

- `pipeline_version` is supported.
- Every source and artifact VersionId and SHA-256 agrees with S3.
- The protected ready, audio, metadata, and staged source versions remain current.
- The summary's `prompt_version` and `model_id` are allowed.
- Contact details are present only when the owner opted in.
- Exact call and device metadata may be absent by design; do not infer it.

## Safe agent behaviors

- Prepare a private searchable index.
- Extract decisions, action items, dates, and follow-ups.
- Draft reminders, CRM notes, or emails for human approval.
- Link derived records back to the immutable source version.

## Behaviors requiring separate approval

- Sending messages or emails.
- Creating calendar events or CRM records.
- Sharing call content with another service or person.
- Trading, purchasing, filing, or any other consequential action.
- Deleting or changing retention on source artifacts.

## Data handling

- Keep the bucket private and use per-prefix least-privilege access.
- Never put full transcripts or summaries in logs, traces, issue reports, or prompts
  sent to unrelated coding agents.
- Preserve channel labels when identity is uncertain.
- Do not infer a speaker from call direction, contact name, voice characteristics, or
  a single uncalibrated example.
- Carry `uncertainties` forward rather than converting them into facts.

## Idempotency key

Use:

```text
SHA256(canonical terminal JSON bytes + terminal VersionId + agentVersion)
```

Store the key with every side effect. A retry must return the existing result rather
than create a second calendar event, CRM record, or notification.
