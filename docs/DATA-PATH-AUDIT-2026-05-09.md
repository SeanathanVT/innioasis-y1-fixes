# Data-path audit (2026-05-09)

End-to-end fact-check of every Bluetooth / AVRCP / Y1MediaBridge data path the project currently claims to know. Triggered by the metadata regression that followed the 2026-05-08 MediaButton-receiver change — the regression revealed that ARCHITECTURE.md describes the AVRCP wire path correctly but is silent on the cross-component lifecycle that actually keeps the bridge wired up. This document captures what's verified, what's a gap in the existing docs, and what is still unknown.

Scope of this pass: read-only. No code touched. Inputs read:

- `/work/koensayr/docs/ARCHITECTURE.md`, `PATCHES.md`, `BT-COMPLIANCE.md`, `INVESTIGATION.md`
- `/work/v3.0.2/MtkBt.dex` — class metadata + raw bytecode at sPlayServiceInterface read / write sites
- `/work/v3.0.2/system.img.extracted/lib/libextavrcp_jni.so` — symbol table cross-reference (already documented in ARCHITECTURE)
- `/work/koensayr/src/Y1MediaBridge/app/src/main/AndroidManifest.xml`, `MediaBridgeService.java`, `PlaySongReceiver.java`
- `/work/koensayr/staging/y1-apk/unpacked/AndroidManifest.xml` — music-app manifest
- `/work/koensayr/src/patches/patch_mtkbt_odex.py` — F1 / F2 patch documentation

---

## 1. Components and their roles (reality check)

| Component | Process | Role — verified |
|---|---|---|
| `mtkbt` | `/system/bin/mtkbt` (native daemon) | Receives AVCTP frames from the Bluetooth chip, parses op_code, emits IPC msg=519 over `bt.ext.adp.avrcp` abstract socket. P1 patch forces VENDOR_DEPENDENT through the same emit path. |
| `libextavrcp_jni.so` | Loaded into the Bluetooth Java process (`com.android.bluetooth`) | Receives msg=519 in `_Z17saveRegEventSeqIdhh`, dispatches via the trampoline chain (R1 + T1 + T2 stub + extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T8 + T9), reads `y1-track-info` / `y1-trampoline-state` from disk, calls `btmtk_avrcp_send_*_rsp` PLT entries. U1 NOPs the kernel auto-repeat ioctl. |
| `MtkBt.odex` | Loaded into the Bluetooth Java process | Hosts `BluetoothAvrcpService` (hosts the JNI natives), `BTAvrcpMusicAdapter` (binds to Y1MediaBridge's `MediaPlaybackService`), `BTAvrcpProfile` (version flag). F1 unblocks 1.3+ Java dispatch via `getPreferVersion()`. F2 resets `sPlayServiceInterface` on disable. Two cardinality NOPs in `BTAvrcpMusicAdapter.handleKeyMessage` make `notificationTrackChangedNative` / `notificationPlayStatusChangedNative` fire on every Y1MediaBridge broadcast. |
| `Y1MediaBridge.apk` | `com.y1.mediabridge` | Hosts `MediaBridgeService` — serves `IBTAvrcpMusic` + `IMediaPlaybackService` Binders to MtkBt, observes Y1 player state via logcat scraping, writes `y1-track-info` / `y1-trampoline-state` files for the trampoline chain to read, fires `playstatechanged` / `metachanged` broadcasts to wake T5 / T9. |
| `com.innioasis.y1.apk` | `com.innioasis.y1` (foreground music app) | Hosts `PlayerService` (the actual MediaPlayer / IjkMediaPlayer), `PlayControllerReceiver` (manifest-declared `ACTION_MEDIA_BUTTON` receiver at priority `MAX_VALUE`), `BaseActivity` (Patch H). Patch E splits PASSTHROUGH PLAY / PAUSE / STOP routing. Patches A / B / C are unrelated UX (Artist→Album navigation). |

**Verified gap in ARCHITECTURE.md:** the doc describes the wire-level trampoline chain in detail but never explains how MtkBt finds Y1MediaBridge or how the bridge stays wired up across BT toggle cycles. This is the hole the 2026-05-08 attempted fix fell into.

---

## 2. Inbound AVRCP command path (CT → TG response)

Verified end-to-end against the actual binaries:

```
1. Peer CT sends AVCTP COMMAND on the wire (e.g. PDU 0x20 GetElementAttributes).

2. mtkbt receives via the Bluetooth chip, parses the AVCTP frame, emits IPC
   msg=519 (CMD_FRAME_IND) over abstract socket bt.ext.adp.avrcp.
   - P1 patch at mtkbt 0x144e8 forces VENDOR_DEPENDENT through this emit
     path (was silent-drop pre-patch).

3. libextavrcp_jni.so::saveRegEventSeqId (file 0x5f0c) receives the msg=519,
   reads SIZE at sp+374 and PDU at sp+382.

4. R1 patch at jni 0x6538 redirects the size!=3 / size!=8 fall-through to T1.

5. Trampoline chain dispatches by PDU:
     0x10 → T1                  → get_capabilities_rsp via PLT 0x35dc (msg=522)
     0x17 → T_charset           → inform_charsetset_rsp via PLT 0x3588 (msg=536)
     0x18 → T_battery           → battery_status_rsp via PLT 0x357c (msg=538)
     0x20 → T4                  → 7×get_element_attributes_rsp via PLT 0x3570
                                  (msg=540, single packed frame)
     0x30 → T6                  → get_playstatus_rsp via PLT 0x3564 (msg=542)
     0x31 + event 0x02 → ext_T2 → reg_notievent_track_changed_rsp via PLT 0x3384
                                  (msg=544, INTERIM, sentinel_ffx8 track_id)
     0x31 + event ≠ 0x02 → T8   → reg_notievent_*_rsp per event_id (msg=544)
     0x40 / 0x41 → T_continuation → AV/C NOT_IMPLEMENTED via UNKNOW_INDICATION
                                    (msg=520)
     anything else → fall through to original 0x65bc unknow_indication path

6. Each trampoline reads y1-track-info / y1-trampoline-state from
   /data/data/com.y1.mediabridge/files/ via direct open/read syscalls
   (paths embedded as ADR-resolvable string literals in the trampoline blob).

7. Response builder packs the IPC frame (msg_id varies per response type),
   calls AVRCP_SendMessage, mtkbt receives the IPC, forwards to wire.

8. Trampoline lands on b.w 0x712a → epilogue at 0x7154.
```

**Independence claim, verified:** this path does NOT depend on Y1MediaBridge's Binder being bound, nor on `AudioManager` state, nor on any state in `MtkBt.odex`. As long as Y1MediaBridge writes `y1-track-info` correctly, the trampolines emit correct responses regardless of what's happening in the Java land.

**Implication for the v22 regression:** the inbound-response path was probably intact in v22. The metadata regression is therefore most likely upstream — Y1MediaBridge wasn't writing the file correctly, or wasn't writing it at all. See §6.

---

## 3. Outbound proactive notification path (TG → CT CHANGED on edge)

Two trampoline entry points hooked into JNI native methods:

| Trampoline | Native hooked | Trigger |
|---|---|---|
| T5 | `notificationTrackChangedNative` (jni 0x3bc0, first instruction → `b.w T5`) | Fires on every Y1MediaBridge `metachanged` broadcast. Cardinality NOP in `MtkBt.odex` at file 0x3c530 (`sswitch_1a3`) is what makes it fire. T5 emits TRACK_REACHED_END (gated on `y1-track-info[793]` natural-end flag) + TRACK_CHANGED + TRACK_REACHED_START in spec order. |
| T9 | `notificationPlayStatusChangedNative` (jni 0x3c88, first instruction → `b.w T9`) | Fires on every Y1MediaBridge `playstatechanged` broadcast. Cardinality NOP in `MtkBt.odex` at file 0x3c4fe (`sswitch_18a`, event 0x01 case). T9 emits PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED on edge + PLAYBACK_POS_CHANGED at 1 s cadence while playing. |

**Crucial dependency chain for outbound notifications:**

```
1. Y1MediaBridge.LogcatMonitor (background thread inside MediaBridgeService)
   scrapes the music app's debug log for Y1 player state-code lines
   ('1' = PLAYING, '3' = PAUSED, '5' = STOPPED) and track-change lines.

2. On detection, MediaBridgeService.onStateDetected() / onTrackDetected() updates
   in-memory state, calls writeTrackInfoFile() to persist to disk, and fires
   the appropriate broadcast (metachanged / playstatechanged) via
   sendMusicBroadcast().

3. Android delivers the broadcast to MtkBt's BluetoothAvrcpReceiver
   (a static <receiver> declared in MtkBt.apk's manifest with intent-filter
   for the music broadcasts; verified via dex strings —
   `com.android.music.metachanged`, `com.android.music.playstatechanged`).

4. BluetoothAvrcpReceiver.onReceive forwards to BTAvrcpMusicAdapter via the
   adapter's Handler. Cardinality NOPs ensure the relevant native callback
   path isn't gated on (intentionally now-disabled) bitset checks.

5. Java JNI method (notificationTrackChangedNative / PlayStatusChangedNative)
   is invoked. First instruction is rewritten to b.w T5 / T9.

6. T5 / T9 read the freshly-written y1-track-info (and y1-trampoline-state for
   edge detection), emit msg=544 reg_notievent_*_rsp via PLT.
```

**Two key dependencies to flag** for any Y1MediaBridge change:

(a) **`writeTrackInfoFile()` must run before the broadcast fires.** Otherwise T5 / T9 read stale data. `MediaBridgeService.onStateDetected` (line 1119 area) explicitly orders these — file write first, then broadcast. Don't reorder.

(b) **The `BluetoothAvrcpReceiver` is registered manifest-side in MtkBt.apk**, so it works regardless of MtkBt service lifecycle. Y1MediaBridge's broadcast is consumed there; we don't need to bind anything for outbound CHANGED to work — only the `y1-track-info` writes need to be timely.

---

## 4. The MtkBt → Y1MediaBridge binding lifecycle (the missing piece in ARCHITECTURE.md)

Verified from MtkBt.dex string pool + class-metadata cross-reference. **This section did not exist in the ARCHITECTURE doc; it should be added.**

### 4.1 What MtkBt binds to

`BTAvrcpMusicAdapter.checkAndBindPlayService(boolean)` (method idx 1613, dex code at 0x3df00 area) calls `Context.bindService(Intent, ServiceConnection, BIND_AUTO_CREATE)`.

The Intent's action is the literal string `"com.android.music.MediaPlaybackService"` (verified at dex string-pool offset 0x075d65). No `setPackage()` qualifier, no `setComponent()`. PackageManager resolves via Android's standard intent matching — finds the only `<service>` on the device with an `<intent-filter>` for that action.

**Y1MediaBridge declares** that filter in its `AndroidManifest.xml`:

```xml
<service android:name=".MediaBridgeService" android:enabled="true" android:exported="true">
    <intent-filter>
        <action android:name="com.android.music.MediaPlaybackService" />
    </intent-filter>
</service>
```

**The stock Y1 music app** (`com.innioasis.y1`) does NOT export any service with that action — verified by dumping all `<service>` declarations in its manifest. So PackageManager unambiguously resolves to `com.y1.mediabridge/.MediaBridgeService`.

**No `AudioManager` involvement.** A targeted dex scan turned up zero references to `getMediaButtonReceiver`, `registerMediaButtonEventReceiver`, `dispatchMediaKeyEvent`, `getCurrentMediaPlaybackService`, or `getActiveMediaClient` in MtkBt.dex. The only AudioManager usage is volume control (`setStreamVolume` / `getStreamVolume` / `getStreamMaxVolume`) — handled in a separate code path. **The slot-stealing hypothesis from 2026-05-08 was wrong.** The 2026-05-09 metadata regression has a different root cause (see §6).

### 4.2 Lifecycle of the binding

Verified from dex method names + adapter inner-class structure:

| Event | What MtkBt does |
|---|---|
| BT enable / AVRCP profile activation | `BTAvrcpMusicAdapter.init()` runs → `checkAndBindPlayService(true)` → reads `sPlayServiceInterface` (field@1267, byte). If true (already bound), early-return. Otherwise sets `sPlayServiceInterface = true` and calls `bindService(Intent("com.android.music.MediaPlaybackService"), …)`. |
| `onServiceConnected` | `BTAvrcpMusicAdapter$4.onServiceConnected` (class idx 1583) fires when bind completes. Stores the IBinder in `mMusicService`, wraps as `IBTAvrcpMusic.Stub.asInterface(binder)` and as `IMediaPlaybackService.Stub.asInterface(binder)`. Invokes the Y1MediaBridge Binder via `transact(code=1, ...)` to register a callback (`registerCallback(IBTAvrcpMusicCallback)`). |
| RegisterNotification(EVENT_TRACK_CHANGED) from peer CT | `BTAvrcpMusicAdapter.regNotificationEvent(eventId, param)` (transact code=3 outbound to bridge, code=3 also the inbound path) — bridge sets internal flag, will fire callback when state changes. |
| Metadata pull (PDU 0x20 GetElementAttributes from peer) | MtkBt's Java path queries the bridge via `IMediaPlaybackService` transactions: `getTrackName()` (code 13 / 27), `getArtistName()` (code 16 / 29), `getAlbumName()` (code 14 / 28), `getAudioId()` (code 24), `duration()` (code 10), `position()` (code 11), `isPlaying()` (code 4). **However:** in the post-patch architecture this Java path is largely unused — the C-side trampolines read `y1-track-info` directly and respond to PDU 0x20 without ever transacting with the Java bridge. The Binder interface is still required for MtkBt's own bookkeeping (`mMusicService != null` check elsewhere). |
| BT disable | `BluetoothAvrcpService.disable()` runs → unbinds. **F2 patches `disable()` to also reset `sPlayServiceInterface = false`** so a subsequent re-enable doesn't see the stale flag and skip re-init. |

### 4.3 The `sPlayServiceInterface` flag — read / write inventory

Direct hex scan of `MtkBt.dex` for `field@1267` (sput-byte 0x6a + sget-boolean 0x63 + field index `f3 04` little-endian). Verified sites:

| Offset | Op | Method (best guess from surrounding text refs) |
|---|---|---|
| 0x3bbca | sput-byte v3 | early init / `<clinit>` or constructor |
| 0x3df1e | sput-byte v7 | `startToBindPlayService` — set TRUE optimistically before bindService |
| 0x3df84 | sput-byte v0 | `startToBindPlayService` — possibly reset on intermediate failure |
| 0x3dfe0 | sput-byte v7 | `startToBindPlayService` — in catch handler (right after `move-exception v1`) |
| 0x3cc48 | sget-boolean v0 | gate read (precedes the bind path) |
| 0x3d2c4 | sget-boolean v0 | gate read |
| 0x3d4d6 | sget-boolean v2 | gate read |
| 0x3df14 | sget-boolean v2 | `startToBindPlayService` — early-return guard |
| 0x3dfba | sget-boolean v4 | `startToBindPlayService` — late check (post-bind) |

**Pattern at 0x3df14 → 0x3df1c → 0x3df1e:**

```dalvik
sget-boolean v2, sPlayServiceInterface  ; @0x3df14
if-nez v2, +0x0003                       ; @0x3df18 — early return if already true
return-void                               ; @0x3df1c
sput-byte v7, sPlayServiceInterface     ; @0x3df1e — claim the slot
... bindService call follows ...
```

**This means the flag prevents double-init within a single BT-enable cycle.** F2's disable-time reset prevents stale-true across cycles. **None of these reads or writes consult `AudioManager` or any external state.**

### 4.4 Verdict on the 2026-05-09 regression mechanism

**The "MtkBt uses AudioManager.getMediaButtonReceiver to find Y1MediaBridge" hypothesis is disproven.** Static analysis confirms MtkBt's discovery is via `Intent("com.android.music.MediaPlaybackService")` + plain `bindService`, with `sPlayServiceInterface` as the only gate. No `AudioManager` consultation exists in the bind flow.

**So why did metadata break in v22?** Static analysis cannot answer this — we have no on-device logcat from the v22 build. Candidate hypotheses, ranked by evidence weight:

| Hypothesis | Evidence | Weight |
|---|---|---|
| (a) `setupRemoteControlClient` threw an unchecked exception with the cross-package `PendingIntent`, aborting `onCreate` before `startLogcatMonitor()` and `prepareTrackInfoDir()` could run. Result: y1-track-info never updates → trampolines read stale data → no metadata. | `onCreate()` body (line 704) runs the three init steps in sequence with no try / catch. PendingIntent / RCC API surfaces in 4.2.2 are not strictly documented to throw, but `RemoteControlClient.registerRemoteControlClient` does invoke security-sensitive `IAudioService` IPC that has thrown SecurityException historically with cross-package targets. **Plausible but unverified.** | MEDIUM |
| (b) `setupRemoteControlClient` completed cleanly but the RCC's PendingIntent silently failed at fire time (because cross-package broadcast permission), and AudioService logged a warning but everything else continued. Metadata-flow side: Y1MediaBridge keeps writing the file as normal. **Then this hypothesis predicts metadata SHOULD have worked in v22, contradicting the empirical observation.** | The PendingIntent failure model doesn't impact the y1-track-info write path. | LOW |
| (c) Something timing-related: removing `registerMediaButtonEventReceiver` removed an implicit dependency on `AudioManager` initialization (e.g. service connection setup) that, while not on the documented critical path, does affect Y1MediaBridge's startup ordering on some Android 4.2.2 paths. | Speculative. No code-level evidence. | LOW |
| (d) The user's v22 install had a flash artifact / partial flash / cached prior versionCode 21 binary, and the regression isn't actually attributable to the source change. | Not testable retrospectively. | LOW |

**To pin this down empirically:** capture a v23 build's startup logcat (`MediaBridgeService destroyed` / `MediaBridgeService.onCreate` / Y1Patch debug logs) on the Kia and compare against a hypothetical re-flash of v22 with `--debug` enabled. Specifically look for:

- Whether `Y1MediaBridge: RemoteControlClient registered (no audio focus request)` appears
- Whether `Y1MediaBridge: prepareTrackInfoDir:` appears
- Whether the LogcatMonitor's `Track change:` log appears on track changes
- Whether y1-track-info is being written (`adb shell md5sum /data/data/com.y1.mediabridge/files/y1-track-info` over time)

Without that v22 capture, the actual mechanism remains speculation. We should not attempt another "fix" until we have evidence.

---

## 5. The discrete-key chain-break (the bug that started the 2026-05-08 attempt)

Original symptom (still present after the revert):

```
AVRCP 0x44 PLAY arrives at peer CT
  ↓ (verified in btlog: rawkey:68 → MSG_ID_BT_AVRCP_CMD_FRAME_IND)
mtkbt routes to libextavrcp_jni.so → uinput injection at /dev/input/event4
  ↓ (verified in getevent.txt: KEY_PLAYCD DOWN/UP at /dev/input/event4)
Kernel input subsystem dispatches via AVRCP.kl
  ↓ (KEY_PLAYCD → KEYCODE_MEDIA_PLAY 0x7e)
Android InputManager → ViewRootImpl → BaseActivity.dispatchKeyEvent
  ↓ (verified: Y1Patch: BaseActivity.dispatchKeyEvent entry log fires)
Patch H returns false for 0x7e
  ↓ ???
[AudioService dispatch — chain breaks here; no log]
  ↓ ???
PlayControllerReceiver.onReceive — does NOT fire for AVRCP-driven 0x7e
PlayerService.play(true) — does NOT run
```

**What's known:** the kernel-side dispatch is correct (KEY_PLAYCD reaches event4). BaseActivity sees the keycode. Patch H's `return false` should let it propagate to the framework's fallback handler.

**What's unknown:**

- Whether `PhoneFallbackEventHandler.handleMediaKeyEvent` is reached after BaseActivity returns false.
- Whether `AudioManager.dispatchMediaKeyEvent` → `AudioService.dispatchMediaKeyEvent` → registered MediaButton receiver → PendingIntent fire is happening at all.
- If the dispatch reaches AudioService, whether AudioService's logic in 4.2.2 actually translates discrete `KEYCODE_MEDIA_PLAY` (126) into a delivered broadcast vs. drops it. (AudioService in 4.2.2 has stricter handling for some discrete media keycodes; see Android source `AudioService.dispatchMediaKeyEvent` and `MediaButtonReceiverHelper.isMediaKeyCode`.)

**Investigation tasks listed in the existing task table** (#52, #55) claimed to have completed framework-side instrumentation, but the postflash captures show only Y1Patch logs from `BaseActivity` / `PlayerService` — none from the framework. Either the framework-side instrumentation was reverted at some point or never landed in the active build. Need to verify by inspecting the deodexed `framework2.odex` whether it has the Y1Patch hooks for `AudioService.dispatchMediaKeyEvent`.

---

## 6. The Bolt PAUSE mystery (separate from the chain-break)

Verified facts from `dual-bolt-postflash` capture:

- Bolt sends `rawkey:68` (0x44 PLAY), `rawkey:75` (0x4B FORWARD / next), `rawkey:76` (0x4C BACKWARD / prev) only. **Never `rawkey:70` (0x46 PAUSE).**
- `getevent.txt`'s `/dev/input/event4` (the AVRCP uinput device) shows only `KEY_PLAYCD`, `KEY_NEXTSONG`, `KEY_PREVIOUSSONG`. Never `KEY_PAUSECD` or `KEY_PLAYPAUSE`.
- The Bolt UI's pause button transmits *something* — confirmed by the user via Pixel 4 ↔ Bolt working. But that "something" never surfaces in our `MSG_ID_BT_AVRCP_CMD_FRAME_IND` logs.

**Where the silent drop is:** somewhere between the Bluetooth chip's reception of the Bolt's frame and `mtkbt`'s emission of msg=519 to the JNI. mtkbt has internal early-drop logic for AVRCP frames it doesn't understand (the very thing P1 patches around for VENDOR_DEPENDENT). Possibilities:

| Hypothesis | What to check |
|---|---|
| (1) Bolt sends a Browse-channel command (PSM 0x1B). Y1 doesn't advertise Browse. mtkbt may receive but discard at L2CAP. | btlog L2CAP / AVCTP channel-id traces during a Bolt PAUSE press. |
| (2) Bolt sends a vendor-specific PASSTHROUGH op_id outside 0x44 / 0x45 / 0x46. mtkbt's PASSTHROUGH parser may reject it before reaching our P1-patched dispatch. | gdb-attach mtkbt at the AVCTP RX classifier (`0x6db7c` per project notes) and capture the raw inbound bytes during a Bolt PAUSE press. |
| (3) Bolt uses AVDTP-level SUSPEND (a property of the A2DP audio stream, not AVRCP). | btlog `[ME]` / `[AVDTP]` traces during a Bolt PAUSE press. |
| (4) Bolt uses an AVRCP 1.4+ NOTIFY-based pause that requires advertising support we don't expose. | SDP records served by V1 / V2 / S1 patches; the Pixel 4 likely advertises 1.4+ Browse. Compare what the Pixel advertises to what we do. |

**This is orthogonal to everything else.** The fix would likely be a new mtkbt patch (V*-class) and possibly a new SDP attribute, not anything in the trampoline chain or Y1MediaBridge.

---

## 7. What ARCHITECTURE.md is missing — recommended additions

Once the v22 regression mechanism is empirically pinned (per §4.4), update ARCHITECTURE.md to add:

1. **A "How MtkBt finds Y1MediaBridge" section** explaining the bind action, lifecycle, and `sPlayServiceInterface` gating. This is the section whose absence let me write a "fix" without seeing the cross-component coupling.
2. **An "AVRCP-driven control input flow" section** documenting the kernel-uinput → AudioService → PlayControllerReceiver chain (vs. the trampoline-driven outbound metadata flow). The two paths are independent; making the doc separate them clearly would prevent future "fixes" that touch one assuming the other won't move.
3. **A "Y1MediaBridge service lifecycle" section** documenting `MediaBridgeService.onCreate`'s init order (`setupRemoteControlClient` → `startLogcatMonitor` → `prepareTrackInfoDir`) and what fails-shut if any of these throws. Right now the order is implicit in source.
4. **A "Cross-component dependencies" section** that explicitly enumerates every state read / write that crosses process boundaries — `sPlayServiceInterface` (MtkBt internal), `mMediaButtonReceiver` (AudioManager), the on-disk files, the broadcasts. This was the gap the 2026-05-08 fix fell through.

---

## 8. What's still genuinely unknown

These remain open after this audit:

- (Q1) **What actually broke in v22.** Best hypothesis: `setupRemoteControlClient` threw, aborting `onCreate` before `startLogcatMonitor()`. Needs v22 logcat to confirm. **Don't attempt another fix until this is confirmed.**
- (Q2) **Where the discrete-key chain breaks between BaseActivity and PlayControllerReceiver.** The kernel-side delivery is correct; the framework-side dispatch isn't traced in the current logs. Needs framework2.odex Y1Patch instrumentation re-confirmed and a fresh capture.
- (Q3) **What primitive the Bolt sends for PAUSE.** Needs gdb-attach or btlog parse during a Bolt PAUSE press to capture the inbound frame bytes / channel.
- (Q4) **Why the TV exhibits a 15-second stuck `KEY_PAUSECD DOWN`** in the postflash capture. May be related to (Q2) under load, may be TV-side. Needs targeted re-capture after Q2 is fixed.

**No code change should be attempted on Q1 or Q2 without empirical evidence first.** The 2026-05-08 fix was a best-guess from static analysis; the regression cost was a flash cycle. The next attempt has to be backed by a confirmed mechanism.

---

## 9. Recommended sequence

1. **Update ARCHITECTURE.md** with the four sections above, summarizing what THIS audit verified. Cross-link to this audit doc for the per-claim verification record.
2. **Capture v22 logcat** (rebuild v22 source, flash, capture full logcat from boot to first track play, revert immediately). This is the only way to settle Q1.
3. **Confirm framework2.odex Y1Patch hooks are still in place** (or re-add them) so Q2 has on-device traceability. The hooks should log every `AudioService.dispatchMediaKeyEvent` entry + outcome for `KEYCODE_MEDIA_PLAY` / `_PAUSE` / `_STOP`.
4. **gdb-attach mtkbt during a Bolt PAUSE press** to settle Q3.
5. Only after Q1 + Q2 are pinned should we attempt a fix for the chain-break.

The user explicitly asked for no code changes on this pass. None made.
