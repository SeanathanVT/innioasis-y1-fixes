# Innioasis Y1 Firmware Fixes

A comprehensive patching toolkit for the Innioasis Y1 media player (firmware 3.0.2) that fixes Bluetooth AVRCP functionality and improves the media player UI navigation.

## Overview

This project provides tools to patch and enhance the Innioasis Y1 firmware with:

- **Bluetooth AVRCP 1.3 Support** ⚠️ **WIP** – Fixes Java selector issues in Bluetooth audio profile handling
- **Artist→Album Navigation** – Improves media player UX by showing album cover art after artist selection instead of a flat song list
- **System Configuration** – Enables ADB debugging and optimizes Bluetooth settings
- **APK Patching** – Patches the system music player APK at the bytecode level using smali assembly

## Contents

### Main Scripts

- **`innioasis-y1-fixes.bash`** (v1.0.10)
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
- `MtkBt.odex` – Patched Bluetooth MTK HAL with AVRCP 1.3 fix
- `libextavrcp_jni.so` – Patched AVRCP JNI library
- `mtkbt` binary – Updated Bluetooth daemon
- `com.innioasis.y1_3.0.2-patched.apk` – Patched music player
- `Y1MediaBridge.apk` – Additional media integration

**Configuration Changes (`--adb`):**
- `persist.service.adb.enable=1`
- `persist.service.debuggable=1`

**Configuration Changes (`--bluetooth`):**
- `persist.bluetooth.avrcpversion=avrcp13`
- `ro.bluetooth.class=2098204`
- `ro.bluetooth.profiles.a2dp.source.enabled=true`
- `ro.bluetooth.profiles.avrcp.target.enabled=true`
- audio.conf: `Enable=Source,Control,Target`, `Master=true`
- Clears Bluetooth device blacklists (auto_pairing.conf, blacklist.conf)

**Other Changes:**
- Removes bloatware APKs (`--remove-apps`): ApplicationGuide, BackupRestoreConfirmation, BasicDreams, etc.

## Requirements

### For patch_y1_apk.py

- Python 3.8 or later
- Java 11 or later (for apktool's smali assembler)
- androguard: `pip install androguard`
- apktool (downloaded automatically if not found)

### For innioasis-y1-fixes.bash

- Bash 4+
- `sudo` access (for mounting and modifying system.img)
- `--artifacts-dir` parameter pointing to a directory containing:
  - `system.img` – Original firmware system image
  - `com.innioasis.y1_3.0.2-patched.apk` – Patched music player APK (from patch_y1_apk.py)
  - `Y1MediaBridge.apk`, `MtkBt.odex`, `libextavrcp_jni.so`, `mtkbt` – Pre-built patches (for `--avrcp` flag)
- mtkclient 2.1.4.1 installed at `/opt/mtkclient-2.1.4.1`

## Usage

### Step 1: Patch the Music Player APK

```bash
python3 patch_y1_apk.py path/to/com.innioasis.y1_3.0.2.apk
```

Output: `com.innioasis.y1_3.0.2-patched.apk`

Alternatively, if the APK is in the current directory:
```bash
python3 patch_y1_apk.py
```

### Step 2: Prepare Patch Artifacts

Gather the following files in a directory of your choice (e.g., `/home/user/y1-patches/`):
- `system.img` (original firmware system image)
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
- `com.innioasis.y1_3.0.2-patched.apk` (from Step 1)
- `Y1MediaBridge.apk` (required for `--avrcp` flag)
- `MtkBt.odex` (required for `--avrcp` flag)
- `libextavrcp_jni.so` (required for `--avrcp` flag)
- `mtkbt` (binary, required for `--avrcp` flag)

### Step 3: Apply Firmware Patches

```bash
chmod +x innioasis-y1-fixes.bash
./innioasis-y1-fixes.bash --artifacts-dir /path/to/artifacts [OPTIONS]
```

**Available options:**
- `--adb` – Enable ADB debugging via build.prop
- `--avrcp` – Enable AVRCP 1.3 support and fix Bluetooth media control issues ⚠️ **WIP**
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
3. Unmount and generate the patched firmware image

**Output:** `system-3.0.2-devel.img` (patched firmware image)

### Step 4: Flash Firmware

Use the MTK scatter tool or mtkclient to flash `system-3.0.2-devel.img` back to the device.

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

## Version History

- **v1.0.10** (2026-04-25) – Split build.prop configuration, sorting and cleanup
- **v1.0.9** (2026-04-25) – Sort some stuff to make it look cleaner
- **v1.0.8** (2026-04-25) – Add bash parameter handling for selective patching
- **v1.0.7** (2026-04-24) – Install patched Y1 music player APK
- **v1.0.6** (2026-04-24) – Install patched MtkBt.odex for AVRCP 1.3 Java selector fix
- **v1.0.5** (2026-04-23) – Fine tune echo statements
- **v1.0.4** (2026-04-23) – Use unmodified (non-sparse) system.img source
- **v1.0.3** (2026-04-23) – Add explicit Python virtual environment activation/deactivation
- **v1.0.2** (2026-04-23) – Convert app removal to loop for better readability
- **v1.0.1** (2026-04-23) – Append to build.prop instead of overwriting
- **v1.0.0** (2026-04-23) – Initial release

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
