#!/usr/bin/env python3
"""
patch_libaudio_a2dp.py — keep AVDTP stream alive during pause via HAL standby_l fix.

Stock md5:  0d909a0bcf7972d6e5d69a1704d35d1f
Output md5: adbd98afeb5593f1ffe3b90acd0f2536

Single-byte ARM-conditional → unconditional flip at file offset 0x000086ab
inside `_ZN20android_audio_legacy18A2dpAudioInterface18A2dpAudioStreamOut9standby_lEv`
(symbol entry 0x8654). Forces the standby path to ALWAYS skip the call to
`a2dp_stop@plt`, so AudioFlinger's silence-timeout standby leaves the AVDTP
source stream alive instead of emitting AVDTP SUSPEND on the wire.

AVDTP 1.3 §8.13 / §8.15 expectation: AVRCP TG entering PAUSED leaves the
A2DP source stream paused-but-up; SUSPEND is reserved for explicit policy
changes (phone call routing, etc.). TV-class CTs that aggressively close
+ reopen their A2DP sink on SUSPEND otherwise produce burst-on-resume
audio + playhead drift.

Why a HAL byte patch and not a Java-side `setParameters("A2dpSuspended=...")`
hook: empirically (capture `/work/logs/dual-tv-20260509-1538`) the AOSP
A2DP HAL implements `setSuspended(true)` as a *synchronous* stream tear-
down — it calls `a2dp_stop` directly inside `setSuspended`, before any
silence-timeout standby ever fires. So the previous Java approach actively
triggered the very SUSPEND it was trying to prevent. The HAL byte patch
short-circuits the only standby path that calls a2dp_stop instead.

Disassembled standby_l (annotated with the patch site):

    8654: push {r3, r4, r5, lr}
    8658: mov  r4, r0
    865c: ldrb r3, [r0, #8]                   ; r3 = mStandby
    8660: cmp  r3, #0
    8664: beq  8674
    8668: ldrb r5, [r0, #57]
    866c: cmp  r5, #0
    8670: beq  8698                           ; already-in-standby fast return
    8674: ldrb r0, [r4, #56]
    8678: cmp  r0, #0
    867c: movne r5, #0
    8680: beq  86a0                           ; falls into the a2dp_stop guard
    8684: ldr  r2, [pc, #48]                  ; "WAKE_LOCK_NAME"-ish
    8688: add  r0, pc, r2
    868c: bl   release_wake_lock
    8690: mov  r1, #1
    8694: strb r1, [r4, #8]                   ; mStandby = 1
    8698: mov  r0, r5                         ; return r5
    869c: pop  {r3, r4, r5, pc}

    86a0: ldrb r5, [r4, #48]
    86a4: cmp  r5, #0
    86a8: beq  8684                           ; ← PATCH SITE: change to `b 8684`
    86ac: ldr  r0, [r4, #40]
    86b0: bl   a2dp_stop@plt                  ; ← becomes unreachable post-patch
    86b4: mov  r5, r0
    86b8: b    8684

Patch: at file offset 0x000086ab change byte 0x0a (ARM cond `EQ`) to 0xea
(ARM cond `AL` = always). The `beq 8684` becomes an unconditional `b 8684`,
so the a2dp_stop branch (instructions at 0x86ac-0x86b8) is never taken.

Pairs with: nothing — self-contained single-byte HAL fix. The
`Y1MediaBridge/MediaBridgeService.java::onStateDetected` path no longer
calls `setParameters("A2dpSuspended=...")` (reverted in v2.9 / versionCode
29 — see CHANGELOG + BT-COMPLIANCE.md §9.2).

Usage:
    python3 patch_libaudio_a2dp.py libaudio.a2dp.default.so
    python3 patch_libaudio_a2dp.py libaudio.a2dp.default.so --output /tmp/lib.patched
    python3 patch_libaudio_a2dp.py libaudio.a2dp.default.so --verify-only
"""

import argparse
import hashlib
import os
import sys
from pathlib import Path

STOCK_MD5         = "0d909a0bcf7972d6e5d69a1704d35d1f"
OUTPUT_MD5        = "adbd98afeb5593f1ffe3b90acd0f2536"

DEBUG_LOGGING     = os.environ.get("KOENSAYR_DEBUG", "") == "1"
OUTPUT_DEBUG_MD5  = OUTPUT_MD5

EXPECTED_OUTPUT_MD5 = OUTPUT_DEBUG_MD5 if DEBUG_LOGGING else OUTPUT_MD5

PATCHES = [
    {
        "name":   "[A2DP-HAL] standby_l: beq 8684 -> b 8684 (skip a2dp_stop unconditionally)",
        "offset": 0x000086ab,
        "before": bytes([0x0a]),  # ARM cond EQ
        "after":  bytes([0xea]),  # ARM cond AL (always)
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
        description="HAL standby_l byte-patch — keep AVDTP stream alive across pauses"
    )
    parser.add_argument("input", help="Path to stock libaudio.a2dp.default.so")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/libaudio.a2dp.default.so.patched)")
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
        output_path = output_dir / "libaudio.a2dp.default.so.patched"
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
    print(f"  adb push {output_path} /system/lib/libaudio.a2dp.default.so")
    print(f"  adb shell chmod 644 /system/lib/libaudio.a2dp.default.so")
    print(f"  adb reboot")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
