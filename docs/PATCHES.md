# Patch Reference

Byte-level reference for the patches currently shipped by this repo. Each section describes what the patch ships **today** (offsets, before / after bytes, rationale, ICS status). For the commit-by-commit evolution that produced the current shape — including reverts, dead-end attempts, and the empirical evidence that motivated each behavior change — see [`INVESTIGATION.md`](INVESTIGATION.md) and `git log`. Spec citations follow the discipline in [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §0.

## Patch ID Legend

| ID(s) | Binary | Site / effect |
|---|---|---|
| **V1, V2, V3, V4, V5, V6, V7, V8, S1, P1, M1** | `mtkbt` | SDP shape (AVRCP 1.0→1.3, AVCTP 1.0→1.2, A2DP/AVDTP 1.0→1.3, sig 0x0c→0x02 alias, internal `activeVersion` 10→14 to route the dispatcher to the AVRCP 1.3 served record, drop AdditionalProtocolDescriptorList Browse-PSM advertisement (AVRCP 1.4 §8 Table 8.2 introduced; absent from AVRCP 1.3 §6 Table 6.2), clear stock GroupNavigation feature bit (Y1 doesn't implement the Group Navigation PASSTHROUGH PDUs), ServiceName-for-SupportedFeatures swap, force-PASSTHROUGH-emit op_code dispatch, RegNotif INTERIM/CHANGED dispatch cmp constant widened from 1 to 0x0F so wire ctype matches the JNI trampoline's reasonCode). |
| **R1, T1, T2 stub, extended_T2, T4, T5, T_charset, T_battery, T_continuation, T6, T8, T9, U1** | `libextavrcp_jni.so` | Trampoline chain in `_Z17saveRegEventSeqIdhh` + LOAD #1 page-padding extension + uinput EV_REP NOP. Synthesises AVRCP 1.3 metadata responses directly from C, bypassing the no-op Java AVRCP TG. |
| **F1, F2** | `MtkBt.odex` | `getPreferVersion()=14` to unblock 1.3+ command dispatch through MtkBt's Java layer; `disable()` resets `sPlayServiceInterface`. |
| **odex cardinality NOPs** (×2) | `MtkBt.odex` | NOP the `if-eqz v5` cardinality gates in `BTAvrcpMusicAdapter.handleKeyMessage` for events 0x02 (TRACK_CHANGED, sswitch_1a3) and 0x01 (PLAYBACK_STATUS_CHANGED, sswitch_18a) so the JNI natives fire on every `metachanged` / `playstatechanged` broadcast. Pairs with T5 / T9 in `libextavrcp_jni.so`. |
| **A, B, C, E, H, H′, H″** | `com.innioasis.y1*.apk` | Smali edits: A/B/C for Artist→Album navigation; E for discrete PASSTHROUGH PLAY/PAUSE/STOP/NEXT/PREVIOUS routing per AV/C Panel Subunit Spec op_id table; H for foreground-activity propagation of `KEYCODE_MEDIA_PLAY/PAUSE/STOP/NEXT/PREVIOUS`; H′ for the same propagation in `BasePlayerActivity` (which overrides `dispatchKeyEvent` and bypasses BaseActivity); H″ adds a `repeatCount > 0 → silent consume` filter to both H and H′ so framework-synthesized key repeats from `InputDispatcher::synthesizeKeyRepeatLocked` don't trigger long-press FF/RW handlers. |
| **AH1** | `libaudio.a2dp.default.so` | `A2dpAudioStreamOut::standby_l` cond-flip: `beq 8684` → `b 8684` at file offset `0x000086ab` so silence-timeout standby skips `a2dp_stop` unconditionally. Keeps the AVDTP source stream alive across pauses; matches AVDTP 1.3 §8.13 / §8.15 expectation that PAUSED leaves the stream paused-but-up. |
| **su** | `/system/xbin/su` | Setuid-root `su` binary installed by `--root`. |

---

## `patch_mtkbt.py`

Ten byte patches against stock `/system/bin/mtkbt`. Eight reshape the served SDP record so a peer CT engages with AVRCP 1.3 COMMANDs (per AVRCP 1.3 §6 Service Discovery Interoperability Requirements + ESR07 §2.1 / Erratum 4969 clarifying AVCTP version values), one reroutes inbound VENDOR_DEPENDENT frames into the JNI msg-519 emit path so the trampoline chain can respond, and one is a best-effort dispatch alias for AVDTP signal 0x0c.

The mtkbt daemon ships two physical AVRCP TG SDP record templates in `.data.rel.ro`. The internal `activeVersion` field selects which is served on the wire: stock = 10 (legacy 1.0 record), V6 → 14 (AVRCP 1.3 record). V1/V2/S1 patch the legacy record (kept for the fall-through path); V7/V8 patch the AVRCP 1.3 record (where V6 routes the daemon by default) so it conforms to AVRCP 1.3 §6 Table 6.2 SDP record shape — no AdditionalProtocolDescriptorList (a 1.4-introduced attribute per AVRCP 1.4 §8 Table 8.2), Group Navigation feature bit cleared (the bit exists in 1.3 §6 Table 6.2 but Y1 doesn't implement the Group Navigation PASSTHROUGH PDUs).

**V1 — AVRCP 1.0 → 1.3** at file `0x0eba58` (1 byte): `0x00` → `0x03`. LSB of the served Group D ProfileDescList Version field.

**V2 — AVCTP 1.0 → 1.2** at file `0x0eba6d` (1 byte): `0x00` → `0x02`. LSB of the served Group D ProtocolDescList AVCTP Version field.

**V3 — A2DP 1.0 → 1.3** at file `0x0eb9f2` (1 byte): `0x00` → `0x03`. LSB of the served A2DP Source ProfileDescList Version field. Per A2DP 1.3 §5.3 Figure 5.1 the Mandatory version value is `0x0103`.

**V4 — AVDTP 1.0 → 1.3** at file `0x0eba09` (1 byte): `0x00` → `0x03`. LSB of the served A2DP Source ProtocolDescList AVDTP Version field. Per A2DP 1.3 §5.3 the Mandatory AVDTP version is `0x0103`. Pairs with V3 — peers consult our advertised AVDTP version before GAVDP setup per A2DP §3.1, so both bumps ship together.

**V5 — AVDTP sig 0x0c dispatch alias** at file `0x0aa834` (2 bytes): halfword `0x0660` → `0x0083`.

| | bytes | TBH halfword | target |
|---|---|---|---|
| before | `60 06` | `0x0660` | `0xab4de` (sig 0x0c stub — always returns BAD_LENGTH error) |
| after  | `83 00` | `0x0083` | `0xaa924` (sig 0x02 GET_CAPABILITIES handler) |

Edits one entry of the AVDTP signal dispatcher's TBH jump table at file `0xaa81e`. Position 11 (`sig_id - 1` for sig 0x0c) is repointed from the stub at `0xab4de` to the full GET_CAPABILITIES handler at `0xaa924`.

This is a **structural workaround**, not a real GET_ALL_CAPABILITIES implementation — the response we emit is the sig 0x02 capability list, which per AVDTP V13 §8.8 is a wire-compatible **subset** of the sig 0x0c response (no extended Service Capabilities like DELAY_REPORTING / RECOVERY / MULTIPLEXING / HEADER_COMPRESSION). For an SBC-only Source this matches what we'd advertise anyway. Closes GAVDP 1.3 ICS Acceptor Table 5 row 9 on paper.

Wire-correct by decoupling: the response builder is `fcn.000ae418` (calls `L2CAP_SendData` at file `0xae58e`), and byte 1 of the response frame (sig_id) is read at `0xae480` from `txn->[0xe]` — the per-channel transaction state populated by the request parser at RX time. The dispatcher and per-signal handlers do not write `txn->[0xe]`. So a sig 0x0c request lands in the GET_CAPABILITIES handler post-V5, but the response frame still emits `sig_id=0x0c` matching the request. Payload is a V13 §8.8 subset valid for an SBC-only Source. See `INVESTIGATION.md` Trace #16.

**V6 — internal `activeVersion` 10 → 14** at file `0x10dca` (2 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `0a 23` | `movs r3, #0xa` |
| after  | `0e 23` | `movs r3, #0xe` |

The stock activation handler at `fcn.00010d00` hardcodes the activeVersion field stored to the avrcp_state struct's `+0xb86` offset. The downstream SDP record builder at `fcn.00038ab8` reads this byte and dispatches: `v != 0xd && v != 0xe` → legacy AVRCP 1.0 served record (logs `AVRCP sdp 1.0 target role`); `v == 0xd || v == 0xe` → AVRCP 1.3 served record (logs `AVRCP sdp 1.3 target role`). V6 changes the immediate from 10 to 14 so the daemon takes the latter branch by default, aligning the served record with the version F1 surfaces to the Java layer.

**V7 — `0x000d AdditionalProtocolDescList` → `0x0100 ServiceName`** at file `0x0f9798` (12 bytes):

| | bytes | shape |
|---|---|---|
| before | `0d 00 14 00 12 ba 0e 00 00 00 00 00` | attr=`0x000d`, len=`0x14`, ptr=`0x0eba12` (→ AdditionalProtocolDescList: L2CAP / PSM `0x001b` Browse + AVCTP) |
| after  | `00 01 11 00 ce b9 0e 00 00 00 00 00` | attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` (→ `25 0f "Advanced Audio\0"`) |

The stock AVRCP 1.3 served record advertises attribute `0x000d AdditionalProtocolDescriptorList` carrying the AVRCP Browse PSM `0x001b`. AdditionalProtocolDescriptorList is introduced in AVRCP 1.4 §8 Table 8.2 (conditional on SupportedFeatures bit 6 "Supports browsing"); AVRCP 1.3 §6 Table 6.2 does not list it. V7 swaps this entry slot for a `0x0100 ServiceName` entry pointing at the same "Advanced Audio" string S1 reuses for the legacy record. Net wire effect: drops the Browse advertisement, restores ServiceName presence so the served record matches AVRCP 1.3 §6 Table 6.2 shape.

**V8 — `SupportedFeatures` 0x0021 → 0x0001** at file `0x0eba4e` (1 byte):

| | byte | bits set |
|---|---|---|
| before | `21` | bit 0 (Category 1: Player/Recorder) + bit 5 (Group Navigation) |
| after  | `01` | bit 0 only |

LSB of the AVRCP 1.3 served record's SupportedFeatures `uint16` (byte stream `09 00 21` → `09 00 01` at `0x0eba4c`). AVRCP 1.3 §6 Table 6.2 defines bit 5 as "Group Navigation" (conditional on bit 0 Category 1 being set) with the note "the bits for supported categories are set to 1; others are set to 0." Y1's stock advertises bit 5 set but ships no Group Navigation PASSTHROUGH handler; V8 clears it so the advertised mask is 0x0001 (Category 1 only), matching what's actually implemented. Bits 6-15 are RFA in 1.3 Table 6.2; bit 6 became "Supports browsing" in AVRCP 1.4 §8 Table 8.2.

**S1 — `0x0311 SupportedFeatures` → `0x0100 ServiceName`** at file `0x0f97ec` (12 bytes):

| | bytes | shape |
|---|---|---|
| before | `11 03 03 00 59 ba 0e 00 00 00 00 00` | attr=`0x0311`, len=3, ptr=`0x0eba59` (→ `uint16 0x0001`) |
| after  | `00 01 11 00 ce b9 0e 00 00 00 00 00` | attr=`0x0100`, len=`0x11`, ptr=`0x0eb9ce` (→ `25 0f "Advanced Audio\0"`) |

Patches the same entry-slot swap on the legacy AVRCP 1.0 served record (the fall-through served when `activeVersion != 0xd && != 0xe`). Reuses the existing "Advanced Audio" SDP-encoded string from mtkbt's A2DP record. Cost: the legacy served record loses the `0x0311 SupportedFeatures` attribute. CTs in our test matrix engage with the record without it.

**P1 — force PASSTHROUGH-emit branch** at file `0x144e8` (2 bytes):

| | bytes | mnemonic |
|---|---|---|
| before | `30 2b` | `cmp r3, #0x30` |
| after  | `1e e0` | `b.n 0x14528` |

Replaces the first comparison in fn `0x144bc`'s op_code dispatch with an unconditional branch to the PASSTHROUGH-emit branch at `0x14528` (which ends with `bl 0x10404`, the function that emits msg 519 CMD_FRAME_IND to the JNI socket). Every AV/C frame flows through the emit path. Cost: VENDOR_DEPENDENT bytes get interpreted in PASSTHROUGH-shaped fields, so mtkbt's mid-stack response may be malformed — but the JNI trampoline chain takes over before that matters.

**M1 — RegNotif INTERIM/CHANGED discriminator: cmp ctxt[8] against 0x0F** at file `0x12230` (1 site, 2 bytes):

| site | bytes (before → after) | mnemonic |
|---|---|---|
| `0x12230` | `01 29` → `0f 29` | `cmp r1, 1` → `cmp r1, 0xF` |

Stock mtkbt's RegNotif response packetFrame builder dispatch at fn `0x121d8` reads `ctxt[8]` and compares against `1` to choose between INTERIM (ctype `0x0F` at `0x12238`) and CHANGED (ctype `0x0D` at `0x12244`) branches. The JNI's `btmtk_avrcp_send_reg_notievent_*_rsp` helpers in `libextavrcp.so` marshal the reasonCode argument (REASON_INTERIM=`0x0F` / REASON_CHANGED=`0x0D`) into IPC payload byte 8 — verified by the `strb.w r7, [sp, #12]` encoding (bytes `8d f8 0c 70`) at the cardinality=0 path of every helper, where sp+12 maps to payload+8 (the helper's 40-byte buffer starts at sp+4). Stock mtkbt reads the correct byte but compares against `1`, so `0x0F` and `0x0D` both fail the cmp and the dispatch always lands on the CHANGED branch — wire ctype is `0x0D` for every RegNotif response regardless of which reasonCode the trampoline passes.

M1 widens the cmp constant from `1` to `0x0F`. After M1: `ctxt[8] == 0x0F` (T2 / extended_T2 / T8 first-response INTERIM arms) → INTERIM branch → wire ctype `0x0F` INTERIM. `ctxt[8] != 0x0F` (T5 / T9 edge emits, where r2 = REASON_CHANGED = `0x0D`) → CHANGED branch → wire ctype `0x0D` CHANGED. Spec-compliant per AVRCP 1.3 §6.7.1 and matches the Pixel-as-TG btsnoop pattern (INTERIM on first response per registration, CHANGED on subsequent value updates without waiting for re-registration).

End-to-end byte chain: IPC msg=544 → `fcn.00067768` (sets ctxt ptr at msg+0x1c) → `fcn.000518ac` case 44 → `fcn.00012478` event_id tbb → per-event response builder → `fcn.000121d8` (M1 site at `0x12230`) → `fcn.00011894` strb ctype to packetFrame[0xb] → `fcn.0000f0bc` queue → `fcn.0000ef08` strb to wire `buf[0]`. Full radare2 trace in `docs/INVESTIGATION.md` Trace #37 + Trace #38 + Trace #39 (M2 diagnostic confirmed `fcn.0000ef08` is the wire emitter and verified that the JNI helper writes reasonCode to payload[8], not payload[7] as an earlier M1a misread suggested).

**MD5s:** Stock `3af1d4ad8f955038186696950430ffda` → Output `926b8e808693a4c44028ee257b33e898`.

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

Overwrites the unused JNI debug method `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa` (~44 byte slot). Detects PDU 0x10, calls `btmtk_avrcp_send_get_capabilities_rsp` via PLT `0x35dc` with an 8-element `EventsSupported` array, branches to epilogue at `0x712a`. Fall-through (b.w `0x72d4`) bridges to T2.

Per AVRCP 1.3 §5.4.2 + ICS Table 7 row 11, GetCapabilities is **mandatory** for any TG advertising PASS THROUGH Cat 1 (which our V1 SDP does). Advertised set: `0x01` PLAYBACK_STATUS, `0x02` TRACK_CHANGED, `0x05` PLAYBACK_POS, `0x08` PLAYER_APPLICATION_SETTING_CHANGED, plus `0x09` NOW_PLAYING_CONTENT_CHANGED, `0x0a` AVAILABLE_PLAYERS_CHANGED, `0x0b` ADDRESSED_PLAYER_CHANGED, `0x0c` UIDS_CHANGED. The four 0x09..0x0c IDs come from AVRCP 1.4+ and are advertised here — even though the SDP profile descriptor is 1.3 — because strict CT metadata-pane render empirically gates on them being acknowledged. Pixel-as-TG does the same. T8 INTERIM-acks all four with zero/empty payload; no CHANGED ever fires (Y1 has one player, no Now Playing folder, no UID database).

### T2 stub + extended_T2 — RegisterNotification (PDU 0x31) entry

T2 stub at `0x72d0` (8 bytes) overwrites `classInitNative` with `movs r0, #0; bx lr` (preserves the `return 0` contract; loses the debug logs) followed by `b.w extended_T2`. extended_T2 lives in the LOAD #1 padding region; it handles event 0x02 TRACK_CHANGED specifically:

1. Read `y1-track-info[0..7]` (track_id) into a stack buffer.
2. Write `[track_id || transId || pad]` to `y1-trampoline-state` so T4 can detect track-id edges later.
3. Reply INTERIM via `reg_notievent_track_changed_rsp` (PLT `0x3384`) with `r1=0` (success), `r2=REASON_INTERIM` (`0x0f`), `r3=&track_selected`.

Other PDU / event combos fall through to T4 (PDU 0x20 → main, 0x17 → T_charset, 0x18 → T_battery, 0x30 → T6, 0x40 / 0x41 → T_continuation, 0x31+event≠0x02 → T8) before hitting the original "unknow indication" path.

`r1=0` matters: response builders dispatch on r1 — `r1==0` writes the spec-correct event payload (reasonCode + event_id + 8-byte track_id memcpy per AVRCP 1.3 §5.4.2 Table 5.30); `r1!=0` writes a reject-shape frame. We pass `r1=0` everywhere.

**Track_id payload** = `0x0000000000000000` (8 bytes of 0x00), the AVRCP 1.4+ SELECTED value meaning "the currently playing media is selected — render its metadata." Mirrors what Pixel-as-TG sends. Distinct from `0xFFFFFFFFFFFFFFFF` which means "no media currently selected" (AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 clarifying the field is 8 bytes) — that latter value would tell a strict 1.4+ CT to suppress metadata-pane render because nothing is supposedly playing. Y1 always has a track loaded when a CT is connected (the music app is running), so SELECTED is the honest signal. The `y1-trampoline-state[0..7]` field still holds the real per-track UID internally so T4 / T5 can detect edges and emit CHANGED proactively.

### T4 — GetElementAttributes (PDU 0x20)

In the LOAD #1 padding region, reached from extended_T2's PDU-0x20 dispatch arm. Implements the AVRCP 1.3 §6.6.1 Table 6.26 response contract: "If NumAttributes is set to zero, all attribute information shall be returned, else attribute information for the specified attribute IDs shall be returned by the TG."

T4 reads the inbound `NumAttributes` byte (caller's sp+394) and:
- **`NumAttributes == 0`**: emits all seven supported attributes in canonical order (1..7) via a compile-time-unrolled loop.
- **`NumAttributes > 0`**: emits each requested `AttributeID[i]` in the CT-specified order. For IDs in {0x01..0x07}, the value comes from the corresponding `y1-track-info` slot below. For any other ID (e.g. 0x08 — "Reserved" in 1.3, never supported), Y1 emits the attribute header with `AttributeValueLength=0` per §5.3.4 "for attributes not supported by the TG, this field shall be sent with 0 length data".

| attr_id | Name | Source slot in `y1-track-info` |
|---|---|---|
| 0x01 | Title | `[8..263]` |
| 0x02 | Artist | `[264..519]` |
| 0x03 | Album | `[520..775]` |
| 0x04 | TrackNumber | `[800..815]` (UTF-8 ASCII decimal) |
| 0x05 | TotalNumberOfTracks | `[816..831]` (UTF-8 ASCII decimal) |
| 0x06 | Genre | `[848..1103]` |
| 0x07 | PlayingTime | `[832..847]` (UTF-8 ASCII decimal milliseconds) |

All values ship as UTF-8 (charset `0x006A`); a missing attribute is signalled by `AttributeValueLength=0`. Y1's emission of zero-length entries requires `patch_libextavrcp.py` E1 to land — the stock `libextavrcp.so` response builder otherwise drops such attributes on the floor (a §5.3.4 violation in the stock code). The numeric attrs (4 / 5 / 7) are stored pre-formatted as ASCII strings by the music app's `TrackInfoWriter` rather than binary u16 / u32 with a Thumb-2 itoa, keeping the trampoline a uniform strlen+memcpy loop.

T4 also detects track-id edges (compares `y1-track-info[0..7]` against `y1-trampoline-state[0..7]`) and emits a reactive CHANGED via `reg_notievent_track_changed_rsp` before the GetElementAttributes response, then writes the new track_id back to state.

Pre-check dispatch table: `0x20 → main`, `0x17 → T_charset`, `0x18 → T_battery`, `0x30 → T6`, `0x40 → T_continuation`, `0x41 → T_continuation`, `0x31+event≠0x02 → T8`, else fall through to "unknow indication".

### T5 — proactive track-edge CHANGED burst

In LOAD #1 padding. Entered via `b.w T5` from the patched first instruction of `notificationTrackChangedNative` at file offset `0x3bc0`:

| | bytes | mnemonic |
|---|---|---|
| before | `2D E9 F0 47` | `stmdb sp!, {r4, r5, r6, r7, r8, r9, sl, lr}` (function prologue) |
| after  | `[b.w T5 emitted by patcher]` | branch to T5 trampoline |

T5 obtains the AVRCP per-conn struct via JNI helper at `0x36c0` (the same helper the stock native called), reads `y1-track-info` (full 800 B) and `y1-trampoline-state` (21 B), and on track-id divergence emits the Pixel-mirror track-edge CHANGED burst:

1. `reg_notievent_now_playing_content_rsp` (PLT `0x330c`, event 0x09) with `r1=0`, `r2=REASON_CHANGED` (`0x0d`). Gated on `state[20]` (sub_now_playing_content, armed by T8 0x09 INTERIM).
2. `reg_notievent_pos_changed_rsp` (PLT `0x3360`, event 0x05 — Tbl 5.33) with `r1=0`, `r2=REASON_CHANGED`, `r3=REV(file[780..783])` (current position in host order — `duration_ms` on natural end, `0` on NEXT / PREV). Gated on `state[13]` (sub_pos, armed by T8 0x05 INTERIM).
3. `reg_notievent_reached_end_rsp` (PLT `0x3378`, event 0x03 — Tbl 5.31) **only when** `y1-track-info[793]` (the `previous_track_natural_end` flag set by `PlaybackStateBridge.onCompletion`) `== 1` AND `state[17]` (sub_track_reached_end, armed by T8 0x03 INTERIM). Strict spec semantic: TRACK_REACHED_END fires on natural end, not on a skip.
4. `reg_notievent_track_changed_rsp` (PLT `0x3384`, event 0x02 — Tbl 5.30) with `r1=0`, `r2=REASON_CHANGED`, `r3=&track_selected`. Gated on `state[16]` (sub_track_changed, armed by extended_T2's INTERIM emit).
5. `reg_notievent_reached_start_rsp` (PLT `0x336c`, event 0x04 — Tbl 5.32) with `r1=0`, `r2=REASON_CHANGED`. Gated on `state[18]` (sub_track_reached_start, armed by T8 0x04 INTERIM).

Then writes the new track_id back to state and returns `jboolean(1)`.

The emit ordering mirrors Pixel-as-TG's wire trace (NowPlayingContent → PlaybackPos → TrackChanged) for events that overlap; 0x03 / 0x04 are AVRCP 1.3 extensions Y1 supports if the CT subscribes (they're not advertised in the current `T1` event set, so the gates are typically `0` and these emits become no-ops).

Fired on every `com.android.music.metachanged` broadcast emitted by the music app (after the MtkBt.odex sswitch_1a3 cardinality NOP at 0x3c530 wakes the dispatch path). The remaining 196 bytes of the original native body are unreachable. T5's frame is 824 B (24 state + 800 file_buf, with state byte 20 holding sub_now_playing_content).

### T_charset — InformDisplayableCharacterSet (PDU 0x17)

Branched from T4's pre-check on PDU 0x17. Restores lr canary + r0=conn and tail-jumps to UNKNOW_INDICATION (`0x65bc`), which emits an AV/C `NOT_IMPLEMENTED` reject. 12 bytes. Spec-permissible per AVRCP 1.3 §5.2.7 (Optional). Matches Pixel-as-TG behaviour (its `btsnoop_hci.log` shows the same reject). An earlier ACK-via-`inform_charsetset_rsp` variant stalled at least one strict CT into a 3-second wait between 0x17 and the first RegisterNotification — apparently waiting on a follow-up notification that never came; the reject path lets the subscription burst land in <10 ms.

### T_battery — InformBatteryStatusOfCT (PDU 0x18)

Same shape as T_charset, calls `battery_status_rsp` via PLT `0x357c`. AVRCP 1.3 §5.2.8.

### T_continuation — RequestContinuingResponse (0x40) / AbortContinuingResponse (0x41)

Branched from T4's pre-check on PDU 0x40 or 0x41. Restores `lr` canary + `r0=conn` and tail-jumps to UNKNOW_INDICATION (the catch-all reject path that emits AV/C NOT_IMPLEMENTED via msg=520). Functionally identical to the catch-all fall-through but routed through an explicit dispatch in the pre-check so ICS Table 7 rows 31-32 read "shipped" rather than "fall-through".

AVRCP 1.3 §4.7.7 / §5.5: continuation is initiated by the TG setting `Packet Type=01` (start) in a response — the CT only sends 0x40 in reply to a fragmented response. `get_element_attributes_rsp` never sets the start-of-fragmentation flag, so a spec-conforming CT never sends 0x40. The trampoline body is 6 bytes (one `ldrh.w`, one `add.w`, one `b.w`).

§6.15.2 specifies AV/C INVALID_PARAMETER (status 0x05) as the spec-strict response when receiving 0x40 without prior fragmentation; NOT_IMPLEMENTED is a different but spec-acceptable AV/C reject for an unsupported PDU and is functionally indistinguishable to the CT (both are reject frames; the CT abandons the continuation flow either way).

### T6 — GetPlayStatus (PDU 0x30)

Branched from T4's pre-check on PDU 0x30. Reads `y1-track-info[776..795]` (4 BE u32 fields: duration_ms / position_at_state_change_ms / state_change_time_ms / playing_flag), byte-swaps the u32s to host order via Thumb-2 `REV`, and calls `btmtk_avrcp_send_get_playstatus_rsp` via PLT `0x3564` with `arg1=0` + `r2=duration_ms` + `r3=live_position_ms` + `sp[0]=play_status`. Outbound `msg_id=542`, 20-byte IPC frame.

**Live position extrapolation:** when `playing_flag == PLAYING` (the music app's `PlaybackStateBridge` maps `Static.setPlayValue`'s newValue 0/1/3/5 directly to AVRCP 1.3 §5.4.1 Table 5.26 PlayStatus bytes), T6 calls `clock_gettime(CLOCK_BOOTTIME, &timespec)` (NR=263, clk_id=7 — same monotonic source `TrackInfoWriter` stamps `mStateChangeTime` from), computes `now_ms = tv_sec * 1000 + tv_nsec / 1e6`, then `live_pos = saved_pos_ms + (now_ms - state_change_ms)`, passes that as r3. The nsec→ms division is done via magic-multiply (`(tv_nsec * 0x431BDE83) >> 50`, equivalent to high-half UMULL then >>18) — bit-exact for tv_nsec ∈ [0, 1e9). Both endpoints carry full ms precision on the wire, so the position the CT renders is exact relative to Y1's playhead, no per-state-edge ±1 s lurch. When STOPPED / PAUSED the position field stays at the saved freeze point. Implements AVRCP 1.3 §5.4.1 Table 5.26's `SongPosition` definition ("the current position of the playing in milliseconds elapsed"). `struct timespec` is stashed in unused outgoing-args slack at sp+8..15 inside the existing T6 frame (no frame growth).

ICS Table 7 row 21: GetPlayStatus is **mandatory** for any TG that ships GetElementAttributes Response (per ICS condition C.2). T6 closes that mandatory row.

### T_papp — PlayerApplicationSettings (PDUs 0x11..0x16)

Branched from T4's pre-check when the inbound PDU byte is in `[0x11..0x16]`. Per AVRCP 1.3 ICS Table 7 condition C.14, supporting any single PApp PDU makes the whole 7-row group (PDUs 0x11..0x16 + event 0x08) Mandatory — they ship together.

Y1 supports Repeat (id=2) + Shuffle (id=3); other AVRCP §5.2.1 attributes (Equalizer 0x01, Scan 0x04) aren't surfaced by the music app and aren't advertised. Six PDU dispatchers internal to T_papp + a paired event 0x08 INTERIM case in T8. Event 0x08 INTERIM (T8) and proactive CHANGED (T9) bind to live state via `y1-track-info[795..796]`, written by the music app's `PappStateBroadcaster` on every `musicRepeatMode` / `musicIsShuffle` SharedPreferences change. GetCurrent reads the same bytes via `lseek` + `read` with a static OFF/OFF fallback on I/O failure. List / AttrText / ValueText return static-schema responses (these reflect the *capabilities* of the player rather than per-edge state).

| PDU | Builder PLT | Behavior |
|---|---|---|
| 0x11 ListPlayerApplicationSettingAttributes | `0x35d0` | Returns `[Repeat=2, Shuffle=3]`, n=2 |
| 0x12 ListPlayerApplicationSettingValues | `0x35c4` | Switches on inbound `attr_id`: Repeat → `[1,2,3,4]`, Shuffle → `[1,2,3]`, else reject |
| 0x13 GetCurrentPlayerApplicationSettingValue | `0x35b8` | Branches on inbound `n` per V13 §6.12 ("TG returns the value(s) of the setting(s) requested by the CT"). `n==1`: validate the requested `attr_id` (Repeat or Shuffle, else reject with status 0x05 INVALID_PARAMETER), read the matching live byte from `y1-track-info[795..796]`, emit n=1 response. Other `n`: read both bytes, emit n=2 response (kept for permissive CTs). I/O failure falls back to `0x01 OFF` for n=1 / `[(Repeat, OFF), (Shuffle, OFF)]` for n!=1. |
| 0x14 SetPlayerApplicationSettingValue | `0x3594` | Reads inbound `(attr_id, value)` pair from caller's sp+387/+388, writes 2 bytes to `/data/data/com.innioasis.y1/files/y1-papp-set` (atomic O_WRONLY|O_TRUNC), ACKs the peer. The music app's `PappSetFileObserver` consumes the CLOSE_WRITE, translates AVRCP→Y1 enum, and applies via `SharedPreferencesUtils.setMusicRepeatMode/setMusicIsShuffle` directly (no Intent hop). Multi-pair Sets (n>1) only the first pair is applied. |
| 0x15 GetPlayerApplicationSettingAttributeText | `0x35ac` | Accumulator: emit "Repeat" (idx=0) then "Shuffle" (idx=1, total=2 → SendMessage) |
| 0x16 GetPlayerApplicationSettingValueText | `0x35a0` | Emits per-(attr_id, value_id) text via switch: Repeat 0x01/0x02/0x03 → "Off"/"Single Track"/"All Tracks"; Shuffle 0x01/0x02 → "Off"/"All Tracks". Unsupported pairs fall through with no response (peer times out / falls back). |

Per-builder calling-convention reference: [`ARCHITECTURE.md`](ARCHITECTURE.md) PApp builder table.

ICS Table 7 rows 12-15 (C.14 Mandatory if any), 16-17 (Optional), and 30 (event 0x08, Optional) — all closed by T_papp + the T8 event 0x08 INTERIM case.

### T8 — RegisterNotification dispatcher for events ≠ 0x02

In LOAD #1 padding. Branched from extended_T2's "PDU 0x31 + event ≠ 0x02" arm. Reads `y1-track-info` for events that need payloads (0x01 / 0x05), then dispatches on event_id and calls the matching `reg_notievent_*_rsp` PLT entry:

| event_id | name | PLT | payload |
|---|---|---|---|
| 0x01 | PLAYBACK_STATUS_CHANGED | `0x339c` | play_status u8 (from `y1-track-info[792]`) |
| 0x03 | TRACK_REACHED_END | `0x3378` | (none) |
| 0x04 | TRACK_REACHED_START | `0x336c` | (none) |
| 0x05 | PLAYBACK_POS_CHANGED | `0x3360` | position_ms u32 (from `y1-track-info[780..783]`, REV-swapped) |
| 0x06 | BATT_STATUS_CHANGED | `0x3354` | battery_status u8 from `y1-track-info[794]` (real bucket from `Intent.ACTION_BATTERY_CHANGED`) |
| 0x07 | SYSTEM_STATUS_CHANGED | `0x3348` | canned `0x00 POWER_ON` (intentional — while trampolines run the system is by definition POWER_ON; the canned value IS the real value) |
| 0x08 | PLAYER_APPLICATION_SETTING_CHANGED | `0x345c` | n=2 + `[(Repeat, repeat_avrcp), (Shuffle, shuffle_avrcp)]` from `y1-track-info[795..796]` |
| 0x09 | NOW_PLAYING_CONTENT_CHANGED | `0x330c` | (none) |
| 0x0a | AVAILABLE_PLAYERS_CHANGED | `0x3324` | (none) |
| 0x0b | ADDRESSED_PLAYER_CHANGED | `0x3330` | PlayerID u16 = 0 + UidCounter u16 = 0 |
| 0x0c | UIDS_CHANGED | `0x3318` | UidCounter u16 = 0 |

Events 0x01-0x08 cover AVRCP 1.3 §5.4.2 (Tbls 5.29/5.31/5.32/5.33/5.34/5.36/5.37). Events 0x09-0x0c are 1.4+ event IDs whose response builders are already linked by `libextavrcp_jni.so` (their PLT stubs are present though stock JNI never invokes them); T8 acks each with INTERIM-only zero/empty payload. This matches what Pixel-as-TG does and is what strict CTs empirically gate metadata-pane render on, even when the SDP profile descriptor advertises 1.3.

All response builders share the calling convention `r0=conn`, `r1=0` (success), `r2=reasonCode`, `r3=event-specific u8/u16/u32`. Unknown event_ids fall through to "unknow indication" for the spec-correct NOT_IMPLEMENTED reject. T8 handles INTERIM for every event_id; proactive CHANGED for events 0x01/0x05/0x06/0x08 lives in T9 (entered from `notificationPlayStatusChangedNative`) and for 0x02/0x03/0x04 in T5/extended_T2 (entered from `notificationTrackChangedNative` / extended_T2's PDU 0x31 + event 0x02 arm respectively). Events 0x07 and 0x09-0x0c are INTERIM-only — nothing on Y1 ever changes them. (0x07 SYSTEM_STATUS rationale: see footnote in `docs/BT-COMPLIANCE.md` §2; 0x09-0x0c rationale: Y1 has one player, no Now Playing folder, no UID database.)

### T9 — proactive CHANGED on play-state / battery / papp / 1Hz position tick

T5's structural twin for events 0x01, 0x06, 0x05, 0x08, 0x09. Entered via `b.w T9` from the patched first instruction of `notificationPlayStatusChangedNative` at file offset `0x3c88`:

| | bytes | mnemonic |
|---|---|---|
| before | `2D E9 F3 41` | function prologue |
| after  | `[b.w T9 emitted by patcher]` | branch to T9 trampoline |

T9 reads `y1-track-info` into its file buffer and `y1-trampoline-state` (21 B) into the state buffer, then runs five independent edge / cadence checks:

- **play_status:** compare file[792] vs state[9] (`last_play_status`). On inequality, emit `reg_notievent_playback_rsp` via PLT `0x339c` with `r1=0`, `r2=REASON_CHANGED` (`0x0d`), `r3=play_status`. Gated on `state[14]` (sub_play_status). Then, in the same edge branch, emit `reg_notievent_now_playing_content_rsp` via PLT `0x330c` if `state[20]` (sub_now_playing_content) is armed — Pixel emits NowPlayingContent + PlaybackStatus as a paired burst on play-edge. Update state[9].
- **battery_status:** compare file[794] vs state[10] (`last_battery_status`). On inequality, emit `reg_notievent_battery_status_changed_rsp` via PLT `0x3354` with `r3=battery_status`. Gated on `state[19]` (sub_battery). Update state[10].
- **papp settings:** compare file[795]/file[796] (repeat_avrcp / shuffle_avrcp) vs state[11]/state[12]. On any inequality, emit `reg_notievent_player_appsettings_changed_rsp` via PLT `0x345c` with `r3=2`, `sp[0]=&papp_attr_ids` (=`[0x02, 0x03]`), `sp[4]=&file[795]`. Gated on `state[15]` (sub_papp). Update state[11..12]. The values pointer is just `sp+T9_OFF_FILE_REPEAT` since file_buf already holds `[r, s]` contiguously at 795..796.
- **playback_pos:** if file[792] == 1 (PLAYING), `clock_gettime(CLOCK_BOOTTIME, &timespec)` (NR=263, clk_id=7 via `svc 0`), compute `now_ms = tv_sec * 1000 + tv_nsec / 1e6` (nsec/1e6 via magic-multiply 0x431BDE83 then high-half >>18), then `live_pos = REV(file[780..783]) + (now_ms - REV(file[784..787]))` and emit `reg_notievent_pos_changed_rsp` via PLT `0x3360` with `r3=live_pos`. Gated on `state[13]` (sub_pos). Same arithmetic T6 does for GetPlayStatus, so position parity is maintained between polled GetPlayStatus and notification CHANGED. Both endpoints (`state_change_time_ms` written by `TrackInfoWriter` from `SystemClock.elapsedRealtime()`; `now_ms` from `clock_gettime(CLOCK_BOOTTIME)`) carry full ms precision in the same monotonic-since-boot epoch, so subtraction is bit-exact. Fires on every `playstatechanged` broadcast while playing — the music app's 1 s position ticker produces the ~1Hz cadence that matches Pixel's PlaybackPos CHANGED rate.

T9's frame is 840 B (8 outgoing-args at sp+0..7 + 24 state + 800 file_buf + 8 timespec).

If play, battery, or papp changed, the state file is written back at offset 9 (4 B: bytes 9..12) — never touches the gate region at bytes 13..20; the position emit is independent and never dirties state. Fires on every `playstatechanged` broadcast (after the MtkBt.odex sswitch_18a cardinality NOP at 0x3c4fe wakes the dispatch path). Closes AVRCP 1.3 §5.4.2 Table 5.29's CHANGED requirement on event-0x01 subscribers, Table 5.34's on 0x06, Table 5.33's on 0x05, Table 5.36's on 0x08, and AVRCP 1.4 §6.9.5's NowPlayingContentChanged on play-edge for 0x09.

`playstatechanged` is emitted whenever any of the following occurs:
- play state edge (the music app's `PlayerService` fires `com.android.music.playstatechanged` directly per android.music standard)
- battery bucket transition (level+plug bucket-mapped to the AVRCP §5.4.2 Tbl 5.35 enum)
- `musicRepeatMode` / `musicIsShuffle` SharedPreferences change (the music app's `PappStateBroadcaster` writes `y1-track-info[795..796]` and triggers a `playstatechanged` relay)
- 1 s position tick while playing

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

## `patch_libextavrcp.py`

Single 2-byte Thumb-2 CBZ→NOP flip inside `btmtk_avrcp_send_get_element_attributes_rsp` (function entry at `0x2188`).

**E1** at file `0x00002266` (2 bytes): `88 b3 → 00 bf` (CBZ r0, +0x62 → NOP T1). The function's per-attribute loop has a gate that skips the emit path when `(attr_id == 0) OR (strlen == 0)`, logging `"AVRCP send_get_element_attributes ignore empty attrib attri_id:%d strlen:%d"` instead of writing the attribute slot into the response buffer. The strlen-zero half of this gate is a deviation from AVRCP 1.3 §5.3.4:

> "For attributes not supported by the TG, this field shall be sent with 0 length data."

Patching the CBZ to a NOP makes execution fall through unconditionally to the emit path. Empty-value attributes are now emitted with `AttributeID + CharsetID + AttributeValueLength=0` (no value bytes), per spec. The attr_id=0 ("Not Used" per §26 Table 26.1) half of the gate also collapses, but `T4` in `libextavrcp_jni.so` never emits attr 0, so that side has no caller.

Strict CTs in the test matrix request a specific attribute set in their metadata-pane query (one such CT requests `[0x1, 0x2, 0x3, 0x6, 0x8, 0x7]`) and gate render on receiving every requested attribute back. Without E1, Y1 silently drops any whose value isn't set on its side, and the CT refuses to render. Lenient CTs were already rendering — they pick out what they recognize from the response.

**MD5s:** Stock `6442b137d3074e5ac9a654de83a4941a` → Output `1347e1b337879840ad2f66597836b05f`.

---

## `patch_mtkbt_odex.py`

Patches `MtkBt.odex` with four byte edits and recomputes the DEX adler32 checksum embedded in the ODEX header.

**F1** at file `0x3e0ea` (1 byte): `0a → 0e`. `BTAvrcpProfile.getPreferVersion()` returns the BlueAngel-internal flag value 14 instead of 10. This is internal flag bookkeeping inside MtkBt's Java-side dispatcher — it unblocks 1.3+ command handling on a stack that was originally compiled for an earlier AVRCP version. The wire shape is unchanged; we ship AVRCP 1.3 PDUs only. See [`BT-COMPLIANCE.md`](BT-COMPLIANCE.md) §1.

**F2** at file `0x03f21a`: `BluetoothAvrcpService.disable()` resets `sPlayServiceInterface = false`. Fixes a BT-toggle bug where the service tears itself down prematurely on second activation because the flag is left stale across restarts.

**Cardinality NOP — TRACK_CHANGED** at file `0x03c530`: NOPs the `if-eqz v5, :cond_184` cardinality gate in `BTAvrcpMusicAdapter.handleKeyMessage`'s sswitch_1a3 (event 0x02 case). Java's `mRegisteredEvents` BitSet is permanently empty (Java-side AVRCP TG bookkeeping isn't updated by our trampolines), so without this NOP `notificationTrackChangedNative` is never invoked. With it, the native fires on every `metachanged` broadcast emitted by the music app and lands in T5 (libextavrcp_jni.so). Pairs with T5.

**Cardinality NOP — PLAYBACK_STATUS_CHANGED** at file `0x03c4fe`: same idiom for sswitch_18a (event 0x01 case). Without this, `notificationPlayStatusChangedNative` is never invoked. With it, the native fires on every `playstatechanged` broadcast and lands in T9. Pairs with T9.

**MD5s:** Stock `11566bc23001e78de64b5db355238175` → Output `fa2e34b178bee4dfae4a142bc5c1b701`.

---

## `patch_libaudio_a2dp.py`

Single-byte cond-flip in `_ZN20android_audio_legacy18A2dpAudioInterface18A2dpAudioStreamOut9standby_lEv` (the AOSP A2DP HAL's standby path).

**AH1 — `beq 8684 → b 8684`** at file `0x000086ab` (1 byte): `0x0a` → `0xea`. ARM condition-code flip from `EQ` to `AL` (always). Forces standby_l's `if (mIsStreaming != 0) call a2dp_stop` guard to ALWAYS skip the call site. The instructions at 0x86ac-0x86b8 (`ldr r0, [r4,#40]; bl a2dp_stop@plt; mov r5, r0; b 8684`) become unreachable; standby still completes (`release_wake_lock`, `mStandby = 1`, return) but no AVDTP SUSPEND fires on the wire.

**Why this site.** AudioFlinger's silence-timeout (~3 s after the music app stops writing samples) calls `A2dpAudioStreamOut::standby` → `standby_l`, the only HAL-side path that calls `a2dp_stop`. NOPing that call leaves the AVDTP source stream alive while AudioFlinger thinks the HAL is in standby; the next `write()` after PLAYING resumes pushes samples into the same open AVDTP session. Per AVDTP 1.3 §8.13 / §8.15: PAUSED leaves the source stream paused-but-up; SUSPEND is reserved for explicit policy changes.

**MD5s:** Stock `0d909a0bcf7972d6e5d69a1704d35d1f` → Output `adbd98afeb5593f1ffe3b90acd0f2536`.

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

Each arm calls the corresponding `PlayerService` method per AV/C Panel Subunit Spec semantics. `play(true)` runs `Static.setPlayValue()` after `IjkMediaPlayer.start()` to propagate the resume edge to the rest of the app. `pause(0x12, true)` tags the discrete PASSTHROUGH path (existing stock pause-reason values span `0xc..0x11`). `nextSong()` / `prevSong()` are the discrete-track variants distinct from FAST_FORWARD (0x49) / REWIND (0x48); reached only via Patch H/H′'s propagation path. `playOrPause()` keeps the legacy single-physical-key toggle semantic.

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

`BaseActivity.dispatchKeyEvent` is the foreground entry point for every music-app Activity (all extend `BaseActivity`). Stock returns `v0=1` (consumed) unconditionally, including for keycodes the activity doesn't handle — so `KEYCODE_MEDIA_PLAY` (126), `MEDIA_PAUSE` (127), `MEDIA_STOP` (86) never reach `PhoneFallbackEventHandler` → `AudioService` → `ACTION_MEDIA_BUTTON` → `PlayControllerReceiver`.

Patched: insert an early-return block after `move-result v2` gated on `keyCode ∈ {0x7e, 0x7f, 0x56, 0x57, 0x58}`. Check `KeyEvent.getRepeatCount()`: if `> 0` (framework-synthesized repeat — see Patch H″), silently consume (return TRUE); if `== 0` (genuine first press), return FALSE so the framework continues dispatch.

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

`MusicPlayerActivity` and other player-screen activities extend `BasePlayerActivity`, which overrides `dispatchKeyEvent` and `return p1=1` unconditionally — `BaseActivity.dispatchKeyEvent` (Patch H) is unreachable from those screens. `BasePlayerActivity.onKeyUp` matches only `KeyMap` entries (KEY_LEFT=88, KEY_RIGHT=87, KEY_MENU=4, KEY_ENTER=66, KEY_PLAY=85), so discrete media keycodes 126/127/86 fall through and get silently consumed.

Patched: insert the same five-keycode early-return block at the top of `BasePlayerActivity.dispatchKeyEvent`, with the same `repeatCount > 0 → silent consume` filter as Patch H, before the `Intrinsics.checkNotNull` call (defensive null-safe ordering):

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

Android 4.2.2's `InputDispatcher::synthesizeKeyRepeatLocked` synthesizes `KeyEvent` repeats independently of the kernel's `EV_REP` (which U1 patches off in `libextavrcp_jni.so:0x74e8`); for AVRCP-derived keycodes those synthetic repeats trigger `BasePlayerActivity.onKeyLongPress` at `repeatCount == 8` → music app enters FF/RW mode and stays stuck if the CT drops PASSTHROUGH RELEASE under subscribe load. H″ filters them: `getRepeatCount() > 0` → return TRUE (silent consume); `== 0` → propagate normally. Applies in both `BaseActivity` (Patch H) and `BasePlayerActivity` (Patch H′). The addition of `0x57` / `0x58` to the propagated keycode set is also part of H″.

**Patch B3** — `com.koensayr.PappSetReceiver` for AVRCP-driven Repeat / Shuffle Sets.

BroadcastReceiver class registered dynamically from `Y1Application.onCreate`. Listens for two actions:

| Action | Extra | Calls |
|---|---|---|
| `com.koensayr.y1.bridge.SET_REPEAT_MODE` | `value:I` (Y1 enum 0/1/2 = OFF/ONE/ALL) | `SharedPreferencesUtils.setMusicRepeatMode(I)` |
| `com.koensayr.y1.bridge.SET_IS_SHUFFLE` | `value:Z` | `SharedPreferencesUtils.setMusicIsShuffle(Z)` |

Same setters the in-app Settings screen calls when the Y1 user toggles Repeat / Shuffle, so `PlayerService` re-reads SharedPreferences at the next track-end and the playback behavior changes without an app restart. Receiver class lives under `com.koensayr.*` to avoid collisions with the existing `com.innioasis.y1.*` tree. The live CT-Set consumer is B5's `PappSetFileObserver`; B3 stays in tree as a no-op safety net.

**Patch B4** — `com.koensayr.PappStateBroadcaster` for Y1-side Repeat / Shuffle CHANGED relay.

`OnSharedPreferenceChangeListener` against the `"settings"` SharedPreferences (the same prefs file `SharedPreferencesUtils` reads/writes), registered from `Y1Application.onCreate`. Fires for any write to any key, filters to two:

| Key | Maps to | AVRCP §5.2.4 |
|---|---|---|
| `musicRepeatMode` (int 0/1/2) | AVRCP repeat 0x01/0x02/0x03 (OFF/SINGLE/ALL) | Tbl 5.20 |
| `musicIsShuffle` (boolean) | AVRCP shuffle 0x01/0x02 (OFF/ALL_TRACK) | Tbl 5.21 |

On match, reads both live values via `SharedPreferencesUtils.INSTANCE.getMusicRepeatMode()` / `getMusicIsShuffle()`, maps to the AVRCP enum bytes, calls `TrackInfoWriter.setPapp(repeat, shuffle)` so the music-app `y1-track-info[795..796]` reflects the new state immediately, and fires `com.android.music.playstatechanged` so MtkBt's BluetoothAvrcpReceiver wakes T9 → AVRCP §5.4.2 Tbl 5.36 `PLAYER_APPLICATION_SETTING_CHANGED` CHANGED via PLT `0x345c`.

`Y1Application.onCreate` calls `sendNow()` once on registration so a fresh music-app start syncs the file + downstream state to actual SharedPreferences values. The broadcaster also stashes itself in a static `sInstance` field so the GC doesn't reclaim it (Android's SharedPreferences holds `OnSharedPreferenceChangeListener` instances by weak reference — without a strong rooting reference the listener stops firing after the next GC cycle).

**Patch B5** — in-app `y1-track-info` production (`com.koensayr.y1.*` injected classes).

The music app is the canonical writer of the 1104-byte `y1-track-info` schema, the 16-byte `y1-trampoline-state` (initial create), and the 2-byte `y1-papp-set` (initial create). All three live in `/data/data/com.innioasis.y1/files/`. The trampoline chain in `libextavrcp_jni.so` reads them directly.

Four new classes under `com/koensayr/y1/` (smali sources at `src/patches/inject/com/koensayr/y1/`, copied into `smali/` — the primary DEX — at patcher time, so they load with `Y1Application` itself; `smali_classes2/` would route through `MultiDex.install`'s cache at `/data/data/com.innioasis.y1/code_cache/secondary-dexes/` which survives `/system/app/` reflashes and stales out the new classes):

| Class | Role |
|---|---|
| `trackinfo.TrackInfoWriter` | Singleton state holder + atomic file writer (tmp + rename, world-readable). 1104-byte schema: audio_id at bytes 0..7 via `syntheticAudioId(path) = (path.hashCode() & 0xFFFFFFFFL) | 0x100000000L`; title/artist/album UTF-8 codepoint-safe-truncated to 240 B; duration/position/state-time BE u32; play_status / natural_end / battery / repeat / shuffle bytes at 792..796; track-num / total-tracks / playing-time / genre at 800..1103. `init(Context)` flushes the file immediately after creating it so MtkBt's first read returns the valid AVRCP defaults (Repeat=0x01 OFF, Shuffle=0x01 OFF) rather than the all-zero fill that would otherwise persist until the first mutator runs. `prepareFiles()` chmods all three files world-rw / world-readable so MtkBt's `bluetooth` uid can `open()` them. `onFreshTrackChange()` (always-reset variant) is called from `PlaybackStateBridge.onEarlyTrackChange` — unconditionally zeroes `mPositionAtStateChange` + `mLastKnownDuration` and stamps `mLastFreshTrackChangeAt`, since the music-app's `restartPlay() → pause()` updates `mCachedAudioId` to the new track's id before our hook can snapshot the old, defeating any audio_id dedup at this entry. `onTrackEdge()` (dedup variant) stays for the OnPreparedListener path where same-track re-prepares mustn't disturb the live-position baseline. `wakeTrackChanged()` / `wakePlayStateChanged()` fire `com.android.music.metachanged` / `playstatechanged` via the stored Application Context — the music app's `PlayerService` doesn't fire these broadcasts itself (it uses an internal `MY_PLAY_SONG` action), so the trampolines' wake path needs them to be synthesised here. |
| `playback.PlaybackStateBridge` | Stateless static dispatcher. `onPlayValue(II)V` maps the music-app's `Static.setPlayValue` newValue (0/1/3/5) to the AVRCP §5.4.1 Tbl 5.26 byte (STOPPED/PLAYING/PAUSED) then calls `TrackInfoWriter.wakePlayStateChanged()` so T9 emits PLAYBACK_STATUS / POS CHANGED on the state edge. On the PLAYING edge it also starts `PositionTicker`; on PAUSED / STOPPED it stops it. `onCompletion()V` latches a natural-end signal; the next `onPrepared()V` consumes it into `mPreviousTrackNaturalEnd`, resets position+time, then calls `wakeTrackChanged()` + `wakePlayStateChanged()` so T5 emits TRACK_CHANGED / REACHED_END / REACHED_START and T9 emits PLAYBACK_POS CHANGED for the position reset. `onError()V` clears the latch. |
| `playback.PositionTicker` | `Runnable` posted to a main-thread `Handler` every 1000 ms while playing. Each tick calls `TrackInfoWriter.wakePlayStateChanged()` so T9 emits PLAYBACK_POS_CHANGED CHANGED with the live-extrapolated position. Started from `PlaybackStateBridge.onPlayValue` on PLAYING edges, stopped on PAUSED / STOPPED. AVRCP 1.3 §5.4.2 Tbl 5.33 leaves the cadence to the TG; 1 s is the conventional minimum interval a 1.3 CT will display playhead at. |
| `battery.BatteryReceiver` | `Intent.ACTION_BATTERY_CHANGED` consumer. Bucket-maps to AVRCP §5.4.2 Tbl 5.35 (FULL_CHARGE / EXTERNAL / CRITICAL / WARNING / NORMAL). Sticky-broadcast value is processed at registration time so cold boot has a real bucket before the next CHANGED tick. |
| `papp.PappSetFileObserver` | `FileObserver` on `/data/data/com.innioasis.y1/files/y1-papp-set` (CLOSE_WRITE). T_papp 0x14 in `libextavrcp_jni.so` writes the file on every CT-initiated PApp Set; the observer reads the 2-byte (attr_id, value) tuple and calls `SharedPreferencesUtils.setMusicRepeatMode` / `setMusicIsShuffle` directly — no Intent hop. |

Existing-file edits (smali prepends, no logic replacement):

| File | Inject |
|---|---|
| `smali_classes2/com/innioasis/y1/utils/Static.smali` | Top of `setPlayValue(II)V` — `invoke-static {p1, p2}, …PlaybackStateBridge;->onPlayValue(II)V`. Single canonical state-edge entry; catches every play/pause/stop/resume regardless of UI foreground state. |
| `smali/com/innioasis/y1/service/PlayerService.smali` | Top of six listener lambdas (`initPlayer$lambda-{10,11,12}` IjkMediaPlayer Bilibili-IJK `OnCompletion`/`OnPrepared`/`OnError`; `initPlayer2$lambda-{13,14,15}` same for `android.media.MediaPlayer`) — each gets one `invoke-static` to the matching `PlaybackStateBridge` callback. Plus `setCurrentPosition(J)V` head (B5.2a) → `PlaybackStateBridge.onSeek`; `toRestart()V` 3 × `setDataSource` sites (B5.2b) → `PlaybackStateBridge.onEarlyTrackChange` (~100-500 ms early TRACK_CHANGED before prepareAsync completes); `playerPrepared()V` 2 × `iput-boolean playerIsPrepared:=true` sites (B5.2c) → `PlaybackStateBridge.onPlayerPreparedTail` (post-prepare flush so `getDuration()` is captured before broadcasting; without this, `flushLocked` from OnPreparedListener runs ~26 ms before the prepared flag flips and reports the previous track's stale duration). |
| `smali/com/innioasis/y1/Y1Application.smali` | `onCreate` `:cond_3` block, between B3 and B4. Brings up `TrackInfoWriter.init(Context)` + `PappSetFileObserver.start(Context)` + `BatteryReceiver.register(Context)`. Order matters: must run before B4's `sendNow()` so the cold-boot file write reflects live SharedPreferences Repeat/Shuffle, not the default OFF/OFF. |
| `smali/com/koensayr/PappStateBroadcaster.smali` (B4 product) | `sendNow()` tail — calls `TrackInfoWriter.setPapp(repeat, shuffle)` so the music-app file reflects the new state immediately, then fires `com.android.music.playstatechanged` so MtkBt's BluetoothAvrcpReceiver wakes T9 to emit PApp CHANGED on the wire. |
| `smali/com/koensayr/y1/battery/BatteryReceiver.smali` | `onReceive` tail — fires `com.android.music.playstatechanged` after each bucket transition so T9 reads the new file[794] and emits BATT_STATUS_CHANGED CHANGED. |

State sources, all read live from `PlayerService` accessors via `Y1Application.Companion.getPlayerService()`: `getPlayingMusic()`/`getPlayingSong()` for the current `Song` (title via `getSongName()`, plus `getArtist`/`getAlbum`/`getGenre`/`getPath`); `getDuration()`; `getMusicIndex()+1` for TrackNumber; `getMusicList().size()` for TotalNumberOfTracks. Position-at-state-change is captured at the `setPlayValue` edge with `SystemClock.elapsedRealtime()` for the lockstep clock the trampoline `T6` extrapolation expects.

**Patch B6** — AvrcpBinder smali (unused groundwork).

Two new classes routed to `smali_classes2/` (secondary DEX) because `classes.dex` sits at 99.7% of the 64K method cap after Patch B5:

| Class | Role |
|---|---|
| `avrcp.AvrcpBridgeService` | Service shell. Not declared in the music APK manifest, so unreferenced at runtime. |
| `avrcp.AvrcpBinder` | `Binder` implementing `IBTAvrcpMusic` + `IMediaPlaybackService` onTransact in smali. Skips `strictModePolicy` + descriptor string and dispatches by transact code (descriptor mismatches across ROM variations have historically aborted `registerCallback` on `enforceInterface`). Codes implemented: 1 (`registerCallback`); 2 (`unregisterCallback`); 3 (`regNotificationEvent` — ACK true; returning false leaves MtkBt's `mRegBit` empty and notifyTrackChanged is dropped); 5 (`getCapabilities` — return `[0x01, 0x02]`); 6-13 (transport keys via `sendMediaKey` broadcast). Every other code: `writeNoException` + `return true` (ack-only). Not instantiated — Y1Bridge.apk hosts the live Binder MtkBt resolves to. The smali stays in tree so MtkBt.odex component-bind work doesn't have to recreate it. |

**`--debug` instrumentation** (gated on `KOENSAYR_DEBUG=1`; `apply.bash --debug` sets it). When enabled the patcher injects `Log.d("Y1Patch", …)` traces at every metadata-relevant entry point and inline value-bearing `_dbgKV(String key, long val)` / `_dbgLogTrampolineState(String tag)` calls at the diagnostic-critical sites. Nothing is added to release builds — helpers and call sites are gated in `patch_y1_apk.py` itself.

| Layer | Coverage |
|---|---|
| Stock smali entry traces | `PlayControllerReceiver.onReceive`; `BaseActivity.dispatchKeyEvent` + `BasePlayerActivity.dispatchKeyEvent`; `PlayerService` — `play / pause / playOrPause / stop / nextSong / prevSong / restartPlay / playerPrepared / toRestart`. |
| Inject-tree entry traces | `TrackInfoWriter` — `init / setPlayStatus / onSeek / markCompletion / markError / onFreshTrackChange / onTrackEdge / setBattery / setPapp / flush / flushLocked / wakeTrackChanged / wakePlayStateChanged`. `PlaybackStateBridge` — `onPlayValue / onEarlyTrackChange / onPrepared / onPlayerPreparedTail / onCompletion / onSeek / onError`. `PositionTicker` — `start / stop / run`. `BatteryReceiver` — `register / onReceive`. `PappSetFileObserver` — `start / onEvent / dispatch`. `NowPlayingRefresher` — `onResume / onPause / refresh / run`. |
| Inline value-bearing | `TrackInfoWriter.onTrackEdge` → `onTE.old`, `onTE.new`, `onTE.EDGE_DETECTED`. `TrackInfoWriter.flushLocked` → `fL.id`, `fL.pos`, `fL.dur`, `fL.ps` (audio_id, position-at-state-change, last-known-duration, AVRCP play-status). `TrackInfoWriter.onSeek` → `onSeek.in`, `onSeek.SUPPRESSED.dtMs`, `onSeek.APPLIED.pos`. `TrackInfoWriter.setPlayStatus` → `sPS.from`, `sPS.to`. `PlaybackStateBridge.onPlayValue` → `oPV.newVal`, `oPV.reason`. |
| Trampoline-state byte dump | `TrackInfoWriter.wakeTrackChanged` → `wTC.pre` + `wTC.post` (post via `Handler.postDelayed(50 ms)`). Same for `wakePlayStateChanged` → `wPSC.pre` + `wPSC.post`. Format: `tramp.state[0..19]=HH …` × 20 hex bytes. Authoritative semantics from `src/patches/_trampolines.py` (T9 stack frame): `[0..7]`=last-synced track_id mirror, `[9]`=last_play_status, `[10]`=last_battery_status, `[11]`=last_repeat_avrcp, `[12]`=last_shuffle_avrcp, `[13]`=sub_pos_changed (0x05), `[14]`=sub_play_status (0x01), `[15]`=sub_papp (0x08), `[16]`=sub_track_changed (0x02), `[17]`=sub_track_reached_end (0x03), `[18]`=sub_track_reached_start (0x04), `[19]`=sub_battery (0x06). Successive pre/post captures answer whether T5/T9 actually emitted CHANGED on the wire (the relevant mirror byte updates between pre and post; e.g., `[9]` flips from `1` to `2` after a PAUSE if T9 emitted PLAYBACK_STATUS_CHANGED) or got gated out (mirror byte stays stale). Gate bytes `[13..19]` typically appear armed at `1` even when CHANGED fires correctly — strict CTs re-`RegisterNotification` faster than the 50 ms post-window so T8 INTERIM re-arms the gate. |

Tail with `adb logcat -s Y1Patch:*` to observe the metadata pipeline live; pipe to a file for post-test analysis.

`AndroidManifest.xml` is NOT modified by the patcher. `com.innioasis.y1` declares `sharedUserId="android.uid.system"`, which constrains the package's signing key to the OEM platform key. Any change to AndroidManifest.xml bytes invalidates `META-INF/MANIFEST.MF`'s recorded SHA1-Digest, JarVerifier throws SecurityException, PackageParser logs "no certificates at entry AndroidManifest.xml; ignoring!", and PackageManager drops the package. JarVerifier doesn't digest-check classes.dex / classes2.dex / resources at scan time — that's why DEX-only modifications work. The intent-filter `<service>` MtkBt's `bindService` resolves to lives in Y1Bridge.apk's manifest, which is self-signed and unconstrained by the platform key requirement.

**Apktool reassembly:** `apktool d --no-res` decode → smali edits → `apktool b` reassemble (the post-DEX aapt step fails because resources weren't decoded, but DEX is already built by then; the script intentionally ignores the exit code). Patched DEX bytes are dropped into a copy of the original APK with `META-INF/` + `AndroidManifest.xml` preserved bit-exact.

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
