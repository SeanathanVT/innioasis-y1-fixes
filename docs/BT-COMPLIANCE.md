# Bluetooth profile-stack spec compliance

Current spec compliance state across the Bluetooth profile stack the Y1 talks to peer Controllers (CTs) over. **AVRCP 1.3 is the primary target** — full ICS Table 7 scorecard with M/O accounting in §2 — because that's where most of the Y1's stock behaviour was non-conformant. **Adjacent profiles (A2DP, AVDTP, AVCTP, GAVDP) are in scope when their behaviour bleeds into AVRCP-level CT interop**, with targeted fixes per §9 rather than full per-profile compliance scoreboards. Per-CT empirical observations behind each design choice live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT".

For why we have a proxy at all, the current trampoline chain shape, and the calling conventions of the response builders we use, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For per-patch byte-level reference, see [`PATCHES.md`](PATCHES.md).

---

## 0. Spec target + citation discipline

**Primary wire protocol target: AVRCP 1.3 (V13, adopted 16 April 2007), with ESR07 errata applied.** AVCTP 1.2 paired per §6 SDP record. Canonical PDFs live under `docs/spec/<PROFILE>/` (gitignored — Bluetooth SIG copyright disallows redistribution; download from <https://www.bluetooth.com/specifications/specs/>):

**AVRCP 1.3 (primary):**
- `docs/spec/AVRCP 1.3/AVRCP_SPEC_V13.pdf` — base spec, 93 pages.
- `docs/spec/AVRCP 1.3/ESR07_ESR_V10.pdf` — Errata Service Release 07 (2013-12-03); §2.1 contains Erratum 4969 (SDP record AVCTP version clarification, the only formal 1.3 erratum); §2.2 carries supplementary clarifications that resolve printed typos in 1.3 wire-format tables (e.g., the 8-byte `Identifier` sentinel form for TRACK_CHANGED).
- `docs/spec/AVRCP 1.3/AVRCP.ICS.p17.pdf` — Implementation Conformance Statement Proforma, revision p17 (2024-07-01, TCRL.2024-1, 25 pages). Authoritative TG/CT feature M/O matrix with conditional logic. Used to anchor the scorecard in §2 below.
- `docs/spec/AVRCP 1.3/AVRCP.IXIT.1.6.0.pdf` — IXIT proforma. Companion to ICS; defines per-implementation values a tester needs (timer values, parameter ranges, declared PASSTHROUGH op_id support).

**AVCTP 1.2 (paired, §6 + ESR07 §2.1 / Erratum 4969):**
- `docs/spec/AVCTP 1.2/AVCTP_SPEC_V12.pdf` — base spec.
- `docs/spec/AVCTP 1.2/AVCTP.ICS.p10.pdf` — ICS proforma.
- `docs/spec/AVCTP 1.2/AVCTP.TS.p12.pdf` — Test Specification.

**A2DP / AVDTP / GAVDP triad (audio path, §9 references):**
- `docs/spec/A2DP 1.3/A2DP_SPEC_V13.pdf` + `ESR08_V1.0.0.pdf`.
- `docs/spec/AVDTP 1.3/AVDTP_SPEC_V13.pdf` + `AVDTP.ICS.p14.pdf` + `AVDTP.TS.p23.pdf` + `Erratum_23224_Update_Conformance_Section.pdf`.
- `docs/spec/GAVDP 1.3/GAVDP_SPEC_V13.pdf` + `GAVDP.ICS.p12.pdf` + `GAVDP.TS_.p8.pdf` + `Erratum_23224_Update_Conformance_Section.pdf`.

Per ICS §1.2 Table 2b, AVRCP 1.3 was deprecated 2023-02-01 and is scheduled for withdrawal 2027-02-01. We're patching a 2012 firmware that was originally qualified against AVRCP 1.0; the deprecation schedule does not block us from shipping.

**Citation hygiene rule.** Cite by **PDU name + AVRCP 1.3 section number** verified against the spec PDF's table-of-contents. Where a behavior comes from AV/C Panel Subunit Spec (PASS THROUGH op codes / press-release semantics), cite as `AVRCP 1.3 §4.6.1 (defined in AV/C Panel Subunit Spec, ref [2])`. Where the spec text contains a printed typo, cite ESR07's clarification: `AVRCP 1.3 §X.Y + ESR07 §2.2`. Section numbers must appear in the spec PDF's table of contents — anything else is a citation error. Adjacent-profile citations follow the same shape: `A2DP 1.3 §X.Y`, `AVDTP 1.3 §X.Y`, `AVCTP 1.2 §X.Y`, `GAVDP 1.3 §X.Y`.

---

## 1. Current coverage state

**AVRCP 1.3 (primary target).** The goal is AVRCP 1.3 spec-completeness so any spec-compliant 1.3+ controller renders our metadata. Scope is the latest revision of the AVRCP 1.3 spec (V13 + ESR07). Anything outside that revision is out of scope for the AVRCP scoreboard.

The one carry-out from outside the 1.3 spec proper is `MtkBt.odex` patch F1's BlueAngel-internal `getPreferVersion()` value — internal flag bookkeeping inside MtkBt's Java-side dispatcher that unblocks 1.3+ command handling on a stack that was originally compiled against AVRCP 1.0. F1 sets the flag and unblocks 1.3 dispatch; nothing in our wire shape is changed by it.

**Lower BT profile stack (adjacent).** A2DP, AVDTP, AVCTP, GAVDP fixes are filed in §9. Goal: spec-completeness where tractable on this hardware (every Optional row we can implement, not just observed-deviation fixes). Each entry in §9 has motivation, scope (or defer-with-rationale), and a verification path. Currently shipped: §9.2 (A2DP HAL coupling), §9.3 (AVRCP attribute cap), §9.6 (U1 AVCTP subscribe-storm mitigation), §9.7 (V2 SDP AVCTP version correction).

**Closed:** every Mandatory row of ICS Table 7, plus every Optional row except 12-17 + 30 (PlayerApplicationSettings).

**Deferred — PlayerApplicationSettings (PDUs 0x11-0x16 + event 0x08, ICS Table 7 rows 12-17 + 30, all Optional).** Condition C.14 demands all-or-none for 0x11-0x14, so partial shipping is non-conformant; going to "all" is the threshold. The implementation requires (a) Y1 APK smali patches that inject `sendBroadcast` calls into static-ish methods (`SharedPreferencesUtils.setMusicIsShuffle` / `setMusicRepeatMode`) where there is no Context handle, requiring `getApp()`-injection chains; (b) Y1MediaBridge cross-package SharedPreferences plumbing for cold-boot reads; (c) at minimum 4 sub-trampolines for 0x11-0x14 plus 0x15/0x16 text-label trampolines and event 0x08 proactive emission for full ICS coverage, where the PLT calling conventions for `list_player_attrs_rsp`, `list_player_values_rsp`, `get_curplayer_value_rsp`, `set_player_value_rsp`, `get_player_attr_text_rsp`, `get_player_value_text_value_rsp` are not yet documented and would each require disassembly + cross-reference work; (d) a SetPlayerAppSettingValue (PDU 0x14) write path that actually mutates Y1's SharedPreferences from a different process; (e) a new cardinality NOP in MtkBt.odex for the event-0x08 sswitch arm if proactive CHANGED is in scope. Total effort ≈ 5 days for Optional-only rows. Schema bytes 795-799 of `y1-track-info` are reserved for the eventual `shuffle_flag` / `repeat_mode` fields. Revisitable when hardware-test bandwidth opens up.

---

## 2. Coverage matrix — current vs spec

Anchored against **ICS Table 7 (Target Features)** in `docs/spec/AVRCP 1.3/AVRCP.ICS.p17.pdf` §1.5, which is the canonical M/O determination. M/O status is conditional on what other features the TG claims; the ICS encodes the conditionals explicitly. PDU = "PDU ID" byte at AV/C body offset +4. AVRCP 1.3 V13 spec sections in `docs/spec/AVRCP 1.3/AVRCP_SPEC_V13.pdf`.

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
| 12-15 | List / Get / Set PApp Settings (0x11–0x14) | §5.2.1–5.2.4 | C.14: M to support **none or all** | not shipped (none) | spec-compliant; PlayerApplicationSettings ships all (deferred — see §1) |
| 16-17 | PApp Setting Attribute / Value Text (0x15-0x16) | §5.2.5-5.2.6 | O | not shipped | PlayerApplicationSettings (deferred) |
| 18 | InformDisplayableCharacterSet (PDU 0x17) | §5.2.7 | O | ✓ T_charset | — |
| 19 | InformBatteryStatusOfCT (PDU 0x18) | §5.2.8 | O | ✓ T_battery | — |
| **20** | GetElementAttributes (PDU 0x20) | §5.3.1 | **M (C.3: M IF cat 1)** | ✓ T4 (all 7 §5.3.4 attrs: Title / Artist / Album / TrackNumber / TotalNumberOfTracks / Genre / PlayingTime, single packed frame) | — |
| **21** | GetPlayStatus (PDU 0x30) | §5.4.1 | **M (C.2: M IF GetElementAttributes Response)** | ✓ T6 with live position via `clock_gettime(CLOCK_BOOTTIME)` | — |
| **22** | RegisterNotification (PDU 0x31) | §5.4.2 | **M (C.12: M IF cat 1)** | ✓ T2 / extended_T2 / T8 | — |
| **23** | Notify EVENT_PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | **M (C.4: M IF GetElementAttributes + RegisterNotification)** | ✓ T8 INTERIM + T9 CHANGED on edge | — |
| **24** | Notify EVENT_TRACK_CHANGED | §5.4.2 Tbl 5.30 | **M (C.4)** | ✓ extended_T2 INTERIM + T5 CHANGED on edge | — |
| 25 | Notify EVENT_TRACK_REACHED_END | §5.4.2 Tbl 5.31 | O | ✓ T8 INTERIM + T5 CHANGED-on-edge (gated on natural-end flag from Y1MediaBridge `onTrackDetected` position-vs-duration check) | — |
| 26 | Notify EVENT_TRACK_REACHED_START | §5.4.2 Tbl 5.32 | O | ✓ T8 INTERIM + T5 CHANGED-on-edge (unconditional on every track edge) | — |
| 27 | Notify EVENT_PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | O | ✓ T8 INTERIM + T9 CHANGED at 1 s cadence while playing (Y1MediaBridge tick fires `playstatechanged`; T9 live-extrapolates position via `clock_gettime(CLOCK_BOOTTIME)`) | — |
| 28 | Notify EVENT_BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | O | ✓ T8 INTERIM reads y1-track-info[794] (real bucket from `Intent.ACTION_BATTERY_CHANGED`) + T9 CHANGED-on-edge piggybacked on `playstatechanged` broadcast | — |
| 29 | Notify EVENT_SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | O | ✓ T8 INTERIM with `0x00 POWER_ON` (canned IS the real value while trampolines run — see footnote†) | — |
| 30 | Notify EVENT_PLAYER_APPLICATION_SETTING_CHANGED | §5.4.2 Tbl 5.37 | O | not shipped | PlayerApplicationSettings (deferred) |
| 31-32 | Continuation (PDUs 0x40/0x41) | §5.5 | C.2: M IF GetElementAttributes Response | ✓ T_continuation explicit dispatch in T4 pre-check → AV/C NOT_IMPLEMENTED reject via UNKNOW_INDICATION path (msg=520) | — |
| **65** | Discoverable Mode | §12.1 | **M** | ✓ (mtkbt) | — |
| 66 | PASSTHROUGH operation supporting Press and Hold | §4.1.3 | O | ✓ (mtkbt + U1 disables kernel auto-repeat on AVRCP uinput) | — |

**Mandatory rows: all hit.** Optional rows fully shipped: 18, 19, 25, 26, 27, 28, 29, 31, 32, 66. Optional rows still pending: 12-17, 30 (PlayerApplicationSettings).

† **Event 0x07 SYSTEM_STATUS_CHANGED — INTERIM-only is intentional.** While trampolines execute, the system is by definition POWER_ON; UNPLUGGED is for accessory / dock contexts that don't apply to the Y1; POWER_OFF is unobservable from inside a process that can no longer emit responses. The canned `0x00 POWER_ON` value IS the real value, so there is no edge to fire CHANGED on.

**INTERIM vs. CHANGED notation reminder.** AVRCP 1.3 §5.4.2 splits each event subscription into two response shapes: an immediate **INTERIM** carrying the current value at registration time, and an asynchronous **CHANGED** when the relevant condition fires. Mandatory rows 23 and 24 ship both halves. Optional rows 25 / 26 / 27 / 28 also ship both halves with real-data CHANGED-on-edge. Row 29 SYSTEM_STATUS_CHANGED ships INTERIM only (see footnote above).

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
| 0x01 | PLAYBACK_STATUS_CHANGED | §5.4.2 Tbl 5.29 | ✓ T8 | ✓ T9 (Y1 play / pause broadcast) |
| 0x02 | TRACK_CHANGED | §5.4.2 Tbl 5.30 | ✓ extended_T2 | ✓ T5 (Y1 track-change broadcast) |
| 0x03 | TRACK_REACHED_END | §5.4.2 Tbl 5.31 | ✓ T8 | ✓ T5 (gated on Y1MediaBridge natural-end flag at file[793]) |
| 0x04 | TRACK_REACHED_START | §5.4.2 Tbl 5.32 | ✓ T8 | ✓ T5 (unconditional on track edge) |
| 0x05 | PLAYBACK_POS_CHANGED | §5.4.2 Tbl 5.33 | ✓ T8 | ✓ T9 (1 s cadence while playing; Y1MediaBridge tick fires `playstatechanged`; live-extrapolated via `clock_gettime(CLOCK_BOOTTIME)`) |
| 0x06 | BATT_STATUS_CHANGED | §5.4.2 Tbl 5.34 | ✓ T8 (real bucket from y1-track-info[794]) | ✓ T9 (piggybacked on playstatechanged; gated on file[794] vs state[10] edge) |
| 0x07 | SYSTEM_STATUS_CHANGED | §5.4.2 Tbl 5.36 | ✓ T8 (canned 0x00 POWER_ON) | intentionally INTERIM-only (see §2 footnote) |
| 0x08 | PLAYER_APPLICATION_SETTING_CHANGED | §5.4.2 Tbl 5.37 | not advertised | PlayerApplicationSettings (deferred) |

---

## 3. Binary discovery — what's already mapped

`objdump -dRT` against the v3.0.2 stock libraries was the entire discovery pass for what response-builder primitives we have to call. Result: every PDU and every event has both:

- A function in `libextavrcp.so` (e.g. `<btmtk_avrcp_send_get_playstatus_rsp>: 0x2354`)
- A PLT stub in `libextavrcp_jni.so` (e.g. `<btmtk_avrcp_send_get_playstatus_rsp@plt>: 0x3564`)

Full PLT inventory (from `libextavrcp_jni.so` md5 `fd2ce74db9389980b55bccf3d8f15660`, what each trampoline would `blx`):

| PDU / event | Response builder | PLT @ | libextavrcp.so body @ |
|---|---|---|---|
| **Currently used by shipped trampolines** ||||
| 0x10 GetCapabilities | get_capabilities_rsp | `0x35dc` | `0x1dac` |
| 0x17 InformDisplayableCharacterSet | inform_charsetset_rsp | `0x3588` | `0x2138` |
| 0x18 InformBatteryStatusOfCT | battery_status_rsp | `0x357c` | `0x2160` |
| 0x20 GetElementAttributes | get_element_attributes_rsp | `0x3570` | `0x2188` |
| 0x30 GetPlayStatus | get_playstatus_rsp | `0x3564` | `0x2354` |
| 0x31 event 0x01 | reg_notievent_playback_rsp | `0x339c` | `0x23f0` |
| 0x31 event 0x02 | reg_notievent_track_changed_rsp | `0x3384` | `0x2458` |
| 0x31 event 0x03 | reg_notievent_reached_end_rsp | `0x3378` | `0x24c8` |
| 0x31 event 0x04 | reg_notievent_reached_start_rsp | `0x336c` | `0x2528` |
| 0x31 event 0x05 | reg_notievent_pos_changed_rsp | `0x3360` | `0x2588` |
| 0x31 event 0x06 | reg_notievent_battery_status_changed_rsp | `0x3354` | `0x25f0` |
| 0x31 event 0x07 | reg_notievent_system_status_changed_rsp | `0x3348` | `0x2658` |
| (default reject for unknown PDU including 0x40/0x41) | pass_through_rsp | `0x3624` | n/a (in libextavrcp.so too — reached via UNKNOW_INDICATION fall-through) |
| **PlayerApplicationSettings (deferred — not yet wired)** ||||
| 0x11 | list_player_attrs_rsp | `0x35d0` | `0x1e24` |
| 0x12 | list_player_values_rsp | `0x35c4` | `0x1e74` |
| 0x13 | get_curplayer_value_rsp | `0x35b8` | `0x1ed0` |
| 0x14 | set_player_value_rsp | `0x3594` | `0x1f2e` |
| 0x15 | get_player_attr_text_rsp | `0x35ac` | `0x1f58` |
| 0x16 | get_player_value_text_value_rsp | `0x35a0` | `0x203c` |
| 0x31 event 0x08 | reg_notievent_player_appsettings_changed_rsp | `0x345c` | `0x2720` |

**No new PLT discovery needed.** The stubs are already linked. Argument-convention discovery is still required for any builder we haven't called yet (the seven PlayerApplicationSettings entries above). Recipe per function: see "Adding a new PDU handler" in [`ARCHITECTURE.md`](ARCHITECTURE.md). Document the resulting C signature in `ARCHITECTURE.md` §"Reverse-engineered semantics" before writing the trampoline.

### Code-cave budget

LOAD #1 padding currently used: `0xac54..0xb2c8` (1652 B). Free space past `0xb2c8` to LOAD #2 at `0xbc08`: **~2368 bytes** (4020 B padding total). PlayerApplicationSettings would need ~400 B for the six new sub-trampolines plus `T_papp_changed`; that fits with significant headroom.

If we ever do exhaust LOAD #1 padding, the fallback is to extend the same trick to the LOAD #2 padding region by bumping LOAD #2's `p_filesz`/`p_memsz`.

---

## 4. y1-track-info schema (cumulative)

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
| 792 | playing_flag | 1 | shipped | `mPlayStatus` (3-valued AVRCP §5.4.1 Tbl 5.26 enum: 0=STOPPED, 1=PLAYING, 2=PAUSED — fed by `LogcatMonitor` mapping Y1's BaseActivity state codes `'1'`/`'3'`/`'5'`) |
| 793 | previous_track_natural_end | 1 | shipped | `mPreviousTrackNaturalEnd` (T5 gate for AVRCP §5.4.2 Tbl 5.31 TRACK_REACHED_END CHANGED) |
| 794 | battery_status | 1 | shipped | `mCurrentBatteryStatus` (T8 INTERIM + T9 CHANGED-on-edge for AVRCP §5.4.2 Tbl 5.34 BATT_STATUS_CHANGED) |
| 795..799 | reserved | 5 | — | (PlayerApplicationSettings shuffle_flag / repeat_mode reservation) |
| 800..815 | TrackNumber (UTF-8 ASCII decimal) | 16 | shipped | `MediaStore.Audio.Media.TRACK % 1000` / parsed from `METADATA_KEY_CD_TRACK_NUMBER` |
| 816..831 | TotalNumberOfTracks (UTF-8 ASCII decimal) | 16 | shipped | `count(*) WHERE ALBUM_ID=?` / parsed from `CD_TRACK_NUMBER` "n/total" |
| 832..847 | PlayingTime (UTF-8 ASCII decimal ms) | 16 | shipped | derived from `duration_ms` |
| 848..1103 | Genre (UTF-8) | 256 | shipped | `MediaStore.Audio.Genres` / `METADATA_KEY_GENRE` |

Total file size: **1104 B**. Page-aligned write is still single-block. Schema bumps are append-only; we never relocate existing fields, so older trampolines keep working against a newer file (T6 / T8 / T9 only read up to offset 792 and are unaffected by attrs 4-7 being appended past 800).

The numeric AVRCP §5.3.4 attrs (4 / 5 / 7) are stored pre-formatted as ASCII decimal strings rather than binary u16 / u32 with a Thumb-2 itoa, keeping the T4 trampoline a uniform strlen+memcpy loop.

`y1-trampoline-state` (16 B, mode 0666) is unchanged; remains the sole writable surface from the BT process side.

---

## 5. PlayerApplicationSettings — response-builder discovery

Discovery work that will be required before trampolines for PDUs 0x11-0x16 + event 0x08 can be written:

| Function | libextavrcp.so @ | Notes |
|---|---|---|
| `list_player_attrs_rsp` | `0x1e24` | PDU 0x11 — return list of supported attribute IDs |
| `list_player_values_rsp` | `0x1e74` | PDU 0x12 — return allowed values for one attribute |
| `get_curplayer_value_rsp` | `0x1ed0` | PDU 0x13 — return current values for the requested attribute IDs |
| `set_player_value_rsp` | `0x1f2e` | PDU 0x14 — accept new value (also requires a write path that mutates Y1's `SharedPreferences` from a different process) |
| `get_player_attr_text_rsp` | `0x1f58` | PDU 0x15 — return UTF-8 attribute label text |
| `get_player_value_text_value_rsp` | `0x203c` | PDU 0x16 — return UTF-8 value label text |
| `reg_notievent_player_appsettings_changed_rsp` | `0x2720` | event 0x08 — proactive CHANGED on shuffle / repeat edges |

Recipe per function: see [`ARCHITECTURE.md`](ARCHITECTURE.md) §"Adding a new PDU handler". The argument shape must be confirmed via disassembly, not inferred from arg name — the OEM names don't always match semantics (see the worked example on `get_element_attributes_rsp`).

**Sub-PDU detail (informative):**
- 0x11 ListPlayerAppSettingAttrs — return 2 attrs: 0x02 (Repeat), 0x03 (Shuffle). 1.3 also defines 0x01 EqualizerStatus and 0x04 ScanStatus, both optional; skip.
- 0x12 ListPlayerAppSettingValues for attr=0x02 → 4 values (off / single / all / group). For attr=0x03 → 3 values (off / all / group).
- 0x13 GetCurrentPlayerAppSettingValue — read shuffle_flag / repeat_mode from y1-track-info, return.
- 0x14 SetPlayerAppSettingValue — controller sending us shuffle / repeat. Forward as a broadcast that the music app receives → setSharedPref → broadcast loops back to us.
- 0x15/0x16 — text labels for attribute and value names. Static strings ("Repeat", "Off", etc.) shippable in LOAD #1 padding.

Plus: proactive CHANGED on shuffle / repeat changes via event 0x08, fed by the same broadcast receivers.

---

## 6. Test / verification strategy

Per change: a hardware capture against at least three CTs covering different policy postures. Test-matrix CT roster + per-CT empirical observations live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT". Recommended posture spread:

- **Permissive CT** — polls metadata regardless of CHANGED edges; most forgiving baseline.
- **High-subscribe-rate CT** — subscribes to TRACK_CHANGED at high frequency (regression check for AVCTP-saturation symptoms).
- **Strict CT** — gates metadata refresh on charset acknowledgement and real CHANGED edges.
- **Polling CT** — uses GetPlayStatus polling rather than RegisterNotification subscriptions (regression check for T6 / live position).
- (stretch) An iOS / WearOS CT — different polling pattern again.

For each CT, capture btlog+logcat with:
```
./tools/dual-capture.sh /work/logs/dual-<ct>-<run>/
```
and verify:
1. **No new NACKs.** msg=520 NOT_IMPLEMENTED count should drop to zero (or stay at zero).
2. **Expected msg=N response sizes.** Each PDU has a known wire-shape; its outbound IPC frame size should be deterministic.
3. **EventsSupported announcement.** T1's GetCapabilities response advertises every event the current build can satisfy; CT either subscribes or it doesn't, but it shouldn't subscribe to events we can't satisfy and then NACK.
4. **Battery footprint.** dmesg-after wake-lock count and `getprop` battery stats should not regress.

The btlog parser (`tools/btlog-parse.py`) gives us full HCI command/event visibility. Any AVCTP-level NACK or rejection will surface there as a `result:` field on the relevant CNF.

---

## 7. Risk register (when extending)

| Risk | Likelihood | Mitigation |
|---|---|---|
| New PDU response builder has an arg convention not derivable from disassembly alone | Medium | Bisect via JNI in-tree caller (most builders are called by stock JNI somewhere even if Java stack never reaches them). Failing that, ship a no-op trampoline that returns NOT_IMPLEMENTED and watch CT behavior to confirm the CT was actually probing for that PDU. |
| The 1 s PLAYBACK_POS_CHANGED cadence creates wakeup pressure | Low | T9's position-emit block runs only when file[792]==PLAYING and is driven by a 1 s `Handler.postDelayed` loop in Y1MediaBridge that's cancelled the moment playback pauses or stops. No timer fires while idle. Strict CTs subscribed for a longer interval are over-served (spec-permissible — `shall be emitted at this interval` defines a max-interval ceiling, not a min cadence floor). |
| LOAD #1 extension exhausts page-padding | Very low | ~2368 B free past the current 1652 B blob; budget supports many more trampolines. Fallback: extend LOAD #2 padding. |
| Trampoline blob shifts every PLT call beyond range | Low (Thumb b.w covers ±16 MB; trampolines and PLT are <0x10000 apart) | Verify `bl.w`/`b.w` reach in `_thumb2asm.py` self-test for each new emit site. |
| AVRCP version negotiation: F1 patch sets MtkBt-internal version flag but our wire-shape PDU set is 1.3 | Low | F1 only flips the BlueAngel-internal flag to unblock 1.3+ command dispatch through MtkBt's Java layer. SDP record advertises AVRCP 1.3 (V1 patch) / AVCTP 1.2 (V2 patch). Per AVRCP 1.3 §6 (Service Discovery Interoperability Requirements) + ESR07 §2.1 / Erratum 4969, the served version is what CTs key against, and they negotiate a 1.3 dialogue — which is what we implement. |
| Continuation PDU 0x40/0x41 traffic appears on a future capture (would require stateful re-emit) | Low (zero across 43 captures so far) | T_continuation currently emits NOT_IMPLEMENTED — spec-acceptable. If 0x40 traffic ever shows up, upgrade to a stateful continuation handler that re-emits the buffered response. |
| AVCTP saturation under a CT subscribe storm drops PASSTHROUGH key-release frames; the music app then interprets the held key as a long-press, calls `startFastForward()`/`startRewind()`, and the lambda thread runs forever | Medium (high-subscribe-rate CT classes have been observed driving this — see [`INVESTIGATION.md`](INVESTIGATION.md) for per-CT empirical context) | **U1**: NOP `UI_SET_EVBIT(EV_REP)` at `libextavrcp_jni.so:0x74e8` so the kernel's `evdev` soft-repeat timer never fires on the AVRCP virtual keyboard. Without auto-repeat, a dropped PASSTHROUGH RELEASE can no longer drive the held-key cascade; the music app sees one event per actual PRESS frame the CT sends. Spec-correct per AVRCP 1.3 §4.6.1 + AV/C Panel Subunit Spec (CT periodic re-send during held button). |

---

## 8. Out of scope (and why)

- **AVRCP TG group navigation (0x7d/0x7e in 1.3-Cat-3)** — falls under PASSTHROUGH which we haven't broken. No changes needed.

Anything outside the AVRCP 1.3 spec proper (V13 + ESR07) is out of scope for this project. See §1 above.

---

## 9. Lower BT profile stack

AVRCP 1.3 sits on top of AVCTP, which rides L2CAP. A2DP rides AVDTP, signalled by GAVDP. All four lower profiles live in `mtkbt` (BlueAngel internal stack — see [`ARCHITECTURE.md`](ARCHITECTURE.md) "Lower BT profile stack").

**Scope.** Spec-completeness where tractable on this hardware — every Optional row we can implement, not just observed-deviation fixes. Per the [`feedback_y1_upstream_spec_compliance`] memory rule, fixes go at the spec-deviation layer, not via AVRCP-level workarounds. Subsections are organised per-issue with the affected profile noted in the heading.

### 9.1 AVRCP META CONTINUING_RESPONSE (PDUs 0x40 / 0x41) — *AVRCP / AVCTP* — DEFERRED

**Spec.** AVRCP 1.3 §5.5: when a TG response exceeds CT buffer / AVCTP MTU, TG sends `packet_type=01 START`; CT responds with PDU 0x40 (Continuing) or 0x41 (Abort). ICS Table 7 rows 31-32: Mandatory if GetElementAttributes Response is supported (C.2).

**Current state.** `T_continuation` rejects 0x40 / 0x41 with AV/C NOT_IMPLEMENTED via the UNKNOW_INDICATION fallback. Counted as ✓ in §2 because the OEM `_send_get_element_attributes_rsp` packs into a 644 B buffer and emits a single non-fragmented frame — TG never sets `packet_type=01`, so a spec-conforming CT never sends 0x40. Zero 0x40 / 0x41 PDUs across 43 captures.

**Why deferred.** A stateful re-emitter is only meaningful after the OEM `_send_get_element_attributes_rsp` is replaced by a manual META builder that fragments at AVRCP layer (i.e., the TG actually sets `packet_type=01`). Until that bypass lands, the re-emitter is dead code. Cheaper INVALID_PARAMETER (status 0x05 per §6.15.2) reject hits a binary-shape barrier: `libextavrcp.so` exposes no META REJECTED builder, and changing the existing `cmd_frame_ind_rsp` arg shape carries regression risk for every unhandled-PDU path with no observable wire-shape benefit (CT can't tell the two reject codes apart).

**Plan when revisited.** Replace T4's `_send_get_element_attributes_rsp` call with a manual META builder that paginates at AVRCP layer (set `packet_type=01` on first fragment, cache the rest in a per-conn state struct in the trampoline blob region). Upgrade T_continuation: on 0x40 emit next chunk with `packet_type=02/03`; on 0x41 drop state. ~3 days; needs `AVRCP_SendMessage` (libextavrcp.so:0x18ec) IPC shape disassembly.

### 9.2 AVRCP playback state ↔ AVDTP source state coupling — *A2DP / AVDTP* — SHIPPED

**Spec.** AVDTP 1.3 §8.13 / §8.15: when AVRCP TG signals PAUSED, the A2DP source should keep the stream paused (NOT torn down); resume without renegotiation on PLAYING. SUSPEND is reserved for explicit policy changes (phone call routing, etc.), not for normal pause / silence handling.

**Pre-fix deviation.** AudioFlinger's silence-timeout (~3 s after the music app stops writing samples) hit `libaudio.a2dp.default.so::A2dpAudioStreamOut::standby_l` and the function called `a2dp_stop` unconditionally → AVDTP SUSPEND on the wire. Peer CTs (notably TVs) closed/reopened their A2DP sink once per pause-of-≥3 s — producing burst-on-resume audio and playhead drift. Empirical: 8 cycles in a 3-min TV capture (`/work/logs/dual-tv-20260509-1410`).

**Current state.** `patch_libaudio_a2dp.py` patches `standby_l` at file offset `0x000086ab` (1 byte: ARM cond `0x0a` EQ → `0xea` AL). The `beq` that conditionally branched past `a2dp_stop` is now an unconditional `b`, so the call site is unreachable. AudioFlinger's silence-timeout still completes the standby (releases the wake lock, sets `mStandby = 1`), but the AVDTP stream is left alive; the next `write()` after PLAYING resumes pushes samples into the same session. See [`PATCHES.md`](PATCHES.md) §`patch_libaudio_a2dp.py` for the byte-level reference.

**Verification.** Capture btlog around a pause: zero `[A2DP] a2dp_stop. is_streaming:1` lines, peer A2DP sink does not cycle, audio resumes clean with no burst, CT playhead tracks real audio.

**Earlier approach (reverted).** First fix attempt drove `audioManager.setParameters("A2dpSuspended=true|false")` from `Y1MediaBridge/MediaBridgeService.java::onStateDetected`. Hardware capture (`/work/logs/dual-tv-20260509-1538`) showed the AOSP A2DP HAL implements `setSuspended(true)` as a *synchronous* tear-down — `setParameters("A2dpSuspended=true")` calls `a2dp_stop` directly before any silence-timeout standby fires, so the Java approach actively triggered the AVDTP SUSPEND it was trying to prevent. Reverted in v2.9; replaced by the HAL byte patch above. The Java path never sees A2dpSuspended now.

### 9.3 Per-attribute 511-byte hard cap in `…send_get_element_attributes_rsp` — *AVRCP* — SHIPPED

**Spec.** AVRCP 1.3 §5.3.4 places no per-attribute byte cap. TG fragments via §5.5 if the total response exceeds AVCTP MTU.

**OEM TG deviation.** `libextavrcp.so:0x2188` (`btmtk_avrcp_send_get_element_attributes_rsp`) enforces a 511-byte per-attribute hard cap; on overflow it emits `[BT][AVRCP][ERR] too large attr_index:%d` and drops the attribute silently.

**Current state.** `Y1MediaBridge/MediaBridgeService.java::putUtf8Padded` caps each string attribute at `AVRCP_ATTR_MAX_BYTES = 240` bytes before it lands in `y1-track-info`. The cap is well below the OEM 511 hard cap so even after multi-byte UTF-8 expansion at the CT side the attribute survives. Truncation is codepoint-safe — if the chosen byte position would split a multi-byte codepoint, the helper walks back to the codepoint boundary so a strict CT never sees a malformed UTF-8 sequence (AVRCP 1.3 §5.3.4 CharacterSet=0x6A). Numeric attributes (TrackNumber 4, TotalNumber 5, PlayingTime 7) are bounded to 15 bytes by `NUMERIC_STR_LEN` and unaffected.

**Optional follow-up.** Bypass `…send_get_element_attributes_rsp` entirely and build the GetElementAttributes response wire frame directly in the trampoline, using the full §5.5 fragmentation flow from §9.1 above. Removes the 511 cap as a constraint. ~3 days if paired with §9.1 (no value as a standalone change — 240 B already fits everything we'd realistically ship).

### 9.4 AVCTP MTU negotiation discoverability — *AVCTP* — NO ACTION

**Spec.** AVCTP 1.2 §5: each side independently advertises its MTU during L2CAP CONFIG; the smaller value wins. AVRCP 1.3 §6 specifies the AVCTP version pairing only.

**Current state.** mtkbt advertises `AVRCP_MAX_PACKET_LEN:512` regardless of peer capability. Captured logs show `(10+u2MtuPayload) <= 512` enforcement. Most peers agree to 512; some negotiate higher in L2CAP CONFIG but we cap at 512.

**Compliance plan.** Not currently a deviation worth fixing — 512 is the standard L2CAP signaling MTU and most CTs are fine with it. Filed here for completeness; revisit if a future capture shows a CT requesting >512 and degrading on the cap.

### 9.5 GAVDP / AVDTP codec advertisement — *GAVDP / AVDTP / A2DP* — INVESTIGATION

**Spec.** AVDTP 1.3 §8.6 (DISCOVER): TG should respond with all locally-supported SEPs. A2DP 1.3 §4.1: SBC Mandatory; AAC / MP3 / ATRAC Optional.

**Current state.** mtkbt rejects non-SBC SEPs in `GavdpAvdtpEventCallback` (`[AVDTP_EVENT_CAPABILITY]not AVDTP_CODEC_TYPE_SBC`). Spec-compliant but not spec-complete.

**Investigation.** Two open questions: (a) does the MTK chipset have a hardware AAC encoder? (b) is the SBC-only check a runtime flag (small patch) or a missing code path (weeks)? Filed for the per-profile ICS-scoreboard pass.

### 9.6 AVCTP subscribe-storm mitigation (U1) — *AVCTP* — SHIPPED

`U1` (`libextavrcp_jni.so:0x74e8` NOPs `UI_SET_EVBIT(EV_REP)`) defangs kernel-side auto-repeat on the AVRCP virtual keyboard, preventing held-key cascades when a subscribe-storming CT drops PASSTHROUGH RELEASEs. Risk-register context in §7. Patch H″ (in `com.innioasis.y1.apk`) handles the framework-side equivalent (`InputDispatcher::synthesizeKeyRepeatLocked` repeats).

### 9.7 SDP AVCTP version correction (V2) — *AVCTP* — SHIPPED

`V2` (`mtkbt 0x0eba6d`: SDP AVCTP version `0x00 → 0x02`) corrects the served record from advertising AVCTP 1.0 to AVCTP 1.2, the version paired with AVRCP 1.3 per AVRCP 1.3 §6 + ESR07 §2.1 / Erratum 4969. Byte detail in [`PATCHES.md`](PATCHES.md).

### 9.8 AVDTP DELAY_REPORT (sig_id 0x0d) — *AVDTP* — INVESTIGATION

**Spec.** AVDTP 1.3 §8.6.7. Sink reports rendering delay so source can compensate. Optional.

**Current state.** Not advertised by mtkbt; inbound handling unverified. No captured CT emits one. Filed for the ICS-scoreboard pass — small fix if mtkbt already accepts inbound DELAY_REPORTs, larger if it rejects.

### 9.9 Remaining workstream order

§9.2 / §9.3 / §9.6 / §9.7 shipped. Active queue:

- **§9.1 stateful T_continuation**: needs either (a) the OEM `_send_get_element_attributes_rsp` replaced by a manual AVRCP META builder (so TG ever sets `packet_type=01`), or (b) `cmd_frame_ind_rsp` arg-shape disassembly to upgrade NOT_IMPLEMENTED to INVALID_PARAMETER (~1 day for a functionally-indistinguishable wire-shape change). Empirical demand stays low — zero 0x40/0x41 PDUs across captured CT sessions.
- **§1 Phase F4 PApp Settings**: ~5 days, all-or-none under C.14. Closes ICS Table 7 rows 12-17 + 30. Highest-value remaining AVRCP work.
- **§9.5 / §9.8 ICS-scoreboard pass**: in progress (§9.10 below); spec PDFs landed at `docs/spec/<PROFILE>/`. Per-profile audits incrementally added.

Each ships with hardware verification across the standard test-matrix postures (permissive / high-subscribe / strict / polling) per §6.

### 9.10 AVCTP 1.2 ICS audit — *AVCTP* — VERIFIED

Anchored against `docs/spec/AVCTP 1.2/AVCTP.ICS.p10.pdf` Table 3 (Target Features). Per Table 0, AVCTP 1.0 / 1.2 / 1.3 are all deprecated 2022-02-01 + withdrawn 2023-02-01; only AVCTP 1.4 is in current SIG qualification scope. We target 1.2 because that's the version paired with AVRCP 1.3 per AVRCP 1.3 §6 + ESR07 §2.1 / Erratum 4969 — same shipping-window argument as AVRCP 1.3 (deprecated 2023-02-01 / withdrawn 2027-02-01).

| ICS Table 3 row | Capability | Status | Y1 |
|---|---|---|---|
| 1 | Message fragmentation | O | ✓ — mtkbt outbound path implements pkt_type 0/1/2/3 (SINGLE/START/CONTINUE/END) per ARCHITECTURE.md §"AVCTP packet types" |
| **2** | **Transaction label management** | **M** | ✓ — `transId` routed from `conn[17]` into every response builder; stamping into byte 5 of IPC frame is the response-builder's responsibility (cross-ref ARCHITECTURE.md cross-profile coupling table item 4). Empirically wire-confirmed via Trace #12 — Sonos sends real AV/C VENDOR_DEPENDENT GetCapabilities and the trampoline-chain response is accepted |
| **3** | **Packet type field management** | **M** | ✓ — pkt_type written by the AVCTP-layer fragmentation logic; Trace #12 captures show 100% pkt_type=0x00 SINGLE for the GetElementAttributes 644 B body (no fragmentation triggered) |
| **4** | **Message type field management** | **M** | ✓ — RESPONSE / COMMAND distinction set per AV/C body type; mtkbt's response builders write the correct type bit |
| **5** | **PID field management** | **M** | ✓ — AVRCP TG PID=0x110E served correctly per the AVRCP record's ProfileDescList; AVCTP layer uses this PID for routing |
| **6** | **IPID field management** | **M** | ✓ — Invalid Profile Identifier handling delegated to mtkbt's AVCTP layer; no observed deviation across captured sessions |
| **7** | **Message information management** | **M** | ✓ — AV/C body parsing + response synthesis lives in the trampoline chain (see ARCHITECTURE.md §"Trampoline chain") |
| 8-13 | Event registration / connect / disconnect / send variants | O | mtkbt provides the connect / disconnect / send paths used by the JNI bridge; event-registration optional and not separately exposed |
| 14 | Multiple AVCTP channel establishment | O — Controller-only row | n/a (TG row table doesn't include this) |

**All Mandatory rows covered.** Optional row 1 (fragmentation) shipped for completeness; remaining Optional rows (8-13) are CT/TG event-registration APIs not exercised by our stack.

**Static AVCTP version-byte multiplicity question (resolved).** Three AVCTP version sites in mtkbt's static SDP-record region; V2 patches one. Per the SDP attribute table at vaddr `0xfa700`, only one served AVRCP TG record references AVCTP — the served-record entries (table file offset `0xf97c0..0xf9808`) include attr 0x0004 ProtocolDescriptorList (containing the V2-patched AVCTP version at `0xeba6d`) but NOT attr 0x000d AdditionalProtocolDescriptorList (which is what would carry the Browse-channel AVCTP descriptor at `0xeba25`). The unpatched site `0xeba37` falls inside a separate template record that is not selected at runtime. sdptool ground truth (Trace #12, post-V1+V2 flash) shows the served record advertises AVCTP `0x0102` — only one version on the wire. Conclusion: **V2 patches the only consulted site; the other two are dead bytes.** Verification path if the static-vs-runtime authority assumption breaks in a future build: experimental flash with all three sites patched + sdptool browse → expect identical AVCTP 0x0102 output.

### 9.11 A2DP 1.3 ICS audit — *A2DP* — SHIPPED (PARTIAL — see §9.5 / §9.8)

Anchored against `docs/spec/A2DP 1.3/A2DP_SPEC_V13.pdf`. There is no separate A2DP ICS proforma in the SIG release; conformance requirements live in the spec body itself.

**Application Layer (§3 Table 3.1):**

| Item | Feature | SRC | SNK | Y1 |
|---|---|---|---|---|
| 1 | Audio Streaming | M | M | ✓ Y1 implements SRC role; SBC stream over AVDTP |

**Audio Codec Interoperability (§4.2 Table 4.1):**

| Codec | Status | Y1 |
|---|---|---|
| SBC | M | ✓ encoder present (`libmtkbtextadpa2dp.so` exports `sbc_pack_frame`, `sbc_proto_4_40_fx`, `sbc_proto_8_80_fx`, `sbc_calculate_bits`, etc.) |
| MPEG-1,2 Audio | O | not advertised, not present |
| MPEG-2,4 AAC | O | not advertised, not present |
| ATRAC family | O | not advertised, not present |

GAVDP layer rejects non-SBC SEPs at `GavdpAvdtpEventCallback` per ARCHITECTURE.md §"Codec scope". Spec-permissible (§4.2.4 codec interop is conditional on Optional codec support, none claimed).

**SDP Source Service Record (§5.3 Figure 5.1):**

| Attribute | Spec status | Spec value (1.3) | Y1 served value | Gap |
|---|---|---|---|---|
| Service Class ID List → Audio Source (UUID `0x110A`) | M | UUID | ✓ (sdptool confirms handle 0x10002 advertises Audio Source) | — |
| ProtocolDescriptorList → L2CAP / AVDTP, AVDTP version | **M** | `0x0103` | `0x0100` (AVDTP 1.0) | **1 byte at file `0xeba09`** |
| BluetoothProfileDescriptorList → AdvancedAudioDistribution (UUID `0x110D`), version | **M** | `0x0103` | `0x0100` (A2DP 1.0) | **1 byte at file `0xeb9f2`** |
| SupportedFeatures (Source bitmap: bit 0 Player / 1 Microphone / 2 Tuner / 3 Mixer) | O | bit 0 = 1 (Player) | not advertised (stock A2DP record omits attr `0x0311`) | none required (Optional) |
| Provider Name / Service Name | O | text | "Advanced Audio" served as Service Name (sdptool confirms) | — |

**Gaps to A2DP 1.3 conformance:**

1. **Advertised AVDTP version in A2DP record's ProtoDescList** is `0x0100` instead of spec-mandated `0x0103` — file offset `0xeba09`, single-byte patch.
2. **Advertised A2DP version in BluetoothProfileDescriptorList** is `0x0100` instead of spec-mandated `0x0103` — file offset `0xeb9f2`, single-byte patch.
3. Both must move together — bumping one without the other creates an asymmetric advertisement. Critically, **the bumps imply we honor AVDTP 1.3 features** (DELAY_REPORT in particular — A2DP 1.3 §1.4.1.2 explicitly cites it as the new-in-1.3 interop addition). Whether to ship the bumps depends on the AVDTP audit (§9.12 below): if `mtkbt`'s sig-id dispatcher honors 0x0d DELAYREPORT (even just gracefully accepting and discarding inbound DELAY_REPORTs), the bump is honest. If 0x0d hits a hard NOT_IMPLEMENTED reject, the bump reproduces the legacy `--avrcp` 1.4-vs-1.0 mismatch shape and should be deferred.

A2DP §3.1 confirms peers consult our advertised AVDTP version before GAVDP_ConnectionEstablishment — the 1.0 advertisement is what currently keeps modern peers from attempting 1.3-only commands against us. Per §9.12 the 1.3 features (DELAY_REPORT, GET_ALL_CAPABILITIES) are Optional even at AVDTP 1.3, so the 1.0→1.3 bump is honest as long as we don't advertise the corresponding SEP capabilities. **Decision: bump both bytes (0xeb9f2 + 0xeba09) to 0x03 to align advertisement with our actual implementation surface, matching the spec-compliance directive in `feedback_avrcp_spec_compliance` memory.** Pairs with §9.12; ships together as a single new patch (`patch_mtkbt.py` add-ons V3 / V4 — to be added in the §9.14 synthesis pass).

### 9.12 AVDTP 1.3 ICS audit — *AVDTP* — VERIFIED, GAP = ADVERTISED VERSION

Anchored against `docs/spec/AVDTP 1.3/AVDTP.ICS.p14.pdf`. Per Table 14a (Versions, Source), AVDTP 1.0 / 1.2 are deprecated/withdrawn; AVDTP 1.3 is the only currently-Mandatory version. Same shipping-window argument as AVRCP / AVCTP.

**Source role capabilities (Table 14):**

| Item | Capability | Status | Y1 |
|---|---|---|---|
| 1 | **Basic transport service support** | **M** | ✓ — A2DP audio streams over AVDTP through `libmtka2dp.so` ↔ mtkbt internal A2DP source state machine ↔ AVDTP MEDIA on the wire (cid 0x40 in captures) |
| 2 | Reporting service support | O | not advertised in SEP capabilities, no handler |
| 3 | Recovery service support | O | not advertised, no handler |
| 4 | Multiplexing service support | O | not advertised, no handler |
| 5 | Robust header compression service support | O | not advertised, no handler |
| 6 | **Delay Reporting** | **O** | not advertised in SEP capabilities; sig_id 0x0d DELAYREPORT reaches General Reject (§8.18 fallback) — see "Handler verification" below |

**Acceptor capabilities (Source role) — Table 8 Signaling Message Format:**

| Item | Capability | Status | Y1 |
|---|---|---|---|
| 1 | Transaction Label | M | ✓ |
| 2 | Packet type | M | ✓ |
| 3 | Message type | M | ✓ |
| 4 | Signal identifier | M | ✓ |

Tables 9-12 (Stream Discovery, Establishment, Suspension, Release, Security) — every row Optional. We implement Stream Discover (DISCOVER), Stream Get Capabilities (GET_CAPABILITIES), Set Configuration, Get Configuration, Open Stream, Start Stream, Suspend, Close, Abort responses (per ARCHITECTURE.md §"AVDTP signal codes" — sig 0x01..0x0b documented in mtkbt strings).

Table 13 (Message Fragmentation) item 1 = M: ✓ (mtkbt's AVDTP layer implements fragmentation, same code path as AVCTP layer per §8.3).

Table 16 (Message Error Handling): Reporting Capability Error (M) ✓ via `[AVDTP_EVENT_GET_CAP_CNF]try another SEP` in `GavdpAvdtpEventCallback`; General Reject Response Includes Signal ID (M) ✓ via §8.18 fallback path.

**Handler verification for AVDTP 1.3 additions (sig_id 0x0c GET_ALL_CAPABILITIES + 0x0d DELAYREPORT):**

Exhaustive `strings`-grep over `bin/mtkbt` for `GET_ALL_CAP|DELAYREPORT|delay.?rep|GenRejRsp|AvdtpSendGeneralReject|AvdtpRcvDelayRep|AvdtpGetAllCap` — **zero handler-suggesting strings** for either signal. Only hit for `delay.?rep` is HFP's `Delay report until command status` (role-change wording, unrelated to AVDTP). Provisional read: codepoint allocation in BlueAngel sig-id enum without runtime handlers — inbound 0x0c / 0x0d hits the General Reject path (Mandatory per §8.18, present in mtkbt's AVDTP error-handling). Spec-compliant rejection.

**Compliance verdict.**

| Aspect | Status |
|---|---|
| All Mandatory rows of Tables 8 / 13 / 16 (Source-role Acceptor) covered | ✓ |
| All advertised SEP capabilities backed by working handlers (SBC + Basic transport service) | ✓ |
| AVDTP 1.3 Optional features (Delay Reporting, Reporting / Recovery / Multiplexing services) | not advertised, not implemented — spec-permissible |
| **Advertised AVDTP version in SDP record** | **`0x0100` (AVDTP 1.0) — gap; spec target `0x0103`** |

**Gap:** the static AVDTP version byte at file offset `0xeba09` is `0x00` instead of `0x03`. Single-byte patch (V3-style: 0x00 → 0x03) brings advertisement into line with our actual implementation surface (which already covers every AVDTP 1.3 Mandatory feature for the Source role — Optional features are spec-permissibly absent). The bump pairs with the matching A2DP version bump per §9.11; ships together to avoid asymmetric-version advertisement.

This **resolves the §9.5 / §9.8 open investigations** — the codec-advertisement and DELAY_REPORT questions are now answered by the spec itself (both the hardware-AAC question and the DELAY_REPORT-handler question are moot because both features are Optional at AVDTP 1.3).

---

## 10. See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — proxy architecture and existing trampoline chain.
- [`PATCHES.md`](PATCHES.md) — per-patch byte detail.
- [`INVESTIGATION.md`](INVESTIGATION.md) — historical investigation including binary discovery passes and the empirical history that produced each shipped behavior.
- `src/patches/_trampolines.py` — current trampoline blob assembler.
- `src/patches/_thumb2asm.py` — Thumb-2 mini-assembler.
