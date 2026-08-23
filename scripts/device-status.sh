#!/usr/bin/env bash
set -euo pipefail

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

echo "Device (serial intentionally not printed)"
printf '  model:       %s\n' "$("${adb_cmd[@]}" shell getprop ro.product.model | tr -d '\r')"
printf '  codename:    %s\n' "$("${adb_cmd[@]}" shell getprop ro.product.device | tr -d '\r')"
printf '  Android:     %s (API %s)\n' \
  "$("${adb_cmd[@]}" shell getprop ro.build.version.release | tr -d '\r')" \
  "$("${adb_cmd[@]}" shell getprop ro.build.version.sdk | tr -d '\r')"
printf '  build:       %s\n' "$("${adb_cmd[@]}" shell getprop ro.build.id | tr -d '\r')"
printf '  boot locked: %s\n' "$("${adb_cmd[@]}" shell getprop ro.boot.flash.locked | tr -d '\r')"
printf '  verified:    %s\n' "$("${adb_cmd[@]}" shell getprop ro.boot.verifiedbootstate | tr -d '\r')"

echo
if "${adb_cmd[@]}" shell "su -c 'id -u'" 2>/dev/null | tr -d '\r' | awk '$0 == "0" { ok=1 } END { exit !ok }'; then
  echo "Root: available"
  echo "Magisk modules:"
  "${adb_cmd[@]}" shell "su -c 'ls /data/adb/modules 2>/dev/null'" | tr -d '\r' | awk '{ print "  " $0 }'
  echo "PCS manifest override:"
  "${adb_cmd[@]}" shell "su -c 'device_config list_local_overrides 2>/dev/null'" |
    tr -d '\r' |
    awk '/PcsManifest__repository/ { print "  " $0 }'
  echo "PCS listeners:"
  "${adb_cmd[@]}" shell "su -c 'ss -ltnp 2>/dev/null'" |
    tr -d '\r' |
    awk '$4 ~ /:727[0-3]$/ { print "  " $0 }'
else
  echo "Root: unavailable"
fi
