# Patch Reference

Byte-level detail for every patch shipped (or attempted) by this repo. Patches are referenced by short IDs throughout `INVESTIGATION.md`, `CHANGELOG.md`, and the patcher source files.

## Patch ID Legend

| ID(s) | Binary | Site / effect |
|---|---|---|
| **V1, V2** | `mtkbt` | AVRCP `0x00 → 0x03` (1.0 → 1.3) at `0x0eba58`; AVCTP `0x00 → 0x02` (1.0 → 1.2) at `0x0eba6d` — the served Group D ProfileDescList / ProtocolDescList. |
| **S1** | `mtkbt` | Replace the `0x0311` SupportedFeatures attribute slot at `0x0f97ec` with a `0x0100` ServiceName entry pointing at the existing "Advanced Audio" SDP string. Pixel-1.3-shape SDP record. |
| **P1** | `mtkbt` | Force fn `0x144bc` op_code dispatch to PASSTHROUGH branch at `0x14528` (`cmp r3, #0x30 → b.n`). Routes inbound VENDOR_DEPENDENT frames into the `bl 0x10404` msg-519 emit path so the JNI trampolines see them. |
| **R1, T1, T2 stub, extended_T2, T4, T5, T_charset, T_battery, T6, T8, T9, U1** | `libextavrcp_jni.so` | Trampoline chain in `_Z17saveRegEventSeqIdhh` + LOAD #1 page-padding extension + iter23 U1 NOP. R1 redirects size!=3 dispatch to T1; T1 handles GetCapabilities; T2 stub bridges to extended_T2 (RegisterNotification(TRACK_CHANGED)) which falls through to T4. T4's pre-check dispatches PDU 0x20 → main GetElementAttributes body, 0x17 → T_charset, 0x18 → T_battery, 0x30 → T6, else fall through to T8 (RegisterNotification dispatcher for non-0x02 events); unhandled paths land at the original "unknow indication". T5 (iter17a) is invoked via the patched `notificationTrackChangedNative` and emits proactive CHANGED on Y1MediaBridge track changes. T9 (iter22b) is invoked via the patched `notificationPlayStatusChangedNative` and emits proactive CHANGED on Y1MediaBridge play/pause edges. T_charset/T_battery (iter19a) ack the spec-mandated CT→TG informational PDUs (0x17 / 0x18). T6 (iter20a + iter22d live position) handles GetPlayStatus (PDU 0x30). T8 (iter20b) is the RegisterNotification dispatcher for events ≠ 0x02 (events 0x01/0x03–0x07, INTERIM-only). U1 (iter23) is a separate 4-byte NOP at 0x74e8 disabling kernel auto-repeat on the AVRCP uinput device. See [`ARCHITECTURE.md`](ARCHITECTURE.md). |
| **U1** | `libextavrcp_jni.so` | iter23 — at file `0x74e8`: NOP the third `blx ioctl@plt` in the `avrcp_input_init` (real init at `0x73c8`, called from `BluetoothAvrcpService_activate_1req` and `wakeupListenerNative`). That ioctl is `UI_SET_EVBIT(EV_REP)` — claiming EV_REP causes the kernel to enable `input_enable_softrepeat()` for the AVRCP `/dev/input/event4` virtual keyboard with default `REP_DELAY=250ms, REP_PERIOD=33ms`. Confirmed cause of the haptic-loop symptom: a single PASSTHROUGH PRESS whose RELEASE is dropped → `KEY_NEXTSONG DOWN` injected → kernel auto-fires `KEY_NEXTSONG REPEAT` at 25 Hz indefinitely → each REPEAT propagates to InputDispatcher → media-key broadcast → haptic feedback. Without EV_REP in `dev->evbit`, the kernel never schedules the soft-repeat timer and only the actual PRESS frames the CT sends generate events. Other three UI_SET_EVBIT calls (EV_KEY at `0x74d4`, EV_REL vendor-typo at `0x74e0`, EV_SYN at `0x74f2`) are left intact. Spec-correct per AVRCP 1.3 §4.6.1 (PASS THROUGH command — defined in AV/C Panel Subunit Specification, ref [2] of AVRCP 1.3): CT is responsible for periodic re-send during held button; TG forwards one event per frame. Replaced iter21 (Patch D), which capped the in-app seek loop at 5 s wall-clock — that was a band-aid for the kernel auto-repeat behavior that iter24 reverted once U1 fixed the root cause; iter21 also unhelpfully bounded local hardware-button hold-FF/RW. |
| **F1** | `MtkBt.odex` | At `0x3e0ea`: `getPreferVersion()` returns `14` (AVRCP 1.4) instead of `10` (BlueAngel internal code for AVRCP 1.3). |
| **F2** | `MtkBt.odex` | At `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false`. Fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts. |
| **iter17a (odex)** | `MtkBt.odex` | At `0x03c530`: NOP the `if-eqz v5, :cond_184` cardinality gate in `BTAvrcpMusicAdapter.handleKeyMessage` TRACK_CHANGED case so `notificationTrackChangedNative` fires on every Y1MediaBridge track-change broadcast. Pairs with the libextavrcp_jni.so iter17a/T5 trampoline. |
| **iter22b (odex)** | `MtkBt.odex` | At `0x03c4fe`: NOP the `if-eqz v5, :cond_184` cardinality gate in `BTAvrcpMusicAdapter.handleKeyMessage` PLAYBACK_STATUS_CHANGED case (sswitch_18a, event 0x01) so `notificationPlayStatusChangedNative` fires on every Y1MediaBridge `playstatechanged` broadcast. Same idiom as iter17a but for event 0x01 instead of 0x02. Pairs with the libextavrcp_jni.so iter22b/T9 trampoline. |
| **T9** | `libextavrcp_jni.so` | iter22b proactive CHANGED for event 0x01 PLAYBACK_STATUS_CHANGED. Triggered via patched `notificationPlayStatusChangedNative` first instruction at file offset 0x3c88 (`stmdb sp!, {r0, r1, r4, r5, r6, r7, r8, lr}` overwritten with `b.w T9`). T5-shaped: reads y1-track-info[792] (current play_status), compares against y1-trampoline-state[9] (last_play_status — was pad), emits CHANGED via PLT 0x339c on edge, updates state. Closes the AVRCP 1.3 §5.4.2 spec gap left by iter20b's INTERIM-only T8. |
| **G1, G2** | `mtkbt` | **Attempted and reverted 2026-05-02 / 2026-05-03.** Diagnostic `__xlog_buf_printf → __android_log_print` redirect (Thumb thunk at `0x675c0`, ARM PLT at `0xb408`). Crashed mtkbt at NULL fmt; even with NULL guard, BT framework couldn't enable. Path closed without root or daemon-side tooling. |
| ~~**H1, H2, H3**~~ | `/sbin/adbd` (in `boot.img` ramdisk) | **Tried 2026-05-03; reverted (caused "device offline").** Both attempted approaches (NOP the three `blx setgroups/setgid/setuid` calls; change their argument values from 2000/11 to 0) caused adbd-at-uid-0 to start and enumerate over USB but fail the ADB protocol handshake. `--root` removed from the bash in v1.7.0; superseded in v1.8.0 by the `su` install approach. |
| **su** | `/system/xbin/su` (new file) | **Reintroduced root path, v1.8.0.** Ship a minimal setuid-root `su` binary (06755, root:root) to obtain root via `adb shell /system/xbin/su` without touching `/sbin/adbd`. Built from `src/su/su.c` + `src/su/start.S` via `arm-linux-gnu-gcc`: ~900-byte direct-syscall ARM-EABI ELF, no libc, no manager APK. |

> **Removed in v2.1.0:** the legacy AVRCP 1.4 byte-patch attempt (B1-E8 in `mtkbt`, C2a/b/C3a/b in `libextavrcp_jni.so`, C4 in `libextavrcp.so`) that regressed stock PASSTHROUGH without delivering metadata. Superseded by the V1/V2/S1/P1 + trampoline-chain pipeline above. See `CHANGELOG.md` for the full rationale and the [v2.0.0 git tag](../CHANGELOG.md) if you need the old byte-level reference.

---

## `patch_mtkbt.py`

**Four patches** against stock mtkbt: three SDP-shape patches that get a CT to send AVRCP 1.3+ COMMANDs against the served TG record (per AVRCP 1.3 §6 Service Discovery Interoperability Requirements + ESR07 §2.1 / Erratum 4969 clarifying AVCTP version values), plus one binary patch that routes the inbound frame to the JNI msg 519 emit path (which the libextavrcp_jni.so trampoline chain consumes).

- **V1** `0x0eba58`: `0x00` → `0x03` — AVRCP 1.0 → 1.3 LSB in served Group D ProfileDescList.
- **V2** `0x0eba6d`: `0x00` → `0x02` — AVCTP 1.0 → 1.2 LSB in served Group D ProtocolDescList.
- **S1** `0x0f97ec` (12 bytes): replace the `0x0311` SupportedFeatures attribute table entry with a `0x0100` ServiceName entry pointing at the existing "Advanced Audio" SDP-encoded string at file offset `0x0eb9ce` (re-used from mtkbt's A2DP record; peers don't validate ServiceName content, only its presence).
  - Before: `11 03 03 00 59 ba 0e 00 00 00 00 00` (attr=`0x0311`, len=3, ptr=`0x0eba59` → `uint16 0x0001`)
  - After:  `00 01 11 00 ce b9 0e 00 00 00 00 00` (attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` → `25 0f "Advanced Audio\0"`)
- **P1** `0x144e8` (2 bytes): replace the first comparison in fn `0x144bc`'s op_code dispatch with an unconditional branch to the PASSTHROUGH-emit branch at `0x14528`. That branch ends with `bl 0x10404`, which is the function that emits msg 519 (CMD_FRAME_IND) to the JNI socket — empirically traced via gdbserver across PASSTHROUGH and VENDOR_DEPENDENT inbound frames. PASSTHROUGH frames already took this path; VENDOR_DEPENDENT frames previously took the `bcc 0x1454a` branch (which only logs via `bl 0x11374`). Patching the cmp to a 2-byte unconditional `b.n` makes all AV/C frames flow through the emit path.
  - Before: `30 2b` (Thumb `cmp r3, #0x30` = `0x2b30`)
  - After:  `1e e0` (Thumb `b.n 0x14528`, jumps +0x3c bytes from PC at `0x144ec`)

**Cost of S1:** the served record loses the `0x0311` SupportedFeatures attribute. Empirically Pixel-1.3 advertises features `0x0001`; CTs in our test matrix engage with our record without `0x0311` too (the iter1 `--avrcp` capture confirmed VENDOR_DEPENDENT GetCapabilities reaches the TG).

**Cost of P1:** the bl `0x10404` path was designed for PASSTHROUGH frames. VENDOR_DEPENDENT frame bytes will be interpreted in PASSTHROUGH-shaped fields, so the response mtkbt sends back to the peer may be malformed. Worst case is mtkbt emits a NOT_IMPLEMENTED reply (which is what currently happens already, so no regression). Best case is msg 519 fires with the inbound frame bytes preserved, JNI passes them to Y1MediaBridge via the `IBTAvrcpMusic` Binder, and Y1MediaBridge builds the appropriate AVRCP RESPONSE on its outbound path.

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `a37d56c91beb00b021c55f7324f2cc09`.

---

## `patch_libextavrcp_jni.py`

Implements the trampoline chain in `_Z17saveRegEventSeqIdhh` (file body 0x5f0c) and the LOAD #1 page-padding extension that lets the JNI synthesise AVRCP 1.3 responses directly. Bypasses both the JNI's "unknow indication" default-reject (the original size!=8 branch) and the JNI->Java callback path (the size==8 branch), since Java-side AVRCP TG bookkeeping on this firmware is a no-op stub.

**R1 — redirect** at `0x6538` (4 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `40 d1 09 25` | `bne.n 0x65bc` + `movs r5, #9` |
| after  | `00 f0 e6 fe` | `bl.w 0x7308` |

Destroys the size==8 path's leading `movs r5, #9`. Acceptable because mtkbt-as-1.0 never legitimately produces size==8 frames on this device, and size!=8 was already routed to "unknow indication" — the trampoline still routes it there via its `fall_through` arm.

**T1 — trampoline** at `0x7308` (40 bytes), overwriting the unused JNI debug method `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa` (~44 byte slot):

```
0x7308: 9d f8 7e 01     ldrb.w r0, [sp, #382]    ; PDU byte (AV/C body+4)
0x730c: 10 28           cmp r0, #0x10            ; GetCapabilities?
0x730e: 0d d1           bne.n 0x732c             ; no -> fall_through
0x7310: 04 a3           adr r3, 0x7324           ; events_data ptr
0x7312: 05 f1 08 00     add.w r0, r5, #8         ; r0 = conn buffer (r5 from prologue)
0x7316: 00 21           movs r1, #0
0x7318: 05 22           movs r2, #5              ; events count
0x731a: fc f7 60 e9     blx 0x35dc               ; PLT->btmtk_avrcp_send_get_capabilities_rsp
0x731e: ff f7 04 bf     b.w 0x712a               ; mov r9,#1; canary; epilogue
0x7322: 00 bf           nop
0x7324: 01 02 09 0a 0b 00 00 00                  ; supported events:
                                                 ;   0x01 PLAYBACK_STATUS_CHANGED
                                                 ;   0x02 TRACK_CHANGED
                                                 ;   0x09 NOW_PLAYING_CONTENT_CHANGED
                                                 ;   0x0a AVAILABLE_PLAYERS_CHANGED
                                                 ;   0x0b ADDRESSED_PLAYER_CHANGED
0x732c: ff f7 46 b9     b.w 0x65bc               ; fall_through (original "unknow")
```

**T2 — classInitNative stub + RegisterNotification(TRACK_CHANGED) trampoline** at `0x72d0` (48 bytes), overwriting the JNI debug method `classInitNative` (which is purely two `__android_log_print` calls + `return 0` — safe to stub). T1's fall-through arm at 0x732c bridges here via `b.w 0x72d4`.

```
0x72d0: 00 20 70 47     classInitNative stub (movs r0, #0; bx lr)
                        — preserves the "return 0" contract; loses debug logs
; T2 stage 2 entry
0x72d4: 31 28           cmp r0, #0x31           ; PDU still in r0 from T1
0x72d6: 0d d1           bne.n 0x72f4            ; not RegisterNotification
0x72d8: 9d f8 82 01     ldrb.w r0, [sp, #386]   ; event_id (clobber PDU)
0x72dc: 02 28           cmp r0, #0x02           ; EVENT_TRACK_CHANGED?
0x72de: 09 d1           bne.n 0x72f4            ; not TRACK_CHANGED
0x72e0: 05 f1 08 00     add.w r0, r5, #8        ; conn buffer
0x72e4: 9d f8 70 11     ldrb.w r1, [sp, #368]   ; transId
0x72e8: 0f 22           movs r2, #0x0f          ; INTERIM reasonCode
0x72ea: 03 a3           adr r3, 0x72f8          ; track_id_data ptr
0x72ec: fc f7 4a e8     blx 0x3384              ; PLT → …reg_notievent_track_changed_rsp
0x72f0: ff f7 1b bf     b.w 0x712a              ; epilogue
0x72f4: ff f7 62 b9     b.w 0x65bc              ; unknown PDU/event → "unknow indication"
0x72f8: ff ff ff ff ff ff ff ff                ; track_id = 0xFFFFFFFFFFFFFFFF
                                                ;   ("Identifier not allocated, metadata not available")
```

PLT 0x3384 → GOT 0xcf0c → `btmtk_avrcp_send_reg_notievent_track_changed_rsp`. Argument shape (r0=conn, r1=transId, r2=reasonCode, r3=ptr_to_8byte_track_id) verified via cross-reference with `notificationTrackChangedNative` at 0x3bc0 — see `add.w r0, r8, #8 / uxtb.w r1, r9 / uxtb.w r2, sl / add r3, sp, #12 / blx 3384` at 0x3c3c.

EVENT_PLAYBACK_STATUS_CHANGED (0x01), NOW_PLAYING_CONTENT_CHANGED (0x09), AVAILABLE_PLAYERS_CHANGED (0x0a), and ADDRESSED_PLAYER_CHANGED (0x0b) all fall through to the unknown branch (b.w 0x65bc) → mtkbt sends NOT_IMPLEMENTED. CTs may retry per their own policy (some do ~4× then give up, others poll forever). Metadata handshake (TRACK_CHANGED is what gates GetElementAttributes) still proceeds with just T2 in place. T8 (iter20b) and T9 (iter22b) later add coverage for events 0x01/0x03/0x04/0x05/0x06/0x07.

**Why this works:**
- At the redirect site, the function has already executed `add.w r0, r5, #8` (at 0x6528), so `r0 = conn buffer` is on register exit. The trampoline reads PDU into r0 (clobbering it) for the cmp, then re-derives `r0 = r5+8` before the response-builder call.
- `r5` is preserved across the `bl.w` because it's callee-saved in AAPCS and the trampoline doesn't push/pop it.
- 0x712a sets `r9=1` (the function's return value), runs the stack-canary check at 0x712e, and falls into the function epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).

**Hardware verification:**
- T1 alone (iter5, 2026-05-05): elicited 30-byte msg=522 outbound (consistent with a real GetCapabilities response); the test CT progressed and sent 4 size:13 RegisterNotification frames at 2-second intervals — confirming T1 fires correctly.
- T2: pending hardware test.

**History:** J1 (cmp.w lr,#8 → cmp.w lr,#9 at 0x6526) was tried 2026-05-05 (iter4) and rolled back — it routed our size-9 frames into the size-8 PASSTHROUGH dispatch, calling `btmtk_avrcp_send_pass_through_rsp` with VENDOR_DEPENDENT-shaped data and dispatching as a fake `key=1 isPress=0` PASSTHROUGH event. See [INVESTIGATION.md](INVESTIGATION.md) Trace #12.

**T4 — stub at extended-LOAD-segment vaddr 0xac54** (12 bytes). The original libextavrcp_jni.so has a LOAD segment ending at vaddr/file 0xac54, with 4276 zero-padding bytes before LOAD #2 starts at file 0xbc08. The patcher writes T4 code into this padding and bumps LOAD #1's `FileSiz`/`MemSiz` from 0xac54 to 0xac60 so the kernel maps the bytes as R+E at runtime. T2's "unknown" branch at 0x72f4 is rewritten to `b.w 0xac54` instead of `b.w 0x65bc`.

```
0xac54: bd f8 76 e1     ldrh.w lr, [sp, #374]   ; restore lr = SIZE
0xac58: 05 f1 08 00     add.w  r0, r5, #8        ; restore r0 = conn buffer
0xac5c: fb f7 ae bc     b.w    0x65bc            ; original "unknow indication"
```

**Why these two restores:** the original `bne 0x65bc` site at file 0x6538 was reached with two specific values that 0x65bc relies on:
- `r0 = r5+8` (set at 0x6528, two instructions before the bne)
- `lr = halfword at sp+374` (= SIZE; loaded at 0x644e, before all the cmp/bne dispatches)

The 0x65bc path then runs `str.w lr, [sp, #12]` to pass SIZE to `btmtk_avrcp_send_pass_through_rsp` as a stack arg. Our T1/T2 trampolines clobber r0 (with PDU/event_id) AND the `bl.w 0x7308` at 0x6538 clobbers lr (to 0x653c, the bl return address). Without both restores, pass_through_rsp gets bogus args and silently fails to emit msg=520 NOT_IMPLEMENTED.

Iter5/iter6 confirmed the symptom: the test CT retried unhandled size:13/size:45 frames forever because mtkbt was emitting nothing back. Iter7 (only the r0 restore, no lr restore) tested the ELF-extension infrastructure but still didn't generate msg=520 — the lr clobber was the second half of the bug. Iter8 (this version) restores both.

**Iter17a: proactive CHANGED on track change via Java→JNI hook.** Builds on iter16's reactive T4/extended_T2 trampolines and adds a third trampoline (T5) that fires asynchronously when Y1MediaBridge writes a new track_id, regardless of CT GetElementAttributes polling rate. Y1MediaBridge → `com.android.music.metachanged` → MtkBt's `BTAvrcpMusicAdapter.passNotifyMsg(2, 0)` → `handleKeyMessage` (with the cardinality `if-eqz` NOPed by `patch_mtkbt_odex.py`'s iter17a entry) → `notificationTrackChangedNative` → patched first instruction `b.w T5` lands in our LOAD #1 trampoline → T5 reads `y1-track-info` and `y1-trampoline-state`, compares track_ids, and on change calls `track_changed_rsp` via PLT 0x3384 with `reason=CHANGED`, `transId=state[8]`, `track_id=&sentinel_ffx8`. T5 obtains the AVRCP per-conn struct by re-using the JNI helper at 0x36c0 the stock native already called for the same purpose.

**Iter17b: T4 multi-attribute single-frame fix.** Hardware test of iter17a showed the protocol/proactive layer working but the test CT rendering metadata field-by-field with visible flicker — Title appearing intermittently while Artist/Album swapped in/out. Diagnosed from logcat msg-id ratio (1299 msg=540 ÷ 433 GetElementAttributes ≈ 3 outbound frames per inbound query) as a regression of the iter12 bug that iter13 had originally fixed: T4's three calls to `btmtk_avrcp_send_get_element_attributes_rsp` were passing `arg2=transId, arg3=0`, which takes the function's legacy `arg3==0 → EMIT each call` path. Restored iter13 semantics: `arg2 = attribute index (0,1,2)`, `arg3 = 3` so only the third call (where `arg2+1 == arg3`) emits, packing all three attributes into one frame. Trampoline blob shrinks 768 → 760 B; LOAD #1 ends at 0xaf4c.

**Iter19a: Phase A0 — Inform PDUs + TRACK_CHANGED wire-shape correctness.** Two coupled changes that together close the strict-CT failure pattern (see [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the empirical context) and reduce the gap to AVRCP 1.3 spec compliance per [`AVRCP13-COMPLIANCE-PLAN.md`](AVRCP13-COMPLIANCE-PLAN.md):

1. **Two new trampolines** for the spec's CT→TG informational PDU pair:
   - `T_charset` for **PDU 0x17 InformDisplayableCharacterSet** → calls `inform_charsetset_rsp` via PLT 0x3588 with `arg1=0` (success). 14 B.
   - `T_battery` for **PDU 0x18 InformBatteryStatusOfCT** → calls `battery_status_rsp` via PLT 0x357c with `arg1=0`. 14 B.
   - Both reached via additional dispatch in T4's pre-check (`bne+b.w` to T_charset/T_battery before fall-through to "unknow indication"); the dispatch costs 12 B.
   - Strict CTs send PDU 0x17 InformDisplayableCharacterSet (UTF-8) once at connect per AVRCP 1.3 §5.2.7; pre-iter19a we NACKed with msg=520 NOT_IMPLEMENTED, which strict CTs treat as the TG distrusting subsequent metadata (no re-fetch after the initial GetElementAttributes). Closing this gap is the §5.2.7-compliant ack response.

2. **TRACK_CHANGED wire-shape correctness fix** in extended_T2 + T4 (CHANGED on track edge) + T5 (proactive CHANGED). All three sites previously passed `r1=transId` to `track_changed_rsp`. Disassembly of the response builder at libextavrcp.so:0x2458 (and confirmed across all `reg_notievent_*_rsp` builders in the same family) shows it dispatches on r1: `r1==0` writes the spec-correct event payload (reasonCode + event_id + track_id memcpy per §6.7.1); `r1!=0` writes a reject-shape frame omitting the event payload. We had been hitting the reject path on every TRACK_CHANGED notification — CTs that poll metadata regardless masked the bug, but strict CTs that depend on the CHANGED edge saw nothing. Fix: `movs r1, #0` in all three sites. Saves 6 B (4-byte `ldrb.w` → 2-byte `movs_imm8`) but the trampoline grows because we add T_charset+T_battery+dispatch.

Trampoline blob 760 → 800 B (cumulative); LOAD #1 ends at 0xaf74.

**Iter19b: drop the iter16 0xFF×8 sentinel; pass real synthetic track_id (from `y1-track-info[0..7]`) on the wire in INTERIM and CHANGED.** iter19a hardware test on a strict-CT class (capture path in [`INVESTIGATION.md`](INVESTIGATION.md)) confirmed the wire-shape fix worked for the **first** CHANGED edge (the CT sent GetElementAttributes 197ms after the first T5-driven CHANGED) but not for subsequent track changes — the strict CT registered TRACK_CHANGED every 3s but never re-fetched. Diagnosis: this CT class compares the CHANGED's `track_id` against its cached value and only re-fetches when they differ (the alternative-mode reading of AVRCP 1.3 §5.4.2 Table 5.30's `Identifier` field — when the TG provides a stable identity, the CT keys its metadata cache against it). With every CHANGED carrying the same `0xFF×8` sentinel, the CT sees "same identity, no real change" and ignores. iter19b switches the wire-level `track_id` argument to `track_changed_rsp` from `&sentinel_ffx8` to the real 8 B from `y1-track-info[0..7]` (= `Y1MediaBridge.mCurrentAudioId`, which iter18d made unique per track via `path.hashCode() | 0x100000000L`). Three sites: extended_T2 INTERIM, T4 CHANGED-on-edge, T5 proactive CHANGED. Each `adr_w(r3, "sentinel_ffx8")` (4 B) becomes `add_sp_imm(r3, <local-offset>)` (2 B) pointing at the relevant on-stack track_id buffer.

**Why this is safe despite iter15's deadlock with permissive CTs:** iter15 did the same thing (real track_id on wire) and deadlocked permissive-CT class behavior because once a CT received a real track_id in INTERIM it switched to "stable identity, refresh on CHANGED" mode, but T4 was reactive only — the CT waited for a CHANGED edge that never came (this CT class wouldn't poll on its own). T5 (iter17a) makes CHANGED proactive: every Y1 track change fires a CHANGED on the wire regardless of whether the CT is polling. The deadlock pre-condition is gone, so we can deliver real track_ids.

Trampoline blob shrinks 800 → 792 B (saved 8 B from the `adr_w → add_sp_imm` instruction-size delta; sentinel data block kept in place harmlessly for future use). LOAD #1 ends at 0xaf6c.

**Program-header surgery:** the patcher updates LOAD #1's program header at file 0x54:
- offset+16 (`p_filesz`): 0xac54 → 0xaf6c (iter19b; was 0xaf74 in iter19a, 0xaf4c in iter17b)
- offset+20 (`p_memsz`):  0xac54 → 0xaf6c (iter19b)

No other section/segment offsets shift, so `.dynsym`/`.text`/`.rodata`/`.dynamic`/`.rel.plt` etc. all stay byte-identical. The dynamic linker just maps slightly more of the file into the R+E segment.

Compliance scorecard unchanged from iter19a (5 mandatory PDUs handled, 5 spec-correct) — this is a behavior correctness change for an existing PDU, not a new PDU. But it's a meaningful win for strict-CT compatibility.

**Iter19d: revert iter19b — restore the iter16 0xFF×8 sentinel.** Hardware test against a CT class subscribing at high frequency showed real track_ids in INTERIM triggered the CT to enter a tight RegisterNotification subscribe storm at ~90 Hz (3401 inbound `size:13` over 38 seconds, sustained ~7 ms inter-frame). Our INTERIM responses kept lockstep with the flood. AVCTP saturation caused PASSTHROUGH release frames to drop, producing held-key fast-forward symptoms via the music-app dispatchKeyEvent path (`seekTo()` repeatedly invoked at ~32× speed; same root cause as held-key haptic feedback firing per frame). Revert the iter19b changes at all three call sites (`extended_T2` INTERIM, `T4` CHANGED-on-edge, `T5` proactive CHANGED) — back to `adr_w(r3, "sentinel_ffx8")` (4 B) from iter19b's `add_sp_imm(r3, <local-offset>)` (2 B). Trampoline blob grows 792 → 800 B (back to iter19a size; the iter19b 8-byte saving from `adr_w → add_sp_imm` is lost). LOAD #1 ends at 0xaf74 (back to iter19a's value). The libextavrcp_jni.so output is **byte-identical to iter19a's** since iter19c was Y1MediaBridge-only. The strict-CT UI-side metadata block (the original motivation for iter19b) was never actually fixed by switching to real track_ids — strict CTs in our test matrix re-fetched on the first CHANGED only and ignored subsequent ones regardless. Per the spec-compliance directive, the AVRCP 1.3 §5.4.2 Table 5.30 (+ ESR07 §2.2 / AVRCP 1.5 §6.7.2 8-byte clarification) "not bound" sentinel is the spec-baseline option; strict-CT compatibility is deferred to iter20+ Phase A1+B (PLAYBACK_STATUS_CHANGED + GetPlayStatus per [`AVRCP13-COMPLIANCE-PLAN.md`](AVRCP13-COMPLIANCE-PLAN.md)). See [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the empirical observations.

**Program-header surgery (iter19d):** the patcher updates LOAD #1's program header at file 0x54:
- offset+16 (`p_filesz`): 0xac54 → 0xaf74 (iter19d, back to iter19a's value)
- offset+20 (`p_memsz`):  0xac54 → 0xaf74 (iter19d)

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `b96978584bcd05762610b8b1131a6125` (iter19d, byte-identical to iter19a).

**Iter20a: Phase B — `GetPlayStatus` (PDU 0x30) trampoline + `y1-track-info` schema extension.** First half of iter20 per [`AVRCP13-COMPLIANCE-PLAN.md`](AVRCP13-COMPLIANCE-PLAN.md). Adds **T6** trampoline (vaddr ~0xaf06) reached via T4's pre-check when the inbound PDU byte is 0x30. T6 allocates an 816 B stack frame (16 B outgoing args + 800 B file_buf), reads the full y1-track-info via the existing SVC-syscall pattern, byte-swaps the BE u32 fields to host order via the new Thumb-2 `REV` instruction (`rev_lo_lo` added to `_thumb2asm.py`), and calls `btmtk_avrcp_send_get_playstatus_rsp` via PLT 0x3564 with `arg1=0` + `r2=duration_ms` + `r3=position_at_state_change_ms` + `sp[0]=play_status`. Outbound `msg_id=542` (new in our msg-id taxonomy), 20 B IPC frame. T4's pre-check expands its dispatch table from 0x20/0x17/0x18 to also cover 0x30. Y1MediaBridge `y1-track-info` schema grows 776 → 800 B with four BE u32 fields at offsets 776..795 (duration_ms / pos_at_state_change_ms / state_change_time_sec / playing_flag). Position is not live-extrapolated in iter20a; T6 returns position-at-last-state-change directly. **iter22d adds live extrapolation** to comply with AVRCP 1.3 §5.4.1 Table 5.26's `SongPosition` definition ("the current position of the playing in milliseconds elapsed"): when `playing_flag == PLAYING`, T6 calls `clock_gettime(CLOCK_BOOTTIME, &timespec)` (NR=263, clk_id=7 — same monotonic source Y1MediaBridge stamps `mStateChangeTime` from, so subtraction yields wall-clock seconds since the last play/pause edge) using the unused outgoing-args slack at sp+8..15 inside the existing T6 frame, computes `live_pos = saved_pos + (now_sec - state_change_sec) * 1000`, and passes that as r3. When stopped/paused the position field stays at the saved value — the freeze point matches the §5.4.1 STOPPED / PAUSED enum semantics where the position is whatever the player froze on. Three new Thumb-2 helpers (`adds_lo_lo` / `subs_lo_lo` / `muls_lo_lo` — T1 forms) added to `_thumb2asm.py`. Closes a known iter20a deferral. Trampoline blob 800 → 892 B (iter20a) → 928 B (iter22d); LOAD #1 ends at 0xb1c4 (iter22d) / 0xb160 (iter22b) / 0xafd0 (iter20a).

**Program-header surgery (iter20a):** the patcher updates LOAD #1's program header at file 0x54:
- offset+16 (`p_filesz`): 0xac54 → 0xafd0 (iter20a; was 0xaf74 in iter19d)
- offset+20 (`p_memsz`):  0xac54 → 0xafd0 (iter20a)

**Compliance scorecard:** mandatory PDUs handled goes 5 → 6 (added 0x30 GetPlayStatus); PDUs spec-correct 5 → 6. iter20b will follow with T8 (notification events 0x01/0x03/0x04/0x05/0x06/0x07) + T1's `EventsSupported` array expansion to advertise the events we now handle.

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `52b1bb70c4edc975ec56c63067c454fb` (iter20a). Y1MediaBridge versionCode 14 → 15, versionName 1.7 → 1.8.

**Iter20b: Phase A1 — `RegisterNotification` event coverage expansion (T8) + `EventsSupported` advertised set expanded.** Adds **T8** trampoline branched from extended_T2's "PDU 0x31 + event ≠ 0x02" arm. T8 reads `y1-track-info` (for events 0x01 / 0x05 which carry payloads from the iter20a schema), then dispatches on `event_id` and emits an INTERIM via the appropriate `reg_notievent_*_rsp` PLT:

| event_id | name | PLT | payload |
|---|---|---|---|
| 0x01 | PLAYBACK_STATUS_CHANGED | 0x339c | play_status u8 (from `y1-track-info[792]`) |
| 0x03 | TRACK_REACHED_END | 0x3378 | (none) |
| 0x04 | TRACK_REACHED_START | 0x336c | (none) |
| 0x05 | PLAYBACK_POS_CHANGED | 0x3360 | position_ms u32 (from `y1-track-info[780..783]`, REV-swapped) |
| 0x06 | BATT_STATUS_CHANGED | 0x3354 | canned `0x00 NORMAL` |
| 0x07 | SYSTEM_STATUS_CHANGED | 0x3348 | canned `0x00 POWERED_ON` |

All response builders share the iter17a/19a calling convention (r0=conn, r1=0 success, r2=reasonCode, r3=event-specific u8/u32). Unknown event_ids fall through to "unknow indication" (0x65bc) for the spec-correct NOT_IMPLEMENTED reject. T8 is INTERIM-only; no proactive CHANGED for the new events.

**T1 `EventsSupported` expansion:** the events array advertised in the `GetCapabilities(0x03)` response goes from `[0x02]` count=1 to `[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]` count=7. Two byte-edits in `T1_TRAMPOLINE`: events count `0x01, 0x22` → `0x07, 0x22`, and the events array bytes. Per the spec-compliance feedback rule, advertise only what's actually implemented — event 0x08 (PLAYER_APPLICATION_SETTING_CHANGED) stays unadvertised until Phase C lands. Strict CTs that gate subscription on `EventsSupported` will now subscribe to all six new events.

Trampoline blob 892 → 1104 B; LOAD #1 ends at 0xb0a4 (was 0xafd0).

**Program-header surgery (iter20b):** the patcher updates LOAD #1's program header at file 0x54:
- offset+16 (`p_filesz`): 0xac54 → 0xb0a4
- offset+20 (`p_memsz`):  0xac54 → 0xb0a4

**Compliance scorecard:** PDU 0x31 event coverage 1/8 → 7/8 mandatory events; T1 `EventsSupported` matches actual coverage. Mandatory PDUs handled stays at 6 (no new PDUs — this is just expanding 0x31 sub-coverage).

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `28d0129cedeb06e7ba233190f92eefde` (iter20b).

---

**Iter23 (current): U1 — disable kernel auto-repeat on the AVRCP `/dev/uinput` virtual keyboard.** A single-instruction NOP, separate from the trampoline chain, but inside the same binary so it ships through `patch_libextavrcp_jni.py`.

**U1 — `UI_SET_EVBIT(EV_REP)` NOP** at file `0x74e8` (4 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `fc f7 b4 e8` | `blx ioctl@plt` |
| after  | `00 bf 00 bf` | `nop ; nop` (Thumb-2) |

The `avrcp_input_init` real body at file offset `0x73c8` opens `/dev/uinput` (string at `0xa849`, fallback paths follow), `strncpy`s the device name `"AVRCP"` (string at `0x828b`) into a `uinput_user_dev` struct, sets `id.bustype = BUS_BLUETOOTH (5)` at `0x749a`, and issues a four-call `UI_SET_EVBIT` sequence:

| Offset | Bytes | Decoded |
|---|---|---|
| `0x74cc` | `23 49 01 22 20 46 7e 44 fc f7 be e8` | `UI_SET_EVBIT, EV_KEY (1)` |
| `0x74d8` | `20 49 02 22 20 46 fc f7 ba e8` | `UI_SET_EVBIT, EV_REL (2)` (vendor typo, harmless) |
| **`0x74e2`** | **`1e 49 14 22 20 46 fc f7 b4 e8`** | **`UI_SET_EVBIT, EV_REP (0x14)` ← U1 target** |
| `0x74ec` | `1b 49 00 22 20 46 fc f7 b0 e8` | `UI_SET_EVBIT, EV_SYN (0)` |

NOPing only the third call drops `EV_REP` from `dev->evbit` without disturbing the other event-class claims. The `r0 = fd / r1 = UI_SET_EVBIT cmd / r2 = 0x14` setup before the NOP'd `blx` is harmless — the next iteration at `0x74ec` reloads `r1` and `r2` cleanly. Verified by reading the patched output: surrounding bytes are byte-identical to stock except for the 4-byte NOP.

**Why this fixes the haptic loop:** Linux's `input_register_device()` calls `input_enable_softrepeat(dev, 250, 33)` only if `EV_REP` is set in `dev->evbit`. By NOT claiming EV_REP, the kernel never schedules the soft-repeat timer for this device. Confirmed source via `getevent -lt` against iter22d hardware: a single PASSTHROUGH FORWARD (`0x4B`) press whose RELEASE was dropped produced **458 `KEY_NEXTSONG REPEAT` events at strict 40 ms intervals** (matching `REP_PERIOD=33ms` plus polling jitter) until something else cancelled the held-key state. Each REPEAT propagated to InputDispatcher → media-key broadcast → haptic feedback (perceived as a continuous 25 Hz vibration).

**Spec alignment:** AVRCP 1.3 §4.6.1 (PASS THROUGH command, defined in AV/C Panel Subunit Specification, ref [2] of AVRCP 1.3) puts the periodic-resend responsibility for held buttons on the CT — the TG forwards one event per frame, not synthesizing extras at the input layer. Disabling EV_REP makes us spec-correct.

**Replaces iter21 (Patch D, reverted iter24).** iter21 capped `PlayerService$startFastForward$1` at 50 iterations × 100 ms (5 s wall-clock) to recover from the runaway seek loop that AVRCP-side dropped releases produced. With U1, the AVRCP-side trigger no longer exists (the kernel doesn't auto-repeat on /dev/input/event4 anymore), and iter21's cap was bounding local hardware-button hold-FF/RW too — making it impossible to scrub through long audiobooks, DJ mixes, or podcasts in a single hold. Reverted in iter24 so local FF/RW hold scrubs as long as the user holds the button (the physical key release always arrives reliably on `mtk-kpd` event0 / `mtk-tpd-kpd` event3, neither of which U1 affects).

**MD5s:** Stock `fd2ce74db9389980b55bccf3d8f15660` → Output `e920b136fdf28b95d95d17ae6e383709` (iter23, U1 only on top of iter22d).

**For the full architectural reference** (data path diagram, response builder calling conventions, ELF program-header surgery, code-cave inventory, msg-id taxonomy, Thumb-2 encoding gotchas), see [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## `patch_mtkbt_odex.py`

Patches `MtkBt.odex` with three fixes:

1. **F1** at `0x3e0ea`: `getPreferVersion()` returns 14 (AVRCP 1.4) instead of 10 (BlueAngel internal code for AVRCP 1.3).
2. **F2** at `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false` — fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts.
3. **iter17a** at `0x03c530`: `BTAvrcpMusicAdapter.handleKeyMessage` TRACK_CHANGED case (sswitch_1a3) — NOPs the `if-eqz v5, :cond_184` cardinality gate. Java's BitSet of registered events is permanently empty (Java-side AVRCP TG bookkeeping isn't updated by our JNI trampolines), so without this NOP the native `notificationTrackChangedNative` call never fires. With this NOP, the native is invoked on every track-change broadcast from Y1MediaBridge. Pairs with the libextavrcp_jni.so iter17a patch which redirects that native to a state-aware T5 trampoline.
4. **iter22b** at `0x03c4fe`: `BTAvrcpMusicAdapter.handleKeyMessage` PLAYBACK_STATUS_CHANGED case (sswitch_18a, event 0x01) — same NOP idiom as iter17a but for event 0x01 instead of 0x02. Without this NOP, `notificationPlayStatusChangedNative` is never invoked because the BitSet check fails. With it, the native fires on every Y1MediaBridge `playstatechanged` broadcast and lands in T9 via the libextavrcp_jni.so iter22b hook at file offset 0x3c88, which then emits the spec-mandated CHANGED frame via PLT 0x339c. Closes the AVRCP 1.3 §5.4.2 gap left by iter20b's INTERIM-only T8 dispatcher.

Recomputes the DEX adler32 checksum embedded in the ODEX header.

**MD5s:** Stock `11566bc23001e78de64b5db355238175` → Output `fa2e34b178bee4dfae4a142bc5c1b701` (iter22b).

---

## `patch_y1_apk.py`

Smali-level patches to the music app `com.innioasis.y1*.apk` via apktool. All four patches are inside two DEX files (`classes.dex` + `classes2.dex`); the original `META-INF/` signature block is retained verbatim because PackageManager rejects an unsigned APK at boot even for system apps. Output goes to `output/com.innioasis.y1_<version>-patched.apk`. See the patcher's docstring for the full DEX-level analysis backing each edit (register layouts, instruction offsets, SQL query, etc).

**Patch D (iter21) was reverted in iter24** — it bounded `PlayerService$startFastForward$1`/`$startRewind$1` at 50 iterations × 100 ms ≈ 5 s as a defense against the AVRCP-side dropped-PASSTHROUGH-release runaway. iter23/U1 in `patch_libextavrcp_jni.py` fixes the AVRCP-side trigger at the kernel input layer (no more auto-repeat on `/dev/input/event4`), and iter21's cap was unhelpfully bounding *local hardware-button* hold-FF/RW too — making it impossible to scrub through long audiobooks/DJ mixes/podcasts in one hold. Local FF/RW now scrubs as long as the user holds the button; the physical key release always arrives reliably on `mtk-kpd` (event0) / `mtk-tpd-kpd` (event3), neither of which U1 affects.

- **Patch A** in `smali_classes2/com/innioasis/music/ArtistsActivity.smali` (`confirm()` artist-tap branch): replaces the in-place `switchSongSortType()` flat-song-list call with an Intent launching `AlbumsActivity` carrying the `artist_key` extra.
- **Patch B** in `smali_classes2/com/innioasis/music/AlbumsActivity.smali` (`initView()`): rebuilds the method (`.locals 2` → `.locals 8`) to read the `artist_key` extra and, if present, query `SongDao.getSongsByArtistSortByAlbum(artist)` and feed a deduplicated `ArrayList<String>` of album names through `AlbumListAdapter.setAlbums()`. If absent, falls through to the original `getAlbumListBySort()` path so the standalone Albums screen still works.
- **Patch C** in `smali/com/innioasis/y1/database/Y1Repository.smali` (field decl): changes `private final songDao` → `public final songDao` so AlbumsActivity (different package) can `iget-object` it without an `IllegalAccessError`. The Kotlin-generated `access$getSongDao$p` exists but exhibits unreliable `NoSuchMethodError` on this device's old Dalvik (API 17).
- **Patch E** (iter22d, refined iter25, +STOP iter27) in `smali_classes2/com/innioasis/y1/receiver/PlayControllerReceiver.smali` at `:cond_c` — splits the short-press `KEY_PLAY → playOrPause()` branch into four discrete arms per AVRCP 1.3 §4.6.1 (PASS THROUGH command, op codes defined in AV/C Panel Subunit Specification ref [2]; concrete frame example in AVRCP 1.3 §19.3 Appendix D) and ICS Table 8 (Cat 1 op_id status):
  - `KEY_PLAY` (85, `KEYCODE_MEDIA_PLAY_PAUSE`) → **`playOrPause()V`** (toggle). Legacy `ACTION_MEDIA_BUTTON` broadcast Intents always use 85, and toggle is the right semantics for a single physical play/pause key. Identical to stock's behavior for this keycode.
  - `KEYCODE_MEDIA_PLAY` (`0x7e` = 126, the Android keycode `libextavrcp_jni`'s `avrcp_input_sendkey` produces from PASSTHROUGH 0x44 → Linux `KEY_PLAYCD` (200) → AVRCP.kl `MEDIA_PLAY` mapping) → **`play(Z)V`** with bool=false. AV/C Panel Subunit Spec PLAY (op_id 0x44): "transition to PLAYING from any state"; if already PLAYING, the call is effectively a no-op.
  - `KEYCODE_MEDIA_PAUSE` (`0x7f` = 127) → **`pause(IZ)V`** with reason=`0x12`, flag=true. AV/C Panel Subunit Spec PAUSE (op_id 0x46): "transition to PAUSED from any state". ICS Table 8 item 21: O. The reason byte is a diagnostic identifier PlayerService Timber-logs as `executed pause from %d`; existing reasons in the stock binary span `0xc..0x11` (various internal sources + `playOrPause`'s pause arm), so `0x12` is a fresh tag for "discrete PASSTHROUGH PAUSE from PlayControllerReceiver". The boolean flag virtually always resolves to `true` in the stock binary (every observed callsite goes through Kotlin's `pause$default` helper with a mask byte that defaults p2 to true); we pass it explicitly.
  - **iter27**: `KEYCODE_MEDIA_STOP` (`0x56` = 86, the Android keycode `libextavrcp_jni`'s `avrcp_input_sendkey` produces from PASSTHROUGH 0x45 → Linux `KEY_STOPCD` (166) → AVRCP.kl `MEDIA_STOP` mapping) → **`stop()V`** (no args). AV/C Panel Subunit Spec STOP (op_id 0x45): "transition to STOPPED state". `PlayerService.stop()` is `public final stop()V .locals 4` and calls `IjkMediaPlayer.stop()` + `reset()` + `MediaPlayer.stop()` — releasing the media position. **ICS Table 8 item 20 is M (mandatory) for Cat 1 TGs**, so adding this arm closes a verified compliance gap. Pre-iter27, KEYCODE_MEDIA_STOP fell through to `:cond_e` and was silently dropped at the music-app layer.
  - **iter22d's first attempt** routed all three keycodes to a single `:cond_play_match` label calling `playOrPause()` (toggle). Empirically wrong for strict CTs: `dual-bolt-iter23` capture showed the Bolt EV head unit issue 5 discrete PLAY (`0x44`) presses while Y1 was already PLAYING — the toggle inverted Bolt's intent on each press, and the user perceived the PLAY button as unresponsive. iter25 splits the join label into three arms, each invoking the correct `PlayerService` method.
  - Stock smali at `:cond_c`: `sget-object KeyMap.INSTANCE` → `getKEY_PLAY()` → `move-result p1` → `if-ne v2, p1, :cond_e` → `[playOrPause action]` → `goto :goto_5`. iter25 patched smali: same prologue (KeyMap getter), then `if-eq v2, p1, :cond_play_pause_toggle` (KEY_PLAY → toggle arm) + `const/16 p1, 0x7e` + `if-eq v2, p1, :cond_play_strict` + `const/16 p1, 0x7f` + `if-eq v2, p1, :cond_pause_strict` + `goto :cond_e` (no match → bail to method end), followed by three labeled arms each ending in `goto :goto_5`. apktool renumbers the user-defined labels to alphanumeric `:cond_X` on reassembly (`:cond_play_pause_toggle` → `:cond_d`, `:cond_play_strict` → `:cond_e`, `:cond_pause_strict` → `:cond_f`); the unconditional `goto :cond_e` (which targets the existing method-end fall-through) is optimized to `goto :goto_5` in the rebuilt DEX since `:cond_e` (stock) sits immediately before `:goto_5` — same control flow either way.

**Apktool reassembly:** `_patch_workdir/apktool.jar d --no-res` decode → smali edits → `apktool b` reassemble (the post-DEX aapt step fails because resources weren't decoded, but DEX is already built by then; the script intentionally ignores the exit code). Patched DEX bytes are then dropped into a copy of the original APK with `META-INF/` preserved.

**Deployment:** `adb root && adb remount && adb push <apk> /system/app/com.innioasis.y1/com.innioasis.y1.apk && adb reboot`. Do **not** use `adb install` — PackageManager rejects re-signed system app APKs.

---

> **Removed in v2.1.0:** the historical `patch_adbd.py` (H1/H2/H3 byte patches against `/sbin/adbd`) and `patch_bootimg.py` (in-place cpio patcher that wrapped it). Both revisions broke ADB protocol on hardware ("device offline") and have been superseded by `src/su/` since v1.8.0. The full diagnosis of the H1/H2/H3 failure modes — including why arg-zero kept all bionic bookkeeping intact yet the device still went offline, why `default.prop`'s `ro.secure=0` is inert on this OEM adbd (no `should_drop_privileges()` gating), and why `adb root` is actively harmful — is preserved in [`INVESTIGATION.md`](INVESTIGATION.md) §"adbd Root Patches (H1/H2/H3)".

---

## `src/su/` (root, v1.8.0+)

Source for a minimal setuid-root `su` binary installed at `/system/xbin/su` by the bash's `--root` flag. Replaces the H1/H2/H3 adbd byte patches that broke ADB protocol on hardware.

- **`src/su/su.c`** — direct ARM-EABI syscall implementation, no libc dependency. `setgid(0)` → `setuid(0)` → `execve("/system/bin/sh", ...)`. Three invocation forms: bare `su` (interactive root shell), `su -c "<cmd>"` (one-off), `su <prog> [args...]` (exec-passthrough).
- **`src/su/start.S`** — ~10-line ARM Thumb-2 entry stub; extracts argc/argv/envp from the ELF process-start stack layout, calls `main`, exits via `__NR_exit`.
- **`src/su/Makefile`** — cross-compile via `arm-linux-gnu-gcc`. `-nostdlib -ffreestanding -static -Os -mthumb -mfloat-abi=soft`; output ~900 bytes, statically linked, no `NEEDED` entries.

**No supply chain beyond GCC + this source.** No SuperSU/Magisk/phh-style binary imported; no manager APK; no whitelist. Trade-off: any process that can exec `/system/xbin/su` becomes root, which is acceptable for a single-user research device but not for a consumer ROM.

**Build:** `cd src/su && make` produces `src/su/build/su`. The bash references this prebuilt path; if missing, `--root` exits with a clear error pointing at `make`. Idempotent.

**Deploy:** the bash's `--root` flag does `install -m 06755 -o root -g root src/su/build/su /system/xbin/su` against the mounted system.img. Post-flash: `adb shell /system/xbin/su -c "id"` → `uid=0(root)`.

**Purpose:** unblock visibility into mtkbt's `__xlog_buf_printf` ring buffer, btsnoop, and live `gdbserver` attach — required to pin down which branch sets `result=0x1000` in `MSG_ID_BT_AVRCP_CONNECT_CNF`.
