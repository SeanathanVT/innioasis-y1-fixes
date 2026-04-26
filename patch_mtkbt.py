#!/usr/bin/env python3
"""
patch_mtkbt.py — Patch stock mtkbt binary → mtkbt.patched

Stock binary md5: 3af1d4ad8f955038186696950430ffda

Patches applied:
  1. 0xeba1d  Browse channel PSM 0x1b → 0x00 (remove browse advertisement)
  2. 0xeba4b  AVRCP version byte 0x00 → 0x03
  3. 0xeba58  MTK vendor version byte 0x00 → 0x03
  4. 0xeba4e  SupportedFeatures Uint16 value 0x21 → 0x23
  5. 0x0f97b2 Descriptor table flags for AttrID 0x0311: 0x03 → 0x05
  6. 0xeba77  ProfileDescList AVRCP version 0x03 → 0x04 (1.3 → 1.4)

Patch 6 changes the registered SDP record from:
  "AV Remote" (0x110e)  Version: 0x0103
to:
  "AV Remote" (0x110e)  Version: 0x0104

Verify with: sdptool browse <Y1_BT_ADDR>

Usage:
    python3 patch_mtkbt.py mtkbt
    python3 patch_mtkbt.py mtkbt --output /tmp/mtkbt.patched
    python3 patch_mtkbt.py mtkbt --verify-only

Deploy:
    adb push mtkbt.patched /system/bin/mtkbt
    adb shell chmod 755 /system/bin/mtkbt
    adb reboot
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5 = "3af1d4ad8f955038186696950430ffda"

PATCHES = [
    {
        "name": "Browse channel PSM 0x1b → 0x00",
        "offset": 0xeba1d,
        "before": bytes([0x1b]),
        "after":  bytes([0x00]),
    },
    {
        "name": "AVRCP version byte 0x00 → 0x03",
        "offset": 0xeba4b,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name": "MTK vendor version byte 0x00 → 0x03",
        "offset": 0xeba58,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name": "SupportedFeatures value 0x21 → 0x23",
        "offset": 0xeba4e,
        "before": bytes([0x21]),
        "after":  bytes([0x23]),
    },
    {
        "name": "AttrID 0x0311 descriptor table flags 0x03 → 0x05",
        "offset": 0x0f97b2,
        "before": bytes([0x03]),
        "after":  bytes([0x05]),
    },
    {
        "name": "ProfileDescList AVRCP version 0x03 → 0x04 (1.3 → 1.4)",
        "offset": 0xeba77,
        "before": bytes([0x03]),
        "after":  bytes([0x04]),
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
    parser = argparse.ArgumentParser(description="Patch stock mtkbt → mtkbt.patched")
    parser.add_argument("input", help="Path to stock mtkbt binary")
    parser.add_argument("--output", "-o", default=None, help="Output path (default: mtkbt.patched)")
    parser.add_argument("--verify-only", action="store_true", help="Check patch sites only, no output")
    parser.add_argument("--skip-md5", action="store_true", help="Skip stock md5 check")
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

    output_path = Path(args.output) if args.output else Path("mtkbt.patched")
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/bin/mtkbt")
    print(f"  adb shell chmod 755 /system/bin/mtkbt")
    print(f"  adb reboot")
    print(f"  sdptool browse <Y1_BT_ADDR>  # verify: AV Remote Version: 0x0104")


if __name__ == "__main__":
    main()
