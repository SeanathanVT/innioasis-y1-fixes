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

> **Layout note (v1.8.2):** the byte/smali patchers (`patch_*.py`) now live under [`src/patches/`](src/patches/). The bash entry-point (`innioasis-y1-fixes.bash`) stays at the repo root; it dispatches into `src/patches/` and `src/su/` as needed. See the v1.8.2 entry under [Changes](#changes) for the full path mapping.

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

- **2026-05-03** – `innioasis-y1-fixes.bash` v1.8.2: move the seven `patch_*.py` scripts (`patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp.py`, `patch_libextavrcp_jni.py`, `patch_y1_apk.py`, `patch_adbd.py`, `patch_bootimg.py`) into `src/patches/`, matching the `src/su/` pattern set in v1.8.1. Bash references updated: `${PATH_SCRIPT_DIR}/${script}` → `${PATH_SCRIPT_DIR}/src/patches/${script}` in `patch_in_place_bytes`; `cd "${PATH_SCRIPT_DIR}"` → `cd "${PATH_SCRIPT_DIR}/src/patches"` in `patch_in_place_y1_apk`; output APK lookup `${PATH_SCRIPT_DIR}/output/...` → `${PATH_SCRIPT_DIR}/src/patches/output/...`. `patch_y1_apk.py` writes its output APK to `output/` relative to CWD, so CWD-changing the patcher invocation moves the output dir too — no Python source change needed. `_patch_workdir/` (apktool scratch) likewise lands under `src/patches/_patch_workdir/`. `.gitignore` is unchanged (the existing `_patch_workdir`, `__pycache__`, `output` patterns are bare and match at any depth). No functional change to user invocation. README updated: layout note added at the top of "Main Scripts"; Step 2 (manual inspection) commands now run patchers via `( cd src/patches && python3 patch_*.py ... )`; "Notes" entries reference `src/patches/patch_*.py`.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.8.1: move `su/` → `src/su/` in anticipation of bringing additional source trees (Y1MediaBridge, the byte patchers) under a shared `src/` root for monorepo organization. The bash now references `${PATH_SCRIPT_DIR}/src/su/build/su`; the build instruction in the `--root` help text and the missing-prebuilt error become `cd src/su && make`. `.gitignore` updated. No functional change.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.8.0: reintroduce `--root` flag with a fundamentally different mechanism. Instead of patching `/sbin/adbd` in the boot.img ramdisk (the v1.3.0–v1.6.0 approach, which caused "device offline" on hardware in every revision tried), the new `--root` installs a minimal setuid-root `su` binary at `/system/xbin/su` (mode 06755, root:root). Stock `/sbin/adbd` stays untouched, so the ADB protocol handshake comes up cleanly, and root is obtained post-flash by running `adb shell /system/xbin/su`. The binary is built from `src/su/su.c` (~80 lines of C) + `src/su/start.S` (~10 lines of ARM Thumb-2 assembly) via `arm-linux-gnu-gcc` — direct ARM-EABI syscalls (`setgid`, `setuid`, `execve`, `write`, `exit`), no libc, no manager APK, no whitelist. Output is a ~900-byte statically-linked ARMv7 ELF with no dynamic dependencies; every byte traces to GCC + the local source. Toolchain install on Rocky/Alma/RHEL/Fedora: `sudo dnf install -y epel-release && sudo dnf install -y gcc-arm-linux-gnu binutils-arm-linux-gnu make`. Build with `cd src/su && make`. The bash references the prebuilt at `${PATH_SCRIPT_DIR}/src/su/build/su`; if missing, `--root` exits with a clear error pointing at `make`. `--root` is system.img-only (no boot.img extraction, no ramdisk repack); the install block does `install -m 06755 -o root -g root src/su/build/su /system/xbin/su` against the mounted system.img. Re-added to `--all`. `patch_adbd.py` and `patch_bootimg.py` remain in the tree as historical record (still unwired). **Pending hardware verification.**
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.7.0: remove `--root` flag entirely. The H1/H2/H3 byte patches in `/sbin/adbd` (both NOP-the-blx and arg-zero revisions) caused "device offline" on hardware in flash testing — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. Static analysis of adbd found no `getuid()` gate, no uid==2000 compare, no obvious capability check; the failure mode is something we can't see without on-device visibility (which we lose the moment we ship a broken adbd). Boot.img extraction, patch_bootimg invocation, and boot.img mtkclient flash all dropped from the bash. Standalone `patch_adbd.py` and `patch_bootimg.py` kept in the tree as historical record with warning banners in their docstrings; their analysis (drop_privileges block at vaddr 0x94b8, bionic syscall wrappers at 0x17038/0x1701c/0x19418, cgroup-migration helper at 0x27b30 opening `/acct/uid/<uid>/tasks`) is preserved for whoever picks the root pass back up. Re-introducing `--root` later is straightforward (re-add the boot.img extraction block + patch_bootimg invocation + boot.img flash) once a working approach is found.
- **2026-05-03** – Revise H1/H2/H3 from "NOP the blx calls" to "change the argument values from 2000 to 0". The earlier NOP-the-blx revision worked in pure dry-run (cpio round-trip + MD5 verification all green) but on hardware caused "device offline" — adbd starts and the USB endpoint enumerates, but the ADB protocol handshake never completes. Diagnosis: the bionic setuid wrapper at adbd vaddr `0x19418` does `bl 0x27b30` (capability bounding-set / thread-credential bookkeeping) *before* reaching the actual `mov r7, #0xd5; svc 0` syscall stub at `0x31a70`. NOPing the blx call entirely skips that bookkeeping and leaves the process in a state with inconsistent credentials/capabilities — uid 0 nominally but with USB-protocol-layer breakage. The new arg-zero approach changes only the immediate values: `movs r0, #0xb` → `movs r0, #0` (file_off `0x14b8`), `mov.w r0, #0x7d0` → `mov.w r0, #0` (file_offs `0x14c6` and `0x14d4`). Every syscall and every bionic wrapper still executes; `setuid(0)` / `setgid(0)` when EUID is already 0 are no-ops that run the same bookkeeping path. New patched adbd MD5: `9eeb6b3bef1bef19b132936cc3b0b230` (was `ccebb66b25200f7e154ec23eb79ea9b4`). Stock MD5 unchanged. cpio round-trip verified end-to-end against the v3.0.2 ramdisk.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.6.0: take the official OTA `rom.zip` as the primary firmware input. Users now stage just `rom.zip` + `Y1MediaBridge.apk` (no separate `boot.img`/`system.img` extraction needed). The bash MD5-validates `rom.zip` against the `KNOWN_FIRMWARES` manifest, derives the firmware version from the match, then `unzip -j -o`'s only the inner files needed by the active flags (system.img for system-affecting flags, boot.img for `--root`) into the tempdir. Each extracted file's MD5 is cross-verified against the manifest as a defensive check (rom.zip MD5 is collision-resistant so this is essentially redundant, but cheap and catches zip-extraction bugs). The sparse-detect / simg2img path still applies to the extracted system.img — v3.0.2's bundled system.img is raw, but a future firmware could bundle a sparse one. `unzip` is now a hard dependency. Help text and README updated; "Stock Firmware Manifest" table reordered to show `rom.zip` as the input and `system.img`/`boot.img` as derived. The v1.5.0 manifest format is unchanged (rom.zip md5 was already a field there); the only schema change is which field is the primary lookup key.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.5.0: replace the hardcoded `VERSION_FIRMWARE="3.0.2"` constant with stock-firmware MD5 validation. New `KNOWN_FIRMWARES` manifest holds (version, system.img md5, boot.img md5, rom.zip md5, music-APK basename) tuples. Staged `system.img` (post-simg2img if sparse) and `boot.img` (when `--root`) are MD5-validated against the manifest before any patch step runs. The matched version drives all version-dependent filenames (working-copy basenames, music-APK lookup, `patch_in_place_y1_apk`'s output path). If both system.img and boot.img are processed they must resolve to the same firmware version; mismatched versions exit with a clear error. Unknown input bails the script and dumps the full manifest. v3.0.2 enrolled as the only known build (system.img raw md5 `473991dadeb1a8c4d25902dee9ee362b`, boot.img md5 `1f7920228a20c01ad274c61c94a8cf36`, rom.zip md5 `82657db82578a38c6f1877e02407127a`). Cross-platform MD5 helper prefers `md5sum` (Linux) and falls back to `md5 -q` (macOS). Help text and README updated; new "Stock Firmware Manifest" section in the README documents the table and how to enrol additional builds.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.4.1: auto-handle sparse `system.img`. The bash now detects Android sparse format (via `file` output or the sparse magic `0xed26ff3a` LE-stored as bytes `3a ff 26 ed`) and runs `simg2img` into the working copy automatically. OTA-supplied `system.img` files are sparse, so this drops a manual prereq step. `simg2img` must be in PATH when the input is sparse; if missing, the script bails with install instructions for Debian/Ubuntu (`android-sdk-libsparse-utils`), Arch (`android-tools`), Fedora (`android-tools`), RHEL/Rocky/Alma 8+ (`android-tools` via EPEL), and macOS (`brew install simg2img`). Raw input images are still `cp`'d through unchanged. The working copy was already required to be raw end-to-end (mount + flash both expect raw), so this is purely a UX improvement — no behavioural change against already-raw inputs.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.4.0: drop the pre-staged-artifacts requirement. `--avrcp` and `--music-apk` now extract the stock binaries directly from the mounted `system.img`, run the corresponding `patch_*.py` against them, and write the patched bytes back in-place. Previously the user had to run each `patch_*.py` manually beforehand and stage `mtkbt.patched`/`MtkBt.odex.patched`/`libextavrcp.so.patched`/`libextavrcp_jni.so.patched`/`com.innioasis.y1_3.0.2-patched.apk` in `--artifacts-dir`. Only `Y1MediaBridge.apk` (externally-built integration APK, not derived from system.img) and `boot.img` (for `--root`) need to be staged now; everything else is extracted from `system.img` and patched on the fly. Two new helpers wrap the cycle: `patch_in_place_bytes <mount-rel> <patch-script> [mode]` for the four byte patchers (which all share `--output` semantics), and `patch_in_place_y1_apk <mount-rel>` for the smali patcher (which is script-style and lands its output in `${PATH_SCRIPT_DIR}/output/`). Idempotent — re-running `--avrcp` detects already-patched binaries (the `patch_*.py` scripts return exit 0 with no output file) and skips the write-back step. Sudo is still only requested when a system-affecting flag is set; `--root` alone runs sudo-less. Same in-place pattern that `patch_bootimg.py` already uses for `default.prop` + `/sbin/adbd`, applied to system.img.
- **2026-05-03** – Add `patch_adbd.py` and wire it into `patch_bootimg.py`. Three Thumb-2 NOP patches (H1/H2/H3) at `0x14bc`/`0x14ca`/`0x14d8` neutralise adbd's drop_privileges block by replacing each `blx setgroups/setgid/setuid` (4 bytes) with `movs r0, #0; nop` so the following `cmp r0, #0; bne.w fail` falls through. Required because empirical confirmation 2026-05-03 (`adb shell id` returning `uid=2000(shell)` with `ro.secure=0`/`ro.debuggable=1`/`ro.adb.secure=0` correctly set per `getprop`) showed this OEM adbd has stripped the standard `should_drop_privileges()` gating — `strings adbd` returns ZERO references to `ro.secure` and the drop block at `0x94b8` runs unconditionally on every adbd startup. The default.prop edits remain in place as belt-and-suspenders for other Android subsystems but are not load-bearing for the adbd-as-root question. `patch_bootimg.py` now extracts `/sbin/adbd` from the cpio, applies the H1/H2/H3 patches via `patch_adbd.patch_bytes()`, and writes it back in-place; the patched adbd has the same file size (223,132 bytes) so cpio record offsets are unchanged. Stock adbd MD5 `9e7091f1699f89dc905dee3d9d5b23d8` → patched MD5 `ccebb66b25200f7e154ec23eb79ea9b4`. Round-trip verified end-to-end against the stock 3.0.2 ramdisk.
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.3.2: no functional changes to the bash itself — reflects `patch_bootimg.py` absorbing `patch_adbd.py` (see entry above). `--root` help text updated: `adb root` is no longer flagged as harmful (the v1.3.1 warning was correct against the property-only patcher but is moot now that adbd is binary-patched).
- **2026-05-03** – `innioasis-y1-fixes.bash` v1.3.1: `--root` no longer touches `system.img` (skip copy / mount / patch / unmount / flash and the sudo prompt unless one of `--adb`/`--avrcp`/`--bluetooth`/`--music-apk`/`--remove-apps` is also set). The previous v1.3.0 flow re-flashed an unmodified `system.img` for `--root`-only invocations — pure cycle waste and a non-trivial flash risk. Also: drop `service.adb.root=1` from `patch_bootimg.py`'s `_DEFAULT_PROP_EDITS` based on the (incorrect) hypothesis that `ro.secure=0` would make adbd skip the privilege drop — disproven same-day by `adb shell id` returning uid 2000 with all properties correctly set, leading to the v1.3.2 binary-patch approach above. `--root` help text and README initially warned against running `adb root` post-flash on this firmware: with the v1.3.1 patcher, adbd was still uid 2000, and `adb root` triggered a stock MTK adbd USB re-bind on self-restart that lost the host connection — that warning was superseded in v1.3.2.
- **2026-05-03** – Reintroduce `--root` flag in `innioasis-y1-fixes.bash` (v1.3.0) backed by a new `patch_bootimg.py`. The previous v1.1.x `--root` was bash + `dd`/`cpio`/`mkbootimg` and drifted on MTK ramdisk header byte counts; the rewrite is pure-Python and patches `default.prop` *in-place* inside the gzipped cpio (no extract/repack — device nodes preserved byte-for-byte), then repacks the Android boot.img header with a recomputed SHA1 ID and the original load addresses. Edits: `ro.secure=0`, `ro.debuggable=1`, `ro.adb.secure=0`, `service.adb.root=1`. Round-tripped against the stock 3.0.2 ramdisk (37 cpio records, all post-`default.prop` offsets shifted by exactly the +35-byte delta from the larger prop file). After flashing the patched boot.img via mtkclient, `adb root && adb shell` yields uid 0 — unblocks `__xlog_buf_printf`/btsnoop/`gdbserver` visibility into mtkbt for the `result=0x1000` investigation.
- **2026-05-03** – Trace #7 (libbluetooth_*.so audit): all four `libbluetooth*.so` libs (`libbluetoothdrv.so`, `libbluetooth_mtk.so`, `libbluetoothem_mtk.so`, `libbluetooth_relayer.so`) inspected end-to-end. They are exclusively HCI/transport: UART link to MT6627, GORM/HCC chip-bringup, NVRAM BD-address management, Engineer Mode test plumbing. Combined `strings` search returned zero hits for `avrcp`, `avctp`, `profile`, `capability`, `notif`, `metadata`, `cardinal`. Conclusion: the cardinality:0 gate cannot live anywhere except inside `mtkbt`. The "we might be patching the wrong binary" doubt is closed.
- **2026-05-03** – Fresh test log (`/work/logs/test.log`, peer `38:42:0B:38:A3:3E`) shows the same gate pattern as prior runs (only msg_ids 506/505/512 reach JNI; no `op_code=4`; no `registerNotificationInd`). New observation: `MSG_ID_BT_AVRCP_CONNECT_CNF conn_id:1  result:4096` — the `result` field is non-zero (`0x1000`). This had not been called out in earlier log analyses and is now flagged as the most concrete static-investigation lead for the post-root pass.
- **2026-05-03** – Test G1-with-NULL-guard on hardware, **BT does not turn on**. Log shows BT framework attempts to enable: `bt_sendmsg(cmd=100, ...)` returns ENOENT — mtkbt's abstract socket isn't there. Either mtkbt crashed on a non-NULL but invalid pointer in r2 (small int, stack pointer, etc.), or the volume of redirected log calls flooding through logd slowed mtkbt's init past the BT framework's timeout. G1 reverted; back to 11-patch build at MD5 `d47c904063e7d201f626cf2cc3ebd50b`. **Conclusion: blanket xlog→logcat redirect is too fragile.** Future diagnostic instrumentation needs to be surgical — add explicit `bl __android_log_print` calls at a small number of high-value sites (dispatcher entries, AVCTP RX handler) with hardcoded tag/fmt strings, instead of trying to hijack the consolidated wrapper.
- **2026-05-02** – Re-add G1 with NULL guard to `patch_mtkbt.py` (no G2). 20-byte Thumb thunk at `0x675c0`: `cbz r2, .L_null; movs r0,#4; mov r1,r2; ldr.w pc,[pc,#4]; nop; .word 0xaef8; .L_null: movs r0,#0; bx lr`. The CBZ guards against NULL fmt pointers from xlog callsites that crashed the previous attempt. (Reverted same-day after testing — see entry above.)
- **2026-05-02** – Test G1/G2 (xlog→logcat redirect) on hardware, **mtkbt SIGSEGV at 0x00000000 immediately after start** (line 160 of /work/logs/logcat-mtkbt.log: `Fatal signal 11 (SIGSEGV) at 0x00000000 (code=1), thread 146 (mtkbt)`). At least one xlog callsite passes a NULL or invalid pointer in r2; bionic's `__android_log_print` at API 17 doesn't NULL-check the tag arg, so `strlen(tag)` faults at addr 0. Both G1 (Thumb wrapper at `0x675c0`) and G2 (ARM PLT at `0xb408`) reverted — patched MD5 returns to `d47c904063e7d201f626cf2cc3ebd50b` (11 patches: B1-B3, C1-C3, A1, D1, E3, E4, E8). Any future xlog→logcat redirect attempt must add a NULL guard in the thunk (e.g., `cbz r2, .L_skip; ...; .L_skip: bx lr`), and the thunk needs more space than 12 bytes — the simplest fix is to overflow into the dead remainder of the wrapper at `0x675c0` (16 bytes, 24 bytes — plenty of room).
- **2026-05-02** – Add G1/G2 diagnostic instrumentation patches (later reverted; see entry above). Redirected mtkbt's `__xlog_buf_printf` calls to `__android_log_print` so daemon-side `[AVRCP]/[AVCTP]` log strings (previously invisible because they go to MediaTek's separate xlog buffer) would appear in logcat. The thunk did `mov r0, #4; mov r1, r2; b __android_log_print_PLT` — translating xlog's `(buf_id, code, fmt, ...)` into android_log's `(prio, tag, fmt, ...)`. Concept is sound but missing the NULL guard.
- **2026-05-02** – Test E8 (`bge` NOP at `0x3065e` in fn `0x3060c`) on hardware. Cardinality:0 persists across all peer types; pattern matches earlier sessions exactly (Recv 506 → Recv 505 with `tg_feature:0 ct_featuer:0`, no msg_id 504 ever arrives). This rules out fn `0x3060c` as the runtime path. The stronger signal: only msg_ids 505/506 are received from mtkbt, never `op_code=4` (GetCapabilities). None of the three dispatchers (`0x3060c`, `0x30708`, `0x3096c`) are reached; the gate is upstream of the dispatcher table itself. E8 is left in place as it remains a verified-correct patch — possibly inert for our peers but harmless.
- **2026-05-02** – Add E8 to `patch_mtkbt.py`: NOP the `bge #0x30688` at `0x3065e` in fn `0x3060c` (op_code=4 dispatcher slot 0). Forces every classification through the AVRCP 1.3/1.4 init path (`b.w 0x2fd34`) regardless of the sign bit of `[conn+0x149]`. Brute-forcing the analogous fix across the other two op_code=4 dispatchers was considered and rejected — fn `0x30708` reads `[conn+0x149]` unsigned and masks `&0x7f` (no high-bit gate exists), and fn `0x3096c`'s analogous BNE→B at `0x309ec` was already tested-and-removed (old E5) as empirically inert. E8 ships as a single-instruction probe; if cardinality:0 persists, the runtime path is not fn `0x3060c`. New patched MD5: `d47c904063e7d201f626cf2cc3ebd50b`.
- **2026-05-02** – Remove E5 / E7a / E7b from `patch_mtkbt.py`. The three patches were observably non-functional across three known-good AVRCP 1.4 controllers (no behavioral change in cardinality:0 across car / Sonos Roam / Samsung TV after each was flashed). Initial reasoning attributed this to "dead code" + "BT chip firmware is the actual AVRCP processor" — both of which were later refined: the chip firmware (`mt6572_82_patch_e1_0_hdr.bin` for MT6627) is the WMT common subsystem (sleep/coredump/queue), contains zero AVRCP code, and `mtkbt` IS the host-side AVRCP processor. The function `0x3096c` (E5 patch site) is also not dead — its pointer is installed at runtime via R_ARM_RELATIVE in a function-pointer table at vaddr `0xf94b8`. The three patches were removed because they're empirically inert on this binary; the actual reason they don't fire isn't statically determinable without the runtime visibility we've documented as out of scope. Reverting them leaves the script with the demonstrably-effective SDP-layer + runtime-struct + registration-guard patches (B1-B3, C1-C3, A1, D1, E3, E4 — ten patches total). Patched MD5 reverts to `b17bdf5448fdae68c1d477626190e63e`.
- **2026-05-02** – Audit pass over all in-repo patch script documentation. Re-verified every concrete address, byte sequence, instruction encoding, descriptor table entry, function entry, ILM offset, global address derivation, ELF segment, and SDP element byte against the stock binaries. All claims confirmed accurate. End state recorded honestly: cardinality:0 persists across three known-good 1.4 controllers (car, Sonos Roam, Samsung TV) despite all SDP/feature/dispatcher patches being live and verified on-wire (sdptool shows AVRCP 1.4 + AVCTP 1.3 + SupportedFeatures 0x0033). The remaining gate is mtkbt's runtime AVCTP receive path, which is opaque to logcat because mtkbt routes its `[AVCTP]`/`[AVRCP]` log strings through MediaTek's separate `__xlog_buf_printf` log buffer rather than `__android_log_print`.
- **2026-05-02** – Add E7a/E7b patches to patch_mtkbt.py: change two `movs r0, #0x90` immediates at `0x033dec` and `0x034100` to `#0x94`. Diagnosed root cause: E5 alone wasn't reaching its target dispatcher because mtkbt processes "AVRCP 1.0" connections through an internal native handler that never forwards to the JNI. Pattern-search of immediate writes to the per-connection version field `[conn+0x5d9]` exposed the fallback: when the remote (the car) doesn't advertise an AVRCP CT (UUID 0x110e) SDP record — which is the common case for car infotainment systems — `[+0x5dc]` stays zero and these two `movs r0, #0x90` writes fire, classifying the connection as 1.0. E7a/E7b flip both immediates so the fallback now decodes to 0x14 (AVRCP 1.4) instead of 0x10 (AVRCP 1.0). New patched MD5: `ff50024bc851395408353ba52d140790`.
- **2026-05-01** – Add E5 patch to patch_mtkbt.py: `BNE 0x30aca` → `B 0x30aca` at `0x309ec` (single-byte change at `0x309ed`: `0xd1` → `0xe0`). Diagnoses why post-E3/E4 cardinality stayed 0 despite a textbook-1.4 SDP advertisement — mtkbt's op_code=4 GetCapabilities dispatcher at `0x3096C` reads `[conn+0x149] & 0x7f` and routes to the AVRCP 1.0 path when classification == 0x10. Empirically the car was being classified as 1.0 (likely from absent/incomplete CT-side 0x110e SDP from the car), so the 1.3/1.4 init path (`0x02fd34` → 5-slot init + `AVAILABLE_PLAYERS`) never ran and the JNI never saw any inbound AVRCP commands. E5 forces every op_code=4 dispatch through the 1.3/1.4 init path. T1 `bne #+218` (`6d d1`) and T2 narrow `b.n #+218` (`6d e0`) happen to share the same numeric offset, so this is a clean 1-byte patch. New patched MD5: `40ee04945f5fba9754cc1bc20bb323e9`.
- **2026-05-01** – Harmonize all four byte-patch scripts (`patch_mtkbt.py`, `patch_mtkbt_odex.py`, `patch_libextavrcp.py`, `patch_libextavrcp_jni.py`) onto a single template: `PATCHES` is a list of dicts with `name`/`offset`/`before`/`after` keys (was tuples in `patch_mtkbt_odex.py`); shared `verify(data, mode)` and `print_results(label, results, mode)` helpers with truncated hex output for long byte sequences and 6-digit offset widths; uniform `[OK — matches stock]` / `[MISMATCH — expected …]` MD5 tags inline with the hash; uniform argparse `help=` text on every flag; patch names carry an explicit ID prefix (`[B1]`, `[C2a]`, `[E3]`, `[F1]`, etc.), and offsets are no longer duplicated in patch names since `print_results` already prints them. No output bytes changed; all MD5s identical.
- **2026-05-01** – Add E3/E4 SupportedFeatures patches to patch_mtkbt.py. Post-flash `sdptool browse` (XML output) showed AVRCP TG record served `AttrID=0x0311 = 0x0001` (Cat1 only) on the wire — directly contradicting the prior eliminated-paths claim that 0x0311 "cannot be added without patching the BlueAngel vtable dispatch." Group 2 wins the SDP merge and its SupportedFeatures LSB at `0x0eba5b` is patched 0x01 → 0x33 (Cat1 + Cat2 + PAS + GroupNav — AVRCP 1.4 baseline matching AOSP Bluedroid). Group 1 LSB at `0x0eba4e` patched 0x21 → 0x33 for defense-in-depth. Browsing bit deliberately omitted (AdditionalProtocolDescriptorList not on the wire post-merge). New patched MD5: `b17bdf5448fdae68c1d477626190e63e`. Also reformatted the patcher's MD5 status output to use `[OK — matches stock]` / `[MISMATCH — expected …]` tags inline with the hash, matching the existing patch-verification style.
- **2026-05-01** – Reverted E1 and E2 patches from patch_mtkbt.py (added and removed same session). Deep binary analysis showed both were incorrect: **E1** (`0x29be4 BNE.W → NOP`) bypassed a legitimate state guard in `0x299fc` — that gate only fires when state∉{3,5}, which is correct (state=3 is set by an *incoming* REGISTER_NOTIFICATION, so no response should be sent without one). Bypassing it caused unsolicited REGISTER_NOTIFICATION responses → car disconnected (explains cycle-1 disconnect in logs). **E2** (`0x0309ec BNE → NOP`) routed AVRCP 1.3/1.4 cars from the correct count=4 path (5-slot initialization + AVAILABLE_PLAYERS at `0x29f56`) to the AVRCP 1.0 count=8 path (TRACK_CHANGED-only at `0x29eda`), bypassing mandatory 1.3/1.4 slot initialization. Patched MD5 restored to `e9e9fbbbadcfe50e5695759862f002a3`. Root cause of `cardinality:0`: C3a/C3b patches in `patch_libextavrcp_jni.py` (GetCapabilities event count cap 13→14 in `getCapabilitiesRspNative`) — identified 2026-04-30, confirmed primary cardinality fix once D1 enables TG SDP registration. Fresh unpair/re-pair required after flash to force car to re-read SDP.
- **2026-04-30** – Investigate persistent `tg_feature:0 ct_feature:0` post-D1. Full CONNECT_CNF handler disassembly (via TBH dispatch table at libextavrcp_jni.so:0x60B8, msg_id=505 → handler at 0x62EA) confirms that `tg_feature` is logged but not used for functional gating — it does not control whether REGISTER_NOTIFICATION is processed. Root cause of `cardinality:0`: `FUN_005de8` (getCapabilitiesRspNative) caps the GetCapabilities event count at 13 (0x0d) in stock, preventing Y1 from advertising AVRCP 1.4 events to the car CT. C3a/C3b patches in `patch_libextavrcp_jni.py` raise the cap to 14 (0x0e). Generated `output/libextavrcp_jni.so.patched` (MD5: `6c348ed9b2da4bb9cc364c16d20e3527`) and `output/libextavrcp.so.patched` (MD5: `943d406bfbb7669fd62cf1c450d34c42`) — both were missing from output dir. These are required for cardinality > 0.
- **2026-04-30** – Add D1 patch to patch_mtkbt.py: NOP the `BNE 0x38C76` at `0x38C6C` to bypass the runtime registration guard in the SDP init function. Without this patch, the AVRCP TG struct is built correctly but never linked into mtkbt's live registry — mtkbt does not process incoming GetCapabilities commands and no peer sends `REGISTER_NOTIFICATION`. Confirmed root cause by fresh-pairing Sonos Roam and Samsung The Frame Pro TV (both known-good AVRCP CTs). Updated patched MD5: `e9e9fbbbadcfe50e5695759862f002a3`.
- **2026-04-30** – Add three AVCTP version patches (B1-B3) to patch_mtkbt.py. Stock mtkbt advertises AVCTP 1.0 (0x0100) in all three AVCTP-bearing SDP blobs; AVRCP 1.4 requires AVCTP 1.3. Patched: `0x0eba6d` (Groups 1&2 TG ProtocolDescList), `0x0eba37` (Group 3 CT ProtocolDescList), `0x0eba25` (Group 1 AdditionalProtocol). Corrected incorrect prior note claiming `AttrID=0x0311` (SupportedFeatures) was not registered — it IS in all three groups. Updated patched MD5: `37ddc966760312b1360743434637ff2d`. Rename existing ProfileDescList patches: B0→C1, B1→C2, B2→C3.
- **2026-04-30** – Regression analysis and SDP confirmation. Discovered descriptor table contains THREE `AttrID=0x0009` (ProfileDescList) entries (records [13], [18], [23]). Old patches #2 (0xeba4b) and #3 (0xeba58) incorrectly eliminated as "read-back only"; regression from 0x0103 → 0x0100 on removal proved both were live. Restored and upgraded all three to 0x04 (AVRCP 1.4). A1 (0x38BFC MOVW) retained as belt-and-suspenders. Confirmed: `sdptool browse` → `AV Remote Version: 0x0104`.
- **2026-04-29** – Full Prong C (JNI/native) audit complete; no new binary patch required for JNI layer. Confirmed call chain: `getPreferVersion(14)` → `checkCapability()` 1.4 block → `activateConfig_3req(bitmask)` → `g_tg_feature=0x0e` (@ 0xD29C) → `activate_1req` → `btmtk_avrcp_send_activate_req` payload byte[6]=0x0e → daemon socket. Add **[A1] patch_mtkbt.py patch 11** at `0x38BFC` (`40 f2 01 37` → `40 f2 01 47`): MOVW r7,#0x0301→#0x0401, the runtime SDP STRH.W — this is the primary SDP advertisement fix. Fix patch 6 offset: `0xeba77` (1 byte) → `0xeba76` (2 bytes `01 03`→`01 04`), the static SDP wire-format template. Update patch_libextavrcp_jni.py docstring with confirmed global addresses and full call chain. Fix misleading "AVRCP 1.0" label in patch_mtkbt_odex.py (BlueAngel code 10 = AVRCP 1.3).
- **2026-04-27** – Rename patch_odex.py → patch_mtkbt_odex.py; add second patch: reset `sPlayServiceInterface` in `BluetoothAvrcpService.disable()` to fix BT toggle service teardown bug
- **2026-04-27** – All patch scripts write output to `output/` subdirectory; `_patch_workdir` cleaned up after patch_y1_apk.py run
- **2026-04-26** – Add patch_libextavrcp.py (libextavrcp.so AVRCP 1.4 version constant); rename patch_so.py → patch_libextavrcp_jni.py; deploy `libextavrcp.so.patched` via `--avrcp` in innioasis-y1-fixes.bash
- **2026-04-26** – Remove `--root` flag and boot.img handling (broken)
- **2026-04-26** – Prompt for sudo credentials upfront; keep ticket alive for script duration to prevent mid-execution prompts
- **2026-04-26** – Fix `--root`: use `sudo cpio` to preserve device nodes; add `ro.adb.secure=0` and `service.adb.root=1` to ramdisk `default.prop`; remove size mismatch failure (non-issue)
- **2026-04-26** – Fix macOS compatibility: replace `stat -c%s` with `wc -c` for file size
- **2026-04-26** – Add `--root` flag to patch boot.img ramdisk for ADB root access
- **2026-04-26** – Add patch_mtkbt.py, patch_odex.py, patch_so.py; all three BT binaries patched for AVRCP 1.4
- **2026-04-25** – Split build.prop configuration, sorting and cleanup
- **2026-04-25** – Add bash parameter handling for selective patching
- **2026-04-24** – Install patched Y1 music player APK
- **2026-04-24** – Install patched MtkBt.odex for AVRCP 1.3 Java selector fix
- **2026-04-23** – Initial release

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
