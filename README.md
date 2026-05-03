# Innioasis Y1 Firmware Fixes

A comprehensive patching toolkit for the Innioasis Y1 media player (firmware 3.0.2) that fixes Bluetooth AVRCP functionality, improves the media player UI navigation, and enables ADB debugging.

## Overview

This project provides tools to patch and enhance the Innioasis Y1 firmware with:

- **Bluetooth AVRCP 1.4 Support** – Forces AVRCP 1.4 advertisement across all BT stack layers (daemon, ODEX, JNI library, core library)
- **Artist→Album Navigation** – Improves media player UX by showing album cover art after artist selection instead of a flat song list
- **System Configuration** – Enables ADB debugging and optimizes Bluetooth settings
- **APK Patching** – Patches the system music player APK at the bytecode level using smali assembly

## Patch IDs

Patches are referenced by short IDs (B1, C2a, D1, E8, F1, H1/H2/H3, su, etc.) throughout this README, [INVESTIGATION.md](INVESTIGATION.md), and [CHANGELOG.md](CHANGELOG.md). The full legend with byte-level offsets and rationale lives in **[docs/PATCHES.md](docs/PATCHES.md)**.

## Contents

### Layout

This repo is a small monorepo. The bash entry-point at the root dispatches into source trees under `src/`:

- [`src/patches/`](src/patches/) — byte/smali patchers (`patch_*.py`)
- [`src/su/`](src/su/) — minimal setuid-root `su` for `/system/xbin/su` (consumed by `--root`)
- [`src/Y1MediaBridge/`](src/Y1MediaBridge/) — Android service app (`Y1MediaBridge.apk` consumed by `--avrcp`); imported via `git subtree` so its commit history is preserved here
- `innioasis-y1-fixes.bash` — single entry point at the root; flag-driven dispatch into the trees above
- `reference/` — manually-extracted reference files for v3.0.2

Per-patch byte-level reference: **[docs/PATCHES.md](docs/PATCHES.md)**.

### Scripts

- **`src/patches/patch_mtkbt.py`** — patches stock `mtkbt` daemon for AVRCP 1.4. Eleven patches (B1-B3, C1-C3, A1, D1, E3, E4, E8). Stock MD5 `3af1d4ad…` → patched `d47c9040…`.
- **`src/patches/patch_mtkbt_odex.py`** — patches `MtkBt.odex` (F1: `getPreferVersion()` returns 14; F2: `disable()` resets `sPlayServiceInterface`). Recomputes DEX adler32.
- **`src/patches/patch_libextavrcp_jni.py`** — patches `libextavrcp_jni.so` (C2a/b in `activateConfig_3req`; C3a/b in `getCapabilitiesRspNative`). Hardcodes `g_tg_feature=0x0e`, `sdpfeature=0x23`; raises GetCapabilities event-list cap 13→14.
- **`src/patches/patch_libextavrcp.py`** — single AVRCP version constant patch (C4: `0x0103 → 0x0104` at `0x002e3b`).
- **`src/patches/patch_y1_apk.py`** — smali patcher for the Y1 music player APK (Artist→Album navigation via Intent routing). Uses androguard + apktool; preserves original signatures for system-app deployment.
- **`src/patches/patch_adbd.py`** — *unwired since v1.7.0; historical record only.* H1/H2/H3 byte patches against `/sbin/adbd` (caused "device offline" on hardware in every revision tried).
- **`src/patches/patch_bootimg.py`** — *unwired since v1.7.0; historical record only.* Format-aware boot.img cpio patcher; invoked `patch_adbd.patch_bytes()`.
- **`src/su/`** — setuid-root `su` source (`su.c`, `start.S`, `Makefile`). Built via `cd src/su && make` → `src/su/build/su`. ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK. Installed at `/system/xbin/su` (mode 06755, root:root) by `--root`.
- **`innioasis-y1-fixes.bash`** — entry point. Takes `rom.zip` (MD5-validated against `KNOWN_FIRMWARES`), extracts `system.img`, mounts it, dispatches each `--flag` to its patcher (auto-extract → patch → write-back, idempotent), and flashes via mtkclient.

### Reference Files

- `reference/3.0.2/` – Manually-patched system files for firmware version 3.0.2
  - `system/build.prop` – Build properties
  - `system/etc/bluetooth/` – Bluetooth configuration files

## What Gets Patched

### APK Changes (patch_y1_apk.py)

Two bytecode patches and one scope-related patch are applied to the Y1 music player DEX:

**Patch A – ArtistsActivity.confirm():**
- Replaces the direct song list navigation with an Intent launch to AlbumsActivity
- Passes the selected artist name via the `"artist_key"` Intent extra

**Patch B – AlbumsActivity.initView():**
- Reads the `"artist_key"` Intent extra
- Calls `SongDao.getSongsByArtistSortByAlbum()` to fetch the artist's albums sorted by title
- Deduplicates and displays albums with cover art before drilling down to songs
- Falls back to standard album list view if no artist is specified

**Patch C – Y1Repository:**
- Makes the `songDao` field public (required for DEX bytecode access)
- Bypasses Kotlin compiler-generated accessors which fail on older Dalvik VMs (API 17)

### Firmware Changes (innioasis-y1-fixes.bash)

**Files Deployed:**
- `mtkbt.patched` – Patched Bluetooth daemon (AVRCP 1.4 SDP advertisement)
- `MtkBt.odex.patched` – Patched ODEX (`getPreferVersion()` returns 14)
- `libextavrcp_jni.so.patched` – Patched JNI library: **C2a/C2b** hardcode `g_tg_feature=14` and `sdpfeature=0x23` in `activateConfig_3req` (bypass bitmask logic); **C3a/C3b** raise the GetCapabilities EventList cap from 13 → 14 in `getCapabilitiesRspNative` so a 1.4-capable response can be served
- `libextavrcp.so.patched` – Patched AVRCP library (version constant `0x0103` → `0x0104`)
- `com.innioasis.y1_3.0.2-patched.apk` – Patched music player
- `Y1MediaBridge.apk` – Additional media integration
- `/system/xbin/su` – Setuid-root escalator (mode 06755, root:root) installed by `--root`. Built from `src/su/su.c` + `src/su/start.S` via `arm-linux-gnu-gcc`; ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK

**Configuration Changes (`--adb`):**
- `persist.service.adb.enable=1`
- `persist.service.debuggable=1`

**Configuration Changes (`--bluetooth`):**
- `persist.bluetooth.avrcpversion=avrcp14`
- `ro.bluetooth.class=2098204`
- `ro.bluetooth.profiles.a2dp.source.enabled=true`
- `ro.bluetooth.profiles.avrcp.target.enabled=true`
- audio.conf: `Enable=Source,Control,Target`, `Master=true`
- Clears Bluetooth device blacklists (auto_pairing.conf, blacklist.conf)

**Other Changes:**
- Removes bloatware APKs (`--remove-apps`): ApplicationGuide, BackupRestoreConfirmation, BasicDreams, etc.

## Requirements

### For patch_mtkbt.py / patch_mtkbt_odex.py / patch_libextavrcp_jni.py / patch_libextavrcp.py

- Python 3.8 or later
- No third-party dependencies (stdlib only)

### For patch_y1_apk.py

- Python 3.8 or later
- Java 11 or later (for apktool's smali assembler)
- androguard: `pip install androguard`
- apktool (downloaded automatically if not found)

### For innioasis-y1-fixes.bash

- Bash 4+
- macOS or Linux (file size calculations use `wc -c` for cross-platform compatibility)
- `sudo` access (for mounting and modifying system.img)
- `md5sum` (Linux) or `md5 -q` (macOS) for stock-firmware validation — both come pre-installed on the respective OSes
- `--artifacts-dir` parameter pointing to a directory containing:
  - `rom.zip` – Official Innioasis Y1 OTA archive (required for any patch flag). MD5-validated against the `KNOWN_FIRMWARES` manifest. The bash extracts `system.img` from this zip on demand.
  - `Y1MediaBridge.apk` – Externally-built integration APK (required for `--avrcp` flag — this is *not* derived from the OTA, so it must be staged separately and is not MD5-validated)
- `unzip` for extracting `system.img` from `rom.zip` (pre-installed on virtually all Linux distros and macOS)
- Python 3.8+, Java 11+ (only if `--music-apk` is set — apktool is downloaded by `patch_y1_apk.py` on first invocation)
- androguard: `pip install androguard` (only if `--music-apk` is set)
- mtkclient 2.1.4.1 installed at `/opt/mtkclient-2.1.4.1`
- `simg2img` (only if a future firmware bundles a sparse `system.img` inside its `rom.zip` — v3.0.2 bundles a raw one, so this is currently unused; install instructions in [Step 1](#step-1-stage-artifacts))
- For `--root` only: prebuilt `src/su/build/su` (run `cd src/su && make` once). The Makefile uses `arm-linux-gnu-gcc` from EPEL — install via `sudo dnf install -y epel-release && sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make` on Rocky/Alma/RHEL/Fedora, or the equivalent `gcc-arm-linux-gnueabi` package on Debian/Ubuntu.

## Usage

### Step 1: Stage artifacts

Gather the following files in a directory of your choice (e.g., `/home/user/y1-patches/`). As of v1.6.0 of `innioasis-y1-fixes.bash`, the firmware input is the official OTA `rom.zip` directly — the bash extracts `boot.img` and `system.img` from it on demand. No pre-patched `*.patched` files are required either; stock binaries are extracted from the mounted `system.img`, patched in-place, and written back.

- `rom.zip` (required for any patch flag). The official Innioasis Y1 OTA archive. The bash MD5-validates it against the `KNOWN_FIRMWARES` manifest (see "[Stock Firmware Manifest](#stock-firmware-manifest)" below), then extracts `system.img` into a tempdir and cross-verifies its MD5 against the manifest.
- `Y1MediaBridge.apk` (required for `--avrcp`). The only patched-style artifact the user has to supply — an externally-built integration APK from the [Y1MediaBridge](../Y1MediaBridge/) project, not derived from the OTA. Not MD5-validated.

**System.img sparse handling.** If a future firmware bundles a sparse `system.img` inside its `rom.zip`, the bash auto-de-sparses via `simg2img` after extraction (the manifest hash is always for the raw representation). v3.0.2's bundled `system.img` is raw, so `simg2img` is not invoked for that build. If you do need it, install via:
- Debian/Ubuntu: `sudo apt install android-sdk-libsparse-utils`
- Arch: `sudo pacman -S android-tools`
- Fedora: `sudo dnf install android-tools`
- RHEL/Rocky/Alma 8+: `sudo dnf install epel-release && sudo dnf install android-tools`
- macOS (Homebrew): `brew install simg2img`

### Step 2 (optional): Run patch scripts manually for inspection

The byte patchers and the smali patcher can be run standalone if you want to inspect the patched output before committing to a flash. Each script verifies the input MD5, checks patch sites before and after, and refuses to write output if anything is unexpected.

All patch scripts now live under `src/patches/`; the su build under `src/su/`. Run the patchers from `src/patches/` so their `output/` and `_patch_workdir/` end up there (the bash always does this; for manual invocation it's a convention).

```bash
# Music player APK
( cd src/patches && python3 patch_y1_apk.py path/to/com.innioasis.y1_3.0.2.apk )    # → src/patches/output/com.innioasis.y1_3.0.2-patched.apk

# Bluetooth binaries (each takes the stock binary extracted from system.img)
( cd src/patches && python3 patch_mtkbt.py           mtkbt )                        # → src/patches/output/mtkbt.patched
( cd src/patches && python3 patch_mtkbt_odex.py      MtkBt.odex )                   # → src/patches/output/MtkBt.odex.patched
( cd src/patches && python3 patch_libextavrcp_jni.py libextavrcp_jni.so )           # → src/patches/output/libextavrcp_jni.so.patched
( cd src/patches && python3 patch_libextavrcp.py     libextavrcp.so )               # → src/patches/output/libextavrcp.so.patched

# adbd (extract /sbin/adbd from boot.img ramdisk first) — NOT WIRED INTO THE BASH (see warning in src/patches/patch_adbd.py)
( cd src/patches && python3 patch_adbd.py            adbd )                         # → src/patches/output/adbd.patched

# su (build the setuid-root escalator for /system/xbin/su; consumed by --root in v1.8.0)
cd src/su && make && cd ../..                                                       # → src/su/build/su
```

These artifacts are not consumed by `innioasis-y1-fixes.bash` — they're for manual inspection / development. The bash invokes the same patch scripts under the hood and discards the temp files. **Notes:**
- `src/patches/patch_adbd.py` and `src/patches/patch_bootimg.py` are kept as historical record but are *not* invoked by the bash in v1.7.0+; their output causes "device offline" on hardware (see warning banner in each script's docstring).
- `src/su/build/su` *is* consumed by the bash's `--root` flag (v1.8.0+); the binary must exist at that path before `--root` runs.

### Step 3: Apply Firmware Patches

```bash
chmod +x innioasis-y1-fixes.bash
./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts [OPTIONS]
```

**Available options:**
- `--adb` – Enable ADB debugging via build.prop
- `--avrcp` – Auto-extract and patch the AVRCP 1.4 binaries from system.img (`mtkbt`, `MtkBt.odex`, `libextavrcp.so`, `libextavrcp_jni.so`) via the corresponding `patch_*.py` scripts, write them back into the mount, and additionally install the externally-built `Y1MediaBridge.apk`. **Requires only `Y1MediaBridge.apk` in `--artifacts-dir`** — the four BT binaries are auto-extracted.
- `--bluetooth` – Configure Bluetooth settings and build.prop Bluetooth entries
- `--music-apk` – Auto-extract and patch the Y1 music player APK from system.img (Artist→Album navigation via smali patches by `patch_y1_apk.py`), then write it back. **No pre-staged APK required** — extracted from the mount.
- `--remove-apps` – Remove unnecessary APK files
- `--root` – Install the prebuilt `src/su/build/su` setuid-root binary at `/system/xbin/su` (mode 06755, root:root). Run `cd src/su && make` once before first use. Post-flash, root is obtained via `adb shell /system/xbin/su` (or `/system/xbin/su -c "<cmd>"`).
- `--all` – Apply all patches including `--root`

**Example:**
```bash
./innioasis-y1-fixes.bash --artifacts-dir /home/user/y1-patches --bluetooth --music-apk --remove-apps
```

The script will:
1. Copy and mount system.img as a working copy
2. Apply selected patches
3. Unmount and generate the patched system image

**Output:**
- `system-3.0.2-devel.img` – Patched system image

### Step 4: Flash Firmware

Use mtkclient to flash the patched image back to the device.

## Deployment Notes

### APK Deployment

⚠️ **Important:** The patched APK must be deployed directly to `/system/app/` on the device filesystem, **not** via ADB install or PackageManager.

The original META-INF signature block is retained from the stock APK. While stale (not re-signed), it satisfies PackageManager's requirement for a parseable signature block. Signature verification is bypassed when deploying via the filesystem during boot.

**Valid Deployment Methods:**

**Option A – ADB Push (requires root/remounted /system):**
```bash
adb root
adb remount
adb push com.innioasis.y1_3.0.2-patched.apk /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb shell chmod 644 /system/app/com.innioasis.y1/com.innioasis.y1.apk
adb reboot
```

**Option B – Firmware Flash:**
Replace the APK inside the firmware image using this toolkit's bash script.

## Verified Against

- Firmware: Innioasis Y1 v3.0.2
- Device: Innioasis Y1 media player
- Platform: MTK (MediaTek) ARM chipset with Dalvik VM (API 17)

## Stock Firmware Manifest

Known stock-firmware MD5s recognised by `innioasis-y1-fixes.bash`'s `KNOWN_FIRMWARES` manifest. The bash validates the staged inputs against this table and uses the matched entry for all version-dependent filename construction. To enrol a new firmware build, add a new row to the array (same five-field schema).

| Version | rom.zip (input) | system.img (raw, extracted) | boot.img (in zip, not extracted by bash since v1.7.0) | Music APK basename in `app/` |
|---|---|---|---|---|
| **3.0.2** | `82657db82578a38c6f1877e02407127a` | `473991dadeb1a8c4d25902dee9ee362b` | `1f7920228a20c01ad274c61c94a8cf36` | `com.innioasis.y1_3.0.2.apk` |

Stock file sizes for reference: `rom.zip` 259,502,414 bytes (the official OTA, **the only firmware artifact the user stages**), `system.img` 681,574,400 bytes (raw ext4 — extracted from rom.zip on demand), `boot.img` 4,706,304 bytes (lives inside rom.zip but is not extracted by the bash in v1.7.0+, since the `--root` flag that consumed it has been removed; the MD5 is kept in the manifest as documentation in case `--root` is reintroduced later). Note that v3.0.2's bundled `system.img` is raw ext4; if a future firmware bundles a sparse `system.img` instead, the bash's auto-simg2img step will de-sparse it before the cross-check MD5 (the manifest hash is always the raw representation).

## Status (2026-05-03)

End-of-investigation state:

- **All four binary patch scripts produce verified output.** SDP layer is on the wire (sdptool confirms AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033). Java layer initializes correctly for AVRCP 1.4 post-F1/F2. Y1MediaBridge bridges IBTAvrcpMusic ↔ IMediaPlaybackService correctly.
- **Cardinality:0 persists across all three known-good 1.4 controllers** (car / Sonos Roam / Samsung TV). No peer ever sends `REGISTER_NOTIFICATION`. The shipping 11-patch mtkbt build at MD5 `d47c904063e7d201f626cf2cc3ebd50b` is the last known-working state.
- **Gate location refined:** post-E8 testing showed only msg_ids 505 (CONNECT_CNF) and 506 (connect_ind) ever arrive — no `op_code=4` (GetCapabilities) message reaches any of the three op_code=4 dispatchers (`0x3060c`, `0x30708`, `0x3096c`). The gate is upstream of the dispatcher table itself, somewhere in mtkbt's L2CAP→AVCTP RX path or the per-connection feature-negotiation logic (`bws:0 tg_feature:0 ct_featuer:0` in CONNECT_CNF suggests negotiation fails on the daemon side before GetCapabilities can even be dispatched).
- **Diagnostic options exhausted within session constraints.** Two attempts at xlog→logcat redirect (G1/G2 with and without NULL guard) both broke Bluetooth — once via SIGSEGV at NULL, once via socket-bind failure / logd-flood timeout. Path closed without root or daemon-side tooling. Surgical instrumentation at a few specific sites is the only remaining static-analysis option but each new site is its own potential crash vector.
- **What would unblock further progress:** HCI snoop access (root) to see what the peer actually sends after CONNECT_CNF, OR daemon-side `__xlog_buf_printf` capture (special tooling), OR running mtkbt under a debugger with a known-good controller at hand.
- **New static-analysis target (2026-05-03):** `result:4096` (= `0x1000`) in `MSG_ID_BT_AVRCP_CONNECT_CNF` from a fresh test log (peer `38:42:0B:38:A3:3E`). The result field on a clean AVRCP connect should be `0`. Non-zero result on a successfully-connected channel suggests mtkbt is reporting the connection as accepted-but-degraded — strongest single static-investigation lead for the post-root pass.
- **Root attempt closed 2026-05-03 (post-Status):** `--root` was added in v1.3.0–v1.6.0 with byte patches H1/H2/H3 in `/sbin/adbd`. Both attempted approaches (NOP the three `blx setgroups/setgid/setuid` calls; change their argument values from 2000/11 to 0) caused "device offline" on hardware — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. Without on-device visibility (logcat / dmesg / strace, all of which require working ADB), we couldn't diagnose the root cause statically. `--root` was removed from the bash in v1.7.0; standalone `patch_adbd.py` and `patch_bootimg.py` kept as historical record.
- **Root reattempted 2026-05-03 (v1.8.0):** new mechanism — install a minimal setuid-root `su` binary at `/system/xbin/su` (mode 06755, root:root) instead of patching `/sbin/adbd`. Stock adbd remains at uid 2000 (shell), ADB protocol comes up cleanly, and root is obtained post-flash by running `adb shell /system/xbin/su`. The binary is a ~900-byte direct-syscall ARM-EABI ELF compiled from `src/su/su.c` + `src/su/start.S` in this repo (no libc, no third-party manager APK). Pending hardware verification; if it works, HCI snoop / `__xlog_buf_printf` capture / `gdbserver` attach become reachable for the `result:4096` investigation.

See [INVESTIGATION.md](INVESTIGATION.md) for the full investigation narrative including refuted hypotheses and the trace history.

## Changes

See [CHANGELOG.md](CHANGELOG.md) for the version history. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/); entries are 1:1 with the git log.

## Author

Sean Halpin ([github.com/SeanathanVT](https://github.com/SeanathanVT))

## License

This project is licensed under the **GNU General Public License v3.0** (GPLv3).

See [LICENSE](LICENSE) file for full details.

You are free to use, modify, and distribute this software under the terms of the GPLv3, which requires that any derivative work also be released under GPLv3.
