# AVRCP 1.3 Spec-Compliance Plan

A staged path from the current iter18d minimum subset (GetCapabilities + GetElementAttributes + RegisterNotification(TRACK_CHANGED) only) to a fully spec-compliant AVRCP 1.3 TG. Written 2026-05-06 in response to a strict-CT failure mode (the TG worked on permissive CTs that poll for metadata regardless, but failed on strict CTs that gate metadata refresh on charset acknowledgement and CHANGED-edge wire correctness). Per-CT empirical observations live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT".

This document is the build plan only. For why we have a proxy at all, the current trampoline chain shape, and the calling conventions of the response builders we already use, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For per-patch byte detail of what's shipped, see [`PATCHES.md`](PATCHES.md).

---

## 0. Spec target + citation discipline (iter26)

**Wire protocol target: AVRCP 1.3 (V13, adopted 16 April 2007), with ESR07 errata applied.** AVCTP 1.2 paired per §6 SDP record. Canonical PDFs (local-only — not committed because Bluetooth SIG copyright disallows redistribution; download from <https://www.bluetooth.com/specifications/specs/a-v-remote-control-profile-1-3/> and drop into `docs/spec/`, which is `.gitignore`d):

- `docs/spec/AVRCP_SPEC_V13.pdf` — base spec, 93 pages
- `docs/spec/ESR07_ESR_V10.pdf` — Errata Service Release 07 (2013-12-03); §2.1 contains the only AVRCP 1.3 erratum (Erratum 4969 — SDP record AVCTP version clarification); §2.2 covers AVRCP 1.5 errata that occasionally inform our reading of inherited 1.3 text (e.g., the 8-byte `Identifier` sentinel form in TRACK_CHANGED).
- `docs/spec/AVRCP.ICS.p17.pdf` — Implementation Conformance Statement Proforma, revision p17 (2024-07-01, TCRL.2024-1, 25 pages). Authoritative TG/CT feature M/O matrix with conditional logic. Used to anchor the scorecard in §2 below.
- `docs/spec/AVRCP.IXIT.1.6.0.pdf` — Implementation eXtra Information for Testing Proforma, version 1.6.0 (2014-09-18, 12 pages). Companion to ICS; defines per-implementation values a tester needs (timer values, parameter ranges, declared PASSTHROUGH op_id support).

**AVRCP 1.3 lifecycle status (per ICS §1.2 Table 2b, 2024-07-01):**

| Version | Status |
|---|---|
| AVRCP v1.0 | Withdrawn 2023-02-01 |
| **AVRCP v1.3 (this project's target)** | **Deprecated 2023-02-01. Withdrawn 2027-02-01.** |
| AVRCP v1.4 | Deprecated 2013-08-01. Withdrawn 2023-02-01. |
| AVRCP v1.5 | Still valid for new qualification |

We're patching a 2012 firmware that was originally qualified against AVRCP 1.0; the deprecation schedule does not block us from shipping. It does mean that **citations to "AVRCP 1.4 §X.Y" are doubly nonsensical** — 1.4 hasn't been a valid qualification target since August 2013. Future iterations should not introduce 1.4-version labels except in F1's BlueAngel-internal-flag context.

**F1 patch in `MtkBt.odex`** sets BlueAngel-internal version code 10→14 ("1.4"). This is internal flag bookkeeping inside MtkBt's Java-side dispatcher to unblock 1.3+ command handling — **it does NOT mean we implement AVRCP 1.4.** We ship zero 1.4-only PDUs (no `0x60` SetAddressedPlayer, no `0x50` SetAbsoluteVolume, no Browsing Channel `0x70..0x77`). When this doc cites "AVRCP 1.4 §X.Y", it's almost certainly historical drift from earlier rev-iterations and should be downgraded to AVRCP 1.3 §... or deleted.

**Citation hygiene rule.** Cite by **PDU name + AVRCP 1.3 section number** verified against `docs/spec/AVRCP_SPEC_V13.pdf` table-of-contents. Where a behavior comes from AV/C Panel Subunit Spec (PASS THROUGH op codes / press-release semantics), cite as `AVRCP 1.3 §4.6.1 (defined in AV/C Panel Subunit Spec, ref [2])`. Where ESR07 clarifies a 1.3 typo against the AVRCP 1.5 successor (notably the TRACK_CHANGED 8-byte Identifier sentinel), cite both: `AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 / AVRCP 1.5 §6.7.2`. Section numbers that don't appear in the spec PDF (`§5.4.3.4`, `§6.7.1`, `§6.7.2` in 1.3, `§11.1.2`, `§6.4.1.4`) are drift; replace with the verified counterpart per the table at the end of this section.

| Drift citation (do not use) | Verified citation (do use) |
|---|---|
| `AVRCP 1.4 §11.1.2` (PASSTHROUGH PLAY/PAUSE op codes) | AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec [ref 2]; concrete frame example in AVRCP 1.3 §19.3 (Appendix D) |
| `AVRCP 1.4 §6.4.1.4` (CT must periodically re-send PRESS during held button) | Same as above (lives in AV/C Panel Subunit Spec, referenced from AVRCP 1.3 §4.6.1) |
| `AVRCP 1.4 §5.4.3.4` (GetPlayStatus song_position semantics, play_status enum) | AVRCP 1.3 §5.4.1 Table 5.26 |
| `AVRCP 1.4 §6.7.1` (RegisterNotification PLAYBACK_STATUS_CHANGED) | AVRCP 1.3 §5.4.2 Table 5.29 |
| `AVRCP 1.4 §6.7.2` (TRACK_CHANGED Identifier 0xFFFFFFFF…FF sentinel) | AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 / AVRCP 1.5 §6.7.2 8-byte clarification |
| `AVRCP 1.4 §5.3` (TG must ack CT charset declaration) | AVRCP 1.3 §5.2.7 (PDU 0x17 InformDisplayableCharacterSet) |

---

## 1. Goal

Implement enough of AVRCP 1.3 that any spec-compliant AVRCP 1.3+ controller renders our metadata.

Out of scope for this plan: AVRCP 1.4 features (Browsing Channel — separate L2CAP PSM and AVCTP stream; SetAbsoluteVolume PDU 0x50; SetAddressedPlayer PDU 0x60). Reachable via the same code-cave infrastructure if ever needed, but not on this plan's path. The Browsing Channel in particular requires response builders we don't need for metadata-only delivery.

---

## 2. Coverage matrix — current vs spec

**The original "Mandatory in 1.3?" column below was a reading of the spec text without ICS conditionals.** As of iter27 we re-anchor against the **ICS Table 7 (Target Features)** in `docs/spec/AVRCP.ICS.p17.pdf` §1.5, which is the canonical M/O determination. M/O status is conditional on what other features the TG claims; the ICS encodes the conditionals explicitly. PDU = "PDU ID" byte at AV/C body offset +4. AVRCP 1.3 V13 spec sections in `docs/spec/AVRCP_SPEC_V13.pdf`.

**Our claims that drive the conditionals:** PASS THROUGH Cat 1 (V1 SDP record, ICS Table 7 item 7), GetElementAttributes Response (T4, item 20). Combining these:

| ICS Table 7 row | PDU / Capability | Spec § | ICS Status (this proj) | Currently shipped | Gap |
|---|---|---|---|---|---|
| **2** | Accepting connection establishment (control) | §4.1.1 | **M** | ✓ (mtkbt) | — |
| **3-4** | Connection release | §4.1.2 | **M** | ✓ (mtkbt) | — |
| **5** | Receiving UNIT INFO | §4.1.3 | **M** | ✓ (mtkbt) | — |
| **6** | Receiving SUBUNIT INFO | §4.1.3 | **M** | ✓ (mtkbt) | — |
| **7** | Receiving PASS THROUGH cat 1 | §4.1.3 | **M (C.1: at least one cat)** | ✓ (mtkbt + Patch E) | — |
| 8 | Receiving PASS THROUGH cat 2 | §4.1.3 | C.1: not required (cat 1 satisfies) | not claimed | optional; would unlock Absolute Volume |
| 9 | Receiving PASS THROUGH cat 3 | §4.1.3 | C.1: not required | not claimed | — |
| 10 | Receiving PASS THROUGH cat 4 | §4.1.3 | C.1: not required | not claimed | — |
| **11** | GetCapabilities Response (PDU 0x10) | §5.1.1 | **M (C.3: M IF cat 1)** | ✓ T1 | — |
| 12-15 | List/Get/Set PApp Settings (0x11–0x14) | §5.2.1–5.2.4 | C.14: M to support **none or all** | not shipped (none) | spec-compliant; Phase C ships all |
| 16-17 | PApp Setting Attribute/Value Text (0x15-0x16) | §5.2.5-5.2.6 | O | not shipped | optional; Phase C |
| 18 | InformDisplayableCharacterSet (PDU 0x17) | §5.2.7 | O | ✓ T_charset (iter19a) | — |
| 19 | InformBatteryStatusOfCT (PDU 0x18) | §5.2.8 | O | ✓ T_battery (iter19a) | — |
| **20** | GetElementAttributes (PDU 0x20) | §5.3.1 | **M (C.3: M IF cat 1)** | ✓ T4 (Title/Artist/Album, single 644-byte frame) | optional: attrs 4–7 (TrackNumber/Total/Genre/PlayingTime) |
| **21** | GetPlayStatus (PDU 0x30) | §5.4.1 | **M (C.2: M IF GetElementAttributes Response)** | ✓ T6 (iter20a + iter22d live position) | — |
| **22** | RegisterNotification (PDU 0x31) | §5.4.2 | **M (C.12: M IF cat 1)** | ✓ T2/extended_T2/T8 | — |
| **23** | Notify EVENT_PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | **M (C.4: M IF GetElementAttributes + RegisterNotification)** | ✓ T8 INTERIM (iter20b) + T9 CHANGED on edge (iter22b) | — |
| **24** | Notify EVENT_TRACK_CHANGED | §5.4.2 Tbl 5.30 | **M (C.4)** | ✓ extended_T2 INTERIM + T5 CHANGED on edge (iter17a) | — |
| 25 | Notify EVENT_TRACK_REACHED_END | §5.4.2 Tbl 5.31 | O | ✓ T8 INTERIM-only (iter20b) | optional: proactive CHANGED |
| 26 | Notify EVENT_TRACK_REACHED_START | §5.4.2 Tbl 5.32 | O | ✓ T8 INTERIM-only (iter20b) | optional |
| 27 | Notify EVENT_PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | O | ✓ T8 INTERIM-only (iter20b) | optional: proactive CHANGED via timer |
| 28 | Notify EVENT_BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | O | ✓ T8 INTERIM with canned 0x00 NORMAL | optional: real battery from Y1 sysfs |
| 29 | Notify EVENT_SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | O | ✓ T8 INTERIM with canned 0x00 POWER_ON | — |
| 30 | Notify EVENT_PLAYER_APPLICATION_SETTING_CHANGED | §5.4.2 Tbl 5.37 | O | not shipped | Phase C (paired with PApp Settings) |
| 31-32 | Continuation (PDUs 0x40/0x41) | §5.5 | C.2: M IF GetElementAttributes Response | not shipped | Phase D — mandatory but no observed CT exercises this |
| 36-58 | MediaPlayerSelection / Browsing (1.4+ PDUs 0x60+) | — | Various C requiring browsing | not shipped (1.4-only) | out of scope per Goal section |
| 60-62 | Absolute Volume (1.4+ PDUs 0x50, EVENT_VOLUME_CHANGED) | — | C.5: M IF cat 2 | not claimed | optional stretch (would require claiming cat 2) |
| **65** | Discoverable Mode | §12.1 | **M** | ✓ (mtkbt) | — |
| 66 | PASSTHROUGH operation supporting Press and Hold | §4.1.3 | O | ✓ (mtkbt + iter23 U1 disables kernel auto-repeat) | — |

**Mandatory rows: all hit.** Optional rows we ship: 18, 19, 25, 26, 27, 28, 29, 66. The Continuation gap (rows 31-32) is a real spec gap but unobserved in our CT test matrix; tracked as Phase D.

**ICS Table 8 (Cat 1 PASSTHROUGH op_ids — mandatory subset):**

| op_id | Operation | ICS status | Currently shipped |
|---|---|---|---|
| 0x44 | Play (item 19) | **M** | ✓ Patch E iter25 → `play(false)` |
| 0x45 | Stop (item 20) | **M** | ✓ Patch E iter27 → `stop()V` |
| 0x46 | Pause (item 21) | O | ✓ Patch E iter25 → `pause(0x12, true)` |
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
| 0x01 | PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | ✓ T8 (iter20b) | ✓ T9 (iter22b — Y1 play/pause broadcast) |
| 0x02 | TRACK_CHANGED | §5.4.2 Tbl 5.30 | ✓ extended_T2 (iter15) | ✓ T5 (iter17a — Y1 track-change broadcast) |
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
| **Phase E — 1.4 absolute volume** ||||
| 0x50 | set_absolute_volume_rsp | `0x3558` | `0x2950` |
| (paired notif) 0x31 event 0x0d | reg_notievent_volume_changed_rsp | `0x333c` | `0x28e8` |

**No new PLT discovery needed.** The stubs are already linked. The work that remains:

### 3a. Per-function argument-convention discovery (still required)

We already learned (the hard way, in iter11→iter13) that argument names are not what their positions suggest — the `get_element_attributes_rsp` "arg2" turned out to be attribute *index*, not transId. Each new response builder needs the same disassembly pass before its trampoline can be written. Pattern, per function:

1. `objdump -d --start-address=<libextavrcp.so addr> --stop-address=<+0x100>` to dump the function body.
2. Look for `ldrb rN, [r0, #17]` — that's transId being auto-extracted from `conn[17]`. If present, `transId` is *not* an arg.
3. Locate the call to `AVRCP_SendMessage` (at `libextavrcp.so:0x18ec`). Walk backwards to see the buffer-build loop and the conditional that decides whether to emit (vs accumulate).
4. Cross-reference with any in-tree caller in `libextavrcp_jni.so` (most response builders have at least one stock JNI caller — those reveal the OEM's intended arg shape). Search `objdump -d libextavrcp_jni.so | grep -B5 "blx <…@plt>"`.
5. Document the resulting C signature in `ARCHITECTURE.md`, same format as `get_element_attributes_rsp`.

Estimated effort per function: 30 min for simple ones, 2 hours for the multi-arg accumulator-style ones. Total Phase A→E discovery: ~1 day of focused work.

### 3b. Code-cave budget

LOAD #1 padding currently used by iter17b's blob: `0xac54..0xaf4c` (760 B). Free space past `0xaf4c` to LOAD #2 at `0xbc08`: **3,260 bytes**. New trampolines average ~80 bytes each; budget supports ~40 more trampolines. We're not space-constrained.

If we ever do exhaust LOAD #1 padding, we have a known fallback: extend the trick to the LOAD #2 padding region by bumping LOAD #2's `p_filesz`/`p_memsz`. Not needed for this plan.

---

## 4. Implementation phases

Each phase is independent and ship-able on its own. Order is by expected user impact + prerequisite chain. Phases were re-factored 2026-05-06 after a strict-CT capture established that PDU 0x17 InformDisplayableCharacterSet was the dominant blocker — splitting Phase A0 (Inform PDUs + the wire-shape correctness fix for our existing TRACK_CHANGED implementation) out of the original Phase A/C buckets gives a coherent compliance unit small enough to ship in hours rather than days.

### Phase A0 — Inform PDUs + TRACK_CHANGED wire-shape fix (iter19)

**Why first:** Smallest coherent compliance slice that closes a real-world CT failure. PDUs 0x17 and 0x18 are the spec's "CT→TG informational" pair (CT tells the TG a fact, TG acks); they share a near-identical 8-byte ack-frame response shape and don't require Y1MediaBridge data plumbing or music-app patches. Plus this phase fixes the existing T2/T5 wire-shape regression (passing `r1=transId` hits the response builder's reject-shape path; should pass `r1=0`) so existing TRACK_CHANGED notifications go out spec-correct.

**What it adds:**
- **T_charset trampoline** for PDU 0x17 InformDisplayableCharacterSet → calls `inform_charsetset_rsp` via PLT 0x3588 with `r1=0` (success).
- **T_battery trampoline** for PDU 0x18 InformBatteryStatusOfCT → calls `battery_status_rsp` via PLT 0x357c with `r1=0` (success).
- **T2/T5 r1 fix**: replace `ldrb.w r1, [sp, #368]` (transId) with `movs r1, #0` (success path). Saves 2 bytes per site.

**Strict-CT capture confirms 0x17 is the blocker:** the strict-CT capture (see [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the relevant CT and the log path) shows the CT sending PDU 0x17 once at connection setup; we currently NACK with msg=520; afterwards the CT registers TRACK_CHANGED 30 times but only ever issues a single GetElementAttributes — consistent with both "the TG won't acknowledge my charset declaration so I distrust subsequent metadata" and "your CHANGED notifications are reject-shaped so I'm not re-fetching." iter19 fixes both.

**No Y1MediaBridge changes. No music-app patches.** The Inform PDUs are pure CT→TG informational acks with no data flow back to the Y1.

**Files touched:**
- `src/patches/_trampolines.py` — add T_charset + T_battery emitters; replace `r1=transId` with `r1=0` in T2 and T5 emitters (~80 lines)
- `src/patches/patch_libextavrcp_jni.py` — bump LOAD #1 filesz/memsz (~5 lines)

**Estimated effort:** 2 hours.

**Compliance delta:** mandatory PDUs handled goes 3→5; PDUs spec-correct goes 2→5 (both inform PDUs added + TRACK_CHANGED's existing implementation made spec-correct).

### Phase A1 — Notification expansion (T8 + T9) — SHIPPED iter20b / iter22b

**Status:** Implemented in iter20b (T8 INTERIM dispatcher) + iter22b (T9 proactive CHANGED for event 0x01). Reproducible build at `fdb50b8a569dbef038424e82ceeed882`. Hardware verification status per CT lives in [`INVESTIGATION.md`](INVESTIGATION.md).

**Final implementation:** T8 trampoline branched from extended_T2's "PDU 0x31 + event ≠ 0x02" arm (replaces the previous fall-through to "unknow indication"). T8 allocates an 800 B stack frame, reads `y1-track-info` (for events 0x01/0x05 which carry payloads from the iter20a schema), then dispatches on `event_id` and emits an INTERIM via the matching `reg_notievent_*_rsp` PLT:

| event_id | PLT | payload | source |
|---|---|---|---|
| 0x01 PLAYBACK_STATUS_CHANGED | 0x339c | u8 play_status | y1-track-info[792] |
| 0x03 TRACK_REACHED_END | 0x3378 | (none) | — |
| 0x04 TRACK_REACHED_START | 0x336c | (none) | — |
| 0x05 PLAYBACK_POS_CHANGED | 0x3360 | u32 position_ms | y1-track-info[780..783] (REV-swapped) |
| 0x06 BATT_STATUS_CHANGED | 0x3354 | u8 canned `0x00 NORMAL` | — |
| 0x07 SYSTEM_STATUS_CHANGED | 0x3348 | u8 canned `0x00 POWERED_ON` | — |

INTERIM coverage as above. **iter22b adds proactive CHANGED for event 0x01 PLAYBACK_STATUS_CHANGED via T9** — structurally a clone of T5 (the iter17a TRACK_CHANGED proactive trampoline). T9 is invoked by the patched `notificationPlayStatusChangedNative` (file offset 0x3c88, stock prologue `2D E9 F3 41` overwritten with `b.w T9`), which fires on every Y1MediaBridge `playstatechanged` broadcast once the matching MtkBt cardinality NOP at 0x3c4fe (sswitch_18a, event 0x01 case) is in place. T9 reads `y1-track-info[792]` (current play_status), compares against `y1-trampoline-state[9]` (`last_play_status` — previously pad), emits `reg_notievent_playback_rsp(conn, 0, REASON_CHANGED, play_status)` via PLT 0x339c on edge, and writes the new value back. transId is auto-extracted from conn[17] by the response builder (same convention T5 uses for track_changed_rsp). Closes the §6.7.1 spec gap for event 0x01. Position (event 0x05) and the other events stay INTERIM-only — proactive CHANGED for 0x05 would need a periodic timer (no broadcast equivalent), and 0x03/0x04/0x06/0x07 don't have natural Y1-side edge sources.

**T1 `EventsSupported` expansion:** events array advertised in `GetCapabilities(0x03)` responses goes from `[0x02]` count=1 to `[0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07]` count=7. Two byte-edits in `T1_TRAMPOLINE`. Per the spec-compliance feedback rule, advertise only what's implemented — event 0x08 (PLAYER_APPLICATION_SETTING_CHANGED) stays unadvertised until Phase C.

**Schema dependency:** Y1MediaBridge schema fields needed by T8 (`play_status` at offset 792, `position_at_state_change_ms` at offsets 780..783) were already added in iter20a — no further Y1MediaBridge changes for iter20b.

**Files touched (cumulative iter20b + iter22b):**
- `src/patches/_trampolines.py` — `_emit_t8` (iter20b, ~210 B INTERIM dispatcher), `_emit_t9` (iter22b, ~190 B T5-shaped proactive CHANGED for event 0x01); modified `extended_T2`'s unknown-event arm to bridge to T8; new `T8_*` and `T9_*` frame constants; 6 new `PLT_reg_notievent_*_rsp` constants; `BATT_STATUS_NORMAL` / `SYSTEM_STATUS_POWERED` canned-value constants.
- `src/patches/patch_libextavrcp_jni.py` — `T1_TRAMPOLINE` events count `1→7` + events array (iter20b); new `NATIVE_PLAY_STATUS_CHANGED_VADDR=0x3c88` hook + `_native_play_status_changed_stub` (iter22b); `OUTPUT_MD5` bumped from `28d0129cedeb06e7ba233190f92eefde` (iter20b) to `fdb50b8a569dbef038424e82ceeed882` (iter22b).
- `src/patches/patch_mtkbt_odex.py` — new `[iter22b]` patch entry NOPing `if-eqz v5, :cond_184` at 0x3c4fe (sswitch_18a / event 0x01); `OUTPUT_MD5` bumped from `ca23da7a4d55365e5bcf9245a48eb675` (iter17a) to `fa2e34b178bee4dfae4a142bc5c1b701` (iter22b).

**Compliance scorecard delta:** PDU 0x31 event coverage 1/8 → 7/8 (iter20b). T1 `EventsSupported` matches actual coverage. Event 0x01 now ships INTERIM + proactive CHANGED-on-edge (iter22b), matching event 0x02 TRACK_CHANGED's iter15+ behavior. Spec compliance for event 0x01 §6.7.1 closed.

### Phase B — GetPlayStatus (T6) — SHIPPED iter20a

**Status:** Implemented in iter20a. Reproducible build at `52b1bb70c4edc975ec56c63067c454fb`. Awaiting hardware verification.

**Final implementation:** T6 branched from T4's pre-check on PDU 0x30 (alongside the existing 0x20/0x17/0x18 dispatch). Reads y1-track-info[776..795] for `duration_ms` / `position_at_state_change_ms` / `state_change_time_sec` (reserved) / `playing_flag` — all stored big-endian to match the existing track_id encoding, byte-swapped to host order via the new Thumb-2 `REV` instruction (`rev_lo_lo` added to `_thumb2asm.py`). Calls `get_playstatus_rsp` via PLT 0x3564 with `arg1=0` + duration + position + play_status.

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

### Phase C — PlayerApplicationSettings (T7 family)

**Why third:** Spec-mandated but rarely a metadata gate. Defer until Phases A0+A1+B don't fix the next strict CT. Largest single phase by code volume.

**What it adds:** A T7 trampoline that branches into 6 sub-trampolines for PDUs 0x11–0x16 (the configurational sub-set; 0x17 and 0x18 are now in Phase A0). Each sub-trampoline reads from y1-track-info extended fields (shuffle / repeat / ...), constructs the appropriate response, and calls the matching PLT.

**Music-app patches needed (`patch_y1_apk.py`):**
- Hook `SharedPreferencesUtils.setShuffle(Z)V` to broadcast `com.y1.mediabridge.SHUFFLE_CHANGED` with `extra:bool`. ~10 smali instructions.
- Hook `SharedPreferencesUtils.setRepeatMode(I)V` to broadcast `com.y1.mediabridge.REPEAT_CHANGED` with `extra:int`. ~10 smali instructions.

**Y1MediaBridge additions:**
- Two new `BroadcastReceiver`s; bump y1-track-info schema with `shuffle_flag` u8 + `repeat_mode` u8.
- Initial values read at service startup by reading `com.innioasis.y1`'s SharedPreferences XML (since broadcasts only fire on changes and we need the cold-boot value).

**Sub-PDU detail (informative):**
- 0x11 ListPlayerAppSettingAttrs — return 2 attrs: 0x02 (Repeat), 0x03 (Shuffle). 1.3 also defines 0x01 EqualizerStatus and 0x04 ScanStatus, both optional; skip.
- 0x12 ListPlayerAppSettingValues for attr=0x02 → 4 values (off / single / all / group). For attr=0x03 → 3 values (off / all / group).
- 0x13 GetCurrentPlayerAppSettingValue — read shuffle_flag/repeat_mode from y1-track-info, return.
- 0x14 SetPlayerAppSettingValue — controller sending us shuffle/repeat. Forward as a broadcast that the music app receives → setSharedPref → broadcast loops back to us. Or: directly write SharedPreferences via root-helper. (Path TBD; simpler is the broadcast roundtrip.)
- 0x15/0x16 — text labels for attribute and value names. Static strings ("Repeat", "Off", etc.) shippable in LOAD #1 padding.
- 0x17 InformDisplayableCharacterSet — receive CT's accepted charsets. We currently ignore; respond with bare ack and continue sending UTF-8.
- 0x18 InformBatteryStatusOfCT — receive CT's battery state. Respond with bare ack.

Plus: proactive CHANGED on shuffle/repeat changes via event 0x08, fed by the same broadcast receivers.

**Files touched:**
- `src/patches/_trampolines.py` — T7 family (~400 lines, the largest single addition)
- `src/patches/patch_libextavrcp_jni.py` — filesz bump
- `src/patches/patch_y1_apk.py` — two new smali patches (D, E for shuffle/repeat)
- `src/Y1MediaBridge/.../MediaBridgeService.java` — receivers + schema (~120 lines)
- `src/Y1MediaBridge/app/src/main/AndroidManifest.xml` — receiver entries (or runtime-register)
- `T1 update` — `EventsSupported` grows to include 0x08

**Estimated effort:** 5-7 days. Volume + spec-shape + cross-app IPC + 8 sub-PDUs each needing arg-discovery.

### Phase D — Continuation PDUs (T9)

**Why fourth:** Spec-mandated but only relevant if any single response goes >512 bytes. We currently ship 644-byte msg=540 frames packed at the IPC layer; mtkbt likely fragments these to AVCTP under the hood, but we should verify.

**Diagnostic step before implementing:** check if any peer ever sends 0x40/0x41 to us in any capture. If never, demote this to "spec-only; no real-world impact" and document.

**If implemented:** T9 handles 0x40/0x41 by re-emitting the buffered response (continuation) or zeroing it (abort). Requires carrying state across PDU dispatches — first time we'd persist intra-AVCTP state in the trampolines.

**Estimated effort:** 2-3 days. May not be needed.

### Phase E — Audit + cleanup

- **Patch E v2 — discrete PASSTHROUGH PLAY/PAUSE per AVRCP 1.3 §4.6.1 — SHIPPED iter25.** PASSTHROUGH op codes + press/release behavior live in AV/C Panel Subunit Spec (ref [2] of AVRCP 1.3); §4.6.1 in AVRCP 1.3 references that spec. Concrete frame example in AVRCP 1.3 §19.3 (Appendix D, informative) shows op_id 0x44 PLAY with state_flag = 0 / 1 for press / release. Spec semantic: PLAY transitions to PLAYING from any state; PAUSE transitions to PAUSED from any state. iter22d's first cut wired `KEY_PLAY` (85) + `KEYCODE_MEDIA_PLAY` (126) + `KEYCODE_MEDIA_PAUSE` (127) all through `PlayerService.playOrPause()` (toggle), arguing that toggle is no-op when target state matches current state. `dual-bolt-iter23` capture refuted that: Bolt EV's strict CT issues discrete PLAY (PASSTHROUGH 0x44 → KEYCODE_MEDIA_PLAY 126) when it wants PLAYING; the toggle inverted Bolt's intent on each press while Y1 was already PLAYING. iter25 splits the join arm into three labeled blocks — KEY_PLAY (legacy `ACTION_MEDIA_BUTTON` toggle) keeps `playOrPause()`; KEYCODE_MEDIA_PLAY routes to `play(Z)V` (bool=false); KEYCODE_MEDIA_PAUSE routes to `pause(IZ)V` (reason=0x12 diagnostic tag, flag=true matching every observed `pause$default` callsite's resolved arg). Smali-level edit only, in `patch_y1_apk.py`'s Patch E block.
- **U1 — disable kernel auto-repeat on the AVRCP `/dev/uinput` device — SHIPPED iter23.** AVRCP 1.3 §4.6.1 (PASS THROUGH command, defined in AV/C Panel Subunit Specification ref [2]) puts the periodic re-send responsibility for held buttons on the CT; the TG forwards one event per frame. Linux's `evdev` `EV_REP` soft-repeat is an Android implementation artifact that violates this layering — it synthesizes ~25 Hz `KEY_xxx REPEAT` events whenever a `DOWN` arrives without a matching `UP`, which happens whenever a CT-side PASSTHROUGH RELEASE is dropped on a saturated AVCTP channel. Fix: NOP the `blx ioctl@plt` for `UI_SET_EVBIT(EV_REP)` at file offset `0x74e8` in `libextavrcp_jni.so`'s `avrcp_input_init` (real body at `0x73c8`). Without `EV_REP` in `dev->evbit`, Linux's `input_register_device()` skips `input_enable_softrepeat()` entirely; only the actual PASSTHROUGH PRESS frames the CT sends produce `KEY_xxx` events. Confirmed source via `getevent -lt` on iter22d hardware (`/work/logs/dual-tv-iter22d-vibloop/`): single `KEY_NEXTSONG DOWN` → 458 `KEY_NEXTSONG REPEAT` events at strict 40 ms intervals; mtkbt boundary remained at strict 1:1 PASSTHROUGH-PRESS-to-KEY_INFO ratio. Stock `fd2ce74db9389980b55bccf3d8f15660` → `e920b136fdf28b95d95d17ae6e383709`.
- T1's `EventsSupported` array maintained in lock-step with what's actually implemented (each phase bumps it).
- SDP record audit: re-confirm that what we advertise in the served record matches what we actually implement post-Phase A/B/C.
- Optional 1.4 absolute volume support (PDU 0x50 + event 0x0d). Trivial trampoline (T10), no Y1MediaBridge schema change (volume is system-level). Useful for cars that use Bluetooth for hands-free where AVRCP volume changes the phone's media volume.
- Investigate whether `BluetoothAvrcpService.disable()` flag in MtkBt.odex (F2 patch) needs a sister patch for the new event subscriptions.
- mtkbt has a software-side `fftimer` (strings at `0xc8ada`, `0xc8b05`, `0xc8b2a`) that may re-fire FF/RW keys at the AVRCP layer independently of the kernel auto-repeat that U1 disables. Not exercised in any current capture; on the radar if held-button cascades reappear after U1 ships.
- **Y1 player state-code coverage in `MediaBridgeService.LogcatMonitor`.** The monitor at `MediaBridgeService.java:869` only recognizes Y1 `BaseActivity` state codes `'1'` (playing) and `'3'` (paused). Earlier captures (notably `/work/logs/dual-tv-iter21/`) showed Y1 emitting `播放状态切换 5` after FF cascades terminated — likely a STOPPED state that we silently drop. AVRCP 1.3 §5.4.1 Table 5.26 distinguishes `STOPPED (0x00)` from `PAUSED (0x02)` as separate `PlayStatus` values; under the current code `mIsPlaying` stays `true` if Y1 transitions playing → state-5 (no-op for state 5) without an intervening state-3 (paused) hop. T6 GetPlayStatus and T9 PLAYBACK_STATUS_CHANGED would then misreport. Fix: reverse-engineer Y1's full state-code space from `BaseActivity` smali, extend the LogcatMonitor's state-char dispatch to cover STOPPED and any other AVRCP-mappable codes, and extend the `playing_flag` byte at `y1-track-info[792]` to carry the three-valued AVRCP enum (we already write `0=STOPPED, 1=PLAYING, 2=PAUSED` per the schema, but nothing currently writes 0).
- Logging cleanup: gate trampoline + Y1MediaBridge logging behind a build-time debug flag (already noted in `project_y1_mods_status.md` post-release work).

**Estimated effort:** 1-2 days.

---

## 5. y1-track-info extended schema (cumulative across phases)

| Offset | Field | Size | Phase | Source |
|---|---|---|---|---|
| 0..7 | track_id (synthetic) | 8 | shipped | iter18d |
| 8..263 | Title | 256 | shipped | iter14b |
| 264..519 | Artist | 256 | shipped | iter14b |
| 520..775 | Album | 256 | shipped | iter14b |
| 776..779 | duration_ms | 4 | A or B | `MediaMetadataRetriever.METADATA_KEY_DURATION` |
| 780..783 | position_at_state_change_ms | 4 | A | `MediaBridgeService.mPositionAtStateChange` |
| 784..791 | state_change_time_elapsed_ms | 8 | A | `MediaBridgeService.mStateChangeTime` |
| 792 | playing_flag | 1 | A | `mIsPlaying` (1=playing, 2=paused, 0=stopped) |
| 793 | shuffle_flag | 1 | C | broadcast from music app |
| 794 | repeat_mode | 1 | C | broadcast from music app |
| 795..796 | track_number | 2 | (optional) | broadcast from music app — `PlayerService.musicIndex+1` |
| 797..798 | total_tracks | 2 | (optional) | broadcast from music app — `PlayerService.musicList.size()` |
| 799..1054 | Genre | 256 | (optional) | `MediaMetadataRetriever.METADATA_KEY_GENRE` |

Total file size grows from 776 B to up to **1055 B**. Page-aligned write is still single-block. Schema bumps are append-only; we never relocate existing fields, so trampolines from earlier iters keep working.

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

Each entry should produce a documented C signature in `ARCHITECTURE.md` §"Reverse-engineered semantics" before its trampoline is written. Skipping this step is what caused the iter11→iter13 thrash; not skipping it again.

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
4. **Battery footprint.** dmesg-after wake-lock count and `getprop` battery stats should not regress vs iter18d baseline. Particularly important after Phase A introduces position tracking.

The btlog parser (`tools/btlog-parse.py`) gives us full HCI command/event visibility. Any AVCTP-level NACK or rejection will surface there as a `result:` field on the relevant CNF.

---

## 8. Risk register

| Risk | Likelihood | Mitigation |
|---|---|---|
| New PDU response builder has an arg convention not derivable from disassembly alone | Medium | Bisect via JNI in-tree caller (most builders are called by stock JNI somewhere even if Java stack never reaches them). Failing that, ship a no-op trampoline that returns NOT_IMPLEMENTED and watch CT behavior to confirm the CT was actually probing for that PDU. |
| Phase A's PLAYBACK_POS_CHANGED proactive emit creates wakeup pressure | Low (we don't do proactive on 0x05 — only on track edge via T5) | Track-edge-only emit confirmed in plan. If a CT hard-requires periodic, gate behind a build-time flag. |
| Music-app smali patch (Phase C) breaks UI in some unanticipated way | Medium | Each patch is additive (no replacing existing logic); pre-flight test in DEX-validate pass. Each individual smali change has a `getPlayerService() == null` early-out so we never crash if init order shifts. |
| LOAD #1 extension exhausts page-padding | Very low | 3,260 B free post-iter17b; budget supports >40 trampolines. Fallback: extend LOAD #2 padding. |
| Trampoline blob shifts every PLT call beyond range | Low (Thumb b.w covers ±16 MB; trampolines and PLT are <0x10000 apart) | Verify `bl.w`/`b.w` reach in `_thumb2asm.py` self-test for each new emit site. |
| AVRCP version negotiation: F1 patch sets MtkBt-internal version to 1.4 but our wire-shape PDU set is 1.3 | Low | F1 only flips the BlueAngel-internal flag to unblock 1.3+ command dispatch through MtkBt's Java layer. SDP record advertises AVRCP 1.3 (V1 patch) / AVCTP 1.2 (V2 patch). Per AVRCP 1.3 §6 (Service Discovery Interoperability Requirements) + ESR07 §2.1 / Erratum 4969, the served version is what CTs key against, and they negotiate a 1.3 dialogue — which is what we implement. |
| Cross-app broadcasts (Phase C music-app→Y1MediaBridge) get killed by some Android battery saver | Very low (4.2.2 has no doze; both apps are /system/app) | n/a |
| Continuation PDU 0x40/0x41 (Phase D) requires intra-session state | Medium if we need it | Probably not needed — gate Phase D entirely on whether any peer ever sends 0x40 in our captures. |
| AVCTP saturation under a CT subscribe storm drops PASSTHROUGH key-release frames; the music app then interprets the held key as a long-press, calls `startFastForward()`/`startRewind()`, and the lambda thread runs forever | Medium (observed under iter19b real-track_id and iter19c/iter20b sentinel; reduced but not eliminated by Phase A1's 7-event fan-out — see [`INVESTIGATION.md`](INVESTIGATION.md) for the per-CT empirical context) | **iter23 / U1**: NOP `UI_SET_EVBIT(EV_REP)` at `libextavrcp_jni.so:0x74e8` so the kernel's `evdev` soft-repeat timer never fires on the AVRCP virtual keyboard. Without auto-repeat, a dropped PASSTHROUGH RELEASE can no longer drive the held-key cascade; the music app sees one event per actual PRESS frame the CT sends. Spec-correct per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec (CT periodic re-send during held button). *(Earlier iter21 / Patch D capped the in-app seek loop at 5 s as a band-aid; reverted in iter24 because it also bounded local hardware-button hold-FF/RW, breaking long scrubs.)* |

---

## 9. Effort summary

| Phase | iter | Trampoline LOC | Schema bump | Music-app patch | New docs | Estimated effort |
|---|---|---|---|---|---|---|
| A0 — Inform PDUs + wire-shape | iter19 | ~50 | no | no | ARCH update | 2 hours |
| A1 — Notifications | iter20 | ~150 | yes | no | ARCH update | 2–3 days |
| B — GetPlayStatus | iter20 (paired) | ~80 | (rolled into A1) | no | ARCH update | 1–2 days |
| C — PlayerAppSettings (0x11–0x16) | iter22 (was iter21) | ~350 | yes | yes | patch_y1_apk.py docstring + ARCH | 5–7 days |
| D — Continuation | iter23 (was iter22) | ~200 | no | no | ARCH update | 2–3 days (skip if not needed) |
| E — Audit | iter23 (paired) | ~80 (T10 abs-vol) | no | no | PATCHES.md sync | 1–2 days |
| ~~Defensive: bound music-app FF/RW hold-loop~~ | ~~iter21~~ (reverted iter24; superseded by iter23/U1) | ~~0 (smali only)~~ | no | ~~yes (Patch D)~~ | CHANGELOG + PATCHES.md | 1 day |
| **Total** | iter19–iter22 | **~860** | two schema bumps | two new smali patches | three doc updates | **11–17 days** |

Compared with the cumulative effort from iter1 through iter18d (already shipped: ~6 weeks of focused work for the metadata core), full 1.3 compliance is a 2–3 week extension on top, not a re-architecture. The trampoline chain pattern scales linearly with PDU count.

---

## 10. Decision gates

These let us short-circuit phases when a CT actually works:

- **After Phase A0 (iter19):** retest the strict-CT class. PDU 0x17 NACK is closed and TRACK_CHANGED is now spec-correct on the wire — most likely fixes strict-CT failures directly. If yes → defer A1+B as the next compliance increment rather than urgent fixes.
- **After Phase A1+B (iter20):** retest against any new strict CT that surfaced. By this point we cover all 8 RegisterNotification events the spec mandates plus GetPlayStatus, which together account for the bulk of CT compatibility issues we know about.
- **After Phase C (iter22, was iter21):** PApp Settings is mostly spec-completeness; few CTs gate metadata behind it. Diminishing returns from here.
- **After Phase D+E (iter23, was iter22):** full 1.3 spec compliance achieved.

> Phase numbering after iter21 slid by one because iter21 was repurposed mid-stream from "Phase C music-app patch" into a defensive bound on the FF/RW hold-loop after dropped-PASSTHROUGH-release symptoms (see [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT") forced it ahead of compliance work. Phase C is now scheduled as iter22+.

We don't have to commit to the full 1.3 build up front. Each iter ships an incremental compliance milestone that's coherent on its own.

---

## 11. Out of scope (and why)

- **AVRCP 1.4 browsing channel** (out-of-scope for AVRCP 1.3; listed for reference only) — separate L2CAP PSM, separate AVCTP stream, requires `set_browsedplayer_rsp`/`get_folderitems_rsp`/`change_path_rsp`/`get_itemattributes_rsp`/`play_items_rsp`/`add_tonowplaying_rsp`/`search_rsp` (PLT stubs all exist; calling convention work is non-trivial). Useful for cars that show a media library view, but our use case is "what's playing right now," which is the metadata channel only. Defer.
- **AVRCP 1.5 cover art (BIP)** — separate OBEX channel, separate PSM, image-encoder integration. Y1 has cover art in MediaStore but pushing it over BIP is a different protocol entirely. Out of scope.
- **AVRCP TG group navigation (0x7d/0x7e in 1.3-Cat-3)** — falls under PASSTHROUGH which we haven't broken. No changes needed.
- **Browsing-channel SDP advertisement** — currently absent from our served record. Adding it would invite browse-channel probes we can't answer. Leave as-is.

---

## 12. See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — proxy architecture and existing trampoline chain.
- [`PATCHES.md`](PATCHES.md) — per-patch byte detail.
- [`INVESTIGATION.md`](INVESTIGATION.md) — historical investigation including binary discovery passes (iter5–iter13 proxy bring-up empirical history).
- `src/patches/_trampolines.py` — current trampoline blob assembler; the file each phase will extend.
- `src/patches/_thumb2asm.py` — Thumb-2 mini-assembler; may need new instruction encodings for some Phase A/C trampolines.
