#!/usr/bin/env python3
"""
patch_libextavrcp_jni_minimal.py — Trampoline T1 for the --avrcp-min research
probe. Redirects size!=8 dispatch in `_Z17saveRegEventSeqIdhh` to a code-cave
that calls `btmtk_avrcp_send_get_capabilities_rsp` directly via PLT, then
exits the function. Bypasses both the JNI->Java callback (which the size==8
path uses) and the "unknow indication" default-reject (the original size!=8
target). Pairs with patch_mtkbt_minimal.py's P1 patch which routes inbound
VENDOR_DEPENDENT AV/C commands through msg 519 with size=9.

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        5949a7f28bf700e4d3934fa7fab00c9f

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

--- Patch (T1) ---

R1 — redirect: at file 0x6538, replace `bne.n 0x65bc; movs r5, #9`
     (4 bytes: 40 d1 09 25) with `bl.w 0x7308` (4 bytes: 00 f0 e6 fe).
     This branches to the trampoline at 0x7308 for ALL size!=3 cases.
     Destroys the size==8 fall-through head (`movs r5, #9`) — acceptable
     because mtkbt-as-1.0 never legitimately produces size==8 frames on
     this device, and the original size!=8 path led to "unknow indication"
     anyway (which the trampoline still routes to via the fall_through arm).

T1 — trampoline: at file 0x7308, overwrite the unused JNI debug method
     `_Z33BluetoothAvrcpService_testparmnumP7_JNIEnvP8_jobjectaaaaaaaaaaaa`
     with 40 bytes of Thumb code:

       0x7308: ldrb.w r0, [sp, #382]   ; PDU byte (AV/C body offset 4 → sp+382)
       0x730c: cmp    r0, #0x10        ; GetCapabilities PDU?
       0x730e: bne.n  0x732c           ; no → fall_through
       0x7310: adr    r3, 0x7324       ; events_data ptr
       0x7312: add.w  r0, r5, #8       ; r0 = conn buffer (r5 was set in prologue)
       0x7316: movs   r1, #0           ; ?
       0x7318: movs   r2, #5           ; events count = 5
       0x731a: blx    0x35dc           ; btmtk_avrcp_send_get_capabilities_rsp
       0x731e: b.w    0x712a           ; mov r9,#1; canary check; epilogue
       0x7322: nop
       0x7324: 01 02 09 0a 0b 00 00 00 ; events: PLAYBACK_STATUS_CHANGED,
                                       ;         TRACK_CHANGED,
                                       ;         NOW_PLAYING_CONTENT_CHANGED,
                                       ;         AVAILABLE_PLAYERS_CHANGED,
                                       ;         ADDRESSED_PLAYER_CHANGED
       0x732c: b.w    0x65bc           ; fall_through (original "unknow" target)

     PLT 0x35dc → GOT 0xcfd4 → btmtk_avrcp_send_get_capabilities_rsp
     (verified via objdump -R).

testparmnum is presumed unused (debug method that takes 12 jbyte args, logs
each, returns 0). MtkBt.apk's smali should be grep-checked for any caller
before shipping (none expected).

If T1 succeeds (Sonos receives a real GetCapabilities response and proceeds
to RegisterNotification/GetElementAttributes), the next patches T2/T3/T4
follow the same trampoline pattern in additional code-cave space (see
docs/PROXY-BUILD.md).

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
OUTPUT_MD5 = "5949a7f28bf700e4d3934fa7fab00c9f"

# T1 trampoline at 0x7308 (overwrites testparmnum, 40 bytes of 44 available).
TRAMPOLINE_BYTES = bytes([
    0x9D, 0xF8, 0x7E, 0x01,                  # ldrb.w r0, [sp, #382]
    0x10, 0x28,                               # cmp r0, #0x10
    0x0D, 0xD1,                               # bne.n 0x732c
    0x04, 0xA3,                               # adr r3, 0x7324
    0x05, 0xF1, 0x08, 0x00,                  # add.w r0, r5, #8
    0x00, 0x21,                               # movs r1, #0
    0x05, 0x22,                               # movs r2, #5
    0xFC, 0xF7, 0x60, 0xE9,                  # blx 0x35dc (PLT: get_capabilities_rsp)
    0xFF, 0xF7, 0x04, 0xBF,                  # b.w 0x712a (epilogue)
    0x00, 0xBF,                               # nop
    0x01, 0x02, 0x09, 0x0A, 0x0B,            # events: PLAYBACK,TRACK,NPL,AVAIL,ADDR
    0x00, 0x00, 0x00,                         # padding
    0xFF, 0xF7, 0x46, 0xB9,                  # b.w 0x65bc (fall_through)
])
assert len(TRAMPOLINE_BYTES) == 40

# Stock testparmnum first 40 bytes (unused JNI debug method).
TESTPARMNUM_STOCK = bytes([
    0x10, 0xB5, 0x04, 0x20, 0x07, 0x4C, 0x08, 0x4A,
    0x7C, 0x44, 0x21, 0x46, 0x7A, 0x44, 0xFB, 0xF7,
    0xF4, 0xEF, 0x06, 0x4A, 0x04, 0x20, 0x21, 0x46,
    0x00, 0x23, 0x7A, 0x44, 0xFB, 0xF7, 0xEC, 0xEF,
    0x00, 0x20, 0x10, 0xBD, 0x01, 0x07, 0x00, 0x00,
])
assert len(TESTPARMNUM_STOCK) == 40

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
        "after":  TRAMPOLINE_BYTES,
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
