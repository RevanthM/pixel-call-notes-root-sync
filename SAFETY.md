# Safety gates

Read this before using any Android or AWS instructions.

## Human-only device actions

An AI agent may explain or verify these steps, but must not perform or confirm them:

- Bootloader unlock, which immediately factory-resets the phone.
- Partition flashing, erasing, slot changes, or bootloader relocking.
- Magisk/NeoZygisk/Vector module installation.

Before each write, personally verify the Pixel codename, exact installed build, active
slot, downloaded factory-image checksum, extracted stock-image checksum, and recovery
path. Stop on any mismatch. Never relock while modified verified-boot partitions remain.

Back up accounts, photos, messages, authenticators, eSIM recovery information, and
anything needed to regain access. Never place seed phrases, recovery codes, or
credentials in this repository, a terminal transcript, screenshot, or cloud backup.

## Root and PCS residual risk

Root weakens Android's security boundary and may break Wallet, banking, DRM, RCS,
enterprise policy, Play Integrity, or support. Vector/NeoZygisk defects can destabilize
privileged Android processes.

PCS 1.0.6 has an unsafe root-shell configuration path and an unauthenticated fixed-port
proxy. This repository constructs its manifest URL from a hex-only pinned commit, but
cannot remove the proxy risk. Do not install untrusted apps on the rooted phone, do not
paste arbitrary repository URLs, and review `SECURITY.md`.

## Calls and consent

Recording, transcribing, and cloud-processing calls may require every participant's
consent. Requirements vary by all participants' locations. This project is not legal
advice and must not be used to bypass recording disclosures.

## Cloud actions

AWS deployment and upload are separate approval boundaries. Never use account-root
credentials, expose the bucket publicly, enable prompt/content logging, or upload
before choosing retention, residency, access, budget, and incident-response controls.
