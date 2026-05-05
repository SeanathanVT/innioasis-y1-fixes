# Proxy Build — User-Space AVRCP TG Plan

Concrete plan for delivering AVRCP metadata to peers (Sonos, cars). Builds on the empirics from Trace #12 in [INVESTIGATION.md](../INVESTIGATION.md).

## Why a proxy is needed

mtkbt is a 1.0-class AVRCP TG. It:
- Receives 1.3+ COMMANDs (now demonstrated end-to-end with `--avrcp-min`).
- Has the L2CAP/AVCTP plumbing that gets bytes on the wire.
- Has working response-builder C functions in `libextavrcp.so` (`btmtk_avrcp_send_get_capabilities_rsp` etc.) that mtkbt's *own* dispatcher never calls for non-PASSTHROUGH inbound.

The proxy reuses the existing response builders by calling them from a **code-cave trampoline** in `libextavrcp_jni.so`. No new IPC, no MtkBt.apk smali patch, no Y1MediaBridge changes for the GetCapabilities + RegisterNotification handshake. Y1MediaBridge stays involved for the actual track metadata (which it already publishes via its `MediaBridgeService`).

## Status of this plan as of 2026-05-05

Done:
- [x] SDP-shape patches (V1, V2, S1) — make Sonos send AVRCP 1.3+ commands
- [x] mtkbt P1 — routes inbound VENDOR_DEPENDENT through msg 519 emit path
- [x] J1 attempted and rolled back — wrong dispatch (size==8 path is for PASSTHROUGH)
- [x] **Trampoline T1** — code-cave at 0x7308 calls `btmtk_avrcp_send_get_capabilities_rsp` for inbound GetCapabilities (PDU 0x10). Hardware-verified iter5 2026-05-05: Sonos received the response and progressed to sending RegisterNotification frames (size:13 at 2-second intervals).
- [x] **Trampoline T2** — code-cave at 0x72d0 (overwrites `classInitNative` debug stub) calls `btmtk_avrcp_send_reg_notievent_track_changed_rsp` for inbound RegisterNotification(EVENT_TRACK_CHANGED). **Hardware-verified iter6 2026-05-05**: 2× msg=544 size=40 outbound on inbound size:13, and Sonos progressed to sending size:45 GetElementAttributes (PDU 0x20, 26 retries at 2-second intervals).
- [x] **T4 stub iter7** — first attempt: extended LOAD #1 to 0xac5c (8 bytes), restored only r0. Hardware-tested but didn't generate msg=520 — Sonos kept retrying. Diagnosis: 0x65bc also relies on `lr = SIZE` (loaded earlier at 0x644e from sp+374), and our `bl.w` at 0x6538 clobbered lr to 0x653c.
- [x] **T4 stub iter8** — extended LOAD #1 to 0xac60 (12 bytes); restores BOTH `r0 = r5+8` AND `lr = halfword at sp+374` before falling through to 0x65bc. T2's unknown branch at 0x72f4 points to 0xac54. Hardware-verified iter9 (2026-05-05): msg=520 NOT_IMPLEMENTED finally flows for unhandled frames. **Major side effect**: AVRCP service stops restart-looping → PASSTHROUGH play/pause now works on Sonos (transport controls). Output md5: `fbe2670b1e61953730edf3cf3e8a29b5`.
- [x] **iter10: advertise only TRACK_CHANGED** — Two-byte change: T1's events count `5 → 1`, events_data `01 02 09 0a 0b → 02`. **Hardware-verified iter10**: Sonos sent 1265× size:13 + 1264× size:45 in a tight loop — proves the full T1+T2 path engages and Sonos asks for metadata, but Sonos won't stop polling without a real T4 response.
- [x] **iter11: full T4 (Title-only with hardcoded "Y1 Test")** — replaces the 12-byte T4 stub with a 68-byte trampoline. **Hardware-verified**: "Y1 Test" displayed on Sonos Now Playing screen — first ever AVRCP metadata delivered to a peer device. Argument layout for `btmtk_avrcp_send_get_element_attributes_rsp` confirmed empirically (r1=0, r2=transId, r3=0, sp[0]=attribute_id LSB, sp[4]=0x6a, sp[8]=length, sp[12]=ptr). 70Hz response loop continues because TRACK_CHANGED INTERIM with track_id=0xFF...FF tells Sonos to keep re-checking.
- [x] **iter12: multi-attribute T4 dispatch** — extends T4 to 152 bytes, parses inbound num_attributes + attribute_ids, dispatches on attribute_id LSB to per-attribute hardcoded strings: 0x01 → "Y1 Title", 0x02 → "Y1 Artist", 0x03 → "Y1 Album". Other attribute IDs (0x04 TrackNumber, 0x05 TotalTracks, 0x06 Genre, 0x07 PlayingTime) are silently skipped. Each requested+supported attribute generates one msg=540 outbound. LOAD #1 grows to 0xacec. **Pending hardware verification.** Output md5: `fa6191d6ce8170f5ef5c8142202c8ba5`.

Pending (this document is the plan):
- [ ] Hardware test iter12 — does Sonos now display title=Y1 Title, artist=Y1 Artist, album=Y1 Album as a fully-populated row? Sonos's display behavior may also reveal whether multi-attribute responses are interleaved correctly or need fragmentation.
- [ ] iter13: Y1MediaBridge writes /data/local/tmp/y1-track-info on track change; T4 reads it via open/read/close instead of using hardcoded strings.
- [ ] Trampoline T3 — call `btmtk_avrcp_send_reg_notievent_playback_rsp` for inbound RegisterNotification(EVENT_PLAYBACK_STATUS_CHANGED). Optional unless Sonos blocks on it.
- [ ] Trampoline T4 — call `btmtk_avrcp_send_get_element_attributes_rsp` for inbound GetElementAttributes (needs current track info from Y1MediaBridge — see "Track-data plumbing" below). **This is what actually puts metadata on Sonos's screen.**

## Concrete addresses (stock libextavrcp_jni.so md5 `fd2ce74db9389980b55bccf3d8f15660`)

### Receive function (where we redirect from)

`_Z17saveRegEventSeqIdhh` is at file 0x5ee4. The actual handler body starts at 0x5f0c (the prior 0x28 bytes are a small `saveRegEventSeqId` helper that shares the symbol). The size-dispatch in the body:

| File | Insn | Effect |
|---|---|---|
| 0x6452 | `cmp.w lr, #3` | size==3 → PASSTHROUGH path |
| 0x6524 | `cmp.w lr, #8` | size==8 → BT-SIG vendor-check path |
| 0x6538 | `bne 0x65bc` | otherwise → 0x65bc "unknow indication" + default reject |

The redirect site is **the bne at 0x6538**. Patch it to `bl <trampoline>` (4 bytes, Thumb-2 long branch). The trampoline then dispatches based on the actual inbound frame bytes (PDU at data+6 if AV/C-stripped) and calls the appropriate response builder.

### PLT entries (response builders)

GOT offset (R_ARM_JUMP_SLOT) → PLT stub address. The PLT stub is what we `blx` from Thumb code:

| Function | GOT @ | PLT stub @ |
|---|---|---|
| `btmtk_avrcp_send_get_capabilities_rsp` | `0xcfd4` | `0x35dc` |
| `btmtk_avrcp_send_get_element_attributes_rsp` | `0xcfb0` | TBD (find via `bl … <…@plt>` xref) |
| `btmtk_avrcp_send_get_playstatus_rsp` | `0xcfac` | TBD |
| `btmtk_avrcp_send_reg_notievent_track_changed_rsp` | `0xcf0c` | TBD |
| `btmtk_avrcp_send_reg_notievent_playback_rsp` | `0xcf14` | TBD |
| `btmtk_avrcp_send_pass_through_rsp` (reference) | `0xcfec` | `0x3624` |

Other PLT stubs can be found by grepping `/tmp/libavrcp.dis` for `<…@plt>` and checking the GOT offset they jump through (patterns like `add ip, pc, #...; ldr pc, [ip, …]`).

### Code-cave for the trampoline

No usable zero-runs in `.text` (it's contiguous 0x3660–0x7764). Three viable options for placement:

**Option A (preferred): overwrite `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa`** at `0x7308`.

```
00007308 <…testparmnum…>:
    7308–732B: log args + return 0    (~36 bytes)
    732C–7337: literal pool             (~12 bytes)
```

`testparmnum` takes 12 jbyte args, logs each, returns 0. The name suggests a debug method. It's nearly certainly never called in normal operation. Total ~44 bytes available. Sufficient for a single trampoline.

**Option B**: overwrite `_Z33BluetoothAvrcpService_getPlayerIdP7_JNIEnvP8_jobject` at `0x7300` (only 4 bytes — too small alone, but combined with testparmnum gives ~52 bytes contiguous).

**Option C**: append a new section to the ELF. Out of scope for this plan; only do this if (A) and (B) are insufficient.

## Trampoline T1 (GetCapabilities) — final

Implementation in `src/patches/patch_libextavrcp_jni_minimal.py`. Two patches:

- **R1 (redirect)** at file `0x6538`: 4 bytes `40 d1 09 25` (`bne.n 0x65bc; movs r5, #9`) → `00 f0 e6 fe` (`bl.w 0x7308`).
- **T1 (trampoline)** at file `0x7308`, 40 bytes (overwrites `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa`):

```
0x7308: ldrb.w r0, [sp, #382]         ; PDU byte (AV/C body+4)
0x730c: cmp r0, #0x10                  ; GetCapabilities?
0x730e: bne.n 0x732c                   ; no → fall_through
0x7310: adr r3, 0x7324                 ; events_data ptr
0x7312: add.w r0, r5, #8               ; r0 = conn buffer (r5 from prologue)
0x7316: movs r1, #0
0x7318: movs r2, #5                    ; events count
0x731a: blx 0x35dc                     ; PLT → btmtk_avrcp_send_get_capabilities_rsp
0x731e: b.w 0x712a                     ; mov r9,#1; canary; epilogue
0x7322: nop
0x7324: 01 02 09 0a 0b 00 00 00        ; supported events
0x732c: b.w 0x65bc                     ; fall_through (original "unknow")
```

**Resolved questions:**
1. *Stack offset for PDU.* The redirect uses `bl.w` rather than a function-frame-altering call sequence; the trampoline pushes nothing, so its sp == caller's sp at the redirect site. Confirmed via disassembly that the size==3 path reads `[sp, #379]` for the AV/C body's byte-1 (`op_id|state` for PASSTHROUGH), so `[sp, #378]` is the body start. For VENDOR_DEPENDENT, byte-4 of the body = PDU, hence `[sp, #382]`.
2. *r5 preservation.* `bl.w` clobbers `lr` only; `r5` is preserved (AAPCS callee-saved, untouched by trampoline).
3. *r0 setup.* The function has *already* executed `add.w r0, r5, #8` at 0x6528 by the time we reach the redirect at 0x6538 — so r0 is the conn buffer on entry to the trampoline. The trampoline reads PDU into r0 (clobbering it) for the cmp, then re-derives `r0 = r5+8` before the response-builder call.
4. *Return path.* `b.w 0x712a` lands on `mov.w r9, #1` (return value = 1), falling through to the stack-canary check at 0x712e and the function epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).

## Track-data plumbing for T4 (GetElementAttributes)

T4 needs current title/artist/album. Y1MediaBridge already has these in `MediaBridgeService.mTrackTitle / mTrackArtist / mTrackAlbumName`. To expose them to native code, two options:

- **Sysprop**: Y1MediaBridge writes `persist.y1.track.title` etc. on track change; native trampoline reads via `__system_property_get`.
- **Shared file**: Y1MediaBridge writes `/data/local/tmp/y1-track-info` (or similar) on track change; trampoline reads.

Sysprops are cleaner but `persist.*` writes require system permissions. `/data/local/tmp/` is shell-writeable but world-readable, fine for our use case. Y1MediaBridge runs as system, so writing is fine.

Recommend the file approach for first pass — simpler than wiring up native sysprop access in the trampoline.

## Task ordering

1. Find PLT stubs for `…get_element_attributes_rsp`, `…get_playstatus_rsp`, `…reg_notievent_track_changed_rsp`, `…reg_notievent_playback_rsp` (grep `<…@plt>` in libavrcp.dis).
2. Confirm `testparmnum` is genuinely unused (grep MtkBt.apk smali for any `testparmnum` references; should be zero).
3. Build trampoline T1 (GetCapabilities response). Test on hardware. Watch for `[BT][AVRCP]+_activate_1req` mention of the response, or `cardinality` going non-zero on inbound RegisterNotification, or Sonos starting to display ANYTHING.
4. Build T2/T3 (RegisterNotification responses). These ack EVENT_TRACK_CHANGED + EVENT_PLAYBACK_STATUS_CHANGED.
5. Build T4 (GetElementAttributes) after Y1MediaBridge is updated to publish track info to a shared file.
6. Outbound notifications (when track changes, push EVENT_TRACK_CHANGED notification to peer): probably also feasible via the same `…reg_notievent_track_changed_rsp` PLT entry, called from a Java side that's wired to track-change events.

## Estimated effort

- T1 alone: 1–2 days of careful binary work + flash/test cycles.
- T1+T2+T3: 3–5 days.
- T4 (with Y1MediaBridge plumbing): another 2–3 days.
- Outbound notifications: 2–4 days.

Total: 1–2 weeks of focused work. Consistent with INVESTIGATION.md's earlier estimate.
