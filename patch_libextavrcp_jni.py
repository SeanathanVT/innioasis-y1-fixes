#!/usr/bin/env python3
"""
patch_libextavrcp_jni.py — Patch stock libextavrcp_jni.so → libextavrcp_jni.so.patched

Stock binary md5:  fd2ce74db9389980b55bccf3d8f15660
Output md5:        485a632e799e0cd9ed44455238a8340e

Context (ARM Thumb2 function at 0x375c):
  The function selects an AVRCP version (r4) and sdpfeature (r5) based on
  capability bits, then stores both to globals before calling _activate_1req.

  Version selection logic (stock):
    0x379a: movne r4, #0xa   → version 10 (AVRCP 1.0, fallback)
    0x379e: movs  r4, #0xd   → version 13 (AVRCP 1.3)
    0x37a2: movs  r4, #0xe   → version 14 (AVRCP 1.4)
  ...followed by:
    0x37b2: strb r4, [lr]    → stores version to g_tg_feature global
    0x37b6: strb r5, [ip]    → stores sdpfeature to sdpfeature global

Patches applied:
  1. 0x3764  mov r5, r3  → movs r5, #0x23
             Forces sdpfeature = 0x23 (SupportedFeatures bitmask, correct for 1.3 and 1.4)

  2. 0x37a8  movs r0, #1 → movs r4, #0x0e
             Overwrites version selection result with 0x0e = 14 (AVRCP 1.4)
             Was 0x0d = 13 (AVRCP 1.3) in prior patch

  These override whatever the capability-bit logic selected, ensuring
  activate_config always receives version=14 and sdpfeature=0x23.

Usage:
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --output /tmp/libextavrcp_jni.so.patched
    python3 patch_libextavrcp_jni.py libextavrcp_jni.so --verify-only

Deploy:
    adb push libextavrcp_jni.so.patched /system/lib/libextavrcp_jni.so
    adb reboot
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "fd2ce74db9389980b55bccf3d8f15660"
OUTPUT_MD5 = "485a632e799e0cd9ed44455238a8340e"

PATCHES = [
    {
        "name": "sdpfeature: mov r5,r3 → movs r5,#0x23",
        "offset": 0x3764,
        "before": bytes([0x1d, 0x46]),   # mov r5, r3
        "after":  bytes([0x23, 0x25]),   # movs r5, #0x23
    },
    {
        "name": "g_tg_feature: movs r0,#1 → movs r4,#0x0e  (AVRCP 1.4)",
        "offset": 0x37a8,
        "before": bytes([0x01, 0x20]),   # movs r0, #1
        "after":  bytes([0x0e, 0x24]),   # movs r4, #0x0e
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def verify(data: bytes, mode: str) -> tuple[bool, list[dict]]:
    results = []
    for p in PATCHES:
        expected = p[mode]
        actual = data[p["offset"]: p["offset"] + len(expected)]
        results.append({**p, "actual": actual, "ok": actual == expected})
    return all(r["ok"] for r in results), results


def print_results(label: str, results: list[dict]) -> None:
    print(f"\n{label}")
    print("-" * 72)
    for r in results:
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:04x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected: {r['before'].hex(' ')}")
            print(f"          actual:   {r['actual'].hex(' ')}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Patch stock libextavrcp_jni.so for AVRCP 1.4"
    )
    parser.add_argument("input", help="Path to stock libextavrcp_jni.so")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output path (default: libextavrcp_jni.so.patched)"
    )
    parser.add_argument("--verify-only", action="store_true",
                        help="Check patch sites only, no output")
    parser.add_argument("--skip-md5", action="store_true",
                        help="Skip stock md5 check")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: {input_path} not found", file=sys.stderr)
        sys.exit(1)

    data = bytearray(input_path.read_bytes())
    input_md5 = md5(data)

    print(f"Input:  {input_path}  ({len(data):,} bytes)")
    print(f"MD5:    {input_md5}")

    if not args.skip_md5 and input_md5 != STOCK_MD5:
        print(f"ERROR: MD5 mismatch. Expected stock {STOCK_MD5}")
        print("       Use --skip-md5 to override.")
        sys.exit(1)

    pre_ok, pre_results = verify(data, "before")
    print_results("Pre-patch verification", pre_results)

    if not pre_ok:
        post_ok, post_results = verify(data, "after")
        print_results("Already-patched check", post_results)
        if post_ok:
            print("\nBinary is already patched. Nothing to do.")
            sys.exit(0)
        print("\nERROR: patch sites match neither stock nor patched.")
        sys.exit(1)

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    for p in PATCHES:
        data[p["offset"]: p["offset"] + len(p["after"])] = p["after"]

    post_ok, post_results = verify(data, "after")
    print_results("Post-patch verification", post_results)

    if not post_ok:
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    output_path = (
        Path(args.output) if args.output
        else Path("libextavrcp_jni.so.patched")
    )
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}", end="")
    print(f"  ({'OK' if output_md5 == OUTPUT_MD5 else 'MISMATCH — expected ' + OUTPUT_MD5})")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp_jni.so")
    print(f"  adb reboot")


if __name__ == "__main__":
    main()
