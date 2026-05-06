# Y1MediaBridge

Single-APK AVRCP metadata bridge for the Innioasis Y1. Implements the in-Android-OS half of the metadata-forwarding pipe (`IBTAvrcpMusic` + `IMediaPlaybackService` Binder, plus an `RemoteControlClient` publish path). The bridge implementation itself is verified-correct (see [INVESTIGATION.md](../../docs/INVESTIGATION.md) "Verified true"); end-to-end metadata delivery is currently blocked by the native `mtkbt` daemon not relaying inbound AVRCP COMMANDs at the layer that would call into this bridge — see top-level [Status](../../README.md#status).

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
./gradlew --stop && ./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk`. The top-level
[`apply.bash`](../../apply.bash) reads from this path directly under
`--avrcp` — no need to copy the APK anywhere.

The `--stop` is defensive: gradle's daemon caches the JVM it started with,
so a `JAVA_HOME` change between builds doesn't take effect until the daemon
restarts. `--stop` first guarantees a fresh daemon. It's cheap (single
build) and safe to always run.

`assembleRelease` is intentionally avoided: AGP wires `lintVitalReportRelease`
into the release-assembly chain, which fails with `SDK location not found`
unless `local.properties` (`sdk.dir=...`) is configured. `assembleDebug` skips
that lint step. The `release` and `debug` APKs are otherwise structurally
identical here (`minifyEnabled false` on both; both signed with the debug
keystore per `app/build.gradle`'s `signingConfig signingConfigs.debug`). Debug
also leaves `debuggable=true` in the manifest, which is useful for a research
device — you can JDWP-attach to the running service.

Toolchain pinned in the tree: Gradle 9.5.0 wrapper, AGP 9.2.0, `compileSdk 34`,
`minSdk 17`, `targetSdk 17`, Java 8 bytecode. **Build with JDK 17 or newer.**
Confirmed working on AGP 9.2.0: JDK 25. Earlier AGP 8.7.3 builds also
confirmed JDK 17 and 21; both should still work on AGP 9.2.0 but only 25 has
been re-verified end-to-end. See
[`../../docs/ANDROID-SDK.md`](../../docs/ANDROID-SDK.md#jdk-requirement) for
install instructions and the gradle-daemon-caching gotcha to watch for if you
ever change `JAVA_HOME`.

The Java-8-bytecode target produces a "source/target version 8 is obsolete"
warning on JDK 21+. That's informational, not an error — Java 8 bytecode is
fully compatible with Android 4.2.2 via D8/dex, and we deliberately target
API 17. The warning will become an error in some future JDK; if/when that
happens, raise `sourceCompatibility` / `targetCompatibility` in
`app/build.gradle` to whatever JDK that release picks as the new floor.

## Install as system app

The APK *must* be installed as a system app — `READ_LOGS` is a signature/system
permission and `adb install` will leave it ungranted. Path inside `/system`:
`/system/app/Y1MediaBridge.apk` (flat file, not a subdirectory — the Y1 system
image's `app/` directory uses the flat layout). Mode `644`, owner `root:root`.
See [`../../apply.bash`](../../apply.bash) for the full system.img patch +
flash flow.

Ship alongside the patched binaries (current `--avrcp` stack — advertises **AVRCP 1.3** over AVCTP 1.2, since `mtkbt`'s shipped command-handling layer is internally 1.0 and 1.3 is the highest version where SDP advertisement, AVCTP, and trampoline coverage all line up):

- Patched `/system/bin/mtkbt` — V1 (AVRCP 1.0→1.3 SDP), V2 (AVCTP 1.0→1.2 SDP), S1 (replace `0x0311` SupportedFeatures slot with `0x0100` ServiceName), P1 (force fn 0x144bc op_code dispatch into the msg-519 emit path so VENDOR_DEPENDENT frames reach our trampolines)
- Patched `/system/lib/libextavrcp_jni.so` — R1 (redirect at 0x6538), T1 GetCapabilities trampoline at 0x7308, T2 stub at 0x72d4, and the iter17b T4 + extended_T2 + T5 trampoline blob in the LOAD #1 page-padding region at 0xac54 (state-tracked TRACK_CHANGED CHANGED + multi-attribute single-frame GetElementAttributes responses with strings read from `/data/data/com.y1.mediabridge/files/y1-track-info`, plus proactive CHANGED on Y1 track changes via the patched `notificationTrackChangedNative`)
- Patched `/system/app/MtkBt.odex` — F1 (`getPreferVersion()`), F2 (reset `sPlayServiceInterface` on disable), iter17a (NOP `if-eqz` cardinality gate at `0x3c530` so `notificationTrackChangedNative` fires on every track-change broadcast). DEX adler32 recomputed.

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
- [`../../docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) — full AVRCP investigation narrative
- [`../../CHANGELOG.md`](../../CHANGELOG.md) — top-level changelog
