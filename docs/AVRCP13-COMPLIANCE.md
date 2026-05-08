# AVRCP 1.3 Spec Compliance

Current AVRCP 1.3 spec coverage. Anchored against ICS Table 7 in `docs/spec/AVRCP.ICS.p17.pdf`. Per-CT empirical observations behind each design choice live in [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT".

For why we have a proxy at all, the current trampoline chain shape, and the calling conventions of the response builders we use, see [`ARCHITECTURE.md`](ARCHITECTURE.md). For per-patch byte-level reference, see [`PATCHES.md`](PATCHES.md).

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

## 1. Current coverage state

The goal is AVRCP 1.3 spec-completeness so any spec-compliant 1.3+ controller renders our metadata. Scope is the latest revision of the AVRCP 1.3 spec (V13 + ESR07). Anything outside that revision is out of scope.

The one carry-out from outside the 1.3 spec proper is `MtkBt.odex` patch F1's BlueAngel-internal `getPreferVersion()` value — internal flag bookkeeping inside MtkBt's Java-side dispatcher that unblocks 1.3+ command handling on a stack that was originally compiled against AVRCP 1.0. F1 sets the flag and unblocks 1.3 dispatch; nothing in our wire shape is changed by it.

**Closed:** every Mandatory row of ICS Table 7, plus every Optional row except 12-17 + 30 (PlayerApplicationSettings).

**Deferred — PlayerApplicationSettings (PDUs 0x11-0x16 + event 0x08, ICS Table 7 rows 12-17 + 30, all Optional).** Condition C.14 demands all-or-none for 0x11-0x14, so partial shipping is non-conformant; going to "all" is the threshold. The implementation requires (a) Y1 APK smali patches that inject `sendBroadcast` calls into static-ish methods (`SharedPreferencesUtils.setMusicIsShuffle` / `setMusicRepeatMode`) where there is no Context handle, requiring `getApp()`-injection chains; (b) Y1MediaBridge cross-package SharedPreferences plumbing for cold-boot reads; (c) at minimum 4 sub-trampolines for 0x11-0x14 plus 0x15/0x16 text-label trampolines and event 0x08 proactive emission for full ICS coverage, where the PLT calling conventions for `list_player_attrs_rsp`, `list_player_values_rsp`, `get_curplayer_value_rsp`, `set_player_value_rsp`, `get_player_attr_text_rsp`, `get_player_value_text_value_rsp` are not yet documented and would each require disassembly + cross-reference work; (d) a SetPlayerAppSettingValue (PDU 0x14) write path that actually mutates Y1's SharedPreferences from a different process; (e) a new cardinality NOP in MtkBt.odex for the event-0x08 sswitch arm if proactive CHANGED is in scope. Total effort ≈ 5 days for Optional-only rows. Schema bytes 795-799 of `y1-track-info` are reserved for the eventual `shuffle_flag` / `repeat_mode` fields. Revisitable when hardware-test bandwidth opens up.

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

## 9. See also

- [`ARCHITECTURE.md`](ARCHITECTURE.md) — proxy architecture and existing trampoline chain.
- [`PATCHES.md`](PATCHES.md) — per-patch byte detail.
- [`INVESTIGATION.md`](INVESTIGATION.md) — historical investigation including binary discovery passes and the empirical history that produced each shipped behavior.
- `src/patches/_trampolines.py` — current trampoline blob assembler.
- `src/patches/_thumb2asm.py` — Thumb-2 mini-assembler.
