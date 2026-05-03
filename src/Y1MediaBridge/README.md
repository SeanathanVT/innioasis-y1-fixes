# Y1MediaBridge

Single-APK AVRCP metadata bridge for the Innioasis Y1.

## Architecture

One system app. No separate daemon, no shared files, no IPC.

- **MediaBridgeService** — persistent service running as system app. Monitors
  logcat for Y1 player events, maintains track metadata and play state in
  memory, publishes to both the Android `RemoteControlClient` (for AVRCP
  metadata forwarding) and a single `Binder` that dispatches on the interface
  token at the head of each incoming Parcel — serving
  `com.mediatek.bluetooth.avrcp.IBTAvrcpMusic` and
  `com.android.music.IMediaPlaybackService` from the same process.

- **PlaySongReceiver** — static broadcast receiver for `MY_PLAY_SONG`,
  `BOOT_COMPLETED`, `ABOUT_SHUT_DOWN`, and `MEDIA_BUTTON`. Forwards media
  button events from the car directly to the stock player's
  `PlayControllerReceiver`.

## Build

```bash
./gradlew assembleRelease
```

Output: `app/build/outputs/apk/release/app-release.apk`.

Toolchain pinned in the tree: Gradle 8.11.1 wrapper, AGP 8.7.3, `compileSdk 34`,
`minSdk 17`, `targetSdk 17`, Java 8 bytecode. A manual `javac --release 8 -cp
android-34.jar` type-check of the source passes cleanly with zero errors (only
the expected "--release 8 is obsolete" note from JDK 21 and a `RemoteControlClient`
deprecation note — that deprecation is intentional, we target API 17).

## Install as system app

The APK *must* be installed as a system app — `READ_LOGS` is a signature/system
permission and `adb install` will leave it ungranted. Path inside `/system`:
`/system/app/Y1MediaBridge.apk` (flat file, not a subdirectory — the Y1 system
image's `app/` directory uses the flat layout). Mode `644`, owner `root:root`.
See [`../../innioasis-y1-fixes.bash`](../../innioasis-y1-fixes.bash) for the
full system.img patch + flash flow.

Ship alongside the patched binaries:

- Patched `/system/bin/mtkbt` — AVRCP 1.4 SDP record + runtime fixes (11 patches: B1-B3 AVCTP 1.0→1.3 in three SDP groups, C1-C3 AVRCP 1.0/1.3→1.4 in three ProfileDescList entries, A1 runtime SDP MOVW immediate, D1 NOP the registration guard, E3/E4 SupportedFeatures `0x0001/0x0021 → 0x0033`, E8 NOP the op_code=4 dispatcher slot-0 sign gate)
- Patched `/system/lib/libextavrcp_jni.so` — 4 patches (C2a/b in `BluetoothAvrcpService_activateConfig_3req` at `0x375c`: hardcode `g_tg_feature = 0x0e` and `sdpfeature = 0x23`; C3a/b in `getCapabilitiesRspNative` at `0x5e56`/`0x5e5c`: raise the EventList cap from 13 to 14)
- Patched `/system/lib/libextavrcp.so` — C4 (AVRCP version constant at `0x002e3b`, `0x0103 → 0x0104`)
- Patched `/system/app/MtkBt.odex` — F1 (`getPreferVersion()` returns `0x0e` (1.4) instead of `10`); F2 (resets `sPlayServiceInterface = false` in `BluetoothAvrcpService.disable()`). DEX adler32 recomputed.

Per-patch byte-level reference: [`../../docs/PATCHES.md`](../../docs/PATCHES.md).

## Verify install

```bash
# After reboot:
adb shell dumpsys package com.y1.mediabridge | grep codePath
# Expected: codePath=/system/app/Y1MediaBridge.apk

adb shell ls /data/dalvik-cache/ | grep y1
# Expected: a .dex file entry — means dexopt succeeded

adb shell ps | grep mediabridge
# Expected: running process

adb shell dumpsys package com.y1.mediabridge | grep READ_LOGS
# Expected: granted=true
```

## End-to-end test

Play a track on the Y1 player, then in one terminal:

```bash
adb logcat | grep -E "Y1MediaBridge|BT.AVRCP"
```

Expected sequence on car connect → track change:

1. Bind:
   ```
   Y1MediaBridge: onBind: com.android.music.MediaPlaybackService
   ```
2. Register — MtkBt hits us on code 1 with its callback binder; we log and
   echo current state:
   ```
   Y1MediaBridge: IBTAvrcpMusic.onTransact code=1 descriptor=com...IBTAvrcpMusic
   Y1MediaBridge: IBTAvrcpMusic.registerCallback total=1
   [BT][AVRCP][b] registercallback
   ```
3. Subscribe — car sends REGISTER_NOTIFICATION for EVENT_TRACK_CHANGED (0x05)
   and EVENT_PLAYBACK_STATUS_CHANGED (0x01); MtkBt relays as code 3:
   ```
   Y1MediaBridge: IBTAvrcpMusic.regNotificationEvent event=0x5 param=0
   Y1MediaBridge: IBTAvrcpMusic.regNotificationEvent event=0x1 param=0
   ```
4. Metadata pulls — codes 24/27/28/29/30/31 come through in a burst:
   ```
   Y1MediaBridge: IBTAvrcpMusic.onTransact code=24 ...
   Y1MediaBridge: IBTAvrcpMusic.onTransact code=28 ...
   ```
5. Track change — Y1 player logs Chinese prefix, we parse, notify:
   ```
   Y1MediaBridge: Track change: /storage/sdcard0/Music/...
   Y1MediaBridge: MediaStore hit: Title / Artist
   Y1MediaBridge: RCC metadata: Title / Artist
   ```
   No `onReceive EVENT_TRACK_CHANGED fail` line should appear after this point.

## Reverse engineering notes

The ground-truth transaction codes in `MediaBridgeService` come from:

```
MtkBt.odex (Dalvik 036 odex)
  → dex blob at offset 0x28, length 0x98490
  → MtkBt.dex (standard DEX 035 — androguard can parse metadata)
  → class com.mediatek.bluetooth.avrcp.IBTAvrcpMusic$Stub
    → static int TRANSACTION_* fields (every method → numeric code)
  → class com.android.music.IMediaPlaybackService$Stub
    → same pattern
```

DEX-level method bodies are odex-quickened (opcodes in the `0xe3–0xff` range,
resolved against the boot classpath vtables), so androguard can't disassemble
them fully without the original `boot.oat` / framework vtables. The interface
`TRANSACTION_*` constants live in the DEX encoded_array_item and come out
cleanly regardless — which is all we needed.

## Changes

### versionCode 9 (1.2)

- **`AndroidManifest.xml` versionCode synced to `9`** — previously hardcoded to `1`,
  which would cause the package manager to reject an upgrade from versionCode 8 as a
  downgrade on Android 4.2 installs.
- **`onBind` logging demoted** — was `Log.e` with a full stack trace on every bind.
  Reduced to `Log.d` with just the action. Binding happens on every car reconnect;
  the stack trace was only needed to confirm MtkBt was resolving to our service.
- **Duplicate-scan guard** — `mPendingScanPath` field prevents two concurrent
  `MediaScannerConnection.scanFile` calls for the same path when the Y1 player emits
  both a lyrics line (`刷新一次歌词`) and an album-art line (`刷新一次专辑图`) before
  the first scan completes. Second call is now a no-op; `mPendingScanPath` cleared
  in the scan callback before `broadcastTrackAndState`.

### versionCode 8 — `callbacks=0` fix

`attachInterface` was removed from `AvrcpBinder`. Adding it (versionCode 7) had
caused `IBTAvrcpMusic.Stub.asInterface()` to take the local path and cast our
`AvrcpBinder` to `IBTAvrcpMusic` — a cast it doesn't satisfy — silently
swallowing `registerCallback`. Removing `attachInterface` forces the remote
Proxy path for all callers; `registerCallback` now arrives as `onTransact
code=1` and populates `mAvrcpCallbacks`. Confirmed fixed: no `callbacks=0`
entries after first bind in the versionCode 9 session.

### versionCode 3 — full transaction-code coverage

Previous builds bound successfully and `registerCallback` ran, but cars still
logged `[BT][AVRCP] onReceive EVENT_TRACK_CHANGED fail`. Root cause: our
binder's `onTransact` only handled ~14 of the 37 codes `IBTAvrcpMusic$Stub`
actually declares.

Ground-truth transaction codes were extracted directly from the device's
`MtkBt.odex` (de-odex → `MtkBt.dex`, parsed with androguard) and every code
in `IBTAvrcpMusic$Stub` is now handled. In particular:

| Code | Method                                   | Why it matters                                |
|-----:|------------------------------------------|-----------------------------------------------|
|    3 | `regNotificationEvent(byte,int)->bool`   | **THE blocker** — car subscribe path          |
|    5 | `getCapabilities() -> byte[]`            | populates MtkBt `mCapabilities`               |
|   22 | `informDisplayableCharacterSet(int)->b`  | setup handshake                               |
|   23 | `informBatteryStatusOfCT() -> bool`      | setup handshake                               |
|   14,16,18,20 | set{Equalize,Shuffle,Repeat,Scan}Mode | previously fell through                    |
|   12,13 | prevGroup / nextGroup                 | remapped to prev / next keys                  |
|   32,33,34,35,37 | enqueue / getNowPlaying / etc.    | stubbed with typed zero replies               |

`handleMediaPlayback` (the AOSP-style `IMediaPlaybackService`) was also
re-aligned against the DEX ground truth: `position` is code 11 (not 30),
`duration` is code 10 (not 31), `getArtistName` is code 16 (not 15), and
`getAlbumId` / `getArtistId` were added at codes 15 / 17.

No behavior change to the logcat monitor, the RCC pipeline, the callback
notifier, or the lifecycle. All transactions that previously worked still
work identically.

## See also

- [`../../README.md`](../../README.md) — project overview
- [`../../docs/PATCHES.md`](../../docs/PATCHES.md) — per-patch byte-level reference for the firmware binaries this APK pairs with
- [`../../INVESTIGATION.md`](../../INVESTIGATION.md) — full AVRCP investigation narrative
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog
