#!/usr/bin/env python3
"""
patch_mtkbt.py — Patch stock mtkbt binary

Stock binary md5:  3af1d4ad8f955038186696950430ffda

Supported targets:
  patched4a  — SDP blob patches only (no descriptor table ptr redirects)
               Safe: BT init works. tg_feature:0 (expected — bisect confirmed ptr redirects crash init)
               Patches 1-4 from patched4.

  patched5   — SDP blob patches + in-place value fixes at descriptor table ptr targets
               Goal: tg_feature:0x0023 without ptr redirects or region crossings
               Patches 1-6 (no ptr redirects; two new value patches in-place)

Patch map:
  1. 0xeba1d  Browse channel PSM 0x001b → 0x00 (removes browse advertisement)
  2. 0xeba4b  ProfileDescriptorList AVRCP version 1.0 → 1.3
  3. 0xeba58  MTK vendor AttrID 0x0021 AVRCP version 1.0 → 1.3
  4. 0xeba97  Replace language descriptor with AttrID 0x0311 = 0x0023 (SupportedFeatures blob write)
  [patched4a stops here]
  5. 0xeba4e  Descriptor table entry1 ptr target: Uint16 value 0x0021 → 0x0023
  6. 0xeba5b  Descriptor table entry2 ptr target: Uint16 value 0x0001 → 0x0023
  [patched5 uses patches 1-4 + 5-6]

Descriptor table (3 entries for AttrID 0x0311):
  0x0f97b0  ptr=0x0eba4c → 09 00 21  (Uint16, value patched by patch 5)
  0x0f97ec  ptr=0x0eba59 → 09 00 01  (Uint16, value patched by patch 6)
  0x0f9828  ptr=0x0eba0f → 09 00 0f  (AttrID 0x000f context — left untouched)

Usage:
    python3 patch_mtkbt.py mtkbt --target patched5
    python3 patch_mtkbt.py mtkbt --target patched4a
    python3 patch_mtkbt.py mtkbt --target patched5 --output mtkbt.patched5
    python3 patch_mtkbt.py mtkbt --target patched5 --verify-only
"""

import argparse
import hashlib
import sys
from pathlib import Path

STOCK_MD5 = "3af1d4ad8f955038186696950430ffda"

PATCHES_BASE = [
    {
        "name": "Browse channel PSM 0x001b → 0x00",
        "offset": 0xeba1d,
        "before": bytes([0x1b]),
        "after":  bytes([0x00]),
    },
    {
        "name": "ProfileDescriptorList AVRCP version 1.0 → 1.3",
        "offset": 0xeba4b,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name": "MTK vendor AttrID 0x0021 AVRCP version 1.0 → 1.3",
        "offset": 0xeba58,
        "before": bytes([0x00]),
        "after":  bytes([0x03]),
    },
    {
        "name": "0xeba97: replace language descriptor with SupportedFeatures blob",
        "offset": 0xeba97,
        "before": bytes([0x35, 0x09, 0x09, 0x65, 0x6e, 0x09, 0x00, 0x6a, 0x09, 0x01, 0x00]),
        "after":  bytes([0x09, 0x03, 0x11, 0x09, 0x00, 0x23, 0x00, 0x00, 0x00, 0x00, 0x00]),
    },
]

PATCHES_P5_EXTRA = [
    {
        "name": "0xeba4e: desc table entry1 ptr target value 0x0021 → 0x0023",
        "offset": 0xeba4e,
        "before": bytes([0x21]),
        "after":  bytes([0x23]),
    },
    {
        "name": "0xeba5b: desc table entry2 ptr target value 0x0001 → 0x0023",
        "offset": 0xeba5b,
        "before": bytes([0x01]),
        "after":  bytes([0x23]),
    },
]

TARGETS = {
    "patched4a": {
        "patches": PATCHES_BASE,
        "md5": None,  # fill in after first confirmed flash
        "description": "blob-only, no ptr redirects (bisect confirmed safe)",
    },
    "patched5": {
        "patches": PATCHES_BASE + PATCHES_P5_EXTRA,
        "md5": "8578c3b374a082f30e6935308c208efb",
        "description": "blob + in-place desc table value patches (no ptr redirects)",
    },
}


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def verify_patches(data: bytes, patches: list, mode: str = "before") -> list[dict]:
    results = []
    for p in patches:
        offset = p["offset"]
        expected = p[mode]
        actual = data[offset: offset + len(expected)]
        results.append({
            "name": p["name"],
            "offset": offset,
            "expected": expected,
            "actual": actual,
            "ok": actual == expected,
        })
    return results


def print_results(results: list[dict], label: str) -> bool:
    print(f"\n{label}")
    print("-" * 72)
    all_ok = True
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        if not r["ok"]:
            all_ok = False
        print(f"  [{status}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected: {r['expected'].hex(' ')}")
            print(f"          actual:   {r['actual'].hex(' ')}")
    print("-" * 72)
    return all_ok


def main():
    parser = argparse.ArgumentParser(description="Patch stock mtkbt")
    parser.add_argument("input", help="Path to stock mtkbt binary")
    parser.add_argument("--target", "-t", choices=list(TARGETS.keys()), default="patched5",
                        help="Patch target (default: patched5)")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: <input>.<target>)")
    parser.add_argument("--verify-only", action="store_true",
                        help="Verify patch sites without writing output")
    parser.add_argument("--skip-md5", action="store_true",
                        help="Skip stock md5 check")
    args = parser.parse_args()

    target = TARGETS[args.target]
    patches = target["patches"]

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"ERROR: input file not found: {input_path}", file=sys.stderr)
        sys.exit(1)

    data = bytearray(input_path.read_bytes())
    input_md5 = md5(data)

    print(f"Input:    {input_path}")
    print(f"Target:   {args.target} — {target['description']}")
    print(f"Size:     {len(data):,} bytes")
    print(f"MD5:      {input_md5}")

    if not args.skip_md5:
        if input_md5 == STOCK_MD5:
            print(f"MD5:      OK (matches stock)")
        else:
            print(f"MD5:      WARNING — does not match known stock md5 ({STOCK_MD5})")
            print("          Use --skip-md5 to patch anyway.")
            sys.exit(1)

    pre_results = verify_patches(data, patches, mode="before")
    pre_ok = print_results(pre_results, "Pre-patch verification (expect stock bytes)")

    if not pre_ok:
        post_results = verify_patches(data, patches, mode="after")
        post_ok = print_results(post_results, "Already-patched check")
        if post_ok:
            print(f"\nBinary appears to already be {args.target}. Nothing to do.")
        else:
            print(f"\nERROR: patch sites match neither stock nor {args.target}.")
        sys.exit(1 if not post_ok else 0)

    if args.verify_only:
        print("\nVerify-only mode — no output written.")
        sys.exit(0)

    for p in patches:
        offset = p["offset"]
        data[offset: offset + len(p["after"])] = p["after"]

    post_results = verify_patches(data, patches, mode="after")
    post_ok = print_results(post_results, f"Post-patch verification (expect {args.target} bytes)")

    if not post_ok:
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    output_path = Path(args.output) if args.output else Path(f"{input_path}.{args.target}")
    output_path.write_bytes(data)
    output_md5 = md5(data)

    print(f"\nOutput:   {output_path}")
    print(f"MD5:      {output_md5}")
    if target["md5"]:
        md5_status = "OK" if output_md5 == target["md5"] else f"WARNING — expected {target['md5']}"
        print(f"MD5 check: {md5_status}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/bin/mtkbt")
    print(f"  adb shell chmod 755 /system/bin/mtkbt")
    print(f"  adb reboot")


if __name__ == "__main__":
    main()
