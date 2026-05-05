#!/usr/bin/env python3
"""
patch_libextavrcp_jni_minimal.py — Minimum JNI-side patch for the --avrcp-min
research probe. One byte rewrite that routes size-9 inbound msg-519 frames
(produced by patch_mtkbt_minimal.py's P1 patch for VENDOR_DEPENDENT AV/C
commands) into the JNI's existing size-8 BT-SIG VENDOR path, instead of
the "unknow indication" default-reject path.

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        eb4814395b9b07a78c8d03118cf58124

--- Background (per INVESTIGATION.md Trace #12) ---

The JNI's msg-519 receive function `_Z17saveRegEventSeqIdhh` (file 0x5ee4)
dispatches inbound CMD_FRAME_IND on frame size:

  size == 3 → PASSTHROUGH path → btmtk_avrcp_send_pass_through_rsp
  size == 8 → branch with BT-SIG vendor check (cmp halfword, #0x5819 at 0x656a)
              → on match, jumps to 0x65a4 → calls JNIEnv->CallVoidMethodV
              (vtable offset 248) into a Java *Ind callback
  otherwise → "unknow indication" + default reject (msg 520 NOT_IMPLEMENTED)

The mtkbt P1 patch routes VENDOR_DEPENDENT frames into the msg-519 emit path,
producing CMD_FRAME_IND with size=9 (one byte off from the size-8 path's
expected layout). Without this JNI patch, size=9 frames take the "unknow
indication" branch — which is what blocks Y1MediaBridge from receiving the
inbound command.

--- The patch (J1) ---

  At file 0x6524 the JNI does `cmp.w lr, #8` (Thumb-2 32-bit insn). The
  imm8 field is at file offset 0x6526. Change it from 0x08 to 0x09 so
  size-9 frames take the BT-SIG path:

    cmp.w lr, #8  =  bytes  be f1 08 0f
    cmp.w lr, #9  =  bytes  be f1 09 0f

  Single byte: file 0x6526, 0x08 → 0x09.

--- Risks ---

  - The size-8 path reads bytes at sp+381, sp+382, sp+385 expecting a specific
    stack layout. The size-9 frame's stack layout may differ by one byte; the
    BT-SIG check at sp+382 may fail unexpectedly.
  - The path also calls `btmtk_avrcp_send_pass_through_rsp` at file 0x6550
    BEFORE the BT-SIG check fires. For our VENDOR_DEPENDENT frame this sends
    a malformed PASSTHROUGH response to the peer. Risk: Sonos sees the bogus
    response and disengages, even if BT-SIG path succeeds afterwards.
  - The methodID looked up at file 0xd008 (loaded at 0x65b0) is one specific
    Java callback (likely registerNotificationInd or playerAppCapabilitiesInd
    given the *Ind name list). For a GetCapabilities inbound, that may be the
    wrong handler — Java may throw a NoSuchMethodError or just no-op.

  If any of these manifest, the next patch is to NOP the pass_through_rsp
  call at 0x6550 (4 bytes → two `nop`s). If even that fails, we fall back to
  the binary trampoline plan from INVESTIGATION.md "Path forward".

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
OUTPUT_MD5 = "eb4814395b9b07a78c8d03118cf58124"

PATCHES = [
    {
        "name":   "[J1] cmp.w lr, #8 -> cmp.w lr, #9  size-dispatch in saveRegEventSeqIdhh",
        "offset": 0x6526,
        "before": bytes([0x08]),
        "after":  bytes([0x09]),
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
