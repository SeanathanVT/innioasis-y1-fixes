# Patch Reference

Byte-level reference for the patches currently shipped by this repo. Each section describes what the patch ships **today** (offsets, before / after bytes, rationale, ICS status). For the commit-by-commit evolution that produced the current shape — including reverts, dead-end attempts, and the empirical evidence that motivated each behavior change — see [`INVESTIGATION.md`](INVESTIGATION.md) and `git log`. Spec citations follow the discipline in [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §0.

## Patch ID Legend

| ID(s) | Binary | Site / effect |
|---|---|---|
| **V1, V2, S1, P1** | `mtkbt` | SDP shape (AVRCP 1.0→1.3, AVCTP 1.0→1.2, ServiceName-for-SupportedFeatures swap, force-PASSTHROUGH-emit op_code dispatch). |
| **R1, T1, T2 stub, extended_T2, T4, T5, T_charset, T_battery, T_continuation, T6, T8, T9, U1** | `libextavrcp_jni.so` | Trampoline chain in `_Z17saveRegEventSeqIdhh` + LOAD #1 page-padding extension + uinput EV_REP NOP. Synthesises AVRCP 1.3 metadata responses directly from C, bypassing the no-op Java AVRCP TG. |
| **F1, F2** | `MtkBt.odex` | `getPreferVersion()=14` to unblock 1.3+ command dispatch through MtkBt's Java layer; `disable()` resets `sPlayServiceInterface`. |
| **odex cardinality NOPs** (×2) | `MtkBt.odex` | NOP the `if-eqz v5` cardinality gates in `BTAvrcpMusicAdapter.handleKeyMessage` for events 0x02 (TRACK_CHANGED, sswitch_1a3) and 0x01 (PLAYBACK_STATUS_CHANGED, sswitch_18a) so the JNI natives fire on every Y1MediaBridge broadcast. Pairs with T5 / T9 in `libextavrcp_jni.so`. |
| **A, B, C, E, H, H′, H″** | `com.innioasis.y1*.apk` | Smali edits: A/B/C for Artist→Album navigation; E for discrete PASSTHROUGH PLAY/PAUSE/STOP/NEXT/PREVIOUS routing per AV/C Panel Subunit Spec op_id table; H for foreground-activity propagation of `KEYCODE_MEDIA_PLAY/PAUSE/STOP/NEXT/PREVIOUS`; H′ for the same propagation in `BasePlayerActivity` (which overrides `dispatchKeyEvent` and bypasses BaseActivity); H″ adds a `repeatCount > 0 → silent consume` filter to both H and H′ so framework-synthesized key repeats from `InputDispatcher::synthesizeKeyRepeatLocked` don't trigger long-press FF/RW handlers. |
| **AH1** | `libaudio.a2dp.default.so` | `A2dpAudioStreamOut::standby_l` cond-flip: `beq 8684` → `b 8684` at file offset `0x000086ab` so silence-timeout standby skips `a2dp_stop` unconditionally. Keeps the AVDTP source stream alive across pauses; matches AVDTP 1.3 §8.13 / §8.15 expectation that PAUSED leaves the stream paused-but-up. |
| **su** | `/system/xbin/su` | Setuid-root `su` binary installed by `--root` flag. Replaces the historical adbd byte-patch attempts. |

> **Not shipped (attempted and removed):** G1/G2 (mtkbt xlog redirect, crashed at NULL fmt — closed without root or daemon-side tooling); H1/H2/H3 (adbd setuid byte patches, broke ADB protocol — superseded by `src/su/`). Earlier byte-patch experiments preserved in [`INVESTIGATION.md`](INVESTIGATION.md).

---

## `patch_mtkbt.py`

Four byte patches against stock `/system/bin/mtkbt`. Three reshape the served SDP record so a peer CT engages with AVRCP 1.3+ COMMANDs (per AVRCP 1.3 §6 Service Discovery Interoperability Requirements + ESR07 §2.1 / Erratum 4969 clarifying AVCTP version values); one reroutes inbound VENDOR_DEPENDENT frames into the JNI msg-519 emit path so the trampoline chain can respond.

**V1 — AVRCP 1.0 → 1.3** at file `0x0eba58` (1 byte): `0x00` → `0x03`. LSB of the served Group D ProfileDescList Version field.

**V2 — AVCTP 1.0 → 1.2** at file `0x0eba6d` (1 byte): `0x00` → `0x02`. LSB of the served Group D ProtocolDescList AVCTP Version field.

**S1 — `0x0311 SupportedFeatures` → `0x0100 ServiceName`** at file `0x0f97ec` (12 bytes):

| | bytes | shape |
|---|---|---|
| before | `11 03 03 00 59 ba 0e 00 00 00 00 00` | attr=`0x0311`, len=3, ptr=`0x0eba59` (→ `uint16 0x0001`) |
| after  | `00 01 11 00 ce b9 0e 00 00 00 00 00` | attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` (→ `25 0f "Advanced Audio\0"`) |

Reuses the existing "Advanced Audio" SDP-encoded string from mtkbt's A2DP record. Cost: the served record loses the `0x0311 SupportedFeatures` attribute. CTs in our test matrix engage with the record without it.

**P1 — force PASSTHROUGH-emit branch** at file `0x144e8` (2 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `30 2b` | `cmp r3, #0x30` |
| after  | `1e e0` | `b.n 0x14528` |

Replaces the first comparison in fn `0x144bc`'s op_code dispatch with an unconditional branch to the PASSTHROUGH-emit branch at `0x14528` (which ends with `bl 0x10404`, the function that emits msg 519 CMD_FRAME_IND to the JNI socket). Pre-P1, VENDOR_DEPENDENT frames took the `bcc 0x1454a` branch and only logged via `bl 0x11374`; post-P1 every AV/C frame flows through the emit path. Cost: VENDOR_DEPENDENT bytes get interpreted in PASSTHROUGH-shaped fields, so mtkbt's mid-stack response may be malformed — but the JNI trampoline chain takes over before that matters.

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `a37d56c91beb00b021c55f7324f2cc09`.

---

## `patch_libextavrcp_jni.py`

The trampoline chain that synthesises AVRCP 1.3 responses directly from the JNI library, bypassing the no-op Java AVRCP TG. Patches into `_Z17saveRegEventSeqIdhh` (the JNI msg-519 receive function, body at file `0x5f0c`) and extends LOAD #1's filesz / memsz to map the page-alignment padding region as R+E for trampoline code.

### R1 — redirect at `0x6538` (4 bytes)

| | bytes | mnemonic |
|---|---|---|
| before | `40 d1 09 25` | `bne.n 0x65bc` + `movs r5, #9` |
| after  | `00 f0 e6 fe` | `bl.w 0x7308` |

Diverts the size!=3 dispatch arm to T1 instead of falling into "unknow indication". Destroys the size==8 path's `movs r5, #9`, which is acceptable because mtkbt-as-1.0 never legitimately produces size==8 frames on this device.

### T1 — GetCapabilities (PDU 0x10) at `0x7308` (40 bytes)

Overwrites the unused JNI debug method `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa` (~44 byte slot). Detects PDU 0x10, calls `btmtk_avrcp_send_get_capabilities_rsp` via PLT `0x35dc` with the 7-element `EventsSupported` array `[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]`, branches to epilogue at `0x712a`. Fall-through (b.w `0x72d4`) bridges to T2.

Per AVRCP 1.3 §5.4.2 + ICS Table 7 row 11, GetCapabilities is **mandatory** for any TG advertising PASS THROUGH Cat 1 (which our V1 SDP does). The advertised events are exactly what we implement — per the spec-compliance directive, advertise only what we cover (event `0x08 PLAYER_APPLICATION_SETTING_CHANGED` is unadvertised because PlayerApplicationSettings is deferred).

### T2 stub + extended_T2 — RegisterNotification (PDU 0x31) entry

T2 stub at `0x72d0` (8 bytes) overwrites `classInitNative` with `movs r0, #0; bx lr` (preserves the `return 0` contract; loses the debug logs) followed by `b.w extended_T2`. extended_T2 lives in the LOAD #1 padding region; it handles event 0x02 TRACK_CHANGED specifically:

1. Read `y1-track-info[0..7]` (track_id) into a stack buffer.
2. Write `[track_id || transId || pad]` to `y1-trampoline-state` so T4 can detect track-id edges later.
3. Reply INTERIM via `reg_notievent_track_changed_rsp` (PLT `0x3384`) with `r1=0` (success), `r2=REASON_INTERIM` (`0x0f`), `r3=&sentinel_ffx8`.

Other PDU / event combos fall through to T4 (PDU 0x20 → main, 0x17 → T_charset, 0x18 → T_battery, 0x30 → T6, 0x40 / 0x41 → T_continuation, 0x31+event≠0x02 → T8) before hitting the original "unknow indication" path.

`r1=0` matters: response builders dispatch on r1 — `r1==0` writes the spec-correct event payload (reasonCode + event_id + 8-byte track_id memcpy per AVRCP 1.3 §5.4.2 Table 5.30); `r1!=0` writes a reject-shape frame. We pass `r1=0` everywhere.

**Track_id sentinel** = `0xFFFFFFFFFFFFFFFF` (8 bytes of 0xFF) per AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 (printed `0xFFFFFFFF` in 1.3 is a typo; ESR07 clarifies the field is 8 bytes — "this information is not bound to a particular media element"). Keeps CTs in poll-on-each-event mode rather than the alternative "stable identity, refresh on CHANGED only" mode (which deadlocks against a reactive-only TG). The `y1-trampoline-state[0..7]` field still holds the real track_id internally so T4 / T5 can detect edges and emit CHANGED proactively.

### T4 — GetElementAttributes (PDU 0x20)

In the LOAD #1 padding region, reached from extended_T2's PDU-0x20 dispatch arm. Seven sequential calls to `btmtk_avrcp_send_get_element_attributes_rsp` via PLT `0x3570` with `arg2=0..6`, `arg3=7` — only the seventh call (where `arg2+1 == arg3`) emits a frame, packing all seven AVRCP 1.3 §5.3.4 attributes into a single response:

| attr_id | Name | Source slot in `y1-track-info` |
|---|---|---|
| 0x01 | Title | `[8..263]` |
| 0x02 | Artist | `[264..519]` |
| 0x03 | Album | `[520..775]` |
| 0x04 | TrackNumber | `[800..815]` (UTF-8 ASCII decimal) |
| 0x05 | TotalNumberOfTracks | `[816..831]` (UTF-8 ASCII decimal) |
| 0x06 | Genre | `[848..1103]` |
| 0x07 | PlayingTime | `[832..847]` (UTF-8 ASCII decimal milliseconds) |

All values ship as UTF-8 (charset `0x006A`); per §5.3.4 a missing attribute is signalled by `AttributeValueLength=0`, which is what an empty string slot produces (strlen returns 0, the response builder packs the 8-byte attribute header with no value bytes). The numeric attrs (4 / 5 / 7) are stored pre-formatted as ASCII strings on the Y1MediaBridge side rather than binary u16 / u32 with a Thumb-2 itoa, keeping the trampoline a uniform strlen+memcpy loop.

T4 also detects track-id edges (compares `y1-track-info[0..7]` against `y1-trampoline-state[0..7]`) and emits a reactive CHANGED via `reg_notievent_track_changed_rsp` before the GetElementAttributes response, then writes the new track_id back to state.

Pre-check dispatch table: `0x20 → main`, `0x17 → T_charset`, `0x18 → T_battery`, `0x30 → T6`, `0x40 → T_continuation`, `0x41 → T_continuation`, `0x31+event≠0x02 → T8`, else fall through to "unknow indication".

### T5 — proactive TRACK_CHANGED on Y1 track-change broadcast

In LOAD #1 padding. Entered via `b.w T5` from the patched first instruction of `notificationTrackChangedNative` at file offset `0x3bc0`:

| | bytes | mnemonic |
|---|---|---|
| before | `2D E9 F0 47` | `stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}` (function prologue) |
| after  | `[b.w T5 emitted by patcher]` | branch to T5 trampoline |

T5 obtains the AVRCP per-conn struct via JNI helper at `0x36c0` (the same helper the stock native called), reads `y1-track-info` (full 800 B) and `y1-trampoline-state`, and on track-id divergence emits the AVRCP 1.3 §5.4.2 track-edge 3-tuple in spec order:

1. `reg_notievent_reached_end_rsp` (PLT `0x3378`, event 0x03 — Tbl 5.31) **only when** `y1-track-info[793]` (the `previous_track_natural_end` flag set by Y1MediaBridge) `== 1`. Strict spec semantic: TRACK_REACHED_END fires on natural end, not on a skip.
2. `reg_notievent_track_changed_rsp` (PLT `0x3384`, event 0x02 — Tbl 5.30) with `r1=0`, `r2=REASON_CHANGED` (`0x0d`), `r3=&sentinel_ffx8`. Always.
3. `reg_notievent_reached_start_rsp` (PLT `0x336c`, event 0x04 — Tbl 5.32) with `r1=0`, `r2=REASON_CHANGED`. Always (every track edge crosses a start-of-new-track boundary).

Then writes the new track_id back to state and returns `jboolean(1)`.

Fired on every Y1MediaBridge `com.android.music.metachanged` broadcast (after the MtkBt.odex sswitch_1a3 cardinality NOP at 0x3c530 wakes the dispatch path). The remaining 196 bytes of the original native body are unreachable. T5's frame is 816 B (16 state + 800 file_buf, mirroring T9's frame shape — needed so T5 can read `file[793]` for the natural-end gate).

### T_charset — InformDisplayableCharacterSet (PDU 0x17)

Branched from T4's pre-check on PDU 0x17. Calls `inform_charsetset_rsp` via PLT `0x3588` with `r1=0` (success). 14 bytes. Tail-jumps to t4_to_epilogue. AVRCP 1.3 §5.2.7 — strict CTs send this once at connect to declare their charset support; pre-T_charset our TG NACKed, which strict CTs interpret as the TG distrusting subsequent metadata.

### T_battery — InformBatteryStatusOfCT (PDU 0x18)

Same shape as T_charset, calls `battery_status_rsp` via PLT `0x357c`. AVRCP 1.3 §5.2.8.

### T_continuation — RequestContinuingResponse (0x40) / AbortContinuingResponse (0x41)

Branched from T4's pre-check on PDU 0x40 or 0x41. Restores `lr` canary + `r0=conn` and tail-jumps to UNKNOW_INDICATION (the catch-all reject path that emits AV/C NOT_IMPLEMENTED via msg=520). Functionally identical to the catch-all fall-through but routed through an explicit dispatch in the pre-check so ICS Table 7 rows 31-32 read "shipped" rather than "fall-through".

AVRCP 1.3 §4.7.7 / §5.5: continuation is initiated by the TG setting `Packet Type=01` (start) in a response — the CT only sends 0x40 in reply to a previously-fragmented response. `get_element_attributes_rsp` never sets the start-of-fragmentation flag (verified: 2868 PDU 0x20 frames in a single TV capture, 100% `packet_type=0x00`); mtkbt fragments below at the AVCTP layer transparently. Across all 43 captures, zero 0x40 / 0x41 PDUs from any CT in the test matrix. The trampoline body is 6 bytes (one `ldrh.w`, one `add.w`, one `b.w`).

§6.15.2 specifies AV/C INVALID_PARAMETER (status 0x05) as the spec-strict response when receiving 0x40 without prior fragmentation; NOT_IMPLEMENTED is a different but spec-acceptable AV/C reject for an unsupported PDU and is functionally indistinguishable to the CT (both are reject frames; the CT abandons the continuation flow either way). If a future capture surfaces non-zero 0x40 traffic, upgrade to a stateful continuation handler that re-emits the buffered response.

### T6 — GetPlayStatus (PDU 0x30)

Branched from T4's pre-check on PDU 0x30. Reads `y1-track-info[776..795]` (4 BE u32 fields: duration_ms / position_at_state_change_ms / state_change_time_sec / playing_flag), byte-swaps the u32s to host order via Thumb-2 `REV`, and calls `btmtk_avrcp_send_get_playstatus_rsp` via PLT `0x3564` with `arg1=0` + `r2=duration_ms` + `r3=live_position_ms` + `sp[0]=play_status`. Outbound `msg_id=542`, 20-byte IPC frame.

**Live position extrapolation:** when `playing_flag == PLAYING` (Y1MediaBridge's enum maps directly to AVRCP 1.3 §5.4.1 Table 5.26 PlayStatus), T6 calls `clock_gettime(CLOCK_BOOTTIME, &timespec)` (NR=263, clk_id=7 — same monotonic source Y1MediaBridge stamps `mStateChangeTime` from), computes `live_pos = saved_pos + (now_sec - state_change_sec) * 1000`, passes that as r3. When STOPPED / PAUSED the position field stays at the saved freeze point. Implements AVRCP 1.3 §5.4.1 Table 5.26's `SongPosition` definition ("the current position of the playing in milliseconds elapsed"). `struct timespec` is stashed in unused outgoing-args slack at sp+8..15 inside the existing T6 frame (no frame growth).

ICS Table 7 row 21: GetPlayStatus is **mandatory** for any TG that ships GetElementAttributes Response (per ICS condition C.2). T6 closes that mandatory row.

### T8 — RegisterNotification dispatcher for events ≠ 0x02

In LOAD #1 padding. Branched from extended_T2's "PDU 0x31 + event ≠ 0x02" arm. Reads `y1-track-info` for events that need payloads (0x01 / 0x05), then dispatches on event_id and calls the matching `reg_notievent_*_rsp` PLT entry:

| event_id | name | spec § | PLT | payload |
|---|---|---|---|---|
| 0x01 | PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | `0x339c` | play_status u8 (from `y1-track-info[792]`) |
| 0x03 | TRACK_REACHED_END | §5.4.2 Tbl 5.31 | `0x3378` | (none) |
| 0x04 | TRACK_REACHED_START | §5.4.2 Tbl 5.32 | `0x336c` | (none) |
| 0x05 | PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | `0x3360` | position_ms u32 (from `y1-track-info[780..783]`, REV-swapped) |
| 0x06 | BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | `0x3354` | battery_status u8 from `y1-track-info[794]` (real bucket from `Intent.ACTION_BATTERY_CHANGED`) |
| 0x07 | SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | `0x3348` | canned `0x00 POWER_ON` (intentional — while trampolines run the system is by definition POWER_ON; the canned value IS the real value) |

All response builders share the calling convention `r0=conn`, `r1=0` (success), `r2=reasonCode`, `r3=event-specific u8/u32`. Unknown event_ids fall through to "unknow indication" for the spec-correct NOT_IMPLEMENTED reject. T8 handles INTERIM for every event_id; proactive CHANGED for events 0x01/0x05/0x06 lives in T9 (entered from `notificationPlayStatusChangedNative`) and for 0x02/0x03/0x04 in T5/extended_T2 (entered from `notificationTrackChangedNative` / extended_T2's PDU 0x31 + event 0x02 arm respectively). Event 0x07 SYSTEM_STATUS_CHANGED is intentionally INTERIM-only (see footnote in `docs/BT-COMPLIANCE.md` §2).

### T9 — proactive PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED + PLAYBACK_POS_CHANGED

T5's structural twin for events 0x01, 0x06, and 0x05. Entered via `b.w T9` from the patched first instruction of `notificationPlayStatusChangedNative` at file offset `0x3c88`:

| | bytes | mnemonic |
|---|---|---|
| before | `2D E9 F3 41` | function prologue |
| after  | `[b.w T9 emitted by patcher]` | branch to T9 trampoline |

T9 reads `y1-track-info` into its file buffer, then runs three independent edge / cadence checks:

- **play_status:** compare file[792] vs state[9] (`last_play_status`, previously pad). On inequality, emit `reg_notievent_playback_rsp` via PLT `0x339c` with `r1=0`, `r2=REASON_CHANGED` (`0x0d`), `r3=play_status`. Update state[9].
- **battery_status:** compare file[794] vs state[10] (`last_battery_status`). On inequality, emit `reg_notievent_battery_status_changed_rsp` via PLT `0x3354` with `r1=0`, `r2=REASON_CHANGED`, `r3=battery_status`. Update state[10].
- **playback_pos:** if file[792] == 1 (PLAYING), `clock_gettime(CLOCK_BOOTTIME, &timespec)` (NR=263, clk_id=7 via `svc 0`), compute `live_pos = REV(file[780..783]) + (now_sec - REV(file[784..787])) * 1000` and emit `reg_notievent_pos_changed_rsp` via PLT `0x3360` with `r2=REASON_CHANGED`, `r3=live_pos`. Same arithmetic T6 does for GetPlayStatus, so position parity is maintained between polled GetPlayStatus and notification CHANGED. T9's frame is 824 B (16 state + 800 file_buf + 8 timespec at sp+816..823).

If play or battery changed, the 16 B state file is written back (single combined write per fire); the position emit is independent and never dirties state. Fires on every Y1MediaBridge `playstatechanged` broadcast (after the MtkBt.odex sswitch_18a cardinality NOP at 0x3c4fe wakes the dispatch path). Closes AVRCP 1.3 §5.4.2 Table 5.29's CHANGED requirement on event-0x01 subscribers, Table 5.34's CHANGED requirement on event-0x06 subscribers, and Table 5.33's CHANGED requirement on event-0x05 subscribers.

Y1MediaBridge fires `playstatechanged` whenever any of the following occurs:
- actual play state edge
- battery bucket transition via `mBatteryReceiver` on `Intent.ACTION_BATTERY_CHANGED` (level+plug bucket-mapped to the AVRCP §5.4.2 Tbl 5.35 enum)
- 1 s `mPosTickRunnable` Handler.postDelayed loop while `mIsPlaying` (started on the play edge in `onStateDetected`, cancelled on the pause / stop edge and in `onDestroy`).

Stock MtkBt's battery dispatch chain via `BTAvrcpSystemListener.onBatteryStatusChange` is dead — `BTAvrcpMusicAdapter$2` overrides it with a log-only stub — so reusing `playstatechanged` as the trigger is the cheapest correct alternative, with `BATT_STATUS_NORMAL` retained only as the safe default for a short y1-track-info file (`stack_buf` is memset to zero before the read). The position emit deviates slightly from strict spec (we emit at our 1 s cadence rather than the CT-supplied `playback_interval`); this is a permissible floor since "shall be emitted at this interval" defines a maximum interval, not a minimum cadence — emitting more frequently over-serves rather than under-serves.

### U1 — disable kernel auto-repeat on the AVRCP `/dev/uinput` keyboard

At file `0x74e8` (4 bytes), inside `avrcp_input_init`:

| | bytes | mnemonic |
|---|---|---|
| before | `fc f7 b4 e8` | `blx ioctl@plt` |
| after  | `00 bf 00 bf` | `nop ; nop` (Thumb-2) |

`avrcp_input_init` (real body at `0x73c8`, called from `BluetoothAvrcpService_activate_1req` and `wakeupListenerNative`) opens `/dev/uinput` at `0x73f2`, `strncpy`s the device name `"AVRCP"` (string at `0x828b`) into a `uinput_user_dev` struct, sets `id.bustype = BUS_BLUETOOTH (5)` at `0x749a`, and issues a four-call `UI_SET_EVBIT` sequence:

| Offset | Bytes | Decoded |
|---|---|---|
| `0x74cc` | `23 49 01 22 20 46 7e 44 fc f7 be e8` | `UI_SET_EVBIT, EV_KEY (1)` |
| `0x74d8` | `20 49 02 22 20 46 fc f7 ba e8` | `UI_SET_EVBIT, EV_REL (2)` (vendor typo, harmless) |
| **`0x74e2`** | **`1e 49 14 22 20 46 fc f7 b4 e8`** | **`UI_SET_EVBIT, EV_REP (0x14)` ← U1 target** |
| `0x74ec` | `1b 49 00 22 20 46 fc f7 b0 e8` | `UI_SET_EVBIT, EV_SYN (0)` |

NOPing only the third call drops `EV_REP` from `dev->evbit` without disturbing the other event-class claims. Linux's `input_register_device()` calls `input_enable_softrepeat(dev, 250, 33)` only if `EV_REP` is set — by NOT claiming it, the kernel never schedules the soft-repeat timer for this device. Without auto-repeat, a dropped PASSTHROUGH RELEASE no longer drives a 25 Hz `KEY_xxx REPEAT` cascade against InputDispatcher → media-key broadcast → haptic feedback (the "vibration loop" symptom on strict CTs).

Spec-correct per AVRCP 1.3 §4.6.1 (PASS THROUGH command, defined in AV/C Panel Subunit Specification ref [2]): the CT is responsible for periodic re-send during a held button; the TG forwards one event per frame, not synthesizing extras at the input layer. Local Y1 hardware buttons are unaffected — they go through `mtk-kpd` (event0) / `mtk-tpd-kpd` (event3), not the patched AVRCP uinput device.

### LOAD #1 program-header surgery

The patcher writes the trampoline blob into LOAD #1's page-alignment padding (4020 zero bytes between LOAD #1's stock end at file `0xac54` and LOAD #2's start at `0xbc08`) and bumps LOAD #1's `p_filesz` and `p_memsz` to map the new code as R+E:

- offset+16 (`p_filesz`): `0xac54 → 0xb2c8`
- offset+20 (`p_memsz`): `0xac54 → 0xb2c8`

Current trampoline blob is 1652 bytes (~2368 bytes still free in the 4020-byte padding region). No other section / segment offsets shift; `.dynsym` / `.text` / `.rodata` / `.dynamic` / `.rel.plt` etc. all stay byte-identical.

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `a2d41f924e07abff4a18afb87989b04c`.

**For the full architectural reference** (data-path diagram, response-builder calling conventions, ELF program-header surgery details, code-cave inventory, msg-id taxonomy, Thumb-2 encoding gotchas), see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## `patch_mtkbt_odex.py`

Patches `MtkBt.odex` with four byte edits and recomputes the DEX adler32 checksum embedded in the ODEX header.

**F1** at file `0x3e0ea` (1 byte): `0a → 0e`. `BTAvrcpProfile.getPreferVersion()` returns the BlueAngel-internal flag value 14 instead of 10. This is internal flag bookkeeping inside MtkBt's Java-side dispatcher — it unblocks 1.3+ command handling on a stack that was originally compiled for an earlier AVRCP version. The wire shape is unchanged; we ship AVRCP 1.3 PDUs only. See [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §1.

**F2** at file `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false`. Fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts.

**Cardinality NOP — TRACK_CHANGED** at file `0x03c530`: NOPs the `if-eqz v5, :cond_184` cardinality gate in `BTAvrcpMusicAdapter.handleKeyMessage`'s sswitch_1a3 (event 0x02 case). Java's `mRegisteredEvents` BitSet is permanently empty (Java-side AVRCP TG bookkeeping isn't updated by our trampolines), so without this NOP `notificationTrackChangedNative` is never invoked. With it, the native fires on every Y1MediaBridge `metachanged` broadcast and lands in T5 (libextavrcp_jni.so). Pairs with T5.

**Cardinality NOP — PLAYBACK_STATUS_CHANGED** at file `0x03c4fe`: same idiom for sswitch_18a (event 0x01 case). Without this, `notificationPlayStatusChangedNative` is never invoked. With it, the native fires on every `playstatechanged` broadcast and lands in T9. Pairs with T9.

**MD5s:** Stock `11566bc23001e78de64b5db355238175` → Output `fa2e34b178bee4dfae4a142bc5c1b701`.

---

## `patch_libaudio_a2dp.py`

Single-byte cond-flip in `_ZN20android_audio_legacy18A2dpAudioInterface18A2dpAudioStreamOut9standby_lEv` (the AOSP A2DP HAL's standby path).

**AH1 — `beq 8684 → b 8684`** at file `0x000086ab` (1 byte): `0x0a` → `0xea`. ARM condition-code flip from `EQ` to `AL` (always). Forces standby_l's `if (mIsStreaming != 0) call a2dp_stop` guard to ALWAYS skip the call site. The instructions at 0x86ac-0x86b8 (`ldr r0, [r4,#40]; bl a2dp_stop@plt; mov r5, r0; b 8684`) become unreachable; standby still completes (`release_wake_lock`, `mStandby = 1`, return) but no AVDTP SUSPEND fires on the wire.

**Why this site.** AudioFlinger's silence-timeout (~3 s after the music app stops writing samples) calls `A2dpAudioStreamOut::standby` → `standby_l`, and standby_l is the *only* HAL-side path that calls `a2dp_stop`. Removing that one call leaves the AVDTP source stream alive while AudioFlinger thinks the HAL is in standby; the next `write()` after PLAYING resumes pushes samples into the same open AVDTP session. TV-class CTs that aggressively close + reopen their A2DP sink on AVDTP SUSPEND no longer cycle, eliminating the burst-on-resume + playhead-drift symptom (capture `/work/logs/dual-tv-20260509-1538` empirically grounded the design).

**Why not the Java-side `setParameters("A2dpSuspended=...")` approach.** Tried first as the §9.2 fix; reverted in v2.9. Empirical capture showed the AOSP A2DP HAL implements `setSuspended(true)` as a *synchronous* tear-down — calls `a2dp_stop` directly inside the setSuspended path, before silence-timeout standby ever fires. The Java approach ended up triggering exactly the SUSPEND it was trying to prevent. The HAL byte patch short-circuits the only standby path that calls a2dp_stop instead, with no Java-side coupling.

**MD5s:** Stock `0d909a0bcf7972d6e5d69a1704d35d1f` → Output `adbd98afeb5593f1ffe3b90acd0f2536`.

Spec citation: AVDTP 1.3 §8.13 / §8.15 — PAUSED leaves the source stream paused-but-up, SUSPEND is reserved for explicit policy changes.

---

## `patch_y1_apk.py`

Smali-level patches to the music app `com.innioasis.y1*.apk` via apktool. Four patches inside two DEX files (`classes.dex` + `classes2.dex`); the original `META-INF/` signature block is retained verbatim because PackageManager rejects an unsigned APK at boot even for system apps. Output to `output/com.innioasis.y1_<version>-patched.apk`. See the patcher's docstring for full DEX-level analysis (register layouts, instruction offsets, SQL query, etc.).

**Patch A** in `smali_classes2/com/innioasis/music/ArtistsActivity.smali` — `confirm()` artist-tap branch: replaces the in-place `switchSongSortType()` flat-song-list call with an Intent launching `AlbumsActivity` carrying the `artist_key` extra.

**Patch B** in `smali_classes2/com/innioasis/music/AlbumsActivity.smali` — `initView()`: rebuilds the method (`.locals 2 → .locals 8`) to read the `artist_key` extra and, if present, query `SongDao.getSongsByArtistSortByAlbum(artist)` and feed a deduplicated `ArrayList<String>` of album names through `AlbumListAdapter.setAlbums()`. If absent, falls through to the original `getAlbumListBySort()` path so the standalone Albums screen still works.

**Patch C** in `smali/com/innioasis/y1/database/Y1Repository.smali` (field decl): `private final songDao` → `public final songDao` so AlbumsActivity (different package) can `iget-object` it without an `IllegalAccessError`. The Kotlin-generated `access$getSongDao$p` exists but exhibits unreliable `NoSuchMethodError` on this device's old Dalvik (API 17).

**Patch E** in `smali_classes2/com/innioasis/y1/receiver/PlayControllerReceiver.smali` at `:cond_c` — splits the short-press `KEY_PLAY → playOrPause()` branch into six discrete arms per AVRCP 1.3 §4.6.1 (PASS THROUGH command, op codes defined in AV/C Panel Subunit Specification ref [2]; concrete frame example in AVRCP 1.3 §19.3 Appendix D) and ICS Table 8 (Cat 1 op_id status):

| keyCode | Source | Action | ICS Table 8 status |
|---|---|---|---|
| `KEY_PLAY` (85, `KEYCODE_MEDIA_PLAY_PAUSE`) | Legacy `ACTION_MEDIA_BUTTON` Intent (single physical play / pause key) | `playOrPause()V` (toggle) | n/a (toggle is a Y1-side abstraction) |
| `KEYCODE_MEDIA_PLAY` (`0x7e` = 126) | PASSTHROUGH 0x44 → Linux `KEY_PLAYCD` (200) → AVRCP.kl `MEDIA_PLAY` | `play(Z)V` with `bool=true` | item 19 — **M (mandatory)** |
| `KEYCODE_MEDIA_PAUSE` (`0x7f` = 127) | PASSTHROUGH 0x46 → Linux `KEY_PAUSECD` (201) → AVRCP.kl `MEDIA_PLAY_PAUSE` | `pause(IZ)V` with `reason=0x12, flag=true` | item 21 — O (optional) |
| `KEYCODE_MEDIA_STOP` (`0x56` = 86) | PASSTHROUGH 0x45 → Linux `KEY_STOPCD` (166) → AVRCP.kl `MEDIA_STOP` | `stop()V` | item 20 — **M (mandatory)** |
| `KEYCODE_MEDIA_NEXT` (`0x57` = 87) | PASSTHROUGH 0x4B → Linux `KEY_NEXTSONG` (163) → AVRCP.kl `MEDIA_NEXT` | `nextSong()V` | item 26 — O (optional) |
| `KEYCODE_MEDIA_PREVIOUS` (`0x58` = 88) | PASSTHROUGH 0x4C → Linux `KEY_PREVIOUSSONG` (165) → AVRCP.kl `MEDIA_PREVIOUS` | `prevSong()V` | item 27 — O (optional) |

Rationale per `PlayerService` method:
- **`play(Z)V` `bool=true`** — AV/C Panel Subunit Spec PLAY (op_id 0x44): "transition to PLAYING from any state". The boolean controls whether `Static.setPlayValue()` runs after the underlying `IjkMediaPlayer.start()` / `MediaPlayer.start()`. That singleton edge is what propagates the resume to the rest of the app (UI, RemoteControlClient, AudioFocus); without it the player resumes but other components don't see the edge and either fight back to paused or never reflect the change. Kotlin's `play$default(this, dummy, mask=1, null)` (which the music app's own `playOrPause()` resume path uses) overrides the boolean to `1` via the default-args mask, so passing `true` here matches.
- **`pause(IZ)V` `reason=0x12, flag=true`** — AV/C Panel Subunit Spec PAUSE (op_id 0x46): "transition to PAUSED from any state". The reason byte is a diagnostic identifier PlayerService Timber-logs as `executed pause from %d`; existing reasons in stock span `0xc..0x11`, so `0x12` is a fresh tag for "PlayController discrete PASSTHROUGH PAUSE". The boolean flag virtually always resolves to `true` in stock (every observed callsite goes through Kotlin's `pause$default` helper with a mask byte that defaults p2 to true); we pass `true` explicitly.
- **`stop()V`** — AV/C Panel Subunit Spec STOP (op_id 0x45): "transition to STOPPED state". `PlayerService.stop()` is `public final stop()V .locals 4` and calls `IjkMediaPlayer.stop() + reset() + MediaPlayer.stop()` — releasing the media position.
- **`nextSong()V`** — AV/C Panel Subunit Spec FORWARD (op_id 0x4B): discrete next-track command, distinct from FAST_FORWARD (op 0x49). Reached only via Patch H/H′'s propagation path, which bypasses `BasePlayerActivity`'s KeyMap.KEY_RIGHT-arm long-press FF detection that misfires on framework-synthesized repeats.
- **`prevSong()V`** — AV/C Panel Subunit Spec BACKWARD (op_id 0x4C): discrete prev-track command, distinct from REWIND (op 0x48). Symmetric to NEXT.
- **`playOrPause()V`** — Y1's existing toggle; correct semantic for the legacy single-physical-key path.

Patched smali (apktool renames the user-defined labels `:cond_play_pause_toggle / :cond_play_strict / :cond_pause_strict / :cond_stop_strict / :cond_next_strict / :cond_prev_strict` to alphanumeric `:cond_X` on reassembly):

```
:cond_c
[KeyMap.getKEY_PLAY()]
if-eq v2, p1, :cond_play_pause_toggle    # 85  → toggle
const/16 p1, 0x7e
if-eq v2, p1, :cond_play_strict          # 126 → play(true)
const/16 p1, 0x7f
if-eq v2, p1, :cond_pause_strict         # 127 → pause(0x12, true)
const/16 p1, 0x56
if-eq v2, p1, :cond_stop_strict          # 86  → stop()
const/16 p1, 0x57
if-eq v2, p1, :cond_next_strict          # 87  → nextSong()
const/16 p1, 0x58
if-eq v2, p1, :cond_prev_strict          # 88  → prevSong()
goto :cond_e                             # no match → existing fall-through
[six labeled arms, each ending in goto :goto_5]
```

Uses scratch registers `v0` (bool / reason) and `v3` (flag) which are dead at this point in the `.locals 6` `onReceive` method. The next/prev arms only need `p1` (PlayerService) and don't touch `v0` / `v3`. apktool optimizes the no-match `goto :cond_e` to `goto :goto_5` since stock's `:cond_e` sits immediately before `:goto_5` (same control flow).

**Patch H** in `smali/com/innioasis/y1/base/BaseActivity.smali` — propagate unhandled discrete media keys.

`BaseActivity.dispatchKeyEvent(KeyEvent)` is the foreground activity's key entry point for the music app's screens (every Activity in the app extends `BaseActivity`, which extends `AppCompatActivity`). Stock implementation:

```
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 7
    const/4 v0, 0x1
    if-nez p1, :cond_0
    return v0                   # null event → "consumed"
    :cond_0
    invoke-virtual {p1}, KeyEvent;->getAction()I
    move-result v1
    invoke-virtual {p1}, KeyEvent;->getKeyCode()I
    move-result v2
    const/4 v3, 0x3
    ... [if-eq v2, KEY_LEFT / RIGHT / UP / DOWN / MENU / PLAY / ENTER → ... goto :goto_0] ...
    :goto_2
    return v0                   # always returns 1 (TRUE)
.end method
```

`v0` is set to `0x1` at method entry and never reassigned — every KeyEvent the activity receives is consumed regardless of whether it acted. For the keycodes `KeyMap` covers (the device's hardware scroll-wheel: KEY_LEFT / RIGHT / UP / DOWN / MENU / PLAY / ENTER) the activity dispatches directly to `PlayerService` (`playOrPause`, `nextSong`, `prevSong`, etc.) and consuming-with-action is the right behaviour. For discrete media keycodes the activity does NOT recognise — `KEYCODE_MEDIA_PLAY` (`0x7e`), `KEYCODE_MEDIA_PAUSE` (`0x7f`), `KEYCODE_MEDIA_STOP` (`0x56`) — control falls through every if-eq check, reaches `:goto_2 / return v0`, and the events are silently swallowed. They never reach `PhoneFallbackEventHandler.handleMediaKeyEvent` → `AudioManager.dispatchMediaKeyEvent` → `AudioService` → `ACTION_MEDIA_BUTTON` broadcast, so `PlayControllerReceiver` (which has discrete handlers from Patch E) never fires for them.

This is what was empirically blocking AVRCP PASSTHROUGH 0x44 PLAY end-to-end on a strict CT: kernel uinput via `/system/usr/keylayout/AVRCP.kl` correctly maps PASSTHROUGH 0x44 → `KEY_PLAYCD` (200) → `KEYCODE_MEDIA_PLAY` (126), the focused window's `BaseActivity.dispatchKeyEvent` receives it, the activity's `KeyMap` knows nothing about code 126, and the event terminates there.

Patched: insert an early-return block immediately after `move-result v2` gated on `keyCode ∈ {0x7e, 0x7f, 0x56, 0x57, 0x58}`. For any matched keycode, check `KeyEvent.getRepeatCount()`: if `> 0` (framework synthesized repeat — see Patch H″ section below), silently consume by returning TRUE; if `== 0` (genuine first press), return FALSE so the framework continues dispatch via `PhoneFallbackEventHandler` → `AudioManager.dispatchMediaKeyEvent` → `AudioService` → `ACTION_MEDIA_BUTTON` broadcast → `PlayControllerReceiver` discrete arms (Patch E).

```
[stock through `move-result v2`]
const/16 v3, 0x7e
if-eq v2, v3, :patch_h_avrcp_key
const/16 v3, 0x7f
if-eq v2, v3, :patch_h_avrcp_key
const/16 v3, 0x56
if-eq v2, v3, :patch_h_avrcp_key
const/16 v3, 0x57
if-eq v2, v3, :patch_h_avrcp_key
const/16 v3, 0x58
if-eq v2, v3, :patch_h_avrcp_key
goto :patch_h_continue
:patch_h_avrcp_key
invoke-virtual {p1}, KeyEvent;->getRepeatCount()I
move-result v3
if-eqz v3, :patch_h_propagate
return v0                       # repeat: consume silently (v0 is still 1 from method entry)
:patch_h_propagate
const/4 v0, 0x0
return v0                       # first press: let the framework continue dispatch
:patch_h_continue
const/4 v3, 0x3                 # original next instruction
[stock continues unchanged]
```

`v3` is reused as scratch then overwritten by the next instruction (or by the `getRepeatCount()` result); `v0` is set to 0 only on the propagate path which immediately returns. The patched method semantically becomes "for AVRCP-derived keycodes, propagate the first press to the framework media-button path and silently swallow framework-synthesized repeats; for everything else, behave exactly as stock."

**Keycode set: `0x7e MEDIA_PLAY`, `0x7f MEDIA_PAUSE`, `0x56 MEDIA_STOP`, `0x57 MEDIA_NEXT`, `0x58 MEDIA_PREVIOUS`.** Note: AVRCP.kl maps PASSTHROUGH 0x46 PAUSE → `KEY_PAUSECD` (201) → `KEYCODE_MEDIA_PLAY_PAUSE` (85), NOT MEDIA_PAUSE (127), so 0x7f comes from CTs that emit a discrete pause keycode (some Android-side AVRCP profile transformers do, on top of standard AV/C). 0x57 / 0x58 are added even though the activity's KeyMap.KEY_RIGHT / KEY_LEFT entries match them (87 / 88) because the existing `BasePlayerActivity` arms conflate AVRCP NEXT (op 0x4B) with hardware-wheel-RIGHT-LONG-press FF/scrub. AVRCP 1.3 §4.6.1 separates op 0x4B (NEXT) from op 0x49 (FAST_FORWARD); we honour that separation by routing 0x57 to the dedicated `nextSong()` arm in Patch E.

**Side effect on hardware NEXT/PREV touch buttons (event2 mtk-tpd also emits keycodes 87/88): holding such a button no longer enters FF/RW; it produces a single nextSong()/prevSong() per tap. Matches the AVRCP-spec semantic but diverges from prior stock behaviour. The hardware scroll wheel uses different keycodes (KeyMap.KEY_UP=21 DPAD_LEFT, KEY_DOWN=22 DPAD_RIGHT) and is unaffected.**

**Upstream-compatibility note.** This patch lives entirely inside the music app's APK. Other foreground apps installable on the device (e.g. Rockbox) extend `AppCompatActivity` directly and do not inherit from `com.innioasis.y1.base.BaseActivity`, so their AVRCP key handling is unaffected. The keylayout `/system/usr/keylayout/AVRCP.kl` stays stock — the kernel→`KeyEvent` mapping continues to deliver `KEYCODE_MEDIA_PLAY` (126) for op_id 0x44, which is the spec-correct keycode for any app that handles standard Android media keys.

**Patch H′** in `smali_classes2/com/innioasis/y1/base/BasePlayerActivity.smali` — same propagation, applied to the music-player superclass.

`MusicPlayerActivity` (and any other player-screen activity) extends `BasePlayerActivity`, which overrides `dispatchKeyEvent` itself and never delegates up the chain. Stock implementation:

```
.method public dispatchKeyEvent(Landroid/view/KeyEvent;)Z
    .locals 2
    invoke-static {p1}, Intrinsics;->checkNotNull(Object;)V
    invoke-virtual {p1}, KeyEvent;->getAction()I
    move-result v0
    if-nez v0, :cond_2
    [DOWN path: repeatCount==8 + KeyMap match → onKeyLongPress; else → onKeyDown]
    :cond_2
    invoke-virtual {p1}, KeyEvent;->getKeyCode()I
    move-result v0
    invoke-virtual {p0, v0, p1}, BasePlayerActivity;->onKeyUp(I, KeyEvent;)Z
    :goto_0
    const/4 p1, 0x1
    return p1                       # always TRUE — never delegates to BaseActivity
.end method
```

Because this override is in the chain when the player screen is foreground, `BaseActivity.dispatchKeyEvent` (and Patch H) is unreachable. `BasePlayerActivity.onKeyUp` matches keycodes only against `KeyMap` entries — `KEY_LEFT=88` (MEDIA_PREVIOUS), `KEY_RIGHT=87` (MEDIA_NEXT), `KEY_MENU=4` (BACK), `KEY_ENTER=66` (DPAD_CENTER), `KEY_PLAY=85` (MEDIA_PLAY_PAUSE). The discrete media keycodes (126 / 127 / 86) match none, fall through `:cond_8 / :goto_1`, and `return v1=1` silently consumes them.

Empirical confirmation: TV postflash logcat 2026-05-09 shows zero `pause from 18` reasons (Patch E's discrete-pause tag) across an entire AVRCP test session even though Patch H was deployed — every pause traced to internal music-app sources or `playOrPause` toggle, every `play(Z)` traced to `BasePlayerActivity.onKeyUp:195` (the long-press-release-from-FF cleanup path on `KEY_RIGHT`).

Patched: insert the same five-keycode early-return block at the top of `BasePlayerActivity.dispatchKeyEvent`, with the same `repeatCount > 0 → silent consume` filter as Patch H, BEFORE the `Intrinsics.checkNotNull` call (defensive — matches Patch H's null-safe ordering in `BaseActivity`):

```
[method header + .locals 2]
invoke-virtual {p1}, KeyEvent;->getKeyCode()I
move-result v0
const/16 v1, 0x7e
if-eq v0, v1, :patch_h2_avrcp_key
const/16 v1, 0x7f
if-eq v0, v1, :patch_h2_avrcp_key
const/16 v1, 0x56
if-eq v0, v1, :patch_h2_avrcp_key
const/16 v1, 0x57
if-eq v0, v1, :patch_h2_avrcp_key
const/16 v1, 0x58
if-eq v0, v1, :patch_h2_avrcp_key
goto :patch_h2_continue
:patch_h2_avrcp_key
invoke-virtual {p1}, KeyEvent;->getRepeatCount()I
move-result v0
if-eqz v0, :patch_h2_propagate
const/4 v0, 0x1
return v0                       # repeat: consume silently
:patch_h2_propagate
const/4 v0, 0x0
return v0                       # first press: let the framework continue dispatch
:patch_h2_continue
[stock method body resumes]
```

`v0` and `v1` are the existing scratch locals (`.locals 2` covers both). Returning false from `BasePlayerActivity.dispatchKeyEvent` causes the framework to fall through to `PhoneFallbackEventHandler` → `AudioService` → `ACTION_MEDIA_BUTTON` broadcast, where `PlayControllerReceiver`'s Patch E discrete arms then fire. Returning true on a repeat is the no-action consume path.

**Patch H″** — framework-synthetic-repeat filter, paired with NEXT/PREV keycode propagation. Logically a single change embedded in both Patch H and Patch H′.

The `repeatCount > 0 → silent consume` branch and the addition of `0x57` / `0x58` to the propagated keycode set are both Patch H″. Background:

Android 4.2.2's `InputDispatcher::synthesizeKeyRepeatLocked` synthesizes `KeyEvent` repeats above the kernel's `evdev` layer for any key still in DOWN state without an UP. The kernel's `EV_REP` softrepeat is patched off by U1 (`libextavrcp_jni.so:0x74e8`), but the framework synthesizer is independent of `EV_REP` and keeps generating events with climbing `repeatCount` at ~50 ms intervals (after a ~400 ms initial delay). For AVRCP-derived keycodes that path triggers `BasePlayerActivity.onKeyLongPress` at `repeatCount == 8` → music app enters FF/RW mode. Empirical proof from the 2026-05-09 TV logcat: `getevent` shows ONE `KEY_NEXTSONG DOWN` at boottime 304186.40, no further events for 24 s, then ONE `UP` — yet `WindowManager.interceptKeyTi` shows `keyCode=87 down=true` firing repeatedly with `repeatCount` climbing 2 → 70+ over the same 24 s window. Same pattern for held `KEY_PLAYCD` (`repeatCount` 0 → 30+ at 50 ms).

H″ collapses any held AVRCP keycode to a single one-shot action per genuine press by checking `KeyEvent.getRepeatCount()` after the keycode match: if `> 0`, return TRUE (silent consume — neither propagate nor dispatch to onKeyDown / Up); if `== 0`, propagate normally. Applies to both `BaseActivity` (Patch H location) and `BasePlayerActivity` (Patch H′ location). Closes the "stuck fast-forwarding" symptom on TV CTs that drop PASSTHROUGH RELEASE under subscribe load.

**Apktool reassembly:** `apktool d --no-res` decode → smali edits → `apktool b` reassemble (the post-DEX aapt step fails because resources weren't decoded, but DEX is already built by then; the script intentionally ignores the exit code). Patched DEX bytes are dropped into a copy of the original APK with `META-INF/` preserved.

**Deployment:** `adb root && adb remount && adb push <apk> /system/app/com.innioasis.y1/com.innioasis.y1.apk && adb reboot`. Do **not** use `adb install` — PackageManager rejects re-signed system app APKs.

---

## `src/su/` (root, v1.8.0+)

Source for a minimal setuid-root `su` binary installed at `/system/xbin/su` by the bash's `--root` flag. Replaces the historical adbd byte patches that broke ADB protocol on hardware (preserved diagnosis in [`INVESTIGATION.md`](INVESTIGATION.md) §"adbd Root Patches (H1 / H2 / H3)").

- **`src/su/su.c`** — direct ARM-EABI syscall implementation, no libc dependency. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", ...)`. Three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), `su <prog> [args...]` (exec-passthrough).
- **`src/su/start.S`** — ~10-line ARM Thumb-2 entry stub; extracts argc/argv/envp from the ELF process-start stack layout, calls `main`, exits via `__NR_exit`.
- **`src/su/Makefile`** — cross-compile via `arm-linux-gnu-gcc`. `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft`; output ~900 bytes, statically linked, no `NEEDED` entries.

**No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported; no manager APK; no whitelist. Trade-off: any process that can exec `/system/xbin/su` becomes root, which is acceptable for a single-user research device but not for a consumer ROM.

**Build:** `cd src/su && make` produces `src/su/build/su`. The bash references this prebuilt path; if missing, `--root` exits with a clear error pointing at `make`.

**Deploy:** the bash's `--root` flag does `install -m 06755 -o root -g root src/su/build/su /system/xbin/su` against the mounted system.img. Post-flash: `adb shell /system/xbin/su -c "id"` → `uid=0(root)`.
