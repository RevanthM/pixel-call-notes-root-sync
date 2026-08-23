# Pixel Call Notes Root Sync

An auditable, privacy-first toolkit for restoring Pixel Call Notes on a rooted
Pixel and exporting its on-device artifacts to an optional AWS transcription and
summarization pipeline.

> [!CAUTION]
> Unlocking a bootloader **factory-resets the phone** and weakens its security
> posture. Root can break Play Integrity, banking apps, Wallet, DRM, enterprise
> policy, and warranty/support expectations. Read [Safety](SAFETY.md),
> [Android setup](docs/ANDROID_SETUP.md), and [Security](SECURITY.md) completely
> before changing a device.

## What this repository does

```text
Magisk → NeoZygisk → Vector → Public Compute Services (PCS)
                                      ↓
                            Google AICore / Gemini Nano
                                      ↓
                         Pixel Call Notes in Google Phone
                                      ↓
                   rooted read-only sync → private S3 bucket
                                      ↓
                  Transcribe channels → Bedrock summary
```

Status: the on-device audio/transcript/summary path and redacted rooted sync dry run
were verified on the reference device. The AWS templates and functions pass local
tests and linting but have **not** been deployed end to end.

- Documents the exact stack tested on a Pixel 11 Pro Fold running Android 17.
- Verifies the rooted Android and PCS configuration without embedding device IDs.
- Reads Google Dialer's private Fermat audio **without modifying the phone**.
- Uploads immutable audio and privacy-configurable metadata to a private S3 bucket.
- Provides an undeployed AWS SAM pipeline for dual-channel transcription and
  structured summaries.
- Gives AI coding tools the same safety rules through `AGENTS.md`, `CLAUDE.md`,
  Cursor rules, and VS Code/Copilot instructions.

It does **not** contain patched boot images, APKs, Magisk modules, model files,
phone numbers, call audio, transcripts, cloud credentials, or device-specific
factory images. Download third-party software only from the linked upstream
projects and verify it yourself.

## Tested baseline

| Component | Tested version |
|---|---|
| Device | Pixel 11 Pro Fold (`yogi`) |
| Android | Android 17 / API 37 |
| Magisk | 30.7 |
| NeoZygisk | CI build `291-0699f64` |
| Vector | 2.2 canary 3107 |
| Public Compute Services | 1.0.6 |
| Google Phone | Pixel 2026 public beta family |

Other Pixel models or releases may use different partitions, labels, scopes, or
model manifests. Never flash an image from another device or build.

## Start here

1. Read [Android setup](docs/ANDROID_SETUP.md), especially the backup and recovery
   gates.
2. Run `scripts/check-prerequisites.sh`.
3. Download the matching Google factory image and upstream releases yourself.
4. Unlock, patch, and flash only after personally confirming the irreversible wipe.
5. Install NeoZygisk, Vector, and PCS one layer at a time, rebooting and verifying
   after each.
6. Configure PCS using a pinned manifest commit:

   ```bash
   export PCS_MANIFEST_COMMIT=f18a0d6e464febc2f21342f2e0b927fe758f1c7f
   scripts/configure-pcs.sh --yes
   ```

7. Make a consented test call longer than one minute and run:

   ```bash
   python3 scripts/verify_call_note.py
   ```

8. For private backups, follow [S3 sync](sync/README.md).
9. For cloud transcription and summaries, follow [AWS setup](docs/AWS_SETUP.md).
10. To connect an AI agent, use the versioned contract in
    [Agent integration](docs/AGENT_INTEGRATION.md).

## Upstream projects

| Layer | Purpose | License | Repository |
|---|---|---|---|
| Magisk | Systemless root and module manager | GPL-3.0 | [topjohnwu/Magisk](https://github.com/topjohnwu/Magisk) |
| NeoZygisk | Android Zygote injection / Zygisk API | GPL-3.0 | [JingMatrix/NeoZygisk](https://github.com/JingMatrix/NeoZygisk) |
| Vector | Modern Xposed-compatible ART hooking | GPL-3.0 | [JingMatrix/Vector](https://github.com/JingMatrix/Vector) |
| PCS | Alternative AICore manifest proxy | GPL-3.0 | [KieronQuinn/PublicComputeServices](https://github.com/KieronQuinn/PublicComputeServices) |
| PCS manifests | Mirrored model manifests | See upstream | [PublicComputeServices/pcs-manifests](https://github.com/PublicComputeServices/pcs-manifests) |

This repository is independent of Google and all projects above.

## Privacy defaults

- Runtime state, bucket names, cloud profiles, binaries, databases, protobufs,
  recordings, transcripts, and screenshots are ignored by Git.
- Exact call metadata, contact details, and device/build facts are separate explicit
  opt-ins; default S3 object IDs are pseudonymous.
- The public safety scanner runs locally and in CI.
- AWS resources default to private access; do not deploy with account-root credentials.

## Development

Use Python 3.10 or newer; Python 3.13 is the tested host and Lambda runtime. The
dependency floor intentionally rejects the vulnerable `urllib3` 1.26 line selected by
older Python/Botocore combinations.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
python3 -m unittest discover -s sync -p 'test_*.py' -v
python3 -m unittest discover -s aws/tests -p 'test_*.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 scripts/public_safety_check.py --root .
cfn-lint aws/template.yaml aws/bucket-template.yaml
pip-audit -r requirements-dev.txt
bandit -q -r sync scripts aws/functions -x '*/test_*.py' -ll
shellcheck scripts/*.sh
```

Read [Privacy](docs/PRIVACY.md), [Version-aware erasure](docs/ERASURE.md), and
[Contributing](CONTRIBUTING.md). Licensed under the [MIT License](LICENSE); see
[Third-party notices](THIRD_PARTY_NOTICES.md).
