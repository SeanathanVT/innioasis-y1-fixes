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

Pending (this document is the plan):
- [ ] Trampoline T1 — call `btmtk_avrcp_send_get_capabilities_rsp` for inbound GetCapabilities
- [ ] Trampoline T2 — call `btmtk_avrcp_send_reg_notievent_track_changed_rsp` for inbound RegisterNotification(EVENT_TRACK_CHANGED)
- [ ] Trampoline T3 — call `btmtk_avrcp_send_reg_notievent_playback_rsp` for inbound RegisterNotification(EVENT_PLAYBACK_STATUS_CHANGED)
- [ ] Trampoline T4 — call `btmtk_avrcp_send_get_element_attributes_rsp` for inbound GetElementAttributes (needs current track info from Y1MediaBridge — see "Track-data plumbing" below)

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

## Trampoline T1 sketch (GetCapabilities)

Called from saveRegEventSeqId via `bl <trampoline>` (replacing `bne 0x65bc` at file 0x6538).

```
trampoline_t1:
    push    {r4, r5, lr}              ; preserve regs we'll touch
    sub     sp, #8                    ; events array buffer

    ; --- detect frame type ---
    ; saveRegEventSeqId has the inbound data buffer accessible via sp.
    ; The data layout at sp+378 (per the size==3/8 paths' reads) is
    ; the AV/C-stripped body. We need to peek at byte 4 (PDU) and
    ; check if it's 0x10 (GetCapabilities).
    ; …compute caller's sp+378 offset… (depends on exact stack frame
    ; we land in; finalize during trampoline write)
    ldrb    r4, [<caller_sp>+382]     ; PDU byte (or whichever offset
                                      ;  matches our captured layout)
    cmp     r4, #0x10
    bne     trampoline_fallthrough    ; not GetCapabilities → defer

    ; --- build events array on stack ---
    movs    r0, #1
    strb    r0, [sp, #0]              ; PLAYBACK_STATUS_CHANGED
    movs    r0, #2
    strb    r0, [sp, #1]              ; TRACK_CHANGED
    movs    r0, #9
    strb    r0, [sp, #2]              ; NOW_PLAYING_CONTENT_CHANGED
    movs    r0, #10
    strb    r0, [sp, #3]              ; AVAILABLE_PLAYERS_CHANGED
    movs    r0, #11
    strb    r0, [sp, #4]              ; ADDRESSED_PLAYER_CHANGED

    ; --- arg setup for btmtk_avrcp_send_get_capabilities_rsp ---
    add     r0, r5, #8                ; r0 = "conn buffer" — r5 was set
                                      ;  in saveRegEventSeqId at 0x5f30
                                      ;  from `bl 0x36c0`. We'll need
                                      ;  to either preserve r5 across
                                      ;  the redirect or pass it as arg.
    movs    r1, #0
    movs    r2, #5                    ; events count
    mov     r3, sp                    ; events array ptr
    blx     0x35dc                    ; bl …get_capabilities_rsp@plt

    add     sp, #8
    pop     {r4, r5, pc}

trampoline_fallthrough:
    ; Frame isn't GetCapabilities — fall back to default-reject path.
    add     sp, #8
    pop     {r4, r5, lr}
    b       0x65bc                    ; original "unknow indication" target
```

Estimated size: ~50 bytes. Fits in testparmnum's slot.

## Open questions to resolve when writing the actual asm

1. **Exact offset of the inbound data buffer** in saveRegEventSeqId's stack frame at the redirect point — this depends on which redirect site we use (`bne 0x6538` lands inside the function with sp at one specific value; from a `bl` to a code-cave the trampoline's stack frame is a child, so it'd need to read caller-sp via offsets relative to LR/saved fp). May be cleaner to do the redirect via a `b` (not `bl`), keeping us in the parent frame.
2. **r5's preservation across the redirect**. saveRegEventSeqId saves r5 in its prologue. If we redirect via `bl`, r5 is preserved across our call automatically (callee-saved). If via `b`, r5 stays in scope.
3. **PDU offset detection**. Our inbound CMD_FRAME_IND has data_len:9 with bytes `00 00 19 58 10 00 00 01 02`. If we read byte at data+4 we get `0x10` (PDU). The actual sp offset depends on saveRegEventSeqId's data buffer placement.
4. **Return path semantics**. After `btmtk_avrcp_send_get_capabilities_rsp` succeeds, what does the caller's flow expect? We should NOT call `btmtk_avrcp_send_pass_through_rsp` (avoids the bogus PASSTHROUGH-shaped response we saw in iter4). Probably skip ahead to the function's epilogue at 0x6e74 → 0x711c.

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
