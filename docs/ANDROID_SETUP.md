# Android setup

This is an advanced, destructive procedure. The tested target was a Pixel 11 Pro Fold
(`yogi`) on Android 17. Adaptation to another device requires independent partition
and compatibility research.

## 1. Stop conditions

Do not continue unless all are true:

- Every important account, photo, authenticator, eSIM, message, and app is backed up.
- You accept that `fastboot flashing unlock` immediately factory-resets the phone.
- OEM unlocking and USB debugging are available.
- You have the official factory image for the exact build currently installed.
- You extracted and hashed its stock `init_boot.img`.
- You know how to reach the bootloader and disable a broken Magisk module.
- You accept reduced Play Integrity, DRM, banking, enterprise, and physical-security
  guarantees.
- Call recording is legal and consented where you use it.

Run the non-mutating checks:

```bash
scripts/check-prerequisites.sh
scripts/device-status.sh
```

## 2. Recovery kit

Download the exact factory image from
[Google Pixel factory images](https://developers.google.com/android/images).
Verify Google's published checksum, then extract the nested image ZIP and
`init_boot.img`:

```bash
python3 scripts/extract_init_boot.py \
  /path/to/device-factory.zip \
  --output-dir "$HOME/pixel-recovery"
```

Record the hash:

```bash
shasum -a 256 "$HOME/pixel-recovery/init_boot.img"
```

Never use an `init_boot.img` from this repository, another build, or another model.

## 3. Magisk and bootloader unlock

Download Magisk only from [topjohnwu/Magisk releases](https://github.com/topjohnwu/Magisk/releases).
The tested version is 30.7.

1. Install the official Magisk APK.
2. Reboot to the bootloader and confirm the codename again.
3. Run `fastboot flashing unlock` only when you intend to wipe the phone.
4. Confirm the unlock physically on the phone.
5. Complete Android setup again.
6. Copy the matching stock `init_boot.img` to the phone.
7. In Magisk, choose **Install → Select and Patch a File**.
8. Pull the patched image to the recovery directory and hash it.
9. Check the active slot with `fastboot getvar current-slot`.
10. Flash only the matching active `init_boot` partition, then reboot.

Verify:

```bash
adb shell su -c id
adb shell getprop ro.boot.flash.locked       # expected: 0
adb shell getprop ro.boot.verifiedbootstate # expected: orange
```

Keep the stock and patched images. Do not relock while a patched image is installed.

## 4. Install the injection stack

Install one layer, reboot, and verify before adding the next.

### NeoZygisk

Repository: [JingMatrix/NeoZygisk](https://github.com/JingMatrix/NeoZygisk)

The exact tested Pixel 11 fix was CI build `291-0699f64` from
[run 32421683107](https://github.com/JingMatrix/NeoZygisk/actions/runs/32421683107).
Its downloaded wrapper SHA-256 was:

```text
63808a74efef68cb827a6b83f923940eeaf6c9192b76e00d28a257f96afc5279
```

CI artifacts expire. Prefer a newer official release only after confirming it contains
the `0699f64` memfd/SELinux fix. Do not substitute v2.4 build 289 on a Pixel 11 showing
`expected=1 actual=0`.

- Disable Magisk's built-in Zygisk.
- Leave Magisk Enforce DenyList off.
- Install NeoZygisk in Magisk and reboot.

### Vector

Repository: [JingMatrix/Vector](https://github.com/JingMatrix/Vector)

The tested build is Vector 2.2 canary 3107. Release ZIP SHA-256:

```text
82010704a98fe503bd89ef972d8fbde1f424e8e0952c32581a838e65c91bed54
```

Install through Magisk, reboot, and confirm Vector's daemon is healthy.

### Public Compute Services

Repository: [KieronQuinn/PublicComputeServices](https://github.com/KieronQuinn/PublicComputeServices)

The tested APK is PCS 1.0.6. SHA-256:

```text
316833bc5a0ef1cbe192bdeaf01d6628b3eda3927607ab0987c9d6d9432e747c
```

Install the APK and enable its Vector scopes. For Call Notes, enable installed
packages from this set:

```text
com.kieronquinn.app.pcs
com.google.android.as.oss
com.google.android.as
com.google.android.dialer
com.google.android.aicore
```

PCS also lists Pixel System Intelligence, Google TTS, and Pixel Agent for other AI
features. Scope only packages you intend to modify.

Reboot. PCS should report that its Xposed module is enabled.

## 5. Configure an immutable manifest

Read `SECURITY.md` first. PCS 1.0.6 can pass a malicious repository value to a root
shell. Do not paste a URL from a post, chat, or issue.

The script accepts only a 40-character Git commit and constructs the URL:

```bash
export PCS_MANIFEST_COMMIT=f18a0d6e464febc2f21342f2e0b927fe758f1c7f
scripts/configure-pcs.sh --yes
```

Use the device's native build labels unless you have evidence they are wrong. The
tested Pixel 11 reported tier MID / variant 24.

Open PCS and sync manifests while connected to unmetered Wi-Fi. If downloads remain
stuck, use the bounded reset only after reading it:

```bash
scripts/reset_model_downloads.sh --yes
```

That script force-stops target Google packages before deleting only their MDD
shared-preference caches. It does not delete model files or user call data.

## 6. Verify Call Notes

Make a consented call longer than one minute, enable Call Notes, and wait after
hanging up. Confirm Google Phone shows audio, transcript, and a non-empty summary.

Then run:

```bash
python3 scripts/verify_call_note.py
```

Pass criteria:

- A new `fermat_audio` row and `.m4a` exist.
- `fermat_transcription_state` has a SODA transcript path without an error.
- `fermat_summary_state` has `Duplex / postCall`, a non-empty summary, and no error.

The verifier redacts summary text unless `--show-summary` is explicitly used.

## 7. Recovery

- A broken module: use Magisk safe mode or create its `disable` marker from recovery/
  adb, then reboot.
- Broken patched boot: flash the saved stock `init_boot.img` for the current build.
- OTA update: obtain the new matching factory image and patch its new `init_boot.img`;
  never reuse the old one.
- Relock only after returning every verified-boot partition to stock. Relocking a
  modified device can make it unbootable and performs another wipe.
