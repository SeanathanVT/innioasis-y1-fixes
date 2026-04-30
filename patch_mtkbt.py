#!/usr/bin/env python3
"""
patch_mtkbt.py — Patch stock mtkbt binary → mtkbt.patched

Stock md5:  3af1d4ad8f955038186696950430ffda
Output md5: (regenerated on each build — see script output)

--- Descriptor table structure (key finding) ---

The mtkbt descriptor table contains TWO AttrID=0x0009 (ProfileDescList) entries
for the AVRCP TG SDP record. The SDP stack serves the LAST one:

  Record [13]  AttrID=0x0009  ptr=0x0eba6e  -> 35 08 35 06 19 11 0e 09 [01 03]
               Version field at 0x0eba77: 0x03 (AVRCP 1.3)
               NOT served in SDP responses — only the later record wins.

  Record [18]  AttrID=0x0009  ptr=0x0eba4f  -> 35 08 35 06 19 11 0e 09 [01 00]
               Version field at 0x0eba58: 0x00 (AVRCP 1.0 in stock)
               THIS IS WHAT sdptool SEES. Patching here changes the advertised
               AVRCP version.

Prior investigation incorrectly concluded the .rodata blob was "read-back only."
That conclusion was reached by testing patches to record [13] (0xeba76-77) while
record [18] was already patched by old #3 (0xeba58: 00->03). Changes to [13]
appeared to have no effect because [18] was already controlling the SDP output.
The regression from 0x0103 to 0x0100 after removing old #3 confirms [18] is live.

--- Eliminated patches (do not restore) ---

  ELIMINATED — old #1 (0xeba1d): PSM byte — unrelated to version.
  ELIMINATED — old #2 (0xeba4b): version byte in a non-served blob region.
  ELIMINATED — old #4 (0xeba4e): SupportedFeatures — AttrID 0x0311 not registered.
  ELIMINATED — old #5 (0x0f97b2): descriptor table flags = element size, not control.
  ELIMINATED — old #7, #8 (0x00012d7c, 0x00012d84): FUN_00022cec, not on SDP path.
  ELIMINATED — old #9 (0x0000ead4): FUN_000108d0 ignores r1 parameter.
  ELIMINATED — old #10 (0x000afd6a): version sink downstream of SDP construction.

--- Patches in this script ---

  The descriptor table contains THREE AttrID=0x0009 (ProfileDescList) entries
  for the AVRCP TG SDP record. The SDP stack serves the last-wins entry.
  Which of the three is actually served is determined at runtime; all three are
  patched to 1.4 to guarantee the correct value regardless:

    Record [23]  ptr=0x0eba42  minor version at 0x0eba4b  stock: 0x00  -> 0x04
    Record [18]  ptr=0x0eba4f  minor version at 0x0eba58  stock: 0x00  -> 0x04
    Record [13]  ptr=0x0eba6e  minor version at 0x0eba77  stock: 0x03  -> 0x04

  Old patch #2 (0xeba4b: 00->03) targeted record [23] and old patch #3
  (0xeba58: 00->03) targeted record [18]. Both were previously mislabelled
  as "eliminated." At least one is effective; both are now set to 1.4.

  A1 — Runtime SDP MOVW at 0x38BFC: runtime struct version
    The SDP init function at 0x38AB0-0x38C74 also writes the version to a
    runtime SDP struct via STRH.W r7,[r3,#72] at 0x38C02. MOVW r7,#0x0301
    (bytes: 40 f2 01 37) is patched to MOVW r7,#0x0401 (40 f2 01 47).
    Belt-and-suspenders alongside the blob patches.

Usage:
    python3 patch_mtkbt.py mtkbt
    python3 patch_mtkbt.py mtkbt --output /tmp/mtkbt.patched
    python3 patch_mtkbt.py mtkbt --verify-only

Deploy:
    adb push output/mtkbt.patched /system/bin/mtkbt
    adb shell chmod 755 /system/bin/mtkbt
    adb reboot
    sdptool browse <Y1_BT_ADDR>   # expect: AV Remote (0x110e) Version: 0x0104
    logcat | grep -E 'tg_feature|ct_feature|cardinality|CONNECT_CNF'
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5  = "3af1d4ad8f955038186696950430ffda"
OUTPUT_MD5 = "9e8d155987f64596091335d2d4225898"

PATCHES = [
    # Three ProfileDescList (AttrID=0x0009) entries exist for AVRCP TG.
    # All are patched to 1.4 — whichever the SDP stack serves last-wins.
    {
        "name":   "0x0eba4b: record [23] minor version (ptr=0x0eba42)  [SDP — last entry]",
        "offset": 0x0eba4b,
        "before": bytes([0x00]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "0x0eba58: record [18] minor version (ptr=0x0eba4f)  [SDP — mid entry]",
        "offset": 0x0eba58,
        "before": bytes([0x00]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "0x0eba77: record [13] minor version (ptr=0x0eba6e)  [SDP — first entry]",
        "offset": 0x0eba77,
        "before": bytes([0x03]),
        "after":  bytes([0x04]),
    },
    {
        "name":   "0x38BFC: MOVW r7,#0x0301 -> #0x0401  [A1 — runtime SDP struct]",
        "offset": 0x038BFC,
        "before": bytes([0x40, 0xf2, 0x01, 0x37]),
        "after":  bytes([0x40, 0xf2, 0x01, 0x47]),
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
    parser = argparse.ArgumentParser(description="Patch stock mtkbt -> mtkbt.patched")
    parser.add_argument("input", help="Path to stock mtkbt binary")
    parser.add_argument("--output", "-o", default=None)
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--skip-md5", action="store_true",
                        help="Skip stock MD5 check (use for alternate stock builds)")
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
        output_path = output_dir / "mtkbt.patched"
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
