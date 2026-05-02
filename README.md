# Innioasis Y1 Firmware Fixes

A comprehensive patching toolkit for the Innioasis Y1 media player (firmware 3.0.2) that fixes Bluetooth AVRCP functionality, improves the media player UI navigation, and enables ADB debugging.

## Overview

This project provides tools to patch and enhance the Innioasis Y1 firmware with:

- **Bluetooth AVRCP 1.4 Support** – Forces AVRCP 1.4 advertisement across all BT stack layers (daemon, ODEX, JNI library, core library)
- **Artist→Album Navigation** – Improves media player UX by showing album cover art after artist selection instead of a flat song list
- **System Configuration** – Enables ADB debugging and optimizes Bluetooth settings
- **APK Patching** – Patches the system music player APK at the bytecode level using smali assembly

## Contents

### Main Scripts

- **`patch_mtkbt.py`**
  - Patches the stock `mtkbt` Bluetooth daemon binary for AVRCP 1.4
  - **Thirteen patches applied:**
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
    - **G1** `0x675c0`: `0c b4 55 4b 2d e9 f0 4f 83 46 54 48` → `04 20 11 46 df f8 00 f0 f8 ae 00 00` (12 bytes, Thumb mode). Replaces the prologue of mtkbt's `[AVRCP]/[AVCTP]` xlog wrapper with a thunk that tail-calls `__android_log_print(LOG_INFO, fmt, fmt, varargs...)`, redirecting daemon-side log output from MediaTek's xlog buffer (invisible without root) to logcat. **Diagnostic-only.** See G note below.
    - **G2** `0xb408`: `00 c6 8f e2 ee ca 8c e2 80 fb bc e5` → `04 00 a0 e3 02 10 a0 e1 b8 fe ff ea` (12 bytes, ARM mode). Replaces the `__xlog_buf_printf` PLT entry with the same thunk, in ARM, branching to the `__android_log_print` PLT at `0xaef8`. Catches the 1091 callers that call the xlog PLT directly without going through the wrapper. **Diagnostic-only.**
  - The descriptor table contains three service record groups. Groups 1 & 2 are TG (AV Remote Target 0x110c); Group 3 is CT (AV Remote 0x110e). All AVCTP version bytes were stock 1.0; AVRCP 1.4 requires AVCTP 1.3. All three ProfileDescList entries are patched to AVRCP 1.4 (last-wins semantics).
  - **D1 note:** The SDP init function at `0x38AB0` builds the TG struct, then gates the final `STR r3,[r1]` registration write behind `CMP r0,r5 / BNE` where r5=`0x111F`. r0 is never `0x111F`, so without D1 the registration never completes and mtkbt silently discards incoming GetCapabilities commands.
  - **E3/E4 note:** Wire-confirmed via `sdptool browse` after D1 was live: `AttrID=0x0311` IS served inside the AVRCP TG record (UUID 0x110c), but the served value is `0x0001` (Cat1 only — Group 2 wins the merge). 1.4 controllers see ProfileVersion=1.4 with a feature bitmask consistent with 1.0, treat the advertiser as inconsistent, and skip `REGISTER_NOTIFICATION` (which is why earlier builds had `cardinality:0` even with C3a/C3b applied). Browsing bit (6) is deliberately omitted because `AdditionalProtocolDescriptorList` (0x000d) is in Group 1 only and isn't on the wire after the merge — claiming Browsing without serving the descriptor would re-introduce the same inconsistency.
  - **E8 note:** Trace #1g resolved the indirect-call graph and identified three op_code=4 dispatchers reached via the 3-slot fn-ptr table at vaddr `0xf94b0..0xf94bc`: fn `0x3060c` (slot 0), fn `0x30708` (slot 1), fn `0x3096c` (slot 2). Of these only fn `0x3060c` has a clean single-instruction high-bit gate on `[conn+0x149]`: `ldrsb.w r0,[r4,#0x149]; cmp r0,#0; bge #0x30688`. The bge skips the 1.3/1.4 init path when the version byte's high bit is clear; NOPing it forces the init path unconditionally. Brute-forcing the analogous fix to the other two slots was considered and rejected: fn `0x30708` reads the byte unsigned and masks `&0x7f` (no high-bit gate exists; failure exits gate on a multi-byte state-machine on `[conn+0x5d0]`); fn `0x3096c`'s analogous BNE→B (the old E5 patch at `0x309ec`) was already empirically tested in earlier sessions and removed as inert. E8 ships as a low-risk single-instruction probe; tested 2026-05-02 and observed inert (cardinality:0 persists, no `op_code=4` GetCapabilities messages reach the dispatchers — the gate is upstream of the dispatcher table entirely).
  - **G1/G2 note:** Diagnostic instrumentation. mtkbt routes its `[AVRCP]/[AVCTP]` log strings through MediaTek's `__xlog_buf_printf`, which writes to a separate buffer not accessible without root — this has been the chronic blind spot in the cardinality:0 investigation. Both `__xlog_buf_printf` and `__android_log_print` are already imported by mtkbt (`NEEDED: liblog.so`), so we redirect xlog calls to logcat via a 3-instruction thunk: `mov r0, #4; mov r1, r2; b __android_log_print_PLT`. The xlog signature `(buf_id, code, fmt, ...)` and android_log signature `(prio, tag, fmt, ...)` differ only in the first two args; the format string at r2 and all variadic args at r3+stack pass through unchanged because both functions follow ARM EABI varargs ABI. We overwrite r0 with `LOG_INFO` and r1 with the original r2 (the format string itself, used as the tag — produces logcat lines tagged with `[AVRCP]`/`[AVCTP]` prefixes that survive truncation). Two patches are needed because mtkbt has 4079 xlog callsites split between a Thumb wrapper (G1, 2988 callers) and direct PLT calls (G2, 1091 callers). Verify with `logcat -s '*:V' | grep -E '\[AVRCP\]|\[AVCTP\]'`. **These are diagnostic patches and should be removed once the cardinality:0 root cause is identified and fixed via a behavioural patch.**
  - Stock MD5: `3af1d4ad8f955038186696950430ffda` — Output MD5: `18c34b11a0a27c17c318c6de2a7b3fd0`

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

- **`innioasis-y1-fixes.bash`** (v1.2.0)
  - Accepts mandatory `--artifacts-dir` parameter for artifact location
  - Supports selective patching with individual flags: `--adb`, `--avrcp`, `--bluetooth`, `--music-apk`, `--remove-apps`
  - Mounts and patches the system.img firmware image
  - Copies patched APKs, libraries, and binaries into the filesystem
  - Configures build.prop and Bluetooth settings
  - Removes unnecessary bloatware APKs

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
- `libextavrcp_jni.so.patched` – Patched JNI library (`g_tg_feature=14`, `sdpfeature=0x23`)
- `libextavrcp.so.patched` – Patched AVRCP library (version constant `0x0103` → `0x0104`)
- `com.innioasis.y1_3.0.2-patched.apk` – Patched music player
- `Y1MediaBridge.apk` – Additional media integration

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
- `--artifacts-dir` parameter pointing to a directory containing:
  - `system.img` – Original firmware system image
  - `com.innioasis.y1_3.0.2-patched.apk` – Patched music player APK (from patch_y1_apk.py)
  - `Y1MediaBridge.apk`, `mtkbt.patched`, `MtkBt.odex.patched`, `libextavrcp_jni.so.patched`, `libextavrcp.so.patched` – Patched BT binaries (from patch scripts, for `--avrcp` flag)
- mtkclient 2.1.4.1 installed at `/opt/mtkclient-2.1.4.1`

## Usage

### Step 1: Patch the Music Player APK

```bash
python3 patch_y1_apk.py path/to/com.innioasis.y1_3.0.2.apk
```

Output: `output/com.innioasis.y1_3.0.2-patched.apk`

Alternatively, if the APK is in the current directory:
```bash
python3 patch_y1_apk.py
```

### Step 2: Patch the Bluetooth Binaries (for `--avrcp`)

Run each patch script against the corresponding stock binary extracted from the firmware:

```bash
python3 patch_mtkbt.py mtkbt
python3 patch_mtkbt_odex.py MtkBt.odex
python3 patch_libextavrcp_jni.py libextavrcp_jni.so
python3 patch_libextavrcp.py libextavrcp.so
```

Outputs (all written to the `output/` directory):
- `output/mtkbt.patched`
- `output/MtkBt.odex.patched`
- `output/libextavrcp_jni.so.patched`
- `output/libextavrcp.so.patched`

Each script verifies the input MD5, checks patch sites before and after, and refuses to write output if anything is unexpected.

### Step 3: Prepare Patch Artifacts

Gather the following files in a directory of your choice (e.g., `/home/user/y1-patches/`):
- `system.img` (original firmware system image, required for any system flag)
  - Obtained from an OTA update package, or dumped from the device block device via ADB:
    ```bash
    adb shell "dd if=/dev/block/<partition> bs=4096" > system.img
    ```
    (Replace `<partition>` with the correct block device node for your device.)
  - **Important:** If the image is sparse (output of `file` shows "Android sparse image"), convert it to raw format using simg2img:
    ```bash
    simg2img system.img system-raw.img
    mv system-raw.img system.img
    ```
- `com.innioasis.y1_3.0.2-patched.apk` – copy from `output/` produced in Step 1
- `Y1MediaBridge.apk` (required for `--avrcp` flag)
- `mtkbt.patched`, `MtkBt.odex.patched`, `libextavrcp_jni.so.patched`, `libextavrcp.so.patched` – copy from `output/` produced in Step 2 (required for `--avrcp` flag)

### Step 4: Apply Firmware Patches

```bash
chmod +x innioasis-y1-fixes.bash
./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts [OPTIONS]
```

**Available options:**
- `--adb` – Enable ADB debugging via build.prop
- `--avrcp` – Deploy AVRCP 1.4 patched binaries (`mtkbt.patched`, `MtkBt.odex.patched`, `libextavrcp_jni.so.patched`, `libextavrcp.so.patched`, `Y1MediaBridge.apk`)
- `--bluetooth` – Configure Bluetooth settings and build.prop Bluetooth entries
- `--music-apk` – Install patched Y1 music player APK
- `--remove-apps` – Remove unnecessary APK files
- `--all` – Apply all patches

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

### Step 5: Flash Firmware

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

## Changes

- **2026-05-02** – Add **G1/G2** diagnostic instrumentation patches to `patch_mtkbt.py`. Redirects mtkbt's `__xlog_buf_printf` calls to `__android_log_print` so daemon-side `[AVRCP]/[AVCTP]` log strings (previously invisible because they go to MediaTek's separate xlog buffer) appear in logcat. Two thunks: G1 at `0x675c0` (Thumb wrapper, 2988 callsites) and G2 at `0xb408` (xlog PLT, 1091 callsites). 3-instruction thunk: `mov r0, #4; mov r1, r2; b __android_log_print_PLT` — translates xlog's `(buf_id, code, fmt, ...)` into android_log's `(prio, tag, fmt, ...)` with format string and varargs passing through unchanged. Required because trace #1g and the E8 test together established the cardinality:0 gate is upstream of the op_code=4 dispatchers (no GetCapabilities messages even reach them) — but the upstream classification logic logs only via xlog, and we cannot diagnose without seeing those messages. Diagnostic only; should be reverted once a behavioural fix is found. New patched MD5: `18c34b11a0a27c17c318c6de2a7b3fd0`.
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
