# Copilot instructions

Follow `AGENTS.md`.

This repository interacts with bootloader unlocking, root, private phone-call data,
and AWS. Prefer read-only diagnostics. Do not generate commands that unlock, wipe,
flash, relock, install modules, upload call content, or deploy cloud resources unless
the user has explicitly approved that exact operation.

Never hard-code a person, phone number, email, device serial, local username/path,
AWS account, bucket, credential, call timestamp, or real transcript. Use placeholders
and keep contact upload opt-in.

Preserve the existing fail-closed behavior for S3 authorization, immutable audio,
source VersionId checks, and least-privilege IAM.
