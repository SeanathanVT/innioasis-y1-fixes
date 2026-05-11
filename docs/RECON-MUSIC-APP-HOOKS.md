# RECON — Music App Hook Sites for Y1MediaBridge Retirement

Phase 0 deliverable for [`PLAN-Y1MEDIABRIDGE-RETIREMENT.md`](PLAN-Y1MEDIABRIDGE-RETIREMENT.md). Catalogues every smali site Phase 1 will inject into or read from, in `com.innioasis.y1` v3.0.2 stock (input APK md5 `d2cd2841305830db2daf388cb9866c67`).

Source: clean apktool 2.9.3 decode at `staging/y1-apk-stock/` (gitignored). Stock APK extracted from `system.img.extracted/app/com.innioasis.y1_3.0.2.apk`.

## 0. Top-line deltas vs. plan assumptions

| Plan §4 assumed | Actual stock state | Phase 1 impact |
|---|---|---|
| `MediaPlayer.OnCompletion/Prepared/ErrorListener` registration | Primary engine is `tv.danmaku.ijk.media.player.IjkMediaPlayer` (Bilibili IJK FFmpeg fork); secondary is `android.media.MediaPlayer` (`player2`). Listener interfaces are `IMediaPlayer$OnCompletionListener` etc. for IJK path. | Hook BOTH lambda paths — `initPlayer$lambda-{10,11,12}` for IJK and `initPlayer2$lambda-{13,14,15}` for MediaPlayer. Same hook surface, different listener types. |
| `MediaMetadataRetriever` extracts Title/Artist/Album per track | Stock app does no MMR — metadata lives in the Room `Song` entity (already populated at scan time). | `TrackInfoWriter` reads `PlayerService.getPlayingMusic()` / `getPlayingAudiobook()` Song getters directly. No re-extraction. |
| Music app emits `com.android.music.metachanged` / `playstatechanged` natively | **It doesn't.** Y1MediaBridge is the sole sender today. | `PlaybackStateBridge` either replicates the broadcasts (cardinality NOPs use them as wakeup) for Phase 1+2 compatibility, or skips them in Phase 3 once `AvrcpBridgeService` calls back into MtkBt directly. |
| `BasePlayerActivity` Chinese log line `刷新一次专辑图` is the canonical track-edge signal | It's a UI render reaction — fires *after* the underlying state-change. The cause is in `PlayerService` writing `Song`; `BasePlayerActivity$handler` observes and renders. | Don't hook the log site. Hook `Static.setPlayValue(II)V` (single canonical state-edge entry) and the listener lambdas in `PlayerService` (single track-load entry per engine). |
| Multi-process risk on `PlayerService` | No `android:process` anywhere in the manifest. Single-process app. | No special handling. `AvrcpBridgeService` is co-resident with `Y1Application` and `PlayerService` automatically. |

## 1. Application bootstrap

**`Y1Application`** — `smali/com/innioasis/y1/Y1Application.smali`.
- Extends `androidx/multidex/MultiDexApplication`.
- `onCreate()V` is the canonical bootstrap (line 339, `.locals 9`). Already starts + binds `StateBarService`, `FmRadioService`, `PlayerService` and seeds `appContext` static at line 409.
- Static accessors: `Companion.getApp()`, `Companion.getAppContext()`, `Companion.getPlayerService()` (returns `Lcom/innioasis/y1/service/PlayerService;` once `Y1Application$onCreate$2.onServiceConnected` fires).
- B3 + B4 already inject here. Phase 1 extends the same anchor — append our `register/start/init` calls after the existing `MultiDex.install` (line 412), before the `startService(StateBarService)` block at line 415.

**`Y1Application$onCreate$2`** — `smali/com/innioasis/y1/Y1Application$onCreate$2.smali`. PlayerService ServiceConnection. `onServiceConnected` (line 57) calls `Y1Application.Companion.setPlayerService(svc)` — this is where the in-process `PlayerService` reference becomes available. **Phase 1 inject point** for `PlaybackStateBridge.attach(svc)` if we want to register listeners reactively (only useful if we don't hook the listener lambdas directly — see §3).

**`BootCompletedReceiver`** — `smali_classes2/com/innioasis/y1/receiver/BootCompletedReceiver.smali`. Fires on `BOOT_COMPLETED` + `ACTION_SHUTDOWN`. Registered + exported. `onReceive` already handles `restoreShutdownState` via a kotlinx coroutine. Inject point not needed — Application is bootstrapped before this fires anyway.

**Manifest** — `<application>` has neither `android:persistent="true"` nor `android:process`. Phase 1 manifest patch must add `android:persistent="true"` (matches Y1MediaBridge's current behaviour; risk row #2 in plan).

## 2. State-source-of-truth: `Static.setPlayValue`

**`com.innioasis.y1.utils.Static`** — `smali_classes2/com/innioasis/y1/utils/Static.smali`. Kotlin object (singleton via `INSTANCE`). Holds five MutableLiveData properties: `mPlayValue`, `mBatteryValue`, `mBluetoothValue`, `mHeadsetValue`, `mTimeValue`.

| Method | Line | Signature | Hook |
|---|---|---|---|
| `setPlayValue(II)V` | 334 | `(int newValue, int reason)` — every play-state edge passes through; `reason` matches `play()/pause()/stop()` call-site IDs (4/5/6). | **Phase 1 primary hook.** Inject `invoke-static {p1, p2}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onPlayValue(II)V` at the top. Replaces the logcat-scrape source-of-truth entirely. |
| `setBatteryValue(Lcom/innioasis/y1/utils/Static$Battery;)V` | 289 | `Static$Battery` enum — set by `BatteryReceiver` elsewhere | Optional Phase 1 hook (but Phase 1 plan §1.6 calls for our own `BatteryReceiver` — we don't need this). |

**Why this is the right hook:** `setPlayValue` is invoked from the public play/pause/stop methods directly (`PlayerService.play(Z)V` line 4585, 4604, 4653; `pause(IZ)V` line 4298+; `stop()V` line 7009+). The `BaseActivity.setObserve$lambda-7` log line `播放状态切换 N` (line 819) we currently scrape is the LiveData *observer's* reaction — fires after `setPlayValue` updates the LiveData. Hooking the observer means waiting for activity resume. Hooking `setPlayValue` catches every edge regardless of foreground state.

## 3. Playback engine entry points

**`PlayerService`** — `smali/com/innioasis/y1/service/PlayerService.smali` (7150 lines). Inner classes in `smali_classes2/com/innioasis/y1/service/`.

### Listener registration (one-time, at engine init)

| Method | Line | Engine | Listeners registered |
|---|---|---|---|
| `initPlayer()V` | 875 | `IjkMediaPlayer` (`player`) | `setOnCompletionListener` (913), `setOnPreparedListener` (924), `setOnErrorListener` (935) |
| `initPlayer2()V` | 1091 | `MediaPlayer` (`player2`) | `setOnCompletionListener` (1119), `setOnPreparedListener` (1130), `setOnErrorListener` (1141) |

Listeners are R8 lambda thunks — they delegate to:
- `initPlayer$lambda-10` (line 940) — IJK OnPrepared
- `initPlayer$lambda-11` (line 1042) — IJK OnCompletion
- `initPlayer$lambda-12` (line 1062) — IJK OnError
- `initPlayer2$lambda-13` (line 1146) — MediaPlayer OnPrepared
- `initPlayer2$lambda-14` (line 1341) — MediaPlayer OnCompletion
- `initPlayer2$lambda-15` (line 1365) — MediaPlayer OnError

**Phase 1 hook**: prepend `invoke-static` to `PlaybackStateBridge.onPrepared(player)` / `onCompletion(player)` / `onError(player, what, extra)` at the top of each lambda. Pure inserts; no logic replacement.

### Public state-change methods

| Method | Line | Notes |
|---|---|---|
| `play(Z)V` | 4508 | Branches by `playing` enum (`PlayerService$Playing` ordinal): cond_3 = first play (`playerPrepared` path), cond_2 = resume from pause, cond_0 = FM-radio resume. Ends with `sendBroadcast(MY_PLAY_SONG)` at line 4668. |
| `pause(IZ)V` | 4298 | `(int reason, boolean propagate)` — Patch E calls `pause(0x12, true)`. |
| `stop()V` | 7009 | |
| `nextSong()V` | 3925 | discrete-track NEXT |
| `prevSong()V` | 4906 | discrete-track PREV |
| `playOrPause()V` | 4673 | toggle (legacy single physical key) |

These are **not** the primary hook surface — `setPlayValue` already catches the resulting state edges. They're noted for reference and for the Phase 3 `AvrcpBridgeService` Binder methods (which need to call into them when the peer CT issues `play()` / `pause()` / `next()` etc.).

### Track-load entry point

`play(Z)V` cond_3 branch (line 4609+) is where a fresh track is loaded:
1. `getPlayingMusic()` returns the current `Song` (line 4610).
2. `isUseIjk(path)` selects engine (line 4624).
3. `IjkMediaPlayer.start()` (line 4635) or `MediaPlayer.start()` (line 4645).

Track-load is fully captured by hooking the `OnPreparedListener` lambdas (§3 above) — `onPrepared` fires after `prepareAsync` completes, which is the moment metadata is loadable. No additional hook needed at the play() entry.

### State + metadata accessors (read-only, used by `TrackInfoWriter`)

| Accessor | Returns | Source |
|---|---|---|
| `getPlayingMusic()` | `Lcom/innioasis/y1/database/Song;` | line 3251 |
| `getPlayingAudiobook()` | `Lcom/innioasis/y1/database/Song;` | line 3188 |
| `getPlayingSong()` | `Song` (whichever is active) | line 3314 |
| `getCurrentPosition()` | `J` (ms) | line 2830 |
| `getDuration()` | `J` (ms) | line 2922 |
| `getMusicIndex()` | `I` (TrackNumber - 1) | line 3011 |
| `getMusicList()` | `Ljava/util/List;` (`.size()` = TotalNumberOfTracks) | line 3020 |
| `getRepeatState()` | `I` (0=OFF, 1=ONE, 2=ALL) | line 3367 |
| `getPlaying()` | `Lcom/innioasis/y1/service/PlayerService$Playing;` enum | line 3179 |
| `getPlayerIsPrepared()` | `Z` | line 3170 |

All public, all read-from-anywhere. `TrackInfoWriter` calls these whenever `setPlayValue` or one of the listener lambdas fires.

## 4. Track metadata schema (`Song` Room entity)

**`com.innioasis.y1.database.Song`** — `smali_classes2/com/innioasis/y1/database/Song.smali`.

Fields and getters:

| Field | Line (decl) | Getter | AVRCP §5.3.4 attribute |
|---|---|---|---|
| `songName: String` | 142 | `getSongName()` line 1510 | 0x01 Title |
| `name: String` (file basename?) | 126 | `getName()` line 1438 | — |
| `artist: String` | 112 | `getArtist()` line 1384 | 0x02 Artist |
| `album: String` | 110 | `getAlbum()` line 1375 | 0x03 Album |
| `genre: String` | 118 | `getGenre()` line 1411 | 0x06 Genre |
| `songId: String` | 140 | `getSongId()` line 1501 | (audio_id used internally) |
| `path: String` | 128 | `getPath()` line 1447 | — |
| `date: J` | 114 | `getDate()` line 1393 | — |

No `duration` field on `Song`. Phase 1's `TrackInfoState` sources duration from `PlayerService.getDuration()` (live from the engine).

No `track_number` on the entity either — Phase 1 uses `(PlayerService.getMusicIndex() + 1)` as TrackNumber and `getMusicList().size()` as TotalNumberOfTracks (matches Y1MediaBridge's current behaviour).

## 5. PApp (Repeat / Shuffle) state plumbing

**`com.innioasis.y1.utils.SharedPreferencesUtils`** — `smali/com/innioasis/y1/utils/SharedPreferencesUtils.smali`. Kotlin object singleton (`INSTANCE`).

| Method | Line | Used by |
|---|---|---|
| `getMusicIsShuffle()Z` | 693 | reads pref key `"musicIsShuffle"` |
| `getMusicRepeatMode()I` | 719 | reads pref key `"musicRepeatMode"` (0=OFF, 1=ONE, 2=ALL per `PlayerService.REPEAT_MODE_*`) |
| `setMusicIsShuffle(Z)V` | 2380 | writes pref key `"musicIsShuffle"` |
| `setMusicRepeatMode(I)V` | 2419 | writes pref key `"musicRepeatMode"` |

Pref file: `"settings"` (per existing B4 OnSharedPreferenceChangeListener registration).

**Phase 1 wiring**:
- `PappSetFileObserver` (FileObserver on `/data/data/com.innioasis.y1/files/y1-papp-set`) calls `SharedPreferencesUtils.INSTANCE.setMusicRepeatMode(...)` / `setMusicIsShuffle(...)` directly when the trampoline T_papp 0x14 writes the file.
- B4's `PappStateBroadcaster` (already present, will move to `com.koensayr.y1.papp` namespace) listens for `OnSharedPreferenceChangeListener` callbacks on those two keys and updates `TrackInfoState` directly (instead of broadcasting to Y1MediaBridge).
- `TrackInfoState.init()` reads both values synchronously at `Y1Application.onCreate` time — closes the cold-boot OFF/OFF default gap.

## 6. Manifest deltas needed

Stock manifest (extracted via `aapt dump xmltree`):
- Package: `com.innioasis.y1`, versionCode 302, versionName 3.0.2
- `android:sharedUserId="android.uid.system"` — runs as system uid
- Application: `Y1Application`, no `android:persistent`, no `android:process`
- Single-process. No `android:process` on any service.
- 5 services declared: `PlayerService`, `StateBarService`, `FmRadioService`, `MultiInstanceInvalidationService`, `MessengerUtils$ServerService`. None export `com.android.music.MediaPlaybackService`.
- 5 receivers: `PlayControllerReceiver` (MEDIA_BUTTON priority MAX_INT, exported), `BootCompletedReceiver` (BOOT_COMPLETED + ACTION_SHUTDOWN, exported), `AutoShutdownReceiver`, `UnmountSdcardReceiver`, `SDReceiver`.

**Phase 1 patches** (additive):
- `<application android:persistent="true">` so the music-app process isn't killed under memory pressure (matches Y1MediaBridge's current behavior).

**Phase 3 patches** (additive):
- New exported `<service android:name="com.koensayr.y1.avrcp.AvrcpBridgeService">` with two intent filters:
  - `<action android:name="com.android.music.MediaPlaybackService"/>`
  - `<action android:name="com.android.music.IMediaPlaybackService"/>`
- `<intent-filter android:priority="100">` to win resolution over Y1MediaBridge during transition.

## 7. AIDL interface surface (extracted from `MtkBt.dex`)

Phase 3 needs `AvrcpBridgeService` to implement two interfaces. AIDL definitions are not in source — extracted from `MtkBt.dex` (de-odex'd) via `androguard`. **Transaction codes are byte-exact ground truth.**

### `Lcom/mediatek/bluetooth/avrcp/IBTAvrcpMusic;` — 38 methods

| TRANSACTION | code | TRANSACTION | code |
|---|---|---|---|
| `registerCallback` | 1 | `setShuffleMode` | 16 |
| `unregisterCallback` | 2 | `getShuffleMode` | 17 |
| `regNotificationEvent` | 3 | `setRepeatMode` | 18 |
| `setPlayerApplicationSettingValue` | 4 | `getRepeatMode` | 19 |
| `getCapabilities` | 5 | `setScanMode` | 20 |
| `play` | 6 | `getScanMode` | 21 |
| `stop` | 7 | `informDisplayableCharacterSet` | 22 |
| `pause` | 8 | `informBatteryStatusOfCT` | 23 |
| `resume` | 9 | `getPlayStatus` | 24 |
| `prev` | 10 | `position` | 25 |
| `next` | 11 | `duration` | 26 |
| `prevGroup` | 12 | `getAudioId` | 27 |
| `nextGroup` | 13 | `getTrackName` | 28 |
| `setEqualizeMode` | 14 | `getAlbumName` | 29 |
| `getEqualizeMode` | 15 | `getAlbumId` | 30 |
| `getArtistName` | 31 | `getQueuePosition` | 36 |
| `enqueue` | 32 | `setQueuePosition` | 37 |
| `getNowPlaying` | 33 | | |
| `getNowPlayingItemName` | 34 | | |
| `open` | 35 | | |

### `Lcom/mediatek/bluetooth/avrcp/IBTAvrcpMusicCallback;` — 8 methods (we **call into**, don't implement)

| TRANSACTION | code |
|---|---|
| `notifyPlaybackStatus` | 1 |
| `notifyTrackChanged` | 2 |
| `notifyTrackReachStart` | 3 |
| `notifyTrackReachEnd` | 4 |
| `notifyPlaybackPosChanged` | 5 |
| `notifyAppSettingChanged` | 6 |
| `notifyNowPlayingContentChanged` | 7 |
| `notifyVolumehanged` | 8 |

### `Lcom/android/music/IMediaPlaybackService;` — 32 methods

| TRANSACTION | code | TRANSACTION | code |
|---|---|---|---|
| `openFile` | 1 | `getQueue` | 20 |
| `open` | 2 | `moveQueueItem` | 21 |
| `getQueuePosition` | 3 | `setQueuePosition` | 22 |
| `isPlaying` | 4 | `getPath` | 23 |
| `stop` | 5 | `getAudioId` | 24 |
| `pause` | 6 | `setShuffleMode` | 25 |
| `play` | 7 | `getShuffleMode` | 26 |
| `prev` | 8 | `removeTracks` | 27 |
| `next` | 9 | `removeTrack` | 28 |
| `duration` | 10 | `setRepeatMode` | 29 |
| `position` | 11 | `getRepeatMode` | 30 |
| `seek` | 12 | `getMediaMountedCount` | 31 |
| `getTrackName` | 13 | `getAudioSessionId` | 32 |
| `getAlbumName` | 14 | | |
| `getAlbumId` | 15 | | |
| `getArtistName` | 16 | | |
| `getArtistId` | 17 | | |
| `getMIMEType` | 18 | | |
| `enqueue` | 19 | | |

### Implementation strategy

Both Stub classes' `onTransact` were not directly disassemblable due to a quickened-opcode error on an unrelated method elsewhere in `MtkBt.dex`. Two options for Phase 3:

1. **Re-derive AIDL stubs from this table.** Write `IBTAvrcpMusic.aidl` + `IBTAvrcpMusicCallback.aidl` + `IMediaPlaybackService.aidl` source files matching the method signatures (extracted from the interface DEX classes via androguard — descriptors are visible) and let `aidl` generate the Stub classes at compile time. Pin the `Stub.TRANSACTION_*` constants to the values above via a post-process step.
2. **Write `onTransact` by hand in smali.** A single switch over the inbound `code` parameter dispatching to in-process handlers that read from `TrackInfoState`. ~76 cases total across both Stubs; the music app's stub will only need to implement the methods MtkBt actually calls (Y1MediaBridge today serves a much smaller subset — codes 1, 3, 13, 16, 24, 27, 28, 29, 30, 31 per the reverse-engineering note in `src/Y1MediaBridge/README.md`). Phase 3 enumerates "actually-called" by reading MtkBt.dex's `BTAvrcpMusicAdapter` smali.

Recommendation: option 2. Cuts the AIDL toolchain dependency, keeps the smali surgical, and the actively-called codes are a small subset.

## 8. Net Phase 1 hook list

Concrete inject sites for Phase 1 (no Phase 2/3 inserts):

| File | Line | Insert |
|---|---|---|
| `smali/com/innioasis/y1/Y1Application.smali` | after line 412 (`MultiDex.install`) | `invoke-static {v4}, Lcom/koensayr/y1/trackinfo/TrackInfoWriter;->prepareFiles(Landroid/content/Context;)V` + `Lcom/koensayr/y1/trackinfo/TrackInfoState;->init(Landroid/content/Context;)V` + `Lcom/koensayr/y1/papp/PappSetFileObserver;->start(Landroid/content/Context;)V` + `Lcom/koensayr/y1/battery/BatteryReceiver;->register(Landroid/content/Context;)V` |
| `smali_classes2/com/innioasis/y1/utils/Static.smali` | top of `setPlayValue(II)V` line 334 | `invoke-static {p1, p2}, Lcom/koensayr/y1/playback/PlaybackStateBridge;->onPlayValue(II)V` |
| `smali/com/innioasis/y1/service/PlayerService.smali` | top of `initPlayer$lambda-10` (940), `lambda-11` (1042), `lambda-12` (1062), `initPlayer2$lambda-13` (1146), `lambda-14` (1341), `lambda-15` (1365) | one `invoke-static` per lambda to the matching `PlaybackStateBridge` callback |
| `AndroidManifest.xml` | `<application>` element | add `android:persistent="true"` |
| existing B3/B4 smali under `com/koensayr/` | (move) | rename to `com.koensayr.y1.papp.PappSetReceiver` / `PappStateBroadcaster`. B4's broadcaster path stops sending `ACTION_PAPP_STATE_DID_CHANGE` Intent and instead calls `TrackInfoState.setPapp(repeat, shuffle)` directly. |

New smali to drop into `smali_classes2/com/koensayr/y1/`:
- `trackinfo/TrackInfoWriter.smali` + `trackinfo/TrackInfoState.smali`
- `playback/PlaybackStateBridge.smali`
- `papp/PappSetReceiver.smali` (moved from existing B3) + `papp/PappStateBroadcaster.smali` (moved from existing B4) + `papp/PappSetFileObserver.smali` (new)
- `battery/BatteryReceiver.smali` (new)

## 9. Open questions for Phase 1

1. **Does `IjkMediaPlayer.OnPreparedListener` fire on the same thread as `MediaPlayer.OnPreparedListener`?** Y1MediaBridge currently uses an off-thread Executor for file writes; we should keep that pattern in `TrackInfoWriter` regardless. To verify by JDWP attach during Phase 1 verification.
2. **Does `Static.setPlayValue` always run on the main thread?** It calls `MutableLiveData.setValue` (which requires main thread). Implies our hook can also run main-thread but should defer the file write to an Executor.
3. **`PlayerService$Playing` enum ordinal mapping** — need to confirm None/Music/Audiobook/FM/etc. ordinals match the AVRCP `PlayStatus` enum mapping Y1MediaBridge currently uses. Quick decode of the `Playing` smali in Phase 1 kickoff.

These are blocking for `PlaybackStateBridge` implementation but not for the recon itself.
