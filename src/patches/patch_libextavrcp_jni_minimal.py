#!/usr/bin/env python3
"""
patch_libextavrcp_jni_minimal.py — Trampolines T1 (GetCapabilities) and T2
(RegisterNotification(EVENT_TRACK_CHANGED)) for the --avrcp-min research
probe. Redirects size!=8 dispatch in `_Z17saveRegEventSeqIdhh` to code-caves
that call response-builder functions directly via PLT. Pairs with
patch_mtkbt_minimal.py's P1 patch which routes inbound VENDOR_DEPENDENT
AV/C commands through msg 519 with size=9.

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        5fec125a259d9fc210831d20dc2ecf48

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
OUTPUT_MD5 = "5fec125a259d9fc210831d20dc2ecf48"

# T1 — GetCapabilities trampoline at 0x7308 (overwrites testparmnum, 40 of 48 bytes).
T1_TRAMPOLINE = bytes([
    0x9D, 0xF8, 0x7E, 0x01,                  # ldrb.w r0, [sp, #382]
    0x10, 0x28,                               # cmp r0, #0x10
    0x0D, 0xD1,                               # bne.n 0x732c (bridge to T2)
    0x04, 0xA3,                               # adr r3, 0x7324
    0x05, 0xF1, 0x08, 0x00,                  # add.w r0, r5, #8
    0x00, 0x21,                               # movs r1, #0
    0x05, 0x22,                               # movs r2, #5
    0xFC, 0xF7, 0x60, 0xE9,                  # blx 0x35dc (PLT: get_capabilities_rsp)
    0xFF, 0xF7, 0x04, 0xBF,                  # b.w 0x712a (epilogue)
    0x00, 0xBF,                               # nop
    0x01, 0x02, 0x09, 0x0A, 0x0B,            # events: PLAYBACK,TRACK,NPL,AVAIL,ADDR
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
    # 0x72f4: unknown — fall through to original "unknow indication"
    0xFF, 0xF7, 0x62, 0xB9,                  # b.w 0x65bc
    # 0x72f8: track_id_data — 0xFFFFFFFFFFFFFFFF
    0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF, 0xFF,
])
assert len(T2_BLOCK) == 48

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
