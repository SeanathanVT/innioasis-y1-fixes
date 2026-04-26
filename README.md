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

## Changes in 1.2 (versionCode 9)

**versionCode 9** fixes the remaining issues from the versionCode 8 session log.

### versionCode 9 changes
- **`AndroidManifest.xml` versionCode synced to `9`** — previously hardcoded to `1`,
  which would cause the package manager to reject an upgrade from versionCode 8 as a
  downgrade on Android 4.2 installs.
- **`onBind` logging demoted** — was `Log.e` with a full stack trace on every bind.
  Reduced to `Log.d` with just the action. Binding happens on every car reconnect;
  the stack trace was written to verify MtkBt was resolving to our service (confirmed
  in the versionCode 8 session — no longer needed).
- **Duplicate-scan guard** — `mPendingScanPath` field prevents two concurrent
  `MediaScannerConnection.scanFile` calls for the same path when the Y1 player emits
  both a lyrics line (`刷新一次歌词`) and an album-art line (`刷新一次专辑图`) before
  the first scan completes. The second call is now a no-op; `mPendingScanPath` is
  cleared in the scan callback before `broadcastTrackAndState`.

### versionCode 8 changes (prior session — callbacks=0 fix)
`attachInterface` was removed from `AvrcpBinder`. Adding it (versionCode 7) had
caused `IBTAvrcpMusic.Stub.asInterface()` to take the local path and cast our
`AvrcpBinder` to `IBTAvrcpMusic` — a cast it doesn't satisfy — silently swallowing
`registerCallback`. Removing `attachInterface` forces the remote Proxy path for all
callers; `registerCallback` now arrives as `onTransact code=1` and populates
`mAvrcpCallbacks`. Confirmed fixed: no `callbacks=0` entries after first bind in
the versionCode 9 session.

### versionCode 3/1.2 changes (full code coverage)
Previous builds bound successfully and `registerCallback` ran, but cars still
logged `[BT][AVRCP] onReceive EVENT_TRACK_CHANGED fail`. Root cause: our
binder's `onTransact` only handled ~14 of the 37 codes `IBTAvrcpMusic$Stub`
actually declares.

Ground truth transaction codes were extracted directly from the device's
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

## Build (on macOS host)

```bash
./gradlew assembleRelease
```

Output: `app/build/outputs/apk/release/app-release.apk`

Toolchain pinned in the tree: Gradle 8.11.1 wrapper, AGP 8.7.3, `compileSdk 34`,
`minSdk 17`, `targetSdk 17`, Java 8 bytecode. A manual `javac --release 8 -cp
android-34.jar` type-check of the source passes cleanly with zero errors (only
the expected "--release 8 is obsolete" note from JDK 21 and a `RemoteControlClient`
deprecation note — that deprecation is intentional, we target API 17).

## Install (MUST be system app for READ_LOGS)

APK path inside `/system`: `/system/app/Y1MediaBridge.apk` (flat file, not a
subdirectory — the Y1 system image's `app/` directory uses the flat layout).
Mode `644`, owner `root:root`. See `innioasis-y1-fixes.bash` for the full
system.img patch + flash flow.

Ship alongside:
- Patched `/system/bin/mtkbt` (AVRCP 1.4 SDP record; 6 patches: browse PSM zeroed, version/feature bytes set, ProfileDescList version `0x03→0x04`, descriptor table flags)
- Patched `/system/lib/libextavrcp_jni.so` (forces `g_tg_feature = 0x0e` (1.4), `sdpfeature = 0x23`; 2 byte patches at `0x3764`, `0x37a8`)
- Patched `/system/app/MtkBt.odex` (`getPreferVersion()` returns `0x0e` (1.4); DEX adler32 recomputed)

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

## Test — end-to-end

Play a track on the Y1 player, then in one terminal:
```bash
adb logcat | grep -E "Y1MediaBridge|BT.AVRCP"
```

Expected sequence on car connect → track change:

1. Bind:
   ```
   Y1MediaBridge: onBind: com.android.music.MediaPlaybackService
   ```
2. Register: MtkBt hits us on code 1 with its callback binder; we log and
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

DEX-level method bodies are odex-quickened (opcodes in the 0xe3–0xff range,
resolved against the boot classpath vtables), so androguard can't disassemble
them fully without the original `boot.oat` / framework vtables. The interface
TRANSACTION_* constants live in the DEX encoded_array_item and come out cleanly
regardless — which is all we needed.
