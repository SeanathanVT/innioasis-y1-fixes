#!/usr/bin/env python3
"""
patch_libextavrcp.py — Patch stock libextavrcp.so → libextavrcp.so.patched

Stock binary md5:  6442b137d3074e5ac9a654de83a4941a
Output md5:        943d406bfbb7669fd62cf1c450d34c42

Patches applied:
  1. 0x002e3b  AVRCP version 0x0103 (1.3) → 0x0104 (1.4)

Patch 1 changes the AVRCP version constant advertised in the SDP record from
1.3 to 1.4, enabling bidirectional metadata flow with AVRCP 1.4 car head units.

Verify with: sdptool browse <Y1_BT_ADDR>
  # Should show: AV Remote (0x110e) Version: 0x0104

Usage:
    python3 patch_libextavrcp.py libextavrcp.so
    python3 patch_libextavrcp.py libextavrcp.so --output /tmp/libextavrcp.so.patched
    python3 patch_libextavrcp.py libextavrcp.so --verify-only

Deploy:
    adb push libextavrcp.so.patched /system/lib/libextavrcp.so
    adb reboot
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "6442b137d3074e5ac9a654de83a4941a"
OUTPUT_MD5 = "943d406bfbb7669fd62cf1c450d34c42"

PATCHES = [
    {
        "name": "AVRCP version 0x0103 (1.3) → 0x0104 (1.4)",
        "offset": 0x002e3b,
        "before": bytes([0x03, 0x01]),
        "after":  bytes([0x04, 0x01]),
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
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected: {r['before'].hex(' ')}")
            print(f"          actual:   {r['actual'].hex(' ')}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="Patch stock libextavrcp.so for AVRCP 1.4"
    )
    parser.add_argument("input", help="Path to stock libextavrcp.so")
    parser.add_argument(
        "--output", "-o", default=None,
        help="Output path (default: libextavrcp.so.patched)"
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

    if not args.skip_md5 and STOCK_MD5 and input_md5 != STOCK_MD5:
        print(f"ERROR: MD5 mismatch. Expected stock {STOCK_MD5}")
        print("       Use --skip-md5 to bypass.")
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

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "libextavrcp.so.patched"
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}", end="")
    print(f"  ({'OK' if output_md5 == OUTPUT_MD5 else 'MISMATCH — expected ' + OUTPUT_MD5})")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp.so")
    print(f"  adb reboot")
    print(f"  sdptool browse <Y1_BT_ADDR>  # verify: AV Remote Version: 0x0104")


if __name__ == "__main__":
    main()
