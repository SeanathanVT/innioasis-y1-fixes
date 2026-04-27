#!/usr/bin/env python3
"""
patch_mtkbt.py — Patch stock mtkbt binary → mtkbt.patched

Stock binary md5:  3af1d4ad8f955038186696950430ffda
Output md5:        recompute after first run

Patches applied:
  1.  0xeba1d     Browse channel PSM 0x1b → 0x00 (remove browse advertisement)
  2.  0xeba4b     AVRCP version byte 0x00 → 0x03
  3.  0xeba58     MTK vendor version byte 0x00 → 0x03
  4.  0xeba4e     SupportedFeatures Uint16 value 0x21 → 0x23
  5.  0x0f97b2    Descriptor table flags for AttrID 0x0311: 0x03 → 0x05
  6.  0xeba77     ProfileDescList blob version 0x03 → 0x04 (read-back only)
  7.  0x00012d7c  MOVW r0,#0x1003 → #0x1004 (FUN_00022cec — wrong path, harmless)
  8.  0x00012d84  MOVW r1,#0x1003 → #0x1004 (FUN_00022cec — wrong path, harmless)
  9.  0x0000ead4  ldrb.w r1,[r4,#0xb7e] → movs r1,#4 + nop (log read, cosmetic)
 10.  0x000afd6a  LDRH R6,[R1,#0xa] + MOVS R2,#0 → MOVW R6,#0x104
                  Forces AVRCP version to 0x0104 at the read site in FUN_000afd60

--- Patch 10: version sink patch (direct, no cave) ---

Root cause (confirmed):
  FUN_000afd60 at 0x000afd60 reads the AVRCP ProfileDescList version via
  LDRH R6,[R1,#0xa] at 0x000afd6a. R1 points to a runtime struct in .bss
  populated by an init path that hardcodes 0x0103. The struct has no static
  file representation (ET_DYN, BSS, ASLR).

Solution:
  Replace the LDRH + following MOVS R2,#0 (4 bytes total) with a single
  Thumb2 MOVW R6,#0x104. This forces the version to 0x0104 regardless of
  what the init path wrote. R2=0 from the clobbered MOVS is confirmed safe:
  the STR.W at +4 uses R3, not R2, and R2 is overwritten before any use.

  0x000afd6a: 4e 89 00 22  →  40 f2 04 16
  LDRH R6,[R1,#0xa]           MOVW R6,#0x104
  MOVS R2,#0                  (consumed by MOVW)

Call chain (confirmed):
  FUN_000518ac → FUN_00010d00 → FUN_0000eabc → FUN_000108d0 → FUN_000afd60

Previous approaches tried and failed:
  - Cave in .data (0x000f99f8): RW- segment, not executable → BT crash
  - Cave in .rodata (0x000eb986): live SDP blob data → BT init broken
  - Cave in .rodata (0x000ec243): odd address + string table → broken
  - All MOVW/literal pool patches for 0x0103: wrong init paths
  - r1 intercept patches 9+10 (ldrb→movs): FUN_000208d0 ignores r1
  - Ghidra DAT_001cdebc: outside binary map at base 0, does not exist as
    a patchable file offset

Verification:
  sdptool browse <Y1_BT_ADDR>
    → AV Remote (Target): Version 0x0104
  logcat | grep -E 'tg_feature|ct_feature|cardinality|CONNECT_CNF'
    → tg_feature > 0, ct_feature > 0, cardinality > 0

Usage:
    python3 patch_mtkbt.py mtkbt
    python3 patch_mtkbt.py mtkbt --output /tmp/mtkbt.patched
    python3 patch_mtkbt.py mtkbt --verify-only

Deploy:
    adb push mtkbt.patched /system/bin/mtkbt
    adb shell chmod 755 /system/bin/mtkbt
    adb reboot
    sdptool browse <Y1_BT_ADDR>
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "3af1d4ad8f955038186696950430ffda"
OUTPUT_MD5 = "d3511e1afcb59d11791d64ba5698b796"

PATCHES = [
    {
        "name":   "Browse channel PSM 0x1b → 0x00",
        "offset": 0xeba1d,
        "before": bytes([0x1b]),
        "after":  bytes([0x00]),
    },
    {
        "name":   "AVRCP version byte 0x00 → 0x03",
        "offset": 0xeba4b,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name":   "MTK vendor version byte 0x00 → 0x03",
        "offset": 0xeba58,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name":   "SupportedFeatures value 0x21 → 0x23",
        "offset": 0xeba4e,
        "before": bytes([0x21]),
        "after":  bytes([0x23]),
    },
    {
        "name":   "AttrID 0x0311 descriptor table flags 0x03 → 0x05",
        "offset": 0x0f97b2,
        "before": bytes([0x03]),
        "after":  bytes([0x05]),
    },
    {
        "name":   "ProfileDescList AVRCP version 0x03 → 0x04 (blob, read-back only)",
        "offset": 0xeba77,
        "before": bytes([0x03]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "MOVW r0,#0x1003 → #0x1004 — FUN_00022cec (harmless)",
        "offset": 0x00012d7c,
        "before": bytes([0x41, 0xf2, 0x03, 0x00]),
        "after":  bytes([0x41, 0xf2, 0x04, 0x00]),
    },
    {
        "name":   "MOVW r1,#0x1003 → #0x1004 — FUN_00022cec (harmless)",
        "offset": 0x00012d84,
        "before": bytes([0x41, 0xf2, 0x03, 0x01]),
        "after":  bytes([0x41, 0xf2, 0x04, 0x01]),
    },
    {
        "name":   "ldrb.w r1,[r4,#0xb7e] → movs r1,#4 + nop (log read, cosmetic)",
        "offset": 0x0000ead4,
        "before": bytes([0x94, 0xf8, 0x7e, 0x1b]),
        "after":  bytes([0x04, 0x21, 0x00, 0xbf]),
    },
    {
        "name":   "LDRH R6,[R1,#0xa]+MOVS R2,#0 → MOVW R6,#0x104 (version sink)",
        "offset": 0x000afd6a,
        "before": bytes([0x4e, 0x89, 0x00, 0x22]),
        "after":  bytes([0x40, 0xf2, 0x04, 0x16]),
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
        fmt = lambda b: b.hex(" ") if n <= 8 else b[:8].hex(" ") + " …"
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected ({mode}): {fmt(r[mode])}")
            print(f"          actual:            {fmt(r['actual'])}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(description="Patch stock mtkbt → mtkbt.patched")
    parser.add_argument("input", help="Path to stock mtkbt binary")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-md5", action="store_true")
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
    print_results("Pre-patch verification (stock)", pre_results, "before")

    if not pre_ok:
        post_ok, post_results = verify(data, "after")
        print_results("Already-patched check", post_results, "after")
        if post_ok:
            print("\nBinary is already fully patched. Nothing to do.")
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

    output_path = Path(args.output) if args.output else Path("mtkbt.patched")
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput: {output_path}")
    print(f"MD5:    {output_md5}")
    if OUTPUT_MD5:
        print(f"        ({'OK' if output_md5 == OUTPUT_MD5 else 'MISMATCH — expected ' + OUTPUT_MD5})")
    else:
        print(f"        (set OUTPUT_MD5 = \"{output_md5}\" in script)")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/bin/mtkbt")
    print(f"  adb shell chmod 755 /system/bin/mtkbt")
    print(f"  adb reboot")
    print(f"  sdptool browse <Y1_BT_ADDR>   # expect: AV Remote Version: 0x0104")
    print(f"  logcat | grep -E 'tg_feature|ct_feature|cardinality|CONNECT_CNF'")


if __name__ == "__main__":
    main()
