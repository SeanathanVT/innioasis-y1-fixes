# Y1Bridge

Binder host for the Innioasis Y1 AVRCP pipeline. Hosts the
`IBTAvrcpMusic` Binder MtkBt resolves to via
`bindService(com.android.music.MediaPlaybackService)`, and serves the
synchronous state queries that drive MtkBt's Java-side mirror.

## Why this APK exists

MtkBt's `BTAvrcpMusicAdapter.checkAndBindPlayService` calls
`Context.bindService(Intent("com.android.music.MediaPlaybackService"))` to
find its AVRCP TG companion. The music app (`com.innioasis.y1`) cannot
declare this intent-filter in its manifest because it's signed with the OEM
platform key (required by `android:sharedUserId="android.uid.system"`) and
any change to `AndroidManifest.xml` invalidates `META-INF/MANIFEST.MF`'s
recorded SHA1-Digest. PackageManager rejects the APK at `/system/app/` scan
with "no certificates at entry AndroidManifest.xml; ignoring!" — see
[`docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md) Trace #23.

Y1Bridge is its own package (`com.koensayr.y1.bridge`), signed with the
debug keystore. Its manifest is freely editable. It exists solely to
declare the `<service>` MtkBt's `bindService` resolves to.

## What it does

- `MediaBridgeService.onBind` returns a `Binder` whose `onTransact`
  implements the `IBTAvrcpMusic` codes MtkBt's `BTAvrcpMusicAdapter`
  calls — primarily `getPlayStatus` (24), `position` (25), `duration` (26),
  `getAudioId` (27), `getTrackName` (28), `getAlbumName` (29),
  `getArtistName` (31), `getRepeatMode` (19), `getShuffleMode` (17), and
  `getCapabilities` (5) — by reading live values from
  `/data/data/com.innioasis.y1/files/y1-track-info` (the 1104-byte file
  maintained by the music app's injected `TrackInfoWriter`, world-readable
  per `setReadable(true, false)`). The Binder thread reads on every call
  so MtkBt's Java mirror always reflects current state. Callback-register,
  notification-register, setter, and passthrough codes (1, 2, 3, 4, 6–14,
  16, 18, 20, 22, 23, 32–37) ack with the success replies that keep
  `BTAvrcpMusicAdapter.mRegBit` armed.
- The proactive wake path is independent of the Binder: the music app
  fires `com.android.music.metachanged` / `playstatechanged`, MtkBt's
  cardinality-NOP-patched JNI natives fire, and the trampoline chain in
  `libextavrcp_jni.so` builds the wire response from the same
  `y1-track-info` file.
- `BootReceiver` listens for `BOOT_COMPLETED` and calls
  `startService(MediaBridgeService)` so the Service is alive when MtkBt
  first binds.

## What it does NOT do

All AVRCP observation + state production lives in the music app
(`com.innioasis.y1`) via the Patch B3..B6 smali injections in
`src/patches/inject/com/koensayr/y1/*`:

- `TrackInfoWriter` — writes `y1-track-info` / `y1-trampoline-state` /
  `y1-papp-set` under `/data/data/com.innioasis.y1/files/` (the trampoline
  chain in `libextavrcp_jni.so` reads from there).
- `PlaybackStateBridge` — hooks the music app's player engine
  (`Static.setPlayValue` + IjkMediaPlayer / `android.media.MediaPlayer`
  listener lambdas). State edges observed in-process, no logcat scraping,
  no foreground/background visibility gaps.
- `BatteryReceiver` — bucket-maps `ACTION_BATTERY_CHANGED` and fires
  `com.android.music.playstatechanged` so T9 emits `BATT_STATUS_CHANGED CHANGED`.
- `PappSetFileObserver` + `PappStateBroadcaster` — round-trip Repeat /
  Shuffle between the CT and the music app's SharedPreferences.

See [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md) for the full
trampoline chain reference.

## Build

```bash
cd src/Y1Bridge && ./gradlew --stop && ./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk` (~5-10 KB). `apply.bash
--avrcp` copies it to `/system/app/Y1Bridge.apk` at flash time.

Source is tiny — three files total:

- `app/src/main/java/com/koensayr/y1/bridge/MediaBridgeService.java` (~260 lines)
- `app/src/main/java/com/koensayr/y1/bridge/BootReceiver.java` (~28 lines)
- `app/src/main/AndroidManifest.xml` (~43 lines)
