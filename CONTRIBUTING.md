# Contributing

Contributions are welcome when they preserve the safety and privacy boundaries in
`AGENTS.md`.

## Rules

- Do not attach APKs, module ZIPs, boot images, factory images, call artifacts,
  screenshots, databases, logs, model files, or credentials.
- Use synthetic names, numbers, timestamps, account IDs, bucket names, and transcripts
  in tests and documentation.
- Never weaken fail-closed checks to make a demo pass.
- Cite upstream documentation for version, partition, or compatibility claims.
- State the device codename, Android API, and component versions used for a successful
  test without publishing a device serial.

## Checks

```bash
python3 -m unittest discover -s sync -p 'test_*.py' -v
python3 -m unittest discover -s aws/tests -p 'test_*.py' -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 -m py_compile sync/*.py aws/functions/*/app.py scripts/*.py
bash -n scripts/*.sh
shellcheck scripts/*.sh
cfn-lint aws/template.yaml aws/bucket-template.yaml
pip-audit -r requirements.txt
pip-audit -r requirements-dev.txt
bandit -q -r sync scripts aws/functions -x '*/test_*.py' -ll
python3 scripts/public_safety_check.py --root .
```

Use a feature branch and explain any irreversible-device or cloud-security impact in
the pull request.
