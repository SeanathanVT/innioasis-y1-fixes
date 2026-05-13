# AVRCP Metadata Architecture

How the Innioasis Y1 delivers AVRCP 1.3 metadata (Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime) to peer Controllers, given that the OEM Bluetooth stack is fundamentally an AVRCP 1.0 implementation that auto-rejects 1.3+ commands. We advertise 1.3 over AVCTP 1.2 (see `patch_mtkbt.py` V1/V2, with ESR07 §2.1 / Erratum 4969 SDP-record clarifications applied) and implement the 1.3 metadata feature set: `GetCapabilities` 0x10, `InformDisplayableCharacterSet` 0x17, `InformBatteryStatusOfCT` 0x18, `GetElementAttributes` 0x20 (all 7 §5.3.4 attributes packed in a single response), `GetPlayStatus` 0x30 (with `clock_gettime(CLOCK_BOOTTIME)` live-position extrapolation), `RegisterNotification` 0x31 with INTERIM coverage of events 0x01..0x08 and proactive CHANGED-on-edge for every advertised event except 0x07 SYSTEM_STATUS (which is intentionally INTERIM-only — see `BT-COMPLIANCE.md` §2 footnote), and explicit AV/C reject for Continuation 0x40/0x41. F1's MtkBt-internal-version flip is a Java-side dispatcher-unblock flag (BlueAngel internal value), not a wire-shape upgrade. See [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §0 for spec citation discipline and §2 for the ICS Table 7 coverage scorecard.

This document covers the **full proxy architecture**: the trampoline chain that intercepts inbound AVRCP commands in `libextavrcp_jni.so`, calls the existing C response-builder functions (which were never wired up by the OEM Java side), and delivers spec-compliant 1.3 responses on the wire.

For **per-patch byte details**: see [`PATCHES.md`](PATCHES.md).
For **investigation history** (how we got here): see [`INVESTIGATION.md`](INVESTIGATION.md).
For **AVRCP 1.3 spec-coverage state**: see [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md).

---

## TL;DR

Two independent data paths cross this stack:

**Inbound metadata (CT → TG response).** A peer CT sends a stock AVRCP 1.3+ AV/C COMMAND → mtkbt routes it through msg-519 (P1 patch) → `libextavrcp_jni.so::saveRegEventSeqId` is intercepted at file 0x6538 (R1 patch) → a chain of trampolines (T1 / T2 stub / extended_T2 / T4 / T5 / T_charset / T_battery / T_continuation / T6 / T8 / T9) inspects the inbound PDU byte (and event_id, for PDU 0x31), reads `y1-track-info` / `y1-trampoline-state` from disk, and calls the matching `btmtk_avrcp_send_*_rsp` PLT entry directly → mtkbt builds a real AVRCP 1.3 response frame and emits it on the wire → the CT displays the metadata.

**AVRCP-driven control input (CT → music app).** A peer CT sends an AVRCP PASSTHROUGH op_id (0x44 PLAY / 0x46 PAUSE / 0x45 STOP / 0x4B NEXT / 0x4C PREV) → mtkbt → `libextavrcp_jni.so` injects an EV_KEY into `/dev/input/event4` (uinput) → kernel input → `AVRCP.kl` → `KEYCODE_MEDIA_*` → BaseActivity (Patch H propagates discrete keys past the foreground activity) → AudioService → `ACTION_MEDIA_BUTTON` → `PlayControllerReceiver` → Patch E's discrete-key dispatch → `PlayerService.play(true)` / `pause(0x12, true)` / `stop()`.

The trampolines live in unused / repurposed JNI debug methods (`testparmnum`, `classInitNative`) and in the page-alignment padding past the original LOAD #1 segment end (extended via `FileSiz` / `MemSiz` program-header surgery). The two paths share no state — touching trampolines does not affect control-input dispatch, and touching MediaButton / RCC registration does not affect trampoline-driven metadata. The cross-component-state-dependencies table near the bottom of this doc enumerates every shared surface explicitly.

---

## Why a proxy

mtkbt is compiled internally as **AVRCP 1.0** (compile-time tag, runtime `register activeVersion:10`) regardless of what we advertise in SDP. Its inbound dispatcher in `fn 0x144bc` originally silent-dropped any `op_code != 0x7c` (i.e. anything that wasn't PASSTHROUGH). Java AVRCP TG (`BluetoothAvrcpService` / `BTAvrcpMusicAdapter` in MtkBt.apk) is essentially a stub — `getElementAttributesRspNative` is **declared** but **never called** from any Java code path in the de-odex'd dex.

But the C response-builder functions exist and are correct:

| PLT @  | Symbol                                                  | What it sends |
|--------|---------------------------------------------------------|---------------|
| 0x35dc | `btmtk_avrcp_send_get_capabilities_rsp`                 | msg=522 — GetCapabilities response |
| 0x3384 | `btmtk_avrcp_send_reg_notievent_track_changed_rsp`      | msg=544 — RegisterNotification(TRACK_CHANGED) INTERIM |
| 0x339c | `btmtk_avrcp_send_reg_notievent_playback_rsp`           | msg=544 — RegisterNotification(PLAYBACK_STATUS_CHANGED) INTERIM |
| 0x3570 | `btmtk_avrcp_send_get_element_attributes_rsp`           | msg=540 — GetElementAttributes response (multi-attribute capable) |
| 0x3624 | `btmtk_avrcp_send_pass_through_rsp`                     | msg=520 — PASSTHROUGH ack / NOT_IMPLEMENTED reject |

The trampolines call these directly. No new IPC, no Java surgery for the core handshake.

---

## Lower BT profile stack (A2DP / AVDTP / AVCTP / GAVDP)

AVRCP doesn't run in a vacuum. It rides on AVCTP, which rides on L2CAP. Audio rides on A2DP, which rides on AVDTP, which is signalled via GAVDP, which rides on L2CAP. All four lower profiles are implemented in a single MediaTek "BlueAngel" daemon (`/system/bin/mtkbt`). A handful of `.so` files surround it as adapters / shims / userspace plumbing. There is no separate `libavctp.so` / `libavdtp.so` / `libgavdp.so`; everything from L2CAP up through AVRCP TG lives inside `mtkbt`.

### Where each profile lives

Source-tree fingerprint (paths embedded in `mtkbt` strings — confirms BlueAngel internal codebase):

| Profile | BlueAngel source files (inside mtkbt) | External adapter / shim |
|---|---|---|
| L2CAP | `btcore/btstack/stack/l2cap/{l2cap,l2cap_if,l2cap_sm,l2cap_utl}.c` | — |
| HCI | `btcore/btstack/stack/hci/{hci,hci_evnt,hci_proc,hci_util,hci_meta,hci_amp}.c` | — |
| AVCTP | `btcore/btstack/stack/avctp/{avctp,avctpcon,avctpmsg}.c` | — |
| AVDTP | `btcore/btstack/stack/avdtp/{avdtp,avsigmgr}.c` | — |
| GAVDP | `btcore/btstack/stack/gavdp/gavdp.c` | — |
| A2DP | `btcore/btprofiles/a2dp/a2dp.c` | `libmtka2dp.so` (userspace stream socket), `libmtkbtextadpa2dp.so` (Java↔mtkbt shim), `libaudio.a2dp.default.so` (legacy AOSP `a2dp.default` HAL) |
| AVRCP TG | `btcore/btprofiles/avrcp/{avrcp,avrcpevent,avrcputil}.c` + `btadp_int/profiles/avrcp/bt_adp_avrcp.c` | `libextavrcp.so` (response builders), `libextavrcp_jni.so` (JNI bridge — our trampoline host) |

mtkbt internal version tag: `[AVRCP] AVRCP V10 compiled` — built against AVRCP 1.0. F1's BlueAngel-internal version flip unblocks 1.3+ command dispatch in the Java layer; the wire-shape upgrade is what our trampolines provide.

### AVCTP — transport for AVRCP

L2CAP PSM 0x17 (signaling channel). Browse PSM 0x1B exists in mtkbt code paths but is dead code — Browse-related strings (`[AVRCP][BWS] No av/c parse`, `[AVRCP][BWS] Receive browse-packet`) are from a later AVRCP version that we don't claim in our SDP record (V1 patch advertises 1.3, not 1.4+).

**MTU bookkeeping**:

```
[AVRCP] AVRCP_NUM_TX_PACKETS:4 AVRCP_MAX_PACKET_LEN:512
```

advertised at AVRCP service init. Per-packet ceiling enforced via the runtime check `(10 + u2MtuPayload) <= 512` — anything above the ceiling triggers one of `MTU violation 1..5` log lines and the packet is dropped. `4 × 512 = 2048` is the practical ceiling for fragmented AVRCP responses.

**AVCTP packet types** (from `cmdFrame->type` / `rawFrame->type` log fields, and the `pkt_type` 2-bit field at byte 0 of every AVCTP frame):

| pkt_type | Meaning |
|---|---|
| 0 | SINGLE — entire AV/C body in one AVCTP packet |
| 1 | START — first fragment of a multi-fragment AV/C body; byte 1 = num_packets |
| 2 | CONTINUE — middle fragment |
| 3 | END — final fragment |

Fragmentation is mandatory at the AVCTP layer when the AV/C body exceeds the negotiated AVCTP MTU. mtkbt's outbound path implements this transparently to AVRCP — the `AVRCP_SendMessage` IPC frame size (e.g., the `len=644 size=672` we observe in EXTADP_AVRCP logs for `GetElementAttributes` responses) is the IPC payload, which mtkbt then fragments into multiple AVCTP packets if the body > AVCTP MTU.

**AVCTP transaction model** (events visible at the JNI boundary as `AVCTP_EVENT:N`):

| EVENT | Direction | Meaning |
|---|---|---|
| 1 | inbound | `CONNECT_IND` — peer opened the AVCTP signaling channel |
| 2 | inbound | `DISCONNECT_IND` — peer closed |
| 4 | inbound | `DATA_IND` — peer sent an AV/C COMMAND |
| 7 | outbound | `DATA_CFM` — confirmation that our outbound write completed (verified empirically — paired 1:1 with EVENT:4 at the lib level for COMMAND/RESPONSE round-trips) |

### AVDTP + GAVDP — transport + setup for A2DP

AVDTP runs on L2CAP PSM 0x19 (separate channels for signaling and media stream). GAVDP is the role-coordinator profile that sits above AVDTP and below A2DP — it handles SEP discovery, capability negotiation, and stream lifecycle.

**Codec scope**: `[GAVDP][GavdpAvdtpEventCallback][AVDTP_EVENT_CAPABILITY]not AVDTP_CODEC_TYPE_SBC` — non-SBC stream endpoints are rejected by `try another SEP` fallback. Only SBC sinks are accepted.

**AVDTP signal codes** (sig_id field at byte 1 of every AVDTP signaling frame; only the codes we have confirmed in mtkbt code are listed):

| sig_id | Operation | Spec § (AVDTP 1.3) |
|---|---|---|
| 0x01 | DISCOVER | §8.6 |
| 0x02 | GET_CAPABILITIES | §8.7 |
| 0x03 | SET_CONFIGURATION | §8.9 |
| 0x04 | GET_CONFIGURATION | §8.10 |
| 0x05 | RECONFIGURE | §8.11 |
| 0x06 | OPEN | §8.12 |
| 0x07 | START | §8.13 |
| 0x08 | CLOSE | §8.14 |
| 0x09 | SUSPEND | §8.15 |
| 0x0a | ABORT | §8.16 |
| 0x0b | SECURITY_CONTROL | §8.17 |
| 0x0c | GET_ALL_CAPABILITIES | §8.8 (1.3 addition) |
| 0x0d | DELAYREPORT | §8.19 |

**State machine surface** (visible state names from log strings — most transitions are silent):

- `GAVDP_STATE_DEINITIALIZING` (init/teardown path)
- `AVDP_STATE_SIG_PASSIVE_DISCONNECTING` / `AVDP_STATE_SIG_PASSIVE_DISCONNECTED` (signaling channel teardown)
- `AVDTP_EVENT_CAPABILITY` / `AVDTP_EVENT_GET_CAP_CNF` / `AVDTP_EVENT_STREAM_CLOSED` (event names emitted by AVDTP into GAVDP via `GavdpAvdtpEventCallback`)

The full state graph isn't logged in production — confirming any transition beyond what's visible above requires disassembly of `avdtp.c` / `avsigmgr.c` / `gavdp.c` symbols.

### A2DP source state

A2DP is the only one of the four lower profiles with a userspace bridge: `libmtka2dp.so` opens `/dev/socket/bt.a2dp.stream` (an abstract Unix socket created by mtkbt) and shuttles SBC-encoded audio frames from the legacy AOSP `a2dp.default` HAL to mtkbt's AVDTP source.

**The HAL → BT stack chain**:

```
AudioFlinger (audioflinger process)
   ↓ writes audio frames
A2dpAudioStreamOut::write()  (libaudio.a2dp.default.so)
   ↓ writes to /dev/socket/bt.a2dp.stream
libmtka2dp.so (linked into the BT process)
   ↓ MSG_ID_BT_A2DP_STREAM_DATA_*
mtkbt internal A2DP source state machine
   ↓ packetizes as RTP-over-AVDTP MEDIA
peer A2DP sink
```

**A2DP source state machine** (function names from `libmtkbtextadpa2dp.so` exports — these are the IPC entry points mtkbt uses):

| State change | Function |
|---|---|
| OPEN_REQ / IND / CNF | `btmtk_a2dp_send_stream_open_req` / `_handle_stream_open_ind` / `_handle_stream_open_cnf` |
| START_REQ / IND / CNF | `btmtk_a2dp_send_stream_start_req` / `_handle_stream_start_ind` / `_handle_stream_start_cnf` |
| SUSPEND (`pause`) REQ / IND / CNF | `btmtk_a2dp_send_stream_pause_req` / `_handle_stream_suspend_ind` / `_handle_stream_suspend_cnf` |
| CLOSE_REQ / IND / CNF | `btmtk_a2dp_send_stream_close_req` / `_handle_stream_close_ind` / `_handle_stream_close_cnf` |
| ABORT_REQ / IND / CNF | `btmtk_a2dp_send_stream_abort_req` / `_handle_stream_abort_ind` / `_handle_stream_abort_cnf` |
| RECONFIGURE_REQ / RES | `btmtk_a2dp_send_stream_reconfig_req` / `_send_stream_reconfig_res` |
| Direct AVDTP SUSPEND | `btmtk_a2dp_pause_immediately` |

`btmtk_a2dp_send_stream_pause_req` emits AVDTP SUSPEND on the wire. `btmtk_a2dp_pause_immediately` is a synchronous wrapper. Both are reachable from mtkbt-internal IPC handlers — neither is called from `libaudio.a2dp.default.so` directly.

**`A2dpAudioStreamOut::standby_l` (legacy HAL)** at `libaudio.a2dp.default.so:0x8654`:

```
8654: push {r3,r4,r5,lr}
8658: mov  r4, r0
865c: ldrb r3, [r0, #8]              ; r3 = mStandby
...
86a0: ldrb r5, [r4, #48]
86a4: cmp  r5, #0
86a8: beq  8684                      ; AH1 patches this to `b 8684` — always skip a2dp_stop
86ac: ldr  r0, [r4, #40]
86b0: bl   a2dp_stop@plt              ; only HAL-side path that emits AVDTP SUSPEND
86b4: mov  r5, r0
86b8: b    8684                      ; release_wake_lock + mStandby=1 + return r5
```

`a2dp_stop` at vaddr `0x86b0` is the **only** HAL-side path that would emit AVDTP SUSPEND on the wire. AudioFlinger calls `standby_l` after a ~3 s silence-timeout when the music app stops writing samples. `patch_libaudio_a2dp.py` (AH1) flips the conditional `beq 8684` at `0x86a8` to an unconditional `b 8684`, making the call site at `0x86b0` unreachable. Standby still completes (release_wake_lock, mStandby = 1, return 0); the AVDTP stream is left alive across pauses. See [`PATCHES.md`](PATCHES.md) §`patch_libaudio_a2dp.py` for the byte-level reference.

### Cross-profile coupling gaps relevant to AVRCP 1.3

These are the spec-conformance deviations that affect AVRCP-1.3-class controllers in the wild. Each is filed for the [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §9 plan:

| # | Coupling gap | What spec says | What Y1 currently does |
|---|---|---|---|
| 1 | **AVRCP META CONTINUING_RESPONSE (PDUs 0x40 / 0x41)** | AVRCP 1.3 §5.5: when a TG response exceeds the CT-buffer / AVCTP-MTU budget, TG sends packet_type=START with the first chunk and waits for CT to send `RequestContinuingResponse` (0x40). TG then sends CONTINUE / END chunks until the response is exhausted. C.2 makes this Mandatory if `GetElementAttributes Response` is supported. | T_continuation explicit-rejects 0x40 / 0x41 with AV/C NOT_IMPLEMENTED. Spec-acceptable today (TG never fragments since each attribute is capped at 240 B); a strict CT with a small buffer would lose metadata mid-fragment. |
| 2 | **AVRCP playback state ↔ AVDTP source state** | AVDTP 1.3 §8.13 / §8.15: when AVRCP TG transitions to PAUSED, the A2DP source should keep the AVDTP stream paused (NOT torn down); SUSPEND is reserved for explicit policy changes. | `patch_libaudio_a2dp.py` (AH1) flips `beq 8684` to unconditional `b 8684` at `libaudio.a2dp.default.so:0x86a8`, making the call to `a2dp_stop@plt` inside `standby_l` unreachable. Silence-timeout standby leaves the AVDTP source stream alive; the next `write()` after PLAYING resumes pushes samples into the same session. |
| 3 | **Per-attribute size cap in `…send_get_element_attributes_rsp`** | AVRCP 1.3 §5.3.4 places no per-attribute byte cap; TG fragments via §5.5 if total response doesn't fit. | `libextavrcp.so:0x2188` enforces a 511-byte per-attribute hard cap and emits `[BT][AVRCP][ERR] too large attr_index:%d` then drops the attribute on overflow. The music app's `TrackInfoWriter.putUtf8Padded` caps each string attribute at 240 B (codepoint-safe) before it lands in `y1-track-info`, well below the OEM 511 limit, so the silent-drop branch never fires for content we ship. |
| 4 | **AVCTP transaction-label management** | AVCTP 1.2 §6: TG response transaction label must match the inbound COMMAND label (4-bit field at byte 0 high nibble). | mtkbt routes `transId` from `conn[17]` into every response builder, but the response builders (`…send_*_rsp`) are responsible for stamping it into byte 5 of the IPC frame. This appears correct for everything we've shipped — flagged here for completeness, no observed deviation. |

The complete cross-profile dependency table (state files, broadcasts, IBinder fields, etc.) lives in the "Cross-component state dependencies" section near the bottom of this doc. The four entries above are the spec-conformance deviations specifically; the table at the bottom catalogues runtime state crossings.

---

## How MtkBt discovers and binds to Y1Bridge

For metadata + state-event delivery to peer CTs, two things must be true at runtime:

1. **The trampoline chain in `libextavrcp_jni.so` can read `y1-track-info` / `y1-trampoline-state` from disk** under `/data/data/com.innioasis.y1/files/`. This depends only on the music app's `TrackInfoWriter` having written those files; it does not depend on any Binder being bound.
2. **MtkBt's `BTAvrcpMusicAdapter` has a live `IBTAvrcpMusic` Binder reference to `MediaBridgeService`.** This is required because `MtkBt.odex` gates its 1.3-class Java dispatch on `sPlayServiceInterface`, a static byte field that's set when the bind succeeds and reset by F2 on disable. With it false, the Java layer's AVRCP-event-callback paths short-circuit and the AVRCP wire defaults to the compile-time AVRCP 1.0 dispatch.

### Bind action and resolution

`BTAvrcpMusicAdapter.checkAndBindPlayService(boolean)` (DEX method idx 1613) calls `Context.bindService(Intent, ServiceConnection, BIND_AUTO_CREATE)`. The Intent's action is the literal string `"com.android.music.MediaPlaybackService"` (verified at MtkBt.dex string-pool offset `0x075d65`). No `setPackage` qualifier, no `setComponent`.

PackageManager resolves via Android's standard intent matching. Y1Bridge declares the only matching `<service>` on the device:

```xml
<service android:name=".MediaBridgeService" android:enabled="true" android:exported="true">
    <intent-filter>
        <action android:name="com.android.music.MediaPlaybackService" />
    </intent-filter>
</service>
```

The music app (`com.innioasis.y1`) does NOT export any service with this action — its manifest can't be modified safely. `com.innioasis.y1` declares `sharedUserId="android.uid.system"`, which constrains the package's signing key to the OEM platform key. Any change to AndroidManifest.xml bytes invalidates `META-INF/MANIFEST.MF`'s SHA1-Digest, causing PackageManager to reject the package at /system/app/ scan with "no certificates at entry AndroidManifest.xml; ignoring!". JarVerifier doesn't digest-check classes.dex / classes2.dex / resources at scan time, which is why our DEX-only smali edits work. So `bindService` unambiguously resolves to `com.koensayr.y1.bridge/.MediaBridgeService`, and Y1Bridge hosts the Binder — the music app's `TrackInfoWriter` is the canonical writer for AVRCP state.

**No `AudioManager` involvement in service discovery.** A targeted dex scan of MtkBt.dex turned up zero references to `getMediaButtonReceiver`, `registerMediaButtonEventReceiver`, `dispatchMediaKeyEvent`, `getCurrentMediaPlaybackService`, or `getActiveMediaClient`. MtkBt's AudioManager use is exclusively volume control (`setStreamVolume` / `getStreamVolume` / `getStreamMaxVolume`).

### `sPlayServiceInterface` — the bind gate

`field@1267`, static byte (declared as `private static boolean` but written via `sput-byte` opcode). 4 writes + 5 reads across the dex. The bind site at `BTAvrcpMusicAdapter.startToBindPlayService()` follows this pattern:

```dalvik
sget-boolean v2, sPlayServiceInterface  ; @ dex 0x3df14 — gate read
if-nez v2, +0x0003                       ; @ dex 0x3df18 — early return if already true
return-void                               ; @ dex 0x3df1c
sput-byte v7, sPlayServiceInterface     ; @ dex 0x3df1e — claim slot before bind
... bindService(Intent("com.android.music.MediaPlaybackService"), ...) ...
```

Within a single BT-enable cycle, the flag prevents double-init. **F2 patches `BluetoothAvrcpService.disable()` to reset the flag to false** so a subsequent re-enable doesn't see stale-true and skip re-init. Without F2, the second BT-enable would call `notifyProfileState(STATE_ENABLED)` immediately (because `sPlayServiceInterface` looks already-initialized), which Android's BT framework interprets as "service is up", which causes `stopSelf` and tear-down before any peer CT can connect.

### Bind lifecycle

| Event | What MtkBt does |
|---|---|
| BT enable / AVRCP profile activation | `BTAvrcpMusicAdapter.init()` → `checkAndBindPlayService(true)` → `startToBindPlayService()` reads `sPlayServiceInterface`. If false, sets it true and calls `bindService`. |
| `onServiceConnected` callback | `BTAvrcpMusicAdapter$4.onServiceConnected` (DEX class idx 1583) fires when bind completes. Stores the IBinder in `mMusicService`. Wraps as both `IBTAvrcpMusic.Stub.asInterface(binder)` and `IMediaPlaybackService.Stub.asInterface(binder)` — Y1Bridge's `AvrcpBinder` (in `src/Y1Bridge/`) returns the same IBinder for both interfaces and dispatches `onTransact` by transact code (descriptor skipped). Invokes `IBTAvrcpMusic.getCapabilities()` (transact 5) to enumerate event support. |
| Peer CT subscribes / queries metadata | MtkBt's Java path is observed to query `getCapabilities` once at bind, then never transact again — verified empirically against a permissive CT in [`INVESTIGATION.md`](INVESTIGATION.md) Trace #21. The C-side trampolines deliver every CT-visible AVRCP PDU on the wire directly. `AvrcpBinder` ack-only's every other transact code, which is sufficient. The Binder presence is what flips `sPlayServiceInterface=true` and unblocks the cardinality-NOP-driven Java native paths that wake T5 / T9 in response to the music app's `metachanged` / `playstatechanged` broadcasts. |
| BT disable | `BluetoothAvrcpService.disable()` runs → unbinds. F2 patches this method to also reset `sPlayServiceInterface = false`. |

---

## Music app component lifecycle

The music app's `Y1Application.onCreate` registers four in-process components that together produce every byte of `y1-track-info`, `y1-trampoline-state`, and `y1-papp-set` under `/data/data/com.innioasis.y1/files/`:

| Component | Purpose |
|---|---|
| `com.koensayr.y1.trackinfo.TrackInfoWriter` | Singleton state holder + atomic file writer. Owns the 1112-byte `y1-track-info` schema and the 16-byte `y1-trampoline-state` initial create. `prepareFiles()` chmods both files world-rw / world-readable so the BT process (different uid) can `open()` them. |
| `com.koensayr.y1.playback.PlaybackStateBridge` | Stateless dispatcher hooked into `Static.setPlayValue` and the `PlayerService` listener lambdas (`onPrepared`, `onCompletion`, `onError`). Maps player state to AVRCP play-status enum and calls into TrackInfoWriter on every edge. |
| `com.koensayr.y1.battery.BatteryReceiver` | `Intent.ACTION_BATTERY_CHANGED` receiver. Bucket-maps level + plugged-state to the AVRCP §5.4.2 Tbl 5.35 enum (NORMAL / WARNING / CRITICAL / EXTERNAL / FULL_CHARGE) and writes byte 794. Fires `com.android.music.playstatechanged` on bucket transition so T9 emits BATT_STATUS_CHANGED CHANGED. |
| `com.koensayr.y1.papp.PappSetFileObserver` | `FileObserver(y1-papp-set, CLOSE_WRITE)`. Reads the 2-byte payload (attr_id, value), maps AVRCP enum → Y1 enum, calls `SharedPreferencesUtils.setMusicRepeatMode / setMusicIsShuffle`. Lets a CT's PApp Set round-trip into the music app's settings. |
| `com.koensayr.y1.papp.PappStateBroadcaster` | `OnSharedPreferenceChangeListener`. On every `musicRepeatMode` / `musicIsShuffle` SharedPreferences change, calls `TrackInfoWriter.setPapp` to update y1-track-info bytes 795..796 and fires `com.android.music.playstatechanged` so T9 emits PApp CHANGED. |

In `smali_classes2` (secondary DEX):

| Component | Purpose |
|---|---|
| `com.koensayr.y1.avrcp.AvrcpBridgeService` | Service shell. Not declared in the music app manifest, so unreferenced at runtime. |
| `com.koensayr.y1.avrcp.AvrcpBinder` | `Binder` implementing the `IBTAvrcpMusic` + `IMediaPlaybackService` transact protocols in smali. Not instantiated. Would only become live if MtkBt's `bindService` ever resolved into the music-app process directly (requires either an MtkBt.odex component-bind patch or a forwarder APK — see [`INVESTIGATION.md`](INVESTIGATION.md) Trace #23). |

**State-write ordering is load-bearing**: PlaybackStateBridge calls `TrackInfoWriter.flush()` (which writes `y1-track-info` atomically via tmp+rename) BEFORE the music app's `metachanged` / `playstatechanged` broadcast fires. The broadcast wakes T5 / T9 via the cardinality-NOP-patched Java path; if the file write hasn't happened yet, T5 / T9 read stale data. Don't reorder.

### Y1Bridge (the slim Binder host)

Y1Bridge.apk stays installed for one reason: MtkBt's `bindService(Intent("com.android.music.MediaPlaybackService"))` needs a `<service>` declaration with that intent-filter, and the music app can't declare it (sharedUserId / platform-key constraint described above). Y1Bridge is its own package (`com.koensayr.y1.bridge`), self-signed with the debug keystore, so its manifest is freely editable.

The bridge presents the Binder and serves synchronous state queries:

- `MediaBridgeService.onCreate` is empty.
- `MediaBridgeService.onBind` returns an `AvrcpBinder` whose `onTransact` implements the `IBTAvrcpMusic` codes `BTAvrcpMusicAdapter` calls. Synchronous state queries (`getPlayStatus` / `position` / `duration` / `getAudioId` / `getTrackName` / `getAlbumName` / `getArtistName` / `getRepeatMode` / `getShuffleMode`) are answered live by reading `/data/data/com.innioasis.y1/files/y1-track-info` (the same 1104-byte file `TrackInfoWriter` maintains; world-readable so the bridge's `uid` can `open()` it). Registration / setter / passthrough codes ack with the success replies that keep `BTAvrcpMusicAdapter.mRegBit` armed and the Java mirror in sync with on-disk state.
- `BootReceiver` only handles `BOOT_COMPLETED` → `startService(MediaBridgeService)` so the Service is alive when MtkBt first binds.

All AVRCP observation, file writes, broadcast emission, and proactive-notification wake live in the music app — the bridge has no `LogcatMonitor`, no `BatteryReceiver`, no `RemoteControlClient` setup, no file writer, no callback dispatcher. Source: ~300 lines across three files in `src/Y1Bridge/`.

---

## The data path, end-to-end

```
                           Peer CT (AVRCP 1.3+ controller)
                                       │
                                       ▼
                            ┌──────────────────────┐
                            │ AVCTP COMMAND on the │
                            │ Bluetooth wire       │
                            └──────────┬───────────┘
                                       │
                                       ▼
                          ┌──────────────────────────┐
                          │ mtkbt (native daemon)    │
                          │                          │
                          │  fn 0x144bc op_code      │
                          │  dispatcher              │
                          │                          │
                          │  ─ P1 patch at 0x144e8 ─ │
                          │  cmp r3, #0x30           │
                          │      ↓                   │
                          │  b.n 0x14528 (ALWAYS,    │
                          │     was conditional)     │
                          │      ↓                   │
                          │  bl 0x10404              │
                          │      ↓                   │
                          │  IPC msg=519 emit        │
                          └──────────┬───────────────┘
                                     │ (over abstract socket
                                     │  bt.ext.adp.avrcp)
                                     ▼
              ┌────────────────────────────────────────────┐
              │ libextavrcp_jni.so::saveRegEventSeqId      │
              │   (loaded into the Bluetooth Java process) │
              │                                            │
              │  reads inbound CMD_FRAME_IND, then         │
              │  dispatches on AV/C body SIZE (sp+374):    │
              │                                            │
              │   size==3  → PASSTHROUGH path (intact)     │
              │   size==8  → BT-SIG vendor (intact)        │
              │   else     → bne 0x65bc "unknow"           │
              │                                            │
              │  ─ R1 patch at file 0x6538 ─               │
              │  bne.n 0x65bc; movs r5,#9                  │
              │     ↓                                      │
              │  bl.w 0x7308 (T1 entry)                    │
              └────────────────────┬───────────────────────┘
                                   │
                                   ▼
                       ┌───────────────────────┐
                       │ T1 (file 0x7308)      │  Trampoline #1
                       │ overwrites unused     │  GetCapabilities
                       │   testparmnum         │
                       │                       │
                       │  read PDU at sp+382   │
                       │  if PDU == 0x10:      │
                       │     blx 0x35dc        │
                       │     (get_caps_rsp)    │
                       │     b.w 0x712a (epi)  │
                       │  else: b.w 0x72d4     │  → T2
                       └───────────┬───────────┘
                                   │
                                   ▼
                       ┌────────────────────────┐
                       │ T2 (file 0x72d0)       │  Trampoline #2
                       │ overwrites             │  RegisterNotif
                       │   classInitNative      │  (TRACK_CHANGED)
                       │ (4-byte stub at start  │
                       │  preserves return-0)   │
                       │                        │
                       │  PDU == 0x31 AND       │
                       │  event_id (sp+386)==2: │
                       │     blx 0x3384         │
                       │     (track_changed_rsp │
                       │      with INTERIM,     │
                       │      track_id=FFx8)    │
                       │     b.w 0x712a         │
                       │  else: b.w 0xac54      │  → T4
                       └───────────┬────────────┘
                                   │
                                   ▼
              ┌──────────────────────────────────────────┐
              │ T4 (vaddr 0xac54)                        │  Trampoline #3
              │ in EXTENDED LOAD #1 segment              │  GetElementAttributes
              │ (page-padding bytes between LOAD #1      │
              │  and LOAD #2; LOAD #1 FileSiz / MemSiz   │
              │  bumped from 0xac54 to 0xb2c8)           │
              │                                          │
              │  PDU == 0x20:                            │
              │     7 sequential calls to PLT 0x3570     │
              │     (get_element_attributes_rsp):        │
              │                                          │
              │     Call 1 (idx=0, total=7, attr=Title): │
              │        buffer reset on idx==0,           │
              │        accumulate, no emit yet           │
              │     Call 2 (idx=1, total=7, attr=Artist):│
              │        accumulate, no emit               │
              │     Call 3 (idx=2, total=7, attr=Album)  │
              │     Call 4 (idx=3, total=7, attr=TrkNum) │
              │     Call 5 (idx=4, total=7, attr=Total)  │
              │     Call 6 (idx=5, total=7, attr=Genre)  │
              │     Call 7 (idx=6, total=7, attr=PlyTime)│
              │        idx+1 == total → EMIT msg=540     │
              │        with all 7 attributes packed in   │
              │     b.w 0x712a                           │
              │                                          │
              │  else (PDU != 0x20):                     │
              │     restore r0 = r5+8 (conn buffer)      │
              │     restore lr = halfword[sp+374] (SIZE) │
              │     b.w 0x65bc                           │  → original
              └────────────┬─────────────────────────────┘     unknow
                           │ (only when our chain doesn't        path
                           │  handle this PDU)
                           ▼
              ┌────────────────────────────────────────┐
              │ Original "unknow indication" at 0x65bc │  Default reject
              │                                        │  (msg=520
              │  Builds default-reject frame           │   NOT_IMPLEMENTED)
              │  blx 0x3624 (pass_through_rsp)         │
              │  → msg=520                             │
              └────────────────────────────────────────┘
```

After any of these branches, `b.w 0x712a` lands on `mov.w r9, #1` (set return value = 1) → stack-canary check at 0x712e → function epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).

The diagram above traces the original 1.0-era PDU 0x10 / 0x31 event 0x02 / 0x20 path. T4's pre-check additionally branches PDU 0x17 → T_charset, 0x18 → T_battery, 0x30 → T6, 0x40/0x41 → T_continuation, and PDU 0x31 + event ≠ 0x02 → T8 (which dispatches per-event_id to events 0x01/0x03/0x04/0x05/0x06/0x07). Two further trampolines hook native-method entries rather than the saveRegEventSeqId chain: T5 (entered from `notificationTrackChangedNative`, emits the §5.4.2 track-edge 3-tuple proactively) and T9 (entered from `notificationPlayStatusChangedNative`, emits PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED + PLAYBACK_POS_CHANGED proactively). All trampolines are catalogued in the Patch summary table below.

---

## AVRCP-driven control input path (CT → music app)

Independent of the trampoline-driven outbound metadata path. CT-driven transport keys (PLAY / PAUSE / STOP / NEXT / PREV) reach the Y1 music app via the kernel-uinput chain, NOT via the trampolines. The two paths share no state. Touching one does not affect the other.

```
1. Peer CT sends AVRCP PASSTHROUGH command (op_id 0x44 PLAY / 0x46 PAUSE / etc.)

2. mtkbt receives, parses, routes the PASSTHROUGH op_id through libextavrcp_jni.so's
   avrcp_input_init / avrcp_input_sendkey path.

3. libextavrcp_jni.so writes the EV_KEY event to /dev/input/event4 (the AVRCP
   virtual keyboard created by avrcp_input_init at boot). The op_id maps to a
   Linux key code per the shipped AVRCP.kl key-layout file:
       0x44 PLAY      → KEY_PLAYCD       (Linux 200)
       0x45 STOP      → KEY_STOPCD       (Linux 166)
       0x46 PAUSE     → KEY_PAUSECD      (Linux 201)
       0x4B FORWARD   → KEY_NEXTSONG     (Linux 163)
       0x4C BACKWARD  → KEY_PREVIOUSSONG (Linux 165)

   U1 patch NOPs the UI_SET_EVBIT(EV_REP) ioctl in avrcp_input_init so the kernel
   never enables auto-repeat for this uinput device — strict CTs that drop a
   PASSTHROUGH RELEASE no longer trigger held-key cascades.

4. Kernel input subsystem dispatches the EV_KEY event. Android's InputManager
   reads /system/usr/keylayout/AVRCP.kl, translates to KeyEvent with
   KEYCODE_MEDIA_PLAY (0x7e) / _STOP (0x56) / _PAUSE (0x7f) / _NEXT (0x57) /
   _PREVIOUS (0x58).

5. WindowManager → ViewRootImpl → Activity hierarchy. Foreground music-app
   activities extend BaseActivity, OR — for the music player screen —
   BasePlayerActivity (which itself extends BaseActivity but overrides
   dispatchKeyEvent and never delegates up). dispatchKeyEvent receives the
   KeyEvent. Patches H + H′ + H″ together apply the same early-return logic
   in both classes for keycodes 0x7e / 0x7f / 0x56 / 0x57 / 0x58:
       repeatCount == 0 → return false → propagate to framework fallback
       repeatCount  > 0 → return true  → silent consume (collapse synthetic
                                          repeats from
                                          InputDispatcher::synthesizeKeyRepeatLocked
                                          to a single one-shot per genuine press)

6. PhoneFallbackEventHandler.handleMediaKeyEvent → AudioManager.dispatchMediaKeyEvent
   → AudioService routes ACTION_MEDIA_BUTTON.

7. Either via PendingIntent fire (if a MediaButton receiver is registered with
   AudioManager) or via ordered broadcast (manifest filter, fallback). The music
   app's PlayControllerReceiver declares an ACTION_MEDIA_BUTTON intent-filter at
   priority MAX_VALUE, so it wins ordered-broadcast dispatch.

8. PlayControllerReceiver.onReceive runs Patch E's discrete-key dispatch:
       KEYCODE_MEDIA_PLAY_PAUSE (85) → playOrPause() (toggle, legacy MediaButton path)
       KEYCODE_MEDIA_PLAY (126)      → play(true)        (discrete PLAY)
       KEYCODE_MEDIA_PAUSE (127)     → pause(0x12, true) (discrete PAUSE)
       KEYCODE_MEDIA_STOP (86)       → stop()            (discrete STOP)
       KEYCODE_MEDIA_NEXT (87)       → nextSong()        (discrete NEXT)
       KEYCODE_MEDIA_PREVIOUS (88)   → prevSong()        (discrete PREV)
       others fall through to original receiver logic.

9. PlayerService method runs → IjkMediaPlayer / MediaPlayer state change → audio
   plays / pauses / stops on the Y1.
```

Steps 1-5 are verified end-to-end via `getevent` + Y1Patch debug-log instrumentation in `BaseActivity.dispatchKeyEvent`. Steps 6-8 (framework-side `AudioService.dispatchMediaKeyEvent` → `PlayControllerReceiver`) are not currently traced; see [`INVESTIGATION.md`](INVESTIGATION.md) for per-CT empirical state.

---

## Inbound frame layout (saveRegEventSeqId stack frame)

When `_Z17saveRegEventSeqIdhh` runs (entry symbol at file 0x5ee4, body at 0x5f0c), the inbound msg-519 IPC payload is laid out at:

| Offset      | Field |
|-------------|-------|
| `sp+368`    | transId (jbyte) — also auto-extracted from `conn[17]` by response builders |
| `sp+369`    | (sub_unit byte) |
| `sp+374`    | **SIZE** halfword. AV/C body length: 3=PASSTHROUGH, 9=size9 (e.g. GetCapabilities), 13=RegisterNotification, 45=GetElementAttributes-w/-7-attrs. Loaded into `lr` at file 0x644e for the original size dispatch. |
| `sp+376`    | (halfword) |
| `sp+378`    | AV/C body byte 0 (op_code: 0x00=VENDOR_DEPENDENT, 0x7c=PASSTHROUGH) |
| `sp+379-381`| company_id BE = `00 19 58` for BT-SIG |
| `sp+382`    | **PDU byte** — every trampoline reads this first |
| `sp+383`    | packet_type |
| `sp+384-385`| param_length BE |
| `sp+386`    | For PDU 0x31 RegisterNotification: **event_id** (1 byte) — extended_T2 / T8 read this to dispatch. For PDU 0x20 GetElementAttributes: first byte of the 8-byte **identifier** (track_id; sp+386..393). |
| `sp+387-390`| For PDU 0x31 event 0x05 only: **playback_interval** (4 bytes BE — CT-supplied notification cadence). Currently unread by the trampolines (T9 emits PLAYBACK_POS_CHANGED at a fixed 1 s rate regardless of CT-requested interval — spec-permissible since "shall be emitted at this interval" defines a max-interval ceiling, not a min cadence floor). For PDU 0x20: continuation of the identifier. |
| `sp+394`    | num_attributes (PDU 0x20 GetElementAttributes only) |
| `sp+395+`   | attribute_ids, 4 bytes BE each (PDU 0x20; last byte is the LSB we dispatch on) |

`r5` in saveRegEventSeqId's frame holds the conn-buffer base. **`r5+8` is the conn buffer pointer** that all `btmtk_avrcp_send_*_rsp` functions take as their first arg.

---

## The "unknow indication" path (0x65bc onwards)

This is the original code that handled "size != 3 AND size != 8" — i.e., everything we now intercept. It's also what we want our trampolines to fall through to for unhandled PDUs (so unhandled commands still get a proper msg=520 NOT_IMPLEMENTED reject instead of disappearing).

```
0x65bc: mov.w ip, #9
0x65c0: movs r4, #8
0x65c2: add.w r5, sp, #378           ; r5 → AV/C body ptr (clobbers our r5 use!)
0x65c6: stmia.w sp, {r4, ip}          ; sp[0]=8, sp[4]=9
0x65ca: str r5, [sp, #16]             ; sp[16] = body ptr
0x65cc: movs r4, #0
0x65d4: str.w lr, [sp, #12]           ; sp[12] = SIZE   ← REQUIRES lr=SIZE!
0x65d8: …
0x65de: blx 0x3624 (pass_through_rsp)
```

**Critical preconditions** (inherited from original 0x6528-0x6534):

1. `r0 = r5+8` (conn buffer) — set 16 bytes earlier; the 0x65bc code does NOT re-derive it.
2. `r1 = byte at sp+368`, `r2 = byte at sp+369`, `r3 = halfword at sp+376`.
3. `lr = halfword at sp+374` (= SIZE) — set 380 bytes earlier at file 0x644e via `ldrh.w lr, [sp, #374]`.

When our trampoline chain falls through to 0x65bc, items (1) and (3) need to be **restored** because `bl.w 0x7308` clobbers `lr` and the trampolines clobber `r0` (with PDU/event_id). r1/r2/r3 stay valid since we don't touch them.

That's why T4's fall-through pre-amble is:

```
0xac5c: ldrh.w lr, [sp, #374]    ; restore lr=SIZE
0xac60: add.w r0, r5, #8         ; restore r0=conn buffer
0xac64: b.w 0x65bc                ; → original unknow indication
```

Both `r0` and `lr` need to be restored before falling through. Restoring only `r0` leaves `pass_through_rsp` reading `lr=0x653c` (the stale bl return address) as its SIZE arg and silently dropping the response; the AVRCP service then restart-loops every 2 seconds waiting on responses that never come. Restoring `lr` from the saved canary at `[sp+374]` makes msg=520 flow correctly.

---

## ELF program-header surgery — extending LOAD #1

The original `libextavrcp_jni.so` has two LOAD segments:

```
LOAD #1: file 0x0..0xac54, vaddr 0x0..0xac54,  R+E
LOAD #2: file 0xbc08..0xc2a4, vaddr 0xcc08..0xd548, R+W
```

Between LOAD #1's end at file `0xac54` and LOAD #2's start at file `0xbc08`, the file contains **4020 zero bytes of page-alignment padding** (`0xbc08 - 0xac54`). We can write code into that padding and bump LOAD #1's `FileSiz`/`MemSiz` (program-header at file offset `0x54`, fields at +16 and +20 within the phdr) to extend the executable mapping over our new code. **No other section/segment offsets shift** — `.dynsym`/`.text`/`.rodata`/`.dynamic`/`.rel.plt` etc. all stay byte-identical. The dynamic linker just maps slightly more file content as R+E.

The patcher does this with three PATCHES entries:

1. Write the trampoline blob at file 0xac54. **Current size: 2036 bytes (extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T_papp + T8 + T9 + path strings + sentinel + PApp data tables); ~1984 bytes still free in the 4020-byte padding region.** U1 is a separate 4-byte NOP elsewhere in the binary that doesn't grow the blob.
2. Update LOAD #1 program-header `p_filesz` at file 0x64: `0xac54 → 0xb2c8` (current).
3. Update LOAD #1 program-header `p_memsz` at file 0x68: `0xac54 → 0xb2c8`.

The trampoline at 0xac54 is reachable from the existing trampolines via `b.w` (24-bit signed offset, ±16 MB range — distance from 0x72f4 to 0xac54 is ~0x395c, well within range).

---

## Reverse-engineered semantics: `btmtk_avrcp_send_get_element_attributes_rsp`

Lives at `libextavrcp.so:0x2188`, called via PLT 0x3570 in `libextavrcp_jni.so`. Argument layout is **non-obvious** and was deduced by disassembling the function:

```c
void btmtk_avrcp_send_get_element_attributes_rsp(
    void* conn,        // r0 = conn buffer (= r5+8 in saveRegEventSeqId frame)
    uint8_t arg1,      // r1 = "with-string / reset" flag:
                       //      0   = with string, append to internal buffer
                       //     !=0  = no-string finalize/reset
    uint8_t index,     // r2 = attribute INDEX in this response (0..N-1)
                       //      NOT transId
    uint8_t total,     // r3 = TOTAL number of attributes in this response
    uint8_t attr_id,   // sp[0] = attribute_id LSB (1=Title, 2=Artist, 3=Album, ...)
    uint16_t charset,  // sp[4] = 0x6a (UTF-8) — JNI hardcodes this
    uint16_t length,   // sp[8] = string length in bytes
    char*    str       // sp[12] = pointer to UTF-8 string data
);
```

**Buffer reset logic** (lines 0x21ca onwards):

```
r3 = (arg1 != 0) OR (arg2 == 0)
if r3 != 0:
    memset(internal_static_buffer, 0, 644)   ; 644 = full IPC payload size
    *internal_counter = 0
```

The buffer is zeroed when **either** `arg1` is nonzero (explicit reset / finalize) **or** `arg2 == 0` (first attribute in a new response).

**Send trigger** (lines 0x22ee–0x2310):

```
r5 = arg2 + 1
if (arg2 + 1) == arg3 AND arg3 != 0:
    GOTO send         ; last attribute path

if (arg1 != 0) OR (arg3 == 0):
    GOTO send         ; finalize / legacy path

return without sending   ; arg1==0 AND arg3 != 0 AND (arg2+1) < arg3

send:
    AVRCP_SendMessage(conn, msg_id=540, buffer, size=644)
```

So the function emits an IPC msg=540 frame when:
- `(arg2 + 1) == arg3 AND arg3 != 0` — last attribute in a multi-attribute response
- OR `arg1 != 0` — explicit finalize call
- OR `arg3 == 0` — single-shot / legacy mode (one frame per attribute)

It only **accumulates without emitting** when `arg1 == 0 AND arg3 != 0 AND (arg2+1) < arg3`.

**transId** is NOT one of the args. The function reads it from `conn[17]` (line 0x21f2: `ldrb r2, [r0, #17]`) and copies into the response's wire frame. Passing `transId` as `arg2` would be miscoding the attribute INDEX as the transId value — Title would land in `slot[transId]` of the response buffer with all other slots zero, leaving the CT to scan for the one valid attribute.

### Calling pattern for a 7-attribute response (current)

```c
// All seven calls share: conn=r5+8, arg1=0, arg3=7, charset=0x6a
send_rsp(conn, 0, idx=0, total=7, attr=0x01, len, "Y1 Title");       // accumulate
send_rsp(conn, 0, idx=1, total=7, attr=0x02, len, "Y1 Artist");      // accumulate
send_rsp(conn, 0, idx=2, total=7, attr=0x03, len, "Y1 Album");       // accumulate
send_rsp(conn, 0, idx=3, total=7, attr=0x04, len, "3");              // accumulate (TrackNumber)
send_rsp(conn, 0, idx=4, total=7, attr=0x05, len, "12");             // accumulate (TotalNumberOfTracks)
send_rsp(conn, 0, idx=5, total=7, attr=0x06, len, "Rock");           // accumulate (Genre)
send_rsp(conn, 0, idx=6, total=7, attr=0x07, len, "180000");         // (idx+1==total) → EMIT
```

Per AVRCP 1.3 §5.3.4 a missing attribute is signalled by `AttributeValueLength=0` — `TrackInfoWriter` writes empty UTF-8 string slots when the underlying tag is absent (e.g., a flat audio file with no Genre tag), strlen returns 0, and the response builder packs an attribute header with no value bytes for that entry.

**One** msg=540 IPC frame outbound containing all seven attributes.

### Calling pattern for `…send_reg_notievent_track_changed_rsp` (PLT 0x3384, used by extended_T2 / T4 / T5)

```c
void btmtk_avrcp_send_reg_notievent_track_changed_rsp(
    void* conn,           // r0 = r5+8
    uint8_t reject,       // r1 = 0 for success (event-payload path); non-zero takes
                          //      the reject path that omits the 8-byte track_id payload.
                          //      See "Note on the arg1==0 / arg1!=0 dispatch shared by all
                          //      reg_notievent_*_rsp functions" below.
    uint8_t reasonCode,   // r2 = 0x0F (INTERIM) or 0x0D (CHANGED)
    void* track_id_ptr    // r3 = pointer to 8-byte BE track_id
);
```

**transId is NOT an arg.** The function reads it from `conn[17]` (the per-conn struct that mtkbt set up for the inbound RegisterNotification command) and writes it into the response's wire frame at offset 5. Passing `transId` as `r1` would route into the reject-shape path that omits the event payload — see the historical note in the bottom subsection of this Reverse-engineered semantics block.

Cross-referenced with `notificationTrackChangedNative` at libextavrcp_jni.so:0x3bc0 which calls the same PLT with the same arg shape. extended_T2 (the actual handler reached via the T2 stub at 0x72d4) and T5 both pass `track_id_ptr` → 8 bytes of `0xFF` ("identifier not allocated, metadata not available" per AVRCP §5.4.2 Tbl 5.30 + ESR07 §2.2 — see "Wire-level track_id choice" below for the rationale).

### Calling pattern for `…send_get_capabilities_rsp` (PLT 0x35dc, used by T1)

```c
void btmtk_avrcp_send_get_capabilities_rsp(
    void* conn,         // r0 = r5+8
    uint8_t cap_id,     // r1 = 0 (we always pass 0 — likely capability-id type)
    uint8_t count,      // r2 = events count (currently 7)
    void* events_ptr    // r3 = pointer to N-byte events array
);
```

T1 advertises 8 events `[0x01..0x08]`, paired with T8 INTERIM coverage so the NOT_IMPLEMENTED rejects don't fire for any advertised event. Per the spec-compliance rule we advertise only what we actually implement; event 0x08 (PLAYER_APPLICATION_SETTING_CHANGED) is paired with T_papp + T8's INTERIM emit.

### Calling pattern for `…send_get_playstatus_rsp` (PLT 0x3564, used by T6)

From disassembly of `libextavrcp.so:0x2354` plus cross-reference with the stock JNI caller `_Z46BluetoothAvrcpService_getPlayerstatusRspNativeP7_JNIEnvP8_jobjectaiia` at `libextavrcp_jni.so:0x5680`.

```c
void btmtk_avrcp_send_get_playstatus_rsp(
    void* conn,           // r0 = r5+8
    uint8_t arg1,         // r1 = 0 for the success path that writes the
                          //      song_length / song_position / play_status fields.
                          //      Non-zero takes a path that only sets sp+10/+11 in
                          //      the IPC frame (interpreted by mtkbt as a reject).
    uint32_t song_length, // r2 = track duration in milliseconds
    uint32_t song_position,// r3 = current playback position in milliseconds
    uint8_t play_status   // sp[0] = 0x00 STOPPED / 0x01 PLAYING / 0x02 PAUSED /
                          //         0x03 FWD_SEEK / 0x04 REV_SEEK / 0xFF ERROR
);
```

Outbound IPC: `msg_id=542`, frame size 20 B. transId auto-extracted from `conn[17]` and written at frame offset 5. song_length at offset 8 (u32), song_position at offset 12 (u32), play_status at offset 16 (u8). The stock JNI (`PlayerstatusRspNative`) always passes `arg1=0` and stores a `getSavedSeqId(541)` result into `conn[25]` before the call — we don't need the latter because the conn struct is set up by mtkbt's inbound dispatch already, not by Java.

### Calling pattern for `…send_reg_notievent_playback_rsp` (PLT 0x339c, used by T8 + T9)

```c
void btmtk_avrcp_send_reg_notievent_playback_rsp(
    void* conn,           // r0 = r5+8
    uint8_t arg1,         // r1 = 0 for success (the path that writes reasonCode +
                          //      play_status into the frame); non-zero takes the
                          //      reject path.
    uint8_t reasonCode,   // r2 = 0x0F (INTERIM) or 0x0D (CHANGED)
    uint8_t play_status   // r3 = 0=STOPPED, 1=PLAYING, 2=PAUSED, 3=FWD_SEEK,
                          //      4=REV_SEEK, 0xFF=ERROR
);
```

Outbound IPC: `msg_id=544`, frame size 40 B. transId at offset 5; reasonCode at offset 8; event_id constant `0x01` at offset 9 (function bakes this in — distinguishes from track_changed_rsp's `0x02` and pos_changed_rsp's `0x05`); play_status at offset 10.

### Calling pattern for `…send_reg_notievent_pos_changed_rsp` (PLT 0x3360, used by T8 + T9)

```c
void btmtk_avrcp_send_reg_notievent_pos_changed_rsp(
    void* conn,           // r0 = r5+8
    uint8_t arg1,         // r1 = 0 for success
    uint8_t reasonCode,   // r2 = 0x0F INTERIM / 0x0D CHANGED
    uint32_t position_ms  // r3 = current playback position in milliseconds (u32)
);
```

Outbound IPC: `msg_id=544`, frame size 40 B. transId at offset 5; reasonCode at offset 8; event_id constant `0x05` at offset 9; position_ms u32 at offset 36 (note the offset jump — pos_changed buffers the u32 near the tail of the 40-byte frame, unlike track_changed which puts the 8-byte track_id at offset 11).

### Note on the arg1==0 / arg1!=0 dispatch shared by all `reg_notievent_*_rsp` functions

All `…reg_notievent_*_rsp` builders in `libextavrcp.so` are templated on the same shape (40-byte buffer, msg=544, conn[17]→transId at sp+9). Each function bakes in its event-specific constant at sp+13 (1=playback, 2=track_changed, 5=pos_changed, ...). The `cbnz` test on r1 is shared: r1==0 = "write event payload", r1!=0 = "write reject flag (sp+10=1) + reject code (sp+11=arg1) and skip event payload".

All currently shipped trampolines (extended_T2 / T4 / T5 / T6 / T8 / T9 — anything that calls a `reg_notievent_*_rsp` PLT) pass `r1 = 0` to take the spec-correct event-payload path.

### PlayerApplicationSettings response builders (PDUs 0x11-0x16) and event 0x08 builder

Reverse-engineered calling conventions for the PApp builders linked through `libextavrcp_jni.so`. Trampolines for these PDUs are not yet shipped; see `INVESTIGATION.md` Trace #17 for the disassembly notes.

| PDU | Builder | PLT | Signature |
|-----|---------|-----|-----------|
| 0x11 | `…send_list_player_attrs_rsp` | 0x35d0 | `(conn, reject, n_attrs, *attr_id_array)` — payload 14 B, msg_id 524 |
| 0x12 | `…send_list_player_values_rsp` | 0x35c4 | `(conn, reject, attr_id, n_values, *value_array)` — payload 14 B, msg_id 526. arg5 on stack. |
| 0x13 | `…send_get_curplayer_value_rsp` | 0x35b8 | `(conn, reject, n_pairs, *attr_id_array, *value_array)` — payload 18 B, msg_id 528. arg5 on stack. |
| 0x14 | `…send_set_player_value_rsp` | 0x3594 | `(conn, reject_status)` — payload 8 B, msg_id 530. ACK if reject==0, else reject with status. |
| 0x15 | `…send_get_player_attr_text_rsp` | 0x35ac | `(conn, reject, idx, total, attr_id, charset, length, *str)` — accumulator pattern (parallel to `…send_get_element_attributes_rsp`); emits when idx+1==total. Per-attribute text capped at 79 B. msg_id 532. args5-8 on stack. Static buffer at vaddr 0x5ea4. |
| 0x16 | `…send_get_player_value_text_value_rsp` | 0x35a0 | `(conn, reject, idx, total, attr_id, value_id, charset, length, *str)` — accumulator pattern. msg_id 534. args5-9 on stack. Static buffer at vaddr 0x5ffe. |
| event 0x08 | `…send_reg_notievent_player_appsettings_changed_rsp` | 0x345c | `(conn, reject, type, n, *attr_ids, *values)` — payload 40 B, msg_id 544. type: 0=INTERIM, 1=CHANGED. n is capped at 4 internally. args5-6 on stack. event_id 0x08 baked at offset 13. |

For all six PDU builders + the event builder: arg2 (reject) follows the same shape as `reg_notievent_*_rsp` — 0 = success path with full payload, !=0 = reject status with truncated payload. transId is auto-sourced from `conn[17]` in every builder.

---

## Patch summary

| Patch | File / addr | Description |
|-------|-------------|-------------|
| **mtkbt patches** (in `patch_mtkbt.py`) ||| 
| V1 | mtkbt 0x0eba58 | AVRCP version SDP attribute: 1.0 → 1.3 |
| V2 | mtkbt 0x0eba6d | AVCTP version SDP attribute: 1.0 → 1.2 |
| S1 | mtkbt 0x0f97ec | Replace 0x0311 SupportedFeatures slot with 0x0100 ServiceName pointing at "Advanced Audio" |
| P1 | mtkbt 0x144e8  | `cmp r3, #0x30` → `b.n 0x14528` (route VENDOR_DEPENDENT through msg-519 emit instead of silent-drop) |
| **JNI patches** (in `patch_libextavrcp_jni.py`) ||| 
| R1 | jni 0x6538 (4 B) | `bne.n 0x65bc; movs r5, #9` → `bl.w 0x7308` (redirect to T1) |
| T1 | jni 0x7308 (40 B) | Overwrites unused `testparmnum`. PDU 0x10 → calls `get_capabilities_rsp` via PLT 0x35dc, advertising the eight events 0x01..0x08 (PLAYBACK_STATUS / TRACK_CHANGED / TRACK_REACHED_END / TRACK_REACHED_START / PLAYBACK_POS / BATT_STATUS / SYSTEM_STATUS / PLAYER_APPLICATION_SETTING_CHANGED). |
| T2 stub | jni 0x72d0 (8 B) | Overwrites `classInitNative`. 4-byte `return 0` stub at 0x72d0 + 4-byte `b.w extended_T2` at 0x72d4 |
| extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T_papp + T8 + T9 | jni 0xac54 (2736 B) | New LOAD #1 extension, dynamically assembled by `_trampolines.py`. Per-trampoline behavior + entry conditions: see [`PATCHES.md`](PATCHES.md) `## patch_libextavrcp_jni.py` (one `###` subsection per trampoline). |
| Track-change native stub | jni 0x3bc0 (4 B) | First instruction of `notificationTrackChangedNative` rewritten to `b.w T5`. The Java side (after the MtkBt.odex sswitch_1a3 cardinality NOP) calls this native on every `metachanged` broadcast emitted by the music app; T5 emits CHANGED on the AVRCP wire asynchronously to any inbound query. The remaining 196 B of the original native body are unreachable. |
| Play-status native stub | jni 0x3c88 (4 B) | First instruction of `notificationPlayStatusChangedNative` rewritten to `b.w T9`. Paired with the MtkBt.odex sswitch_18a cardinality NOP at 0x3c4fe so every `playstatechanged` broadcast emitted by the music app lands in T9. |
| LOAD#1 filesz | jni 0x64 | `0xac54 → 0xb704` (2736 B blob). |
| LOAD#1 memsz  | jni 0x68 | Same |

Stock md5s and patcher-output md5s are baked into the patcher headers; check them before quoting.

The JNI trampoline blob is built dynamically by `src/patches/_trampolines.py` using a tiny Thumb-2 assembler in `src/patches/_thumb2asm.py`. Both files are imported by `patch_libextavrcp_jni.py` at run time. Self-tests in `_thumb2asm.py` verify several encodings against known-good byte sequences (b.w, blx, addw, movw, ldrb.w, add immediate T3).

**Wire-level `track_id` choice.**

The wire-level `Identifier` field in TRACK_CHANGED notifications is pinned to the `0xFF×8` "not bound to a particular media element" sentinel per AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 (the printed `0xFFFFFFFF` in 1.3 is a typo; ESR07 clarifies the field is 8 bytes, sentinel form `0xFFFFFFFFFFFFFFFF`). This keeps CTs in "no stable identity, refresh on each event" mode rather than the alternative "stable identity, only refresh on CHANGED" mode that some CTs adopt when given a real synthetic id. The latter mode causes high-subscribe-rate CT classes to enter a tight `RegisterNotification` storm at ~90 Hz that saturates AVCTP and drops PASSTHROUGH release frames; the sentinel mode avoids that entirely while still being spec-conformant.

Per-track CHANGED edge information is delivered by T4 / T5 detecting divergence between `y1-track-info[0..7]` and `y1-trampoline-state[0..7]` (comparison runs on real track_ids; the emitted wire packet uses the sentinel). The state file at `y1-trampoline-state[0..7]` holds the synthetic audioId derived in `TrackInfoWriter.syntheticAudioId` (= `(path.hashCode() & 0xFFFFFFFFL) | 0x100000000L`) for that internal change-detection logic.

See [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the empirical observations that drove this design choice.

### Music-app ↔ trampoline file contract

Three files, all in `/data/data/com.innioasis.y1/files/`:

- **y1-track-info** (1112 B, mode 0644 so the BT process can open it). Written by `TrackInfoWriter` on every state change atomically via tmp+rename. Full byte-level layout in [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §4.
- **y1-trampoline-state** (16 B, mode 0666, world-rw, pre-created by `TrackInfoWriter.prepareFiles` at music-app startup, updated by the trampolines):
  - 0..7  = last track_id we told the CT about (updated by T4 after emitting CHANGED, and by extended_T2 / T5 after emitting CHANGED)
  - 8     = last RegisterNotification transId (updated by extended_T2)
  - 9     = last_play_status (T9 edge-detect)
  - 10    = last_battery_status (T9 edge-detect)
  - 11    = last_repeat (T9 edge-detect)
  - 12    = last_shuffle (T9 edge-detect)
  - 13..15 = padding
- **y1-papp-set** (2 B, mode 0666). Written by T_papp 0x14 with `[attr_id, value]` on every PApp Set; consumed by `PappSetFileObserver` in the music app, which dispatches to `SharedPreferencesUtils.setMusicRepeatMode` / `setMusicIsShuffle`.

`TrackInfoWriter.prepareFiles()` ensures the BT process can reach all three files: `setExecutable(true, false)` on the files dir adds world-x for traversal; each file is created with `setReadable(true, false)` and (for the two writable from the BT side) `setWritable(true, false)`.

### Code-cave inventory

| Region | Address | Size | Used by |
|--------|---------|------|---------|
| `testparmnum` | 0x7308 | 48 bytes | T1 (40 bytes used) |
| `classInitNative` | 0x72d0 | 48 bytes | T2 stub (8 bytes used; remaining 40 zero-filled, unreachable) |
| `notificationTrackChangedNative` | 0x3bc0 | 200 bytes | T5 entry stub (4 bytes `b.w T5` used; remaining 196 unreachable) |
| `notificationPlayStatusChangedNative` | 0x3c88 | 200 bytes | T9 entry stub (4 bytes `b.w T9` used; remaining unreachable) |
| LOAD #1 padding | 0xac54..0xbc07 | 4020 bytes | trampoline blob (2736 B), ~1284 free |
| `getPlayerId` | 0x7300 | 4 bytes | (preserved, returns 0 — not touched) |
| `getMaxPlayerNum` | 0x7304 | 4 bytes | (preserved, returns 20 — not touched) |

---

## msg-id taxonomy (mtkbt's IPC, visible in EXTADP_AVRCP logs)

These are mtkbt-internal IPC IDs over the abstract socket `bt.ext.adp.avrcp`. NOT visible on the BT wire.

| msg_id | Direction | Meaning |
|--------|-----------|---------|
| 500    | various | `AVRCP_HandleA2DPInfo` |
| 502, 507 | various | Connection lifecycle |
| 519    | mtkbt → JNI | `CMD_FRAME_IND` — inbound AVRCP COMMAND from peer |
| 520    | JNI → mtkbt | `CMD_FRAME_RSP` generic ack / reject (PASSTHROUGH ack OR NOT_IMPLEMENTED) |
| 522    | JNI → mtkbt | GetCapabilities response (from `…send_get_capabilities_rsp`) |
| 540    | JNI → mtkbt | GetElementAttributes response (from `…send_get_element_attributes_rsp`) |
| 544    | JNI → mtkbt | RegisterNotification response (from `…send_reg_notievent_*_rsp`) |

---

## ARM / Thumb-2 instruction encoding gotchas (lessons from this work)

Patches add up — these tripped us up at least once each:

- **ADR T1** (16-bit) requires offset to be a multiple of 4 AND target to be 4-byte aligned. When emitting strings of non-4-aligned length, pad each string to the next 4-byte boundary so subsequent ADR targets stay aligned. ADR.W (32-bit) is more flexible.
- **POP {r4, lr}** is NOT 16-bit. Only `POP {regs, pc}` (which RETURNS) and `POP {low_regs}` are 16-bit. Restoring `lr` without returning needs `POP.W` (32-bit, 4 bytes). We solved this in T4 by not pushing / popping at all — `r4-r9` are restored by saveRegEventSeqId's epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).
- **ADD Rd, SP, #imm** (16-bit T1) requires imm to be a multiple of 4 AND in 0..1020. For arbitrary 12-bit immediates use `ADDW Rd, SP, #imm12` (T4, 32-bit, no rotation / alignment requirement).
- **bl.w** clobbers `lr`. **b.w** doesn't. **blx** changes ARM / Thumb mode based on the target's bit 0 (PLT stubs are at even addresses → switches to ARM, which is what we want).
- **AAPCS callee-saved regs (r4-r11)**: saveRegEventSeqId pushes them in its prologue and restores them in the epilogue at 0x7154. Our trampolines can trash r4-r9 freely without local push/pop — they'll be restored by the parent function's epilogue when we `b.w 0x712a`.

---

## Adding a new PDU handler (a recipe)

When adding a new T-trampoline (e.g., GetPlayStatus PDU 0x30):

1. **Find the response builder PLT entry** in `libextavrcp_jni.so` via `objdump -R … | grep <name>` and follow the GOT entry to its PLT stub.
2. **Disassemble the C function** in `libextavrcp.so` to learn its real argument semantics. Don't assume — the names (`arg1` etc.) don't tell you what the args mean. Look for:
   - Buffer reset condition (when does it `memset` the internal buffer?)
   - Send trigger condition (which args make it call `AVRCP_SendMessage`?)
   - Where transId comes from (usually `conn[17]`, not an arg)
3. **Allocate cave space** in the LOAD #1 padding region (currently ~1284 bytes free past 0xb704 — the trampoline blob is 2736 B; the padding region is 4020 B total, ending at LOAD #2's start at 0xbc08).
4. **Wire it into the chain**: change the previous trampoline's "unknown" branch (the `b.w` to `0x65bc` or to the next trampoline) to point at your new entry.
5. **End with**:
   - `b.w 0x712a` for the success path (lands on stack-canary check + epilogue).
   - For the fall-through path: restore `r0 = r5+8` and `lr = halfword[sp+374]` before `b.w 0x65bc`.
6. **Bump LOAD #1 `FileSiz`/`MemSiz`** to cover your new bytes.
7. **Verify with objdump** before committing — disassemble your bytes and confirm every branch resolves to the intended target.

---

## Cross-component state dependencies

Every state read or write that crosses process boundaries. Consult this table before touching any of these surfaces — every entry is a potential regression source.

| State | Owner | Read by | Set by | Reset by | Notes |
|---|---|---|---|---|---|
| `sPlayServiceInterface` (byte field@1267) | `MtkBt.odex` (Java, in BT process) | `BTAvrcpMusicAdapter.startToBindPlayService` (gate read), other adapter methods | `BTAvrcpMusicAdapter.startToBindPlayService` (set true at bind start) | F2 patches `BluetoothAvrcpService.disable()` to set false | Critical. If false → AVRCP wire degrades to compile-time 1.0 dispatch + no Java callbacks. |
| `mMusicService` (IBinder field) | `BTAvrcpMusicAdapter` (Java, in BT process) | All adapter methods that delegate to the bridge (transact codes 1 / 3 / 4 / 13-31) | `BTAvrcpMusicAdapter$4.onServiceConnected` after `bindService` succeeds | `onServiceDisconnected` (sets null), `disable` | Required even though the trampolines bypass the Java path for most queries — MtkBt's `mMusicService != null` checks gate the cardinality-NOP-driven Java callback path. |
| `y1-track-info` (1112 B file) | Music app `TrackInfoWriter` | T4 (full file), T5 (16 + 800 B), T6 (offsets 776..795), T8 (792 / 794), T9 (792 / 794 / 780..787) — all in BT process | `TrackInfoWriter.flush()` on every state change (driven by `PlaybackStateBridge` edges, `BatteryReceiver`, `PappStateBroadcaster`) | Process shutdown / OS reboot | Mode `0644`, world-readable. Path: `/data/data/com.innioasis.y1/files/y1-track-info`. **Must be written before the corresponding broadcast fires.** |
| `y1-trampoline-state` (16 B file) | Music app `TrackInfoWriter.prepareFiles` (initial create) + trampolines (mutate) | All trampolines that need edge-detection (T4 / T5 / T9) | T4 / extended_T2 / T5 (after CHANGED emit) and T9 (after edge fires) write back | — | Mode `0666`, world-rw. Both processes write it. Path: `/data/data/com.innioasis.y1/files/y1-trampoline-state`. |
| `y1-papp-set` (2 B file) | Music app `TrackInfoWriter.prepareFiles` (initial create) + T_papp 0x14 (write on PApp Set) | Music app `PappSetFileObserver` | T_papp 0x14 writes `[attr_id, value]` on every CT-initiated PApp Set | — | Mode `0666`, world-rw. Path: `/data/data/com.innioasis.y1/files/y1-papp-set`. CT → Y1 side of the Repeat / Shuffle round-trip. |
| `metachanged` broadcast | Music app `PlayerService` fires; MtkBt's `BluetoothAvrcpReceiver` consumes | `BluetoothAvrcpReceiver` (manifest-declared in MtkBt.apk) | Music app's track-load path sends `com.android.music.metachanged` on track change | n/a | Wakes the chain into `notificationTrackChangedNative` → T5 (proactive TRACK_CHANGED 3-tuple). MtkBt.odex cardinality NOP at file 0x3c530 makes the Java callback fire unconditionally. |
| `playstatechanged` broadcast | Music app `PlayerService` + `PappStateBroadcaster` fire; MtkBt's `BluetoothAvrcpReceiver` consumes | Same as above | Fires on play/pause/stop edge, on battery bucket transition, on the 1 s position tick while playing, and on every `musicRepeatMode` / `musicIsShuffle` change | n/a | Wakes `notificationPlayStatusChangedNative` → T9. MtkBt.odex cardinality NOP at file 0x3c4fe makes it fire unconditionally on event 0x01. |
| `mMediaButtonReceiver` slot (AudioManager) | Android system service | AudioService for ACTION_MEDIA_BUTTON dispatch routing | Not actively registered; music app's manifest-declared `PlayControllerReceiver` (priority=MAX_INT for ACTION_MEDIA_BUTTON) is what receives via ordered broadcast | — | **Not consulted by MtkBt** for service discovery — verified via dex string scan. The RCC subsystem on Android 4.2 falls back to ordered broadcast when no PendingIntent receiver is registered, which is the active path since Y1Bridge does not call `registerMediaButtonEventReceiver`. |
| `IBTAvrcpMusic` / `IMediaPlaybackService` Binder | `Y1Bridge.MediaBridgeService` (`com.koensayr.y1.bridge`) | MtkBt `BTAvrcpMusicAdapter` post-bind | Y1Bridge's own manifest declares the service with the `com.android.music.MediaPlaybackService` intent-filter; `bindService` cold-starts the bridge process. | `onUnbind` | C-side trampolines deliver real metadata + control on the AVRCP wire; the Binder exists so `mMusicService != null` checks pass and MtkBt issues REGISTER_NOTIFICATION. |

Touching any of these requires (a) tracing what depends on it, (b) confirming the change won't break the dependent path, (c) capturing on-device evidence post-flash.

---

## See also

- [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) — current ICS Table 7 coverage scorecard (every Mandatory + every Optional row).
- [`PATCHES.md`](PATCHES.md) — per-patch byte-level reference.
- [`INVESTIGATION.md`](INVESTIGATION.md) — chronological investigation history including the gdbserver capture work and dead-end paths.
- `src/patches/patch_libextavrcp_jni.py` — the patcher containing R1 / T1 / T2 / T4. Header comments and PATCHES list are the source of truth for byte-level details.
