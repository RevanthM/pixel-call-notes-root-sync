#!/usr/bin/env bash
set -euo pipefail

required=(adb fastboot python3)
optional=(ffprobe aws sam cfn-lint pip-audit bandit shellcheck gh gitleaks)
missing=0

echo "Required tools"
for tool in "${required[@]}"; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '  ok       %s\n' "$tool"
  else
    printf '  MISSING  %s\n' "$tool"
    missing=1
  fi
done

if command -v python3 >/dev/null 2>&1 &&
  ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "  MISSING  Python 3.10 or newer (current python3 is too old)"
  missing=1
fi

echo
echo "Optional tools"
for tool in "${optional[@]}"; do
  if command -v "$tool" >/dev/null 2>&1; then
    printf '  ok       %s\n' "$tool"
  else
    printf '  optional %s\n' "$tool"
  fi
done

echo
if command -v adb >/dev/null 2>&1; then
  authorized="$(adb devices | awk 'NR > 1 && $2 == "device" { count++ } END { print count + 0 }')"
  unauthorized="$(adb devices | awk 'NR > 1 && $2 == "unauthorized" { count++ } END { print count + 0 }')"
  printf 'Android devices: %s authorized, %s unauthorized (serials hidden)\n' \
    "$authorized" "$unauthorized"
else
  echo "Android devices: unavailable because adb is missing"
fi

if ((missing)); then
  echo
  echo "Install missing required tools before continuing." >&2
  exit 1
fi

echo
echo "Prerequisites found. Read docs/ANDROID_SETUP.md before changing a device."
