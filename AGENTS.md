# Agent instructions

These rules apply to Cursor, Claude Code, Codex, Copilot, and other coding agents.
Read `SAFETY.md` before any device or cloud operation.

## Safety boundaries

- Never run `fastboot flashing unlock`, flash a partition, erase data, relock a
  bootloader, or install a root module without the user explicitly approving that
  exact action.
- Never automate confirmation on the phone for an irreversible bootloader action.
- Before any flash, verify device codename, current build, active slot, image hash,
  and the presence of a matching stock recovery image.
- Do not download or redistribute APKs, boot images, factory images, model weights,
  or Magisk module ZIPs. Link to upstream releases and verify hashes.
- Never configure PCS from an arbitrary URL. Accept only an HTTPS URL derived from a
  reviewed 40-character commit in `PublicComputeServices/pcs-manifests`.
- Treat the PCS root-shell interpolation and unauthenticated ports 7270–7273 as known
  risks. Do not conceal or minimize them.

## Privacy and cloud rules

- Call audio, transcripts, summaries, contacts, phone numbers, device serials,
  databases, protobufs, screenshots, cloud account IDs, bucket names, and credentials
  are private data. Never add them to Git.
- Default to redacted metadata. Require explicit user intent before including contact
  details in S3.
- Never use AWS account-root credentials. Stop if `aws sts get-caller-identity`
  returns an ARN ending in `:root`.
- Never make an S3 bucket public or enable model invocation logging for call content.
- Do not deploy or mutate AWS resources unless the user explicitly requests it.

## Code changes

- Keep device reads non-mutating unless a script is specifically documented as a
  configuration step.
- Preserve source VersionId and SHA-256 provenance across the AWS pipeline.
- Run all unit-test suites, Python compilation, `cfn-lint`, `pip-audit`, Bandit,
  shell syntax checks, and `scripts/public_safety_check.py` before a commit.
- Update the setup and security docs whenever tested versions or upstream risks change.
