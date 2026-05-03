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
- [`src/Y1MediaBridge/`](src/Y1MediaBridge/) — Android service app source for `Y1MediaBridge.apk` (consumed by `--avrcp`). Build with Gradle: `cd src/Y1MediaBridge && ./gradlew assembleRelease`.
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
- **`innioasis-y1-fixes.bash`** — entry point. Takes `rom.zip`, MD5-validates against `KNOWN_FIRMWARES`, mounts `system.img`, dispatches each `--flag` to its patcher (auto-extract → patch → write-back, idempotent), flashes via mtkclient.

Per-patch byte-level reference: **[docs/PATCHES.md](docs/PATCHES.md)**.

## Quick start

Stage `rom.zip` (the official OTA — MD5-validated against [`KNOWN_FIRMWARES`](#stock-firmware-manifest)) in a directory. If using `--avrcp` build `src/Y1MediaBridge/` once; if using `--root` build `src/su/` once. The bash picks up both build outputs directly — no need to stage the artifacts.

```bash
mkdir -p ~/y1-patches
cp /path/to/rom.zip ~/y1-patches/

# Build src/Y1MediaBridge/ once if using --avrcp:
( cd src/Y1MediaBridge && ./gradlew assembleRelease )

# Build src/su/ once if using --root:
( cd src/su && make )

./innioasis-y1-fixes.bash --artifacts-dir ~/y1-patches --all
```

`rom.zip` is the only required artifact. Both `src/Y1MediaBridge/` and `src/su/` only need to be rebuilt when their sources change.

The bash extracts `system.img` from `rom.zip`, mounts it as a loop device, applies the selected patches in-place, unmounts, and flashes the patched image via mtkclient.

### Flags

| Flag | Effect |
|---|---|
| `--adb` | Sets `persist.service.adb.enable=1` and `persist.service.debuggable=1` in `build.prop`. |
| `--avrcp` | Auto-extracts and patches `mtkbt`, `MtkBt.odex`, `libextavrcp.so`, `libextavrcp_jni.so` from the mount; installs `Y1MediaBridge.apk` from `src/Y1MediaBridge/app/build/outputs/apk/release/app-release.apk` (build once via `cd src/Y1MediaBridge && ./gradlew assembleRelease`). |
| `--bluetooth` | Configures `audio.conf`, clears BT blacklists, sets `persist.bluetooth.avrcpversion=avrcp14` and the AVRCP target/source profile flags. |
| `--music-apk` | Auto-extracts and patches the Y1 music player APK (Artist→Album navigation). |
| `--remove-apps` | Removes bloatware APKs (`ApplicationGuide`, `BackupRestoreConfirmation`, `BasicDreams`, etc.). |
| `--root` | Installs the prebuilt `src/su/build/su` setuid-root binary at `/system/xbin/su` (mode 06755, root:root). Stock `/sbin/adbd` is untouched; root is obtained post-flash via `adb shell /system/xbin/su`. |
| `--all` | All flags above. |

Run `./innioasis-y1-fixes.bash --help` for the full flag listing.

### Manual patcher invocation

The patchers can be run standalone from `src/patches/`. Each verifies the input MD5, checks patch sites before and after, and refuses to write output on mismatch. Example:

```bash
( cd src/patches && python3 patch_mtkbt.py mtkbt )    # → src/patches/output/mtkbt.patched
```

## Status (2026-05-03)

All four binary patch scripts produce on-wire-verified output (sdptool confirms AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033) and the Java layer initializes correctly for AVRCP 1.4. **Cardinality:0 persists across all three known-good 1.4 controllers** (car, Sonos Roam, Samsung TV) — no peer ever sends `REGISTER_NOTIFICATION`. The remaining gate is upstream of mtkbt's op_code=4 dispatcher table; the strongest static lead is `MSG_ID_BT_AVRCP_CONNECT_CNF result:4096` (= `0x1000`), suggesting accepted-but-degraded negotiation. The v1.8.0 setuid-`su` root path is pending hardware verification; if it works, HCI snoop / `__xlog_buf_printf` capture / `gdbserver` attach become reachable. See [INVESTIGATION.md](INVESTIGATION.md) for the full narrative including refuted hypotheses and the trace history.

## Stock firmware manifest

Known stock firmwares recognised by `KNOWN_FIRMWARES` in the bash. Add a row (same five-field schema) to enrol a new build.

| Version | rom.zip (input) | system.img (raw, extracted) | boot.img (in zip; not consumed since v1.7.0) | Music APK basename in `app/` |
|---|---|---|---|---|
| **3.0.2** | `82657db82578a38c6f1877e02407127a` | `473991dadeb1a8c4d25902dee9ee362b` | `1f7920228a20c01ad274c61c94a8cf36` | `com.innioasis.y1_3.0.2.apk` |

Stock sizes (v3.0.2, the currently enrolled build): `rom.zip` 259,502,414 bytes; `system.img` 681,574,400 bytes (raw ext4 — auto-de-sparsed via `simg2img` if a build bundles a sparse one); `boot.img` 4,706,304 bytes.

## Requirements

- Bash 4+, `sudo` (loop-mount + chown), `unzip`, `md5sum` (Linux) or `md5 -q` (macOS).
- `mtkclient` 2.1.4.1 at `/opt/mtkclient-2.1.4.1`.
- Python 3.8+ (all patchers; stdlib only except `patch_y1_apk.py`).
- Java 11+ and `androguard` (`pip install androguard`) — only for `--music-apk`. apktool is downloaded by `patch_y1_apk.py` on first invocation.
- `simg2img` — only if the matched `KNOWN_FIRMWARES` build bundles a sparse `system.img` (the currently-enrolled v3.0.2 is raw). Install: `dnf install android-tools` (Fedora/RHEL via EPEL), `apt install android-sdk-libsparse-utils` (Debian/Ubuntu), `pacman -S android-tools` (Arch), `brew install simg2img` (macOS).
- For `--root` only: prebuilt `src/su/build/su`. Build via `cd src/su && make`. Toolchain: `dnf install -y epel-release && dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make` (Rocky/Alma/RHEL/Fedora) or the equivalent `gcc-arm-linux-gnueabi` package on Debian/Ubuntu.

## Documentation

- [CHANGELOG.md](CHANGELOG.md) — version history (Keep a Changelog format)
- [INVESTIGATION.md](INVESTIGATION.md) — full AVRCP investigation narrative, refuted hypotheses, trace history
- [docs/PATCHES.md](docs/PATCHES.md) — per-patch byte-level reference (offsets, before/after bytes, rationale)
- [docs/DEX.md](docs/DEX.md) — DEX-level analysis for `patch_y1_apk.py`'s smali patches

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
