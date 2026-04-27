#!/usr/bin/env python3
"""
patch_mtkbt_odex.py — Patch stock MtkBt.odex → MtkBt.odex.patched

Stock binary md5:  11566bc23001e78de64b5db355238175
Output md5:        acc578ada5e41e27475340f4df6afa59

ODEX structure:
  ODEX header (0x28 bytes): magic "dey\n036\0", dex_offset=0x28, dex_length=0x98490
  DEX data at 0x28: standard DEX with magic "dex\n035\0"
  DEX adler32 at ODEX file offset 0x30 (= DEX offset 0x28 + DEX field offset 0x08)
    covers DEX bytes [12:dex_length] (i.e. ODEX file bytes [0x34:0x984b8])

Patches applied:

  1. 0x3e0ea  BTAvrcpProfile.getPreferVersion() return  0x0a → 0x0e
     Forces AVRCP version reported to mtkbt from 1.0 (10) to 1.4 (14), triggering
     AVRCP 1.4 negotiation with the car head unit.

  2. 0x03f21a..0x03f227  BluetoothAvrcpService.disable() — reset sPlayServiceInterface
     Replaces the "-disable" log preamble (two const-string + invoke-static Log::i,
     14 bytes) with:
         const/4  v1, #0                                              (2 bytes)
         sput-byte v1, BTAvrcpMusicAdapter.sPlayServiceInterface      (4 bytes)
         nop × 4                                                      (8 bytes)

     Root cause: sPlayServiceInterface (field@1267, static boolean, written via the
     ODEX-specific sput-byte opcode 0x6a) is set to true in startToBindPlayService()
     when bindService() succeeds. It is never cleared during the BT-toggle disable
     cycle, leaving it stale across service restarts.

     On second activation, onServiceConnected() in the BTAvrcpMusicAdapter$1
     ServiceConnection reads sPlayServiceInterface as true, takes the "already bound"
     fast-path, and immediately calls notifyProfileState(11) via the mAvrcpSrv
     back-reference. The Android BT profile manager receives STATE_ENABLED before any
     car CONNECT_IND has arrived and calls stopSelf(), tearing the service down at
     onDestroy — visible in logcat immediately after PlayService.onServiceConnected.

     On first activation the service survives because PlayService.onServiceConnected
     fires before activate_cnf, so sPlayServiceInterface is still false at that point
     and the "already bound" path is not taken.

     Fix: reset sPlayServiceInterface = false inside disable(), so the flag is always
     false when the service next starts.

  3. 0x0030   DEX adler32 recomputed over patched content.

DEX analysis notes:
  BTAvrcpMusicAdapter  class_def @ dex[0x015c8c]
  sPlayServiceInterface: field@1267, static boolean, flags=0x000a (private+static)
    Written:  startToBindPlayService() dex[0x03df46] and dex[0x03dfac] via sput-byte (0x6a)
    Read:     startToBindPlayService() dex[0x03df3c], dex[0x03dfe2] via sget-boolean (0x63)
              onServiceConnected inner-class path via sget-boolean (0x63)
    Reset:    disable() dex[0x03f21a] — THIS PATCH
  checkCapability():    mInitCapability @ vtable+0xf4; guard at insn@15 — not patched
  activateCnf():        notifyProfileState(11) at dex[0x03ef1c] — not patched
  disable() code_off:   dex[0x03f188] = ODEX[0x03f1b0]
  insns_off:            dex[0x03f198] = ODEX[0x03f1c0]; count=70

Logcat confirmation (logcat-bt.log, second activation after BT toggle):
  Before fix: PlayService.onServiceConnected (L200) → notifyProfileState:11 (L201)
              → onDestroy (L202) — service torn down before car connects
  After fix:  PlayService.onServiceConnected (L200) → [no premature state:11]
              → connect_ind (L207) → CONNECT_CNF (L214) — full connection established

Usage:
    python3 patch_mtkbt_odex.py MtkBt.odex
    python3 patch_mtkbt_odex.py MtkBt.odex --output /tmp/MtkBt.odex.patched
    python3 patch_mtkbt_odex.py MtkBt.odex --verify-only

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
OUTPUT_MD5  = "acc578ada5e41e27475340f4df6afa59"

DEX_OFFSET       = 0x28        # DEX data starts here in the ODEX file
ADLER_FILE_OFF   = 0x30        # adler32 field: DEX_OFFSET + 0x08

# ── Patch table ─────────────────────────────────────────────────────────────
# Each entry: (label, offset, before_bytes, after_bytes)
PATCHES = [
    (
        "getPreferVersion return  (AVRCP 1.0→1.4)",
        0x3e0ea,
        bytes([0x0a]),
        bytes([0x0e]),
    ),
    (
        "disable() reset sPlayServiceInterface",
        0x03f21a,
        # Two const-string prolog + invoke-static Log::i  (14 bytes)
        bytes([0x1a, 0x01, 0x02, 0x03,   # const-string v1, "EXT_AVRCP"
               0x1a, 0x02, 0x21, 0x0b,   # const-string v2, "[BT][AVRCP] -disable"
               0x71, 0x20, 0x86, 0x01,   # invoke-static Log::i
               0x21, 0x00]),             #   {v1, v2}
        # const/4 v1,#0; sput-byte v1,field@1267(sPlayServiceInterface); nop×4
        bytes([0x12, 0x10,               # const/4 v1, #0
               0x6a, 0x01, 0xf3, 0x04,  # sput-byte v1, sPlayServiceInterface
               0x00, 0x00,               # nop
               0x00, 0x00,               # nop
               0x00, 0x00,               # nop
               0x00, 0x00]),             # nop
    ),
]


def md5(data: bytes) -> str:
    return hashlib.md5(data).hexdigest()


def compute_adler32(data: bytes) -> int:
    # dex_length is stored at ODEX[0x0c]; adler32 covers data[DEX+12 : DEX+dex_len]
    dex_len = struct.unpack_from("<I", data, 12)[0]
    return zlib.adler32(data[DEX_OFFSET + 12 : DEX_OFFSET + dex_len]) & 0xFFFFFFFF


def verify_patches(data: bytes, mode: str) -> tuple[bool, list[dict]]:
    """mode is 'before' or 'after'."""
    results = []
    for label, offset, before, after in PATCHES:
        expected = before if mode == "before" else after
        actual = bytes(data[offset : offset + len(expected)])
        results.append({"label": label, "offset": offset,
                        "expected": expected, "actual": actual,
                        "ok": actual == expected})
    return all(r["ok"] for r in results), results


def print_results(heading: str, results: list[dict]) -> None:
    print(f"\n{heading}")
    print("-" * 72)
    for r in results:
        status = "OK" if r["ok"] else "FAIL"
        print(f"  [{status}] 0x{r['offset']:06x}  {r['label']}")
        if not r["ok"]:
            print(f"          expected: {r['expected'].hex(' ')}")
            print(f"          actual:   {r['actual'].hex(' ')}")
    print("-" * 72)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Patch stock MtkBt.odex for AVRCP 1.4 + BT toggle fix"
    )
    parser.add_argument("input", help="Path to stock MtkBt.odex")
    parser.add_argument("--output", "-o", default=None,
                        help="Output path (default: output/MtkBt.odex.patched)")
    parser.add_argument("--verify-only", action="store_true",
                        help="Check patch sites only, no output")
    parser.add_argument("--skip-md5", action="store_true",
                        help="Skip stock MD5 check")
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

    if data[:4] != b"dey\n":
        print("ERROR: not an ODEX file (missing 'dey\\n' magic)")
        sys.exit(1)

    # ── Pre-patch verification ───────────────────────────────────────────────
    pre_ok, pre_results = verify_patches(data, "before")
    print_results("Pre-patch verification", pre_results)

    if not pre_ok:
        post_ok, post_results = verify_patches(data, "after")
        print_results("Already-patched check", post_results)
        if post_ok:
            print("\nBinary is already fully patched. Nothing to do.")
            sys.exit(0)
        print("\nERROR: patch sites match neither stock nor fully-patched state.")
        sys.exit(1)

    # ── adler32 sanity ───────────────────────────────────────────────────────
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

    # ── Apply patches ────────────────────────────────────────────────────────
    for _label, offset, _before, after in PATCHES:
        data[offset : offset + len(after)] = after

    # ── Recompute adler32 ────────────────────────────────────────────────────
    new_adler = compute_adler32(data)
    struct.pack_into("<I", data, ADLER_FILE_OFF, new_adler)

    # ── Post-patch verification ──────────────────────────────────────────────
    post_ok, post_results = verify_patches(data, "after")
    print_results("Post-patch verification", post_results)

    stored_after   = struct.unpack_from("<I", data, ADLER_FILE_OFF)[0]
    computed_after = compute_adler32(data)
    adler_after_ok = stored_after == computed_after
    print(f"  [{'OK' if adler_after_ok else 'FAIL'}] 0x{ADLER_FILE_OFF:06x}  "
          f"adler32 = 0x{stored_after:08x}")

    if not (post_ok and adler_after_ok):
        print("\nERROR: post-patch verification failed — output not written.")
        sys.exit(1)

    # ── Write output ─────────────────────────────────────────────────────────
    if args.output:
        output_path = Path(args.output)
    else:
        output_dir = Path("output")
        output_dir.mkdir(exist_ok=True)
        output_path = output_dir / "MtkBt.odex.patched"

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
