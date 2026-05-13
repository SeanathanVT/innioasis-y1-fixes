#!/usr/bin/env python3
"""
patch_libextavrcp.py — make GetElementAttributes response §5.3.4-compliant.

Stock md5:  6442b137d3074e5ac9a654de83a4941a
Output md5: 1347e1b337879840ad2f66597836b05f

Single 2-byte Thumb-2 CBZ→NOP flip at file offset 0x00002266 inside
`btmtk_avrcp_send_get_element_attributes_rsp` (function entry at 0x2188).
Disables the "ignore empty attrib" check that drops attributes whose value
length is 0 — a deviation from AVRCP 1.3 §5.3.4 which requires the TG to
emit unsupported attributes with `AttributeValueLength=0` rather than
omit them entirely.

Strict-CT impact: some CTs request a specific attribute set (e.g. one
strict CT in the test matrix requests `[0x1, 0x2, 0x3, 0x6, 0x8, 0x7]`)
and gate their metadata-pane render on receiving every requested
attribute back, even if some come with zero-length values. With the
"ignore empty" drop in place, that CT receives a response missing any
attribute whose value isn't set on the TG side, and refuses to render.
Lenient CTs already render fine — they pick out whatever attribute IDs
they recognize from the response.

Stock disassembled gate (annotated with the patch site):

    2254: adds.w r0, fp, #0          ; r0 = attr_id (fp), set flags
    2258: it ne
    225a: movne r0, #1                 ; r0 = 1 if attr_id != 0 else 0
    225c: cmp r7, #0                   ; r7 = strlen
    225e: ite eq
    2260: moveq r0, #0                 ; r0 = 0 if strlen == 0
    2262: andne.w r0, r0, #1           ; else r0 &= 1
    2266: cbz r0, 0x22cc               ; ← PATCH SITE: 88 b3 -> 00 bf (NOP)
                                       ;   Stock: skip emit when (attr_id == 0)
                                       ;          OR (strlen == 0)
                                       ;   Patched: fall through unconditionally —
                                       ;   emit every attr with whatever length
                                       ;   T4 supplied (zero is fine per §5.3.4).
    2268: ... (emit attribute path)
    22cc: ldr r1, [pc, #124]           ; "ignore empty attrib" log path —
                                       ; unreachable post-patch.

Patch: at file offset 0x00002266 change 2 bytes from `88 b3` (CBZ r0,
+0x62) to `00 bf` (NOP T1). Execution falls through to the emit path
unconditionally. The attr_id=0 ("Not Used" per AVRCP 1.3 §26 Table 26.1)
guard is also dropped, but T4 in `libextavrcp_jni.so` never emits attr_id
0, so that side of the gate has no caller in practice.

Pairs with: T4 in `_trampolines.py` reads the CT's inbound `NumAttributes`
+ `AttributeID[N]` request (AVRCP 1.3 §6.6.1 Table 6.26) and emits each
requested ID in order. For IDs outside the canonical 1.3 set 0x01-0x07,
T4 emits with length 0 per §5.3.4. Without this patch, stock libextavrcp.so
silently drops the zero-length entries, so strict CTs that gate render
on response-shape see a truncated frame.

Usage:
    python3 patch_libextavrcp.py libextavrcp.so
    python3 patch_libextavrcp.py libextavrcp.so --output /tmp/lib.patched
    python3 patch_libextavrcp.py libextavrcp.so --verify-only
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

STOCK_MD5         = "6442b137d3074e5ac9a654de83a4941a"
OUTPUT_MD5        = "1347e1b337879840ad2f66597836b05f"

DEBUG_LOGGING     = os.environ.get("KOENSAYR_DEBUG", "") == "1"
OUTPUT_DEBUG_MD5  = OUTPUT_MD5

EXPECTED_OUTPUT_MD5 = OUTPUT_DEBUG_MD5 if DEBUG_LOGGING else OUTPUT_MD5

PATCHES = [
    {
        "name":   "[E1] GetElementAttributes empty-attr drop -> NOP (§5.3.4 zero-length emit)",
        "offset": 0x00002266,
        "before": bytes([0x88, 0xb3]),  # cbz r0, +0x62 (-> 0x22cc 'ignore empty')
        "after":  bytes([0x00, 0xbf]),  # nop T1 (fall through to emit)
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
    ok_count = sum(1 for r in results if r["ok"])
    total = len(results)
    if ok_count == total:
        print(f"\n{label}: {ok_count}/{total} sites OK")
        return
    print(f"\n{label}")
    print("-" * 72)
    for r in results:
        n = len(r["before"])
        fmt = lambda b: b.hex(" ") if n <= 8 else b[:12].hex(" ")
        print(f"  [{'OK' if r['ok'] else 'FAIL'}] 0x{r['offset']:06x}  {r['name']}")
        if not r["ok"]:
            print(f"          expected ({mode}): {fmt(r[mode])}")
            print(f"          actual:            {fmt(r['actual'])}")
    print("-" * 72)


def main():
    parser = argparse.ArgumentParser(
        description="libextavrcp.so byte-patch — drop the 'ignore empty attrib' check"
                    " so GetElementAttributes responses honor AVRCP 1.3 §5.3.4"
                    " (unsupported attributes emit with AttributeValueLength=0)"
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

    if EXPECTED_OUTPUT_MD5 is not None and input_md5 == EXPECTED_OUTPUT_MD5:
        print(f"Input:  {input_path}  ({len(data):,} bytes)")
        print(f"MD5:    {input_md5}  [OK — already at expected output]")
        print("Nothing to do.")
        sys.exit(0)

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
        if EXPECTED_OUTPUT_MD5 is not None:
            print(f"       Expected stock ({STOCK_MD5}) or already-patched ({EXPECTED_OUTPUT_MD5}).")
        print("       Use --skip-md5 for alternate stock builds.")
        sys.exit(1)

    show_sites = args.skip_md5 or EXPECTED_OUTPUT_MD5 is None

    if show_sites:
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

    output_md5 = md5(data)
    output_md5_mismatch = EXPECTED_OUTPUT_MD5 is not None and output_md5 != EXPECTED_OUTPUT_MD5

    if show_sites or output_md5_mismatch:
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

    md5_var = "OUTPUT_DEBUG_MD5" if DEBUG_LOGGING else "OUTPUT_MD5"
    if EXPECTED_OUTPUT_MD5 is None:
        out_tag = f"[set {md5_var} = \"{output_md5}\"]"
    elif output_md5 == EXPECTED_OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {EXPECTED_OUTPUT_MD5}]"

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/lib/libextavrcp.so")
    print(f"  adb shell chmod 644 /system/lib/libextavrcp.so")
    print(f"  adb reboot")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
