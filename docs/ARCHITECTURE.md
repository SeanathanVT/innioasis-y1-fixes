# AVRCP Metadata Architecture

How the Innioasis Y1 delivers AVRCP 1.3 metadata (Title/Artist/Album/TrackNumber/TotalNumberOfTracks/Genre/PlayingTime) to peer Controllers, given that the OEM Bluetooth stack is fundamentally an AVRCP 1.0 implementation that auto-rejects 1.3+ commands. We advertise 1.3 over AVCTP 1.2 (see `patch_mtkbt.py` V1/V2, with ESR07 §2.1 / Erratum 4969 SDP-record clarifications applied) and implement the 1.3 metadata feature set: `GetCapabilities` 0x10, `InformDisplayableCharacterSet` 0x17, `InformBatteryStatusOfCT` 0x18, `GetElementAttributes` 0x20 (all 7 §5.3.4 attributes packed in a single response), `GetPlayStatus` 0x30 (with `clock_gettime(CLOCK_BOOTTIME)` live-position extrapolation), `RegisterNotification` 0x31 with INTERIM coverage of events 0x01..0x07 and proactive CHANGED-on-edge for 0x01..0x06 (PLAYBACK_STATUS / TRACK_CHANGED / TRACK_REACHED_END / TRACK_REACHED_START / PLAYBACK_POS / BATT_STATUS), and explicit AV/C reject for Continuation 0x40/0x41. F1's MtkBt-internal-version flip is a Java-side dispatcher-unblock flag (BlueAngel internal value), not a wire-shape upgrade. See [`AVRCP13-COMPLIANCE.md`](AVRCP13-COMPLIANCE.md) §0 for spec citation discipline and §2 for the ICS Table 7 coverage scorecard.

This document covers the **full proxy architecture**: the trampoline chain that intercepts inbound AVRCP commands in `libextavrcp_jni.so`, calls the existing C response-builder functions (which were never wired up by the OEM Java side), and delivers spec-compliant 1.3 responses on the wire.

For **per-patch byte details**: see [`PATCHES.md`](PATCHES.md).
For **investigation history** (how we got here): see [`INVESTIGATION.md`](INVESTIGATION.md).
For **AVRCP 1.3 spec-coverage state**: see [`AVRCP13-COMPLIANCE.md`](AVRCP13-COMPLIANCE.md).

---

## TL;DR

A peer CT sends a stock AVRCP 1.3+ AV/C COMMAND → mtkbt routes it through msg-519 (P1 patch) → `libextavrcp_jni.so::saveRegEventSeqId` is intercepted at file 0x6538 (R1 patch) → a chain of trampolines (T1 / T2 stub / extended_T2 / T4 / T5 / T_charset / T_battery / T_continuation / T6 / T8 / T9) inspects the inbound PDU byte (and event_id, for PDU 0x31) and calls the matching `btmtk_avrcp_send_*_rsp` PLT entry directly → mtkbt builds a real AVRCP 1.3 response frame and emits it on the wire → the CT displays the metadata.

The trampolines live in unused/repurposed JNI debug methods (`testparmnum`, `classInitNative`) and in the page-alignment padding past the original LOAD #1 segment end (extended via `FileSiz`/`MemSiz` program-header surgery).

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
              │  and LOAD #2; LOAD #1 FileSiz/MemSiz     │
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

1. Write the trampoline blob at file 0xac54. **Current size: 1652 bytes (extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T8 + T9 + path strings + sentinel data); ~2368 bytes still free in the 4020-byte padding region.** U1 is a separate 4-byte NOP elsewhere in the binary that doesn't grow the blob.
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

The buffer is zeroed when **either** `arg1` is nonzero (explicit reset/finalize) **or** `arg2 == 0` (first attribute in a new response).

**Send trigger** (lines 0x22ee–0x2310):

```
r5 = arg2 + 1
if (arg2 + 1) == arg3 AND arg3 != 0:
    GOTO send         ; last attribute path

if (arg1 != 0) OR (arg3 == 0):
    GOTO send         ; finalize/legacy path

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

Per AVRCP 1.3 §5.3.4 a missing attribute is signalled by `AttributeValueLength=0` — Y1MediaBridge writes empty UTF-8 string slots when the underlying tag is absent (e.g., a flat audio file with no Genre tag), strlen returns 0, and the response builder packs an attribute header with no value bytes for that entry.

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

T1 advertises 7 events `[0x01..0x07]`, paired with T8 INTERIM coverage so the NOT_IMPLEMENTED rejects don't fire for any advertised event. Per the spec-compliance rule we advertise only what we actually implement; event 0x08 (PLAYER_APPLICATION_SETTING_CHANGED) is unadvertised because PlayerApplicationSettings (PDUs 0x11-0x16 + event 0x08) is deferred.

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

All currently shipped trampolines (extended_T2 / T4 / T5 / T6 / T8 / T9 — anything that calls a `reg_notievent_*_rsp` PLT) pass `r1 = 0` to take the spec-correct event-payload path. An earlier trampoline shape passed `r1 = transId` and silently hit the reject-shape path (see `INVESTIGATION.md` for the empirical history).

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
| T1 | jni 0x7308 (40 B) | Overwrites unused `testparmnum`. PDU 0x10 → calls `get_capabilities_rsp` via PLT 0x35dc, advertising the seven events 0x01..0x07 (PLAYBACK_STATUS / TRACK_CHANGED / TRACK_REACHED_END / TRACK_REACHED_START / PLAYBACK_POS / BATT_STATUS / SYSTEM_STATUS). |
| T2 stub | jni 0x72d0 (8 B) | Overwrites `classInitNative`. 4-byte `return 0` stub at 0x72d0 + 4-byte `b.w extended_T2` at 0x72d4 |
| extended_T2 + T4 + T5 + T_charset + T_battery + T_continuation + T6 + T8 + T9 | jni 0xac54 (1652 B) | NEW LOAD #1 extension, dynamically assembled. **extended_T2** (reactive RegisterNotification): reads track_id from y1-track-info into a stack buffer, writes `[track_id || transId || pad]` to y1-trampoline-state, replies INTERIM with `arg1=0` + REASON_INTERIM + **&sentinel_ffx8**. PDU 0x31 + event ≠ 0x02 → b.w T8. **T4** (reactive GetElementAttributes): emits track_changed_rsp CHANGED on track-id edge, then **7-attr** get_element_attributes_rsp covering all AVRCP 1.3 §5.3.4 attribute IDs 0x01..0x07. T4's pre-check dispatch: 0x20 → main, 0x17 → T_charset, 0x18 → T_battery, 0x30 → T6, 0x40/0x41 → T_continuation, else fall through to "unknow indication". **T5** (proactive on Y1 track change): emits the AVRCP §5.4.2 track-edge 3-tuple in spec order — `reg_notievent_reached_end_rsp` (event 0x03, PLT 0x3378, gated on `y1-track-info[793]==1` natural-end flag), then `track_changed_rsp` (event 0x02, PLT 0x3384, with sentinel_ffx8), then `reg_notievent_reached_start_rsp` (event 0x04, PLT 0x336c, unconditional). **T_charset / T_battery**: PDU 0x17 / 0x18 ack trampolines. **T_continuation**: explicit reject for PDU 0x40/0x41 (RequestContinuingResponse / AbortContinuingResponse) — routes to UNKNOW_INDICATION for an AV/C NOT_IMPLEMENTED reject, since `get_element_attributes_rsp` never sets `packet_type=01`. **T6** (PDU 0x30 GetPlayStatus): reads `y1-track-info[776..795]` (duration / position / state-change-time / playing_flag, BE on disk → REV → host order), calls `get_playstatus_rsp` PLT 0x3564, with `clock_gettime(CLOCK_BOOTTIME)` live-position extrapolation when playing. **T8** (RegisterNotification dispatcher for events ≠ 0x02): dispatches on event_id and calls the matching `reg_notievent_*_rsp` PLT for events 0x01 (PLAYBACK_STATUS_CHANGED → 0x339c, payload from y1-track-info[792]), 0x03/0x04 (TRACK_REACHED_END/START → 0x3378/0x336c, no payload), 0x05 (PLAYBACK_POS_CHANGED → 0x3360, position from y1-track-info[780..783] REV-swapped), 0x06 (BATT_STATUS_CHANGED → 0x3354, real bucket from y1-track-info[794]), 0x07 (SYSTEM_STATUS_CHANGED → 0x3348, canned `0x00 POWER_ON` — intentional, since while trampolines run the system is by definition POWER_ON; the canned value IS the real value, so there is no edge to fire CHANGED on). Unknown events fall through to "unknow indication". T8 handles INTERIM only; CHANGED-on-edge for 0x02/0x03/0x04 lives in T5 and for 0x01/0x05/0x06 in T9. **T9** (proactive PLAYBACK_STATUS_CHANGED + BATT_STATUS_CHANGED + PLAYBACK_POS_CHANGED): entered via `b.w T9` from the patched `notificationPlayStatusChangedNative` first instruction (libextavrcp_jni.so:0x3c88, paired with the sswitch_18a cardinality NOP at MtkBt.odex:0x3c4fe). Three independent edge / cadence checks: play_status edge (file[792] vs state[9]) → emit `reg_notievent_playback_rsp`; battery_status edge (file[794] vs state[10]) → emit `reg_notievent_battery_status_changed_rsp`; if file[792]==PLAYING, `clock_gettime(CLOCK_BOOTTIME)` + compute live_pos + emit `reg_notievent_pos_changed_rsp` (1 s cadence driven by Y1MediaBridge `mPosTickRunnable` firing `playstatechanged`). Single combined state-file write per fire if any edge fired. |
| Track-change native stub | jni 0x3bc0 (4 B) | First instruction of `notificationTrackChangedNative` rewritten to `b.w T5`. The Java side (after the MtkBt.odex sswitch_1a3 cardinality NOP) calls this native on every Y1MediaBridge track-change broadcast; T5 emits CHANGED on the AVRCP wire asynchronously to any inbound query. The remaining 196 B of the original native body are unreachable. |
| Play-status native stub | jni 0x3c88 (4 B) | First instruction of `notificationPlayStatusChangedNative` rewritten to `b.w T9`. Paired with the MtkBt.odex sswitch_18a cardinality NOP at 0x3c4fe so every Y1MediaBridge `playstatechanged` broadcast lands in T9. |
| LOAD#1 filesz | jni 0x64 | `0xac54 → 0xb2c8` (1652 B blob). |
| LOAD#1 memsz  | jni 0x68 | Same |

Stock md5s and patcher-output md5s are baked into the patcher headers; check them before quoting.

The JNI trampoline blob is built dynamically by `src/patches/_trampolines.py` using a tiny Thumb-2 assembler in `src/patches/_thumb2asm.py`. Both files are imported by `patch_libextavrcp_jni.py` at run time. Self-tests in `_thumb2asm.py` verify several encodings against known-good byte sequences (b.w, blx, addw, movw, ldrb.w, add immediate T3).

**Wire-level `track_id` choice.**

The wire-level `Identifier` field in TRACK_CHANGED notifications is pinned to the `0xFF×8` "not bound to a particular media element" sentinel per AVRCP 1.3 §5.4.2 Table 5.30 + ESR07 §2.2 (the printed `0xFFFFFFFF` in 1.3 is a typo; ESR07 clarifies the field is 8 bytes, sentinel form `0xFFFFFFFFFFFFFFFF`). This keeps CTs in "no stable identity, refresh on each event" mode rather than the alternative "stable identity, only refresh on CHANGED" mode that some CTs adopt when given a real synthetic id. The latter mode causes high-subscribe-rate CT classes to enter a tight `RegisterNotification` storm at ~90 Hz that saturates AVCTP and drops PASSTHROUGH release frames; the sentinel mode avoids that entirely while still being spec-conformant.

Per-track CHANGED edge information is delivered by T4/T5 detecting divergence between `y1-track-info[0..7]` and `y1-trampoline-state[0..7]` (comparison runs on real track_ids; the emitted wire packet uses the sentinel). The state file at `y1-trampoline-state[0..7]` holds the real synthetic audioId from `Y1MediaBridge.mCurrentAudioId` (= `path.hashCode() | 0x100000000L`) for that internal change-detection logic.

See [`INVESTIGATION.md`](INVESTIGATION.md) "Hardware test history per CT" for the empirical observations that drove this design choice.

### Y1MediaBridge ↔ trampoline file contract

Two files, both in `/data/data/com.y1.mediabridge/files/`:

- **y1-track-info** (1104 B, written by Y1MediaBridge on every state change). Full byte-level layout in [`AVRCP13-COMPLIANCE.md`](AVRCP13-COMPLIANCE.md) §4; summary:
  - 0..7      = `mCurrentAudioId` big-endian (synthetic track_id)
  - 8..775    = Title / Artist / Album (256 B each, UTF-8, null-padded)
  - 776..791  = duration_ms / pos_at_state_change_ms / state_change_time_sec (BE u32 each, T6 reads)
  - 792       = playing_flag (0=STOPPED / 1=PLAYING / 2=PAUSED, AVRCP §5.4.1 Tbl 5.26 enum, T6/T8/T9 read)
  - 793       = previous_track_natural_end u8 (T5 gate for TRACK_REACHED_END CHANGED)
  - 794       = battery_status u8 (AVRCP §5.4.2 Tbl 5.35 bucket, T8 INTERIM + T9 CHANGED-on-edge read)
  - 795..799  = reserved (PlayerApplicationSettings shuffle/repeat reservation)
  - 800..1103 = TrackNumber / TotalNumberOfTracks / PlayingTime / Genre (UTF-8 ASCII strings — T4 reads)
  - mode 0644 (world-readable so the BT process can open it)
- **y1-trampoline-state** (16 B, pre-created by Y1MediaBridge at startup, updated by the trampolines):
  - 0..7  = last track_id we told the CT about (updated by T4 after emitting CHANGED, and by extended_T2 / T5 after emitting CHANGED)
  - 8     = last RegisterNotification transId (updated by extended_T2)
  - 9     = last_play_status (T9 edge-detect)
  - 10    = last_battery_status (T9 edge-detect)
  - 11..15 = padding
  - mode 0666 (world-rw so the BT process can rewrite it)

Y1MediaBridge's `prepareTrackInfoDir()` is what ensures the BT process can reach both files: `setExecutable(true, false)` on the dir adds world-x for traversal; the y1-track-info file gets `setReadable(true, false)`; the y1-trampoline-state file gets both `setReadable` and `setWritable`.

### Code-cave inventory

| Region | Address | Size | Used by |
|--------|---------|------|---------|
| `testparmnum` | 0x7308 | 48 bytes | T1 (40 bytes used) |
| `classInitNative` | 0x72d0 | 48 bytes | T2 stub (8 bytes used; remaining 40 zero-filled, unreachable) |
| `notificationTrackChangedNative` | 0x3bc0 | 200 bytes | T5 entry stub (4 bytes `b.w T5` used; remaining 196 unreachable) |
| `notificationPlayStatusChangedNative` | 0x3c88 | 200 bytes | T9 entry stub (4 bytes `b.w T9` used; remaining unreachable) |
| LOAD #1 padding | 0xac54..0xbc07 | 4020 bytes | trampoline blob (1652 B), ~2368 free |
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
| 520    | JNI → mtkbt | `CMD_FRAME_RSP` generic ack/reject (PASSTHROUGH ack OR NOT_IMPLEMENTED) |
| 522    | JNI → mtkbt | GetCapabilities response (from `…send_get_capabilities_rsp`) |
| 540    | JNI → mtkbt | GetElementAttributes response (from `…send_get_element_attributes_rsp`) |
| 544    | JNI → mtkbt | RegisterNotification response (from `…send_reg_notievent_*_rsp`) |

---

## ARM/Thumb-2 instruction encoding gotchas (lessons from this work)

Patches add up — these tripped us up at least once each:

- **ADR T1** (16-bit) requires offset to be a multiple of 4 AND target to be 4-byte aligned. When emitting strings of non-4-aligned length, pad each string to the next 4-byte boundary so subsequent ADR targets stay aligned. ADR.W (32-bit) is more flexible.
- **POP {r4, lr}** is NOT 16-bit. Only `POP {regs, pc}` (which RETURNS) and `POP {low_regs}` are 16-bit. Restoring `lr` without returning needs `POP.W` (32-bit, 4 bytes). We solved this in T4 by not pushing/popping at all — `r4-r9` are restored by saveRegEventSeqId's epilogue at 0x7154 (`pop {r4-r9, sl, fp, pc}`).
- **ADD Rd, SP, #imm** (16-bit T1) requires imm to be a multiple of 4 AND in 0..1020. For arbitrary 12-bit immediates use `ADDW Rd, SP, #imm12` (T4, 32-bit, no rotation/alignment requirement).
- **bl.w** clobbers `lr`. **b.w** doesn't. **blx** changes ARM/Thumb mode based on the target's bit 0 (PLT stubs are at even addresses → switches to ARM, which is what we want).
- **AAPCS callee-saved regs (r4-r11)**: saveRegEventSeqId pushes them in its prologue and restores them in the epilogue at 0x7154. Our trampolines can trash r4-r9 freely without local push/pop — they'll be restored by the parent function's epilogue when we `b.w 0x712a`.

---

## Adding a new PDU handler (a recipe)

When adding a new T-trampoline (e.g., GetPlayStatus PDU 0x30):

1. **Find the response builder PLT entry** in `libextavrcp_jni.so` via `objdump -R … | grep <name>` and follow the GOT entry to its PLT stub.
2. **Disassemble the C function** in `libextavrcp.so` to learn its real argument semantics. Don't assume — the names (`arg1` etc.) don't tell you what the args mean. Look for:
   - Buffer reset condition (when does it `memset` the internal buffer?)
   - Send trigger condition (which args make it call `AVRCP_SendMessage`?)
   - Where transId comes from (usually `conn[17]`, not an arg)
3. **Allocate cave space** in the LOAD #1 padding region (currently ~2368 bytes free past 0xb2c8 — the trampoline blob is 1652 B; the padding region is 4020 B total, ending at LOAD #2's start at 0xbc08).
4. **Wire it into the chain**: change the previous trampoline's "unknown" branch (the `b.w` to `0x65bc` or to the next trampoline) to point at your new entry.
5. **End with**:
   - `b.w 0x712a` for the success path (lands on stack-canary check + epilogue).
   - For the fall-through path: restore `r0 = r5+8` and `lr = halfword[sp+374]` before `b.w 0x65bc`.
6. **Bump LOAD #1 `FileSiz`/`MemSiz`** to cover your new bytes.
7. **Verify with objdump** before committing — disassemble your bytes and confirm every branch resolves to the intended target.

---

## See also

- [`AVRCP13-COMPLIANCE.md`](AVRCP13-COMPLIANCE.md) — current ICS Table 7 coverage scorecard (PlayerApplicationSettings is the only Optional area still deferred).
- [`PATCHES.md`](PATCHES.md) — per-patch byte-level reference.
- [`INVESTIGATION.md`](INVESTIGATION.md) — chronological investigation history including the gdbserver capture work and dead-end paths.
- `src/patches/patch_libextavrcp_jni.py` — the patcher containing R1/T1/T2/T4. Header comments and PATCHES list are the source of truth for byte-level details.
