# AVRCP 1.3 Spec-Compliance Plan

Current AVRCP 1.3 spec coverage and the staged path to closing remaining gaps. Anchored against the AVRCP ICS (Implementation Conformance Statement) Table 7 in `docs/spec/AVRCP.ICS.p17.pdf`. Per-CT empirical observations behind each design choice live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT".

This document is the build plan only. For why we have a proxy at all, the current trampoline chain shape, and the calling conventions of the response builders we use, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For per-patch byte-level reference, see [`PATCHES.md`](PATCHES.md).

---

## 0. Spec target + citation discipline

**Wire protocol target: AVRCP 1.3 (V13, adopted 16 April 2007), with ESR07 errata applied.** AVCTP 1.2 paired per §6 SDP record. Canonical PDFs (local-only — not committed because Bluetooth SIG copyright disallows redistribution; download from <https://www.bluetooth.com/specifications/specs/a-v-remote-control-profile-1-3/> and drop into `docs/spec/`, which is `.gitignore`d):

- `docs/spec/AVRCP_SPEC_V13.pdf` — base spec, 93 pages.
- `docs/spec/ESR07_ESR_V10.pdf` — Errata Service Release 07 (2013-12-03); §2.1 contains Erratum 4969 (SDP record AVCTP version clarification, the only formal 1.3 erratum); §2.2 carries supplementary clarifications that resolve printed typos in 1.3 wire-format tables (e.g., the 8-byte `Identifier` sentinel form for TRACK_CHANGED).
- `docs/spec/AVRCP.ICS.p17.pdf` — Implementation Conformance Statement Proforma, revision p17 (2024-07-01, TCRL.2024-1, 25 pages). Authoritative TG/CT feature M/O matrix with conditional logic. Used to anchor the scorecard in §2 below.
- `docs/spec/AVRCP.IXIT.1.6.0.pdf` — Implementation eXtra Information for Testing Proforma. Companion to ICS; defines per-implementation values a tester needs (timer values, parameter ranges, declared PASSTHROUGH op_id support).

Per ICS §1.2 Table 2b, AVRCP 1.3 was deprecated 2023-02-01 and is scheduled for withdrawal 2027-02-01. We're patching a 2012 firmware that was originally qualified against AVRCP 1.0; the deprecation schedule does not block us from shipping.

**Citation hygiene rule.** Cite by **PDU name + AVRCP 1.3 section number** verified against `docs/spec/AVRCP_SPEC_V13.pdf` table-of-contents. Where a behavior comes from AV/C Panel Subunit Spec (PASS THROUGH op codes / press-release semantics), cite as `AVRCP 1.3 §4.6.1 (defined in AV/C Panel Subunit Spec, ref [2])`. Where the spec text contains a printed typo, cite ESR07's clarification: `AVRCP 1.3 §X.Y + ESR07 §2.2`. Section numbers must appear in the AVRCP 1.3 spec PDF's table of contents — anything else is a citation error.

---

## 1. Goal

Implement AVRCP 1.3 spec-completely so any spec-compliant 1.3+ controller renders our metadata. Scope is the latest revision of the AVRCP 1.3 spec (V13 + ESR07). Anything outside that revision is out of scope.

The one carry-out from outside the 1.3 spec proper is `MtkBt.odex` patch F1's BlueAngel-internal `getPreferVersion()` value — this internal flag must be set high enough for MtkBt's Java-side dispatcher to invoke 1.3+ command handling on a stack that was originally compiled against AVRCP 1.0. F1 sets the flag and unblocks 1.3 dispatch; nothing in our wire shape is changed by it.

---

## 2. Coverage matrix — current vs spec

Anchored against **ICS Table 7 (Target Features)** in `docs/spec/AVRCP.ICS.p17.pdf` §1.5, which is the canonical M/O determination. M/O status is conditional on what other features the TG claims; the ICS encodes the conditionals explicitly. PDU = "PDU ID" byte at AV/C body offset +4. AVRCP 1.3 V13 spec sections in `docs/spec/AVRCP_SPEC_V13.pdf`.

**Our claims that drive the conditionals:** PASS THROUGH Cat 1 (V1 SDP record, ICS Table 7 item 7), GetElementAttributes Response (T4, item 20). Combining these:

| ICS Table 7 row | PDU / Capability | Spec § | ICS Status (this proj) | Currently shipped | Gap |
|---|---|---|---|---|---|
| **2** | Accepting connection establishment (control) | §4.1.1 | **M** | ✓ (mtkbt) | — |
| **3-4** | Connection release | §4.1.2 | **M** | ✓ (mtkbt) | — |
| **5** | Receiving UNIT INFO | §4.1.3 | **M** | ✓ (mtkbt) | — |
| **6** | Receiving SUBUNIT INFO | §4.1.3 | **M** | ✓ (mtkbt) | — |
| **7** | Receiving PASS THROUGH cat 1 | §4.1.3 | **M (C.1: at least one cat)** | ✓ (mtkbt + Patch E) | — |
| 8 | Receiving PASS THROUGH cat 2 | §4.1.3 | C.1: not required (cat 1 satisfies) | not claimed | — |
| 9 | Receiving PASS THROUGH cat 3 | §4.1.3 | C.1: not required | not claimed | — |
| 10 | Receiving PASS THROUGH cat 4 | §4.1.3 | C.1: not required | not claimed | — |
| **11** | GetCapabilities Response (PDU 0x10) | §5.1.1 | **M (C.3: M IF cat 1)** | ✓ T1 | — |
| 12-15 | List/Get/Set PApp Settings (0x11–0x14) | §5.2.1–5.2.4 | C.14: M to support **none or all** | not shipped (none) | spec-compliant; Phase C ships all |
| 16-17 | PApp Setting Attribute/Value Text (0x15-0x16) | §5.2.5-5.2.6 | O | not shipped | optional; Phase C |
| 18 | InformDisplayableCharacterSet (PDU 0x17) | §5.2.7 | O | ✓ T_charset | — |
| 19 | InformBatteryStatusOfCT (PDU 0x18) | §5.2.8 | O | ✓ T_battery | — |
| **20** | GetElementAttributes (PDU 0x20) | §5.3.1 | **M (C.3: M IF cat 1)** | ✓ T4 (all 7 §5.3.4 attrs: Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime, single packed frame) | — |
| **21** | GetPlayStatus (PDU 0x30) | §5.4.1 | **M (C.2: M IF GetElementAttributes Response)** | ✓ T6 with live position via `clock_gettime(CLOCK_BOOTTIME)` | — |
| **22** | RegisterNotification (PDU 0x31) | §5.4.2 | **M (C.12: M IF cat 1)** | ✓ T2/extended_T2/T8 | — |
| **23** | Notify EVENT_PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | **M (C.4: M IF GetElementAttributes + RegisterNotification)** | ✓ T8 INTERIM + T9 CHANGED on edge | — |
| **24** | Notify EVENT_TRACK_CHANGED | §5.4.2 Tbl 5.30 | **M (C.4)** | ✓ extended_T2 INTERIM + T5 CHANGED on edge | — |
| 25 | Notify EVENT_TRACK_REACHED_END | §5.4.2 Tbl 5.31 | O | ✓ T8 INTERIM-only — CHANGED-on-edge planned (Phase F1) | partial |
| 26 | Notify EVENT_TRACK_REACHED_START | §5.4.2 Tbl 5.32 | O | ✓ T8 INTERIM-only — CHANGED-on-edge planned (Phase F1) | partial |
| 27 | Notify EVENT_PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | O | ✓ T8 INTERIM-only — periodic CHANGED planned (Phase F3) | partial |
| 28 | Notify EVENT_BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | O | ✓ T8 INTERIM with canned `0x00 NORMAL` — real battery + CHANGED-on-edge planned (Phase F2) | partial |
| 29 | Notify EVENT_SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | O | ✓ T8 INTERIM with `0x00 POWER_ON` (canned, but the canned value IS the real value — see §4 Phase note) | — |
| 30 | Notify EVENT_PLAYER_APPLICATION_SETTING_CHANGED | §5.4.2 Tbl 5.37 | O | not shipped | Phase C (paired with PApp Settings) |
| 31-32 | Continuation (PDUs 0x40/0x41) | §5.5 | C.2: M IF GetElementAttributes Response | not shipped (current fall-through to msg=520 is functionally adequate; explicit handler planned for ICS-completeness — Phase D) | partial |
| **65** | Discoverable Mode | §12.1 | **M** | ✓ (mtkbt) | — |
| 66 | PASSTHROUGH operation supporting Press and Hold | §4.1.3 | O | ✓ (mtkbt + U1 disables kernel auto-repeat on AVRCP uinput) | — |

**Mandatory rows: all hit.** Optional rows fully shipped: 18, 19, 29, 66. Optional rows partial (INTERIM-only or canned, real-data CHANGED-on-edge work tracked under Phase F): 25, 26, 27, 28. The Continuation gap (rows 31-32) is unobserved in our CT test matrix; explicit handler tracked as Phase D for ICS-completeness only.

**INTERIM vs. CHANGED notation reminder.** AVRCP 1.3 §5.4.2 splits each event subscription into two response shapes: an immediate **INTERIM** carrying the current value at registration time, and an asynchronous **CHANGED** when the relevant condition fires. A row marked "INTERIM-only" handles registration but never emits CHANGED; spec-strict subscribers expect both halves. Mandatory rows 23 and 24 ship both halves; the optional rows above currently ship only INTERIM and are tracked under Phase F to ship the missing CHANGED-on-edge halves.

**ICS Table 8 (Cat 1 PASSTHROUGH op_ids — mandatory subset):**

| op_id | Operation | ICS status | Currently shipped |
|---|---|---|---|
| 0x44 | Play (item 19) | **M** | ✓ Patch E → `play(true)` |
| 0x45 | Stop (item 20) | **M** | ✓ Patch E → `stop()V` |
| 0x46 | Pause (item 21) | O | ✓ Patch E → `pause(0x12, true)` |
| 0x48 | Rewind (item 23) | O | partial (mtkbt fftimer + Y1 long-press) |
| 0x49 | Fast forward (item 24) | O | partial |
| 0x4B | Forward / next track (item 26) | O | ✓ |
| 0x4C | Backward / prev track (item 27) | O | ✓ |

**Mandatory cat 1 op_ids: both hit (PLAY + STOP).**

---

### Notification events (PDU 0x31 sub-dispatch, AVRCP 1.3 §5.4.2 Tables 5.29–5.37)

The advertised set in the GetCapabilities response (T1's `EventsSupported` array) determines what a CT can register for. We currently advertise events `0x01..0x07`.

| event_id | Name | Spec § | INTERIM | CHANGED on edge |
|---|---|---|---|---|
| 0x01 | PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | ✓ T8 | ✓ T9 (Y1 play/pause broadcast) |
| 0x02 | TRACK_CHANGED | §5.4.2 Tbl 5.30 | ✓ extended_T2 | ✓ T5 (Y1 track-change broadcast) |
| 0x03 | TRACK_REACHED_END | §5.4.2 Tbl 5.31 | ✓ T8 | needs end-of-track Y1 broadcast |
| 0x04 | TRACK_REACHED_START | §5.4.2 Tbl 5.32 | ✓ T8 | needs start-of-track Y1 broadcast |
| 0x05 | PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | ✓ T8 | needs periodic Playback-interval timer |
| 0x06 | BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | ✓ T8 (canned 0x00 NORMAL) | optional |
| 0x07 | SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | ✓ T8 (canned 0x00 POWER_ON) | optional |
| 0x08 | PLAYER_APPLICATION_SETTING_CHANGED | §5.4.2 Tbl 5.37 | not advertised | Phase C |

---

## 3. Binary discovery — what's already mapped

`objdump -dRT` against the v3.0.2 stock libraries was the entire discovery pass for what response-builder primitives we have to call. Result: every PDU and every event has both:

- A function in `libextavrcp.so` (e.g. `<btmtk_avrcp_send_get_playstatus_rsp>: 0x2354`)
- A PLT stub in `libextavrcp_jni.so` (e.g. `<btmtk_avrcp_send_get_playstatus_rsp@plt>: 0x3564`)

Full PLT inventory (from `libextavrcp_jni.so` md5 `fd2ce74db9389980b55bccf3d8f15660`, what each trampoline would `blx`):

| PDU / event | Response builder | PLT @ | libextavrcp.so body @ |
|---|---|---|---|
| **In use** ||||
| 0x10 GetCapabilities | get_capabilities_rsp | `0x35dc` | `0x1dac` |
| 0x20 GetElementAttributes | get_element_attributes_rsp | `0x3570` | `0x2188` |
| 0x31 event 0x02 | reg_notievent_track_changed_rsp | `0x3384` | `0x2458` |
| (default reject) | pass_through_rsp | `0x3624` | n/a (in libextavrcp.so too — not used) |
| **Phase A — notifications** ||||
| 0x31 event 0x01 | reg_notievent_playback_rsp | `0x339c` | `0x23f0` |
| 0x31 event 0x03 | reg_notievent_reached_end_rsp | `0x3378` | `0x24c8` |
| 0x31 event 0x04 | reg_notievent_reached_start_rsp | `0x336c` | `0x2528` |
| 0x31 event 0x05 | reg_notievent_pos_changed_rsp | `0x3360` | `0x2588` |
| 0x31 event 0x06 | reg_notievent_battery_status_changed_rsp | `0x3354` | `0x25f0` |
| 0x31 event 0x07 | reg_notievent_system_status_changed_rsp | `0x3348` | `0x2658` |
| 0x31 event 0x08 | reg_notievent_player_appsettings_changed_rsp | `0x345c` | `0x2720` |
| **Phase B — playback status** ||||
| 0x30 GetPlayStatus | get_playstatus_rsp | `0x3564` | `0x2354` |
| **Phase C — player application settings** ||||
| 0x11 | list_player_attrs_rsp | `0x35d0` | `0x1e24` |
| 0x12 | list_player_values_rsp | `0x35c4` | `0x1e74` |
| 0x13 | get_curplayer_value_rsp | `0x35b8` | `0x1ed0` |
| 0x14 | set_player_value_rsp | `0x3594` | `0x1f2e` |
| 0x15 | get_player_attr_text_rsp | `0x35ac` | `0x1f58` |
| 0x16 | get_player_value_text_value_rsp | `0x35a0` | `0x203c` |
| 0x17 | inform_charsetset_rsp | `0x3588` | `0x2138` |
| 0x18 | battery_status_rsp (CT-side battery) | `0x357c` | `0x2160` |

**No new PLT discovery needed.** The stubs are already linked. The work that remains:

### 3a. Per-function argument-convention discovery (still required)

Argument names from the OEM are not what their positions suggest — `get_element_attributes_rsp`'s "arg2" turned out to be attribute *index*, not transId; we found that the hard way (see [`INVESTIGATION.md`](INVESTIGATION.md)). Each new response builder needs the same disassembly pass before its trampoline can be written. Pattern, per function:

1. `objdump -d --start-address=<libextavrcp.so addr> --stop-address=<+0x100>` to dump the function body.
2. Look for `ldrb rN, [r0, #17]` — that's transId being auto-extracted from `conn[17]`. If present, `transId` is *not* an arg.
3. Locate the call to `AVRCP_SendMessage` (at `libextavrcp.so:0x18ec`). Walk backwards to see the buffer-build loop and the conditional that decides whether to emit (vs accumulate).
4. Cross-reference with any in-tree caller in `libextavrcp_jni.so` (most response builders have at least one stock JNI caller — those reveal the OEM's intended arg shape). Search `objdump -d libextavrcp_jni.so | grep -B5 "blx <…@plt>"`.
5. Document the resulting C signature in `ARCHITECTURE.md`, same format as `get_element_attributes_rsp`.

Estimated effort per function: 30 min for simple ones, 2 hours for the multi-arg accumulator-style ones. Total Phase A→E discovery: ~1 day of focused work.

### 3b. Code-cave budget

LOAD #1 padding currently used: `0xac54..0xb21c` (1480 B). Free space past `0xb21c` to LOAD #2 at `0xbc08`: **~2540 bytes**. New trampolines average ~80–200 bytes each; budget supports a few dozen more. Not space-constrained.

If we ever do exhaust LOAD #1 padding, we have a known fallback: extend the trick to the LOAD #2 padding region by bumping LOAD #2's `p_filesz`/`p_memsz`. Not needed for this plan.

---

## 4. Implementation phases

Each phase is independent and ship-able on its own. Order is by expected user impact + prerequisite chain. Phases A0 / A1 / B / GetElementAttributes 7-attr are shipped; Phases C and D remain.

### Phase A0 — Inform PDUs + TRACK_CHANGED wire-shape — SHIPPED

**What it ships:**
- **T_charset trampoline** for PDU 0x17 InformDisplayableCharacterSet → calls `inform_charsetset_rsp` via PLT 0x3588 with `r1=0` (success).
- **T_battery trampoline** for PDU 0x18 InformBatteryStatusOfCT → calls `battery_status_rsp` via PLT 0x357c with `r1=0` (success).
- **T2/T5 r1=0 wire shape**: T2 (extended_T2) and T5 pass `r1=0` to `track_changed_rsp` (`r1!=0` would hit the response builder's reject-shape path that omits the event payload).

The Inform PDUs are pure CT→TG informational acks; no Y1MediaBridge data plumbing, no music-app patches. Closes the §5.2.7 / §5.2.8 NACK that strict CTs interpret as "the TG distrusts subsequent metadata" (see [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the strict-CT capture that established 0x17 as the dominant blocker pre-Phase-A0).

### Phase A1 — Notification expansion (T8 + T9) — SHIPPED

T8 trampoline branches from extended_T2's "PDU 0x31 + event ≠ 0x02" arm. T8 allocates an 800 B stack frame, reads `y1-track-info` (for events 0x01/0x05 which carry payloads from the GetPlayStatus block), then dispatches on `event_id` and emits an INTERIM via the matching `reg_notievent_*_rsp` PLT:

| event_id | PLT | payload | source |
|---|---|---|---|
| 0x01 PLAYBACK_STATUS_CHANGED | 0x339c | u8 play_status | y1-track-info[792] |
| 0x03 TRACK_REACHED_END | 0x3378 | (none) | — |
| 0x04 TRACK_REACHED_START | 0x336c | (none) | — |
| 0x05 PLAYBACK_POS_CHANGED | 0x3360 | u32 position_ms | y1-track-info[780..783] (REV-swapped) |
| 0x06 BATT_STATUS_CHANGED | 0x3354 | u8 canned `0x00 NORMAL` | — |
| 0x07 SYSTEM_STATUS_CHANGED | 0x3348 | u8 canned `0x00 POWERED_ON` | — |

**T9** adds proactive CHANGED for event 0x01 PLAYBACK_STATUS_CHANGED (structurally a clone of T5, the TRACK_CHANGED proactive trampoline). T9 is invoked by the patched `notificationPlayStatusChangedNative` (file offset 0x3c88, stock prologue `2D E9 F3 41` overwritten with `b.w T9`), which fires on every Y1MediaBridge `playstatechanged` broadcast once the matching MtkBt cardinality NOP at 0x3c4fe (sswitch_18a, event 0x01 case) is in place. T9 reads `y1-track-info[792]` (current play_status), compares against `y1-trampoline-state[9]` (`last_play_status`), emits `reg_notievent_playback_rsp(conn, 0, REASON_CHANGED, play_status)` via PLT 0x339c on edge, and writes the new value back. transId is auto-extracted from conn[17] by the response builder (same convention T5 uses for track_changed_rsp). Position (event 0x05) and the other events are INTERIM-only — proactive CHANGED for 0x05 would need a periodic timer (no broadcast equivalent), and 0x03/0x04/0x06/0x07 don't have natural Y1-side edge sources.

**T1 `EventsSupported`:** advertises events `[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]` (count=7). Per the spec-compliance feedback rule, advertise only what's implemented — event 0x08 (PLAYER_APPLICATION_SETTING_CHANGED) stays unadvertised until Phase C.

**Schema dependency:** Y1MediaBridge writes `play_status` at offset 792 and `position_at_state_change_ms` at offsets 780..783; T8 reads from those.

### Phase B — GetPlayStatus (T6) — SHIPPED

T6 branches from T4's pre-check on PDU 0x30 (alongside the 0x20/0x17/0x18 dispatch). Reads y1-track-info[776..795] for `duration_ms` / `position_at_state_change_ms` / `state_change_time_sec` / `playing_flag` — all stored big-endian to match the existing track_id encoding, byte-swapped to host order via the Thumb-2 `REV` instruction (`rev_lo_lo` in `_thumb2asm.py`). Calls `get_playstatus_rsp` via PLT 0x3564 with `arg1=0` + duration + position + play_status. When `playing_flag == PLAYING`, T6 calls `clock_gettime(CLOCK_BOOTTIME, &timespec)` to extrapolate live position from the saved freeze-point; when stopped/paused the position field stays at the saved freeze point.

**Calling convention** (confirmed via disassembly + cross-reference with stock JNI caller `getPlayerstatusRspNative`, documented in `ARCHITECTURE.md`):
```c
btmtk_avrcp_send_get_playstatus_rsp(
    void* conn,             // r0 = r5+8
    uint8_t reject_code,    // r1 = 0 for success
    uint32_t song_length,   // r2 = duration_ms
    uint32_t song_position, // r3 = position_ms
    uint8_t play_status     // sp[0] = 0/1/2 = STOPPED/PLAYING/PAUSED
);
// Outbound msg_id=542, 20 B IPC frame.
```

**Position handling: not live-extrapolated.** T6 returns `position_at_state_change_ms` directly. CTs poll GetPlayStatus periodically (typically every few seconds) so the value updates per poll cycle. Skipping live extrapolation avoids a `clock_gettime` syscall + multiplication in the trampoline hot path. Future iters can add live extrapolation if a CT requires continuously-ticking position display (none observed so far; the `state_change_time_sec` field is reserved in the schema for that purpose).

**Files touched:**
- `src/patches/_trampolines.py` — added `_emit_t6` (~52 B trampoline body), modified T4 pre-check to dispatch PDU 0x30, added `T6_*` frame constants, added `PLT_get_playstatus_rsp = 0x3564`. ~80 lines.
- `src/patches/_thumb2asm.py` — added `rev_lo_lo` (REV T1) for BE→LE byte-swap. 4 lines.
- `src/patches/patch_libextavrcp_jni.py` — bumped `OUTPUT_MD5` to `52b1bb70c4edc975ec56c63067c454fb`.
- `src/Y1MediaBridge/.../MediaBridgeService.java` — extended `writeTrackInfoFile()` schema 776→800 B with the four new fields; added `putBE32` helper. versionCode 14→15, versionName 1.7→1.8.

**Estimated effort:** 1-2 days. Includes the disassembly pass.

### Phase D — Continuation PDUs (RequestContinuingResponse 0x40 + AbortContinuingResponse 0x41)

**Status:** ICS Table 7 rows 31-32 are M (C.2: M IF GetElementAttributes Response). Per AVRCP 1.3 §4.7.7 / §5.5, continuation flow is initiated by the TG setting `Packet Type=01` (start) in a response — the CT only sends 0x40 in reply to a previously-fragmented response. Two findings establish the current state:

1. **Across 2868 PDU 0x20 frames in a single TV capture, 100% carry `packet_type=0x00`** (single non-fragmented AVRCP packet). `get_element_attributes_rsp` never sets the start-of-fragmentation flag; mtkbt fragments below at the AVCTP layer transparently to AVRCP. Even with the 7-attr T4 expansion, worst-case packed responses (~1100 B with maxed Title/Artist/Album/Genre slots) ship as a single AVRCP packet.
2. **Across all 43 captures, zero 0x40/0x41 PDUs** from any CT in our test matrix, against thousands of GetElementAttributes / RegisterNotification PDUs.

**Current behavior** is a fall-through to `pass_through_rsp` returning msg=520 NotImplemented for any unknown PDU including 0x40/0x41. Functionally adequate; spec-acceptable.

**Phase D ships an explicit handler** for ICS-conformance scorecard reasons only: a tiny T-trampoline that returns AV/C `INVALID PARAMETER` (status 0x05) since we never set `packet_type=01` and therefore never legitimately receive 0x40. Effort: ~30 min. Documented as paper-only closure of ICS rows 31-32.

**Re-evaluation trigger:** if any future hardware capture surfaces non-zero PDU 0x40 traffic (would indicate `get_element_attributes_rsp` started fragmenting after the 7-attr expansion), upgrade to a stateful continuation handler that re-emits the buffered response (~2-3 days).

### Phase F — Optional event coverage (real data, CHANGED-on-edge)

Closes the partial-implementation entries on ICS Table 7 rows 25-28 by completing the CHANGED-on-edge half of each event subscription. Listed here in increasing effort order. All four sub-phases ship real data sourced from Y1 / Android system APIs — no canned values.

#### Phase F1 — Track-edge events (PDUs in event 0x03 TRACK_REACHED_END + 0x04 TRACK_REACHED_START). ~3 hours.

**Spec:** AVRCP 1.3 §5.4.2 Tables 5.31 / 5.32. ICS rows 25 / 26 (both Optional).

**Real data:** Y1MediaBridge's existing `com.android.music.metachanged` broadcast already fires at every track edge — the moment when the previous track reaches end and the new track reaches start.

**Spec-strict semantic for END:** §5.4.2 Table 5.31 is "Notify when reached the end of the track of the playing element" — natural-end-only, not skip-driven. Distinguish via `mPositionAtStateChange ≈ mCurrentDuration` (within ~1 sec) on the previous track at the moment of the metachanged broadcast. Skip-driven track changes fire only event 0x02 + event 0x04, not 0x03.

**Implementation:** extend T5's existing track-change CHANGED emission to fire a 3-tuple in sequence — event 0x03 TRACK_REACHED_END (only on natural end), event 0x02 TRACK_CHANGED (existing), event 0x04 TRACK_REACHED_START (always on track edge). T5 already runs at the right moment via the patched `notificationTrackChangedNative`; just add two PLT calls and a stack-buffer compare for the natural-end check. ~30 B added to the trampoline blob. Y1MediaBridge writes a 1-bit `previous_track_natural_end` flag into the schema before firing the broadcast.

#### Phase F2 — Real battery state (event 0x06 BATT_STATUS_CHANGED). ~4 hours.

**Spec:** AVRCP 1.3 §5.4.2 Tables 5.34 / 5.35. ICS row 28 (Optional). Allowed values: `0=NORMAL, 1=WARNING, 2=CRITICAL, 3=EXTERNAL, 4=FULL_CHARGE`.

**Real data:** Android `Intent.ACTION_BATTERY_CHANGED` (sticky broadcast on API 17+). Provides `EXTRA_LEVEL` (0-100), `EXTRA_PLUGGED` (charger state), `EXTRA_STATUS` (CHARGING/FULL/etc.). No sysfs access required.

**Implementation:**
- Y1MediaBridge: new BroadcastReceiver for `ACTION_BATTERY_CHANGED`. Bucket-map level + plug state to AVRCP value (`charging+full → FULL_CHARGE; charging → EXTERNAL; level≤15 → CRITICAL; level≤30 → WARNING; else → NORMAL`). Emit broadcast `com.y1.mediabridge.batterychanged` only on bucket transitions to avoid flooding on every percent tick.
- Schema: append `battery_status` u8 byte at `y1-track-info[795]` (currently in the reserved 793..799 range).
- T8's existing event-0x06 INTERIM arm reads `[795]` instead of returning canned `0x00 NORMAL`.
- New T_battery_changed proactive trampoline (clone of T9 shape, ~50 B), reads `[795]`, calls `reg_notievent_battery_status_changed_rsp` (PLT 0x3354) on edge.
- One additional cardinality NOP in MtkBt.odex for the sswitch arm handling event 0x06 (mirror of the existing 0x01/0x02 NOPs).

#### Phase F3 — Periodic PLAYBACK_POS_CHANGED (event 0x05). ~1 day.

**Spec:** AVRCP 1.3 §5.4.2 Table 5.33. ICS row 27 (Optional). RegisterNotification command for event 0x05 carries a `playback_interval` u32 (in seconds); TG fires CHANGED every N seconds while playing.

**Real data:** existing Y1MediaBridge `computePosition()` (live extrapolation from `mPositionAtStateChange + (now - mStateChangeTime)`).

**Implementation (single-conn assumption — Y1 connects to one CT at a time per the test matrix):**
- Per-conn subscription state stored at `y1-trampoline-state[10..15]` (currently pad): `pos_subscribed_interval_sec` (u32 BE, 4 B) + `pos_last_emitted_ms` (u32 BE, 4 B).
- T8's existing event-0x05 INTERIM arm extended to write the saved interval into `[10..13]` on subscription.
- Y1MediaBridge fires `com.y1.mediabridge.posticked` every 1 second while playing (Handler postDelayed loop).
- New T_pos_tick trampoline reads saved interval from state, computes whether elapsed ≥ interval, emits CHANGED with current position via `reg_notievent_pos_changed_rsp` (PLT 0x3360), updates `pos_last_emitted_ms`.
- Pause/stop: Y1MediaBridge stops the broadcast cadence; resume restarts it.

#### Phase F4 — PlayerApplicationSettings (PDUs 0x11-0x16 + event 0x08). ~5 days.

**Spec:** AVRCP 1.3 §5.2.1–5.2.6 + §5.4.2 Table 5.37. ICS rows 12-17 + 30. Condition C.14: support either none or all of 0x11-0x14 — we currently support none (spec-conformant); going to "all" is the threshold.

**Real data:** Y1's `com.innioasis.y1.apk` `SharedPreferencesUtils.setShuffle(Z)V` and `setRepeatMode(I)V`.

**Implementation:** two new smali patches in `patch_y1_apk.py` (broadcast on shuffle/repeat setters); Y1MediaBridge BroadcastReceivers + cold-boot SharedPreferences read; six new T7-family sub-trampolines for PDUs 0x11-0x16 (~350 B); proactive trampoline T_papp_changed for event 0x08; T1 advertises events grow to include 0x08.

**Sub-PDU detail (informative):**
- 0x11 ListPlayerAppSettingAttrs — return 2 attrs: 0x02 (Repeat), 0x03 (Shuffle). 1.3 also defines 0x01 EqualizerStatus and 0x04 ScanStatus, both optional; skip.
- 0x12 ListPlayerAppSettingValues for attr=0x02 → 4 values (off / single / all / group). For attr=0x03 → 3 values (off / all / group).
- 0x13 GetCurrentPlayerAppSettingValue — read shuffle_flag/repeat_mode from y1-track-info, return.
- 0x14 SetPlayerAppSettingValue — controller sending us shuffle/repeat. Forward as a broadcast that the music app receives → setSharedPref → broadcast loops back to us.
- 0x15/0x16 — text labels for attribute and value names. Static strings ("Repeat", "Off", etc.) shippable in LOAD #1 padding.

Plus: proactive CHANGED on shuffle/repeat changes via event 0x08, fed by the same broadcast receivers.

### Phase E — Audit + cleanup

**Already shipped:**
- **Patch E — discrete PASSTHROUGH PLAY/PAUSE/STOP per AVRCP 1.3 §4.6.1.** Splits `PlayControllerReceiver`'s short-press join arm into four labeled blocks — KEY_PLAY (85, legacy `ACTION_MEDIA_BUTTON`) keeps `playOrPause()` (toggle); KEYCODE_MEDIA_PLAY (126, from PASSTHROUGH 0x44) routes to `play(Z)V` (bool=true); KEYCODE_MEDIA_PAUSE (127, from PASSTHROUGH 0x46) routes to `pause(IZ)V` (reason=0x12, flag=true); KEYCODE_MEDIA_STOP (86, from PASSTHROUGH 0x45) routes to `stop()V` — closing **ICS Table 8 item 20 (mandatory for Cat 1 TGs)**. The `play(Z)V` boolean controls whether `Static.setPlayValue()` runs after the underlying `IjkMediaPlayer.start()` / `MediaPlayer.start()`; that singleton edge is what propagates the resume to the rest of the music app (UI, RemoteControlClient, AudioFocus state). Calling `play(false)` starts the player but skips the singleton update — other components don't see the resume edge. The Kotlin-generated `play$default(this, dummy, mask=1, null)` wrapper (used by the music app's own `playOrPause()` resume path) overrides the boolean to `1` via the default-args mask, so passing `true` here matches that behavior. Smali-level edits only.
- **U1 — disable kernel auto-repeat on the AVRCP `/dev/uinput` device.** AVRCP 1.3 §4.6.1 (PASS THROUGH command, defined in AV/C Panel Subunit Specification ref [2]) puts the periodic re-send responsibility for held buttons on the CT; the TG forwards one event per frame. Linux's `evdev` `EV_REP` soft-repeat is an Android implementation artifact that violates this layering. Fix: NOP the `blx ioctl@plt` for `UI_SET_EVBIT(EV_REP)` at file offset `0x74e8` in `libextavrcp_jni.so`'s `avrcp_input_init`. Without `EV_REP` in `dev->evbit`, Linux's `input_register_device()` skips `input_enable_softrepeat()` entirely; only the actual PASSTHROUGH PRESS frames the CT sends produce `KEY_xxx` events. Spec-correct per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec ref [2]. Stock `fd2ce74db9389980b55bccf3d8f15660` → current build `bd3554d38486856cfbb17a37c02fd0a0` (cumulative across all libextavrcp_jni.so patches including U1).

**Pending audit items (no spec gap, no estimated effort attached):**
- T1's `EventsSupported` array maintained in lock-step with what's actually implemented (each phase bumps it).
- SDP record audit: re-confirm that what we advertise in the served record matches what we actually implement post-Phase F4.
- Investigate whether `BluetoothAvrcpService.disable()` flag in MtkBt.odex (F2 patch) needs a sister patch for the new event subscriptions added in Phase F.
- mtkbt has a software-side `fftimer` (strings at `0xc8ada`, `0xc8b05`, `0xc8b2a`) that may re-fire FF/RW keys at the AVRCP layer independently of the kernel auto-repeat that U1 disables. Not exercised in any current capture; on the radar if held-button cascades reappear after U1 ships.
- **Y1 player state-code coverage in `MediaBridgeService.LogcatMonitor`.** The monitor at `MediaBridgeService.java:869` only recognizes Y1 `BaseActivity` state codes `'1'` (playing) and `'3'` (paused). Hardware captures have shown Y1 emitting `播放状态切换 5` after FF cascades terminated — likely a STOPPED state that we currently silently drop. AVRCP 1.3 §5.4.1 Table 5.26 distinguishes `STOPPED (0x00)` from `PAUSED (0x02)` as separate `PlayStatus` values; under the current code `mIsPlaying` stays `true` if Y1 transitions playing → state-5 (no-op for state 5) without an intervening state-3 (paused) hop. T6 GetPlayStatus and T9 PLAYBACK_STATUS_CHANGED would then misreport. Fix: reverse-engineer Y1's full state-code space from `BaseActivity` smali, extend the LogcatMonitor's state-char dispatch to cover STOPPED and any other AVRCP-mappable codes, and extend the `playing_flag` byte at `y1-track-info[792]` to carry the three-valued AVRCP enum (we already write `0=STOPPED, 1=PLAYING, 2=PAUSED` per the schema, but nothing currently writes 0).
- **Event 0x07 SYSTEM_STATUS_CHANGED.** T8 emits canned `0x00 POWER_ON` for the INTERIM and never fires CHANGED. This is intentional and correct: while trampolines execute, the system is by definition POWER_ON; UNPLUGGED is for accessory/dock contexts that don't apply to the Y1; POWER_OFF is unobservable from inside a process that can no longer emit responses. The canned value IS the real value. Documented here to forestall future "this is canned, fix it" audits.
- Logging cleanup: gate trampoline + Y1MediaBridge logging behind a build-time debug flag.

---

## 5. y1-track-info extended schema (cumulative across phases)

| Offset | Field | Size | Status | Source |
|---|---|---|---|---|
| 0..7 | track_id (synthetic) | 8 | shipped | `mCurrentAudioId` (MediaStore `_ID` or `syntheticAudioId(path)` fallback) |
| 8..263 | Title | 256 | shipped | `MediaStore.Audio.Media.TITLE` / `METADATA_KEY_TITLE` |
| 264..519 | Artist | 256 | shipped | `MediaStore.Audio.Media.ARTIST` / `METADATA_KEY_ARTIST` |
| 520..775 | Album | 256 | shipped | `MediaStore.Audio.Media.ALBUM` / `METADATA_KEY_ALBUM` |
| 776..779 | duration_ms (BE u32) | 4 | shipped | `MediaStore.Audio.Media.DURATION` / `METADATA_KEY_DURATION` |
| 780..783 | position_at_state_change_ms (BE u32) | 4 | shipped | `MediaBridgeService.mPositionAtStateChange` |
| 784..787 | state_change_time_sec (BE u32) | 4 | shipped | `MediaBridgeService.mStateChangeTime / 1000` (CLOCK_BOOTTIME source — T6 live-position extrapolation) |
| 788..791 | reserved | 4 | — | (pad) |
| 792 | playing_flag | 1 | shipped | `mIsPlaying` (1=PLAYING, 2=PAUSED, 0=STOPPED — AVRCP §5.4.1 Tbl 5.26) |
| 793..799 | reserved | 7 | — | (Phase C shuffle_flag/repeat_mode reservation) |
| 800..815 | TrackNumber (UTF-8 ASCII decimal) | 16 | shipped | `MediaStore.Audio.Media.TRACK % 1000` / parsed from `METADATA_KEY_CD_TRACK_NUMBER` |
| 816..831 | TotalNumberOfTracks (UTF-8 ASCII decimal) | 16 | shipped | `count(*) WHERE ALBUM_ID=?` / parsed from `CD_TRACK_NUMBER` "n/total" |
| 832..847 | PlayingTime (UTF-8 ASCII decimal ms) | 16 | shipped | derived from `duration_ms` |
| 848..1103 | Genre (UTF-8) | 256 | shipped | `MediaStore.Audio.Genres` / `METADATA_KEY_GENRE` |

Total file size: **1104 B**. Page-aligned write is still single-block. Schema bumps are append-only; we never relocate existing fields, so trampolines from earlier iters keep working (T6/T8/T9 only read up to offset 792 and are unaffected by attrs 4-7 being appended past 800).

The numeric AVRCP §5.3.4 attrs (4 / 5 / 7) are stored pre-formatted as ASCII decimal strings rather than binary u16/u32 with a Thumb-2 itoa, keeping the T4 trampoline a uniform strlen+memcpy loop.

`y1-trampoline-state` (16 B, mode 0666) is unchanged; remains the sole writable surface from the BT process side.

---

## 6. Response builder argument discovery — per function

For each response builder we plan to call, the discovery work is mechanical and follows the recipe in `ARCHITECTURE.md` §"Adding a new PDU handler". Targets:

| Function | libextavrcp.so @ | Priority |
|---|---|---|
| `get_playstatus_rsp` | `0x2354` | Phase B (high) |
| `reg_notievent_playback_rsp` | `0x23f0` | Phase A (high) |
| `reg_notievent_pos_changed_rsp` | `0x2588` | Phase A |
| `reg_notievent_player_appsettings_changed_rsp` | `0x2720` | Phase C |
| `list_player_attrs_rsp` | `0x1e24` | Phase C |
| `list_player_values_rsp` | `0x1e74` | Phase C |
| `get_curplayer_value_rsp` | `0x1ed0` | Phase C |
| `set_player_value_rsp` | `0x1f2e` | Phase C |
| `get_player_attr_text_rsp` | `0x1f58` | Phase C |
| `get_player_value_text_value_rsp` | `0x203c` | Phase C |
| `inform_charsetset_rsp` | `0x2138` | Phase C |
| `battery_status_rsp` | `0x2160` | Phase C |
| `reg_notievent_reached_end_rsp` | `0x24c8` | Phase A (low) |
| `reg_notievent_reached_start_rsp` | `0x2528` | Phase A (low) |
| `reg_notievent_battery_status_changed_rsp` | `0x25f0` | Phase A (low) |
| `reg_notievent_system_status_changed_rsp` | `0x2658` | Phase A (low) |

Each entry should produce a documented C signature in `ARCHITECTURE.md` §"Reverse-engineered semantics" before its trampoline is written. The argument shape of every response builder must be confirmed via disassembly, not inferred from arg name; the arg names in the OEM symbols don't always match the arg semantics (see ARCHITECTURE.md for the worked example on `get_element_attributes_rsp`).

---

## 7. Test/verification strategy

Per phase: a hardware capture against at least three CTs covering different policy postures. Test-matrix CT roster + per-CT empirical observations live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT". Recommended posture spread:

- **Permissive CT** — polls metadata regardless of CHANGED edges; most forgiving baseline.
- **High-subscribe-rate CT** — subscribes to TRACK_CHANGED at high frequency (regression check for AVCTP-saturation symptoms).
- **Strict CT** — gates metadata refresh on charset acknowledgement and real CHANGED edges.
- **Polling CT** — uses GetPlayStatus polling rather than RegisterNotification subscriptions (regression check for T6 / live position).
- (stretch) An iOS / WearOS CT — different polling pattern again.

For each CT, capture btlog+logcat with:
```
./tools/dual-capture.sh /work/logs/dual-<ct>-iter<N>/
```
and verify:
1. **No new NACKs.** msg=520 NOT_IMPLEMENTED count should drop to zero post-phase (or stay at zero).
2. **Expected msg=N response sizes.** Each new PDU has a known wire-shape; its outbound IPC frame size should be deterministic.
3. **EventsSupported announcement.** T1's GetCapabilities response advertises every event the current build can satisfy; CT either subscribes or it doesn't, but it shouldn't subscribe to events we can't satisfy and then NACK.
4. **Battery footprint.** dmesg-after wake-lock count and `getprop` battery stats should not regress vs the previous build. Particularly important when introducing position tracking or any new periodic timer.

The btlog parser (`tools/btlog-parse.py`) gives us full HCI command/event visibility. Any AVCTP-level NACK or rejection will surface there as a `result:` field on the relevant CNF.

---

## 8. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| New PDU response builder has an arg convention not derivable from disassembly alone | Medium | Bisect via JNI in-tree caller (most builders are called by stock JNI somewhere even if Java stack never reaches them). Failing that, ship a no-op trampoline that returns NOT_IMPLEMENTED and watch CT behavior to confirm the CT was actually probing for that PDU. |
| Phase A's PLAYBACK_POS_CHANGED proactive emit creates wakeup pressure | Low (we don't do proactive on 0x05 — only on track edge via T5) | Track-edge-only emit confirmed in plan. If a CT hard-requires periodic, gate behind a build-time flag. |
| Music-app smali patch (Phase C) breaks UI in some unanticipated way | Medium | Each patch is additive (no replacing existing logic); pre-flight test in DEX-validate pass. Each individual smali change has a `getPlayerService() == null` early-out so we never crash if init order shifts. |
| LOAD #1 extension exhausts page-padding | Very low | ~2540 B free past the current 1480 B blob; budget supports >20 more trampolines. Fallback: extend LOAD #2 padding. |
| Trampoline blob shifts every PLT call beyond range | Low (Thumb b.w covers ±16 MB; trampolines and PLT are <0x10000 apart) | Verify `bl.w`/`b.w` reach in `_thumb2asm.py` self-test for each new emit site. |
| AVRCP version negotiation: F1 patch sets MtkBt-internal version to 1.4 but our wire-shape PDU set is 1.3 | Low | F1 only flips the BlueAngel-internal flag to unblock 1.3+ command dispatch through MtkBt's Java layer. SDP record advertises AVRCP 1.3 (V1 patch) / AVCTP 1.2 (V2 patch). Per AVRCP 1.3 §6 (Service Discovery Interoperability Requirements) + ESR07 §2.1 / Erratum 4969, the served version is what CTs key against, and they negotiate a 1.3 dialogue — which is what we implement. |
| Cross-app broadcasts (Phase C music-app→Y1MediaBridge) get killed by some Android battery saver | Very low (4.2.2 has no doze; both apps are /system/app) | n/a |
| Continuation PDU 0x40/0x41 (Phase D) requires intra-session state | Medium if we need it | Probably not needed — gate Phase D entirely on whether any peer ever sends 0x40 in our captures. |
| AVCTP saturation under a CT subscribe storm drops PASSTHROUGH key-release frames; the music app then interprets the held key as a long-press, calls `startFastForward()`/`startRewind()`, and the lambda thread runs forever | Medium (high-subscribe-rate CT classes have been observed driving this — see [`INVESTIGATION.md`](INVESTIGATION.md) for per-CT empirical context) | **U1**: NOP `UI_SET_EVBIT(EV_REP)` at `libextavrcp_jni.so:0x74e8` so the kernel's `evdev` soft-repeat timer never fires on the AVRCP virtual keyboard. Without auto-repeat, a dropped PASSTHROUGH RELEASE can no longer drive the held-key cascade; the music app sees one event per actual PRESS frame the CT sends. Spec-correct per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec (CT periodic re-send during held button). |

---

## 9. Remaining effort

Phases A0/A1/B + GetElementAttributes 7-attr already shipped (see compliance scorecard in §2). Estimated effort to close remaining gaps:

| Phase | Status | Trampoline LOC | Schema bump | Music-app patch | Estimated effort |
|---|---|---|---|---|---|
| A0 — Inform PDUs + wire-shape | **shipped** | ~50 | no | no | — |
| A1 — Notification expansion | **shipped** | ~150 | yes | no | — |
| B — GetPlayStatus | **shipped** | ~80 | (with A1) | no | — |
| GetElementAttributes attrs 4-7 | **shipped** | ~140 (T4 7-attr loop) | yes (1104 B y1-track-info) | no | — |
| F1 — Track-edge events (0x03/0x04 CHANGED) | not shipped | ~30 | yes (1 flag byte) | no | 3 hours |
| F2 — Real battery state (0x06 CHANGED) | not shipped | ~50 | yes (1 byte) | no | 4 hours |
| F3 — Periodic 0x05 PLAYBACK_POS_CHANGED | not shipped | ~120 | (uses state file pad) | no | 1 day |
| F4 — PlayerApplicationSettings (0x11–0x16 + event 0x08) | not shipped | ~400 | yes (2 bytes) | yes | 5 days |
| D — Continuation (0x40–0x41) | not shipped — diagnostic shows zero CT exercises across 43 captures | ~30 | no | no | 30 min for spec-only stub |

Total remaining for full 1.3 compliance: ~7–8 days end-to-end.

---

## 10. Decision gates

Shipped phases let us short-circuit further work if compatibility is achieved:

- **A0 + A1 + B + GetElementAttributes 7-attr (shipped):** PDU 0x17 NACK closed; TRACK_CHANGED wire-correct; all 7 advertised RegisterNotification events 0x01..0x07 covered (INTERIM-only for 0x03–0x07, INTERIM + CHANGED-on-edge for 0x01 and 0x02); GetPlayStatus with live position; **GetElementAttributes packs all 7 §5.3.4 attribute IDs** (Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime). Plus discrete PASSTHROUGH PLAY/PAUSE/STOP at the music-app layer (Patch E) and kernel auto-repeat off on the AVRCP uinput device (U1). Per the ICS scorecard in §2, every mandatory row is hit.
- **Phase F (optional event CHANGED-on-edge coverage):** completes the partial-implementation rows on ICS Table 7 25-28 by shipping real-data CHANGED edges for 0x03/0x04/0x05/0x06 and PApp Settings for 0x11-0x16 + event 0x08. F4 (PApp) is rarely a metadata gate in observed CT behavior — diminishing returns relative to F1/F2/F3.
- **Phase D (Continuation):** mandatory per ICS condition C.2 but **diagnostic across all 43 captures shows zero CT exercises 0x40/0x41**. Ships as a 30-minute spec-only stub for ICS-completeness; full state machine deferred unless hardware ever surfaces 0x40 traffic.
- **Phase E (audit + cleanup):** Patch E + U1 already shipped; remaining items (SDP audit, MtkBt.odex disable() check, fftimer investigation, Y1 player state-code expansion, logging gate) are non-spec work tracked separately.

Each phase ships an incremental compliance milestone that's coherent on its own.

---

## 11. Out of scope (and why)

- **AVRCP TG group navigation (0x7d/0x7e in 1.3-Cat-3)** — falls under PASSTHROUGH which we haven't broken. No changes needed.

Anything outside the AVRCP 1.3 spec proper (V13 + ESR07) is out of scope for this project. See §1 Goal.

---

## 12. See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — proxy architecture and existing trampoline chain.
- [`PATCHES.md`](PATCHES.md) — per-patch byte detail.
- [`INVESTIGATION.md`](INVESTIGATION.md) — historical investigation including binary discovery passes and the per-iter empirical history.
- `src/patches/_trampolines.py` — current trampoline blob assembler; the file each phase will extend.
- `src/patches/_thumb2asm.py` — Thumb-2 mini-assembler; may need new instruction encodings for some Phase A/C trampolines.
