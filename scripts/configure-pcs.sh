#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
  cat >&2 <<'EOF'
This writes a root Device Config override for PCS.
Review SECURITY.md, set PCS_MANIFEST_COMMIT, then rerun with --yes.
EOF
  exit 2
fi

commit="${PCS_MANIFEST_COMMIT:-}"
if [[ ! "$commit" =~ ^[0-9a-fA-F]{40}$ ]]; then
  echo "PCS_MANIFEST_COMMIT must be exactly 40 hexadecimal characters." >&2
  exit 2
fi
commit="$(printf '%s' "$commit" | tr '[:upper:]' '[:lower:]')"

serial="${ADB_SERIAL:-}"
if [[ -z "$serial" ]]; then
  serial="$(adb devices | awk 'NR > 1 && $2 == "device" { print $1 }')"
  count="$(printf '%s\n' "$serial" | awk 'NF { count++ } END { print count + 0 }')"
  if [[ "$count" != "1" ]]; then
    echo "Expected one authorized device; set ADB_SERIAL explicitly." >&2
    exit 1
  fi
fi
if [[ -z "$serial" ]]; then
  echo "No authorized Android device found." >&2
  exit 1
fi

url="https://raw.githubusercontent.com/PublicComputeServices/pcs-manifests/${commit}"
adb_cmd=(adb -s "$serial")

if [[ "$("${adb_cmd[@]}" shell "su -c 'id -u'" 2>/dev/null | tr -d '\r')" != "0" ]]; then
  echo "Magisk root is unavailable or not granted to adb shell." >&2
  exit 1
fi

# The URL is not user-provided shell text: it is constructed from a fixed HTTPS host
# plus a value restricted to hexadecimal characters.
command="device_config override device_personalization_services PcsManifest__repository ${url}"
"${adb_cmd[@]}" shell "su -c '$command'" >/dev/null

actual="$("${adb_cmd[@]}" shell "su -c 'device_config list_local_overrides'" |
  tr -d '\r' |
  awk -F= '/PcsManifest__repository/ { print $2; exit }')"

if [[ "$actual" != "$url" ]]; then
  echo "PCS override verification failed." >&2
  exit 1
fi

echo "PCS manifest pinned to commit ${commit}."
echo "Open PCS, sync manifests on unmetered Wi-Fi, then reboot."
