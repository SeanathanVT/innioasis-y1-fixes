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
  - **Ten patches applied:**
    - **B1** `0x0eba6d`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Groups 1 & 2 shared ProtocolDescList (TG control channel — what `sdptool` sees)
    - **B2** `0x0eba37`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 3 CT ProtocolDescList
    - **B3** `0x0eba25`: `0x00` → `0x03` — AVCTP 1.0 → 1.3 LSB in Group 1 AdditionalProtocol (browsing channel descriptor)
    - **C1** `0x0eba4b`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[23]
    - **C2** `0x0eba58`: `0x00` → `0x04` — AVRCP 1.0 → 1.4 LSB in ProfileDescList entry[18] (served by SDP last-wins)
    - **C3** `0x0eba77`: `0x03` → `0x04` — AVRCP 1.3 → 1.4 LSB in ProfileDescList entry[13]
    - **A1** `0x38BFC`: `40 f2 01 37` → `40 f2 01 47` — `MOVW r7,#0x0301` → `MOVW r7,#0x0401` (runtime SDP struct, belt-and-suspenders)
    - **E1** `0x029be4`: `40 f0 fe 80` → `00 bf 00 bf` — `BNE.W 0x29de4` → `NOP×2` — bypasses state gate in `0x299fc` that silently dropped GetCapabilities responses when `[cb+0xe99]` was neither 3 nor 5 (see note below)
    - **E2** `0x0309ec`: `6d d1` → `00 bf` — `BNE 0x030aca` → `NOP` — bypasses version check in GetCapabilities dispatcher that routed all AVRCP 1.3/1.4 cars to the wrong dispatch path (see note below)
    - **D1** `0x38C6C`: `03 d1` → `00 bf` — `BNE 0x38C76` → `NOP` — bypasses registration guard so the AVRCP TG SDP struct is always linked into mtkbt's live registry (see note below)
  - The descriptor table contains three service record groups. Groups 1 & 2 are TG (AV Remote Target 0x110c); Group 3 is CT (AV Remote 0x110e). All AVCTP version bytes were stock 1.0; AVRCP 1.4 requires AVCTP 1.3. All three ProfileDescList entries are patched to AVRCP 1.4 (last-wins semantics). `AttrID=0x0311` (SupportedFeatures) IS present in all three groups in the descriptor table (values 0x0021, 0x0001, 0x000f).
  - **D1 note:** The SDP init function at `0x38AB0` builds the TG struct, then gates the final `STR r3,[r1]` registration write behind `CMP r0,r5 / BNE` where r5=`0x111F`. r0 is never `0x111F`, so without D1 the registration never completes and mtkbt silently discards incoming GetCapabilities commands.
  - **E1 note:** Function `0x299fc` dispatches REGISTER_NOTIFICATION and GetCapabilities. For CapabilityId=2 (`sb=2`), it checks `[cb+0xe99]` for state==3 or state==5 before sending a response. During an incoming GetCapabilities PDU, state is not guaranteed to be 3 or 5, so the `BNE.W` at `0x29be4` would exit without sending a response — the car CT never receives a capability list and never sends REGISTER_NOTIFICATION. The NOP forces fall-through to the state==5 send path.
  - **E2 note:** The GetCapabilities operation dispatcher (`0x3096C`, op_code=4) version-checks `[cb+0x149] & 0x7f`. AVRCP 1.3/1.4 cars have version bytes `0x13`/`0x14` — neither `0x10` nor `0x20` — so the `BNE` at `0x0309ec` fired and routed them to `0x02fd34`, which called `0x299fc(conn, 0x0a)` (AVAILABLE_PLAYERS), not `0x299fc(conn, 2)` (GetCapabilities). The car received the wrong PDU type and never sent REGISTER_NOTIFICATION. The NOP falls through to `CMP r2,#0x20` / `BNE → 0x030a74` (count=8 path) → `callback(count=8)` → `0x299fc(conn, 2)` → (with E1) correct GetCapabilities response is sent.
  - Stock MD5: `3af1d4ad8f955038186696950430ffda` — Output MD5: `8e8785a89df1554d299c222a561559f9`

- **`patch_mtkbt_odex.py`**
  - Patches `MtkBt.odex` with two fixes:
    1. `getPreferVersion()` returns 14 (AVRCP 1.4) instead of 10 (at `0x3e0ea`)
    2. `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false` (at `0x03f21a`) — fixes BT toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts
  - Recomputes the DEX adler32 checksum embedded in the ODEX header
  - Input: stock `MtkBt.odex` (md5 `11566bc23001e78de64b5db355238175`) → Output: `output/MtkBt.odex.patched` (md5 `acc578ada5e41e27475340f4df6afa59`)

- **`patch_libextavrcp_jni.py`**
  - Patches `libextavrcp_jni.so` to force `g_tg_feature=14` (AVRCP 1.4) and `sdpfeature=0x23`; prevents CONNECT_CNF from downgrading negotiated version below 1.4
  - 4 ARM Thumb2 instruction overwrites: 2 in `BluetoothAvrcpService_activateConfig_3req` at 0x375c (hardcode tg_feature/sdpfeature, bypassing bitmask logic), 2 in CONNECT_CNF handler at 0x5e56/0x5e5c (raise version cap from 0x0d to 0x0e)
  - The bitmask bypass at 0x375c complements (not replaces) the ODEX `getPreferVersion` patch — both are required for reliable 1.4 negotiation. Verified global addresses: `g_tg_feature` @ 0xD29C, `g_ct_feature` @ 0xD004.
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

- **2026-05-01** – Add E1 and E2 patches to patch_mtkbt.py. **E2** (`0x0309ec` `BNE → NOP`): version check in GetCapabilities dispatcher was routing all AVRCP 1.3/1.4 cars to `0x02fd34` which called `0x299fc(conn, 0x0a)` (AVAILABLE_PLAYERS) instead of `0x299fc(conn, 2)` (GetCapabilities). **E1** (`0x029be4` `BNE.W → NOP×2`): state gate in `0x299fc` silently dropped GetCapabilities responses when connection state `[cb+0xe99]` was neither 3 nor 5. Together these fix the two root causes of `cardinality:0` — car CT never received a capability list, so REGISTER_NOTIFICATION was never sent. Updated patched MD5: `8e8785a89df1554d299c222a561559f9`.
- **2026-04-30** – Investigate persistent `tg_feature:0 ct_feature:0` post-D1. Full CONNECT_CNF handler disassembly (via TBH dispatch table at libextavrcp_jni.so:0x60B8, msg_id=505 → handler at 0x62EA) confirms that `tg_feature` is logged but not used for functional gating — it does not control whether REGISTER_NOTIFICATION is processed. Root cause of `cardinality:0`: `FUN_005de8` (getCapabilitiesRspNative) caps the GetCapabilities event count at 13 (0x0d) in stock, preventing Y1 from advertising AVRCP 1.4 events to the car CT. C3a/C3b patches in `patch_libextavrcp_jni.py` raise the cap to 14 (0x0e). Generated `output/libextavrcp_jni.so.patched` (MD5: `6c348ed9b2da4bb9cc364c16d20e3527`) and `output/libextavrcp.so.patched` (MD5: `943d406bfbb7669fd62cf1c450d34c42`) — both were missing from output dir. These are required for cardinality > 0.
- **2026-04-30** – Add D1 patch to patch_mtkbt.py: NOP the `BNE 0x38C76` at `0x38C6C` to bypass the runtime registration guard in the SDP init function. Without this patch, the AVRCP TG struct is built correctly but never linked into mtkbt's live registry — mtkbt does not process incoming GetCapabilities commands and no peer sends `REGISTER_NOTIFICATION`. Confirmed root cause by fresh-pairing Sonos Roam and Samsung The Frame Pro TV (both known-good AVRCP CTs). Updated patched MD5: `e9e9fbbbadcfe50e5695759862f002a3`.
- **2026-04-30** – Add three AVCTP version patches (B1-B3) to patch_mtkbt.py. Stock mtkbt advertises AVCTP 1.0 (0x0100) in all three AVCTP-bearing SDP blobs; AVRCP 1.4 requires AVCTP 1.3. Patched: `0x0eba6d` (Groups 1&2 TG ProtocolDescList), `0x0eba37` (Group 3 CT ProtocolDescList), `0x0eba25` (Group 1 AdditionalProtocol). Corrected incorrect prior note claiming `AttrID=0x0311` (SupportedFeatures) was not registered — it IS in all three groups. Updated patched MD5: `37ddc966760312b1360743434637ff2d`. Rename existing ProfileDescList patches: B0→C1, B1→C2, B2→C3.
- **2026-04-30** – Regression analysis and SDP confirmation. Discovered descriptor table contains THREE `AttrID=0x0009` (ProfileDescList) entries (records [13], [18], [23]). Old patches #2 (0xeba4b) and #3 (0xeba58) incorrectly eliminated as "read-back only"; regression from 0x0103 → 0x0100 on removal proved both were live. Restored and upgraded all three to 0x04 (AVRCP 1.4). A1 (0x38BFC MOVW) retained as belt-and-suspenders. Confirmed: `sdptool browse` → `AV Remote Version: 0x0104`. Generated unified brief at `/root/briefs/Innioasis_Y1_AVRCP_Unified_Brief.md`.
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
