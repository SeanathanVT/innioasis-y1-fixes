# Innioasis Y1 Firmware Fixes

A comprehensive patching toolkit for the Innioasis Y1 media player (firmware 3.0.2) that fixes Bluetooth AVRCP functionality, improves the media player UI navigation, and enables ADB debugging.

## Overview

This project provides tools to patch and enhance the Innioasis Y1 firmware with:

- **Bluetooth AVRCP 1.4 Support** – Forces AVRCP 1.4 advertisement across all BT stack layers (daemon, ODEX, JNI library, core library)
- **Artist→Album Navigation** – Improves media player UX by showing album cover art after artist selection instead of a flat song list
- **System Configuration** – Enables ADB debugging and optimizes Bluetooth settings
- **APK Patching** – Patches the system music player APK at the bytecode level using smali assembly

## Patch ID Legend

Patches are referenced throughout this README, in `INVESTIGATION.md`, and in the changelog by short IDs. The full mapping:

| ID(s) | Binary | Site / effect |
|---|---|---|
| **B1, B2, B3** | `mtkbt` | AVCTP version `0x00 → 0x03` (1.0 → 1.3) in three SDP descriptor groups: Groups 1&2 TG ProtocolDescList (`0x0eba6d`), Group 3 CT ProtocolDescList (`0x0eba37`), Group 1 AdditionalProtocol/browsing (`0x0eba25`). AVRCP 1.4 requires AVCTP 1.3. |
| **C1, C2, C3** | `mtkbt` | AVRCP version → 1.4 in three ProfileDescList entries: `0x0eba4b` (entry[23], 1.0→1.4), `0x0eba58` (entry[18], 1.0→1.4), `0x0eba77` (entry[13], 1.3→1.4). |
| **A1** | `mtkbt` | Runtime SDP MOVW immediate at `0x38BFC`: `MOVW r7,#0x0301 → MOVW r7,#0x0401` — belt-and-suspenders against the static SDP template. |
| **D1** | `mtkbt` | NOP the registration guard at `0x38C6C` (`BNE → NOP`). Without this, the AVRCP TG SDP struct is built but never linked into mtkbt's live registry; mtkbt silently discards inbound GetCapabilities. |
| **E3, E4** | `mtkbt` | TG SupportedFeatures bitmask: Group 2 (served) `0x0001 → 0x0033` at `0x0eba5b`; Group 1 (defense-in-depth) `0x0021 → 0x0033` at `0x0eba4e`. `0x33` = Cat1 + Cat2 + PAS + GroupNav (AVRCP 1.4 baseline). |
| **E8** | `mtkbt` | NOP the `bge #0x30688` at `0x3065e` in fn `0x3060c` (op_code=4 dispatcher slot 0). Forces classification through the AVRCP 1.3/1.4 init path regardless of `[conn+0x149]`'s sign bit. **Empirically inert** for our peers (gate is upstream of the dispatcher table); kept as a verified-correct probe. |
| **E5, E7a, E7b** | `mtkbt` | **Removed 2026-05-02.** Tested across three known-good 1.4 controllers, no observable behavioural change — code paths not exercised at runtime for our peer state. |
| **C2a, C2b** | `libextavrcp_jni.so` | In `BluetoothAvrcpService_activateConfig_3req` at `0x375c`: hardcode `g_tg_feature = 0x0e` and `sdpfeature = 0x23`, bypassing the bitmask negotiation logic. |
| **C3a, C3b** | `libextavrcp_jni.so` | In `getCapabilitiesRspNative` (`FUN_005de8`) at `0x5e56`/`0x5e5c`: raise the GetCapabilities EventList cap from `13 → 14` so a 1.4-capable response can be served if the JNI ever receives an inbound GetCapabilities. |
| **C4** | `libextavrcp.so` | Single AVRCP version constant at `0x002e3b`: `0x0103 → 0x0104` (1.3 → 1.4). |
| **F1** | `MtkBt.odex` | At `0x3e0ea`: `getPreferVersion()` returns `14` (AVRCP 1.4) instead of `10` (BlueAngel internal code for AVRCP 1.3). |
| **F2** | `MtkBt.odex` | At `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false`. Fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts. |
| **G1, G2** | `mtkbt` | **Attempted and reverted 2026-05-02 / 2026-05-03.** Diagnostic `__xlog_buf_printf → __android_log_print` redirect (Thumb thunk at `0x675c0`, ARM PLT at `0xb408`). Crashed mtkbt at NULL fmt; even with NULL guard, BT framework couldn't enable. Path closed without root or daemon-side tooling. |
| ~~**H1, H2, H3**~~ | `/sbin/adbd` (in `boot.img` ramdisk) | **Tried 2026-05-03; reverted (caused "device offline").** Both attempted approaches (NOP the three `blx setgroups/setgid/setuid` calls; change their argument values from 2000/11 to 0) caused adbd-at-uid-0 to start and enumerate over USB but fail the ADB protocol handshake. Static analysis didn't find a `getuid()`-based gate or a uid==2000 compare in adbd, so the failure mode is something we can't see without on-device visibility (which we lose the moment we ship a broken adbd). `--root` flag removed from the bash in v1.7.0; standalone `patch_adbd.py` and `patch_bootimg.py` kept in the tree as historical record. Superseded in v1.8.0 by the `su` install approach. |
| **su** | `/system/xbin/su` (new file) | **Reintroduced root path, v1.8.0.** Instead of patching `adbd`, ship a minimal setuid-root `su` binary into `/system/xbin/su` (mode 06755, root:root). Stock `/sbin/adbd` stays untouched, ADB protocol comes up cleanly, and root is obtained post-flash by `adb shell /system/xbin/su`. The binary is built from `src/su/su.c` + `src/su/start.S` in this repo via `arm-linux-gnu-gcc`: ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK, no whitelist — every byte traces to GCC + the local source. See [`src/su/`](src/su/) and the `--root` section under [Step 3](#step-3-apply-firmware-patches). |

The "Final state" in `INVESTIGATION.md` and the "Status (2026-05-03)" section below summarise which IDs ship in the current build.

## Contents

### Main Scripts

> **Layout note (v1.8.x):** this repo is organized as a small monorepo. The bash entry-point (`innioasis-y1-fixes.bash`) stays at the repo root and dispatches into the source trees under `src/`:
>
> - [`src/su/`](src/su/) — minimal setuid-root `su` for `/system/xbin/su` (consumed by `--root`)
> - [`src/patches/`](src/patches/) — byte/smali patchers (`patch_*.py`)
> - [`src/Y1MediaBridge/`](src/Y1MediaBridge/) — Android service app (the `Y1MediaBridge.apk` consumed by `--avrcp`); imported via `git subtree` so its commit history is preserved in this repo's log
>
> See the v1.8.2 / Y1MediaBridge-import entries under [Changes](#changes) for path-mapping details.

- **`patch_mtkbt.py`**
  - Patches the stock `mtkbt` Bluetooth daemon binary for AVRCP 1.4
  - **Eleven patches applied:**
    - **B1** `0x0eba6d`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Groups 1 & 2 shared ProtocolDescList (TG control channel — what `sdptool` sees)
    - **B2** `0x0eba37`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 3 CT ProtocolDescList
    - **B3** `0x0eba25`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 1 AdditionalProtocol (browsing channel descriptor)
    - **C1** `0x0eba4b`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[23]
    - **C2** `0x0eba58`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[18] (served by SDP last-wins)
    - **C3** `0x0eba77`: `0x03` → `0x04` — AVRCP 1.3 → 1.4 LSB in ProfileDescList entry[13]
    - **A1** `0x38BFC`: `40 f2 01 37` → `40 f2 01 47` — `MOVW r7,#0x0301` → `MOVW r7,#0x0401` (runtime SDP struct, belt-and-suspenders)
    - **D1** `0x38C6C`: `03 d1` → `00 bf` — `BNE 0x38C76` → `NOP` — bypasses registration guard so the AVRCP TG SDP struct is always linked into mtkbt's live registry (see note below)
    - **E3** `0x0eba5b`: `0x01` → `0x33` — Group 2 TG SupportedFeatures (served): `0x0001` → `0x0033` (Cat1 + Cat2 + PAS + GroupNav — AVRCP 1.4 baseline matching AOSP Bluedroid)
    - **E4** `0x0eba4e`: `0x21` → `0x33` — Group 1 TG SupportedFeatures (defense-in-depth): `0x0021` → `0x0033`
    - **E8** `0x3065e`: `13 da` → `00 bf` — `BGE 0x30688` → `NOP` in fn `0x3060c` (op_code=4 dispatcher slot 0). Forces every classification through the AVRCP 1.3/1.4 init path (`b.w 0x2fd34`) regardless of the sign bit of `[conn+0x149]`. See E8 note below.
  - The descriptor table contains three service record groups. Groups 1 & 2 are TG (AV Remote Target 0x110c); Group 3 is CT (AV Remote 0x110e). All AVCTP version bytes were stock 1.0; AVRCP 1.4 requires AVCTP 1.3. All three ProfileDescList entries are patched to AVRCP 1.4 (last-wins semantics).
  - **D1 note:** The SDP init function at `0x38AB0` builds the TG struct, then gates the final `STR r3,[r1]` registration write behind `CMP r0,r5 / BNE` where r5=`0x111F`. r0 is never `0x111F`, so without D1 the registration never completes and mtkbt silently discards incoming GetCapabilities commands.
  - **E3/E4 note:** Wire-confirmed via `sdptool browse` after D1 was live: `AttrID=0x0311` IS served inside the AVRCP TG record (UUID 0x110c), but the served value is `0x0001` (Cat1 only — Group 2 wins the merge). 1.4 controllers see ProfileVersion=1.4 with a feature bitmask consistent with 1.0, treat the advertiser as inconsistent, and skip `REGISTER_NOTIFICATION` (which is why earlier builds had `cardinality:0` even with C3a/C3b applied). Browsing bit (6) is deliberately omitted because `AdditionalProtocolDescriptorList` (0x000d) is in Group 1 only and isn't on the wire after the merge — claiming Browsing without serving the descriptor would re-introduce the same inconsistency.
  - **E8 note:** Trace #1g resolved the indirect-call graph and identified three op_code=4 dispatchers reached via the 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`: fn `0x3060c` (slot 0), fn `0x30708` (slot 1), fn `0x3096c` (slot 2). Of these only fn `0x3060c` has a clean single-instruction high-bit gate on `[conn+0x149]`: `ldrsb.w r0,[r4,#0x149]; cmp r0,#0; bge #0x30688`. The bge skips the 1.3/1.4 init path when the version byte's high bit is clear; NOPing it forces the init path unconditionally. Brute-forcing the analogous fix to the other two slots was considered and rejected: fn `0x30708` reads the byte unsigned and masks `&0x7f` (no high-bit gate exists; failure exits gate on a multi-byte state-machine on `[conn+0x5d0]`); fn `0x3096c`'s analogous BNE→B (the old E5 patch at `0x309ec`) was already empirically tested in earlier sessions and removed as inert. E8 ships as a low-risk single-instruction probe; tested 2026-05-02 and observed inert (cardinality:0 persists, no `op_code=4` GetCapabilities messages reach the dispatchers — the gate is upstream of the dispatcher table entirely).
  - Stock MD5: `3af1d4ad8f955038186696950430ffda` — Output MD5: `d47c904063e7d201f626cf2cc3ebd50b`

- **`patch_mtkbt_odex.py`**
  - Patches `MtkBt.odex` with two fixes:
    1. `getPreferVersion()` returns 14 (AVRCP 1.4) instead of 10 (BlueAngel internal code for AVRCP 1.3) (at `0x3e0ea`)
    2. `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false` (at `0x03f21a`) — fixes BT toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts
  - Recomputes the DEX adler32 checksum embedded in the ODEX header
  - Input: stock `MtkBt.odex` (md5 `11566bc23001e78de64b5db355238175`) → Output: `output/MtkBt.odex.patched` (md5 `acc578ada5e41e27475340f4df6afa59`)

- **`patch_libextavrcp_jni.py`**
  - Patches `libextavrcp_jni.so` to force `g_tg_feature=14` (AVRCP 1.4) and `sdpfeature=0x23`, and raises the GetCapabilities event-list cap in `getCapabilitiesRspNative` from 13 to 14 so a 1.4-capable response can be served if the JNI ever receives an inbound GetCapabilities request
  - 4 ARM Thumb2 instruction overwrites: 2 in `BluetoothAvrcpService_activateConfig_3req` at 0x375c (hardcode tg_feature/sdpfeature, bypassing bitmask logic), 2 in `getCapabilitiesRspNative` (`FUN_005de8`) at 0x5e56/0x5e5c (raise the EventList cap from 13 to 14 — *not* the CONNECT_CNF handler, which lives at 0x62EA and does not gate on tg_feature)
  - The bitmask bypass at 0x375c complements (not replaces) the ODEX `getPreferVersion` patch — both are required for reliable 1.4 negotiation. Verified global addresses: `g_tg_feature` @ 0xD29C, `g_ct_feature` @ 0xD004.
  - **Empirical note:** in testing across three known-good 1.4 controllers (car, Sonos Roam, Samsung TV), `getCapabilitiesRspNative` is never observed firing — mtkbt does not dispatch inbound GetCapabilities to the JNI for any of them. C3a/C3b are correctly applied on-binary but their effect cannot be observed; the cardinality:0 gate is upstream in mtkbt's AVCTP receive path.
  - Input: stock `libextavrcp_jni.so` (md5 `fd2ce74db9389980b55bccf3d8f15660`) → Output: `output/libextavrcp_jni.so.patched` (md5 `6c348ed9b2da4bb9cc364c16d20e3527`)

- **`patch_libextavrcp.py`**
  - Patches `libextavrcp.so` to advertise AVRCP 1.4 instead of 1.3
  - Single patch: version constant at 0x002e3b changed from `0x0103` (1.3) to `0x0104` (1.4)
  - Input: stock `libextavrcp.so` → Output: `output/libextavrcp.so.patched`

- **`innioasis-y1-fixes.bash`** (v1.8.0)
  - Accepts mandatory `--artifacts-dir` parameter for artifact location.
  - Supports selective patching with individual flags: `--adb`, `--avrcp`, `--bluetooth`, `--music-apk`, `--remove-apps`, `--root`.
  - Takes the official OTA `rom.zip` as the firmware input (since v1.6.0). MD5-validates `rom.zip` against the `KNOWN_FIRMWARES` manifest, extracts `system.img` from the zip into a tempdir, cross-verifies its MD5, auto-de-sparses via `simg2img` if needed, and mounts it as a loop device.
  - **Auto-extract / auto-patch (v1.4.0)**: stock binaries (`mtkbt`, `MtkBt.odex`, `libextavrcp.so`, `libextavrcp_jni.so`, the music APK) are extracted from the mounted system.img, fed through their respective `patch_*.py`, and written back in-place. No pre-staged `*.patched` files required. Two helpers (`patch_in_place_bytes`, `patch_in_place_y1_apk`); idempotent — re-running detects already-patched files.
  - **Stock-firmware MD5 validation (v1.5.0)**: a `KNOWN_FIRMWARES` manifest near the top of the script holds (version, system.img md5, boot.img md5, rom.zip md5, music-APK basename) tuples. The matched version drives all version-dependent filenames.
  - Configures `build.prop` and Bluetooth settings (`--adb`, `--bluetooth`).
  - Removes unnecessary bloatware APKs (`--remove-apps`).
  - **`--root` (re-added v1.8.0)** installs the prebuilt `src/su/build/su` setuid-root binary at `/system/xbin/su` (mode 06755, root:root). `/sbin/adbd` is untouched — root is obtained post-flash by `adb shell /system/xbin/su`. Requires `cd src/su && make` once before first use; the bash exits with a clear error pointing at `make` if the prebuilt is missing. Replaces the v1.3.0–v1.6.0 approach of byte-patching `/sbin/adbd` in the boot.img ramdisk, which caused "device offline" on hardware. Historical scripts `patch_adbd.py` and `patch_bootimg.py` remain in the tree as documentation but are not invoked.

- **`patch_adbd.py`**
  - Patches stock `/sbin/adbd` (extracted from the boot.img ramdisk) so it does not drop privileges to AID_SHELL on startup. After flashing, `adb shell` returns a uid 0 shell directly.
  - Three Thumb-2 patches at vaddr 0x94b8 (file_off 0x14b8) — the drop_privileges block. Each patch changes the *argument value* of the three calls from `2000` (AID_SHELL) / `11` (gid count) to `0`, so the syscalls execute and all bionic bookkeeping runs but the process ends up at uid=0/gid=0:
    - **H1** at file_off `0x14b8`: `0b 20` → `00 20` — `movs r0, #0xb` → `movs r0, #0` (setgroups count 11 → 0; clears supplementary groups)
    - **H2** at file_off `0x14c6`: `4f f4 fa 60` → `4f f0 00 00` — `mov.w r0, #0x7d0` → `mov.w r0, #0` (setgid arg 2000 → 0)
    - **H3** at file_off `0x14d4`: `4f f4 fa 60` → `4f f0 00 00` — `mov.w r0, #0x7d0` → `mov.w r0, #0` (setuid arg 2000 → 0)
  - **Why patch the binary instead of relying on `default.prop`?** This OEM adbd has stripped the standard `should_drop_privileges()` gating: `strings adbd` returns ZERO references to `ro.secure`, the drop block at 0x94b8 has no preceding conditional, and the privilege drop runs unconditionally on every adbd startup. Setting `ro.secure=0`/`ro.debuggable=1`/`ro.adb.secure=0` in default.prop is therefore inert for the adbd-as-root question — confirmed empirically 2026-05-03 (`adb shell id` returned `uid=2000(shell)` with all three properties correctly set).
  - **`adb root` is also actively harmful on the un-patched binary.** adbd accepts the `root:` request (ro.debuggable=1 passes the permission check), sets `service.adb.root=1` and exits to be respawned by init. The respawned adbd hits the same unconditional drop_privileges path and ends up at uid 2000 again — but the self-restart cycle requires a USB rebind that stock MTK adbd handles poorly, and the host loses the device until reboot. After the H1/H2/H3 patches, adbd is already root at boot, so `adb root` is a no-op (adbd reports "already running as root") and the USB cycle never happens.
  - **Why arg-zero, not NOP-the-blx (history).** An earlier revision NOPed the three `blx` calls outright (each 4-byte BLX replaced with `movs r0, #0; nop`). On hardware that produced "device offline" — adbd starts and the USB endpoint comes up, but the protocol handshake never completes. The bionic setuid wrapper at `0x19418` does `bl 0x27b30` *before* reaching the actual `mov r7, #0xd5; svc 0` syscall stub at `0x31a70`, doing capability bounding-set and thread-credential bookkeeping that downstream adbd code depends on. Skipping that wrapper entirely produces a process that's technically uid 0 but with inconsistent capabilities/credentials. The arg-zero approach keeps every syscall and bionic wrapper intact — `setuid(0)` when EUID is already 0 is a no-op that runs all the same bookkeeping, just without changing the actual UID. Same for `setgid(0)`.
  - Stock MD5: `9e7091f1699f89dc905dee3d9d5b23d8` (size 223,132) — Output MD5: `9eeb6b3bef1bef19b132936cc3b0b230` (same size).

- **`src/su/`** (root, v1.8.0)
  - Source for a minimal setuid-root `su` binary installed at `/system/xbin/su` by the bash's `--root` flag. Replaces the H1/H2/H3 adbd byte patches that broke ADB protocol on hardware (see `patch_adbd.py` below).
  - **`src/su/su.c`** — direct ARM-EABI syscall implementation, no libc dependency. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", ...)`. Supports three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), and `su <prog> [args...]` (exec-passthrough).
  - **`src/su/start.S`** — ~10-line ARM Thumb-2 entry stub; extracts argc/argv/envp from the ELF process-start stack layout, calls `main`, exits with main's return value via `__NR_exit`.
  - **`src/su/Makefile`** — cross-compile via `arm-linux-gnu-gcc` (EPEL package `gcc-arm-linux-gnu`). `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft`; output ~900 bytes, statically linked, no `NEEDED` entries. Install toolchain on Rocky/Alma/RHEL/Fedora: `sudo dnf install -y epel-release && sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make`.
  - **No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported; no manager APK; no whitelist. Trade-off: any process that can exec `/system/xbin/su` becomes root, which is acceptable for a single-user research device but not for a consumer ROM.
  - **Build:** `cd src/su && make` produces `src/su/build/su`. The bash references this prebuilt path; if missing, `--root` exits with a clear error pointing at `make`. Idempotent (re-running `make` is a no-op if sources are unchanged).
  - **Deploy:** the bash's `--root` flag does `install -m 06755 -o root -g root src/su/build/su /system/xbin/su` against the mounted system.img. Post-flash: `adb shell /system/xbin/su -c "id"` → `uid=0(root)`.
  - **Purpose (2026-05-03):** unblock visibility into mtkbt's `__xlog_buf_printf` ring buffer, btsnoop, and live `gdbserver` attach — required to pin down which branch sets `result=0x1000` in `MSG_ID_BT_AVRCP_CONNECT_CNF`.

- **`patch_bootimg.py`** *(unwired since v1.7.0; kept as historical record)*
  - Patches stock `boot.img` ramdisk so `adb shell` returns a uid 0 shell after flashing. Two changes are applied to the ramdisk in-place inside the gzipped cpio (no extract/repack of device nodes):
    1. **`/sbin/adbd`**: applies the H1/H2/H3 byte patches above (delegated to `patch_adbd.patch_bytes()`). This is the load-bearing change — the OEM adbd ignores property-driven privilege gating, so the binary itself must be patched.
    2. **`default.prop`**: edits as belt-and-suspenders for any other Android subsystem that honours these properties (the patched adbd does not, but `ro.debuggable=1` still affects e.g. dumpable processes and some debug paths):
       - `ro.secure=0` (was 1)
       - `ro.debuggable=1` (was 0)
       - `ro.adb.secure=0` (appended)
  - **Format-aware:** parses the Android boot.img header, strips/repacks the MTK 512-byte `ROOTFS` ramdisk wrapper, and patches `default.prop` and `/sbin/adbd` *in-place* inside the gzipped cpio stream. Device nodes and entry order are preserved byte-for-byte (the adbd patch keeps the same file size, so cpio record offsets are unchanged).
  - Pure-Python; no `dd` / `cpio` / `mkbootimg` / `abootimg` shell dependency. The previous bash-based `--root` (removed in v1.2.0) drifted on MTK header byte counts; this implementation removes that failure mode.
  - Input: stock `boot.img` (in `--artifacts-dir`) → Output: `boot-3.0.2-devel.img` (in `--artifacts-dir`)
  - **Status:** unwired since v1.7.0 because the H1/H2/H3 adbd byte patches caused "device offline" on hardware. Superseded in v1.8.0 by the `/system/xbin/su` install approach (see `src/su/` above), which leaves `/sbin/adbd` untouched.

- **`patch_y1_apk.py`**
  - Unpacks, decompiles, and patches the Y1 music player APK at the smali level
  - Implements Artist→Album navigation via Intent-based routing
  - Preserves original APK signatures (required for system app deployment)
  - Uses androguard for DEX-level analysis and apktool for reassembly

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

## Technical References

### DEX Analysis (ArtistsActivity.confirm)

```
registers_size=5; p0=this=v4
Artist-tap branch: instructions 53-79 (isShowArtists==true, isMultiSelect==false)
switchSongSortType() call: instructions 72-73 (replaced with Intent launch)
Selected artist stored in: ArtistsActivity.artist (Ljava/lang/String;)
```

### DEX Analysis (AlbumsActivity.initView)

```
registers_size=3; p0=this=v2; locals=2 (patched to 8)
UI Resource ID: 2131820833 (0x7f110121)
getAlbumListBySort() launches async coroutine (safe to bypass with early return)
```

### Song Database Query

```sql
SELECT * FROM song
WHERE isAudiobook = 0 AND artist = ?
ORDER BY lower(pinyinAlbum)
```

Song data accessed via: `SongDao.getSongsByArtistSortByAlbum(String)`

## License

This project is licensed under the **GNU General Public License v3.0** (GPLv3).

See [LICENSE](LICENSE) file for full details.

You are free to use, modify, and distribute this software under the terms of the GPLv3, which requires that any derivative work also be released under GPLv3.
