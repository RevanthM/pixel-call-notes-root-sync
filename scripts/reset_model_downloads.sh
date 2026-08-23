#!/usr/bin/env bash
set -euo pipefail

if [[ "${1:-}" != "--yes" ]]; then
  cat >&2 <<'EOF'
This force-stops Google AI packages and removes only their MDD shared-preference
caches. It may interrupt active AI features. Review the script, then rerun with --yes.
EOF
  exit 2
fi

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

adb_cmd=(adb -s "$serial")
packages=(
  com.google.android.aicore
  com.google.android.as.oss
  com.google.android.as
  com.google.android.dialer
  com.kieronquinn.app.pcs
)

for package in "${packages[@]}"; do
  if ! "${adb_cmd[@]}" shell pm path "$package" >/dev/null 2>&1; then
    continue
  fi
  echo "Resetting MDD cache for $package"
  command="am force-stop $package && rm -f /data/user/0/$package/shared_prefs/gms_icing_mdd_*.xml"
  "${adb_cmd[@]}" shell "su -c '$command'"
done

echo "Done. Reboot, open PCS, sync manifests, and remain on unmetered Wi-Fi."
