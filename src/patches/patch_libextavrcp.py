#!/usr/bin/env python3
"""
patch_libextavrcp.py — Patch stock libextavrcp.so -> libextavrcp.so.patched

Stock binary md5:  6442b137d3074e5ac9a654de83a4941a  (size: 17,552 bytes)
Output md5:        943d406bfbb7669fd62cf1c450d34c42

Binary: ARM32 ELF shared object, ET_DYN, base 0x00000000.
        File offsets equal virtual addresses for the first PT_LOAD segment.

--- Patch: AVRCP version constant in .text ---

  Offset 0x002E3B is within .text (range 0x17E0-0x2FC4). The two bytes at
  0x002E3B are a little-endian uint16 embedded in a Thumb-2 instruction sequence:

    Surrounding bytes: 46 f0 01 03 [03 01] 90
                                    ^^^^^
                                 0x0103 in LE = AVRCP 1.3

  Patch changes the LE encoding from 03 01 (= 0x0103) to 04 01 (= 0x0104).

  Confirmed on-binary: stock bytes [0x002e3b:0x002e3d] = 03 01.

  Note (Cline report conflict): an external audit claimed 0x0103 appears as
  inline data at mtkbt offset 0x13FA6. Binary analysis confirms this is a
  false positive — the bytes 09 01 at 0x13FA6 are the second halfword of a
  Thumb32 instruction (hw1=0xEB00, Thumb32 prefix bits[15:13]=111). No
  additional 0x0103 literal exists in mtkbt .text as standalone data.

  Note (file size conflict): an external audit claimed libextavrcp.so is
  17,504 bytes. Confirmed size on stock binary: 17,552 bytes. The external
  audit was run against a different file.

Usage:
    python3 patch_libextavrcp.py libextavrcp.so
    python3 patch_libextavrcp.py libextavrcp.so --output /tmp/libextavrcp.so.patched
    python3 patch_libextavrcp.py libextavrcp.so --verify-only

Deploy:
    adb push output/libextavrcp.so.patched /system/lib/libextavrcp.so
    adb reboot
    sdptool browse <Y1_BT_ADDR>  # verify: AV Remote Version: 0x0104
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "6442b137d3074e5ac9a654de83a4941a"
OUTPUT_MD5 = "943d406bfbb7669fd62cf1c450d34c42"

PATCHES = [
    {
        "name":   "[C4] AVRCP version constant 0x0103 -> 0x0104  (.text)",
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
        description="Patch stock libextavrcp.so for AVRCP 1.4"
    )
    parser.add_argument("input", help="Path to stock libextavrcp.so")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/libextavrcp.so.patched)")
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
        print("       Use --skip-md5 for alternate stock builds.")
        sys.exit(1)

    pre_ok, pre_results = verify(data, "before")
    print_results("Pre-patch verification (stock)", pre_results, "before")

    if not pre_ok:
        post_ok, post_results = verify(data, "after")
        print_results("Already-patched check", post_results, "after")
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
    print_results("Post-patch verification", post_results, "after")

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

    if OUTPUT_MD5 is None:
        out_tag = f"[set OUTPUT_MD5 = \"{output_md5}\"]"
    elif output_md5 == OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {OUTPUT_MD5}]"

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp.so")
    print(f"  adb shell chmod 644 /system/lib/libextavrcp.so")
    print(f"  adb reboot")
    print(f"  sdptool browse <Y1_BT_ADDR>   # expect: AV Remote Version: 0x0104")


if __name__ == "__main__":
    main()
