#!/usr/bin/env python3
"""
patch_libextavrcp_jni_minimal.py — Trampolines T1 (GetCapabilities), T2
(RegisterNotification(EVENT_TRACK_CHANGED)), and T4 stub (placeholder for
GetElementAttributes response, currently just restores r0 and falls through
to the original "unknow indication" path so unhandled PDUs at least
generate msg=520 NOT_IMPLEMENTED).

Pairs with patch_mtkbt_minimal.py's P1 patch which routes inbound
VENDOR_DEPENDENT AV/C commands through msg 519 with size=9.

The T4 stub lives at vaddr 0xac54 — beyond the original LOAD #1 segment
end. The patcher extends LOAD #1's FileSiz/MemSiz from 0xac54 to 0xac5c,
which makes the kernel map those bytes as R+E at runtime. The 4276 bytes
between LOAD #1's old end (0xac54) and LOAD #2's start (0xbc08) are zero
padding for page alignment, so we can grow LOAD #1 freely up to that limit
(this gives us headroom for the eventual full T4 with file-based metadata
plumbing — see docs/PROXY-BUILD.md).

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        fa6191d6ce8170f5ef5c8142202c8ba5

--- Background (per INVESTIGATION.md Trace #12 + docs/PROXY-BUILD.md) ---

The JNI's msg-519 receive function `_Z17saveRegEventSeqIdhh` (body at file
0x5f0c) dispatches inbound CMD_FRAME_IND on frame size:

  size == 3 → PASSTHROUGH path → btmtk_avrcp_send_pass_through_rsp + JNI->Java
  size == 8 → BT-SIG vendor check; on match calls JNIEnv->CallVoidMethodV
              (vtable offset 248) into a Java *Ind callback
  otherwise → "unknow indication" + default reject (msg 520 NOT_IMPLEMENTED)

P1 (in patch_mtkbt_minimal.py) routes VENDOR_DEPENDENT frames into the msg-519
emit path with size=9, so the JNI sees size!=8 and falls into the "unknow
indication" branch. We need to handle GetCapabilities (PDU 0x10) here, since
mtkbt's own dispatcher is compiled-1.0 and never invokes the response builder
for inbound 1.3+ COMMANDs.

--- Patches (R1 + T1 + T2) ---

R1 — redirect: at file 0x6538, replace `bne.n 0x65bc; movs r5, #9`
     (4 bytes: 40 d1 09 25) with `bl.w 0x7308` (4 bytes: 00 f0 e6 fe).
     Branches to the T1 trampoline at 0x7308 for all size!=3 cases.
     Destroys the size==8 fall-through head (`movs r5, #9`) — acceptable
     because mtkbt-as-1.0 never legitimately produces size==8 frames on
     this device.

T1 — GetCapabilities trampoline at 0x7308 (overwrites unused JNI debug
     method `testparmnum`, 48 bytes).
       0x7308: ldrb.w r0, [sp, #382]   ; PDU byte (AV/C body offset 4)
       0x730c: cmp    r0, #0x10        ; GetCapabilities?
       0x730e: bne.n  0x732c           ; no → bridge to T2 stage 2
       0x7310: adr    r3, 0x7324       ; events_data ptr
       0x7312: add.w  r0, r5, #8       ; r0 = conn buffer (r5 from prologue)
       0x7316: movs   r1, #0
       0x7318: movs   r2, #5           ; events count = 5
       0x731a: blx    0x35dc           ; PLT → btmtk_avrcp_send_get_capabilities_rsp
       0x731e: b.w    0x712a           ; mov r9,#1; canary; epilogue
       0x7322: nop
       0x7324: 01 02 09 0a 0b 00 00 00 ; supported events: PLAYBACK_STATUS,
                                       ;   TRACK, NOW_PLAYING_CONTENT,
                                       ;   AVAIL_PLAYERS, ADDR_PLAYER
       0x732c: b.w    0x72d4           ; bridge → T2 stage 2

T2 — classInitNative stub + RegisterNotification(TRACK_CHANGED) trampoline
     at 0x72d0, overwriting the JNI debug method `classInitNative` (48 bytes).
     classInitNative is purely two `__android_log_print` calls + return 0;
     the 4-byte stub at 0x72d0 preserves its return-0 contract (without
     logging), and the remaining 44 bytes hold the T2 logic + track_id_data.

       0x72d0: 00 20 70 47              ; classInitNative stub
                                        ;   movs r0, #0; bx lr
       ; T2 stage 2 (entered from T1's bridge at 0x732c):
       0x72d4: cmp    r0, #0x31         ; PDU still in r0; RegisterNotification?
       0x72d6: bne.n  0x72f4            ; no → unknown_pdu (b.w 0x65bc)
       0x72d8: ldrb.w r0, [sp, #386]    ; event_id (clobber PDU)
       0x72dc: cmp    r0, #0x02         ; EVENT_TRACK_CHANGED?
       0x72de: bne.n  0x72f4            ; no → unknown
       0x72e0: add.w  r0, r5, #8        ; conn buffer
       0x72e4: ldrb.w r1, [sp, #368]    ; transId
       0x72e8: movs   r2, #0x0f         ; INTERIM reasonCode
       0x72ea: adr    r3, 0x72f8        ; track_id_data ptr
       0x72ec: blx    0x3384            ; PLT → btmtk_avrcp_send_reg_notievent_track_changed_rsp
       0x72f0: b.w    0x712a            ; epilogue
       0x72f4: b.w    0x65bc            ; unknown PDU/event → original "unknow indication"
       0x72f8: ff ff ff ff ff ff ff ff  ; track_id = 0xFFFFFFFFFFFFFFFF
                                        ;   ("Identifier not allocated, metadata not available")

     PLT 0x3384 → GOT 0xcf0c → btmtk_avrcp_send_reg_notievent_track_changed_rsp
     (verified via objdump -R + cross-reference with notificationTrackChanged
      Native at 0x3bc0 which calls this PLT with same arg shape:
      r0=conn_buffer, r1=transId, r2=reasonCode, r3=ptr_to_8byte_track_id).

     PLAYBACK_STATUS_CHANGED (event 0x01), NOW_PLAYING_CONTENT_CHANGED
     (0x09), AVAIL_PLAYERS_CHANGED (0x0a), ADDR_PLAYER_CHANGED (0x0b) all
     fall through to the unknown branch (b.w 0x65bc) → mtkbt sends
     NOT_IMPLEMENTED. Sonos retries each event ~4× then gives up; metadata
     handshake (TRACK_CHANGED is what gates GetElementAttributes) still
     proceeds.

     Hardware-verified iter5 (2026-05-05): T1 alone elicited a 30-byte
     msg=522 outbound (consistent with GetCapabilities response) and Sonos
     started sending size:13 RegisterNotification frames at 2-second
     intervals — confirming T1 fires correctly. T2 is the next move.

--- History ---

J1 (cmp.w lr,#8 -> cmp.w lr,#9 at 0x6526) was tried 2026-05-05 (iter4) and
rolled back — it routed VENDOR_DEPENDENT through the size==8 PASSTHROUGH
dispatch which generated fake key=1 PASSTHROUGH events and didn't reach the
intended Java callback (BT-SIG halfword check at sp+382 failed due to size-9
stack misalignment). See INVESTIGATION.md Trace #12.

Mutually exclusive with patch_libextavrcp_jni.py (the v2.0.0 4-patch set);
both target overlapping code regions.

Usage:
    python3 patch_libextavrcp_jni_minimal.py libextavrcp_jni.so
    python3 patch_libextavrcp_jni_minimal.py libextavrcp_jni.so --output /tmp/jni.patched
    python3 patch_libextavrcp_jni_minimal.py libextavrcp_jni.so --verify-only
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "fd2ce74db9389980b55bccf3d8f15660"
OUTPUT_MD5 = "fa6191d6ce8170f5ef5c8142202c8ba5"

# T1 — GetCapabilities trampoline at 0x7308 (overwrites testparmnum, 40 of 48 bytes).
#
# iter10 change: advertise only EVENT_TRACK_CHANGED (0x02), not the full set
# 01/02/09/0a/0b. Sonos's empirical behaviour (iter9) is to RegisterNotification
# events one at a time; on the first NOT_IMPLEMENTED reply it aborts the entire
# registration sequence. Pre-iter9 the unknow-indication path was broken (no
# response emitted), so Sonos timed out on event 0x01 and accidentally tried
# 0x02 anyway. Now that msg=520 actually flows, Sonos respects it and stops —
# meaning it would never reach event 0x02 unless we either ack 0x01 too (= T3,
# more cave use) or just don't advertise 0x01. Latter is simpler.
T1_TRAMPOLINE = bytes([
    0x9D, 0xF8, 0x7E, 0x01,                  # ldrb.w r0, [sp, #382]
    0x10, 0x28,                               # cmp r0, #0x10
    0x0D, 0xD1,                               # bne.n 0x732c (bridge to T2)
    0x04, 0xA3,                               # adr r3, 0x7324
    0x05, 0xF1, 0x08, 0x00,                  # add.w r0, r5, #8
    0x00, 0x21,                               # movs r1, #0
    0x01, 0x22,                               # movs r2, #1   (events count = 1)
    0xFC, 0xF7, 0x60, 0xE9,                  # blx 0x35dc (PLT: get_capabilities_rsp)
    0xFF, 0xF7, 0x04, 0xBF,                  # b.w 0x712a (epilogue)
    0x00, 0xBF,                               # nop
    0x02, 0x00, 0x00, 0x00, 0x00,            # events: TRACK_CHANGED only
    0x00, 0x00, 0x00,                         # padding
    0xFF, 0xF7, 0xD2, 0xBF,                  # b.w 0x72d4 (T2 stage 2 entry)
])
assert len(T1_TRAMPOLINE) == 40

# Stock testparmnum first 40 bytes (unused JNI debug method).
TESTPARMNUM_STOCK = bytes([
    0x10, 0xB5, 0x04, 0x20, 0x07, 0x4C, 0x08, 0x4A,
    0x7C, 0x44, 0x21, 0x46, 0x7A, 0x44, 0xFB, 0xF7,
    0xF4, 0xEF, 0x06, 0x4A, 0x04, 0x20, 0x21, 0x46,
    0x00, 0x23, 0x7A, 0x44, 0xFB, 0xF7, 0xEC, 0xEF,
    0x00, 0x20, 0x10, 0xBD, 0x01, 0x07, 0x00, 0x00,
])
assert len(TESTPARMNUM_STOCK) == 40

# T2 — classInitNative stub (4 bytes) + RegisterNotification(TRACK_CHANGED)
# trampoline (44 bytes) at 0x72d0. Total 48 bytes overwriting classInitNative.
T2_BLOCK = bytes([
    # 0x72d0: classInitNative stub — preserves "return 0" contract
    0x00, 0x20,                               # movs r0, #0
    0x70, 0x47,                               # bx lr
    # 0x72d4: T2 stage 2 entry (called from T1's bridge at 0x732c)
    0x31, 0x28,                               # cmp r0, #0x31  (RegisterNotification?)
    0x0D, 0xD1,                               # bne.n 0x72f4 (unknown)
    0x9D, 0xF8, 0x82, 0x01,                  # ldrb.w r0, [sp, #386]  (event_id)
    0x02, 0x28,                               # cmp r0, #0x02  (TRACK_CHANGED?)
    0x09, 0xD1,                               # bne.n 0x72f4 (unknown)
    0x05, 0xF1, 0x08, 0x00,                  # add.w r0, r5, #8  (conn buffer)
    0x9D, 0xF8, 0x70, 0x11,                  # ldrb.w r1, [sp, #368]  (transId)
    0x0F, 0x22,                               # movs r2, #0x0f  (INTERIM)
    0x03, 0xA3,                               # adr r3, 0x72f8  (track_id_data)
    0xFC, 0xF7, 0x4A, 0xE8,                  # blx 0x3384 (PLT: track_changed_rsp)
    0xFF, 0xF7, 0x1B, 0xBF,                  # b.w 0x712a (epilogue)
    # 0x72f4: unknown — bridge to T4 stub at 0xac54 (in extended LOAD #1)
    0x03, 0xF0, 0xAE, 0xBC,                  # b.w 0xac54 (T4 stub)
    # 0x72f8: track_id_data — 0xFFFFFFFFFFFFFFFF
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
])
assert len(T2_BLOCK) == 48

# T4 — full GetElementAttributes response trampoline at vaddr 0xac54.
# Lives in the LOAD #1 page-alignment padding (4276 zero bytes between LOAD #1
# and LOAD #2). Patcher extends LOAD #1's filesz/memsz so the kernel maps
# these bytes as R+E. T2's "unknown" branch at 0x72f4 jumps here.
#
# iter11 (single Title "Y1 Test") proved the argument layout:
#   r0 = conn (= r5+8)
#   r1 = 0 (string-follows flag)
#   r2 = transId (jbyte at caller_sp+368)
#   r3 = 0 (placeholder — meaning unknown; works as 0)
#   sp[0]  = attribute_id LSB (1=Title, 2=Artist, ...)
#   sp[4]  = 0x6a (UTF-8 charset)
#   sp[8]  = string length (in bytes)
#   sp[12] = pointer to UTF-8 string data
# Hardware-verified: Sonos displayed "Y1 Test" on its Now Playing screen.
#
# iter12 expansion: parse the inbound size:45 GetElementAttributes COMMAND
# (PDU 0x20) to find which attributes Sonos requested, dispatch on each
# attribute_id LSB to the matching hardcoded string, and call the response
# builder once per supported attribute. Supported: Title (0x01), Artist
# (0x02), Album (0x03). Other attributes (TrackNumber, TotalTracks, Genre,
# PlayingTime) are skipped silently.
#
# Inbound frame layout at caller's sp+378 (45 bytes total for size:45):
#   sp+378: op_code (0x00 VENDOR_DEPENDENT)
#   sp+379-381: company_id (0x00 0x19 0x58 BE)
#   sp+382: PDU (0x20)
#   sp+383: packet_type
#   sp+384-385: param_length BE
#   sp+386-393: identifier (8 bytes BE — track_id from RegisterNotification)
#   sp+394: num_attributes
#   sp+395-422: 7 × 4 bytes attribute_ids BE
# After the trampoline's `sub sp, #16`, all caller offsets shift +16:
#   num_attrs at sp+410, attribute_id #i LSB at sp+398+4i+16 = sp+414+4i.
#
# r4-r7 are callee-saved per AAPCS but saveRegEventSeqId's prologue saves them
# and the epilogue at 0x7154 restores via `pop {r4-r9, sl, fp, pc}`. So we
# can trash r4 (num_attrs), r6 (attr_id ptr), r7 (loop counter) without local
# push/pop — they'll be restored when the function returns to its caller.
T4_STUB_VADDR = 0xac54
T4_STUB = bytes([
    # 0xac54: pre-check + fall-through to 0x65bc (20 bytes)
    0x9D, 0xF8, 0x7E, 0x01,    # ldrb.w r0, [sp, #382]   — PDU
    0x20, 0x28,                # cmp r0, #0x20            — GetElementAttributes?
    0x05, 0xD0,                # beq.n do_t4 (0xac68)
    0xBD, 0xF8, 0x76, 0xE1,    # ldrh.w lr, [sp, #374]    — restore lr=SIZE
    0x05, 0xF1, 0x08, 0x00,    # add.w r0, r5, #8         — restore r0=conn
    0xFB, 0xF7, 0xAA, 0xBC,    # b.w 0x65bc               — original "unknow"
    # 0xac68: do_t4
    0x84, 0xB0,                # sub sp, #16              — alloc stack args
    0x9D, 0xF8, 0x9A, 0x41,    # ldrb.w r4, [sp, #410]    — num_attributes
    0x07, 0x2C,                # cmp r4, #7
    0x88, 0xBF,                # it hi
    0x07, 0x24,                # movhi r4, #7             — clamp
    0x0D, 0xF2, 0x9E, 0x16,    # addw r6, sp, #414        — ptr to attr_ids[0] LSB
    0x00, 0x27,                # movs r7, #0              — loop counter
    # 0xac7a: attr_loop
    0xA7, 0x42,                # cmp r7, r4
    0x24, 0xDA,                # bge.n attr_done (0xacc8)
    0x30, 0x78,                # ldrb r0, [r6]            — attr_id LSB
    0x01, 0x28,                # cmp r0, #1
    0x06, 0xD0,                # beq.n use_title (0xac92)
    0x02, 0x28,                # cmp r0, #2
    0x08, 0xD0,                # beq.n use_artist (0xac9a)
    0x03, 0x28,                # cmp r0, #3
    0x0A, 0xD0,                # beq.n use_album (0xaca2)
    # 0xac8c: skip_attr — unsupported attribute
    0x04, 0x36,                # adds r6, #4
    0x01, 0x37,                # adds r7, #1
    0xF3, 0xE7,                # b.n attr_loop (0xac7a)
    # 0xac92: use_title
    0x0F, 0xA0,                # adr r0, title_str (0xacd0)
    0x08, 0x21,                # movs r1, #8
    0x01, 0x22,                # movs r2, #1              — attribute_id LSB
    0x06, 0xE0,                # b.n call_rsp (0xaca8)
    # 0xac9a: use_artist
    0x0F, 0xA0,                # adr r0, artist_str (0xacd8)
    0x09, 0x21,                # movs r1, #9
    0x02, 0x22,                # movs r2, #2
    0x02, 0xE0,                # b.n call_rsp
    # 0xaca2: use_album (falls through to call_rsp)
    0x10, 0xA0,                # adr r0, album_str (0xace4)
    0x08, 0x21,                # movs r1, #8
    0x03, 0x22,                # movs r2, #3
    # 0xaca8: call_rsp
    0x03, 0x90,                # str r0, [sp, #12]        — string ptr
    0x02, 0x91,                # str r1, [sp, #8]         — length
    0x00, 0x92,                # str r2, [sp, #0]         — attribute_id LSB
    0x6A, 0x20,                # movs r0, #0x6a
    0x01, 0x90,                # str r0, [sp, #4]         — UTF-8 charset
    0x05, 0xF1, 0x08, 0x00,    # add.w r0, r5, #8         — conn buffer
    0x00, 0x21,                # movs r1, #0              — has-string flag
    0x9D, 0xF8, 0x80, 0x21,    # ldrb.w r2, [sp, #384]    — transId
    0x00, 0x23,                # movs r3, #0
    0xF8, 0xF7, 0x58, 0xEC,    # blx 0x3570               — PLT: get_element_attributes_rsp
    0x04, 0x36,                # adds r6, #4
    0x01, 0x37,                # adds r7, #1
    0xD8, 0xE7,                # b.n attr_loop
    # 0xacc8: attr_done
    0x04, 0xB0,                # add sp, #16
    0xFC, 0xF7, 0x2E, 0xBA,    # b.w 0x712a               — epilogue
    # 0xacce: pad to 4-byte align for ADR strings
    0x00, 0x00,
    # 0xacd0: title_str = "Y1 Title"  (8 bytes, 4-byte aligned)
    0x59, 0x31, 0x20, 0x54, 0x69, 0x74, 0x6C, 0x65,
    # 0xacd8: artist_str = "Y1 Artist" + 3-byte pad to 4-align (12 bytes total)
    0x59, 0x31, 0x20, 0x41, 0x72, 0x74, 0x69, 0x73, 0x74, 0x00, 0x00, 0x00,
    # 0xace4: album_str = "Y1 Album"   (8 bytes)
    0x59, 0x31, 0x20, 0x41, 0x6C, 0x62, 0x75, 0x6D,
])
assert len(T4_STUB) == 152

# Stock bytes at 0xac54..0xacec — all zero (LOAD #1 page padding).
T4_STUB_STOCK = bytes([0x00] * 152)

# LOAD #1 program-header bookkeeping
LOAD1_PHDR_OFFSET = 0x54
LOAD1_FILESZ_OFFSET = LOAD1_PHDR_OFFSET + 16
LOAD1_MEMSZ_OFFSET  = LOAD1_PHDR_OFFSET + 20
LOAD1_OLD_SIZE = 0xac54
LOAD1_NEW_SIZE = T4_STUB_VADDR + len(T4_STUB)  # 0xac60

# Stock classInitNative (48 bytes) — entry + body + literal pool.
CLASSINITNATIVE_STOCK = bytes([
    0x10, 0xB5, 0x04, 0x20, 0x07, 0x4C, 0x08, 0x4A,
    0x7C, 0x44, 0x21, 0x46, 0x7A, 0x44, 0xFC, 0xF7,
    0x10, 0xE8, 0x06, 0x4A, 0x04, 0x20, 0x21, 0x46,
    0x00, 0x23, 0x7A, 0x44, 0xFC, 0xF7, 0x08, 0xE8,
    0x00, 0x20, 0x10, 0xBD, 0x39, 0x07, 0x00, 0x00,
    0xCA, 0x12, 0x00, 0x00, 0xDD, 0x2C, 0x00, 0x00,
])
assert len(CLASSINITNATIVE_STOCK) == 48

PATCHES = [
    {
        "name": "R1: redirect bne.n 0x65bc → bl.w 0x7308 (T1 trampoline) at 0x6538",
        "offset": 0x6538,
        "before": bytes([0x40, 0xD1, 0x09, 0x25]),  # bne.n 0x65bc; movs r5, #9
        "after":  bytes([0x00, 0xF0, 0xE6, 0xFE]),  # bl.w 0x7308
    },
    {
        "name": "T1: GetCapabilities trampoline (overwrites testparmnum) at 0x7308",
        "offset": 0x7308,
        "before": TESTPARMNUM_STOCK,
        "after":  T1_TRAMPOLINE,
    },
    {
        "name": "T2: classInitNative stub + RegisterNotification(TRACK_CHANGED) at 0x72d0",
        "offset": 0x72d0,
        "before": CLASSINITNATIVE_STOCK,
        "after":  T2_BLOCK,
    },
    {
        "name": f"T4: stub @ 0x{T4_STUB_VADDR:x} ({len(T4_STUB)} bytes appended into LOAD #1 padding)",
        "offset": T4_STUB_VADDR,
        "before": T4_STUB_STOCK,
        "after":  T4_STUB,
    },
    {
        "name": f"LOAD #1 filesz: 0x{LOAD1_OLD_SIZE:x} → 0x{LOAD1_NEW_SIZE:x} (cover T4 stub)",
        "offset": LOAD1_FILESZ_OFFSET,
        "before": LOAD1_OLD_SIZE.to_bytes(4, "little"),
        "after":  LOAD1_NEW_SIZE.to_bytes(4, "little"),
    },
    {
        "name": f"LOAD #1 memsz: 0x{LOAD1_OLD_SIZE:x} → 0x{LOAD1_NEW_SIZE:x} (cover T4 stub)",
        "offset": LOAD1_MEMSZ_OFFSET,
        "before": LOAD1_OLD_SIZE.to_bytes(4, "little"),
        "after":  LOAD1_NEW_SIZE.to_bytes(4, "little"),
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def verify(data: bytes, mode: str) -> tuple[bool, list[dict]]:
    results = []
    for p in PATCHES:
        expected = p[mode]
        actual = bytes(data[p["offset"]: p["offset"] + len(expected)])
        results.append({**p, "actual": actual, "ok": actual == expected})
    return all(r["ok"] for r in results), results


def print_results(label: str, results: list[dict], mode: str) -> None:
    print(f"\n{label}")
    print("-" * 72)
    for r in results:
        n = len(r["before"])
        fmt = lambda b: b.hex(" ") if n <= 8 else b[:8].hex(" ") + " ..."
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected ({mode}): {fmt(r[mode])}")
            print(f"          actual:            {fmt(r['actual'])}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Minimum JNI patch — route size-9 msg-519 frames to BT-SIG VENDOR path"
    )
    parser.add_argument("input", help="Path to stock libextavrcp_jni.so")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/libextavrcp_jni.so.patched)")
    parser.add_argument("--verify-only", action="store_true",
                        help="Check patch sites only, do not write output")
    parser.add_argument("--skip-md5", action="store_true",
                        help="Skip stock MD5 check (use for alternate stock builds)")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    data = bytearray(input_path.read_bytes())
    input_md5 = md5(data)

    if args.skip_md5:
        md5_tag = "(stock check skipped)"
    elif input_md5 == STOCK_MD5:
        md5_tag = "[OK — matches stock]"
    else:
        md5_tag = f"[MISMATCH — expected {STOCK_MD5}]"

    print(f"Input:  {input_path}  ({len(data):,} bytes)")
    print(f"MD5:    {input_md5}  {md5_tag}")

    if not args.skip_md5 and input_md5 != STOCK_MD5:
        print("ERROR: input is not the expected stock build.")
        print("       This patcher targets stock libextavrcp_jni.so only — it is not")
        print("       compatible with the output of patch_libextavrcp_jni.py (the")
        print("       larger --avrcp set). Use --skip-md5 for alternate stock builds.")
        sys.exit(1)

    pre_ok, pre_results = verify(data, "before")
    print_results("Pre-patch verification (stock)", pre_results, "before")

    if not pre_ok:
        post_ok, post_results = verify(data, "after")
        print_results("Already-patched check", post_results, "after")
        if post_ok:
            print("\nBinary is already patched. Nothing to do.")
            sys.exit(0)
        print("\nERROR: patch site matches neither stock nor patched.")
        sys.exit(1)

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    for p in PATCHES:
        data[p["offset"]: p["offset"] + len(p["after"])] = p["after"]

    post_ok, post_results = verify(data, "after")
    print_results("Post-patch verification", post_results, "after")

    if not post_ok:
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "libextavrcp_jni.so.patched"
    output_path.write_bytes(data)
    output_md5 = md5(data)

    output_md5_mismatch = False
    if OUTPUT_MD5 is None:
        out_tag = f"[set OUTPUT_MD5 = \"{output_md5}\"]"
    elif output_md5 == OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {OUTPUT_MD5}]"
        output_md5_mismatch = True

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp_jni.so")
    print(f"  adb shell chmod 644 /system/lib/libextavrcp_jni.so")
    print(f"  adb reboot")
    print(f"  logcat | grep -E 'CMD_FRAME_IND|registerNotificationInd|cardinality|Y1MediaBridge.IBTAvrcpMusic'")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
