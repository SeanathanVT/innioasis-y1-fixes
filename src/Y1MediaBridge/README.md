# Y1MediaBridge

Minimal Binder host for the Innioasis Y1 AVRCP pipeline.

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

Y1MediaBridge is its own package (`com.y1.mediabridge`), signed with the
debug keystore. Its manifest is freely editable. It exists solely to
declare the `<service>` MtkBt's `bindService` resolves to.

## What it does

- `MediaBridgeService.onBind` returns a `Binder` whose `onTransact` is
  ack-only for every code except `getCapabilities` (transact 5), which
  returns `[0x01 EVENT_PLAYBACK_STATUS_CHANGED, 0x02 EVENT_TRACK_CHANGED]` so
  MtkBt's adapter actually issues `REGISTER_NOTIFICATION` for those events.
  Per the Sonos capture in [`docs/INVESTIGATION.md`](../../docs/INVESTIGATION.md)
  Trace #21, MtkBt never transacts on this Binder past the initial
  capability query — the broadcast wake path (cardinality-NOP-patched JNI
  natives + `metachanged`/`playstatechanged` fired by the music app) is what
  drives T5/T9 on the wire.
- `PlaySongReceiver` listens for `BOOT_COMPLETED` and calls
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
cd src/Y1MediaBridge && ./gradlew --stop && ./gradlew assembleDebug
```

Output: `app/build/outputs/apk/debug/app-debug.apk` (~10 KB). `apply.bash
--avrcp` copies it to `/system/app/Y1MediaBridge.apk` at flash time.

Source is tiny — three files total:

- `app/src/main/java/com/y1/mediabridge/MediaBridgeService.java` (~130 lines)
- `app/src/main/java/com/y1/mediabridge/PlaySongReceiver.java` (~30 lines)
- `app/src/main/AndroidManifest.xml` (~45 lines)
