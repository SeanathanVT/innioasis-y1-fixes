# Plan — Y1MediaBridge Retirement & Music-App Integration

**Status:** Proposed (2026-05-10) — awaiting review.
**Branch:** new — `feature/retire-mediabridge` (off `feature/bluetooth-metadata`).
**Estimated scope:** 4 phases over multiple commits; one stop-the-world cutover at the file-path change (Phase 2 → 3 boundary).

This plan retires `Y1MediaBridge.apk` and migrates every responsibility it carries into smali patches injected directly into the Y1 stock music app (`com.innioasis.y1`). The trampoline chain in `libextavrcp_jni.so` and the byte patches in `mtkbt` / `MtkBt.odex` are unchanged in scope (one path-string update in `_trampolines.py` aside).

## 1. Why this is happening — the failure mode driving the pivot

The current architecture has three actors that exchange state across two process boundaries:

```
   ┌─────────────────────┐    LogcatMonitor              ┌──────────────────────┐
   │ com.innioasis.y1    │  scrape UI render lines ────▶ │ com.y1.mediabridge   │
   │ (music app process) │ ◀──── Intent (PApp Set)      │ (Y1MediaBridge proc) │
   │ + Patches B3/B4     │   PappStateBroadcaster ────▶ │ MediaBridgeService   │
   │                     │                              │ + LogcatMonitor      │
   │                     │                              │ + FileObserver       │
   │ MediaPlayer / state │                              │ + BatteryReceiver    │
   └─────────────────────┘                              └──────┬───────────────┘
                                                               │ file writes
                                              ┌────────────────▼──────────────┐
                                              │ /data/data/com.y1.mediabridge │
                                              │ /files/y1-track-info          │
                                              │       /y1-trampoline-state    │
                                              │       /y1-papp-set            │
                                              └────────────────┬──────────────┘
                                                               │ file reads
                                              ┌────────────────▼──────────────┐
                                              │ MtkBt.apk process             │
                                              │ libextavrcp_jni.so trampolines│
                                              │ (T1/T4/T5/T8/T9/T_papp/...)   │
                                              └───────────────────────────────┘
```

The Y1MediaBridge actor scrapes the music app's **UI render log lines** (`刷新一次专辑图` from `BasePlayerActivity`, `播放状态切换 N` from `BaseActivity`) to learn about state changes. Empirically — confirmed in the 2026-05-10 1119/1409/1901/1910 captures — this scrape:

- Only fires when the music app's UI activity is in the foreground. If the user navigates away from Now Playing or turns the screen off, `LogcatMonitor` goes blind, the music app keeps playing audio, and Y1MediaBridge's view of state diverges from reality.
- Is subject to the Android 4.2.2 logcat `-T` flag incompatibility (fixed 2026-05-10 by v3.7) and to spawn/pipe instability.
- Is reactive — it cannot drive state to a fresh CT at connect time before the user has interacted with the music UI.

The user-visible regressions traced to this scraping model: metadata stays empty until the user opens Now Playing; play/pause state freezes when the UI is backgrounded; the playhead timestamp drifts.

The architecture target removes the scrape boundary entirely. Music app gains direct in-process hooks at the playback engine layer; Y1MediaBridge.apk goes away.

## 2. Target architecture

```
   ┌──────────────────────────────────────────────────────────────────────┐
   │ com.innioasis.y1 (music app process)                                 │
   │ ┌──────────────────────────────────────────────────────────────────┐ │
   │ │ STOCK MUSIC APP                                                  │ │
   │ │   PlayerService (MediaPlayer host)                               │ │
   │ │   BasePlayerActivity (Now Playing UI)                            │ │
   │ │   SharedPreferencesUtils (musicRepeatMode / musicIsShuffle)      │ │
   │ │   Y1Application                                                  │ │
   │ └──────────────────────────────────────────────────────────────────┘ │
   │ ┌──────────────────────────────────────────────────────────────────┐ │
   │ │ INJECTED — com.koensayr.y1.*                                     │ │
   │ │   trackinfo.TrackInfoWriter — writes y1-track-info on edges      │ │
   │ │   playback.PlaybackStateBridge — hooks PlayerService callbacks   │ │
   │ │   papp.PappSetReceiver       (was Patch B3)                      │ │
   │ │   papp.PappStateBroadcaster  (was Patch B4)                      │ │
   │ │   papp.PappSetFileObserver — replaces Y1MediaBridge's observer   │ │
   │ │   battery.BatteryReceiver — battery edges                        │ │
   │ │   avrcp.AvrcpBridgeService — IBTAvrcpMusic+IMediaPlaybackService │ │
   │ └──────────────────────────────────────────────────────────────────┘ │
   └──────────────────────────────┬───────────────────────────────────────┘
                                  │ in-process file writes (edge-rate, small)
                ┌─────────────────▼─────────────────────────┐
                │ /data/data/com.innioasis.y1/files/        │
                │   y1-track-info       (800 B, atomic)     │
                │   y1-trampoline-state (written by T5/T9)  │
                │   y1-papp-set         (T_papp 0x14 write) │
                └─────────────────┬─────────────────────────┘
                                  │ file reads (per AVRCP request)
                ┌─────────────────▼─────────────────────────┐
                │ MtkBt.apk process                         │
                │ libextavrcp_jni.so trampolines (unchanged)│
                └───────────────────────────────────────────┘
```

Key properties:
- **One actor** writes state. Two file producers (trampoline writes `y1-trampoline-state` and `y1-papp-set` from native code; music app writes `y1-track-info` and `y1-papp-set` from Java) but exactly one process owns the producer side per file.
- **Engine-layer hooks, not UI-render scrapes.** State edges land in `TrackInfoWriter` directly from `MediaPlayer.OnCompletionListener` / `OnPreparedListener` / play-state transitions / SharedPreferences-write callbacks, regardless of UI focus.
- **One process boundary** (music app ↔ MtkBt.apk via files), down from two. No more cross-app `Intent`-based state sync; no more `LogcatMonitor`.
- **Same file format.** `y1-track-info` 800-byte schema (offsets 0..815) is unchanged. The trampoline chain reads the same bytes; we just move the writer from the bridge process to the music app process and update the path string.

## 3. Scope summary

| Area | Action |
|---|---|
| `src/Y1MediaBridge/` | **Delete** entire directory at end of project (Phase 4). |
| `src/patches/patch_y1_apk.py` | Major extension — new smali classes under `com/koensayr/y1/`, new manifest entries, new injection points. Existing patches A/B/C/E/H/H′/H″/B3/B4 untouched. |
| `src/patches/_trampolines.py` | Single-line path string change: `/data/data/com.y1.mediabridge/files/` → `/data/data/com.innioasis.y1/files/`. New patcher output MD5. |
| `src/patches/patch_libextavrcp_jni.py` | New `OUTPUT_MD5` after trampoline path change. |
| `src/patches/patch_mtkbt.py` | **Untouched.** |
| `src/patches/patch_mtkbt_odex.py` | **Untouched.** F1 / F2 / cardinality NOPs / dedupe NOP all still relevant. |
| `apply.bash` | Removes `Y1MediaBridge.apk` push step. Adds `pm uninstall com.y1.mediabridge` (best-effort) so stale data doesn't ghost the new install. |
| `docs/` | Major updates to PATCHES.md (delete Y1MediaBridge sections; document new injected classes); ARCHITECTURE.md (new data-path diagram); INVESTIGATION.md (append retirement trace); CHANGELOG.md (Removed: Y1MediaBridge; Added: in-app integration). |
| `mtkbt` (V1..V8 / S1 / P1) | **Untouched.** |
| `libaudio.a2dp.default.so` (AH1) | **Untouched.** |

## 4. Phase plan

### Phase 0 — Recon (no code changes; produces a recon doc only)

**Goal:** map every hook point in the stock music app's smali tree so Phase 1's injections are surgical, not exploratory.

Tasks:
1. Decode the v3.0.2 stock APK with `apktool d` into a clean working tree (separate from `staging/y1-apk/`, which has B3/B4 already applied).
2. Locate the **playback engine entry points**:
   - `MediaPlayer.OnCompletionListener` registration site(s) in `PlayerService` and its inner classes.
   - `MediaPlayer.OnPreparedListener` registration.
   - `MediaPlayer.OnErrorListener` registration (for "skip on decode error" track edges).
   - Wherever `MediaPlayer.start()` / `.pause()` / `.seekTo()` are called.
3. Locate **track-load** entry points:
   - The method that takes a `String path` and constructs `Uri` / opens the `MediaPlayer` (likely `PlayerService.play(String)` or similar).
   - Where `MediaMetadataRetriever` is used to extract Title/Artist/Album from the loaded file (this is what we currently re-run in `Y1MediaBridge`; in the unified design the music app's own extraction is the source of truth).
   - Path of `MediaStore` lookups, since the music app may already have cached metadata.
4. Locate **state-change emission sites**:
   - Where the music app sends `com.android.music.metachanged` / `playstatechanged` (we send these from Y1MediaBridge now; the music app may emit them natively too).
   - The `播放状态切换 N` log call site — this is the UI's response to a state change, so the underlying state-change call must precede it. We want the underlying call.
5. Locate **SharedPreferences write sites for Repeat / Shuffle**:
   - `SharedPreferencesUtils.setMusicRepeatMode(int)` and `setMusicIsShuffle(boolean)`. B3 reads these; B4 listens to them. Confirm both still exist post-patch.
6. Locate **the music app's existing Service declarations**:
   - `PlayerService` and `StateBarService` are visible in `smali_classes2/com/innioasis/y1/service/`. Confirm in `AndroidManifest.xml` (use `apktool d -s -r` to keep the manifest decoded).
   - Note their `android:exported`, `android:process`, intent filters. Whether `PlayerService` already declares `com.android.music.MediaPlaybackService` action (probably not — we'd have seen it bind to the music app instead of Y1MediaBridge if so).
7. Identify **multi-process behaviour**: does the music app declare `android:process=":remote"` on any service? If `PlayerService` lives in a separate process from `Y1Application`, the AVRCP bridge service must be co-resident with state writers — adjust accordingly.
8. Map the **boot / launch path**:
   - How does the music app start? Does `BootCompletedReceiver` exist? Does `PlayerService` get `START_STICKY`?
   - Will `MtkBt.apk`'s `bindService(Intent("com.android.music.MediaPlaybackService"))` cold-start the music app process if needed? (Yes, Android will, but the bridge service must be `android:exported="true"` and resolveable.)

**Deliverable:** `docs/RECON-MUSIC-APP-HOOKS.md` listing each smali file path, method signature, and instruction range we'll inject around or replace.

### Phase 1 — In-app state production (`Y1MediaBridge.apk` still installed but bypassed)

**Goal:** make the music app the source of truth for `y1-track-info`, with Y1MediaBridge's `MediaBridgeService` still on disk as a no-op safety net.

This phase keeps both producers live to make the cutover reversible. Y1MediaBridge keeps writing to `/data/data/com.y1.mediabridge/files/y1-track-info` (its current path); the music app starts writing to `/data/data/com.innioasis.y1/files/y1-track-info` (the new path). The trampolines still read the old path. We verify the new path's bytes are correct via `adb pull` before cutting over.

Tasks:

1. **New smali skeleton** under `com/koensayr/y1/`:
   - `com.koensayr.y1.trackinfo.TrackInfoWriter` — owns the 800-byte file write. Schema constants mirror `MediaBridgeService.java:1566-1635` (TRACK_ID_LEN, TITLE_OFFSET, etc.). Atomic via `tmp + rename`. World-readable on creation.
   - `com.koensayr.y1.trackinfo.TrackInfoState` — in-process state holder (Repeat/Shuffle/Battery/PlayStatus/CurrentTrack — title/artist/album/audio_id/duration/position/state_change_time). All writes go through this; it batches the file write.
   - `com.koensayr.y1.playback.PlaybackStateBridge` — registered from `PlayerService.onCreate()` via a smali insert. Wraps the existing `MediaPlayer` listeners with composite versions that call through to TrackInfoState on every edge.
   - `com.koensayr.y1.papp.PappSetReceiver` — moves `com/koensayr/PappSetReceiver.smali` (Patch B3) into the new namespace. Functionality unchanged.
   - `com.koensayr.y1.papp.PappStateBroadcaster` — moves `com/koensayr/PappStateBroadcaster.smali` (Patch B4) into the new namespace; instead of (or in addition to) broadcasting an Intent, it calls `TrackInfoState.setRepeat/setShuffle` directly. Internal IPC → in-process call.
   - `com.koensayr.y1.papp.PappSetFileObserver` — Android `FileObserver` on `/data/data/com.innioasis.y1/files/y1-papp-set`. Watches for the trampoline T_papp 0x14 write; reads 2 bytes; calls `SharedPreferencesUtils.setMusicRepeatMode / setMusicIsShuffle` directly (no Intent hop). Replaces Y1MediaBridge's FileObserver.
   - `com.koensayr.y1.battery.BatteryReceiver` — `Intent.ACTION_BATTERY_CHANGED` receiver; bucket-maps to AVRCP §5.4.2 Tbl 5.35 enum; calls `TrackInfoState.setBattery`.

2. **Y1Application.onCreate injection** (already used by B3/B4 — extend the existing patch):
   ```smali
   # Existing B3 + B4 registrations
   # NEW:
   invoke-static {p0}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->register(Landroid/content/Context;)V
   invoke-static {p0}, Lcom/koensayr/y1/papp/PappSetFileObserver;->start(Landroid/content/Context;)V
   invoke-static {p0}, Lcom/koensayr/y1/battery/BatteryReceiver;->register(Landroid/content/Context;)V
   invoke-static {p0}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->prepareFiles(Landroid/content/Context;)V
   ```

3. **PlayerService smali hooks** at the points identified in Phase 0:
   - At `MediaPlayer.start()` invocation: insert `invoke-static` to `PlaybackStateBridge.onPlay()`.
   - At `MediaPlayer.pause()`: `PlaybackStateBridge.onPause()`.
   - At track-load completion (probably in `OnPreparedListener.onPrepared` or right after `MediaPlayer.prepare()`): `PlaybackStateBridge.onTrackLoaded(path, title, artist, album, duration, audioId)`.
   - At `OnCompletionListener.onCompletion`: `PlaybackStateBridge.onTrackCompleted()` (drives natural-end detection).

4. **Periodic position-tick** — `PlaybackStateBridge` schedules a 1-second tick (Java `Handler` from a Looper thread) while `isPlaying`; the tick re-fires the `y1-track-info` write so T6 (`GetPlayStatus`) returns a fresh `mPositionAtStateChange` anchor without `LogcatMonitor`'s noise. This subsumes `MediaBridgeService.schedulePosTick()`.

5. **PApp state initialisation at startup** — `TrackInfoState.init()` reads `SharedPreferencesUtils.musicRepeatMode` / `musicIsShuffle` synchronously on app start (via `Y1Application.onCreate`) so the initial `y1-track-info[795..796]` bytes are correct from cold boot. Closes the "stale default 0x01/0x01 OFF/OFF until user toggles" gap we currently have.

6. **Patcher integration** — `patch_y1_apk.py` grows new smali file write-outs + manifest patch entries. Re-pinned APK MD5.

7. **Trampoline path: not yet changed.** Y1MediaBridge still writes the canonical file. Music app writes a parallel copy. We diff the two via `adb shell md5sum`.

8. **Verification gate** before Phase 2:
   - Both files exist on device and update in lockstep.
   - Bytes 0..815 of music-app file match Y1MediaBridge file within ±100 ms of every edge.
   - Music app's file updates even when Now Playing is backgrounded (use `adb shell input keyevent KEYCODE_HOME` to background the app, then trigger a track change via Bluetooth PASSTHROUGH).
   - No crashes / ANRs in music app process across a 30-minute session.

### Phase 2 — File-path cutover

**Goal:** trampolines start reading from the music app's path; Y1MediaBridge becomes a no-op.

Tasks:

1. `_trampolines.py` — update three `asciiz` literals:
   ```python
   a.label("path_track_info")
   a.asciiz("/data/data/com.innioasis.y1/files/y1-track-info")
   a.label("path_state")
   a.asciiz("/data/data/com.innioasis.y1/files/y1-trampoline-state")
   a.label("path_papp_set")
   a.asciiz("/data/data/com.innioasis.y1/files/y1-papp-set")
   ```
2. Re-compute `libextavrcp_jni.so` `OUTPUT_MD5`.
3. `patch_y1_apk.py` ensures the music app creates the three files at `Y1Application.onCreate` (`TrackInfoWriter.prepareFiles()` does this already from Phase 1) with world-readable bits so MtkBt's process can read them.
4. **Y1MediaBridge's writes are still happening, but to a path the trampolines no longer read.** Effectively dead but installed.

Verification gate:
- AVRCP CT connect → metadata flows on the wire correctly (verify with one of: Kia, Bolt, TV).
- T_papp 0x14 Set still reaches the music app (PappSetFileObserver in music app picks up the write at the new path).
- Play/pause state edges still drive CHANGED notifications.

### Phase 3 — Retire Y1MediaBridge.apk

**Goal:** remove the bridge APK from the device and from the build pipeline.

Tasks:

1. **Music app manifest** gains the two AVRCP intent filters (`com.android.music.MediaPlaybackService` and `com.android.music.IMediaPlaybackService`) on a new exported service `com.koensayr.y1.avrcp.AvrcpBridgeService`. Smali for this service is a thin shell that implements:
   - `IBTAvrcpMusic.Stub` Binder methods (the methods MtkBt's `BTAvrcpMusicAdapter` actually calls — to be enumerated in Phase 0 by examining `MtkBt.odex`'s smali for `IBTAvrcpMusic` consumer code).
   - `IMediaPlaybackService.Stub` Binder methods (likewise).
   - The Binder methods read from `TrackInfoState` (in-process, fast) rather than from a file.
   - For methods that need to drive playback (play / pause / next / previous), they call the music app's own `PlayerService` directly.

2. **AIDL stubs** — the `IBTAvrcpMusic.aidl` and `IBTAvrcpMusicCallback.aidl` (visible as strings in `MtkBt.odex` at `0x62d10` / `0x62d24`) need their generated smali to live somewhere in the music app's DEX. Phase 0 will determine whether we extract the AIDL definitions from MtkBt's APK or re-derive them from the `.odex` strings; either way the generated `Stub` / `Proxy` smali goes under `com/koensayr/y1/avrcp/`.

3. **Manifest install priority** — set `android:priority="100"` on the music app's filter so during the transition window (both apps installed) Android PMS resolves to the music app.

4. `apply.bash` — three changes:
   - Remove the `adb push Y1MediaBridge.apk → /system/app/` step.
   - Add `adb shell rm -rf /system/app/Y1MediaBridge.apk /system/app/Y1MediaBridge/ /data/data/com.y1.mediabridge/` (after remounting `/system` rw, then ro again).
   - Add `adb shell pm uninstall com.y1.mediabridge 2>/dev/null || true` (defensive — covers earlier non-system installs).

5. **Reboot after install** — so `PackageManager` re-scans `/system/app/` and forgets the old bridge.

Verification gate:
- `adb shell pm list packages | grep mediabridge` returns empty.
- `adb shell ls /system/app/Y1MediaBridge*` returns "No such file or directory".
- MtkBt's `Adapter onConnect` path binds successfully to the music app's `AvrcpBridgeService` (`MMI_AVRCP: PlayService onServiceConnected className:com.koensayr.y1.avrcp.AvrcpBridgeService`).
- Every Bolt + Kia + TV scenario still passes (metadata, PASSTHROUGH, PApp, state edges).

### Phase 4 — Cleanup

Tasks:

1. **Delete `src/Y1MediaBridge/`** entirely. ~2100 LoC + Gradle/AGP build infra goes away.
2. **Update `docs/PATCHES.md`** — delete the Y1MediaBridge section; document the new `com.koensayr.y1.*` injected classes; refresh the architecture overview.
3. **Update `docs/ARCHITECTURE.md`** — new data-path diagram (the one in §2 of this plan); remove the "external observer" descriptions; update the "actor cast" listing.
4. **Update `docs/INVESTIGATION.md`** — append a chronological "Trace #N — Y1MediaBridge retirement (2026-05-XX)" entry documenting the migration, the symptoms it cured, and the architectural rationale.
5. **Update `CHANGELOG.md`** — under [Unreleased] add `### Removed: Y1MediaBridge.apk` and `### Changed: AVRCP TG state production now lives inside the music app`.
6. **Update top-level `README.md`** — drop the Y1MediaBridge build instructions; consolidate the build flow under `apply.bash --avrcp`.
7. **Remove any remaining `com.y1.mediabridge` strings** from the repository (`git grep -i mediabridge`).
8. **Memory note** — update `project_y1_mods_status.md` to reflect the new architecture.

## 5. Risk analysis

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| AIDL stubs in music app don't match MtkBt's exact transaction codes | Medium | High — MtkBt's bind fails or Binder calls misroute | Phase 0 includes extracting MtkBt's smali to confirm IBTAvrcpMusic interface signature byte-for-byte; if mismatch, abort or copy MtkBt's AIDL `.aidl` source by reverse-engineering. |
| Music app process gets killed under memory pressure → AvrcpBridgeService dies → MtkBt's Binder breaks | Low | Medium — re-pairing the CT recovers, but it's a UX wart | `AvrcpBridgeService.onStartCommand` returns `START_STICKY`; mark `<application android:persistent="true">` for the music app (matches Y1MediaBridge's current setup). |
| `MtkBt`'s `bindService` resolution picks Y1MediaBridge over the music app during the transition window | Medium | Low — extends Phase 2 cleanup | Set `android:priority="100"` on the music-app intent filter; uninstall Y1MediaBridge cleanly at Phase 3. |
| `TrackInfoWriter` writes during music app foreground GC pause take >500 ms → CT request times out | Low | Medium | Buffer writes off-thread via a single-thread `Executor`; flush inline only on AVRCP-CT-connect detection. Same pattern Y1MediaBridge currently uses. |
| Smali patcher tooling can't inject all the new classes in one apktool round-trip | Low | Medium | We already inject 4 receivers + manifest changes via `patch_y1_apk.py`; the new classes are larger but the same mechanism. If apktool's smali assembler chokes (Java 22+ issue known), the patcher's existing DEX-signature check catches it. |
| File-write race between `TrackInfoWriter` and `T9` (which writes `y1-trampoline-state`) | Negligible | Low | These are different files; T9 writes `y1-trampoline-state`, music app writes `y1-track-info`. No overlap. |
| The `com.android.music.metachanged` and `playstatechanged` broadcasts currently sent by Y1MediaBridge are consumed somewhere we don't know about | Low | Medium | Audit `git grep -i metachanged playstatechanged` across `src/` and `MtkBt.odex` strings; replicate the broadcast emission inside `PlaybackStateBridge` if needed. Likely the only consumer is `MtkBt.apk`'s patched `notificationPlayStatusChangedNative` / `notificationTrackChangedNative` paths, which fire fine from intra-process broadcast. |
| `/data/data/com.innioasis.y1/files/` permissions don't allow MtkBt's process (uid bluetooth) to read | Low | High | `TrackInfoWriter.prepareFiles()` calls `File.setReadable(true, false)` on creation, same as `MediaBridgeService.writeTrackInfoFile()` does today. Directory permissions inherited; if `/data/data/com.innioasis.y1/` doesn't have +x for `others`, we may need to set `1755` on the dir. Verifiable in Phase 1. |

## 6. Test plan

Cumulative — Phase N's verification gate must pass before Phase N+1 starts.

### Phase 0 → 1 gate
- Recon doc enumerates every hook site and the smali instructions immediately before / after where the insert lands.
- Stock music APK decodes cleanly with `apktool d`.

### Phase 1 → 2 gate
- Patcher produces a new APK that installs to `/system/app/com.innioasis.y1/`.
- Music app starts, doesn't crash.
- `adb shell ls -la /data/data/com.innioasis.y1/files/` shows `y1-track-info`, `y1-trampoline-state`, `y1-papp-set` with `-rw-r--r--` perms.
- `adb shell md5sum /data/data/com.innioasis.y1/files/y1-track-info /data/data/com.y1.mediabridge/files/y1-track-info` are equal after each state edge.
- Music-app-side file updates with the music app backgrounded (`KEYCODE_HOME`-style scenario).
- No `Logcat pipe closed` references in `Y1MediaBridge` log (it should be deprecated / disabled).

### Phase 2 → 3 gate
- Bolt: cold-connect → metadata visible within one polling cycle.
- Kia: cold-connect → metadata visible; no `disconnect_ind` storm (T_papp 0x13 n=1 fix already covers this).
- TV: PASSTHROUGH PLAY/PAUSE/NEXT/PREVIOUS work; track changes show up on the head unit.
- All three: PApp Set Repeat and Shuffle round-trips work (CT → Y1 + Y1 → CT).

### Phase 3 → 4 gate
- `pm list packages | grep mediabridge` empty.
- No bind to `com.y1.mediabridge.*` in `logcat | grep AVRCP`.
- All three CT scenarios from the Phase 2 gate, repeated.

### Phase 4 acceptance
- `git grep -i mediabridge` returns only changelog / investigation references.
- `apply.bash --avrcp` succeeds without `Y1MediaBridge.apk` present anywhere.
- `docs/ARCHITECTURE.md` data-path diagram matches the new reality.

## 7. Rollback plan

Each phase is reversible without losing user data, but rollback gets more costly the later we are:

- **Phase 1 rollback** — revert the patcher commits; `apply.bash --music-apk` reinstalls the previous music APK. Y1MediaBridge unaffected, so previous behaviour restored entirely.
- **Phase 2 rollback** — revert the `_trampolines.py` path change + re-pin `OUTPUT_MD5`; flash the previous `libextavrcp_jni.so`. Trampolines re-read Y1MediaBridge's files.
- **Phase 3 rollback** — push `Y1MediaBridge.apk` back to `/system/app/`; remove the music-app intent filter (revert the manifest patch). Reboot. MtkBt re-binds to Y1MediaBridge.
- **Phase 4 rollback** — restore `src/Y1MediaBridge/` from git history (it's not deleted from history, just from `HEAD`).

## 8. Deliverable file list

To be produced over the project (not in this plan doc):

- `docs/RECON-MUSIC-APP-HOOKS.md` (Phase 0)
- `src/patches/inject/com/koensayr/y1/trackinfo/TrackInfoWriter.smali` (Phase 1)
- `src/patches/inject/com/koensayr/y1/trackinfo/TrackInfoState.smali`
- `src/patches/inject/com/koensayr/y1/playback/PlaybackStateBridge.smali`
- `src/patches/inject/com/koensayr/y1/papp/PappSetReceiver.smali` (moved from `com/koensayr/`)
- `src/patches/inject/com/koensayr/y1/papp/PappStateBroadcaster.smali` (moved)
- `src/patches/inject/com/koensayr/y1/papp/PappSetFileObserver.smali`
- `src/patches/inject/com/koensayr/y1/battery/BatteryReceiver.smali`
- `src/patches/inject/com/koensayr/y1/avrcp/AvrcpBridgeService.smali` (Phase 3)
- `src/patches/inject/com/koensayr/y1/avrcp/IBTAvrcpMusic$Stub.smali`
- `src/patches/inject/com/koensayr/y1/avrcp/IMediaPlaybackService$Stub.smali`
- Modified `src/patches/patch_y1_apk.py` (Phase 1 + Phase 3 manifest amendments)
- Modified `src/patches/_trampolines.py` (Phase 2 path strings)
- Modified `src/patches/patch_libextavrcp_jni.py` (Phase 2 `OUTPUT_MD5`)
- Modified `apply.bash` (Phase 3 install/uninstall flow)
- Deleted `src/Y1MediaBridge/` (Phase 4)

## 9. Out of scope

- **Re-implementing AVRCP TG entirely in Java.** Trampolines stay in `libextavrcp_jni.so`; they're the right tool. This plan keeps the file-based trampoline IPC because the alternatives (shared memory, Binder-from-native) add complexity that doesn't pay back at our state-edge rate.
- **Touching `mtkbt` byte patches (V1..V8 / S1 / P1).** SDP record shape and op-code dispatch are settled.
- **Touching `MtkBt.odex` F1/F2/cardinality NOPs/dedupe NOP.** These unblock the AVRCP TG dispatch chain in MtkBt; orthogonal to where the bind target lives.
- **Touching `libaudio.a2dp.default.so` (AH1).** A2DP standby behaviour is unrelated.

## 10. Single-sentence summary

Move every responsibility currently in `Y1MediaBridge.apk` into smali patches injected at `com.koensayr.y1.*` inside the stock `com.innioasis.y1` music APK; keep the trampoline chain in `libextavrcp_jni.so`; update three path strings; delete the old APK.
