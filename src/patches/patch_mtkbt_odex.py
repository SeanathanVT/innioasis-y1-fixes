#!/usr/bin/env python3
"""
patch_mtkbt_odex.py — Patch stock MtkBt.odex -> MtkBt.odex.patched

Stock binary md5:  11566bc23001e78de64b5db355238175
Output md5:        acc578ada5e41e27475340f4df6afa59

ODEX structure:
  ODEX header (0x28 bytes): magic "dey\n036\0", dex_offset=0x28, dex_length=0x98490
  DEX data at 0x28: standard DEX with magic "dex\n035\0"
  DEX adler32 at ODEX file offset 0x30 (= DEX header field offset 0x08)
    covers DEX bytes [12 : dex_length]  (ODEX file bytes [0x34 : 0x984b8])

--- Patch 1: getPreferVersion() return value ---

  BTAvrcpProfile.getPreferVersion() (code @ DEX 0x0003e0b0):
    const/16 v0, #10   ; DEX bytes: 13 00 0A 00
    return v0

  BlueAngel internal version codes: 10 = AVRCP 1.3, 14 = AVRCP 1.4.

  This return value drives the entire Java -> JNI -> daemon capability cascade:
    getPreferVersion() -> checkCapability() 1.4 block -> activateConfig_3req(bitmask)
    -> g_tg_feature = 0x0e (@ libextavrcp_jni.so 0xD29C)
    -> activate_1req reads global -> btmtk_avrcp_send_activate_req payload[6] = 0x0e
    -> mtkbt daemon receives AVRCP 1.4 internal code on abstract socket bt.ext.adp.avrcp

  Patch: change return value from 10 (0x0a) to 14 (0x0e).
  Confirmed working in logcat: getPreferVersion:14, Support AVRCP1.4,
  _activate_1req version:14 sdpfeature:35.

--- Patch 2: sPlayServiceInterface reset in BluetoothAvrcpService.disable() ---

  Root cause: sPlayServiceInterface (field@1267, static boolean) is set true in
  startToBindPlayService() when bindService() succeeds on first BT activation.
  It is never reset during the BT-toggle disable cycle.

  On second activation, BTAvrcpMusicAdapter$1.onServiceConnected() reads
  sPlayServiceInterface as true, skips re-initialization, and immediately calls
  notifyProfileState(11) (STATE_ENABLED) before any car CONNECT_IND arrives.
  The Android BT profile manager interprets STATE_ENABLED, calls stopSelf(),
  and tears the service down at onDestroy — visible in logcat immediately after
  PlayService.onServiceConnected.

  Fix: replace the 14-byte "-disable" log preamble in disable() with:
    const/4 v1, #0                           (2 bytes)
    sput-byte v1, sPlayServiceInterface      (4 bytes)
    nop x4                                   (8 bytes)

  Verified in logcat (second activation after BT toggle):
    Before fix: PlayService.onServiceConnected -> notifyProfileState:11 -> onDestroy
    After fix:  PlayService.onServiceConnected -> connect_ind -> CONNECT_CNF

--- DEX analysis reference ---

  sPlayServiceInterface: field@1267, static boolean, flags=0x000a (private+static)
    Write opcode: sput-byte (0x6a) — NOT sput-boolean (0x69)
    Write sites:  startToBindPlayService() dex[0x03df46] and dex[0x03dfac]
    Read sites:   startToBindPlayService() dex[0x03df3c], dex[0x03dfe2]
    Reset site:   disable() dex[0x03f21a] — THIS PATCH
  disable() code_off: dex[0x03f188] = ODEX[0x03f1b0]; insns_off: ODEX[0x03f1c0]

Usage:
    python3 patch_mtkbt_odex.py MtkBt.odex
    python3 patch_mtkbt_odex.py MtkBt.odex --output /tmp/MtkBt.odex.patched
    python3 patch_mtkbt_odex.py MtkBt.odex --verify-only

Deploy:
    adb push output/MtkBt.odex.patched /system/app/MtkBt.odex
    adb reboot
"""

import argparse
import hashlib
import struct
import sys
import zlib
from pathlib import Path

STOCK_MD5  = "11566bc23001e78de64b5db355238175"
OUTPUT_MD5 = "acc578ada5e41e27475340f4df6afa59"

DEX_OFFSET     = 0x28
ADLER_FILE_OFF = 0x30

PATCHES = [
    {
        "name":   "[F1] getPreferVersion return  (BlueAngel 10=AVRCP1.3 -> 14=AVRCP1.4)",
        "offset": 0x3e0ea,
        "before": bytes([0x0a]),
        "after":  bytes([0x0e]),
    },
    {
        "name":   "[F2] disable() reset sPlayServiceInterface = false",
        "offset": 0x03f21a,
        "before": bytes([0x1a, 0x01, 0x02, 0x03,   # const-string v1, "EXT_AVRCP"
                         0x1a, 0x02, 0x21, 0x0b,   # const-string v2, "[BT][AVRCP] -disable"
                         0x71, 0x20, 0x86, 0x01,   # invoke-static Log::i
                         0x21, 0x00]),             #   {v1, v2}
        "after":  bytes([0x12, 0x10,               # const/4 v1, #0
                         0x6a, 0x01, 0xf3, 0x04,   # sput-byte v1, sPlayServiceInterface
                         0x00, 0x00,               # nop
                         0x00, 0x00,               # nop
                         0x00, 0x00,               # nop
                         0x00, 0x00]),             # nop
    },
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def compute_adler32(data: bytes) -> int:
    dex_len = struct.unpack_from("<I", data, 12)[0]
    return zlib.adler32(data[DEX_OFFSET + 12: DEX_OFFSET + dex_len]) & 0xFFFFFFFF


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


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch stock MtkBt.odex for AVRCP 1.4 + BT toggle fix"
    )
    parser.add_argument("input", help="Path to stock MtkBt.odex")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/MtkBt.odex.patched)")
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

    if data[:4] != b"dey\n":
        print("ERROR: not an ODEX file (missing 'dey\\n' magic)")
        sys.exit(1)

    pre_ok, pre_results = verify(data, "before")
    print_results("Pre-patch verification (stock)", pre_results, "before")

    if not pre_ok:
        post_ok, post_results = verify(data, "after")
        print_results("Already-patched check", post_results, "after")
        if post_ok:
            print("\nBinary is already fully patched. Nothing to do.")
            sys.exit(0)
        print("\nERROR: patch sites match neither stock nor fully-patched state.")
        sys.exit(1)

    stored_adler   = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
    computed_adler = compute_adler32(data)
    adler_ok = stored_adler == computed_adler
    print(f"\n  [{'OK' if adler_ok else 'WARN'}] 0x{ADLER_FILE_OFF:06x}  "
          f"adler32 stored=0x{stored_adler:08x} computed=0x{computed_adler:08x}")
    if not adler_ok:
        print("  WARNING: adler32 mismatch on input — continuing anyway")

    if args.verify_only:
        print("\nVerify-only — no output written.")
        sys.exit(0)

    for p in PATCHES:
        data[p["offset"]: p["offset"] + len(p["after"])] = p["after"]

    new_adler = compute_adler32(data)
    struct.pack_into("<I", data, ADLER_FILE_OFF, new_adler)

    post_ok, post_results = verify(data, "after")
    print_results("Post-patch verification", post_results, "after")

    stored_after   = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
    computed_after = compute_adler32(data)
    adler_after_ok = stored_after == computed_after
    print(f"  [{'OK' if adler_after_ok else 'FAIL'}] 0x{ADLER_FILE_OFF:06x}  "
          f"adler32 = 0x{stored_after:08x}")

    if not (post_ok and adler_after_ok):
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "MtkBt.odex.patched"

    output_path.write_bytes(data)
    output_md5 = md5(data)

    output_md5_mismatch = False
    if OUTPUT_MD5 is None:
        out_tag = f"[set OUTPUT_MD5 = \"{output_md5}\"]"
    elif output_md5 == OUTPUT_MD5:
        out_tag = "[OK — matches expected]"
    else:
        out_tag = f"[MISMATCH — expected {OUTPUT_MD5}]"
        output_md5_mismatch = True

    print(f"\nOutput: {output_path}  ({len(data):,} bytes)")
    print(f"MD5:    {output_md5}  {out_tag}")
    print(f"\nDeploy:")
    print(f"  adb push {output_path} /system/app/MtkBt.odex")
    print(f"  adb shell chmod 644 /system/app/MtkBt.odex")
    print(f"  adb reboot")

    if output_md5_mismatch and not args.skip_md5:
        print("\nERROR: output MD5 doesn't match expected. Output was written but"
              " the patcher's expected hash is stale or the patch logic diverged."
              " Pass --skip-md5 to suppress.", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
