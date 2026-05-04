# Innioasis Y1 Firmware Fixes

A patching toolkit for the Innioasis Y1 media player that fixes Bluetooth AVRCP, improves the music-player UI, and provides a setuid-root escalator for on-device debugging. Compatibility is defined by the [`KNOWN_FIRMWARES`](#stock-firmware-manifest) manifest in `innioasis-y1-fixes.bash`; add a row to support a new build.

## Overview

- **Bluetooth AVRCP 1.4** — forces AVRCP 1.4 advertisement across all BT stack layers (daemon, ODEX, JNI library, core library).
- **Artist→Album navigation** — improves the music-player UX by showing album cover art after artist selection instead of a flat song list.
- **System configuration** — enables ADB debugging and tunes Bluetooth settings.
- **APK patching** — patches the system music player APK at the smali level.
- **Root** — installs a minimal `/system/xbin/su` (setuid-root, mode 06755) for `adb shell /system/xbin/su`-style escalation. Stock `/sbin/adbd` is untouched.

## Layout

This repo is a small monorepo. The bash entry-point at the root dispatches into source trees under `src/`:

- [`src/patches/`](src/patches/) — byte/smali patchers (`patch_*.py`)
- [`src/su/`](src/su/) — minimal setuid-root `su` for `/system/xbin/su` (consumed by `--root`)
- [`src/Y1MediaBridge/`](src/Y1MediaBridge/) — Android service app source for `Y1MediaBridge.apk` (consumed by `--avrcp`). Build with Gradle: `cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug`.
- [`src/btlog-dump/`](src/btlog-dump/) — minimal ARM ELF that taps `mtkbt`'s `@btlog` abstract socket for `__xlog_buf_printf` + decoded HCI traffic. Used by [`tools/dual-capture.sh`](tools/dual-capture.sh). Build via `cd src/btlog-dump && make`.
- `innioasis-y1-fixes.bash` — single entry point at the root; flag-driven dispatch into the trees above
- `reference/` — manually-extracted reference files for v3.0.2

## Scripts

- **`src/patches/patch_mtkbt.py`** — patches stock `mtkbt` daemon for AVRCP 1.4. Eleven patches (B1-B3, C1-C3, A1, D1, E3, E4, E8). Stock MD5 `3af1d4ad…` → patched `d47c9040…`.
- **`src/patches/patch_mtkbt_odex.py`** — patches `MtkBt.odex` (F1: `getPreferVersion()` returns 14; F2: `disable()` resets `sPlayServiceInterface`).
- **`src/patches/patch_libextavrcp_jni.py`** — patches `libextavrcp_jni.so` (C2a/b: hardcode `g_tg_feature=0x0e`, `sdpfeature=0x23`; C3a/b: raise GetCapabilities event-list cap 13→14).
- **`src/patches/patch_libextavrcp.py`** — single AVRCP version constant patch (C4: `0x0103 → 0x0104` at `0x002e3b`).
- **`src/patches/patch_y1_apk.py`** — smali patcher for the Y1 music player APK (Artist→Album navigation). Uses androguard + apktool; preserves original signatures for system-app deployment.
- **`src/patches/patch_adbd.py`** — *unwired since v1.7.0; historical record only.* H1/H2/H3 byte patches against `/sbin/adbd`.
- **`src/patches/patch_bootimg.py`** — *unwired since v1.7.0; historical record only.* Format-aware boot.img cpio patcher.
- **`src/su/`** — setuid-root `su` source. Built via `cd src/su && make` → `src/su/build/su`. ~900-byte direct-syscall ARM-EABI ELF.
- **`src/btlog-dump/`** — `@btlog` abstract-socket reader (diagnostic; not part of the `--all` flash flow). Built via `cd src/btlog-dump && make` → `src/btlog-dump/build/btlog-dump`. ~1 KB direct-syscall ARM-EABI ELF, same toolchain as `src/su/`. Reuses `src/su/start.S`.
- **`innioasis-y1-fixes.bash`** — entry point. Takes `rom.zip`, MD5-validates against `KNOWN_FIRMWARES`, mounts `system.img`, dispatches each `--flag` to its patcher (auto-extract → patch → write-back, idempotent), flashes via mtkclient.

Per-patch byte-level reference: **[docs/PATCHES.md](docs/PATCHES.md)**.

## Quick start

Stage `rom.zip` (the official OTA — MD5-validated against [`KNOWN_FIRMWARES`](#stock-firmware-manifest)) in a directory. Run `tools/setup.sh` once to clone MTKClient and create the patcher Python venv. Build `src/Y1MediaBridge/` once if using `--avrcp`, and `src/su/` once if using `--root` — the bash picks up both build outputs directly.

```bash
mkdir -p ~/y1-patches
cp /path/to/rom.zip ~/y1-patches/

# One-time tooling setup (clones MTKClient, creates Python venvs):
./tools/setup.sh

# One-time Android SDK install if using --avrcp (Linux/macOS only;
# see docs/ANDROID-SDK.md for Windows or manual setup):
./tools/install-android-sdk.sh

# Source the env file so adb/sdkmanager are on PATH (gradle build itself
# doesn't need this — sdk.dir is in src/Y1MediaBridge/local.properties —
# but adb is handy for verifying the install on the device later).
source tools/android-sdk-env.sh

# Build src/Y1MediaBridge/ once if using --avrcp.
# The --stop is defensive: gradle's daemon caches the JVM it started with,
# so if you ever change JAVA_HOME between builds, --stop ensures a fresh
# daemon picks up the new JDK. Cheap on a fresh setup.
( cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug )

# Build src/su/ once if using --root:
( cd src/su && make )

./innioasis-y1-fixes.bash --artifacts-dir ~/y1-patches --all
```

`rom.zip` is the only required artifact. Subdirectory build outputs (`src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk`, `src/su/build/su`) and the contents of `tools/` are picked up automatically; rebuild any of them only when their sources change.

If you have MTKClient installed elsewhere (or want to test against an alternate checkout), pass `--mtkclient-dir <path>` to the bash, or set `MTKCLIENT_DIR` in your environment. Same for the patcher Python venv via `--python-venv <path>`.

The bash extracts `system.img` from `rom.zip`, mounts it as a loop device, applies the selected patches in-place, unmounts, and flashes the patched image via mtkclient.

### Flags

| Flag | Effect |
|---|---|
| <nobr>`--adb`</nobr> | Sets `persist.service.adb.enable=1` and `persist.service.debuggable=1` in `build.prop`. |
| <nobr>`--avrcp`</nobr> | Auto-extracts and patches `mtkbt`, `MtkBt.odex`, `libextavrcp.so`, `libextavrcp_jni.so` from the mount; installs `Y1MediaBridge.apk` from `src/Y1MediaBridge/app/build/outputs/apk/debug/app-debug.apk` (build once via `cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug`). |
| <nobr>`--bluetooth`</nobr> | Configures `audio.conf`, clears BT blacklists, sets `persist.bluetooth.avrcpversion=avrcp14` and the AVRCP target/source profile flags. |
| <nobr>`--music-apk`</nobr> | Auto-extracts and patches the Y1 music player APK (Artist→Album navigation). |
| <nobr>`--remove-apps`</nobr> | Removes bloatware APKs (`ApplicationGuide`, `BackupRestoreConfirmation`, `BasicDreams`, etc.). |
| <nobr>`--root`</nobr> | Installs the prebuilt `src/su/build/su` setuid-root binary at `/system/xbin/su` (mode 06755, root:root). Stock `/sbin/adbd` is untouched; root is obtained post-flash via `adb shell /system/xbin/su`. |
| <nobr>`--all`</nobr> | All flags above. |

Run `./innioasis-y1-fixes.bash --help` for the full flag listing.

### Manual patcher invocation

The patchers can be run standalone from `src/patches/`. Each verifies the input MD5, checks patch sites before and after, and refuses to write output on mismatch. Example:

```bash
( cd src/patches && python3 patch_mtkbt.py mtkbt )    # → src/patches/output/mtkbt.patched
```

## Diagnostics

Independent of the patch flow, the repo ships a small set of post-root diagnostic tools used to investigate AVRCP behaviour on hardware. Pre-req: `--root` flashed.

- **`src/btlog-dump/`** + **`tools/dual-capture.sh`** + **`tools/btlog-parse.py`** — the `@btlog` tap. `mtkbt` runs an undocumented `SOCK_STREAM` listener at the abstract socket `@btlog` that pushes `__xlog_buf_printf` output (every `[AVRCP]` / `[AVCTP]` / `[L2CAP]` / `[ME]` log line that's invisible to `logcat`) plus decoded HCI command/event traffic. `dual-capture.sh` pushes the in-tree reader, runs it as root alongside `logcat -v threadtime`, and writes both streams to a timestamped output dir; `btlog-parse.py` decodes the structured binary stream and supports `--tag-include` / `--tag-exclude` filters. Replaces the conventional `persist.bt.virtualsniff` btsnoop knob (which breaks BT init on this device) and the `__xlog_buf_printf → logcat` redirect attempts (which crash mtkbt; see [INVESTIGATION.md](INVESTIGATION.md) G1/G2). One-shot:
  ```bash
  ( cd src/btlog-dump && make )                         # one-time build
  ./tools/dual-capture.sh ~/captures/connect-attempt    # Ctrl-C when scenario complete
  ./tools/btlog-parse.py ~/captures/connect-attempt/btlog.bin --tag-include AVRCP --tag-include AVCTP
  ```
- **`tools/probe-postroot.sh`** + **`tools/probe-postroot-device.sh`** — one-shot post-root sanity probe. Pushes a small device-side script that enumerates: `mtkbt` PIE base via `/proc/<pid>/maps`, `/proc/mtprintk` and other MTK debug-node accessibility, canonical btsnoop file paths, all `bt`/`bluetooth`/`snoop` `getprop` keys, `/dev/stp*` permissions, `dmesg` AVRCP/AVCTP/STP traces, gdbserver presence, SELinux mode, ptrace policy, and `/proc/net/unix` for the `bt.ext.adp.*` and `@btlog` abstract sockets. Useful to re-verify against a new firmware version if `KNOWN_FIRMWARES` ever gains a 3.0.3+ entry.

Both tools are diagnostic-only — neither is invoked by the patch flow. Output is intentionally text-friendly so it can be saved alongside the brief / `INVESTIGATION.md` for any future investigator.

## Status (2026-05-04)

All four binary patch scripts produce on-wire-verified output (sdptool confirms AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033) and the Java layer initializes correctly for AVRCP 1.4. The v1.8.0 setuid-`su` root path is hardware-verified (`adb shell` → `su` → `id` returns `uid=0(root) gid=0(root)`).

**Cardinality:0 persists in all real-world test scenarios** — confirmed against the car (Kia/Bolt) with the additional functional symptom that **CT→Y1 PASS_THROUGH play/pause is also broken**, not just notification-cosmetic. Pixel 4 (TG) ↔ Sonos Roam (CT) works correctly with full metadata transfer, providing a free working-reference A/B for future diagnosis.

The previous "primary lead" — `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096` (= `0x1000`) — turned out to be a phantom: the same value is set on `ACTIVATE_CNF` 3 ms after the JNI sends `ACTIVATE_REQ`, before any peer is involved. `0x1000` is mtkbt's standard "request acknowledged" status code, not a peer-feedback or "feature degraded" indicator. The Browsing-bit experiment (testing `SupportedFeatures = 0x0033 → 0x0073`) was confirmed to land on the wire but did not change peer behaviour; that hypothesis is also dead.

The active investigation is now the SDP-record A/B between Pixel 4 (working TG) and Y1 (broken TG) from a Linux laptop with `sdptool browse`. See [INVESTIGATION.md](INVESTIGATION.md) for the full narrative including refuted hypotheses; live working notes (Trace #1 through #11) are maintained externally to the repo.

## Stock firmware manifest

Known stock firmwares recognised by `KNOWN_FIRMWARES` in the bash. Add a row (same five-field schema) to enrol a new build.

| Version | rom.zip (input) | system.img (raw, extracted) | boot.img (in zip; not consumed since v1.7.0) | Music APK basename in `app/` |
|---|---|---|---|---|
| **3.0.2** | `82657db82578a38c6f1877e02407127a` | `473991dadeb1a8c4d25902dee9ee362b` | `1f7920228a20c01ad274c61c94a8cf36` | `com.innioasis.y1_3.0.2.apk` |

Stock sizes (v3.0.2, the currently enrolled build): `rom.zip` 259,502,414 bytes; `system.img` 681,574,400 bytes (raw ext4 — auto-de-sparsed via `simg2img` if a build bundles a sparse one); `boot.img` 4,706,304 bytes.

## Requirements

- Bash 4+, `sudo` (loop-mount + chown), `git`, `unzip`, `md5sum` (Linux) or `md5 -q` (macOS).
- Python 3.8+ with `venv` module. Patcher byte-level scripts are stdlib-only; `patch_y1_apk.py` needs `androguard`, which `tools/setup.sh` installs into `tools/python-venv/`. Java 11+ also required for `--music-apk` (apktool's smali assembler; apktool itself is downloaded by `patch_y1_apk.py` on first invocation).
- `tools/setup.sh` clones MTKClient (currently pinned to 2.1.4.1) into `tools/mtkclient/` and creates `tools/mtkclient/venv/` with its requirements. Override with `--mtkclient-dir <path>` or `MTKCLIENT_DIR` if you have it elsewhere.
- `simg2img` — only if the matched `KNOWN_FIRMWARES` build bundles a sparse `system.img` (the currently-enrolled v3.0.2 is raw). Install: `dnf install android-tools` (Fedora/RHEL via EPEL), `apt install android-sdk-libsparse-utils` (Debian/Ubuntu), `pacman -S android-tools` (Arch), `brew install simg2img` (macOS).
- For `--root` only: prebuilt `src/su/build/su`. Build via `cd src/su && make`. Toolchain: `dnf install -y epel-release && dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make` (Rocky/Alma/RHEL/Fedora) or the equivalent `gcc-arm-linux-gnueabi` package on Debian/Ubuntu.
- For `--avrcp` only: Android SDK + JDK 17+. Gradle is bootstrapped by the in-tree wrapper at `src/Y1MediaBridge/gradlew`. The repo's `tools/install-android-sdk.sh` auto-installs the SDK on Linux/macOS into `tools/android-sdk/` (~1.5 GB; idempotent, short-circuits on existing `ANDROID_HOME`). Manual install per platform — and the Windows path — in [`docs/ANDROID-SDK.md`](docs/ANDROID-SDK.md).

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — version history (Keep a Changelog format)
- [INVESTIGATION.md](INVESTIGATION.md) — full AVRCP investigation narrative, refuted hypotheses, trace history
- [docs/PATCHES.md](docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [docs/DEX.md](docs/DEX.md) — DEX-level analysis for `patch_y1_apk.py`'s smali patches
- [docs/ANDROID-SDK.md](docs/ANDROID-SDK.md) — Android SDK install instructions for Linux / macOS / Windows (only needed for `--avrcp`)

## Deployment notes

The patched music-player APK must be deployed directly to `/system/app/` on the device filesystem — **not** via `adb install` or PackageManager. The original META-INF signature block is retained (stale, not re-signed); it satisfies PackageManager's parseable-signature requirement, and signature verification is bypassed when deploying via the filesystem during boot. The bash's `--music-apk` flag handles this automatically. Manual ADB push:

```bash
adb root && adb remount
adb push com.innioasis.y1_<version>-patched.apk /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb reboot
```

## Verified against

Innioasis Y1 media player — MTK MT6572 ARM, Android 4.2.2 (JDQ39), Dalvik VM API 17. Currently enrolled in `KNOWN_FIRMWARES`: **v3.0.2** (the only build that's been hardware-verified against this toolkit). Adding a new build means dropping in its `rom.zip` MD5 row and re-running the patchers; if site offsets shifted they'll fail their stock-MD5 check and need re-locating.

## Author

Sean Halpin ([github.com/SeanathanVT](https://github.com/SeanathanVT))

## License

GNU General Public License v3.0 (GPLv3) — see [LICENSE](LICENSE).
