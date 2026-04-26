#!/usr/bin/env python3
"""
patch_odex.py — Patch stock MtkBt.odex → MtkBt.odex.patched

Stock binary md5:  11566bc23001e78de64b5db355238175
Output md5:        004d5439e514c42403cf9b470dc0c8cf

ODEX structure:
  ODEX header (0x28 bytes): magic "dey\n036\0", dex_offset=0x28, dex_length=0x98490
  DEX data at 0x28: standard DEX with magic "dex\n035\0"
  DEX adler32 at ODEX file offset 0x30 (= DEX offset 0x28 + DEX field offset 0x08)
    covers DEX bytes [12:dex_length] (i.e. ODEX file bytes [0x34:0x984b8])

Patch applied:
  0x3e0ea  getPreferVersion() return value 0x0a → 0x0e  (AVRCP version 10 → 14)
  0x0030   DEX adler32 recomputed (0x3b165053 → 0xcb985057)

Usage:
    python3 patch_odex.py MtkBt.odex
    python3 patch_odex.py MtkBt.odex --output /tmp/MtkBt.odex.patched
    python3 patch_odex.py MtkBt.odex --verify-only

Deploy:
    adb push MtkBt.odex.patched /system/app/MtkBt.odex
    adb reboot
"""

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

STOCK_MD5   = "11566bc23001e78de64b5db355238175"
OUTPUT_MD5  = "004d5439e514c42403cf9b470dc0c8cf"

DEX_OFFSET       = 0x28        # DEX data starts here in the ODEX file
ADLER_FILE_OFF   = 0x30        # adler32 field: DEX_OFFSET + 0x08
PATCH_OFFSET     = 0x3e0ea     # getPreferVersion return byte
PATCH_BEFORE     = 0x0a        # AVRCP version 10 (stock)
PATCH_AFTER      = 0x0e        # AVRCP version 14


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def compute_adler32(data: bytes) -> int:
    dex_len = struct.unpack_from("<I", data, 12)[0]   # dex_length in ODEX header
    return zlib.adler32(data[DEX_OFFSET + 12 : DEX_OFFSET + dex_len]) & 0xFFFFFFFF


def main():
    parser = argparse.ArgumentParser(description="Patch stock MtkBt.odex → MtkBt.odex.patched")
    parser.add_argument("input", help="Path to stock MtkBt.odex")
    parser.add_argument("--output", "-o", default=None, help="Output path (default: MtkBt.odex.patched)")
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

    # Verify ODEX magic
    if data[:4] != b"dey\n":
        print("ERROR: not an ODEX file (missing 'dey\\n' magic)")
        sys.exit(1)

    # Pre-patch checks
    print(f"\nPre-patch verification")
    print("-" * 72)

    actual_patch = data[PATCH_OFFSET]
    actual_adler = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
    computed_adler = compute_adler32(data)

    patch_ok = actual_patch == PATCH_BEFORE
    adler_ok = actual_adler == computed_adler

    print(f"  [{'OK' if patch_ok else 'FAIL'}] 0x{PATCH_OFFSET:06x}  "
          f"getPreferVersion byte = 0x{actual_patch:02x} (expect 0x{PATCH_BEFORE:02x})")
    print(f"  [{'OK' if adler_ok else 'FAIL'}] 0x{ADLER_FILE_OFF:06x}  "
          f"adler32 = 0x{actual_adler:08x} (computed 0x{computed_adler:08x})")
    print("-" * 72)

    if not patch_ok:
        # Check if already patched
        if actual_patch == PATCH_AFTER:
            print("\nBinary appears already patched. Nothing to do.")
            sys.exit(0)
        print(f"\nERROR: unexpected byte 0x{actual_patch:02x} at patch site.")
        sys.exit(1)

    if not adler_ok:
        print("\nWARNING: stored adler32 doesn't match computed — continuing anyway")

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    # Apply patch
    data[PATCH_OFFSET] = PATCH_AFTER

    # Recompute and write adler32
    new_adler = compute_adler32(data)
    struct.pack_into("<I", data, ADLER_FILE_OFF, new_adler)

    # Post-patch verify
    print(f"\nPost-patch verification")
    print("-" * 72)
    post_patch_ok = data[PATCH_OFFSET] == PATCH_AFTER
    post_adler_stored = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
    post_adler_computed = compute_adler32(data)
    post_adler_ok = post_adler_stored == post_adler_computed
    print(f"  [{'OK' if post_patch_ok else 'FAIL'}] 0x{PATCH_OFFSET:06x}  "
          f"getPreferVersion byte = 0x{data[PATCH_OFFSET]:02x}")
    print(f"  [{'OK' if post_adler_ok else 'FAIL'}] 0x{ADLER_FILE_OFF:06x}  "
          f"adler32 = 0x{post_adler_stored:08x}")
    print("-" * 72)

    if not (post_patch_ok and post_adler_ok):
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    output_path = Path(args.output) if args.output else Path("MtkBt.odex.patched")
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}", end="")
    print(f"  ({'OK' if output_md5 == OUTPUT_MD5 else 'MISMATCH — expected ' + OUTPUT_MD5})")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/app/MtkBt.odex")
    print(f"  adb reboot")


if __name__ == "__main__":
    main()
