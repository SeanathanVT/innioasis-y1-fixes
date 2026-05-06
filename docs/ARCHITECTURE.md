# AVRCP Metadata Architecture

How the Innioasis Y1 delivers AVRCP 1.3 metadata (Title/Artist/Album) to peer controllers like Sonos, given that the OEM Bluetooth stack is fundamentally an AVRCP 1.0 implementation that auto-rejects 1.3+ commands. (We advertise 1.3 over AVCTP 1.2 — see `patch_mtkbt.py` V1/V2 — and implement only the 1.3 metadata feature set: `GetCapabilities` 0x10, `GetElementAttributes` 0x20, `RegisterNotification(TRACK_CHANGED)` 0x31. AVRCP 1.4's browsing channel, Now Playing list, and advanced player-application settings are not implemented.)

This document covers the **full proxy architecture**: the trampoline chain that intercepts inbound AVRCP commands in `libextavrcp_jni.so`, calls the existing C response-builder functions (which were never wired up by the OEM Java side), and delivers spec-compliant 1.4 responses on the wire.

For **per-patch byte details**: see [`PATCHES.md`](PATCHES.md).
For **investigation history** (how we got here): see [`INVESTIGATION.md`](INVESTIGATION.md).
For **iteration plans and pending work**: see [`PROXY-BUILD.md`](PROXY-BUILD.md).

---

## TL;DR

Sonos sends a stock AVRCP 1.3+ GetElementAttributes request (PDU 0x20 — same wire format in 1.3 and 1.4) → mtkbt routes it through msg-519 (P1 patch) → `libextavrcp_jni.so::saveRegEventSeqId` is intercepted at file 0x6538 (R1 patch) → a chain of three trampolines (T1, T2, T4) inspects the inbound PDU byte and calls the matching `btmtk_avrcp_send_*_rsp` PLT entry directly → mtkbt builds a real AVRCP 1.3 metadata response frame and emits it on the wire → Sonos displays the metadata.

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
                           Sonos (AVRCP 1.4 controller)
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
              │  bumped from 0xac54 to 0xace8)           │
              │                                          │
              │  PDU == 0x20:                            │
              │     3 sequential calls to PLT 0x3570     │
              │     (get_element_attributes_rsp):        │
              │                                          │
              │     Call 1 (idx=0, total=3, attr=Title): │
              │        buffer reset on idx==0,           │
              │        accumulate, no emit yet           │
              │     Call 2 (idx=1, total=3, attr=Artist):│
              │        accumulate, no emit               │
              │     Call 3 (idx=2, total=3, attr=Album): │
              │        idx+1 == total → EMIT msg=540     │
              │        with all 3 attributes packed in   │
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
| `sp+386-393`| identifier (8 bytes BE — track_id from RegisterNotification, or attribute identifier in GetElementAttributes) |
| `sp+394`    | num_attributes (in GetElementAttributes) |
| `sp+395+`   | attribute_ids, 4 bytes BE each (last byte is the LSB we dispatch on) |

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

iter7 only restored r0 → msg=520 still didn't flow because pass_through_rsp got `lr=0x653c` (the stale bl return address) as the SIZE arg and silently dropped. iter8/9 added the lr restore → msg=520 flows correctly. The side-effect of this fix was that the AVRCP service stopped restart-looping every 2 seconds (because it was no longer waiting on responses that never came), which made play/pause work for the first time on Sonos.

---

## ELF program-header surgery — extending LOAD #1

The original `libextavrcp_jni.so` has two LOAD segments:

```
LOAD #1: file 0x0..0xac54, vaddr 0x0..0xac54,  R+E
LOAD #2: file 0xbc08..0xc2a4, vaddr 0xcc08..0xd540, R+W
```

Between LOAD #1's end at file `0xac54` and LOAD #2's start at file `0xbc08`, the file contains **4276 zero bytes of page-alignment padding**. We can write code into that padding and bump LOAD #1's `FileSiz`/`MemSiz` (program-header at file offset `0x54`, fields at +16 and +20 within the phdr) to extend the executable mapping over our new code. **No other section/segment offsets shift** — `.dynsym`/`.text`/`.rodata`/`.dynamic`/`.rel.plt` etc. all stay byte-identical. The dynamic linker just maps slightly more file content as R+E.

The patcher does this with three PATCHES entries:

1. Write T4 trampoline bytes at file 0xac54 (currently 148 bytes, ~4128 free).
2. Update LOAD #1 program-header `p_filesz` at file 0x64: `0xac54 → 0xace8`.
3. Update LOAD #1 program-header `p_memsz` at file 0x68: `0xac54 → 0xace8`.

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
- OR `arg3 == 0` — single-shot / legacy mode (this is what iter11/12 accidentally hit)

It only **accumulates without emitting** when `arg1 == 0 AND arg3 != 0 AND (arg2+1) < arg3`.

**transId** is NOT one of the args. The function reads it from `conn[17]` (line 0x21f2: `ldrb r2, [r0, #17]`) and copies into the response's wire frame. So passing `arg2 = transId` (as iter11/12 did) was just abusing arg2 as an attribute-INDEX with the value of transId — Title got written to slot[transId] of the buffer with all other slots zero, and Sonos found the one valid attribute and used it.

### Calling pattern for a 3-attribute response (iter13)

```c
// All three calls share: conn=r5+8, arg1=0, arg3=3, charset=0x6a
send_rsp(conn, arg1=0, idx=0, total=3, attr=1, len=8, "Y1 Title");   // accumulate
send_rsp(conn, arg1=0, idx=1, total=3, attr=2, len=9, "Y1 Artist");  // accumulate
send_rsp(conn, arg1=0, idx=2, total=3, attr=3, len=8, "Y1 Album");   // (idx+1==total) → EMIT
```

**One** msg=540 IPC frame outbound containing all three attributes.

> **iter15/16/17a regression (2026-05-06):** the dynamically-assembled T4 was passing `arg2 = transId, arg3 = 0` — taking the `arg3 == 0` legacy path on every call and emitting 3 separate msg=540 frames per query. Sonos rendered each one in turn (visible flicker: Title appearing intermittently while Artist/Album swapped in/out). Diagnosed from the logcat ratio of 1299 msg=540 outbound to ~433 GetElementAttributes inbound during the iter17a hardware test. Fixed in iter17b by restoring the iter13 calling pattern above.

### Calling pattern for `…send_reg_notievent_track_changed_rsp` (PLT 0x3384, used by T2)

```c
void btmtk_avrcp_send_reg_notievent_track_changed_rsp(
    void* conn,           // r0 = r5+8
    uint8_t transId,      // r1 = transId from sp+368
    uint8_t reasonCode,   // r2 = 0x0F (INTERIM) or 0x0D (CHANGED)
    void* track_id_ptr    // r3 = pointer to 8-byte BE track_id
);
```

Cross-referenced with `notificationTrackChangedNative` at libextavrcp_jni.so:0x3bc0 which calls the same PLT with the same arg shape. Currently T2 passes `track_id_ptr` → 8 bytes of `0xFF` ("identifier not allocated, metadata not available" per AVRCP spec).

### Calling pattern for `…send_get_capabilities_rsp` (PLT 0x35dc, used by T1)

```c
void btmtk_avrcp_send_get_capabilities_rsp(
    void* conn,         // r0 = r5+8
    uint8_t cap_id,     // r1 = 0 (we always pass 0 — likely capability-id type)
    uint8_t count,      // r2 = events count (5 in iter5/6/9, 1 in iter10+)
    void* events_ptr    // r3 = pointer to N-byte events array
);
```

iter10 reduced the advertised events from `01 02 09 0a 0b` (5 events) to just `02` (TRACK_CHANGED only, count=1) because Sonos aborts the entire registration loop on the first NOT_IMPLEMENTED reply — a side-effect we discovered empirically once iter9's "unknow indication" path actually started flowing rejects.

---

## Patch summary (iter17b)

| Patch | File / addr | Description |
|-------|-------------|-------------|
| **mtkbt patches** (in `patch_mtkbt.py`) ||| 
| V1 | mtkbt 0x0eba58 | AVRCP version SDP attribute: 1.0 → 1.3 |
| V2 | mtkbt 0x0eba6d | AVCTP version SDP attribute: 1.0 → 1.2 |
| S1 | mtkbt 0x0f97ec | Replace 0x0311 SupportedFeatures slot with 0x0100 ServiceName pointing at "Advanced Audio" |
| P1 | mtkbt 0x144e8  | `cmp r3, #0x30` → `b.n 0x14528` (route VENDOR_DEPENDENT through msg-519 emit instead of silent-drop) |
| **JNI patches** (in `patch_libextavrcp_jni.py`) ||| 
| R1 | jni 0x6538 (4 B) | `bne.n 0x65bc; movs r5, #9` → `bl.w 0x7308` (redirect to T1) |
| T1 | jni 0x7308 (40 B) | Overwrites unused `testparmnum`. PDU 0x10 → calls `get_capabilities_rsp` via PLT 0x35dc, advertising EVENT_TRACK_CHANGED only |
| T2 stub | jni 0x72d0 (8 B) | Overwrites `classInitNative`. 4-byte `return 0` stub at 0x72d0 + 4-byte `b.w extended_T2` at 0x72d4 |
| extended_T2 + T4 + T5 | jni 0xac54 (760 B) | NEW LOAD #1 extension, dynamically assembled. **extended_T2** (reactive RegisterNotification): reads track_id from y1-track-info, writes [track_id\|\|transId\|\|pad] to y1-trampoline-state, replies INTERIM with track_id=0xFF×8 sentinel. **T4** (reactive GetElementAttributes): reads both files; if state[0..7] != file[0..7], emits track_changed_rsp CHANGED with sentinel + state[8] transId + writes new state, then replies *one* multi-attribute get_element_attributes_rsp frame containing Title (file+8) + Artist (file+264) + Album (file+520) — see [Reverse-engineered semantics: `btmtk_avrcp_send_get_element_attributes_rsp`](#reverse-engineered-semantics-btmtk_avrcp_send_get_element_attributes_rsp). **T5** (proactive on Y1 track change, iter17a): entered via b.w from the patched `notificationTrackChangedNative`; calls the JNI helper at 0x36c0 to obtain the per-conn struct, reads both files, emits CHANGED with sentinel + state[8] transId on track_id divergence, updates state. Returns jboolean(1). Both reactive trampolines fall through to "unknow indication" (0x65bc) for unsupported PDUs. |
| iter17a JNI native stub | jni 0x3bc0 (4 B) | First instruction of `notificationTrackChangedNative` rewritten to `b.w T5`. The Java side (after the MtkBt.odex iter17a NOP) calls this native on every Y1MediaBridge track-change broadcast; T5 emits CHANGED on the AVRCP wire asynchronously to any inbound query. The remaining 196 B of the original native body are unreachable. |
| LOAD#1 filesz | jni 0x64 | `0xac54 → 0xaf4c` (extends executable mapping over the iter17b trampoline blob) |
| LOAD#1 memsz  | jni 0x68 | Same |

Stock md5s and patcher-output md5s are baked into the patcher headers; check them before quoting.

The JNI trampoline blob is built dynamically by `src/patches/_iter15_trampolines.py` using a tiny Thumb-2 assembler in `src/patches/_thumb2asm.py`. Both files are imported by `patch_libextavrcp_jni.py` at run time. Self-tests in `_thumb2asm.py` verify several encodings against known-good bytes from earlier iterations (b.w, blx, addw, movw, ldrb.w, add immediate T3).

**Why both INTERIM and CHANGED carry track_id = 0xFF×8 (iter16):** AVRCP 1.4 §6.7.2 specifies `0xFFFFFFFFFFFFFFFF` as the sentinel meaning "this information is not bound to a particular media element". CTs interpret this as "no stable track identity, refresh on each event" and continue polling `GetElementAttributes` regularly (Sonos in iter14c sent ~50/min). With a real track_id, Sonos enters "stable identity per track, only refresh on CHANGED" mode — and our trampolines are reactive, so Sonos's first INTERIM with a real id can put us in a deadlock where Sonos waits for CHANGED while T4 waits for Sonos to poll. iter15 hit that exact deadlock on hardware (2026-05-06: 14 minutes of zero AVRCP traffic post-INTERIM). iter16 keeps the change-detection bookkeeping (state file's bytes 0..7 = file's last-synced track_id) but pins the wire-level track_id field to the sentinel. CHANGED edges still fire on real track changes — which is what invalidates Sonos's `0xFF×8`-keyed cache and lets the new metadata render.

### Y1MediaBridge ↔ trampoline file contract

Two files, both in `/data/data/com.y1.mediabridge/files/`:

- **y1-track-info** (776 B, written by Y1MediaBridge on every `broadcastTrackAndState()`):
  - bytes 0..7   = `mCurrentAudioId` big-endian
  - bytes 8..263  = Title (UTF-8, max 255 + trailing `\0`)
  - bytes 264..519 = Artist (same)
  - bytes 520..775 = Album (same)
  - mode 0644 (world-readable so the BT process can open it)
- **y1-trampoline-state** (16 B, pre-created by Y1MediaBridge at startup, updated by the trampolines):
  - bytes 0..7  = last track_id we told Sonos about (updated by T4 after emitting CHANGED, and by extended_T2 every RegisterNotification)
  - byte 8     = last RegisterNotification transId (updated by extended_T2)
  - bytes 9..15 = padding
  - mode 0666 (world-rw so the BT process can rewrite it)

Y1MediaBridge's `prepareTrackInfoDir()` is what ensures the BT process can reach both files: `setExecutable(true, false)` on the dir adds world-x for traversal; the y1-track-info file gets `setReadable(true, false)`; the y1-trampoline-state file gets both `setReadable` and `setWritable`.

### Code-cave inventory

| Region | Address | Size | Used by |
|--------|---------|------|---------|
| `testparmnum` | 0x7308 | 48 bytes | T1 (40 bytes used) |
| `classInitNative` | 0x72d0 | 48 bytes | T2 stub (8 bytes used; remaining 40 zero-filled, unreachable) |
| `notificationTrackChangedNative` | 0x3bc0 | 200 bytes | iter17a stub (4 bytes `b.w T5` used; remaining 196 unreachable) |
| LOAD #1 padding | 0xac54..0xbc07 | 4276 bytes | T4 (~316 B) + extended_T2 (~140 B) + T5 (~180 B) + path strings (~108 B) + sentinel (8 B) = 760 B used, ~3516 free |
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
3. **Allocate cave space** in the LOAD #1 padding region (currently ~4128 bytes free past 0xace8).
4. **Wire it into the chain**: change the previous trampoline's "unknown" branch (the `b.w` to `0x65bc` or to the next trampoline) to point at your new entry.
5. **End with**:
   - `b.w 0x712a` for the success path (lands on stack-canary check + epilogue).
   - For the fall-through path: restore `r0 = r5+8` and `lr = halfword[sp+374]` before `b.w 0x65bc`.
6. **Bump LOAD #1 `FileSiz`/`MemSiz`** to cover your new bytes.
7. **Verify with objdump** before committing — disassemble your bytes and confirm every branch resolves to the intended target.

---

## See also

- [`PROXY-BUILD.md`](PROXY-BUILD.md) — concrete iteration plan, status checkboxes, pending work.
- [`PATCHES.md`](PATCHES.md) — per-patch byte-level reference.
- [`INVESTIGATION.md`](INVESTIGATION.md) — chronological investigation history including the gdbserver capture iterations and dead-end paths.
- `src/patches/patch_libextavrcp_jni.py` — the patcher containing R1/T1/T2/T4. Header comments and PATCHES list are the source of truth for byte-level details.
