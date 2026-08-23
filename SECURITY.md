# Security policy

## Reporting a vulnerability

Do not open a public issue containing credentials, call artifacts, exploit payloads,
or device identifiers. Use GitHub's private vulnerability-reporting feature for this
repository. For an upstream defect, report it to the relevant upstream maintainer.

## Known upstream risks

Public Compute Services 1.0.6 has two important unresolved risks:

1. A user-supplied manifest repository value is interpolated into a root shell command
   without robust escaping. This repository's configuration script accepts only a
   40-character commit and constructs the URL itself. Do not paste arbitrary PCS URLs.
2. PCS runs an unauthenticated gRPC proxy on predictable ports 7270–7273 and binds
   beyond loopback. A local app may connect to it or win a bind race. Network policy
   may block LAN access, but that does not remove the local-app risk.

PCS also relies on private Android TLS hooks, includes non-reproducible
`libsekret.so` binaries, and trusts the selected manifest publisher. Read upstream
source and issues before installing it on a daily-driver phone.

Vector and NeoZygisk run inside privileged Android processes. A defect in either can
crash Zygote or system_server. Keep a stock `init_boot.img` and a tested module-disable
recovery path.

## Threat model

- Root and an unlocked bootloader reduce the device's security guarantees.
- Any app with root access can read or alter call artifacts.
- S3 stores highly sensitive audio and metadata; keep Block Public Access enabled.
- Bedrock and Transcribe are external processors. Confirm residency, retention, and
  legal/consent requirements before use.
- Call recording laws vary. Obtain every participant's consent where required.

## Repository hygiene

Before every commit:

```bash
python3 scripts/public_safety_check.py --root .
gitleaks git --redact
```

Public GitHub secret scanning and push protection should remain enabled. If a secret
ever reaches Git history, rotate it immediately; deleting it in a later commit is not
enough.
